"""B38, goal 2: who has solved this, in the owner's words (octo/solved.py). On the focus or a coach note's *work on*;
the coach and Talk it through read it as the owner's account, and only when there is one; the review never does."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import coach as CO
from octo import focus as F
from octo import insight as IN
from octo import solved as SO
from octo import talk as TK
from octo.api import make_router
from octo.db import connect, row, rows
from octo.pipeline import wanted
from octo.stages import STAGES
from tests.test_marks import PINNED, conn as conn, start_of  # noqa: F401 - the fixture
from tests.test_commitments import make


@pytest.fixture
def demo(tmp_path, monkeypatch):
    from tests import demo_lab as DL
    monkeypatch.setattr(DL, "ROOT", tmp_path)
    monkeypatch.setattr(DL, "DEMO", tmp_path / "data" / "demo")
    monkeypatch.setattr(DL, "DB", tmp_path / "data" / "demo" / "octo.db")
    DL.build()
    return connect(DL.DB)


def test_kept_cleared_checked_and_deleted_with_what_its_on(tmp_path):
    conn = connect(tmp_path / "s.db")
    f = F.start(conn, "Say the news first")
    assert SO.put(conn, " Leela ", "News the same  day.", "", focus_id=f["id"]) == \
        {"who": "Leela", "what": "News the same day.", "source": None, "updated_at": pytest.approx(SO.get(conn, focus_id=f["id"])["updated_at"])}
    assert F.summary(conn)["active"]["solved"]["who"] == "Leela"
    with pytest.raises(SO.SolvedError):
        SO.put(conn, "Leela", "", focus_id=f["id"])  # who without what
    with pytest.raises(SO.SolvedError):
        SO.put(conn, "x" * (SO.MAX_WHO + 1), "y", focus_id=f["id"])
    with pytest.raises(SO.SolvedError):
        SO.put(conn, "a", "b")  # on nothing
    assert SO.put(conn, "", "", focus_id=f["id"]) is None and SO.get(conn, focus_id=f["id"]) is None  # cleared

    SO.put(conn, "Leela", "News first.", focus_id=f["id"])
    conn.execute("INSERT INTO coach_notes (id, created_at, status, model) VALUES ('cn', 0, 'done', 'm')")
    SO.put(conn, "A talk", "Ask before answering.", "Some talk", note_id="cn")
    conn.execute("DELETE FROM coach_notes WHERE id='cn'")
    conn.execute("DELETE FROM focus WHERE id=?", (f["id"],))
    assert rows(conn, "SELECT * FROM solved") == []


def test_the_coach_reads_it_only_when_there_is_one_and_keeps_its_line_as_the_owners_account(demo):
    m = CO.material(demo)
    assert m["solved"]["who"] == "Leela, my first manager" and m["solved"]["on"] == "focus"
    prompt = CO.user_prompt(m)
    assert "Who has solved this" in prompt and "What would Leela, my first manager do here?" in prompt
    assert "Add nothing you know or guess" in prompt and "never positioning or winning the room" in prompt
    assert CO.schema(m)["required"][-1] == "would"
    assert CO.system_prompt(m["me"], m.get("owner")).rstrip().endswith(IN.MATERIAL.rstrip())
    latest = CO.summary(demo)["latest"]["note"]
    assert latest["would"]["who"] == "Leela, my first manager" and latest["would"]["text"].startswith("From what you wrote")

    SO.put(demo, "", "", focus_id="foc_demo")  # cleared: the next note asks nothing about anyone
    m = CO.material(demo)
    assert m["solved"] is None and "Who has solved this" not in CO.user_prompt(m) and CO.schema(m) is CO.SCHEMA
    assert CO.check({"work_on": {}, "would": "made up"}, m)["would"] is None  # no note of the owner's, no line

    note = row(demo, "SELECT id FROM coach_notes WHERE status='done' ORDER BY created_at DESC LIMIT 1")["id"]
    SO.put(demo, "A book", "Say what you know today.", "Its title", note_id=note)  # on the last note's work on
    assert CO.material(demo)["solved"]["on"] == "work_on"


def test_talk_it_through_reads_the_focus_of_its_time(demo):
    sid = CO.reviews(demo)[0]["session"]["id"]  # Asha, today: after the focus began
    ls, sp = TK.lines(demo, sid)
    rubric = IN.principles(open(IN.RUBRIC).read())
    prompt = TK.user_prompt(demo, sid, "Sam", {}, rubric, ls, sp, [{"by": "me", "text": "How could I?"}], TK._review(demo, sid))
    assert "Who has solved this, as Sam noted it: Leela, my first manager, in Sam's words" in prompt
    assert "never what Leela, my first manager actually thinks" in prompt
    assert TK.solved_text(demo, 0.0, "Sam", "their") == ""  # before the focus began: nothing
    SO.put(demo, "", "", focus_id="foc_demo")
    assert "Who has solved this" not in TK.user_prompt(demo, sid, "Sam", {}, rubric, ls, sp, [], TK._review(demo, sid))


def test_the_review_never_reads_it(conn):  # noqa: F811
    sid = make(conn)
    f = F.start(conn, "Say the news first", now=start_of(conn, sid) - 60)
    SO.put(conn, "Leela", "News the same day.", focus_id=f["id"])
    conn.execute("INSERT INTO coach_notes (id, created_at, status, model) VALUES ('cn', 0, 'done', 'm')")
    SO.put(conn, "A book", "Ask first.", note_id="cn")
    params, h = wanted(conn, STAGES["insight"], sid, ["dz_1"])
    assert h == PINNED and "Leela" not in str(params)


def test_the_routes(tmp_path):
    db = tmp_path / "r.db"
    conn = connect(db)
    f = F.start(conn, "Say the news first")
    app = FastAPI()
    app.include_router(make_router(lambda: connect(db), coach=lambda nid: None))
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    r = c.put(f"/api/octo/focus/{f['id']}/solved", json={"who": "Leela", "what": "News first.", "source": "A talk"})
    assert r.status_code == 200 and r.json()["active"]["solved"]["source"] == "A talk"
    assert c.put("/api/octo/focus/nope/solved", json={"who": "a", "what": "b"}).status_code == 404
    assert c.put(f"/api/octo/focus/{f['id']}/solved", json={"who": "a"}).status_code == 422
    assert c.put("/api/octo/coach/nope/solved", json={"who": "a", "what": "b"}).status_code == 404
