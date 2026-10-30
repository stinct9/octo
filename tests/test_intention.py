"""B15: an intention before, a reflection after (octo/intention.py). The intention joins the review's input only
when a conversation took one, so no other review is made again; the reflection never reaches the review."""

import json
import time

import pytest

from octo import claude_code as CC
from octo import insight as I
from octo import intention as IN
from octo import person as PE
from octo import rubric as RB
from octo import review_any as RA
from octo.db import connect, row, rows
from octo.pipeline import wanted
from octo.stages import STAGES
from tests.test_commitments import RUBRIC, make
from tests.test_review_any import RUBRIC as PRINCIPLES

PINNED = "244b9216aef4b70a"  # test_commitments: the review's hash for this conversation since before B14


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(RB, "text", lambda: RUBRIC)
    return connect(tmp_path / "i.db")


def hash_of(conn, sid, dz="dz_1"):
    return wanted(conn, STAGES["insight"], sid, [dz])


def test_without_an_intention_the_reviews_input_hash_is_what_it_was(conn):
    sid = make(conn)
    # Set after the conversation started, and one for someone else: neither is this conversation's.
    IN.set_open(conn, "per_asha", "Ask about her week first")
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('per_ravi', 'Ravi', 0, 0)")
    conn.execute("UPDATE intentions SET created_at=0")
    IN.set_open(conn, "per_ravi", "Listen to the end")
    conn.execute("UPDATE intentions SET created_at=? WHERE person_id='per_asha'", (time.time(),))
    params, h = hash_of(conn, sid)
    assert "intentions" not in params and h == PINNED
    assert IN.open_for(conn, "per_asha") and IN.open_for(conn, "per_ravi")  # still waiting: nothing was taken


def test_the_next_conversation_with_them_takes_it_once_and_its_review_reads_it(conn):
    IN.set_open(conn, "per_asha", "  Ask about her week\nfirst  ")
    conn.execute("UPDATE intentions SET created_at=?", (time.time() - 7200,))
    assert IN.open_for(conn, "per_asha")["text"] == "Ask about her week first"  # one line
    sid = make(conn)  # an hour ago, with Asha confirmed
    params, h = hash_of(conn, sid)
    assert params["intentions"] == [{"person": "Asha", "text": "Ask about her week first"}] and h != PINNED
    assert IN.open_for(conn, "per_asha") is None  # cleared from her page…
    assert [i["text"] for i in IN.for_session(conn, sid)] == ["Ask about her week first"]  # …kept with the conversation
    assert hash_of(conn, sid)[1] == h  # asked again, the same input
    later = make(conn, "ses_20261019_00000002", at=time.time() - 60, rec="rec_20261019_00000002", dz_id="dz_2")
    assert "intentions" not in hash_of(conn, later, "dz_2")[0]  # taken once

    page = PE.page(conn, "per_asha")
    assert page["intention"] is None and page["last_intention"]["session_id"] == sid and page["last_intention"]["happened"] is None
    assert IN.set_open(conn, "per_asha", "Thank her for the deck")["text"] == "Thank her for the deck"
    assert hash_of(conn, sid)[1] == h  # a new one doesn't touch the conversation that took the last


def test_a_review_the_owner_answered_is_never_given_one(conn):
    sid = make(conn)
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, output, queued_at) "
                 "VALUES ('ins_1', 'insight', ?, 'h', 'done', 1, '[\"dz_1\"]', '{}', '{}', 0)", (sid,))
    conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, updated_at) VALUES ('ins_1', 'o', ?, 'right', 0)", (sid,))
    IN.set_open(conn, "per_asha", "Ask about her week first")
    conn.execute("UPDATE intentions SET created_at=0")
    assert "intentions" not in hash_of(conn, sid)[0] and IN.open_for(conn, "per_asha")


def test_too_short_to_review_or_not_settled_takes_nothing(conn):
    IN.set_open(conn, "per_asha", "Ask about her week first")
    conn.execute("UPDATE intentions SET created_at=0")
    sid = make(conn)
    conn.execute("UPDATE session_speakers SET state='suggested' WHERE speaker='B'")
    assert IN.take(conn, sid) == [] and IN.open_for(conn, "per_asha")
    conn.execute("UPDATE session_speakers SET state='confirmed'")
    conn.execute("UPDATE stage_runs SET output=? WHERE id='dz_1'",
                 (json.dumps({**json.loads(row(conn, "SELECT output FROM stage_runs WHERE id='dz_1'")["output"]), "talk_s": {"A": 5.0, "B": 30.0}}),))
    assert IN.take(conn, sid) == [] and IN.open_for(conn, "per_asha")  # the owner spoke 5 s: the review would be skipped


def test_someone_said_not_to_be_there_gives_it_back(conn):
    IN.set_open(conn, "per_asha", "Ask about her week first")
    conn.execute("UPDATE intentions SET created_at=0")
    sid = make(conn)
    assert IN.take(conn, sid)
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('per_ravi', 'Ravi', 0, 0)")
    conn.execute("UPDATE session_speakers SET person_id='per_ravi' WHERE speaker='B'")
    assert IN.take(conn, sid) == [] and IN.open_for(conn, "per_asha")["text"] == "Ask about her week first"


