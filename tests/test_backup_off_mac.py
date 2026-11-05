"""Tech review 5 Nov, risk 9: data/backups/ is on the same disk. `octo doctor` and Setup say plainly when nothing copies
the lab off this Mac, a monthly banner says export it (counts only, never names), and Setup says to keep the recorder's
key in a password manager without showing it."""

import json
import subprocess
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import backup as B
from octo import device as D
from octo import doctor as DR
from octo import notify as N
from octo.api import make_router
from octo.db import connect

DAY = 86400
NOW = datetime(2026, 11, 20, 10).timestamp()
NO_DISK = lambda: "Time Machine has no backup disk"  # noqa: E731
REAL_TM = B.time_machine  # read at import, before conftest stands in for it


def fake_tmutil(dest: str, excluded: str = "[Included]  /x"):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, dest if cmd[1] == "destinationinfo" else excluded, "")
    return run


def test_time_machine_is_read_from_tmutil(tmp_path):
    tm = REAL_TM
    disk = "====================================================\nName          : Backups\nKind          : Local\n"
    assert tm(tmp_path, run=fake_tmutil("tmutil: No destinations configured.\n")) == "Time Machine has no backup disk"
    assert tm(tmp_path, run=fake_tmutil(disk, "[Excluded]  /x")) == "Time Machine leaves the lab's folder out"
    assert tm(tmp_path, run=fake_tmutil(disk)) is None


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    c.execute("INSERT INTO recordings (id, path, source, duration_s, created_at) VALUES ('rec_a', 'a.flac', 'device', 60, ?)",
              (NOW - 40 * DAY,))
    return c


def test_off_this_mac_is_time_machine_or_an_export_this_month(conn):
    assert B.off_mac(conn, NOW, tm=lambda: None) is None
    assert B.off_mac(conn, NOW, tm=NO_DISK) == "Time Machine has no backup disk, and the lab was never exported"
    conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('last_export_at', ?, 0)", (json.dumps(NOW - 3 * DAY),))
    assert B.off_mac(conn, NOW, tm=NO_DISK) is None
    assert "over 30 days old" in B.off_mac(conn, NOW + 30 * DAY, tm=NO_DISK)


def test_doctor_and_setup_say_when_nothing_copies_the_lab_off_this_mac(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(B, "time_machine", NO_DISK)
    check = DR.off_mac(conn)
    assert check.mark == DR.WARN and "Time Machine" in check.fix and "octo export" in check.fix
    assert check in DR.home(conn, agents=tmp_path)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    mac = next(s for s in client.get("/api/octo/settings/setup").json()["steps"] if s["id"] == "mac")
    assert [c["mark"] for c in mac["why"] if c["what"] == "A copy off this Mac"] == [DR.WARN]  # said, never blocking
    monkeypatch.setattr(B, "time_machine", lambda: None)
    assert DR.off_mac(conn).mark == DR.OK
    mac = next(s for s in client.get("/api/octo/settings/setup").json()["steps"] if s["id"] == "mac")
    assert not any(c["what"] == "A copy off this Mac" for c in mac["why"])


def test_setup_says_where_the_recorder_key_is_and_never_shows_it(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    monkeypatch.setattr(D, "load_config", lambda: None)
    assert client.get("/api/octo/settings/setup").json()["recorder_key"] is None  # no recorder: nothing to say
    secret = "ab" * 32
    monkeypatch.setattr(D, "load_config", lambda: {"key": "k", "enc_private": secret})
    r = client.get("/api/octo/settings/setup")
    assert r.json()["recorder_key"] == str(D.CONFIG) and secret not in r.text


def test_a_monthly_banner_says_export_your_lab_with_no_names(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(B, "time_machine", NO_DISK)
    assert B.export_due(conn, NOW) == "export:2026-11"
    assert "export:2026-11" in N.waiting(conn, NOW)
    text = N.message({"export:2026-11"}, NOW)
    assert text.startswith("Export your lab") and "rec_a" not in text
    sent = []
    monkeypatch.setattr(N, "STATE", tmp_path / "notify.json")
    monkeypatch.setattr(N, "waiting", lambda c, now=None: {"export:2026-11"})
    assert N.check(conn, send=sent.append, now=NOW) and N.check(conn, send=sent.append, now=NOW) is None
    assert len(sent) == 1  # once a month, not every 5 minutes
    # Next month it's a new key, so it's said again; an export takes it away; a new lab isn't nagged in its first month.
    assert B.export_due(conn, datetime(2026, 12, 2, 10).timestamp()) == "export:2026-12"
    conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('last_export_at', ?, 0)", (json.dumps(NOW),))
    assert B.export_due(conn, NOW + DAY) is None
    conn.execute("DELETE FROM app_config WHERE key='last_export_at'")
    conn.execute("UPDATE recordings SET created_at=?", (NOW - 5 * DAY,))
    assert B.export_due(conn, NOW) is None
    monkeypatch.setattr(B, "time_machine", lambda: None)
    conn.execute("UPDATE recordings SET created_at=?", (NOW - 40 * DAY,))
    assert B.export_due(conn, NOW) is None  # Time Machine copies it: nothing to say
