"""TickTick (octo/ticktick.py): what a task looks like when it's sent, and sending it once.
TickTick itself is never called: `_call` is replaced."""

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import ticktick as TT
from octo.api import make_router
from octo.db import connect

TASK = {"title": "Send Kiran the co-supplier dates table", "detail": "One row per system.", "why": "He asked.",
        "due": "2026-10-08"}


def test_an_all_day_due_date_is_midnight_in_jays_time_zone():
    f = TT.due_fields("2026-10-08", "Asia/Kolkata")
    assert f["dueDate"] == "2026-10-07T18:30:00+0000" and f["isAllDay"] and f["timeZone"] == "Asia/Kolkata"
    assert TT.due_fields(None, "Asia/Kolkata") == {} and TT.due_fields("soon", "Asia/Kolkata") == {}


def test_a_task_goes_to_the_inbox_with_its_details_and_a_link_back():
    calls = []

    def call(method, path, token, body=None):
        calls.append(body)
        if "projectId" not in body:  # suppose the Inbox needs naming
            raise TT.TickTickError("TickTick said 400: projectId required", 400)
        return {"id": "t1", "projectId": "inbox123"}

    got = TT.add(TASK, "Thursday 1 Oct 2026, 20:31", "ses_x", cfg={"token": "k", "timezone": "Asia/Kolkata"}, call=call)
    assert got == {"task_id": "t1", "project_id": "inbox123", "url": "https://ticktick.com/webapp/#p/inbox123/tasks/t1"}
    assert [("projectId" in b) for b in calls] == [False, True] and calls[1]["projectId"] == "inbox"
    body = calls[1]
    assert body["title"] == TASK["title"] and body["tags"] == ["conversation-lab"]
    assert "One row per system." in body["content"] and "Why: He asked." in body["content"]
    assert "127.0.0.1:8765/#/c/ses_x" in body["content"] and body["dueDate"].startswith("2026-10-07T18:30")


def test_a_bad_token_is_not_retried():
    def call(*a, **k):
        raise TT.TickTickError("TickTick refused the token: make a new one and run `octo ticktick setup`")
    with pytest.raises(TT.TickTickError, match="refused"):
        TT.add(TASK, "x", "ses_x", cfg={"token": "k"}, call=call)
    with pytest.raises(TT.TickTickError, match="setup"):
        TT.add(TASK, "x", "ses_x", cfg={})


def test_the_button_sends_a_task_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    db = tmp_path / "t.db"
    conn = connect(db)
    conn.execute("INSERT INTO recordings (id, path, source, started_at, duration_s, created_at) VALUES ('rec_1', 'a.flac', 'device', 1790863200, 3000, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) VALUES ('ses_20261001_aaaaaaaa', 'rec_1', 3660, 5750, 1900, 'active', 0)")
    out = {"kind": "review", "next": [TASK]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('run_1', 'insight', 'ses_20261001_aaaaaaaa', 'h', 'done', 1, ?, 0)", (json.dumps(out),))
    sent = []
    monkeypatch.setattr(TT, "add", lambda task, when, sid: sent.append((task["title"], when)) or
                        {"task_id": "t1", "project_id": "inbox", "url": "u"})
    app = FastAPI()
    app.include_router(make_router(lambda: connect(db), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    for _ in range(2):
        r = c.post("/api/octo/sessions/ses_20261001_aaaaaaaa/ticktick", json={"run": "run_1", "item": "n0"})
        assert r.status_code == 200 and r.json()["ticktick"]["n0"]["task_id"] == "t1"
    assert len(sent) == 1 and sent[0][1] == "Thursday 1 Oct 2026, 20:31"
    assert c.post("/api/octo/sessions/ses_20261001_aaaaaaaa/ticktick", json={"run": "run_1", "item": "n5"}).status_code == 404


def test_settings_take_the_token_but_never_give_it_back(tmp_path, monkeypatch):
    monkeypatch.setattr(TT, "CONFIG", tmp_path / "ticktick.json")
    lists = [{"id": "p1", "name": "Work"}, {"id": "p2", "name": "Home", "closed": True}]

    def call(method, path, token, body=None, timeout=15):
        if token != "secret-token-1234":
            raise TT.TickTickError("TickTick refused the token: make a new one and run `octo ticktick setup`")
        return lists if path == "/project" else {"timeZone": "Asia/Kolkata"}

    monkeypatch.setattr(TT, "_call", call)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    assert c.get("/api/octo/settings/ticktick").json() == {"connected": False}
    assert c.post("/api/octo/settings/ticktick", json={"token": "wrong"}).status_code == 400  # checked first
    assert not TT.CONFIG.exists()
    r = c.post("/api/octo/settings/ticktick", json={"token": " secret-token-1234 "}).json()
    assert r["connected"] and r["list"] == "Inbox" and r["token_ends"] == "1234" and r["timezone"] == "Asia/Kolkata"
    assert r["lists"] == [{"id": "p1", "name": "Work"}]  # closed lists left out
    assert "secret-token-1234" not in json.dumps(r) and "secret-token-1234" not in json.dumps(c.get("/api/octo/settings/ticktick").json())
    assert TT.CONFIG.stat().st_mode & 0o777 == 0o600
    assert c.post("/api/octo/settings/ticktick/list", json={"list_id": "p1"}).json()["list"] == "Work"
    assert c.post("/api/octo/settings/ticktick/list", json={"list_id": "nope"}).status_code == 400
    assert c.post("/api/octo/settings/ticktick/list", json={"list_id": None}).json()["list"] == "Inbox"
    assert c.post("/api/octo/settings/ticktick/disconnect", json={}).json() == {"connected": False}
    assert not TT.CONFIG.exists()


def test_after_a_timeout_it_doesnt_try_the_inbox_and_make_a_second_task():  # M23
    calls = []

    def call(method, path, token, body=None):
        calls.append(body)
        raise TT.TickTickError("Couldn't reach TickTick: timed out")  # it may have been made all the same

    with pytest.raises(TT.TickTickError, match="check TickTick"):
        TT.add(TASK, "x", "ses_x", cfg={"token": "k"}, call=call)
    assert len(calls) == 1