def test_deleting_the_conversation_forgetting_or_merging_the_person(conn):
    from octo import privacy as PR
    from octo import voice as V
    IN.set_open(conn, "per_asha", "Ask about her week first")
    conn.execute("UPDATE intentions SET created_at=0")
    sid = make(conn)
    IN.take(conn, sid)
    IN.reflect(conn, sid, "It went better than I feared.")
    conn.execute("UPDATE recordings SET audio_deleted_at=1")  # nothing to silence in a test
    PR.delete_session(conn, sid)
    assert not rows(conn, "SELECT 1 FROM intentions") and not rows(conn, "SELECT 1 FROM reflections")

    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('per_a2', 'Asha M', 0, 0)")
    IN.set_open(conn, "per_asha", "Thank her")
    IN.set_open(conn, "per_a2", "Ask her first")
    V.rename(conn, "Asha M", "Asha")  # the same person: hers stays, the duplicate's open one goes
    assert [r["text"] for r in rows(conn, "SELECT text FROM intentions")] == ["Thank her"]
    assert V.forget(conn, "Asha") and not rows(conn, "SELECT 1 FROM intentions")


def test_too_long_is_refused_and_empty_clears(conn):
    make(conn)
    with pytest.raises(ValueError):
        IN.set_open(conn, "per_asha", "x" * 301)
    IN.set_open(conn, "per_asha", "Listen")
    assert IN.set_open(conn, "per_asha", "  ") is None
    sid = "ses_20261019_00000001"
    assert IN.reflect(conn, sid, " Fine. \n\nTense at the start. ")["text"] == "Fine. \n\nTense at the start."
    assert IN.reflect(conn, sid, "") is None and IN.reflection(conn, sid) is None


# ---- the review reads it ------------------------------------------------------------------------

DZ = {"speakers": ["A", "B"], "talk_s": {"A": 40.0, "B": 30.0},
      "turns": [{"speaker": "A", "start": 0.0, "end": 40.0}, {"speaker": "B", "start": 40.0, "end": 70.0}],
      "overlapping_turns": [],
      "lines": [{"id": 0, "speaker": "A", "start": 1.0, "end": 5.0, "text": "How was your week, before we start?", "uncertain": False},
                {"id": 1, "speaker": "B", "start": 6.0, "end": 9.0, "text": "Long, but good.", "uncertain": False}]}
ANSWER = {"summary": "s", "one_thing": {}, "reading": [], "between": [], "conduct": [], "no_moment": [], "next": [], "limits": "",
          "intention": [{"person": "Asha", "happened": "yes", "observation": "You asked first.",
                         "quotes": [{"line": 0, "text": "How was your week"}]},
                        {"person": "Ravi", "happened": "partly", "observation": "Made up.", "quotes": [{"line": 1, "text": "Long, but good."}]}]}
PARAMS = {"model": "claude-opus-5-5", "effort": "high", "min_jay_speech_s": 5.0, "max_ctx": 32768}
NAMES = {"A": "Sam", "B": "Asha"}
WITH = [{"person": "Asha", "text": "Ask about her week first"}, {"person": "Ravi", "text": "Listen to the end"}]


def test_the_review_says_whether_it_happened_with_the_owners_own_words(monkeypatch):
    sent = {}

    def ask(model, effort, system, prompt, schema, **kw):
        sent.update(system=system, prompt=prompt, schema=schema)
        return ANSWER, {}
    monkeypatch.setattr(CC, "ask", ask)
    out = I.run(DZ, NAMES, {"A"}, {**PARAMS, "intentions": WITH}, rubric_text=PRINCIPLES)
    assert "- Next time with Asha, I want to: Ask about her week first" in sent["prompt"]
    assert "- intention:" in sent["system"] and "intention" in sent["schema"]["required"]
    assert [(i["person"], i["happened"]) for i in out["intention"]] == [("Asha", "yes"), ("Ravi", None)]  # Ravi: not his words
    assert {"section": "intention", "headline": "Listen to the end", "reason": "no verified quote of your own words"} in out["dropped"]


def test_without_one_the_review_is_asked_exactly_as_before(monkeypatch):
    sent = {}
    monkeypatch.setattr(CC, "ask", lambda m, e, system, prompt, schema, **k: (sent.update(system=system, prompt=prompt, schema=schema),
                                                                              ({**ANSWER, "intention": []}, {}))[1])
    out = I.run(DZ, NAMES, {"A"}, PARAMS, rubric_text=PRINCIPLES)
    assert sent["schema"] is RA.SCHEMA and "intention" not in out and "intention" not in sent["system"]
    assert "Next time with" not in sent["prompt"] and "\n\nNumbers for this conversation:" in sent["prompt"]
    assert sent["system"] == RA.system_prompt({"name": "Sam"}, I.review_rules(PRINCIPLES))


# ---- the routes, the pages ----------------------------------------------------------------------

def test_the_routes_and_the_pages(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router
    monkeypatch.setattr(RB, "text", lambda: RUBRIC)
    conn = connect(tmp_path / "t.db")
    sid = make(conn)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    assert c.post("/api/octo/people/per_asha/intention", json={"text": "Ask about her week first"}).json()["intention"]["text"] \
        == "Ask about her week first"
    assert c.get("/api/octo/people/per_asha").json()["intention"]["text"] == "Ask about her week first"
    assert c.post("/api/octo/people/per_sam/intention", json={"text": "x"}).status_code == 404  # not with yourself
    assert c.post("/api/octo/people/per_asha/intention", json={"text": "x" * 301}).status_code == 422
    assert c.post(f"/api/octo/sessions/{sid}/reflection", json={"text": "Tense at first."}).json()["reflection"]["text"] == "Tense at first."
    page = c.get(f"/api/octo/sessions/{sid}").json()
    assert page["reflection"]["text"] == "Tense at first." and page["intentions"] == []
    assert c.post("/api/octo/sessions/ses_20261019_ffffffff/reflection", json={"text": "x"}).status_code == 404
