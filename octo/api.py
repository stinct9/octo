"""HTTP API for the conversation lab, served by `bench serve` next to the bench API.

  GET /api/octo/sessions                 conversations, newest first, each with what's left to do
  GET /api/octo/search?q=&person=&who=   lines and review text containing q (B12, octo/search.py)
  GET /api/octo/sessions/{id}            one conversation: speakers, lines, numbers, insights
  GET /api/octo/sessions/{id}/audio      its audio (WAV, byte ranges for seeking)
  GET /api/octo/people                   everyone with a voiceprint
  GET /api/octo/people/{id}              one person, before you talk: conversations, steps, readings, numbers
  POST /api/octo/sessions/{id}/speakers/{letter}   {"action": "confirm" | "reject" | "name", "name"?}
  POST /api/octo/people/forget           {"name", "confirm"}: confirm must repeat the name
  POST /api/octo/people/rename           {"name", "to", "merge"?}: everywhere; merge if `to` exists
  POST /api/octo/sessions/{id}/feedback  {"run", "item", "verdict"?, "novelty"?, "useful"?, "acted"?, "note"?}
  GET  /api/octo/progress                reviewed / new / acted, principle coverage, weekly numbers
  GET  /api/octo/focus                   what the owner is practising, and the ones before (octo/focus.py)
  POST /api/octo/focus                   {"text", "session_id"?, "run"?, "item"?}: practise this (ends the last)
  POST /api/octo/focus/stop              no focus for now
  POST /api/octo/sessions/{id}/focus     {"focus_id", "answer": "did"|"missed"|"na"|null}: did it come up here?
  POST /api/octo/sessions/{id}/reflection   {"text"}: how it went, in the owner's words ("" clears it; B15)
  POST /api/octo/people/{id}/intention   {"text"}: next time with them, I want to… ("" clears it; B15)
  POST /api/octo/marks                   this mattered: a mark now, by the Mac's clock (the phone's Mark button; B16)
  POST /api/octo/marks/{id}/delete       Undo: a phone's mark from the last minute, gone
  GET  /api/octo/sessions/{id}/ticktick/done   which of its sent tasks are completed in TickTick
  GET  /api/octo/commitments?person=   open commitments both ways with that person (B14, octo/commitments.py)
  POST /api/octo/commitments/{id}       {"state": "done"|"dropped" (yours) | "did"|"didnt" (theirs) | null}
  POST /api/octo/commitments/{id}/ticktick   one of yours into TickTick, once
  GET  /api/octo/commitments/check       every commitment, oldest first, and the owner's check so far (B14, 21 Oct)
  POST /api/octo/commitments/{id}/check {"verdict": "right"|"partly"|"wrong"|null}
  GET  /api/octo/week?week=             your week's card (B17, octo/week.py): home's, or None; with a Monday, that week's
  POST /api/octo/week/dismiss           {"week"}: home's card goes until the next Monday's
  GET  /api/octo/agenda                 today's and tomorrow's calendar events, who's in each, what was recorded (B34)
  GET  /api/octo/people/{id}/quiet      their usual gap, how long since, the "hasn't heard" line, muted?
  POST /api/octo/people/{id}/quiet      {"muted"}: never flag them, or flag them again
  GET  /api/octo/actions                every open "What to do" step from current reviews
  GET  /api/octo/actions/ticktick       which of those are completed in TickTick
  POST /api/octo/recordings?name=&source=   the raw audio file as the body; header X-Octo-Upload: 1
  GET  /api/octo/incoming                recent recordings still being processed, or that found nothing
  POST /api/octo/sessions/{id}/keep      restore a conversation dropped because the owner's voice wasn't heard
  POST /api/octo/sessions/{id}/aside     {"kind": "tv"|"music"|"other"|null, "undo"?}: not a conversation, or it is one;
                                         undo: no answer at all (B5)
  GET  /api/octo/aside                   what's set aside as not conversations
  GET  /api/octo/questions               others' questions and who spoke next, by 1:1 and group (B4)
  GET  /api/octo/questions/check         the 1:1 questions to hear, and what the owner heard (B4)
  POST /api/octo/questions/check         {"session_id", "start", "verdict": "answered"|"unanswered"|"not_question"|null}
  POST /api/octo/sessions/{id}/retry     run its failed steps again now
  POST /api/octo/sessions/{id}/redo-review   replace a rated review with a fresh one
  POST /api/octo/sessions/{id}/provisional   {"clear": true}: its review counts in full (J9)
  GET  /api/octo/status                  what the background processor is doing (queue_empty_at: when it's done, B3);
                                         code_changed: restart me;
                                         cant_save: the lab can't save to its folder (octo/watchdog.py)
  POST /api/octo/sessions/{id}/speakers/{letter}/merge  {"into"}: they're the same person
  POST /api/octo/sessions/{id}/lines/{line}             {"to"}: someone else said this line
  POST /api/octo/sessions/{id}/corrections/undo         take back the last of those
  POST /api/octo/sessions/{id}/exclude   {"letter", "left_out"}: keep a speaker's words out of insights
  POST /api/octo/sessions/{id}/delete-range  {"start", "end"} seconds into the conversation
  POST /api/octo/sessions/{id}/delete    {"confirm": "delete", "work"?}: the whole conversation
  POST /api/octo/sessions/{id}/join-back  undo the split at its start (or end): one conversation again
  POST /api/octo/sessions/{id}/split     {"at"}: seconds in, or a list of them; each later part becomes a new conversation
  POST /api/octo/sessions/{id}/group     {"side": "before"|"after"}: one discussion in parts with its neighbour (B35, Mac only)
  POST /api/octo/sessions/{id}/ungroup   {"first", "second"}: not one discussion; the lab never groups them again (Mac only)
  GET  /api/octo/device                  the recorder: what it was doing when last seen on the Wi-Fi
  POST /api/octo/device/recording        {"on"}: start or stop recording (the device remembers)
  GET  /api/octo/settings/recorder/filler  the filler cue (B20): on the recorder's firmware?, on, threshold (Mac only)
  POST /api/octo/settings/recorder/filler  {"on", "threshold"?}: switch it; the recorder keeps it
                                         (GET's "loop", B22: the live cue's last 7 days labelled by the transcript,
                                         the installed model, the last retrain, a better one waiting)
  POST /api/octo/settings/recorder/filler/install  {"target"?}: a better cue onto the recorder, in the background
                                         (recording off; another filler's needs rec-27, B29)
  POST /api/octo/settings/recorder/filler/undo  the recorder's model before back (rec-27), in the background
  POST /api/octo/settings/recorder/filler/train  {"target"}: train a cue for a filler, one job at a time (B29, Mac only)
  POST /api/octo/settings/recorder/filler/train/cancel  stop the running training job
  GET  /api/octo/coach                   the coach's latest note, one being asked, earlier ones (octo/coach.py)
  POST /api/octo/coach                   ask the coach (one Claude call, in the background)
  GET  /api/octo/coach/{id}              one note
  POST /api/octo/coach/{id}/feedback     {"part", "verdict"?, "novelty"?}: the owner's marks on it
  POST /api/octo/coach/{id}/focus        {"text"?, "measure"?}: practise what it says to work on
  POST /api/octo/coach/{id}/seen         the owner opened it
  GET  /api/octo/ask                     answers on the page (memory only) and kept ones (octo/ask.py, B24)
  POST /api/octo/ask                     {"question"}: pick its lines on the Mac, then one model call in the background
  GET  /api/octo/ask/{id}                one answer
  POST /api/octo/ask/{id}/keep           keep it (stored until deleted, or its conversation is)
  POST /api/octo/ask/{id}/delete         gone, kept or not
  GET  /api/octo/settings/calendar      calendar links (host and last four only) and this Mac's calendars (B23, Mac only)
  POST /api/octo/settings/calendar/links   {"url"}: fetched once to check, then kept in data/calendar.json
  POST /api/octo/settings/calendar/links/{id}/remove   the link and its events gone
  GET  /api/octo/settings/talk          Talk it through's effort (B31): low / medium / high, medium until changed
  POST /api/octo/settings/talk          {"effort"}
  POST /api/octo/settings/calendar/mac  {"on"}: the Octo app reads this Mac's calendars (EventKit) and posts them
  POST /api/octo/mac-app/calendar       the Octo app's post: {"access", "events"}
  GET  /api/octo/settings/iphone        Set up Octo's iPhone step: its parts checked in order, the install job (Mac only)
  POST /api/octo/settings/iphone/install   build, sign and install the iPhone app with this Mac's Xcode (octo/iphone.py)
  POST /api/octo/settings/iphone/check  open Octo on the iPhone: works once the phone trusts the developer
  GET  /api/octo/rehearsals              rehearsals, newest first, and who can be chosen (B26, octo/rehearse.py)
  POST /api/octo/rehearsals              {"person_id"? | "who"?, "hard", "want"}: a new one, no call yet
  GET  /api/octo/rehearsals/{id}         one: the exchange, what the model was told of them, its review
  POST /api/octo/rehearsals/{id}/say     {"text"}: the owner's line, then the other person's (one call; "" asks again)
  POST /api/octo/rehearsals/{id}/stop    the review (one call)
  POST /api/octo/rehearsals/{id}/delete  {"confirm": "delete"}
  GET  /api/octo/calendar/month         ?month=YYYY-MM: per day, conversations, minutes, reviews to answer, people (B37, octo/month.py)
  GET  /api/octo/calendar/day           ?date=YYYY-MM-DD: that day's conversations and calendar events as one timeline
  GET  /api/octo/people/{id}/prepare     preparing for the next conversation with them (B36, octo/prepare.py): the plan, what to close first
  GET  /api/octo/people/{id}/prepare/pick   ?names=…&skip=…: what a plan would read (their conversations, lines naming them elsewhere)
  POST /api/octo/people/{id}/prepare     {"about"?, "names", "skip"}: a plan, one call in the background (replaces the last)
  POST /api/octo/people/{id}/prepare/delete  the plan gone
  POST /api/octo/people/prepare          {"name", "invitee"?}: someone not in People yet, added by name (no voiceprint)
  GET  /api/octo/sessions/{id}/talk      talking it through (B31, octo/talk.py): the thread, a reply running, why not
  POST /api/octo/sessions/{id}/talk      {"text"}: the owner's message, then one call in the background ("" asks again)
  POST /api/octo/sessions/{id}/talk/clear  Start over
  GET  /api/octo/pipeline                every step, its model and why, and the choices (Mac only)
  GET  /api/octo/pipeline/{stage}/impact what switching that step's model would redo
  POST /api/octo/pipeline/{stage}        {"choice"}: switch the model; the step re-runs everywhere

Each route lives in its feature's module under octo/routes/ (the list, a conversation, speakers, people, home, coach,
ask, talk, rehearse, drills, fillers, calendar, device, settings, setup, pipeline), which make_router mounts on one
router; this list stays the one place every route is named.

IDs are checked against the database; no client path is ever used to open a file. Writes need a
JSON body and, if the browser sends an Origin, this server's own: another website open in the
same browser can't change anything here. After a change the pipeline runs in the background.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from importlib import import_module
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from . import DATA, ROOT
from . import device as D
from . import files as FL
from .db import connect, row
from .pipeline import next_due, retry
from .routes.common import Lab, no_store, same_origin  # noqa: F401  (same_origin: octo/phone_api.py)


# Workers start fresh for every stage, but this server keeps the code it started with: after a
# change it can show old behaviour (2 Oct: a fixed principle title still showed the old way). The
# header says so until it's restarted.
_STARTED = time.time()


def code_changed(since: float | None = None) -> bool:
    since = _STARTED if since is None else since
    return any(f.stat().st_mtime > since for d in ("octo", "bench") for f in (ROOT / d).glob("*.py"))


class Processor:
    """Runs `octo process` in the background whenever something changes. One at a time; a kick
    while it's running makes it go round once more. The pipeline's own lock keeps it from
    colliding with an `octo process` started in a terminal."""

    def __init__(self, db: Callable | None = None) -> None:
        self._wake = threading.Event()
        self.running = False
        self.crashed = False  # the last pass exited with an error (it's tried again soon)
        self.every: float | None = None  # also go round on a timer (set by `bench serve`)
        self.db = db
        threading.Thread(target=self._loop, daemon=True, name="octo-processor").start()

    def kick(self) -> None:
        self._wake.set()

    def keep_going(self, every_s: float = 3600) -> None:
        """Run now, and then at least hourly: work left over from before a restart gets done, and
        the audio deletion (D5) happens on time even when nothing new comes in."""
        self.every = every_s
        self.kick()

    AGAIN_AFTER_CRASH_S = 120.0

    def _wait(self) -> float | None:
        """Until the next pass: the hourly timer, or sooner when a failed step comes off hold (its
        15 or 30 min wait is up, or Claude's usage limit has reset). After a pass that crashed, two
        minutes: on 13 Oct the runner died on a locked database and its step waited an hour."""
        if self.crashed and self.every is not None:
            return self.AGAIN_AFTER_CRASH_S
        if self.every is None or self.db is None:
            return self.every
        try:
            due = next_due(self.db())
        except Exception:  # never let the timer stop the processor
            return self.every
        return self.every if due is None else max(30.0, min(self.every, due - time.time()))

    def _loop(self) -> None:
        while True:
            self._wake.wait(self._wait())
            self._wake.clear()
            self.running = True
            try:
                FL.trim_log(DATA / "process.log")
                for name in ("lab.log", "ollama.log", "relay.log"):  # the login items' (octo/login_item.py)
                    FL.trim_log(DATA / "logs" / name, held=True)
                with open(DATA / "process.log", "a") as log:
                    done = subprocess.run([sys.executable, "-m", "octo.cli", "process"], cwd=ROOT, stdout=log, stderr=log)
                self.crashed = done.returncode != 0
            except OSError:
                self.crashed = True
            finally:
                self.running = False


class Watchdog:
    """Every few minutes, whether the lab can still save to its folder (octo/watchdog.py). When it
    can't, one macOS notification and `problem` set for the header; when it can again, one more, and the
    processor goes round at once. Off until `bench serve` starts it."""

    def __init__(self, processor: "Processor", probe: Callable[[], str | None] | None = None,
                 post: Callable[[str], None] | None = None) -> None:
        from . import notify as N
        from . import watchdog as W
        self.processor = processor
        self.probe, self.post = probe or W.probe, post or N.post
        self.problem: str | None = None
        self.every: float | None = None
        self._wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="octo-watchdog").start()

    def keep_going(self, every_s: float = 300) -> None:
        self.every = every_s
        self._wake.set()

    def check(self) -> None:
        from . import watchdog as W
        was, self.problem = self.problem, self.probe()
        if self.problem and self.problem != was:
            self.post(W.MESSAGE[self.problem])
        elif was and not self.problem:
            self.post(W.RECOVERED)
            self.processor.kick()

    def _loop(self) -> None:
        while True:
            self._wake.wait(self.every)
            self._wake.clear()
            if self.every is None:
                continue
            try:
                self.check()
            except Exception:  # a failed check never stops the next one
                pass


class DeviceSync:
    """Pulls from the recorder every few minutes while it's on the home Wi-Fi, by running
    `octo device sync` in a subprocess (the speech detector loads torch, which the server shouldn't
    keep), and kicks the processor when new recordings arrive. Does nothing until a recorder is set
    up (`octo device setup`)."""

    def __init__(self, processor: "Processor", db: Callable) -> None:
        self.processor, self.db = processor, db
        self._wake = threading.Event()
        self.running = False
        self._checking = False
        self.every: float | None = None
        threading.Thread(target=self._loop, daemon=True, name="octo-device-sync").start()

    def kick(self) -> None:
        self._wake.set()

    def keep_going(self, every_s: float = 300, check_s: float = 30) -> None:
        self.every = every_s
        self.kick()
        if not self._checking:  # and a quick status check in between (D.check)
            self._checking = True
            threading.Thread(target=self._check_loop, args=(check_s,), daemon=True, name="octo-device-check").start()

    def _check_loop(self, every_s: float) -> None:
        while True:
            time.sleep(every_s)
            if self.running or not D.load_config():
                continue
            try:
                D.check(self.db())
            except Exception:  # a failed check never stops the next one; the sync reports real trouble
                pass

    def _count(self) -> int:
        return row(self.db(), "SELECT count(*) n FROM recordings WHERE source='device'")["n"]

    def _loop(self) -> None:
        while True:
            self._wake.wait(self.every)
            self._wake.clear()
            if not D.load_config():
                continue
            from .privacy import STOP_GB, free_gb
            if free_gb() < STOP_GB:  # the card keeps the audio until there's room on the Mac
                with open(DATA / "device.log", "a") as log:
                    log.write(time.strftime(f"\n%Y-%m-%d %H:%M:%S\nnot pulling: under {STOP_GB} GB free on the Mac\n"))
                continue
            self.running = True
            try:
                before = self._count()
                FL.trim_log(DATA / "device.log")
                with open(DATA / "device.log", "a") as log:
                    log.write(time.strftime("\n%Y-%m-%d %H:%M:%S\n"))
                    log.flush()
                    subprocess.run([sys.executable, "-m", "octo.cli", "device", "sync"], cwd=ROOT, stdout=log, stderr=log,
                                   timeout=3 * 3600)
                if self._count() > before:
                    self.processor.kick()
            except subprocess.TimeoutExpired:
                pass
            finally:
                self.running = False


class Notifier:
    """Every few minutes, a macOS notification for whatever newly needs the owner (octo/notify.py).
    Off until `bench serve` starts it, so tests and the CLI never post one."""

    def __init__(self, db: Callable) -> None:
        self.db = db
        self.every: float | None = None
        self._wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="octo-notify").start()

    def keep_going(self, every_s: float = 300) -> None:
        self.every = every_s
        self._wake.set()

    def _loop(self) -> None:
        from . import events as EV
        from . import notify as N
        last = 0.0
        while True:
            self._wake.wait(None if self.every is None else min(self.every, 60))  # the calendar's banners need the minute
            self._wake.clear()
            if self.every is None:
                continue
            try:
                EV.tick(self.db())  # B23: calendar links when due, banners 10 min before
            except Exception:
                pass
            if time.time() - last < self.every:
                continue
            last = time.time()
            try:
                N.check(self.db())
            except Exception:  # a failed check never stops the next one
                pass
            try:  # the iPhone app's weekly re-signing (octo/iphone.py), by itself while the phone is around
                from . import iphone as IP
                IP.renew(IP.JOB)
            except Exception:
                pass


def ask_coach(note_id: str) -> None:
    """Run the coach's call for a note in a subprocess, off the request (it takes minutes): the
    server keeps the code it started with, the subprocess has the current code, as for `octo process`."""
    def go() -> None:
        with open(DATA / "coach.log", "a") as log:
            log.write(time.strftime(f"\n%Y-%m-%d %H:%M:%S {note_id}\n"))
            log.flush()
            subprocess.run([sys.executable, "-m", "octo.cli", "coach", "--run", note_id], cwd=ROOT, stdout=log, stderr=log)
    threading.Thread(target=go, daemon=True, name="octo-coach").start()


MAX_UPLOAD = 1 << 30  # 1 GB: about 16 h of voice memo


async def _receive(request: Request, ext: str) -> Path:
    """An upload's body into data/uploads/, whole, or an error (and nothing left behind)."""
    uploads = DATA / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    tmp = uploads / f"{time.time_ns()}{ext}"
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "over 1 GB")
                f.write(chunk)
        if size == 0:
            raise HTTPException(422, "empty file")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def make_router(db: Callable = connect, processor: Processor | None = None,
                coach: Callable[[str], None] = ask_coach, asker: Callable[[str], None] | None = None,
                talker: Callable[[str], None] | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/octo", dependencies=[Depends(no_store)])
    processor = processor or Processor(db)
    router.processor = processor  # bench serve starts it going
    syncer = DeviceSync(processor, db)
    router.device_sync = syncer  # likewise
    router.notifier = Notifier(db)  # likewise
    router.watchdog = watchdog = Watchdog(processor)  # likewise
    from . import filler_loop as FLP
    router.filler_trainer = FLP.Trainer()  # likewise: the filler cue's weekly retrain (B22)
    from . import models as MD

    def models_done() -> None:  # every model in: what failed for want of one runs now
        retry(db())
        processor.kick()
    router.models = fetcher = MD.Fetcher(db, done=models_done)  # bench serve starts it when something's missing
    lab = Lab(db=db, processor=processor, syncer=syncer, watchdog=watchdog, fetcher=fetcher, coach=coach,
              asker=asker, talker=talker)  # what the route modules (octo/routes/) share
    from .routes import ROUTES
    for name in ROUTES:
        import_module(f".routes.{name}", __package__).add(router, lab)
    return router
