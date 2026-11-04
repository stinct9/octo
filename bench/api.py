"""HTTP API for the bench UI. Localhost only.

  GET  /api/events                 Server-Sent Events: the current run (replayed), then live.
                                   Resumes from Last-Event-ID ("<run_id>:<seq>"); a different
                                   run ID gets the whole current run. Also `device` events.
  GET  /api/status                 board port, runner state, current run ID
  GET  /api/steps                  steps in run-all order, with any skip reason
  POST /api/runs                   {"steps": ["all"] | [...], "model"?, "cues"?} -> 202 {run_id}
  POST /api/runs/current/ready     the human gate (mic step)
  POST /api/runs/current/cancel    cancel at the next safe point
  GET  /api/runs                   recent runs
  GET  /api/runs/{id}/events       a stored run, for the history view
  GET  /api/runs/{id}/recording    that run's WAV (looked up by run ID, never by client path)
  /api/octo/*                      the conversation lab (octo/api.py)
  /                                the built UI (ui/dist)
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from octo import phone as PH

from . import AUDIO, ROOT
from .device import find_port
from .events import EPHEMERAL, Event
from .runner import Busy, Runner, Step, recover_if_idle
from .steps import RUN_ALL, STEPS

UI_DIST = ROOT / "ui" / "dist"
RUN_ID = re.compile(r"^\d{8}-\d{6}(-\d+)?$")
HEARTBEAT_S = 15
DEVICE_POLL_S = 1.5



class FollowingHosts(TrustedHostMiddleware):
    """TrustedHostMiddleware whose list follows the Mac's names and addresses (Wi-Fi and Tailscale),
    looked up again at most every 30 s."""

    def __init__(self, app, fixed: list[str]) -> None:
        super().__init__(app, allowed_hosts=fixed)
        self.fixed = list(fixed)
        self.checked = 0.0

    async def __call__(self, scope, receive, send) -> None:
        import time
        if scope["type"] in ("http", "websocket") and time.monotonic() - self.checked > 30:
            self.checked = time.monotonic()
            names = await asyncio.to_thread(lambda: PH.hostnames() + PH.addresses() + PH.tailscale_ips())
            self.allowed_hosts = self.fixed + names
        await super().__call__(scope, receive, send)

class RunRequest(BaseModel):
    steps: list[str] = ["all"]
    model: str = "large-v3"
    cues: bool = True


class Hub:
    """Bridges runner events (worker threads) to SSE clients (asyncio)."""

    def __init__(self, runner: Runner):
        self.runner = runner
        self.loop: asyncio.AbstractEventLoop | None = None
        self.current: list[Event] = []  # the current/last run, minus ephemeral events
        self.clients: set[asyncio.Queue] = set()
        self.port: str | None = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.runner.listeners.append(self._from_thread)

    def _from_thread(self, event: Event) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self._publish, event)

    def _publish(self, event: Event) -> None:
        if event.type == "run.started":
            self.current = []
        if event.type not in EPHEMERAL:
            self.current.append(event)
        self._broadcast(("run", event))

    def _broadcast(self, item: tuple) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                if item[0] == "run" and item[1].type in EPHEMERAL:
                    continue  # a slow client can miss meter ticks
                self.clients.discard(q)  # too far behind: drop it; EventSource reconnects and replays

    def set_port(self, port: str | None) -> None:
        if port != self.port:
            self.port = port
            self._broadcast(("device", {"port": port}))

    def replay(self, last_event_id: str | None) -> list[Event]:
        if last_event_id and ":" in last_event_id:
            run_id, _, seq = last_event_id.rpartition(":")
            if self.current and run_id == self.current[0].run_id and seq.isdigit():
                return [e for e in self.current if e.seq > int(seq)]
        return list(self.current)


def same_origin(request: Request) -> None:
    """Browsers send Origin on every cross-site POST: another website open in the same browser
    mustn't be able to start, cancel or 'Ready' a bench run."""
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
        raise HTTPException(403, "not from this app")


def _sse(event: Event) -> str:
    # Ephemeral events carry no id, so they never move the client's resume point.
    head = "" if event.type in EPHEMERAL else f"id: {event.run_id}:{event.seq}\n"
    return f"{head}event: run\ndata: {event.model_dump_json()}\n\n"


