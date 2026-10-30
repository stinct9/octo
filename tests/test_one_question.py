"""One question per conversation (6 Oct, E2 in docs/coach-plan.md): "What here was new to you?"
replaces rating every item. The items picked are marked new; "nothing new" is an answer too; either
makes the conversation reviewed. Conversations rated item by item before keep counting. Also "Let it
go" for a review step, and what deleting a conversation takes with it."""

import json

import pytest

from octo import privacy as PV
from octo import review as R
from octo import sessions as SV
from octo.db import row, rows
from octo.planning import pinned_review

from test_close_the_loop import SID, SID2, _client, _lab, _review
from test_corrections import DZ

QUOTE = {"line": 1, "start": 20.0, "speaker": "B", "text": "no downside", "uncertain": False}
REVIEW = {"kind": "review", "skipped": None, "summary": "s",
          "one_thing": {"headline": "You answered before Kiran finished", "why": "w", "quotes": [QUOTE],
                        "unverified_quotes": 0, "flags": [], "next": 0},
          "reading": [{"person": "Kiran", "headline": "Sets rules from examples", "observation": "o", "quotes": [QUOTE],
                       "unverified_quotes": 0, "flags": []}],
          "between": [{"headline": "The flag question stayed open", "observation": "o", "quotes": [QUOTE],
                       "unverified_quotes": 0, "flags": []}],
          "conduct": [{"principle": 7, "title": "Stay calm", "held": "held", "observation": "o", "quotes": [QUOTE],
                       "unverified_quotes": 0, "flags": [], "try_next_time": None}],
          "beneath": [], "no_moment": [], "dropped": [],
          "next": [{"title": "Ask Kiran about the flag", "detail": "d", "why": "w", "due": "2026-10-08"}]}


def _settled(monkeypatch):
    """The list state from the review on: speakers settled, every stage done."""
    monkeypatch.setattr(SV, "SESSION_STAGES", ["diarize", "insight"])
    monkeypatch.setattr(SV, "settled_speakers", lambda conn, sid: [])
    monkeypatch.setattr(SV, "paused", lambda tr, conn=None: None)


def _with_diarize(conn, sid):
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES (?, 'diarize', ?, 'h', 'done', 1, ?, 0)", (f"dz_{sid}", sid, json.dumps(DZ)))


def test_the_review_lists_the_one_thing_first():
    assert [i["id"] for i in R.items(REVIEW)] == ["o", "r0", "l0", "p7", "n0"]


def test_picking_what_was_new_reviews_the_conversation(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    _review(conn, SID, "run_1", out=REVIEW)
    _with_diarize(conn, SID)
    _settled(monkeypatch)
    assert SV.todo(conn, SID) == {"kind": "review"}
    assert not pinned_review(conn, SID)

    R.answer(conn, SID, "run_1", ["o", "l0"], False, now=100.0)
    assert R.answer_for(conn, "run_1") == {"nothing_new": False, "new": ["l0", "o"], "answered_at": 100.0}
    assert row(conn, "SELECT reviewed_at FROM sessions WHERE id=?", SID)["reviewed_at"] == 100.0
    assert SV.todo(conn, SID) == {"kind": "reviewed"}
    assert pinned_review(conn, SID)  # answered: kept until the owner asks for a redo

    g = R.checks(conn)
    assert g["checks"]["reviewed"]["value"] == 1 and g["checks"]["new_share"]["value"] == 1
    assert g["novelty"] == {"new": 2, "knew": 0, "nothing_new": 0}
    assert g["wrong"] == {"value": 0, "of": 5}  # what he could have marked wrong: every item shown

    # Changing the answer: l0 wasn't new after all. The first answer's time stays.
    R.answer(conn, SID, "run_1", ["o"], False, now=200.0)
    assert R.answer_for(conn, "run_1")["new"] == ["o"] and R.answer_for(conn, "run_1")["answered_at"] == 100.0
    assert row(conn, "SELECT novelty FROM feedback WHERE insight_run='run_1' AND item='l0'")["novelty"] is None


def test_nothing_new_is_an_answer_and_counts_as_reviewed_but_not_new(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    _review(conn, SID, "run_1", out=REVIEW)
    _with_diarize(conn, SID)
    _settled(monkeypatch)
    R.answer(conn, SID, "run_1", [], True)
    assert SV.todo(conn, SID) == {"kind": "reviewed"}
    g = R.checks(conn)
    assert g["checks"]["reviewed"]["value"] == 1 and g["checks"]["new_share"]["value"] == 0
    assert g["novelty"]["nothing_new"] == 1


def test_an_answer_must_be_one_or_the_other(tmp_path):
    conn = _lab(tmp_path)
    _review(conn, SID, "run_1", out=REVIEW)
    with pytest.raises(R.AnswerError, match="pick what was new") as e:
        R.answer(conn, SID, "run_1", [], False)
    assert e.value.status == 422
    with pytest.raises(R.AnswerError, match="pick what was new"):
        R.answer(conn, SID, "run_1", ["o"], True)
    with pytest.raises(R.AnswerError) as e:
        R.answer(conn, SID, "run_1", ["p9"], False)
    assert e.value.status == 404
    with pytest.raises(R.AnswerError) as e:
        R.answer(conn, SID2, "run_1", ["o"], False)  # another conversation's review
    assert e.value.status == 404
    _review(conn, SID2, "short", out={"kind": "review", "skipped": "too short", "conduct": []})
    with pytest.raises(R.AnswerError):
        R.answer(conn, SID2, "short", [], True)
    assert not rows(conn, "SELECT * FROM review_answers")


def test_item_by_item_ratings_from_before_still_count(tmp_path, monkeypatch):
    """Rated in full before 6 Oct: reviewed, and its "new" counts. Half rated: the question asks,
    with what he marked new already ticked, and a "knew it" stays as he said."""
    conn = _lab(tmp_path)
    old = {**REVIEW, "one_thing": None}
    _review(conn, SID, "full", out=old)
    _review(conn, SID2, "half", out=old)
    _with_diarize(conn, SID)
    _with_diarize(conn, SID2)
    _settled(monkeypatch)
    for item in ("r0", "l0", "p7", "n0"):
        conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, novelty, updated_at) VALUES ('full', ?, ?, 'right', ?, 0)",
                     (item, SID, "new" if item == "r0" else "knew"))
    assert R.mark_reviewed_if_done(conn, SID, "full")
    assert SV.todo(conn, SID) == {"kind": "reviewed"}

    conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, novelty, updated_at) VALUES ('half', 'r0', ?, 'right', 'new', 0)", (SID2,))
    conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, novelty, updated_at) VALUES ('half', 'l0', ?, 'right', 'knew', 0)", (SID2,))
    assert SV.todo(conn, SID2) == {"kind": "review"}
    R.answer(conn, SID2, "half", ["r0", "p7"], False)
    fb = R.feedback_for(conn, "half")
    assert fb["r0"]["novelty"] == "new" and fb["p7"]["novelty"] == "new" and fb["l0"]["novelty"] == "knew"
    g = R.checks(conn)
    assert g["checks"]["reviewed"]["value"] == 2 and g["checks"]["new_share"]["value"] == 2


