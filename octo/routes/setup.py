"""Set up Octo and the Mac (Mac only): what's left to set up, the last export, This Mac's checks, the speech models and
Hugging Face, the beta's results, and the Octo app's menu bar and banners."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import ROOT
from .. import deploy as DP
from .. import device as D
from .. import labconfig as LC
from .. import models as MD
from .. import settings as ST
from ..db import config, row
from .common import Lab, same_origin


class HuggingFaceToken(BaseModel):
    token: str = Field(min_length=8, max_length=200)




_ts = {"at": 0.0, "status": None}  # Tailscale's answer, kept 30 s: the Setup page asks every 8 s

class Skip(BaseModel):
    id: str
    skip: bool = True

def add(router: APIRouter, lab: Lab) -> None:
    db, watchdog, fetcher = lab.db, lab.watchdog, lab.fetcher

    @router.get("/beta")
    def beta_results() -> dict:
        """The beta's results file (octo/beta.py): counts only, shown before it's saved or sent."""
        from .. import beta as B
        return B.results(db())

    @router.get("/settings/export")
    def export_status() -> dict:
        """When the lab was last exported (octo export, or the Octo app's File menu) and what's come since."""
        conn = db()
        at = config(conn, "last_export_at")
        since = row(conn, "SELECT count(*) AS n FROM sessions WHERE status='active' AND created_at > ?", at or 0)["n"]
        return {"last_export_at": at, "since": since}

    @router.get("/settings/setup")
    def setup_status() -> dict:
        """What a new lab still needs, in one order (the page, `octo doctor` and docs/beta-tester.md follow it): this Mac,
        the speech models, you, your principles, a model for reviews, the iPhone app, the phone paired, away from home,
        your voice. Each is checked, never ticked; the three phone ones can be skipped (`setup_skipped`)."""
        from .. import doctor as DR
        from .. import iphone as IP
        from .. import owner as O
        from .. import phone as PH
        from .. import rubric as RB
        conn = db()
        skipped = set(config(conn, "setup_skipped", []))
        mac = [c for c in (*DR.machine(), *DR.places(), *DR.apple_tools(), *DR.software(), *DR.launcher())
               if c.mark == DR.FAIL]
        from .. import notify as N
        if N._app_mode == "off":  # the Octo app says the owner turned its banners off: worth knowing, never blocking
            mac.append(DR.Check(DR.WARN, "Notifications", "turned off for Octo, so it can't tell you when a review is ready",
                                "System Settings → Notifications → Octo → turn on Allow Notifications"))
        have = IP.installed() or {}
        devices = PH.devices(conn)
        steps = [
            {"id": "mac", "done": not [c for c in mac if c.mark == DR.FAIL], "why": [c.__dict__ for c in mac]},
            {"id": "models", "done": all(m["cached"] for m in MD.status(conn))},
            {"id": "you", "done": bool(O.load(conn).get("name"))},
            {"id": "principles", "done": bool(RB.sections()["parsed"])},
            {"id": "model", "done": ST.review_blocked(conn) is None},
            {"id": "iphone", "done": bool(have.get("trusted") and (have.get("expires") or 0) > time.time()),
             "optional": True},
            {"id": "pair", "done": bool(devices), "optional": True,
             "why": [c.__dict__ for c in DR.firewall(PH.enabled()) if c.mark == DR.FAIL]},  # the Mac refusing the phone
            {"id": "away", "optional": True, **away(devices)},
            {"id": "voice", "done": row(conn, "SELECT 1 FROM people WHERE is_self=1") is not None},
        ]
        for st in steps:
            st["skipped"] = not st["done"] and st["id"] in skipped
        # A lab with no recordings yet: the page offers to bring in an export first (docs/export-plan.md)
        return {"steps": steps, "complete": all(st["done"] or st["skipped"] for st in steps),
                "empty": row(conn, "SELECT 1 FROM recordings LIMIT 1") is None}

    def away(devices: list[dict]) -> dict:
        """Away from home: done once the app has reached the lab over Tailscale; else the next single thing to do."""
        from .. import phone as PH
        if not devices:  # it comes after pairing; and asking Tailscale takes about 2 s, so not before it's needed
            return {"done": False, "do": None, "link": None}
        if time.time() - _ts["at"] > 30:
            _ts.update(at=time.time(), status=PH.tailscale_status())
        nxt = PH.away_step(_ts["status"], PH.enabled(), devices, PH._settings().get("ca_renewed_at"))
        return {"done": nxt["step"] == "ready", "do": nxt["do"], "link": nxt["link"]}

    @router.post("/settings/setup/skip", dependencies=[Depends(same_origin)])
    def setup_skip(body: Skip) -> dict:
        """The phone steps are optional: skipped until the owner comes back to them."""
        if body.id not in ("iphone", "pair", "away"):
            raise HTTPException(400, "only the phone steps can be skipped")
        conn = db()
        skipped = set(config(conn, "setup_skipped", [])) - {body.id} | ({body.id} if body.skip else set())
        conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('setup_skipped', ?, strftime('%s','now')) "
                     "ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                     (json.dumps(sorted(skipped)),))
        return setup_status()

    @router.get("/settings/iphone")
    def iphone_status() -> dict:
        """Set up Octo's iPhone step (octo/iphone.py): each part checked on this Mac in order, the install job, and
        until when the install opens (renewed by itself 2 days before)."""
        from .. import iphone as IP
        return {"steps": IP.steps(), "job": IP.JOB.state(), "installed": IP.installed(), "store": IP.XCODE_STORE}

    @router.post("/settings/iphone/install", dependencies=[Depends(same_origin)])
    def iphone_install() -> dict:
        from .. import iphone as IP
        IP.JOB.start()
        return IP.JOB.state()

    @router.post("/settings/iphone/check", dependencies=[Depends(same_origin)])
    def iphone_check() -> dict:
        """Open Octo on the iPhone: it opens only once the phone trusts the developer (the last part)."""
        from .. import iphone as IP
        ok, why = IP.open_on_phone()
        return {"ok": ok, "why": why}

    @router.get("/settings/mac")
    def mac_checks() -> list[dict]:
        """`octo doctor` for the setup page's "This Mac": everything but the setup steps (shown there already) and
        whether the lab answers (it just did), plus anything else on its port."""
        from .. import doctor as DR
        conn = db()
        checks = [*DR.machine(), *DR.places(), *DR.apple_tools(), *DR.software(), *DR.models(conn), *DR.port(True),
                  *DR.permissions(conn), *DR.home(conn)]
        return [c.__dict__ for c in checks]

    @router.get("/settings/models")
    def models_status() -> dict:
        """The speech models the steps need: ready, downloading (with how much is down), waiting, or waiting for
        access on Hugging Face (open-source plan I3, I4)."""
        return fetcher.report()

    @router.post("/settings/models/download", dependencies=[Depends(same_origin)])
    def models_download() -> dict:
        fetcher.start()
        return fetcher.report()

    @router.get("/settings/huggingface")
    def huggingface() -> dict:
        """Who this Mac is signed in to Hugging Face as, and whether that account may have each gated model."""
        return MD.account()

    @router.post("/settings/huggingface", dependencies=[Depends(same_origin)])
    def huggingface_sign_in(body: HuggingFaceToken) -> dict:
        """Sign in with a read token, check the gated models, and download whatever's still missing. The token is
        kept where `hf auth login` keeps it and never comes back to the page."""
        got = MD.account(body.token.strip())
        if not got.get("name"):
            raise HTTPException(400, got.get("error") or "that token didn't work")
        fetcher.start()
        return got

    @router.get("/mac-app")
    def mac_app() -> dict:
        """The Octo app's menu bar (docs/mac-app-plan.md): its octopus's state and its lines, in the lab's words."""
        from .. import doctor as DR
        from .. import notify as N
        from .. import eta as E
        conn = db()
        lines = []
        if watchdog.problem:  # answered from memory: the database may be what can't be reached
            return {"icon": "needs", "lines": [{"text": "Octo can't save to its folder", "page": "/", "needs": True}]}
        left = sum(1 for c in DR.lab(conn) if c.mark == DR.FAIL)
        if left:
            lines.append({"text": f"Setup: {left} step{'s' if left > 1 else ''} left", "page": "/setup", "needs": True})
        if fetcher.running:
            lines.append({"text": "Getting the speech models ready", "page": "/setup"})
        lines += N.menu(N.waiting(conn))
        eta = E.cached(conn)
        if eta["queue_empty_at"]:
            lines.append({"text": "Working · ready about " + time.strftime("%H:%M", time.localtime(eta["queue_empty_at"])),
                          "page": "/"})
        last = D.last_status() or {}
        want = (last.get("status") or {}).get("want_on")
        if D.load_config():
            lines.insert(0, {"text": "Recording" if want else "Not recording" if want is False else "Recorder",
                             "page": "/"})
        # A beta tester's clone (a new lab): newer code on its own remote, offered, never applied by itself (Jay, 31 Oct)
        if LC.origin(conn) == LC.PRODUCT and DP.update_ready(ROOT):
            lines.append({"text": "Octo update ready", "action": "update"})
        needs = any(x.get("needs") for x in lines)
        from .. import events as EV
        return {"icon": "needs" if needs else "asleep" if D.load_config() and want is False else "awake", "lines": lines,
                "calendar": EV.load()["mac"]}  # B23: the app reads this Mac's calendars and posts them

    @router.get("/mac-app/notice")
    def mac_app_notice(notify: str = "on", after: int | None = None) -> dict:
        """The Octo app's long poll for banners to post (octo/notify.py): {"seq", "text"}, the text None after 50 s."""
        from .. import notify as N
        if notify not in ("on", "ask", "off"):
            raise HTTPException(400, "notify is on, ask or off")
        seq, text = N.app_wait(notify, after)
        return {"seq": seq, "text": text, "page": N.app_page(seq) if text else None}
