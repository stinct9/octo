"""The focus (octo/focus.py): one at a time, checked on the conversations after it, and "did it"
marks the item it came from acted on -- the gate's count -- without taking over the owner's own answer."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import focus as F
from octo import ticktick as TT
from octo.api import make_router
from octo.db import connect, row
from octo.privacy import delete_session

REVIEW = {"kind": "review", "skipped": None, "reading": [], "between": [], "beneath": [],
          "conduct": [{"principle": 3, "quotes": []}], "next": [{"title": "Ask before advising", "detail": "", "why": "", "due": None}]}


def setup(conn):
    conn.execute("INSERT INTO recordings (id, path, source, started_at, duration_s, created_at) VALUES ('rec_1', 'a.flac', 'phone', 1000, 9000, 0)")
    for sid, start in (("ses_20261001_aaaaaaaa", 0), ("ses_20261001_bbbbbbbb", 5000), ("ses_20261001_cccccccc", 7000)):
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) VALUES (?, 'rec_1', ?, ?, 100, 'active', 0)",
                     (sid, start, start + 600))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('run_a', 'insight', 'ses_20261001_aaaaaaaa', 'h', 'done', 1, ?, 0)", (json.dumps(REVIEW),))


def acted(conn, item="n0"):
    r = row(conn, "SELECT acted FROM feedback WHERE insight_run='run_a' AND item=?", item)
    return bool(r and r["acted"])


def test_one_focus_at_a_time_and_which_conversations_it_covers(tmp_path):
    conn = connect(tmp_path / "t.db")
    setup(conn)
    first = F.start(conn, "  Ask before   advising ", "ses_20261001_aaaaaaaa", "run_a", "n0", now=3000)
    assert first["text"] == "Ask before advising"
    assert F.for_session(conn, "ses_20261001_aaaaaaaa", 1000) is None  # before it, and where it came from
    assert F.for_session(conn, "ses_20261001_bbbbbbbb", 6000)["id"] == first["id"]
    second = F.start(conn, "Pause before answering", now=6500)
    assert F.active(conn)["id"] == second["id"]
    assert F.for_session(conn, "ses_20261001_bbbbbbbb", 6000)["id"] == first["id"]  # the one active then
    assert F.for_session(conn, "ses_20261001_cccccccc", 8000)["id"] == second["id"]
    assert [f["id"] for f in F.summary(conn)["past"]] == [first["id"]]


def test_did_it_marks_the_item_acted_and_changing_it_back_undoes_only_that(tmp_path):
    conn = connect(tmp_path / "t.db")
    setup(conn)
    f = F.start(conn, "Ask before advising", "ses_20261001_aaaaaaaa", "run_a", "n0", now=3000)
    F.check(conn, f["id"], "ses_20261001_bbbbbbbb", "did")
    assert acted(conn)
    F.check(conn, f["id"], "ses_20261001_cccccccc", "did")
    F.check(conn, f["id"], "ses_20261001_bbbbbbbb", "missed")
    assert acted(conn)  # another conversation still says "did"
    F.check(conn, f["id"], "ses_20261001_cccccccc", None)
    assert not acted(conn)
    assert F.tally(conn, f["id"]) == {"did": 0, "missed": 1, "na": 0}

    # The owner had marked it acted themselves: the focus never takes that away.
    conn.execute("UPDATE feedback SET acted=1 WHERE insight_run='run_a' AND item='n0'")
    F.check(conn, f["id"], "ses_20261001_cccccccc", "did")
    F.check(conn, f["id"], "ses_20261001_cccccccc", "na")
    assert acted(conn)


def test_deleting_the_conversation_takes_its_focus_with_it(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    setup(conn)
    monkeypatch.setattr("octo.privacy._silence", lambda *a: None)
    f = F.start(conn, "Ask before advising", "ses_20261001_aaaaaaaa", "run_a", "n0", now=3000)
    F.check(conn, f["id"], "ses_20261001_bbbbbbbb", "na")
    delete_session(conn, "ses_20261001_aaaaaaaa")
    assert F.active(conn) is None and not row(conn, "SELECT 1 FROM focus_checks")


def test_the_routes(tmp_path):
    db = tmp_path / "t.db"
    setup(connect(db))
    app = FastAPI()
    app.include_router(make_router(lambda: connect(db), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    assert c.get("/api/octo/focus").json() == {"active": None, "past": []}
    assert c.post("/api/octo/focus", json={"text": "x", "session_id": "ses_20261001_aaaaaaaa", "run": "run_a", "item": "n9"}).status_code == 404
    assert c.post("/api/octo/focus", json={"text": " "}).status_code == 422
    got = c.post("/api/octo/focus", json={"text": "Ask before advising", "session_id": "ses_20261001_aaaaaaaa",
                                         "run": "run_a", "item": "n0"}).json()["active"]
    conn = connect(db)
    conn.execute("UPDATE focus SET started_at=3000")
    # The conversation it came from isn't a test of it; a later one is.
    assert c.post("/api/octo/sessions/ses_20261001_aaaaaaaa/focus", json={"focus_id": got["id"], "answer": "did"}).status_code == 409
    r = c.post("/api/octo/sessions/ses_20261001_bbbbbbbb/focus", json={"focus_id": got["id"], "answer": "did"})
    assert r.status_code == 200 and r.json()["focus"]["answer"] == "did" and r.json()["focus"]["tally"]["did"] == 1
    assert acted(conn)
    assert c.post("/api/octo/sessions/ses_20261001_bbbbbbbb/focus", json={"focus_id": got["id"], "answer": "maybe"}).status_code == 422
    assert c.get("/api/octo/sessions/ses_20261001_bbbbbbbb").json()["focus"]["text"] == "Ask before advising"
    assert c.post("/api/octo/focus/stop", json={}).json()["active"] is None


def test_ticktick_done_is_read_only_and_says_when_it_cannot_tell():
    calls = []

    def call(method, path, token, body=None, timeout=15):
        calls.append((method, path))
        if path.endswith("/t3"):
            raise TT.TickTickError("TickTick said 404: gone")
        return {"status": 2 if path.endswith("/t1") else 0}

    sent = {"n0": {"task_id": "t1", "project_id": "p"}, "n1": {"task_id": "t2", "project_id": "p"},
            "n2": {"task_id": "t3", "project_id": "p"}, "n3": {"task_id": "t4", "project_id": None}}
    assert TT.done(sent, cfg={"token": "k"}, call=call) == {"n0": True, "n1": False, "n2": None, "n3": None}
    assert all(m == "GET" for m, _ in calls)
    assert TT.done(sent, cfg={}, call=call) == {k: None for k in sent}
