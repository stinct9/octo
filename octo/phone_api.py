"""Phone access for the web app (U4) and the Octo app (8 Oct): the HTTPS listener, the gate, pairing, and
Mac-only settings.

Listeners serving the same app:
  127.0.0.1:8765        (plain HTTP)  this Mac only, as before: everything, including the bench
  <Wi-Fi IP>:8766       (HTTPS)       the home Wi-Fi only, only when switched on
  <Tailscale IP>:8766   (HTTPS)       whenever Tailscale is up and phone access is on (8 Oct)
Both HTTPS sockets: paired phones only, and only the routes in PHONE_ROUTES (the reviews, the people, the
coach, the progress, the recorder's switch); deleting and forgetting from the home Wi-Fi only; never the
bench, the settings or the precision tools. **Deny by default:** only a request on the Mac's own loopback
socket, addressed to a loopback name and not forwarded by a proxy, is the Mac. Anything else (another
socket, `tailscale serve` pointed at 127.0.0.1:8765, a header saying it was forwarded) gets the phone's
rules. The Mac's socket wants the Mac's key on every /api call (`octo/mackey.py`, review 8 Oct, finding 4).

  GET  /pair                      Safari's "type the code from your Mac" page
  POST /api/pair                  {"code"|"secret", "name", "app"?} -> device cookie (and the token, for the app)
  GET  /api/octo/phone/hello      (paired phone or Mac) this Mac's name and where to reach it; to a phone, what's
                                  waiting (notify.py's keys: counts for the phone's own notifications)
  GET  /api/octo/phone            (Mac) on/off, where to point the phone, paired phones, Tailscale and the next
                                  step to reach the lab away from home (19 Oct)
  POST /api/octo/phone            (Mac) {"enabled"}
  POST /api/octo/phone/code       (Mac) a new 8-digit pairing code, 2 minutes (Safari)
  POST /api/octo/phone/qr         (Mac) a QR code for the app: a one-time secret, 2 minutes; switches on
  POST /api/octo/phone/unpair     (Mac) {"id"}
  GET  /api/octo/phone/ca.crt     (Mac) the certificate to AirDrop to the iPhone (Safari)
  GET  /let-in?c=&to=             (Mac) lets this browser in with a one-time code (`octo open`), then on to `to`
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import secrets
import socket
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import mackey as MK
from . import phone as PH
from .api import same_origin
from .db import connect


class PhoneServer:
    """The HTTPS listener, started and stopped at runtime from the Mac's settings. It listens on the home
    Wi-Fi address while the Mac is at home and on the Tailscale address while Tailscale is up; a minute's
    check follows both (the Mac leaving home, Tailscale starting or stopping, an address changing)."""

    def __init__(self, app: FastAPI, host: str | None = None, port: int | None = None) -> None:
        self.app = app
        self.host, self.port = host, PH.PORT if port is None else port  # read now, not when this module loaded
        self.server = None
        self.task: asyncio.Task | None = None
        self.watch: asyncio.Task | None = None
        self.error: str | None = None
        self.bound: list[str] = []
        self.wanted_on = False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def _wanted(self) -> tuple[list[str], str | None]:
        """Where to listen now, and why not on the home Wi-Fi when that's left out."""
        hosts, why = [], None
        if self.host:
            return [self.host], None
        # The Wi-Fi address only on the home network, never every interface (M43).
        if await asyncio.to_thread(PH.at_home):
            wifi = next(iter(await asyncio.to_thread(PH.addresses)), None)
            if wifi:
                hosts.append(wifi)
            else:
                why = "no Wi-Fi address"
        else:
            why = "not on the home network"
        hosts += await asyncio.to_thread(PH.tailscale_ips)
        return hosts, why

    async def start(self) -> None:
        self.wanted_on = True
        if self.running:
            return
        await self._open()
        if not self.watch or self.watch.done():
            self.watch = asyncio.create_task(self._watch())

    async def _open(self) -> None:
        import uvicorn
        hosts, why = await self._wanted()
        if not hosts:
            self.error = f"{why}: the phone listener stays off here" if why else "no address to listen on"
            return
        try:
            certs = await asyncio.to_thread(PH.ensure_certs)
        except RuntimeError as e:
            self.error = str(e)
            return
        # Bind the ports here, not in uvicorn: if one is taken, uvicorn calls sys.exit(), which would
        # take the whole app (the Mac's listener too) down with it.
        socks, failed = [], []
        for host in hosts:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, self.port))
                socks.append(sock)
            except OSError as e:
                sock.close()
                failed.append(f"{host}:{self.port} ({e.strerror})")
        if not socks:
            self.error = f"port {self.port} is in use: {', '.join(failed)}"
            return
        self.bound = [sk.getsockname()[0] for sk in socks]
        # lifespan off: the Mac's listener already runs the app's start-up and shut-down.
        config = uvicorn.Config(self.app, ssl_keyfile=str(certs["key"]), ssl_certfile=str(certs["cert"]),
                                lifespan="off", log_level="warning")
        self.server = uvicorn.Server(config)
        # _serve rather than serve: serve() would take over Ctrl-C from the Mac's listener.
        self.task = asyncio.create_task(self.server._serve(sockets=socks))
        self.error = (f"{why}: listening on Tailscale only" if why else None) if not failed else f"couldn't listen on {', '.join(failed)}"

    async def _watch(self) -> None:
        """Follow the Mac: leave the home Wi-Fi when it leaves home, add or drop Tailscale as it comes and goes."""
        while self.wanted_on:
            await asyncio.sleep(60)
            if not self.wanted_on:
                return
            hosts, _ = await self._wanted()
            if sorted(hosts) != sorted(self.bound) or not self.running:
                await self._close()
                await self._open()

    async def _close(self) -> None:
        if self.server and self.running:
            self.server.should_exit = True
            await self.task
        self.server = self.task = None
        self.bound = []

    async def stop(self) -> None:
        self.wanted_on = False
        if self.watch and not self.watch.done():
            self.watch.cancel()
        self.watch = None
        await self._close()


