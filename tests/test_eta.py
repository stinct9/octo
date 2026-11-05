"""When it'll be ready (B3, octo/eta.py): the fit, and the queue walked as the runner would."""

import json

import pytest

from octo import eta as E
from octo import pipeline as P
from octo import runs as RN  # eta reads the hold there (risk 8, 5 Nov)
from octo import planning as PL
from octo.db import connect

NOW = 1_800_000_000.0


def test_the_fit():
    line = [(m, 20 + 8 * m) for m in (1, 3, 5, 10, 20)]
    assert E._fit(line) == pytest.approx((20, 8))
    assert E._fit(line[:4]) is None  # too few of its own: the default stays
    merge = [(m, 55 * m - 10 + (m % 2)) for m in (1, 2, 4, 6, 9)]  # a negative intercept: all per minute
    assert E._fit(merge)[0] == 0 and E._fit(merge)[1] == pytest.approx(53.6, abs=1)
    flat = [(1, 50), (2, 48), (5, 45), (8, 44), (12, 40)]  # falling: noise, not a rate
    assert E._fit(flat) == (pytest.approx(45.4), 0.0)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = connect(tmp_path / "t.db")
    monkeypatch.setattr(P, "claude_blocked_until", lambda conn, now=None: None)
    monkeypatch.setattr(RN, "claude_blocked_until", lambda conn, now=None: None)
    monkeypatch.setattr(PL, "insight_ready", lambda conn, sid: False)  # speakers not confirmed yet
    return c


def _session(c, sid, minutes, started):
    rec = "rec_" + sid
    c.execute("INSERT INTO recordings (id, source, path, started_at, duration_s, created_at) VALUES (?, 'device', 'x.flac', ?, ?, 0)",
              (rec, started, minutes * 60))
    c.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, ?, 0, ?, 1, 0)",
              (sid, rec, minutes * 60))


def _run(c, sid, stage, status="queued", started=None, params=None):
    c.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, queued_at, started_at) "
              "VALUES (?, ?, ?, 'h', ?, 0, '[]', ?, ?, ?)", (f"run_{sid}_{stage}", stage, sid, status, json.dumps(params or {}), NOW - 100, started))


def _left(stages, minutes):
    return sum(a + b * minutes for a, b in (E.DEFAULT[s] for s in stages))


def test_a_conversation_being_transcribed_has_its_later_steps_to_come(conn):
    _session(conn, "ses_a", 10, NOW - 3600)
    _run(conn, "ses_a", "transcribe", "running", started=NOW - 30)
    _run(conn, "ses_a", "speakers")
    f = E.forecast(conn, NOW)
    a = f["sessions"]["ses_a"]
    # transcribe's remainder, then speakers, diarize, identify, numbers; the review waits for the owner
    assert a["at"] == pytest.approx(NOW + _left(["transcribe", "speakers", "diarize", "identify", "numbers"], 10) - 30)
    assert a["then"] == "you" and a["late"] is False
    assert f["queue_empty_at"] == pytest.approx(a["at"]) and f["steps"] == 5


def test_with_speakers_settled_it_runs_through_the_review(conn, monkeypatch):
    monkeypatch.setattr(PL, "insight_ready", lambda conn, sid: True)
    _session(conn, "ses_a", 10, NOW - 3600)
    _run(conn, "ses_a", "identify")
    a = E.forecast(conn, NOW)["sessions"]["ses_a"]
    assert a["then"] == "review"
    assert a["at"] == pytest.approx(NOW + _left(["identify", "numbers", "insight"], 10))


def test_the_newest_conversation_comes_first(conn):
    _session(conn, "ses_old", 5, NOW - 7200)
    _session(conn, "ses_new", 5, NOW - 600)
    for sid in ("ses_old", "ses_new"):
        _run(conn, sid, "transcribe")
    s = E.forecast(conn, NOW)["sessions"]
    assert s["ses_new"]["at"] < s["ses_old"]["at"]


def test_a_review_waits_for_claude(conn, monkeypatch):
    monkeypatch.setattr(PL, "insight_ready", lambda conn, sid: True)
    monkeypatch.setattr(P, "claude_blocked_until", lambda conn, now=None: NOW + 3600)
    monkeypatch.setattr(RN, "claude_blocked_until", lambda conn, now=None: NOW + 3600)
    _session(conn, "ses_a", 10, NOW - 3600)
    _run(conn, "ses_a", "insight", params={"model": "claude-opus-5-5"})
    a = E.forecast(conn, NOW)["sessions"]["ses_a"]
    assert a["claude_wait"] == NOW + 3600 and a["at"] == pytest.approx(NOW + 3600 + _left(["insight"], 10))


def test_a_step_well_past_its_time_is_late(conn):
    _session(conn, "ses_a", 2, NOW - 3600)
    _run(conn, "ses_a", "numbers", "running", started=NOW - 600)
    assert E.forecast(conn, NOW)["sessions"]["ses_a"]["late"] is True


def test_nothing_waiting_nothing_to_say(conn):
    assert E.forecast(conn, NOW) == {"sessions": {}, "queue_empty_at": None, "steps": 0}


def test_its_own_runs_replace_the_default(conn):
    for i, m in enumerate((1, 2, 4, 6, 9)):
        _session(conn, f"ses_{i}", m, NOW - 9000)
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, queued_at, "
                     "started_at, finished_at) VALUES (?, 'speakers', ?, 'h', 'done', 1, '[]', '{}', 0, 100, ?)",
                     (f"r{i}", f"ses_{i}", 100 + 10 + 3 * m))
    assert E.rates(conn)["speakers"] == pytest.approx((10, 3))
