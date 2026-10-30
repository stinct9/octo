"""Runner behaviour with fake steps: statuses, cancel semantics, human gate, locking, persistence."""

import threading
import time

import pytest

import bench.runner as runner_mod
from bench.runner import Busy, Metric, Outcome, Runner, Step
from bench.store import RunStore


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "RUNS", tmp_path)  # flock file lives here
    r = Runner(store=RunStore(tmp_path))
    r.events = []
    r.listeners.append(r.events.append)
    return r


def finish(r, timeout=5):
    r.join(timeout)
    assert not r.running, "run didn't finish"
    return r.events


def types(events):
    return [e.type for e in events]


def step_status(events, sid):
    return next(e.data["status"] for e in events if e.type == "step.finished" and e.step == sid)


class Fake(Step):
    def __init__(self, sid, fn):
        self.id, self.title, self.fn = sid, sid, fn

    def run(self, ctx):
        return self.fn(ctx)


def ok(ctx):
    return Outcome("pass", "fine", metrics=[Metric("speed", "Speed", 42, "KB/s", "pass")])


def test_statuses_and_event_order(runner):
    runner.start([Fake("a", ok), Fake("b", lambda ctx: Outcome("warn", "meh"))])
    ev = finish(runner)
    assert types(ev)[0] == "run.started"
    assert ev[-1].type == "run.finished" and ev[-1].data["status"] == "warn"
    assert [e.seq for e in ev] == list(range(1, len(ev) + 1))  # gapless, ordered


def test_exception_in_step_fails_step_not_run(runner):
    def boom(ctx):
        raise ValueError("kaboom")
    runner.start([Fake("a", boom), Fake("b", ok)])
    ev = finish(runner)
    assert step_status(ev, "a") == "fail" and step_status(ev, "b") == "pass"
    assert ev[-1].data["status"] == "fail"


def test_cancel_stops_at_next_check(runner):
    started = threading.Event()

    def slow(ctx):
        started.set()
        while True:
            ctx.check()
            time.sleep(0.01)
    runner.start([Fake("a", slow), Fake("b", ok)])
    started.wait(2)
    assert runner.cancel()
    ev = finish(runner)
    assert step_status(ev, "a") == "cancelled"
    assert step_status(ev, "b") == "cancelled"  # never started
    assert ev[-1].data["status"] == "cancelled"
    assert any(e.type == "run.state" and e.data["state"] == "cancelling" for e in ev)


def test_protected_section_completes_before_cancel(runner):
    inside = threading.Event()
    completed = []

    def sd_write(ctx):
        with ctx.protected("the SD write"):
            inside.set()
            for _ in range(20):
                ctx.check()  # must NOT raise while protected
                time.sleep(0.01)
            completed.append(True)
        ctx.check()  # raises here, after the safe point
        return Outcome("pass", "unreachable")

    runner.start([Fake("sd", sd_write)])
    inside.wait(2)
    runner.cancel()
    ev = finish(runner)
    assert completed == [True]
    assert step_status(ev, "sd") == "cancelled"


def test_human_gate_waits_for_ready(runner):
    def mic(ctx):
        ctx.wait_for_user("press ready")
        return Outcome("pass", "recorded")
    runner.start([Fake("mic", mic)])
    deadline = time.time() + 2
    while runner.state != "waiting_for_user" and time.time() < deadline:
        time.sleep(0.01)
    assert runner.state == "waiting_for_user"
    time.sleep(0.1)
    assert runner.running  # still waiting
    assert runner.ready()
    ev = finish(runner)
    assert step_status(ev, "mic") == "pass"
    assert any(e.type == "step.prompt" and e.data["phase"] == "ready" for e in ev)


def test_cancel_during_human_gate(runner):
    runner.start([Fake("mic", lambda ctx: (ctx.wait_for_user("go"), Outcome("pass", "x"))[1])])
    while runner.state != "waiting_for_user":
        time.sleep(0.01)
    runner.cancel()
    ev = finish(runner)
    assert step_status(ev, "mic") == "cancelled"


def test_second_start_is_busy_and_lock_held_until_thread_exits(runner):
    release = threading.Event()

    def hold(ctx):
        release.wait(2)
        return Outcome("pass", "done")
    runner.start([Fake("a", hold)])
    with pytest.raises(Busy):
        runner.start([Fake("b", ok)])
    runner.cancel()  # cancel requested, but the step ignores it until released
    with pytest.raises(Busy):
        runner.start([Fake("b", ok)])  # lock still held: the worker hasn't exited
    release.set()
    finish(runner)
    runner.start([Fake("b", ok)])  # now free
    finish(runner)