def create_app(runner: Runner | None = None, steps: dict[str, Step] | None = None,
               run_all: list[str] | None = None, poll_device: bool = True, phone_access: bool = True,
               background_processing: bool = False) -> FastAPI:
    runner = runner or Runner()
    steps = steps or STEPS
    run_all = run_all or RUN_ALL
    hub = Hub(runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        recover_if_idle(runner.store)
        hub.attach(asyncio.get_running_loop())
        task = None
        if poll_device:
            async def poll() -> None:
                while True:
                    hub.set_port(await asyncio.to_thread(find_port))
                    await asyncio.sleep(DEVICE_POLL_S)
            task = asyncio.create_task(poll())
        if background_processing:
            app.state.octo_processor.keep_going()  # leftover work now, then hourly (audio deletion)
            app.state.octo_device_sync.keep_going()  # pull from the recorder every 5 min when it's home
            app.state.octo_notifier.keep_going()  # macOS notifications when something needs the owner
            app.state.octo_watchdog.keep_going()  # and when the lab can't save to its folder
            app.state.octo_filler_trainer.keep_going()  # the filler cue's weekly retrain, at night (B22)
            app.state.octo_models.start()  # speech models still missing (a new lab): fetched in the background
            from octo.api import warm
            from octo.db import connect
            threading.Thread(target=warm, args=(connect,), daemon=True, name="octo-warm").start()  # pages fast from the first visit
        if phone_access and PH.enabled():
            await app.state.phone_server.start()  # the Wi-Fi listener for a paired phone (octo/phone_api.py)
        yield
        if phone_access:
            await app.state.phone_server.stop()
        if task:
            task.cancel()
        if runner.running:
            runner.cancel()

    app = FastAPI(title="octo bench", lifespan=lifespan)
    app.state.hub = hub
    app.state.runner = runner

    @app.get("/api/status")
    def status() -> dict:
        return {"port": hub.port if poll_device else find_port(), "state": runner.state,
                "running": runner.running, "run_id": runner.run_id}

    @app.get("/api/steps")
    def list_steps() -> list[dict]:
        return [{"id": sid, "title": steps[sid].title, "needs_user": steps[sid].needs_user,
                 "skip_reason": steps[sid].skip_reason()} for sid in run_all]

    @app.post("/api/runs", status_code=202, dependencies=[Depends(same_origin)])
    def start_run(req: RunRequest) -> dict:
        ids = run_all if req.steps == ["all"] else req.steps
        unknown = [s for s in ids if s not in steps]
        if unknown or not ids:
            raise HTTPException(400, f"unknown step(s): {unknown}")
        try:
            run_id = runner.start([steps[s] for s in ids], {"model": req.model, "cues": req.cues})
        except Busy:
            raise HTTPException(409, "a run is already in progress")
        return {"run_id": run_id}

    @app.post("/api/runs/current/ready", dependencies=[Depends(same_origin)])
    def ready() -> dict:
        if not runner.ready():
            raise HTTPException(409, "not waiting for you")
        return {"ok": True}

    @app.post("/api/runs/current/cancel", dependencies=[Depends(same_origin)])
    def cancel() -> dict:
        if not runner.cancel():
            raise HTTPException(409, "nothing to cancel")
        return {"ok": True}

    @app.get("/api/runs")
    def runs(limit: int = 20) -> list[dict]:
        return [runner.store.summary(r) for r in runner.store.run_ids()[:limit]]

    def _check_id(run_id: str) -> str:
        if not RUN_ID.match(run_id):
            raise HTTPException(404, "no such run")
        return run_id

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str) -> list[dict]:
        events = runner.store.read(_check_id(run_id))
        if not events:
            raise HTTPException(404, "no such run")
        return events

    @app.get("/api/runs/{run_id}/recording")
    def recording(run_id: str) -> FileResponse:
        path = AUDIO / f"bench_{_check_id(run_id)}.wav"
        if not path.exists():
            raise HTTPException(404, "no recording for this run")
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/events")
    async def events(request: Request, once: bool = False) -> StreamingResponse:
        """once=true ends the stream at run.finished (tests, scripts)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        backlog = hub.replay(request.headers.get("last-event-id"))
        hub.clients.add(q)

        async def stream():
            try:
                yield f"retry: 1000\nevent: device\ndata: {json.dumps({'port': hub.port})}\n\n"
                for e in backlog:
                    yield _sse(e)
                    if once and e.type == "run.finished":
                        return
                while True:
                    try:
                        kind, item = await asyncio.wait_for(q.get(), HEARTBEAT_S)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": keep-alive\n\n"
                        continue
                    if kind == "device":
                        yield f"event: device\ndata: {json.dumps(item)}\n\n"
                    else:
                        yield _sse(item)
                        if once and item.type == "run.finished":
                            return
            finally:
                hub.clients.discard(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    from octo.api import make_router
    octo_router = make_router()
    app.include_router(octo_router)
    app.state.octo_processor = octo_router.processor
    app.state.octo_device_sync = octo_router.device_sync
    app.state.octo_notifier = octo_router.notifier
    app.state.octo_watchdog = octo_router.watchdog
    app.state.octo_filler_trainer = octo_router.filler_trainer
    app.state.octo_models = octo_router.models
    from octo.phone_api import require_mac_key, security_headers
    require_mac_key(app)  # the Mac's socket: /api only with the Mac's key (octo/mackey.py)
    security_headers(app)  # no framing by other sites; the app's pages run only the lab's own scripts
    if phone_access:
        from octo.phone_api import install
        install(app)
    # Blocks DNS-rebinding: a malicious page can't reach this server via its own hostname. Added last, so
    # it runs first (before the phone gate). The phone reaches the Mac by its .local name, Wi-Fi address or
    # Tailscale address (only through the paired-phone gate); those change while the server runs, so the
    # list follows them.
    fixed = ["127.0.0.1", "localhost", "testserver"]
    if phone_access:
        app.add_middleware(FollowingHosts, fixed=fixed)
    else:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=fixed)
    # /about and /case, the pages about Octo itself, registered before the UI takes "/"
    from octo.pages import make_router as pages
    app.include_router(pages())

    if UI_DIST.exists():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    else:
        @app.get("/", response_class=HTMLResponse)
        def placeholder() -> str:
            return ("<!doctype html><title>octo bench</title><body style='font:16px system-ui;padding:2rem'>"
                    "<h1>octo bench API is running</h1><p>The UI isn't built yet (milestone M3). "
                    "Try <a href='/api/steps'>/api/steps</a> or <code>bench run all</code>.</p>")

    return app
