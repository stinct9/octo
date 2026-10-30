"""Runs steps against the board, one job at a time, emitting events.

Concurrency model:
- One run at a time per process (job lock) and per machine (flock on runs/.lock), because
  there's one board and one serial port.
- The run executes on a worker thread; everything blocking (serial, arduino-cli, Whisper)
  happens there. Events are published from that thread and from timer threads, so listeners
  must be thread-safe (the API hands them to asyncio with call_soon_threadsafe).
- Cancel is cooperative. A request moves the run to "cancelling"; steps call ctx.check() at
  safe points, which raises Cancelled -- except inside ctx.protected() sections (SD writes,
  uploads, the recording dump), which always run to completion. The job lock is released only
  when the worker thread has actually finished, never on the cancel request itself.
"""

from __future__ import annotations

import contextlib
import fcntl
import platform
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Iterator, Literal

from . import AUDIO, ROOT, RUNS
from .audio import ModelCache
from .device import Board, BoardError, Cancelled, connect, find_port, open_port
from .events import Event
from .firmware import compile_sketch, upload
from .store import RunStore

Status = Literal["pass", "warn", "fail", "skipped", "cancelled"]
Verdict = Literal["pass", "warn", "fail"] | None
Listener = Callable[[Event], None]


class Busy(RuntimeError):
    pass


