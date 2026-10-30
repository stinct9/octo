"""The lab notices when it can't save to its folder (octo/watchdog.py, 13 Oct)."""

import sys

from octo import watchdog as W
from octo.api import Processor, Watchdog


def test_probe_saves_and_cleans_up(tmp_path):
    assert W.probe(tmp_path, timeout_s=30) is None
    assert list(tmp_path.iterdir()) == []


def test_probe_says_stuck_when_it_doesnt_finish(tmp_path):
    hang = tmp_path / "python"
    hang.write_text(f"#!/bin/sh\nexec {sys.executable} -c 'import time; time.sleep(30)'\n")
    hang.chmod(0o755)
    assert W.probe(tmp_path, timeout_s=0.5, python=str(hang)) == W.STUCK


def test_probe_says_denied_when_macos_refuses(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        assert W.probe(ro, timeout_s=30) == W.DENIED
    finally:
        ro.chmod(0o700)


class _Kicks:
    def __init__(self):
        self.n = 0

    def kick(self):
        self.n += 1


def test_one_notification_per_episode_and_a_kick_when_it_recovers():
    answers = iter([None, W.STUCK, W.STUCK, None, None])
    posted, proc = [], _Kicks()
    wd = Watchdog(proc, probe=lambda: next(answers), post=posted.append)
    for _ in range(5):
        wd.check()
    assert posted == [W.MESSAGE[W.STUCK], W.RECOVERED]
    assert proc.n == 1 and wd.problem is None


def test_a_crashed_pass_is_tried_again_in_minutes(monkeypatch):
    monkeypatch.setattr(Processor, "_loop", lambda self: None)
    p = Processor()
    p.every = 3600
    assert p._wait() == 3600
    p.crashed = True
    assert p._wait() == Processor.AGAIN_AFTER_CRASH_S


def test_status_answers_without_the_database_when_it_cant_save():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router

    def no_db():
        raise AssertionError("the database was asked")

    router = make_router(db=no_db, processor=_Kicks())
    router.watchdog.problem = W.STUCK
    app = FastAPI()
    app.include_router(router)
    st = TestClient(app).get("/api/octo/status").json()
    assert st["cant_save"] == W.STUCK and st["processing"] is False