def test_cross_process_lock(runner, tmp_path):
    import fcntl
    with open(tmp_path / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # "another bench process"
        runner.start([Fake("a", ok)])
        ev = finish(runner)
    assert ev[-1].type == "run.finished" and ev[-1].data["status"] == "fail"
    assert "another bench process" in ev[-1].data["reason"]


def test_events_persisted_as_emitted_and_level_events_are_not(runner, tmp_path):
    def mic(ctx):
        ctx.emit("step.level", dbfs=-50.0)
        ctx.log("hello")
        return Outcome("pass", "ok")
    runner.start([Fake("mic", mic)])
    finish(runner)
    stored = runner.store.read(runner.run_id)
    assert [e["type"] for e in stored] == [e.type for e in runner.events if e.type != "step.level"]


def test_previous_metrics_and_deltas(runner):
    runner.start([Fake("a", ok)])
    finish(runner)
    time.sleep(1.1)  # run IDs are per-second
    runner.events.clear()
    runner.start([Fake("a", ok)])
    ev = finish(runner)
    metric = next(e for e in ev if e.type == "step.metric")
    assert metric.data["previous"] == 42


def test_recover_aborted_run(tmp_path):
    store = RunStore(tmp_path)
    from bench.events import Event
    store.append(Event(seq=1, run_id="20260101-000000", type="run.started", data={"steps": []}))
    assert store.recover_aborted() == ["20260101-000000"]
    assert store.summary("20260101-000000")["status"] == "aborted"
    assert store.recover_aborted() == []  # idempotent


def test_recovery_leaves_runs_live_in_another_process_alone(tmp_path, monkeypatch):
    """Regression: bench run used to mark bench serve's in-progress run as aborted."""
    import fcntl
    from bench.events import Event
    from bench.runner import recover_if_idle
    monkeypatch.setattr(runner_mod, "RUNS", tmp_path)
    store = RunStore(tmp_path)
    store.append(Event(seq=1, run_id="20260101-000000", type="run.started", data={"steps": []}))
    with open(tmp_path / ".lock", "w") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # another process is mid-run
        assert recover_if_idle(store) == []
        assert store.summary("20260101-000000")["status"] == "running"
    assert recover_if_idle(store) == ["20260101-000000"]  # lock free: it really is dead


def test_own_run_is_not_recovered(runner):
    runner.start([Fake("a", ok)])
    ev = finish(runner)
    assert [e.data["status"] for e in ev if e.type == "run.finished"] == ["pass"]


def test_bench_refuses_to_replace_a_recorder_with_audio_on_its_card(runner, monkeypatch):
    monkeypatch.setattr(runner_mod, "recorder_audio_left", lambda: 12)
    compiled = []
    monkeypatch.setattr(runner_mod.Runner, "_compile_all", lambda self, steps: compiled.append(steps))
    s = Fake("sd", ok)
    s.sketch = "01_sd"
    runner.start([s])
    ev = finish(runner)
    assert ev[-1].data["status"] == "fail" and "12 min of audio" in ev[-1].data["reason"]
    assert not compiled  # nothing flashed

    monkeypatch.setattr(runner_mod, "recorder_audio_left", lambda: 0)
    runner.events.clear()
    runner.start([Fake("a", ok)])  # steps without a board never ask
    assert finish(runner)[-1].data["status"] == "pass"


def test_a_worker_holds_the_runners_lock_and_has_a_time_limit(tmp_path, monkeypatch):  # review 4 Oct, M39
    import fcntl
    import subprocess

    from octo import pipeline as P
    from octo.db import connect
    conn = connect(tmp_path / "w.db")
    seen = {}

    def fake_run(args, **kw):
        seen.update(kw)
        with open(P._lock_path(conn), "w") as other:  # a second runner, while this worker lives
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen["second_runner_got_it"] = True
            except BlockingIOError:
                seen["second_runner_got_it"] = False
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(P.subprocess, "run", fake_run)
    monkeypatch.setattr(P, "_drain", lambda conn, log, execute, counts: execute("r") and counts)  # one step
    P.run_pending(conn, log=lambda m: None)
    assert P._LOCK_FD is None  # handed over only while the runner runs
    assert seen["pass_fds"] and seen["timeout"] == P.WORKER_TIMEOUT_S and seen["second_runner_got_it"] is False

    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, kw["timeout"])
    monkeypatch.setattr(P.subprocess, "run", slow)
    assert P._in_worker("x") == (1, "the step took over 3 h and was stopped")
