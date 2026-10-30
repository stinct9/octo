"""Rehearsals (B26, octo/rehearse.py) on a made-up lab, the model mocked: what it's told of the person (never a
reading the owner marked not right), the day's count and a usage limit, the review's quotes checked, kept apart from
conversations (K4), and gone when the owner deletes it or forgets the person."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import claude_code as CC
from octo import coach as CO
from octo import pipeline as P
from octo import rehearse as RH
from octo import settings as ST
from octo import voice as V
from octo.api import make_router
from octo.db import connect, load_json, row

from test_coach import _lab, _three, mark

CLAUDE = {"kind": "claude-code", "model": "claude-opus-5-5", "effort": "high"}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    s1, s2, _ = _three(conn)  # Kiran (p_han) in s1, s2; Tara in s3
    for sid, headline in ((s1, "Kiran hides the supplier risk"), (s2, "Kiran asks for the dates twice")):
        out = load_json(row(conn, "SELECT output FROM stage_runs WHERE id=?", f"insight_{sid}")["output"])
        out["reading"][0]["headline"] = headline
        conn.execute("UPDATE stage_runs SET output=? WHERE id=?", (json.dumps(out), f"insight_{sid}"))
    mark(conn, s1, "r0", verdict="wrong")
    mark(conn, s2, "r0", verdict="partly", note="He raised it early, actually")
    monkeypatch.setattr(ST, "reader", lambda conn: CLAUDE)
    monkeypatch.setattr(CC, "claude_bin", lambda: "claude")
    seen: list[dict] = []
    answers: list = []

    def ask(who, effort, system, prompt, schema):
        seen.append({"effort": effort, "system": system, "prompt": prompt, "schema": schema})
        a = answers.pop(0) if answers else {"reply": "Why didn't you tell me sooner?"}
        if isinstance(a, Exception):
            raise a
        return a, {"output_tokens": 30}
    monkeypatch.setattr(RH, "_ask", ask)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=SimpleNamespace(kick=lambda: None)))
    return SimpleNamespace(conn=conn, seen=seen, answers=answers, s1=s1, s2=s2, client=TestClient(app))


def _reading(conn, sid: str) -> str:
    return load_json(row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1", sid)["output"])["reading"][0]["headline"]


def test_the_person_is_a_guess_from_their_readings_only(lab):
    c = lab.client
    page = c.get("/api/octo/rehearsals").json()
    assert [p["name"] for p in page["people"]] == ["Kiran", "Tara"] and page["rehearsals"] == []
    r = c.post("/api/octo/rehearsals", json={"person_id": "p_han", "hard": "He'll hear it as blame", "want": "Agree new dates"}).json()
    assert r["name"] == "Kiran" and r["turns"] == [] and lab.seen == []  # the owner speaks first: no call yet
    assert [x["session_id"] for x in r["readings"]] == [lab.s2, "ses_20261002_00000004"]  # as his page shows them
    got = c.post(f"/api/octo/rehearsals/{r['id']}/say", json={"text": "The supplier dates slipped again."}).json()
    assert [t["by"] for t in got["turns"]] == ["me", "them"] and got["turns"][1]["text"] == "Why didn't you tell me sooner?"
    sent = lab.seen[0]
    assert _reading(lab.conn, lab.s1) not in sent["system"]  # marked not right: never sent
    assert _reading(lab.conn, lab.s2) in sent["system"] and "He raised it early, actually" in sent["system"]
    assert "not a fact" in sent["system"] and "Make up no facts about Kiran" in sent["system"]
    assert "He'll hear it as blame" in sent["system"] and "Agree new dates" in sent["system"]
    assert sent["prompt"].endswith("[0] Sam: The supplier dates slipped again.") and sent["effort"] == "medium"
    # Nothing else of the lab: no transcript line, no quote, no number.
    lines = [ln["text"] for s in (lab.s1, lab.s2) for ln in json.loads(row(
        lab.conn, "SELECT output FROM stage_runs WHERE target=? AND stage='diarize'", s)["output"])["lines"]]
    assert not any(t in sent["system"] + sent["prompt"] for t in lines)


def test_someone_described_gets_only_the_description(lab):
    r = RH.start(lab.conn, None, "a report who's upset about a rating", "I gave the rating", "That she feels heard")
    RH.say(lab.conn, r, "Thanks for making time.")
    s = lab.seen[0]["system"]
    assert "a report who's upset about a rating" in s and "Kiran" not in s and "You play the other person" in s
    with pytest.raises(RH.RehearsalError):
        RH.start(lab.conn, None, "", "x", "y")  # someone, described or chosen


def test_each_reply_and_the_review_count_in_the_day_and_a_limit_waits(lab):
    before = P.claude_calls_since(lab.conn, 0)
    r = RH.start(lab.conn, "p_han", None, "hard", "want")
    RH.say(lab.conn, r, "One.")
    RH.say(lab.conn, r, "Two.")
    assert P.claude_calls_since(lab.conn, 0) == before + 2
    lab.answers.append(RuntimeError("Claude Code: You've hit your usage limit · resets 11pm"))
    with pytest.raises(RH.RehearsalError) as e:
        RH.say(lab.conn, r, "Three.")
    assert e.value.status == 502 and P.claude_blocked_until(lab.conn) is not None  # the pipeline waits too
    v = RH.view(lab.conn, r)
    assert v["turns"][-1] == {**v["turns"][-1], "by": "me", "text": "Three."} and v["waiting_until"]
    assert "usage limit" in v["why_not"]
    with pytest.raises(RH.RehearsalError, match="usage limit"):
        RH.say(lab.conn, r, "")  # asked again only once it resets
    lab.conn.execute("UPDATE rehearsals SET error_at=error_at-86400")
    RH.say(lab.conn, r, "")  # its reply to "Three." now
    assert RH.view(lab.conn, r)["turns"][-1]["by"] == "them" and RH.view(lab.conn, r)["error"] is None
    assert P.claude_calls_since(lab.conn, 0) == before + 3
    lab.conn.execute("UPDATE rehearsals SET turns=? WHERE id=?",
                     (json.dumps([{"by": "them", "text": "x", "at": time.time(), "reader": CLAUDE}] * P.DAILY_CLAUDE_CALLS), r))
    assert "today's 60 Claude calls are used" in RH.why_not(lab.conn)


def test_the_review_against_the_principles_quotes_checked(lab):
    from octo import rubric as RB
    RB.OWN.write_text("# Rubric\n\n## Conduct principles\n\n1. Own it early.\n\n2. Say the hard part first.\n\n3. Listen to the end.\n")
    r = RH.start(lab.conn, "p_han", None, "hard", "want")
    RH.say(lab.conn, r, "I should have told you on Tuesday.")
    lab.answers.append({
        "summary": "You owned it early.", "limits": "Typed, so no tone.", "no_moment": [],
        "one_thing": {"headline": "You said it plainly", "why": "", "quotes": [{"line": 0, "text": "I should have told you on Tuesday"}], "next": 0},
        "conduct": [{"principle": 1, "held": "held", "observation": "Owned it.", "quotes": [{"line": 0, "text": "told you on Tuesday"}]},
                    {"principle": 2, "held": "missed", "observation": "Made up.", "quotes": [{"line": 0, "text": "I never said this"}]},
                    {"principle": 3, "held": "partly", "observation": "Theirs only.", "quotes": [{"line": 1, "text": "Why didn't you tell me sooner"}]}],
        "next": [{"title": "Name the date first", "detail": "Say the new date before the reasons.", "why": "He asks for it."}]})
    v = RH.stop(lab.conn, r)
    rv = v["review"]
    assert [c["principle"] for c in rv["conduct"]] == [1]  # a made-up quote, and one of the model's lines only: dropped
    assert {d["section"] for d in rv["dropped"]} == {"conduct"} and rv["one_thing"]["next"] == 0
    assert rv["next"][0]["title"] == "Name the date first" and "reader" not in rv and "usage" not in rv
    s = lab.seen[-1]
    assert "Sam's principles, in his words" in s["prompt"] and s["effort"] == "high"
    assert "a guess at Kiran, not Kiran" in s["system"] and "winning the room" in s["system"]
    with pytest.raises(RH.RehearsalError) as e:
        RH.say(lab.conn, r, "More?")
    assert e.value.status == 409
    assert P.claude_calls_since(lab.conn, 0) >= 2


def test_kept_apart_from_conversations(lab):
    c = lab.client
    before = {"list": c.get("/api/octo/sessions").json(),
              "progress": {k: v for k, v in c.get("/api/octo/progress").json().items() if k != "drills"},
              "coach": json.dumps(CO.material(lab.conn, now=2e9), sort_keys=True, default=str)}
    r = RH.start(lab.conn, "p_han", None, "Zanzibar is hard", "Zanzibar agreed")
    RH.say(lab.conn, r, "Zanzibar is the word I typed only in a rehearsal.")
    assert c.get("/api/octo/sessions").json() == before["list"]
    assert "Zanzibar" not in json.dumps(c.get("/api/octo/search", params={"q": "zanzibar"}).json())
    assert {k: v for k, v in c.get("/api/octo/progress").json().items() if k != "drills"} == before["progress"]
    assert json.dumps(CO.material(lab.conn, now=2e9), sort_keys=True, default=str) == before["coach"]
    assert "Zanzibar" not in json.dumps(c.get("/api/octo/people/p_han").json())


def test_deleted_by_the_owner_and_when_the_person_is_forgotten(lab):
    c = lab.client
    a = RH.start(lab.conn, "p_han", None, "hard", "want")
    b = RH.start(lab.conn, None, "a stranger", "hard", "want")
    assert c.post(f"/api/octo/rehearsals/{b}/delete", json={"confirm": "yes"}).status_code == 422
    assert c.post(f"/api/octo/rehearsals/{b}/delete", json={"confirm": "delete"}).json() == {"deleted": b}
    assert c.get(f"/api/octo/rehearsals/{b}").status_code == 404
    assert V.forget(lab.conn, "Kiran")
    assert row(lab.conn, "SELECT 1 FROM rehearsals WHERE id=?", a) is None


def test_a_rename_into_someone_else_keeps_it_with_them(lab):
    a = RH.start(lab.conn, "p_han", None, "hard", "want")
    V.rename(lab.conn, "Kiran", "Tara")
    assert RH.view(lab.conn, a)["name"] == "Tara"


def test_open_intention_is_offered_to_start_from(lab):
    from octo import intention as IN
    IN.set_open(lab.conn, "p_han", "Say the dates first")
    assert next(p for p in RH.summary(lab.conn)["people"] if p["id"] == "p_han")["intention"] == "Say the dates first"


def test_its_calls_still_count_in_the_day_once_it_is_deleted(lab):  # audit 28 Oct
    today = P._midnight(time.time())
    a = RH.start(lab.conn, "p_han", None, "hard", "want")
    b = RH.start(lab.conn, None, "a stranger", "hard", "want")
    RH.say(lab.conn, a, "Can we talk about the dates?")
    RH.say(lab.conn, b, "Can we talk about the dates?")
    before = P.claude_calls_since(lab.conn, today)
    assert before >= 2
    RH.delete(lab.conn, b)
    assert V.forget(lab.conn, "Kiran")
    assert P.claude_calls_since(lab.conn, today) == before
