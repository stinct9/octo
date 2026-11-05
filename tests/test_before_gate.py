"""Batch H of the 4 Oct review: what was left before the gate (M17, M29, M03, M09)."""

import json
import time

from octo import pipeline as P
from octo import runs as RN  # the holds and the day's calls (risk 8, 5 Nov)
from octo import review as R
from octo.db import connect
from octo.stages import dropped, readmitted
from tests.calls import spend


def test_dropped_speech_a_speaker_turn_covers_comes_back_unsure():  # M17
    rej = [{"start": 0.0, "end": 10.0, "text": "hello there", "words": [{"w": "hello", "start": 0.0, "end": 1.0},
                                                                       {"w": "there", "start": 1.0, "end": 2.0}]},
           {"start": 50.0, "end": 60.0, "text": "thanks for watching", "words": [{"w": "thanks", "start": 50.0, "end": 51.0}]},
           {"start": 70.0, "text": "stored before 5 Oct"}]  # no words: nothing to give back
    turns = [("A", 0.03, 51.0)]  # pyannote heard someone for 51 s from the start (2ae17b6f)
    back = readmitted(rej, turns, 0.3)
    assert [w["w"] for w in back] == ["hello", "there"] and all(w["p"] == 0.2 for w in back)  # 50-60 s: 1 s covered
    assert readmitted(rej, [("A", 58.0, 59.0)], 0.3) == []  # 1 s of 10: not enough
    assert dropped({"rejected_as_silence": rej}, [{"speaker": "A", "start": 0.03, "end": 51.0}]) == \
        [{"start": 50.0, "text": "thanks for watching"}, {"start": 70.0, "text": "stored before 5 Oct"}]


def _lab(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO recordings (id, path, source, started_at, duration_s, created_at) VALUES ('r', 'a', 'phone', 1790863200, 900, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) VALUES ('ses_20261001_aaaaaaaa', 'r', 0, 600, 60, 'active', 0)")
    return conn


def test_by_principle_lists_what_each_review_said_with_jays_rating(tmp_path):  # M29
    conn = _lab(tmp_path)
    out = {"kind": "review", "skipped": None, "conduct": [{"principle": 2, "title": "Listened", "observation": "He waited."}]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('i', 'insight', 'ses_20261001_aaaaaaaa', 'h', 'done', 1, ?, 0)", (json.dumps(out),))
    conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, novelty, updated_at) VALUES ('i', 'p2', 'ses_20261001_aaaaaaaa', 'partly', 'new', 0)")
    ps = [{"n": 1, "title": "Truth", "text": "Speak truthfully."}, {"n": 2, "title": "Listen", "text": "Listen first."}]
    bp = R.progress(conn, ps)["by_principle"]
    assert bp[0] == {"n": 1, "text": "Speak truthfully.", "seen": []}
    assert bp[1]["seen"][0]["observation"] == "He waited." and bp[1]["seen"][0]["verdict"] == "partly"


def test_the_daily_claude_cap_holds_claude_steps_until_midnight(tmp_path, monkeypatch):  # M09
    conn = _lab(tmp_path)
    now = time.time()
    monkeypatch.setattr(RN, "DAILY_CLAUDE_CALLS", 3)
    spend(conn, 2, at=now)  # e.g. a Hindi merge's two chunks through Claude Code
    spend(conn, 5, {"kind": "anthropic", "connection": "key", "model": "m"}, at=now)  # not Claude Code's: not counted
    assert P.claude_blocked_until(conn, now) is None
    spend(conn, 1, at=now)  # a review
    assert P.claude_blocked_until(conn, now) == P._midnight(now, 1)
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) "
                 "VALUES ('q1', 'insight', 'y', 'h2', 'queued', ?, 1), ('q2', 'identify', 'y', 'h3', 'queued', '{}', 2)",
                 (json.dumps({"model": "claude-opus-5-5"}),))
    assert P.next_run(conn, now)["id"] == "q2"  # the review waits; the rest carries on


def test_the_list_says_where_every_conversation_is(tmp_path):  # M03; on the list's footer since 5 Nov
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router
    _lab(tmp_path)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    states = TestClient(app).get("/api/octo/progress/states").json()
    assert sum(states.values()) == 1 and set(states) >= {"reviewed", "to_review", "waiting_on_you", "cant_count", "not_counted"}


