"""Recordings set aside as not conversations: TV, music, singing (B5, 13 Oct, octo/aside.py)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import octo.api as API
from octo import aside as AS
from octo import pipeline as P
from octo import review as R
from octo import sessions as SV
from octo.db import connect, row, rows
from tests.test_octo import lab, set_stage  # noqa: F401 (the fixture)


def _two(lab):
    P.add_recording(lab["conn"], lab["wav"], "bench")
    lab["run"]()
    return [s["id"] for s in rows(lab["conn"], "SELECT id FROM sessions WHERE status='active' ORDER BY start_s")]


def test_set_aside_stops_processing_and_survives_finding_conversations_again(lab):
    conn = lab["conn"]
    tv, talk = _two(lab)
    AS.mark(conn, tv, "tv")
    assert row(conn, "SELECT status, aside FROM sessions WHERE id=?", tv) == {"status": "set_aside", "aside": "tv"}
    assert SV.todo(conn, tv) == {"kind": "aside", "what": "tv"}
    set_stage(lab, "transcribe", version=2)  # every conversation would be transcribed again...
    lab["run"]()
    assert P.current(conn, tv, "transcribe")["stale"] == 0 and \
        row(conn, "SELECT count(*) n FROM stage_runs WHERE target=? AND stage='transcribe'", tv)["n"] == 1  # ...but not this one
    assert row(conn, "SELECT count(*) n FROM stage_runs WHERE target=? AND stage='transcribe'", talk)["n"] == 2
    set_stage(lab, "find_conversations", version=2)  # the same boundaries found again
    lab["run"]()
    assert row(conn, "SELECT status FROM sessions WHERE id=?", tv)["status"] == "set_aside"
    AS.put_back(conn, tv)
    lab["run"]()  # carries on from where it stopped
    assert row(conn, "SELECT count(*) n FROM stage_runs WHERE target=? AND stage='transcribe' AND status='done'", tv)["n"] == 2
    assert SV.todo(conn, tv)["kind"] != "aside"


def test_a_step_queued_before_it_was_set_aside_is_dropped(lab):
    conn = lab["conn"]
    tv, _ = _two(lab)
    set_stage(lab, "transcribe", version=2)
    P.plan(conn)
    queued = row(conn, "SELECT * FROM stage_runs WHERE target=? AND status='queued'", tv)
    assert queued
    conn.execute("UPDATE sessions SET status='set_aside', aside='music' WHERE id=?", (tv,))  # marked, queue untouched
    assert P._still_valid(conn, queued) is False
    AS.mark(conn, tv, "music")
    assert row(conn, "SELECT status FROM stage_runs WHERE id=?", queued["id"])["status"] == "obsolete"


def test_never_counts_for_the_gate(lab):
    conn = lab["conn"]
    tv, _ = _two(lab)
    conn.execute("UPDATE sessions SET reviewed_at=1 WHERE id=?", (tv,))
    assert R.checks(conn)["checks"]["reviewed"]["value"] == 1
    AS.mark(conn, tv, "other")
    assert R.checks(conn)["checks"]["reviewed"]["value"] == 0


def test_only_known_kinds_and_only_what_is_there(lab):
    conn = lab["conn"]
    tv, _ = _two(lab)
    with pytest.raises(ValueError):
        AS.mark(conn, tv, "podcast")
    assert AS.put_back(conn, tv) is False  # not set aside: kept as the owner's answer, nothing to restart
    assert row(conn, "SELECT status, aside FROM sessions WHERE id=?", tv) == {"status": "active", "aside": "conversation"}
    with pytest.raises(KeyError):
        AS.mark(conn, "ses_20260101_00000000", "tv")
    with pytest.raises(KeyError):
        AS.put_back(conn, "ses_20260101_00000000")


def test_the_routes(lab):
    tmp = P.HOME
    lab["mp"].setattr(API, "DATA", tmp / "data")
    kicks = []
    app = FastAPI()
    app.include_router(API.make_router(lambda: connect(tmp / "t.db"),
                                       processor=type("P", (), {"kick": lambda self: kicks.append(1)})()))
    c = TestClient(app)
    tv, talk = _two(lab)
    r = c.post(f"/api/octo/sessions/{tv}/aside", json={"kind": "tv"})
    assert r.status_code == 200 and r.json()["todo"] == {"kind": "aside", "what": "tv"}
    listed = c.get("/api/octo/sessions")
    assert [s["id"] for s in listed.json()] == [talk] and listed.headers["X-Aside"] == "1"
    assert [(s["id"], s["aside"]) for s in c.get("/api/octo/aside").json()] == [(tv, "tv")]
    page = c.get(f"/api/octo/sessions/{tv}")
    assert page.status_code == 200 and page.json()["aside"] == "tv"  # it can still be opened and heard
    assert c.post(f"/api/octo/sessions/{tv}/retry", json={}).status_code == 404  # but nothing else is done to it
    assert c.post(f"/api/octo/sessions/{tv}/aside", json={"kind": "podcast"}).status_code == 422
    assert c.post(f"/api/octo/sessions/{talk}/aside", json={"kind": None}).status_code == 200  # "it is one": an answer
    assert kicks == []  # nothing to restart for that one
    assert c.post(f"/api/octo/sessions/{tv}/aside", json={"kind": None}).json()["aside"] is None
    assert kicks == [1]  # put back: processed from where it stopped
    assert c.get(f"/api/octo/sessions/{tv}").json()["aside"] is None


def _sp(*selves):
    return [{"letter": chr(65 + i), "self": s} for i, s in enumerate(selves)]


def test_the_guess_is_few_words_nobody_you_and_no_answer_yet():
    sess = {"start_s": 0.0, "end_s": 240.0, "aside": None}  # four minutes
    assert AS.maybe(sess, _sp(False, False), 0) is True  # B1's evening: voices, no words
    assert AS.maybe(sess, _sp(False, False), 79) is True  # under 20 a minute
    assert AS.maybe(sess, _sp(False, False), 80) is False
    assert AS.maybe(sess, _sp(False, False), 800, without_jay=True) is True  # the TV talks a lot
    assert AS.maybe(sess, _sp(True, False), 0) is False  # The owner is in it
    assert AS.maybe(sess, [], 0) is False  # nobody heard yet: still being worked out
    assert AS.maybe({**sess, "aside": "conversation"}, _sp(False), 0) is False  # he said it is one


def test_the_list_offers_the_guess_and_not_after_an_answer(lab):
    conn = lab["conn"]
    tv, _ = _two(lab)
    lab["mp"].setattr(SV, "speakers", lambda c, sid: [{"letter": "A", "name": None, "state": "unknown", "self": False}])
    lab["mp"].setattr(SV, "display", lambda s: f"Speaker {s['letter']}")
    summary = lambda: SV.summary(conn, row(conn, "SELECT s.*, r.source, r.started_at FROM sessions s JOIN recordings r "
                                                 "ON r.id = s.recording_id WHERE s.id=?", tv))
    assert summary()["maybe_aside"] is True
    AS.put_back(conn, tv)
    assert summary()["maybe_aside"] is False


def test_undo_leaves_no_answer_behind(lab):
    conn = lab["conn"]
    tv, talk = _two(lab)
    AS.mark(conn, tv, "tv")
    assert AS.unanswer(conn, tv) is True
    AS.put_back(conn, talk)
    assert AS.unanswer(conn, talk) is False
    assert rows(conn, "SELECT status, aside FROM sessions WHERE id IN (?, ?)", tv, talk) == [
        {"status": "active", "aside": None}, {"status": "active", "aside": None}]
