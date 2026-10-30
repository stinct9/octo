"""A page per person, before you talk (octo/person.py, docs/coach-plan.md §4.4): the conversations with
them, open steps from those reviews, what the reviews read in them (never what the owner said is wrong), their
numbers with them against everyone, the focus, and where the coach saw its theme with them."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import claude_code as CC
from octo import coach as CO
from octo import person as PE
from octo import sessions as SV
from octo.api import make_router
from octo.db import connect

from test_coach import DAY, T0, _conv, _lab, _three, fake_ask, mark


def test_the_page_for_someone_reads_what_is_stored(tmp_path):
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)  # Kiran on 2 and 3 Oct (and 28 Sep, and a short one on 5 Oct); Tara on 4 Oct
    s4, s5 = "ses_20261002_00000004", "ses_20261002_00000005"
    mark(conn, s1, "r0", verdict="wrong")
    mark(conn, s2, "r0", verdict="partly", note="He raised it early, actually")
    mark(conn, s2, "n0", acted=1)
    p = PE.page(conn, "p_han")
    assert p["name"] == "Kiran" and not p["self"] and p["count"] == 4
    assert [c["session_id"] for c in p["conversations"]] == [s5, s2, s1, s4]  # newest first
    c = p["conversations"][1]
    assert c["summary"] == "Conversation 2 about the supplier dates." and c["one_thing"] == "You answered before Kiran finished (2)"
    assert c["minutes"] == 10 and c["others"] == [] and p["conversations"][0]["summary"] == ""  # too short for a review
    # What the reviews read in them: not the one the owner said is wrong; partly right says so.
    assert [(r["session_id"], r["partly"]) for r in p["readings"]] == [(s2, True), (s4, False)]
    assert p["readings"][0]["note"] == "He raised it early, actually" and p["readings"][0]["quote"]["text"].startswith("I'm not sure")
    # Open steps from his conversations only; the one done is off.
    assert sorted(t["session_id"] for t in p["steps"]) == sorted([s1, s4])
    n = p["numbers"]
    assert n["talk_share"]["with"]["value"] == 0.3 and n["questions"]["with"]["conversations"] == 3  # not 28 Sep: before the lab
    assert n["questions"]["everyone"]["conversations"] == 4 and n["fillers"]["with"] is None
    assert [e["session_id"] for e in n["each"]] == [s1, s2, s5] and n["each"][0]["questions"] == 6.0
    assert p["focus"] is None and p["coach"] is None
    assert PE.page(conn, "p_nobody") is None


def test_a_reading_made_before_a_rename_is_still_theirs(tmp_path):
    """The review of 4 Oct calls B "Kiran", but B is Tara: her quotes are all B's lines."""
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)
    p = PE.page(conn, "p_pooja")
    assert [c["session_id"] for c in p["conversations"]] == [s3]
    assert [r["session_id"] for r in p["readings"]] == [s3]


def test_where_the_coach_saw_its_theme_with_them(tmp_path, monkeypatch):
    monkeypatch.setattr(CC, "claude_bin", lambda: "/usr/local/bin/claude")
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)
    nid = CO.start(conn)
    CO.run(conn, nid, ask=fake_ask([]))  # work on: c1.o (Tara, 4 Oct) and c3.p7 (Kiran, 2 Oct)
    got = PE.page(conn, "p_han")["coach"]
    assert got["note_id"] == nid and got["moments"] == 1 and got["theme"].startswith("You answer before")
    assert PE.page(conn, "p_pooja")["coach"]["moments"] == 1
    _conv(conn, 9, T0 + 5 * DAY, other="p_pooja")
    assert PE.page(conn, "p_pooja")["count"] == 2


def test_speakers_carry_their_person_and_the_route(tmp_path):
    conn = _lab(tmp_path)
    s1, _, _ = _three(conn)
    assert {sp["letter"]: sp["person_id"] for sp in SV.speakers(conn, s1)} == {"A": "p_jay", "B": "p_han"}
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    people = c.get("/api/octo/people").json()
    assert {p["name"]: p["id"] for p in people}["Kiran"] == "p_han"
    assert c.get("/api/octo/people/p_han").json()["count"] == 4
    assert c.get("/api/octo/people/nope").status_code == 404


def test_listening_with_them_against_everyone(tmp_path):
    from test_delivery import DAY as D1, T0 as T1, _conv as conv, _lab as lab, _talk

    conn = lab(tmp_path)
    ask = "Shall we start then, and what did you make of the plan we sent round on Monday?"  # 17 words, 6.7 s
    for i in range(3):  # with Asha: six waits of 1.2 s
        conv(conn, i, T1 + i * D1, [("B", ask), ("A", _talk(100)), ("B", ask), ("A", _talk(100))])
    conv(conn, 5, T1 + 5 * D1, [("B", ask + " " + ask), ("A", _talk(200))], who={"A": "p_jay", "B": "p_ravi"})
    n = PE.page(conn, "p_asha")["numbers"]
    assert n["patience"]["with"]["value"] == 1.2 and n["patience"]["waits_with"] == 6
    assert n["their_longest"]["with"]["value"] == 6.7 and n["their_longest"]["everyone"]["value"] == 6.7
    assert n["their_longest"]["everyone"]["conversations"] == 4
    r = PE.page(conn, "p_ravi")["numbers"]
    assert r["patience"]["with"] is None and r["patience"]["waits_with"] == 1  # under MIN_WAITS
    assert r["their_longest"]["with"]["value"] == 13.5