def test_a_long_evening_is_cut_at_its_longest_pause_near_the_hour():  # M26
    from octo.stages import cap_length
    spans = [[t, t + 50.0] for t in range(0, 9000, 60)]  # 2.5 h of talk, a 10 s pause every minute
    spans[45] = [2700.0, 2720.0]  # a 40 s pause after 45 min
    c = {"start": 0.0, "end": 9050.0, "speech": spans, "speech_s": 0.0}
    parts = cap_length([c], 3600.0, 10.0)
    assert len(parts) == 3 and all(p["end"] - p["start"] <= 3600 for p in parts)
    assert parts[0]["end"] == 2720.0  # at the longest pause, not mid-word
    assert cap_length([{"start": 0.0, "end": 100.0, "speech": [[0.0, 100.0]], "speech_s": 100.0}], 3600.0, 10.0)[0]["end"] == 100.0


def test_long_audio_is_filtered_in_blocks_with_seamless_joins():  # M26
    import numpy as np

    from octo import audio as A
    x = np.random.default_rng(0).normal(size=21 * 60 * 16000).astype(np.float32)
    from scipy.signal import butter, sosfiltfilt
    whole = sosfiltfilt(butter(4, 80.0, "highpass", fs=16000, output="sos"), x).astype(np.float32)
    assert np.max(np.abs(A.highpass(x) - whole)) < 1e-3


def test_the_list_can_load_just_the_last_two_weeks(tmp_path):  # M35
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router
    conn = _lab(tmp_path)
    conn.execute("UPDATE recordings SET started_at=? WHERE id='r'", (time.time() - 30 * 86400,))  # a month ago
    conn.execute("INSERT INTO recordings (id, path, source, started_at, duration_s, created_at) VALUES ('r2', 'b', 'phone', ?, 60, 0)",
                 (time.time() - 3600,))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) VALUES ('ses_20261005_bbbbbbbb', 'r2', 0, 60, 30, 'active', 0)")
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    r = c.get("/api/octo/sessions?days=14")
    assert [s["id"] for s in r.json()] == ["ses_20261005_bbbbbbbb"] and r.headers["X-Older"] == "1"
    assert len(c.get("/api/octo/sessions").json()) == 2
    # What the list polls while busy (review 28 Oct, item 3): still while nothing moves, moved by a step and a recording
    token = lambda: c.get("/api/octo/sessions/changed").json()["token"]
    t0 = token()
    assert token() == t0
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at) VALUES ('q', 'insight', 'ses_20261005_bbbbbbbb', 'h', 'queued', 0)")
    t1 = token()
    conn.execute("UPDATE stage_runs SET status='running' WHERE id='q'")
    t2 = token()
    conn.execute("UPDATE stage_runs SET progress='principle 5 of 14' WHERE id='q'")  # as the worker writes it
    t2p = token()
    conn.execute("UPDATE stage_runs SET status='done' WHERE id='q'")
    t3 = token()
    conn.execute("INSERT INTO recordings (id, path, source, duration_s, created_at) VALUES ('r3', 'c', 'phone', 60, 0)")
    assert len({t0, t1, t2, t2p, t3, token()}) == 6


def test_the_claude_cap_is_jays_plans_and_a_connection_has_its_own_budget(tmp_path):
    """Review 8 Oct, finding 15: the 60 a day protect the owner's Pro plan (Claude Code); a buyer's own key or a server on
    their Mac stopped at them too, with a message about Claude. Now each connection may have a budget of its own."""
    from octo import providers as PV
    conn = _lab(tmp_path)
    now = time.time()
    PV.add("openai", label="Local", base_url="http://127.0.0.1:1234/v1", models=["m"])
    reader = {"kind": "openai", "connection": "local", "model": "m"}
    spend(conn, 70, reader, at=now)  # more than the Claude cap, on the owner's own server
    assert P.claude_calls_since(conn, P._midnight(now)) == 0 and P.claude_blocked_until(conn, now) is None
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) "
                 "VALUES ('q', 'insight', 'y', 'h2', 'queued', ?, 1)", (json.dumps({"model": "m", "reader": reader}),))
    assert P.next_run(conn, now)["id"] == "q"  # no budget set: it runs
    PV.update("local", daily_calls=70)
    assert P.budget_blocked_until(conn, "local", now) == P._midnight(now, 1) and P.next_run(conn, now) is None
    PV.update("local", daily_calls=0)  # none again
    assert P.next_run(conn, now)["id"] == "q"