def _try_board_lock():
    """The machine-wide lock: held by whichever process is driving the board. None if taken."""
    RUNS.mkdir(parents=True, exist_ok=True)
    f = open(RUNS / ".lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except BlockingIOError:
        f.close()
        return None


def recorder_audio_left() -> int:
    """Chunk files (a minute each) on the card, if the board is running the recorder; else 0.
    Opening the port leaves the recorder running (since 8 Oct; it used to restart it)."""
    if not find_port():
        return 0
    try:
        with Board(open_port(wait=3)) as board:
            board.drain(quiet=0.3, limit=3)
            if board.identify() != "recorder":
                return 0
            return max(0, int(board.run(b"s", timeout=10).get("chunk_files", 0)))
    except BoardError:
        return 0


def recover_if_idle(store: RunStore) -> list[str]:
    """Mark crashed runs aborted -- but only when no process holds the board lock. A run
    without a finish marker may simply be live in another process (bench serve vs bench run)."""
    lock = _try_board_lock()
    if not lock:
        return []
    try:
        return store.recover_aborted()
    finally:
        lock.close()


@dataclass
class Metric:
    key: str
    label: str
    value: object
    unit: str | None = None
    verdict: Verdict = None


@dataclass
class Outcome:
    status: Status
    summary: str
    hint: str | None = None
    metrics: list[Metric] = field(default_factory=list)


class Step:
    """One bench test. Subclasses set id/title/sketch and implement run()."""

    id: str = ""
    title: str = ""
    sketch: str | None = None
    needs_user: bool = False

    def skip_reason(self) -> str | None:
        """Why this step can't run right now (e.g. missing secrets.h), or None."""
        return None

    def run(self, ctx: StepContext) -> Outcome:
        raise NotImplementedError


class StepContext:
    """What a step can do: talk to the board, report progress, and honour cancel."""

    def __init__(self, runner: Runner, step: Step, previous: dict[str, object]):
        self._runner = runner
        self.step = step
        self.previous = previous
        self.options = runner.options
        self.state = runner.shared  # carried between steps in one run (e.g. the recording path)
        self.models = runner.models
        self.audio_dir = AUDIO
        self.run_id = runner.run_id or ""

    # --- reporting ---------------------------------------------------------------------
    def emit(self, type_: str, **data) -> None:
        self._runner.emit(type_, step=self.step.id, **data)

    def log(self, line: str, stream: str = "device") -> None:
        self.emit("step.log", line=line, stream=stream)

    def progress(self, phase: str, pct: float | None = None, detail: str | None = None) -> None:
        self.emit("step.progress", phase=phase, pct=pct, detail=detail)

    # --- cancel ------------------------------------------------------------------------
    def check(self) -> None:
        self._runner.check()

    @contextlib.contextmanager
    def protected(self, what: str) -> Iterator[None]:
        """Cancel waits until this block finishes (SD writes, uploads, recording dumps)."""
        with self._runner.protect(self.step.id, what):
            yield

    # --- human gate --------------------------------------------------------------------
    def wait_for_user(self, message: str, while_waiting: Callable[[], None] = lambda: time.sleep(0.1)) -> None:
        self._runner.wait_for_user(self.step.id, message, while_waiting)

    # --- board -------------------------------------------------------------------------
    def flash(self) -> None:
        """Upload this step's pre-compiled sketch."""
        sketch = self.step.sketch
        assert sketch
        err = self._runner.compile_errors.get(sketch)
        if err:
            raise err
        last = [-1.0]

        def on_progress(pct: float) -> None:
            if pct - last[0] >= 5 or pct >= 100:  # don't flood the stream
                last[0] = pct
                self.progress("upload", pct)

        self.progress("upload", 0)
        with self.protected("uploading firmware"):
            upload(sketch, lambda l: self.log(l, "tool"), on_progress)
        self.progress("upload", 100)

    def connect(self) -> Board:
        assert self.step.sketch
        self.progress("connect")
        board = connect(self.step.sketch)
        self._runner.open_boards.append(board)
        return board


class Runner:
    def __init__(self, store: RunStore | None = None, models: ModelCache | None = None):
        self.store = store or RunStore()
        self.models = models or ModelCache()
        self.listeners: list[Listener] = []
        self._job = threading.Lock()
        self._emit_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._ready = threading.Event()
        # Our own completion flag. Thread.is_alive()/join() aren't safe here: on Python 3.12 a
        # Ctrl-C landing inside join() can mark a still-running thread as stopped (the lock
        # clean-up in _wait_for_tstate_lock), which made the CLI exit and kill a live run.
        self._done = threading.Event()
        self._done.set()
        self._protected = 0
        self._protect_lock = threading.Lock()
        self.run_id: str | None = None
        self.state = "idle"
        self._seq = 0
        self.options: dict = {}
        self.shared: dict = {}
        self.compile_errors: dict[str, BoardError] = {}
        self.open_boards: list[Board] = []

    # --- events ------------------------------------------------------------------------
    def emit(self, type_: str, step: str | None = None, **data) -> Event:
        with self._emit_lock:
            self._seq += 1
            event = Event(seq=self._seq, run_id=self.run_id or "", type=type_, step=step, data=data)
            self.store.append(event)
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # a broken listener must not kill the run
                traceback.print_exc()
        return event

    def _set_state(self, state: str) -> None:
        self.state = state
        self.emit("run.state", state=state)

    # --- control (called from other threads) -------------------------------------------
    @property
    def running(self) -> bool:
        return not self._done.is_set()

    def start(self, steps: list[Step], options: dict | None = None) -> str:
        if not self._job.acquire(blocking=False):
            raise Busy("a run is already in progress")
        try:
            self.run_id = self.store.new_run_id()
            self._seq = 0
            self.options = options or {}
            self.shared = {}
            self.compile_errors = {}
            self._cancel.clear()
            self._ready.clear()
            self._done.clear()
            self._thread = threading.Thread(target=self._work, args=(steps,), daemon=True,
                                            name=f"run-{self.run_id}")
            self._thread.start()
            return self.run_id
        except BaseException:
            self._done.set()
            self._job.release()
            raise

    def cancel(self) -> bool:
        if not self.running or self._cancel.is_set():
            return False
        self._cancel.set()
        self._ready.set()  # unblock a pending human gate
        self._set_state("cancelling")
        return True

    def ready(self) -> bool:
        if self.state != "waiting_for_user":
            return False
        self._ready.set()
        return True

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the run to finish; True if it has. Safe to interrupt with Ctrl-C."""
        return self._done.wait(timeout)

    # --- used by steps -----------------------------------------------------------------
    def check(self) -> None:
        with self._protect_lock:
            if self._cancel.is_set() and self._protected == 0:
                raise Cancelled()

    @contextlib.contextmanager
    def protect(self, step: str, what: str) -> Iterator[None]:
        with self._protect_lock:
            self._protected += 1
        try:
            yield
        finally:
            with self._protect_lock:
                self._protected -= 1
            if self._cancel.is_set():
                self.emit("step.log", step=step, line=f"finished {what}; stopping now", stream="tool")

    def wait_for_user(self, step: str, message: str, while_waiting: Callable[[], None]) -> None:
        self._ready.clear()
        self._set_state("waiting_for_user")
        self.emit("step.prompt", step=step, phase="ready", message=message)
        try:
            while not self._ready.is_set():
                while_waiting()
                self.check()
            self.check()  # ready may have been set by cancel
        finally:
            if self.state == "waiting_for_user":
                self._set_state("running")

    # --- the run -----------------------------------------------------------------------
    def _env(self) -> dict:
        def git(*args: str) -> str | None:
            try:
                return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                                      timeout=5).stdout.strip() or None
            except Exception:
                return None
        return {"git_sha": git("rev-parse", "--short", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
                "python": platform.python_version(), "options": self.options}

    def _work(self, steps: list[Step]) -> None:
        lock_file = None
        results: list[Status] = []
        try:
            previous = self.store.previous_metrics(before=self.run_id)
            self.emit("run.started", steps=[{"id": s.id, "title": s.title, "needs_user": s.needs_user}
                                            for s in steps], env=self._env())
            self._set_state("running")

            # Cross-process guard: the CLI and the server must never drive the board at once.
            lock_file = _try_board_lock()
            if not lock_file:
                self.emit("run.finished", status="fail",
                          reason="another bench process is using the board (bench serve or bench run)")
                return
            # Holding the lock, any other unfinished run really is dead.
            self.store.recover_aborted(exclude=self.run_id)

            # Bench guard (plan §3): bench sketches replace the recorder, and 01b_sd_wipe erases the
            # card. Refuse while the recorder has audio the Mac hasn't pulled yet.
            if any(s.sketch for s in steps) and (left := recorder_audio_left()):
                self.emit("run.finished", status="fail",
                          reason=f"the recorder still has {left} min of audio on its card: pull it first "
                                 "(octo device sync, on home Wi-Fi), then run the bench; reflash it after "
                                 "with octo device flash")
                return

            if "transcribe" in {s.id for s in steps}:
                self.models.warm(self.options.get("model", "large-v3"))

            self._compile_all(steps)

            for step in steps:
                if self._cancel.is_set():
                    self._finish_step(step, Outcome("cancelled", "not started"), 0.0)
                    results.append("cancelled")
                    continue
                results.append(self._run_step(step, previous.get(step.id, {})))

            status = ("cancelled" if self._cancel.is_set() else
                      "fail" if "fail" in results else
                      "warn" if "warn" in results else "pass")
            self.emit("run.finished", status=status)
        except Exception as e:
            traceback.print_exc()
            self.emit("run.finished", status="fail", reason=f"runner error: {e}")
        finally:
            for b in self.open_boards:
                b.close()
            self.open_boards = []
            if lock_file:
                lock_file.close()  # releases the flock
            self.state = "idle"
            self._job.release()
            self._done.set()

    def _compile_all(self, steps: list[Step]) -> None:
        """Compile every needed sketch before touching the board: no board needed, and a
        missing secrets.h fails in seconds instead of minutes into the run."""
        for step in steps:
            if not step.sketch or step.skip_reason() or step.sketch in self.compile_errors:
                continue
            self.emit("step.progress", step=step.id, phase="compile")
            try:
                compile_sketch(step.sketch, lambda l, s=step.id: self.emit("step.log", step=s, line=l, stream="tool"),
                               check=self.check)
            except BoardError as e:
                self.compile_errors[step.sketch] = e
            except Cancelled:
                return

    def _run_step(self, step: Step, previous: dict) -> Status:
        t0 = time.time()
        self.emit("step.started", step=step.id, title=step.title)
        reason = step.skip_reason()
        if reason:
            outcome = Outcome("skipped", reason)
        else:
            ctx = StepContext(self, step, previous)
            try:
                outcome = step.run(ctx)
            except Cancelled:
                outcome = Outcome("cancelled", "cancelled")
            except BoardError as e:
                outcome = Outcome("fail", str(e), hint=e.hint)
            except Exception as e:
                traceback.print_exc()
                outcome = Outcome("fail", f"unexpected error: {e}")
            finally:
                for b in self.open_boards:
                    b.close()
                self.open_boards = []
        for m in outcome.metrics:
            self.emit("step.metric", step=step.id, key=m.key, label=m.label, value=m.value, unit=m.unit,
                      verdict=m.verdict, previous=previous.get(m.key))
        self._finish_step(step, outcome, time.time() - t0)
        return outcome.status

    def _finish_step(self, step: Step, outcome: Outcome, duration: float) -> None:
        self.emit("step.finished", step=step.id, status=outcome.status, summary=outcome.summary,
                  hint=outcome.hint, duration_s=round(duration, 1),
                  metrics={m.key: m.value for m in outcome.metrics})