class PairBody(BaseModel):
    code: str = ""
    secret: str = ""
    name: str = "Phone"
    app: bool = False  # the Octo app: the token in the answer too, to keep in its Keychain


class Toggle(BaseModel):
    enabled: bool


class Unpair(BaseModel):
    id: str


PAIR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pair with your Mac</title>
<style>
:root{color-scheme:light dark;--bg:#F4F2ED;--card:#fff;--ink:#1B1A17;--muted:#5B574F;--accent:#1D4F7A;--line:#E2DED5;--fail:#A8261B}
@media (prefers-color-scheme:dark){:root{--bg:#151412;--card:#1E1D1A;--ink:#EDEAE3;--muted:#A8A399;--accent:#6FA8D8;--line:#34322D;--fail:#EE8A7E}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.5 -apple-system,system-ui,sans-serif}
main{max-width:420px;margin:0 auto;padding:40px 20px;display:flex;flex-direction:column;gap:16px}
h1{margin:0;font-size:26px}p{margin:0;color:var(--muted)}
input{width:100%;min-width:0;font:inherit;font-size:28px;letter-spacing:6px;text-align:center;padding:12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
button{font:inherit;font-weight:600;min-height:52px;border:0;border-radius:12px;background:var(--accent);color:#fff}
#err{color:var(--fail);min-height:1.5em}
</style></head><body><main>
<h1>Pair with your Mac</h1>
<p>On the Mac, open Octo → Phone → Or use Safari, and choose "Show a pairing code". Type it here.</p>
<form id="f"><input id="code" size="8" inputmode="numeric" autocomplete="one-time-code" maxlength="8" aria-label="Pairing code" autofocus>
</form><button form="f">Pair</button><div id="err" role="alert"></div>
<p>Only on your home Wi-Fi, while the Mac is awake. Nothing goes through the internet.</p>
</main><script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault()
  const r = await fetch('/api/pair', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({code: document.getElementById('code').value, name: /iPhone/.test(navigator.userAgent) ? 'iPhone' : 'Phone'})})
  if (r.ok) location.href = '/'
  else document.getElementById('err').textContent = (await r.json()).detail || 'That did not work.'
})
</script></body></html>"""


# The Mac's own socket answers to these names only; TestClient's "testserver" stands in for it in tests.
MAC_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
FORWARDED = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip", "tailscale-user-login")


def on_phone_listener(request: Request) -> bool:
    """Deny by default (review 8 Oct): the Mac is only its loopback socket, asked by a loopback name, with
    no sign of a proxy. Everything else, including `tailscale serve` pointed at 127.0.0.1:8765, is a phone."""
    server = getattr(request.app.state, "phone_server", None)
    addr, port = (request.scope.get("server") or (None, None))[:2]
    if port == (server.port if server else PH.PORT) or addr not in MAC_HOSTS:
        return True
    if host_name(request.headers.get("host", "")) not in MAC_HOSTS:
        return True
    return any(h in request.headers for h in FORWARDED)


# What a paired phone may call (review 8 Oct; the product review's §2.3, "what belongs where"): two-minute jobs
# and the table. ANYWHERE is the home Wi-Fi and Tailscale; HOME is the home Wi-Fi only, for what can't be
# undone (product review: "decide with everything in view"). Every other /api route is the Mac's: the bench,
# the settings and keys, the pipeline, phone pairing, and the precision work against the transcript (split,
# merge, move lines, undo, leave out, delete a part, the fillers check by ear). A new route is the Mac's
# until it's added here, and tests/test_phone_routes.py holds the same list, so adding one is a decision.
ANYWHERE, HOME = "anywhere", "home"
PHONE_ROUTES: dict[tuple[str, str], str] = {
    # reading
    ("GET", "/api/octo/sessions"): ANYWHERE,
    ("GET", "/api/octo/sessions/changed"): ANYWHERE,  # what the list polls while busy (review 28 Oct, item 3)
    ("GET", "/api/octo/search"): ANYWHERE,  # B12: read only
    ("GET", "/api/octo/ask"): ANYWHERE,  # B24: the answers on the page and the kept ones
    ("GET", "/api/octo/ask/{ask_id}"): ANYWHERE,
    ("GET", "/api/octo/sessions/{session_id}"): ANYWHERE,
    ("GET", "/api/octo/sessions/{session_id}/audio"): ANYWHERE,  # clips anywhere (8 Oct, Q2)
    ("GET", "/api/octo/sessions/{session_id}/ticktick/done"): ANYWHERE,
    ("GET", "/api/octo/actions"): ANYWHERE,
    ("GET", "/api/octo/commitments"): ANYWHERE,  # B14: open both ways with a person
    ("GET", "/api/octo/commitments/check"): ANYWHERE,  # B14: the owner's check of the step
    ("GET", "/api/octo/actions/ticktick"): ANYWHERE,
    ("GET", "/api/octo/ticktick"): ANYWHERE,  # connected or not; never the token
    ("GET", "/api/octo/focus"): ANYWHERE,
    ("GET", "/api/octo/fillers"): ANYWHERE,
    ("GET", "/api/octo/fillers/live"): ANYWHERE,  # B21: the list for the phone's live cue, cached there for away
    ("GET", "/api/octo/coach"): ANYWHERE,
    ("GET", "/api/octo/coach/{note_id}"): ANYWHERE,
    ("GET", "/api/octo/progress"): ANYWHERE,
    ("GET", "/api/octo/progress/states"): ANYWHERE,
    ("GET", "/api/octo/incoming"): ANYWHERE,
    ("GET", "/api/octo/aside"): ANYWHERE,
    ("GET", "/api/octo/questions"): ANYWHERE,
    ("GET", "/api/octo/questions/check"): ANYWHERE,
    ("GET", "/api/octo/status"): ANYWHERE,
    ("GET", "/api/octo/device"): ANYWHERE,
    ("GET", "/api/octo/people"): ANYWHERE,
    ("GET", "/api/octo/people/{person_id}"): ANYWHERE,
    ("GET", "/api/octo/people/{person_id}/quiet"): ANYWHERE,  # B17
    ("GET", "/api/octo/week"): ANYWHERE,  # B17: your week's card
    ("GET", "/api/octo/agenda"): ANYWHERE,  # B34: home's agenda
    ("GET", "/api/octo/phone/hello"): ANYWHERE,
    ("GET", "/api/octo/drills"): ANYWHERE,  # B18: practice, kept apart from conversations
    ("GET", "/api/octo/drills/{drill_id}"): ANYWHERE,
    ("GET", "/api/octo/drills/{drill_id}/audio"): ANYWHERE,  # as clips (Q2)
    ("GET", "/api/octo/rehearsals"): ANYWHERE,  # B26: practice, kept apart from conversations
    ("GET", "/api/octo/rehearsals/{rehearsal_id}"): ANYWHERE,
    ("GET", "/api/octo/sessions/{session_id}/talk"): ANYWHERE,  # B31: talking it through
    ("GET", "/api/octo/calendar/month"): ANYWHERE,  # B37: a month of conversations, read only
    ("GET", "/api/octo/calendar/day"): ANYWHERE,
    ("GET", "/api/octo/people/{person_id}/prepare"): ANYWHERE,  # B36: a plan before a conversation, read on the way
    ("GET", "/api/octo/people/{person_id}/prepare/pick"): ANYWHERE,
    ("GET", "/api/octo/prefs"): ANYWHERE,  # the owner's folds and views, the same on every device (4 Nov)
    # answering and marking
    ("POST", "/api/octo/sessions/{session_id}/answer"): ANYWHERE,
    ("POST", "/api/octo/sessions/{session_id}/feedback"): ANYWHERE,
    ("POST", "/api/octo/actions/let-go"): ANYWHERE,  # B40: each item's own Let it go, as feedback and commitments are
    ("POST", "/api/octo/sessions/{session_id}/focus"): ANYWHERE,
    ("POST", "/api/octo/sessions/{session_id}/reflection"): ANYWHERE,  # B15: in the owner's words
    ("POST", "/api/octo/sessions/{session_id}/speakers/{letter}"): ANYWHERE,  # "Yes, it's me", who someone is
    ("POST", "/api/octo/sessions/{session_id}/ticktick"): ANYWHERE,  # the Mac sends it
    ("POST", "/api/octo/commitments/{commitment_id}"): ANYWHERE,  # Done / Dropped, They did / They didn't
    ("POST", "/api/octo/commitments/{commitment_id}/ticktick"): ANYWHERE,  # the Mac sends it
    ("POST", "/api/octo/commitments/{commitment_id}/check"): ANYWHERE,  # right / partly / wrong; changes nothing else
    ("POST", "/api/octo/sessions/{session_id}/keep"): ANYWHERE,
    ("POST", "/api/octo/sessions/{session_id}/aside"): ANYWHERE,  # not a conversation; put back as easily
    ("POST", "/api/octo/sessions/{session_id}/retry"): ANYWHERE,
    ("POST", "/api/octo/sessions/{session_id}/provisional"): ANYWHERE,
    ("POST", "/api/octo/sessions/{session_id}/redo-review"): ANYWHERE,
    ("POST", "/api/octo/questions/check"): ANYWHERE,  # what he heard; changes nothing else
    ("POST", "/api/octo/fillers/words"): ANYWHERE,  # a word on or off his list: counted at read time, nothing re-runs
    ("POST", "/api/octo/delivery/hedges"): ANYWHERE,  # the same for his hedges (B10)
    ("POST", "/api/octo/focus"): ANYWHERE,
    ("POST", "/api/octo/focus/stop"): ANYWHERE,
    ("PUT", "/api/octo/focus/{focus_id}/solved"): ANYWHERE,  # B38: the owner's words, like the focus itself
    ("POST", "/api/octo/coach"): ANYWHERE,  # runs on the Mac, counts toward the day's calls
    ("POST", "/api/octo/ask"): ANYWHERE,  # B24: picked on the Mac, one call, counts toward the day's calls
    ("POST", "/api/octo/ask/{ask_id}/keep"): ANYWHERE,  # stores an answer the owner already has on the page
    ("POST", "/api/octo/ask/{ask_id}/delete"): ANYWHERE,  # deletes only an answer, never a conversation
    ("POST", "/api/octo/coach/{note_id}/feedback"): ANYWHERE,
    ("PUT", "/api/octo/coach/{note_id}/solved"): ANYWHERE,
    ("POST", "/api/octo/coach/{note_id}/focus"): ANYWHERE,
    ("POST", "/api/octo/coach/{note_id}/seen"): ANYWHERE,
    ("POST", "/api/octo/people/{person_id}/consent"): ANYWHERE,  # "They agreed"
    ("POST", "/api/octo/people/{person_id}/quiet"): ANYWHERE,  # B17: mute "Octo hasn't heard from…"; undone as easily
    ("POST", "/api/octo/week/dismiss"): ANYWHERE,  # B17: the card goes until next Monday
    ("POST", "/api/octo/people/{person_id}/intention"): ANYWHERE,  # B15: next time with them
    ("POST", "/api/octo/marks"): ANYWHERE,  # B16: the Mark button, by the Mac's clock
    ("POST", "/api/octo/marks/{mark_id}/delete"): ANYWHERE,  # its Undo: the phone's own mark, within a minute
    ("POST", "/api/octo/drills"): ANYWHERE,  # B18: a take, as a memo
    ("POST", "/api/octo/drills/prompts"): ANYWHERE,  # a prompt on or off his list
    ("POST", "/api/octo/rehearsals"): ANYWHERE,  # B26: typed; each line and the review run on the Mac, in the day's calls
    ("POST", "/api/octo/rehearsals/{rehearsal_id}/say"): ANYWHERE,
    ("POST", "/api/octo/rehearsals/{rehearsal_id}/stop"): ANYWHERE,
    ("POST", "/api/octo/sessions/{session_id}/talk"): ANYWHERE,  # B31: one call, run on the Mac, in the day's calls
    ("POST", "/api/octo/people/{person_id}/prepare"): ANYWHERE,  # B36: as Ask, picked and asked on the Mac
    ("POST", "/api/octo/people/prepare"): ANYWHERE,  # B36: someone new, added by name (no voiceprint) to prepare for
    ("POST", "/api/octo/people/{person_id}/prepare/delete"): ANYWHERE,  # a plan, never a conversation, as Ask's Delete
    ("POST", "/api/octo/recordings"): ANYWHERE,  # a memo; over Tailscale only end-to-end encrypted (as Q2)
    ("POST", "/api/octo/device/recording"): ANYWHERE,
    ("PUT", "/api/octo/prefs/{key}"): ANYWHERE,  # a fold opened or closed: a page's look, nothing else
    # can't be undone: from the home Wi-Fi only (each also asks on the page)
    ("POST", "/api/octo/sessions/{session_id}/delete"): HOME,
    ("POST", "/api/octo/drills/{drill_id}/delete"): HOME,
    ("POST", "/api/octo/rehearsals/{rehearsal_id}/delete"): HOME,
    ("POST", "/api/octo/sessions/{session_id}/talk/clear"): HOME,  # B31: Start over
    ("POST", "/api/octo/people/forget"): HOME,
    ("POST", "/api/octo/people/rename"): HOME,  # a rename can merge two people's voiceprints
    ("POST", "/api/octo/people/{person_id}/consent/withdraw"): HOME,  # deletes their voiceprints
}

TAILNET = ipaddress.ip_network("100.64.0.0/10")


# Matched against the templates themselves ({param} is one path segment): FastAPI's own route table is
# private API since 0.142. tests/test_phone_routes.py asks this for every route the app has, so a Mac route
# that one of these templates would also match fails there.
_PATTERNS = [(m, re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(t)) + "$"), where)
             for (m, t), where in PHONE_ROUTES.items()]


def phone_rule(method: str, path: str) -> str | None:
    """ANYWHERE, HOME, or None (the Mac's) for a phone's request."""
    method = "GET" if method == "HEAD" else method
    if (method, path) in PHONE_ROUTES:
        return PHONE_ROUTES[(method, path)]
    return next((where for m, rx, where in _PATTERNS if m == method and rx.match(path)), None)


def over_tailscale(request: Request) -> bool:
    """The phone came in on the Tailscale socket."""
    try:
        return ipaddress.ip_address((request.scope.get("server") or ("",))[0]) in TAILNET
    except ValueError:
        return False


def at_home(request: Request) -> bool:
    """The phone came in on the home Wi-Fi socket: a private address that isn't Tailscale's, this Mac's
    loopback (a proxy in front of it is never home) or link-local, with no sign of a proxy."""
    addr = (request.scope.get("server") or ("",))[0]
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if any(h in request.headers for h in FORWARDED):
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_link_local and ip not in TAILNET


# Off in tests (tests/conftest.py), which call the Mac's routes all day; tests/test_mac_key.py turns it on.
REQUIRE_MAC_KEY = True


# Security review L3 (28 Oct): no other site may frame the lab's pages, and the app's own pages run only the lab's own
# scripts, which limits what any future injection could do on an origin that can read and delete everything. The pages
# with their own inline code (the landing page, the case, pairing) keep only the framing rules.
FRAMING = "frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
APP_CSP = (f"default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
           f"form-action 'self'; {FRAMING}")
OWN_INLINE = ("/about", "/case", "/pair")


def security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        own = request.url.path.rstrip("/") in OWN_INLINE
        response.headers.setdefault("Content-Security-Policy", FRAMING if own else APP_CSP)
        return response


def require_mac_key(app: FastAPI) -> None:
    """The Mac's socket answers /api only with the Mac's key (review 8 Oct, finding 4). Before, any program on
    this Mac, or another macOS user, could read every conversation or point the reviews at their own server."""
    on = REQUIRE_MAC_KEY
    if on:
        MK.key()  # made now, not on the first request
    command = MK.open_command()

    @app.middleware("http")
    async def mac_key(request: Request, call_next):
        # A phone's request is the phone gate's to check (install, below); with no gate here it needs the key like any
        # other (security review L1, 28 Oct: the demo lab, asked by another host name, answered without one).
        if not on or (on_phone_listener(request) and getattr(request.app.state, "phone_server", None)):
            return await call_next(request)
        key = MK.key()  # read each time (a small file): `octo open --rotate` signs everyone out without a restart
        path = request.url.path
        held = request.cookies.get(MK.COOKIE)
        if path.startswith("/api/") and not (held and secrets.compare_digest(held, key)):
            from . import launcher as LN
            return JSONResponse({"detail": f"This browser isn't let in to Octo yet. In Terminal: {command}",
                                 "let_in": True, "command": command, "app": LN.found() is not None,
                                 "window": LN.native()}, status_code=401)
        response = await call_next(request)
        if path in ("/api/octo/status", "/api/status"):  # each visit keeps the cookie for another 400 days
            response.set_cookie(MK.COOKIE, key, max_age=MK.MAX_AGE, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/mac-hello", include_in_schema=False)
    def mac_hello(request: Request, c: str = ""):
        """The lab proves it holds the key before the Octo app sends it (architecture review, 19 Oct): while the lab
        is down, another user's program could be the one listening on 127.0.0.1:8765."""
        if not on or on_phone_listener(request) or not 16 <= len(c) <= 128:
            raise HTTPException(404)
        return {"proof": MK.proof(c)}

    @app.get("/let-in", include_in_schema=False)
    def let_in(request: Request, c: str = "", to: str = "/"):
        if not on or on_phone_listener(request):
            raise HTTPException(404)
        key = MK.key()
        if not MK.redeem(c[:200], value=key):
            return HTMLResponse("<!doctype html><title>Octo</title><p style='font:17px system-ui;padding:2rem'>"
                                f"That link has been used or is out of date. Open Octo from your Applications folder, or "
                                f"in Terminal: <code>{command}</code></p>", status_code=403)
        # 303 to the page: the code leaves the address bar at once (it stays in history, spent).
        resp = RedirectResponse(MK.safe_target(to), status_code=303)
        resp.set_cookie(MK.COOKIE, key, max_age=MK.MAX_AGE, httponly=True, samesite="strict", path="/")
        return resp


def host_name(header: str) -> str:
    """The name in a Host header, without its port: "127.0.0.1:8765" → "127.0.0.1", "[::1]:8765" → "::1"."""
    h = header.strip().lower()
    if h.startswith("["):
        return h[1:h.index("]")] if "]" in h else h
    return h.split(":", 1)[0] if h.count(":") == 1 else h


def install(app: FastAPI, db: Callable = connect) -> PhoneServer:
    pairing = PH.Pairing()
    server = PhoneServer(app)
    app.state.phone_server = server

    def mac_only(request: Request) -> None:
        if on_phone_listener(request):
            raise HTTPException(403, "only on the Mac")

    @app.middleware("http")
    async def gate(request: Request, call_next):
        """Requests on a phone socket need a paired phone, and reach only PHONE_ROUTES."""
        if not on_phone_listener(request):
            return await call_next(request)
        path = request.url.path
        if path in ("/pair", "/api/pair"):
            return await call_next(request)
        device = await asyncio.to_thread(lambda: PH.device_for(db(), request.cookies.get(PH.COOKIE)))
        if device and over_tailscale(request):
            await asyncio.to_thread(PH.seen_away, device["id"])  # the Phone page's "reached away from home"
        if not device:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "pair this phone first"}, status_code=401)
            return RedirectResponse("/pair", status_code=303)
        if path.startswith("/api/"):
            where = phone_rule(request.method, path)
            if where is None:
                return JSONResponse({"detail": "That's on the Mac only."}, status_code=403)
            if where == HOME and not at_home(request):
                return JSONResponse({"detail": "That's at home only: on your home Wi-Fi, or on the Mac.",
                                     "at_home_only": True}, status_code=403)
        return await call_next(request)

    @app.get("/pair", response_class=HTMLResponse)
    def pair_page() -> str:
        return PAIR_PAGE

    @app.post("/api/pair", dependencies=[Depends(same_origin)])
    def pair(body: PairBody, request: Request) -> JSONResponse:
        ok = pairing.use_secret(body.secret) if body.secret else pairing.use(body.code, request.client.host if request.client else "")
        if not ok:
            raise HTTPException(403, "That code didn't work. Show a new one on the Mac.")
        token = PH.add_device(db(), body.name)
        resp = JSONResponse({"paired": True, "mac": PH.mac_name(), **({"token": token} if body.app else {})})
        resp.set_cookie(PH.COOKIE, token, max_age=365 * 86400, secure=True, httponly=True, samesite="strict", path="/")
        return resp

    def reach() -> dict:
        """Where a phone finds this Mac now: its home names and addresses, then Tailscale."""
        home = PH.hostnames() + PH.addresses()
        # The last Tailscale address too while Tailscale is off: the phone keeps what it's told (13 Oct).
        tail = PH.tailscale_to_tell() if PH.ca_version() >= 2 or not (PH.DIR / "ca.crt").exists() else []
        return {"hosts": home + tail, "home": home, "tailscale": tail, "port": server.port}

    @app.get("/api/octo/phone/hello")
    def hello(request: Request) -> dict:
        """For the app: this Mac's name and where to reach it (the addresses change; the app keeps up). To a paired
        phone, also what's waiting for its owner, as the Mac's own notifications see it (`octo/notify.py`): keys
        like "review:<session>", never names or text. The app keeps the keys it has seen and, in the background,
        tells only new ones, in counts (8 Oct: notifications on the phone, no server)."""
        from . import notify as NT
        conn = db()
        device = PH.device_for(conn, request.cookies.get(PH.COOKIE)) if on_phone_listener(request) else None
        out = {"mac": PH.mac_name(), **reach(), "device": device["name"] if device else None,
               "ca_sha256": PH.ca_sha256()}
        if device:
            out["waiting"] = sorted(NT.waiting(conn))
        return out

    @app.post("/api/octo/phone/qr", dependencies=[Depends(mac_only), Depends(same_origin)])
    async def qr() -> dict:
        """A QR code for the Octo app. Showing it switches phone access on: the intent is clear."""
        if not PH.enabled():
            PH.set_enabled(True)
        await asyncio.to_thread(PH.ensure_certs)
        await server.start()
        r = reach()
        secret = pairing.new_secret()
        link = PH.pair_link(secret["secret"], r["hosts"], server.port)
        import segno
        # omitsize: a viewBox instead of a fixed width and height, so the page's 240 px box scales the whole code.
        # Without it the box cropped it to its top-left corner (8 Oct: two of its three corner marks cut off, so
        # no camera could read it). Border 4: the quiet zone the standard asks for.
        svg = segno.make(link, error="m").svg_inline(scale=5, border=4, dark="#1B1A17", light="#FFFFFF", omitsize=True)
        return {"link": link, "svg": svg, "expires_in": secret["expires_in"], "running": server.running,
                "error": server.error, **r}

    @app.get("/api/octo/phone", dependencies=[Depends(mac_only)])
    def status() -> dict:
        names, ips = PH.hostnames(), PH.addresses()
        devices, renewed = PH.devices(db()), PH._settings().get("ca_renewed_at")
        ts = PH.tailscale_status()
        return {"enabled": PH.enabled(), "running": server.running, "error": server.error,
                "port": PH.PORT, "urls": [f"https://{h}:{PH.PORT}" for h in names + ips],
                "ca_ready": (PH.DIR / "ca.crt").exists(), "devices": devices,
                "tailscale": ts["ips"], "listening": server.bound, "ca_renewed_at": renewed,
                "away": {**ts, **PH.away_step(ts, PH.enabled(), devices, renewed)}}

    @app.post("/api/octo/phone", dependencies=[Depends(mac_only), Depends(same_origin)])
    async def toggle(body: Toggle) -> dict:
        PH.set_enabled(body.enabled)
        if body.enabled:
            await server.start()
        else:
            await server.stop()
        return {"enabled": body.enabled, "running": server.running, "error": server.error}

    @app.post("/api/octo/phone/code", dependencies=[Depends(mac_only), Depends(same_origin)])
    def new_code() -> dict:
        return pairing.new_code()

    @app.post("/api/octo/phone/unpair", dependencies=[Depends(mac_only), Depends(same_origin)])
    def unpair(body: Unpair) -> dict:
        if not PH.unpair(db(), body.id):
            raise HTTPException(404, "no such phone")
        return {"unpaired": body.id}

    @app.get("/api/octo/phone/ca.crt", dependencies=[Depends(mac_only)])
    def ca() -> FileResponse:
        PH.ensure_certs()
        return FileResponse(PH.DIR / "ca.crt", media_type="application/x-x509-ca-cert",
                            filename="Octo-Mac.crt")

    return server
