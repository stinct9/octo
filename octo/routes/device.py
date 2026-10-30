"""The recorder: what it was doing, recording on or off, pairing and its Wi-Fi networks (Mac only), and its filler cue
(B20, B22, B29): switch, install, undo, train."""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import RATE
from .. import device as D
from ..db import row
from .common import Lab, same_origin


class Recording(BaseModel):
    on: bool


class Filler(BaseModel):
    on: bool


class FillerTarget(BaseModel):
    target: str = Field(min_length=1, max_length=40)


class FillerInstall(BaseModel):
    target: str | None = Field(None, min_length=1, max_length=40)  # the installed filler's candidate when absent


class RecorderNetwork(BaseModel):  # one of the recorder's Wi-Fi networks (octo/device.py, rec-12)
    op: str = "add"
    ssid: str = ""
    password: str = ""


def add(router: APIRouter, lab: Lab) -> None:
    db, syncer = lab.db, lab.syncer

    # The recorder (product plan 4.4, Mac only): pair it on USB, and its Wi-Fi networks. Passwords go in
    # and never out. Listing only asks over Wi-Fi: opening its USB port would restart it.
    @router.get("/settings/recorder")
    def recorder_get() -> dict:
        from .. import device as D
        cfg = D.load_config() or {}
        out = {"paired": bool(cfg.get("key")), "developer_build": D.developer_build(), "networks": None, "error": None}
        if out["paired"]:
            try:
                with D.holding(db(), wait_s=5):
                    out["networks"] = D.networks_wifi(D.Device.from_config(), "list")
            except D.OldFirmware as e:
                out["error"] = str(e)[:1].upper() + str(e)[1:] + "."
            except (D.DeviceError, OSError) as e:
                out["error"] = f"not reachable on Wi-Fi now ({e})"
        return out

    @router.post("/settings/recorder/pair", dependencies=[Depends(same_origin)])
    def recorder_pair(body: RecorderNetwork) -> dict:
        from bench.device import BoardError, connect

        from .. import device as D
        try:
            with connect("recorder") as board:
                st = D.pair_usb(board)
                names = D.networks_usb(board, D.net_command("add", body.ssid, body.password) if body.ssid else "list")
        except (D.DeviceError, BoardError) as e:
            raise HTTPException(400, str(e))
        return {"paired": True, "fw": st.get("fw"), "networks": names}

    @router.post("/settings/recorder/networks", dependencies=[Depends(same_origin)])
    def recorder_networks(body: RecorderNetwork) -> dict:
        from .. import device as D
        try:
            with D.holding(db(), wait_s=10):
                return {"networks": D.networks(D.net_command(body.op, body.ssid, body.password))}
        except D.DeviceError as e:
            raise HTTPException(400, str(e))

    def filler_view(st: dict | None) -> dict:
        """The filler cue (B20) as the recorder last said: null fields until it's been seen on rec-24 or later."""
        st = st or {}
        return {"model": st.get("filler_model"), "on": st.get("filler_on"), "threshold": st.get("filler_threshold"),
                "ready": st.get("filler_ready"), "count": st.get("fillers"),
                # rec-27 (B29): models as data, which one, the one Undo puts back, one it went back from at boot
                "models": bool(st.get("filler_models")), "name": st.get("filler_name"),
                "before": st.get("filler_before") or None, "reverted": st.get("filler_reverted") or None,
                "recording": st.get("state") == "recording"}

    @router.get("/settings/recorder/filler")
    def recorder_filler() -> dict:
        from .. import device as D
        from .. import filler_loop as FLP
        s = FLP.state()
        cand = s["candidate"]
        loop = {"live": FLP.live(db()), "train": s["train"], "install": s["install"],
                "installed": {k: v for k, v in (s["installed"] or {}).items() if k in ("installed_at", "threshold", "bench")} or None,
                "candidate": {"made_at": cand["made_at"], "threshold": cand["threshold"], "bench": cand["bench"],
                              "line": FLP.better_than_installed(cand)} if cand else None}
        j = FLP.job()
        loop.update(target=FLP.installed_target(), job={"target": j["target"], "started_at": j["started_at"]} if j else None,
                    targets=FLP.readiness(db()))  # B29: which fillers could be trained, and each one's last training
        return {**filler_view((D.last_status() or {}).get("status")), "loop": loop}

    @router.post("/settings/recorder/filler/train", dependencies=[Depends(same_origin)])
    def recorder_filler_train(body: FillerTarget) -> dict:
        from .. import filler_loop as FLP
        from .. import fillers as FI
        target = FI.normalize(body.target)
        if target not in FLP.targets(db()):
            raise HTTPException(422, "not a filler on your list")
        try:
            FLP.start_job(target)
        except FLP.JobRefused as e:
            raise HTTPException(409, str(e))
        return {"started": True, "target": target}

    @router.post("/settings/recorder/filler/train/cancel", dependencies=[Depends(same_origin)])
    def recorder_filler_train_cancel() -> dict:
        from .. import filler_loop as FLP
        return {"cancelled": FLP.cancel_job()}

    @router.post("/settings/recorder/filler/install", dependencies=[Depends(same_origin)])
    def recorder_filler_install(body: FillerInstall | None = None) -> dict:
        """Refusals (nothing waiting, recording, old firmware) at once; sending it (seconds as data on rec-27, minutes
        as a firmware build before) in the background, its progress in GET's loop.install."""
        from .. import filler_loop as FLP
        from .. import fillers as FI
        target = FI.normalize(body.target) if body and body.target else FLP.installed_target()
        if (FLP.state()["install"] or {}).get("running"):
            raise HTTPException(409, "it's already being installed")
        if not (FLP.paths(target)[1] / "octo_filler_model.h").exists():
            raise HTTPException(409, "no better cue is waiting")
        if FLP.job():
            raise HTTPException(409, f"“{FLP.job()['target']}” is training: install once it's done")
        st = (D.last_status() or {}).get("status") or {}
        if st.get("state") == "recording":
            raise HTTPException(409, "the recorder is recording: switch it off (hold its button 1 s), install, then switch it on")
        if target != FLP.YOU_KNOW and not st.get("filler_models"):
            raise HTTPException(409, "the recorder's firmware only has “you know”: update it to rec-27 or later first")
        return _in_background(lambda: FLP.install(log=lambda m: None, target=target))

    @router.post("/settings/recorder/filler/undo", dependencies=[Depends(same_origin)])
    def recorder_filler_undo() -> dict:
        from .. import filler_loop as FLP
        st = (D.last_status() or {}).get("status") or {}
        if (FLP.state()["install"] or {}).get("running"):
            raise HTTPException(409, "it's already being installed")
        if not st.get("filler_before"):
            raise HTTPException(409, "the recorder has no model before this one")
        if st.get("state") == "recording":
            raise HTTPException(409, "the recorder is recording: switch it off (hold its button 1 s), then undo")
        if FLP.job():
            raise HTTPException(409, f"“{FLP.job()['target']}” is training: undo once it's done")
        FLP._write(FLP.INSTALL_STATE, {"started_at": time.time(), "running": True, "undo": True})
        return _in_background(lambda: FLP.undo(log=lambda m: None))

    def _in_background(fn) -> dict:
        t0 = time.time()

        def go() -> None:
            from .. import filler_loop as FLP
            try:
                fn()
            except Exception as e:  # a failed install records its own; a refusal or a failed undo is recorded here
                last = FLP.state()["install"] or {}
                if last.get("running") or (last.get("finished_at") or 0) < t0:
                    FLP._write(FLP.INSTALL_STATE, {"running": False, "finished_at": time.time(), "error": str(e)})
        threading.Thread(target=go, daemon=True, name="octo-filler-install").start()
        return {"started": True}

    @router.post("/settings/recorder/filler", dependencies=[Depends(same_origin)])
    async def recorder_filler_set(body: Filler) -> dict:
        from .. import device as D

        def go() -> dict:
            with D.holding(db(), wait_s=10):
                st = D.set_filler(D.Device.from_config(), body.on, body.threshold)
            D.save_status(st)
            return st
        try:
            return filler_view(await run_in_threadpool(go))
        except D.DeviceError as e:
            raise HTTPException(503, "the recorder's firmware has no filler model yet" if "no_model" in str(e) else str(e))

    @router.get("/device")
    def device() -> dict:
        last = D.last_status() or {}
        st = last.get("status") or {}
        # Minutes of audio (chunks are about 35 s, not a minute: review 4 Oct, M04).
        waiting = round((row(db(), "SELECT coalesce(sum(samples), 0) n FROM device_chunks WHERE recording_id IS NULL")["n"]
                         / RATE / 60), 1)
        return {
            "configured": D.load_config() is not None,
            "seen_at": last.get("seen_at"), "checked_at": last.get("checked_at"), "error": last.get("error"),
            "syncing": syncer.running,
            "state": st.get("state"), "want_on": st.get("want_on"),
            "card_hours_left": round(st["card_free"] / (RATE * 2) / 3600, 1) if st.get("card_free") else None,
            "battery_mv": st.get("battery_mv") if (st.get("battery_mv") or -1) > 0 else None,
            "rssi": st.get("rssi"), "sd_failures": st.get("sd_failures"), "dropped_samples": st.get("dropped_samples"),
            "uptime_h": round(st["uptime_ms"] / 3.6e6, 1) if st.get("uptime_ms") else None,
            "waiting_on_mac": waiting,
        }

    @router.post("/device/recording", dependencies=[Depends(same_origin)])
    async def device_recording(body: Recording) -> dict:
        def go() -> None:
            with D.holding(db(), wait_s=10):
                st = D.set_recording(D.Device.from_config(), body.on)
            D.save_status(st)
        try:
            await run_in_threadpool(go)
        except D.DeviceError as e:
            raise HTTPException(503, str(e))
        return device()
