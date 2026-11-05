"""Talking a conversation through (B31, octo/talk.py) on a made-up lab, the model mocked: only once it has its review,
what the model is sent, moments checked against the lines, the day's count and a usage limit, a stopped reply, and the
thread gone on Start over, a delete or a Forget."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import claude_code as CC
from octo import intention as INT
from octo import pipeline as P
from octo import privacy as PR
from octo import settings as ST
from octo import talk as TK
from octo import voice as V
from octo.api import make_router
from octo.db import connect, row

from test_coach import _lab, _three
from tests.calls import through_reader

CLAUDE = {"kind": "claude-code", "model": "claude-opus-5-5", "effort": "high"}
REPLY = {"reply": "Kiran wasn't saying no.\n\n- He wanted the dates first.",
         "moments": [{"line": 1, "text": "not sure the dates work"}, {"line": 3, "text": "I never said this"}]}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    s1, s2, _ = _three(conn)
    monkeypatch.setattr(ST, "reader", lambda conn: CLAUDE)
    monkeypatch.setattr(CC, "claude_bin", lambda: "claude")
    seen, answers, started = [], [], []

    def ask(who, effort, system, prompt, schema):
        seen.append({"effort": effort, "system": system, "prompt": prompt})
        a = answers.pop(0) if answers else json.loads(json.dumps(REPLY))
        if isinstance(a, Exception):
            raise a
        return a, {"output_tokens": 40}
    monkeypatch.setattr(TK, "_ask", through_reader(ask))
    db = lambda: connect(tmp_path / "t.db")  # noqa: E731
    app = FastAPI()
    app.include_router(make_router(db, processor=SimpleNamespace(kick=lambda: None), talker=started.append))
    return SimpleNamespace(conn=conn, seen=seen, answers=answers, started=started, s1=s1, s2=s2, client=TestClient(app))


def test_a_message_then_a_reply_with_its_moments_checked(lab):
    c = lab.client
    INT.reflect(lab.conn, lab.s1, "I pushed too hard on Friday.")
    v = c.post(f"/api/octo/sessions/{lab.s1}/talk", json={"text": "Was Kiran saying no?"}).json()
    assert v["running"] and [t["by"] for t in v["turns"]] == ["me"] and lab.started == [lab.s1] and lab.seen == []
    assert c.post(f"/api/octo/sessions/{lab.s1}/talk", json={"text": "And?"}).status_code == 409  # one reply at a time
    v = TK.run(lab.conn, lab.s1)
    assert not v["running"] and v["error"] is None
    reply = v["turns"][1]
    assert reply["by"] == "model" and reply["text"].startswith("Kiran wasn't saying no.") and reply["dropped"] == 1
    assert [(m["line"], m["who"]) for m in reply["moments"]] == [(1, "Kiran")]
    assert "reader" not in reply and "usage" not in reply
    s = lab.seen[0]
    assert s["effort"] == "medium" and "winning the room" in s["system"] and "that's an inference" in s["system"]
    assert "[1] 1:00 Kiran: I'm not sure the dates work for the supplier at all." in s["prompt"]  # the lines, numbered
    assert "[0] 0:00 Sam:" in s["prompt"]  # the owner by name
    assert "The one thing: You answered before Kiran finished (1)" in s["prompt"]  # the review as he read it
    assert "I pushed too hard on Friday." in s["prompt"]  # his reflection
    assert s["prompt"].endswith("Sam: Was Kiran saying no?\n\nAnswer Sam's last message.")
    # The next message carries the thread.
    c.post(f"/api/octo/sessions/{lab.s1}/talk", json={"text": "How could I have asked?"})
    TK.run(lab.conn, lab.s1)
    assert "You: Kiran wasn't saying no." in lab.seen[1]["prompt"]
    assert [t["by"] for t in c.get(f"/api/octo/sessions/{lab.s1}/talk").json()["turns"]] == ["me", "model", "me", "model"]


def test_only_once_it_has_its_review(lab):
    lab.conn.execute("UPDATE stage_runs SET output=? WHERE id=?",
                     (json.dumps({"kind": "review", "skipped": "too short", "conduct": []}), f"insight_{lab.s1}"))
    r = lab.client.post(f"/api/octo/sessions/{lab.s1}/talk", json={"text": "Hm?"})
    assert r.status_code == 422 and "review" in r.json()["detail"]
    assert "review" in lab.client.get(f"/api/octo/sessions/{lab.s1}/talk").json()["why_not"]
    assert lab.client.get("/api/octo/sessions/ses_20261002_ffffffff/talk").status_code == 404


def test_each_reply_counts_in_the_day_and_a_limit_waits(lab):
    before = P.claude_calls_since(lab.conn, 0)
    TK.say(lab.conn, lab.s1, "One?")
    TK.run(lab.conn, lab.s1)
    assert P.claude_calls_since(lab.conn, 0) == before + 1
    lab.answers.append(RuntimeError("Claude Code: You've hit your usage limit · resets 11pm"))
    TK.say(lab.conn, lab.s1, "Two?")
    v = TK.run(lab.conn, lab.s1)
    assert "usage limit" in v["error"] and v["waiting_until"] and P.claude_blocked_until(lab.conn) is not None
    assert v["turns"][-1]["by"] == "me" and "usage limit" in v["why_not"]
    with pytest.raises(TK.TalkError, match="usage limit"):
        TK.say(lab.conn, lab.s1, "")  # asked again only once it resets
    lab.conn.execute("UPDATE talks SET error_at=error_at-86400")
    TK.say(lab.conn, lab.s1, "")  # its reply to "Two?" now
    v = TK.run(lab.conn, lab.s1)
    assert [t["by"] for t in v["turns"]] == ["me", "model", "me", "model"] and v["error"] is None
    assert P.claude_calls_since(lab.conn, 0) == before + 2


def test_a_reply_cut_off_by_a_restart_can_be_asked_again(lab):
    TK.say(lab.conn, lab.s1, "Still there?", now=time.time() - TK.STOPPED_AFTER_S - 5)
    v = TK.view(lab.conn, lab.s1)
    assert not v["running"] and "stopped" in v["error"] and v["why_not"] is None
    TK.say(lab.conn, lab.s1, "")
    assert TK.run(lab.conn, lab.s1)["turns"][-1]["by"] == "model"


def test_limits_on_a_message(lab):
    with pytest.raises(TK.TalkError):
        TK.say(lab.conn, lab.s1, "")  # nothing to answer
    with pytest.raises(TK.TalkError, match="at most"):
        TK.say(lab.conn, lab.s1, "x" * (TK.MAX_LINE + 1))
    lab.conn.execute("INSERT INTO talks (session_id, turns, created_at, updated_at) VALUES (?, ?, 0, 0)",
                     (lab.s1, json.dumps([{"by": "me", "text": "x", "at": 0}] * TK.MAX_TURNS)))
    with pytest.raises(TK.TalkError, match="Start over"):
        TK.say(lab.conn, lab.s1, "One more?")


def test_gone_on_start_over_a_delete_and_a_forget(lab):
    c = lab.client
    for sid in (lab.s1, lab.s2):
        TK.say(lab.conn, sid, "Why?")
        TK.run(lab.conn, sid)
    assert c.post(f"/api/octo/sessions/{lab.s1}/talk/clear", json={}).json()["turns"] == []
    TK.say(lab.conn, lab.s1, "Again?")
    lab.conn.execute("UPDATE recordings SET audio_deleted_at=1")  # nothing to silence in the made-up lab's audio
    PR.delete_session(lab.conn, lab.s1)
    assert row(lab.conn, "SELECT 1 FROM talks WHERE session_id=?", lab.s1) is None
    assert row(lab.conn, "SELECT 1 FROM talks WHERE session_id=?", lab.s2)
    assert V.forget(lab.conn, "Kiran")
    assert row(lab.conn, "SELECT 1 FROM talks WHERE session_id=?", lab.s2) is None


def test_replies_still_count_in_the_day_once_their_thread_is_gone(lab):  # audit 28 Oct
    today = P._midnight(time.time())
    for sid in (lab.s1, lab.s2):
        TK.say(lab.conn, sid, "Why?")
        TK.run(lab.conn, sid)
    before = P.claude_calls_since(lab.conn, today)
    assert before >= 2
    TK.clear(lab.conn, lab.s1)  # Start over
    lab.conn.execute("UPDATE recordings SET audio_deleted_at=1")
    PR.delete_session(lab.conn, lab.s2)
    assert P.claude_calls_since(lab.conn, today) == before


def test_its_effort_is_a_setting(lab):  # 29 Oct: high took a minute or two
    c = lab.client
    assert c.get("/api/octo/settings/talk").json() == {"effort": "medium", "efforts": ["low", "medium", "high"]}
    assert c.post("/api/octo/settings/talk", json={"effort": "max"}).status_code == 422
    assert c.post("/api/octo/settings/talk", json={"effort": "high"}).json()["effort"] == "high"
    c.post(f"/api/octo/sessions/{lab.s1}/talk", json={"text": "Was Kiran saying no?"})
    TK.run(lab.conn, lab.s1)
    assert lab.seen[-1]["effort"] == "high"