def test_not_right_is_an_optional_flag_that_can_be_cleared(tmp_path):
    conn = _lab(tmp_path)
    _review(conn, SID, "run_1", out=REVIEW)
    c = _client(tmp_path)
    fb = lambda body: c.post(f"/api/octo/sessions/{SID}/feedback", json={"run": "run_1", **body})
    assert fb({"item": "l0", "verdict": "wrong", "note": "Kiran agreed later"}).json()["feedback"]["l0"]["verdict"] == "wrong"
    assert fb({"item": "l0", "verdict": ""}).json()["feedback"]["l0"]["verdict"] is None
    assert fb({"item": "l0", "verdict": "maybe"}).status_code == 422


def test_the_answer_route(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    _review(conn, SID, "run_1", out=REVIEW)
    _with_diarize(conn, SID)
    _settled(monkeypatch)
    c = _client(tmp_path)
    post = lambda body, **h: c.post(f"/api/octo/sessions/{SID}/answer", json={"run": "run_1", **body}, headers=h)
    assert post({"new": ["o"]}, origin="https://evil.example").status_code == 403
    assert post({"new": ["x9"]}).status_code == 404
    assert post({"new": []}).status_code == 422
    r = post({"new": ["o", "n0"]})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"]["new"] == ["n0", "o"] and body["todo"] == {"kind": "reviewed"}
    assert body["feedback"]["o"]["novelty"] == "new"
    d = c.get(f"/api/octo/sessions/{SID}").json()
    assert d["answer"]["new"] == ["n0", "o"] and not d["answer"]["nothing_new"]
    assert c.post("/api/octo/sessions/ses_20990101_deadbeef/answer", json={"run": "run_1", "new": ["o"]}).status_code == 404


def test_let_it_go_takes_a_step_off_the_list_without_counting_it_acted(tmp_path):
    conn = _lab(tmp_path)
    _review(conn, SID, "run_1", out=REVIEW)
    c = _client(tmp_path)
    assert [t["item"] for t in c.get("/api/octo/actions").json()] == ["n0"]
    r = c.post(f"/api/octo/sessions/{SID}/feedback", json={"run": "run_1", "item": "n0", "let_go": True}).json()
    assert r["feedback"]["n0"]["let_go"] is True and r["feedback"]["n0"]["acted"] is False
    assert c.get("/api/octo/actions").json() == []
    assert R.checks(conn)["checks"]["acted"]["value"] == 0
    c.post(f"/api/octo/sessions/{SID}/feedback", json={"run": "run_1", "item": "n0", "let_go": False})  # Undo
    assert [t["item"] for t in c.get("/api/octo/actions").json()] == ["n0"]


def test_deleting_a_conversation_takes_its_answer(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    monkeypatch.setattr(PV, "_silence", lambda *a: None)
    _review(conn, SID, "run_1", out=REVIEW)
    R.answer(conn, SID, "run_1", ["o"], False)
    PV.delete_session(conn, SID)
    assert not rows(conn, "SELECT * FROM review_answers") and not rows(conn, "SELECT * FROM feedback")


def test_deleting_a_part_of_an_answered_conversation_unreviews_it(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    monkeypatch.setattr(PV, "_silence", lambda *a: None)
    _review(conn, SID, "run_1", out=REVIEW)
    R.answer(conn, SID, "run_1", [], True)
    PV.delete_range(conn, SID, 10.0, 20.0)
    assert row(conn, "SELECT reviewed_at FROM sessions WHERE id=?", SID)["reviewed_at"] is None
    assert not rows(conn, "SELECT * FROM review_answers")


def test_a_redone_review_asks_again_but_the_gate_keeps_its_count(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    _review(conn, SID, "run_1", out=REVIEW)
    _with_diarize(conn, SID)
    _settled(monkeypatch)
    R.answer(conn, SID, "run_1", ["o"], False)
    conn.execute("UPDATE stage_runs SET is_current=0 WHERE id='run_1'")
    _review(conn, SID, "run_2", out=json.loads(json.dumps(REVIEW)))
    assert SV.todo(conn, SID) == {"kind": "review"}
    assert R.checks(conn)["checks"]["reviewed"]["value"] == 1
