"""Your week and "Octo hasn't heard from…" (B17, octo/week.py): the Monday card about the week before, in local time;
the quiet rule (≥ 3 days with them, twice the usual gap); the mute; the notification with no names; next Monday's
card replacing this one."""

import json
import time
from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import notify as N
from octo import week as W
from octo.api import make_router
from octo.db import connect

from test_coach import _conv, _lab, mark


@pytest.fixture(autouse=True)
def bengaluru(monkeypatch):
    """Local time is the lab's: IST, where a Monday starts 5½ hours before UTC's."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def at(*a) -> float:
    return datetime(*a).timestamp()


def test_the_card_covers_the_week_before_in_local_time(tmp_path):
    conn = _lab(tmp_path)
    s1 = _conv(conn, 1, at(2026, 10, 5, 0, 1))      # Monday 5 Oct, just after midnight: in
    s2 = _conv(conn, 2, at(2026, 10, 11, 23, 59))   # Sunday 11 Oct, last minute: in
    _conv(conn, 3, at(2026, 10, 12, 0, 1))          # Monday 12 Oct (Sunday in UTC): next week's
    _conv(conn, 4, at(2026, 10, 4, 23, 59))         # the Sunday before
    conn.execute("UPDATE sessions SET reviewed_at=? WHERE id IN (?, ?)", (at(2026, 10, 6, 9), s1, s2))
    mark(conn, s1, "n0", acted=1)
    conn.execute("UPDATE feedback SET updated_at=?", (at(2026, 10, 7, 9),))
    now = at(2026, 10, 14, 12)  # Wednesday: home still shows the week of 5 Oct
    got = W.current(conn, now)
    assert got["week"] == "2026-10-05" and got["current"]
    assert got["counts"] == {"heard": 2, "reviewed": 2, "acted": 1}
    assert got["coach"] is None and got["focus"] is None and got["commitments"] == {"mine": 0, "theirs": 0, "due": []}
    # An earlier week on Progress: counts, no what's-open-now.
    old = W.card(conn, date(2026, 9, 28), now)
    assert old["counts"]["heard"] == 1 and not old["current"] and "quiet" not in old


def test_dismissed_until_the_next_monday_replaces_it(tmp_path):
    conn = _lab(tmp_path)
    _conv(conn, 1, at(2026, 10, 7, 10))
    assert W.current(conn, at(2026, 10, 4, 10)) is None  # nothing heard before that Monday: no card
    assert W.ready(conn, at(2026, 10, 12, 9)) == "2026-10-05"
    W.dismiss(conn, "2026-10-05")
    assert W.current(conn, at(2026, 10, 13, 9)) is None
    nxt = W.current(conn, at(2026, 10, 19, 0, 30))  # next Monday, local midnight past
    assert nxt["week"] == "2026-10-12" and nxt["counts"]["heard"] == 0  # a quiet week still says so


def test_the_notification_is_monday_morning_once_and_has_no_names(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "STATE", tmp_path / "notify.json")
    import octo.device as D
    monkeypatch.setattr(D, "load_config", lambda: None)
    conn = _lab(tmp_path)
    _conv(conn, 1, at(2026, 10, 7, 10))
    sent = []
    assert W.announce(conn, at(2026, 10, 12, 7, 59)) is None  # Monday, before the morning
    assert "week:2026-10-05" not in N.waiting(conn, at(2026, 10, 12, 7, 59))
    assert N.check(conn, send=sent.append, now=at(2026, 10, 12, 8, 1)) == "Your week is ready · 1 conversation ready to review"
    assert N.check(conn, send=sent.append, now=at(2026, 10, 12, 8, 6)) is None  # once
    assert N.message({"week:2026-10-05"}) == "Your week is ready"
    assert not any(n in sent[0] for n in ("Sam", "Kiran", "Tara"))
    assert {"text": "Your week is ready", "page": "/", "needs": True} in N.menu({"week:2026-10-05"})
    W.dismiss(conn, "2026-10-05")
    assert not any(k.startswith("week:") for k in N.waiting(conn, at(2026, 10, 13, 9)))


def test_hasnt_heard_needs_three_days_and_twice_the_usual_gap(tmp_path):
    conn = _lab(tmp_path)
    _conv(conn, 1, at(2026, 10, 1, 10))
    _conv(conn, 2, at(2026, 10, 8, 10))
    assert W.heard(conn, "p_han", "Kiran", at(2026, 11, 1, 10)) is None  # two days with them: no usual yet
    _conv(conn, 3, at(2026, 10, 15, 10))
    _conv(conn, 4, at(2026, 10, 15, 16))  # the same day twice is one day, not a gap of none
    h = W.heard(conn, "p_han", "Kiran", at(2026, 10, 28, 10))
    assert h["usual_days"] == 7 and h["since_days"] == 13 and h["text"] is None  # under twice
    h = W.heard(conn, "p_han", "Kiran", at(2026, 11, 5, 10))
    assert h["text"] == "Octo hasn't heard a conversation with Kiran in 3 weeks; usually about weekly"
    assert [q["person_id"] for q in W.quiet(conn, at(2026, 11, 5, 10))] == ["p_han"]
    assert W._often(1) == "about daily" and W._often(3) == "about every 3 days" and W._often(14) == "about every 2 weeks"
    assert W._ago(1) == "1 day" and W._ago(9) == "9 days" and W._ago(70) == "2 months"


def test_muted_people_are_left_off_the_card(tmp_path):
    conn = _lab(tmp_path)
    for n, d in enumerate((1, 8, 15), 1):
        _conv(conn, n, at(2026, 10, d, 10))
    now = at(2026, 11, 3, 10)  # Tuesday
    assert [q["name"] for q in W.current(conn, now)["quiet"]] == ["Kiran"]
    W.mute(conn, "p_han", True)
    assert W.current(conn, now)["quiet"] == [] and W.heard(conn, "p_han", "Kiran", now)["muted"]
    W.mute(conn, "p_han", False)
    assert W.quiet(conn, now)[0]["name"] == "Kiran"


def test_open_and_due_commitments_and_the_coach_theme(tmp_path):
    conn = _lab(tmp_path)
    sid = _conv(conn, 1, at(2026, 10, 7, 10))
    for i, (mine, due, state) in enumerate(((1, "2026-10-14", None), (1, "2026-10-30", None), (0, None, None), (0, "2026-10-10", "did"))):
        conn.execute("INSERT INTO commitments (id, session_id, run_id, by_self, by_person, to_person, by_name, to_name, what, line, "
                     "quote, start_s, end_s, due, state, created_at) VALUES (?, ?, 'r', ?, ?, ?, 'x', 'y', ?, 0, 'q', 0, 1, ?, ?, 0)",
                     (f"com_{i}", sid, mine, None if mine else "p_han", "p_han" if mine else None, f"w{i}", due, state))
    conn.execute("INSERT INTO coach_notes (id, created_at, status, model, output) VALUES ('n1', 1, 'done', 'm', ?)",
                 (json.dumps({"work_on": {"theme": "You answer early"}}),))
    got = W.current(conn, at(2026, 10, 13, 9))
    assert got["commitments"]["mine"] == 2 and got["commitments"]["theirs"] == 1
    assert [c["what"] for c in got["commitments"]["due"]] == ["w0"]  # by this Sunday (18 Oct), still open
    assert got["coach"] == {"note_id": "n1", "at": 1, "theme": "You answer early"}


def test_the_focus_number_for_the_week(tmp_path):
    from octo import focus as F
    conn = _lab(tmp_path)
    _conv(conn, 1, at(2026, 10, 2, 10))
    _conv(conn, 2, at(2026, 10, 6, 10))
    F.start(conn, "Ask more", now=at(2026, 10, 3), measure="questions")
    f = W.current(conn, at(2026, 10, 12, 9))["focus"]
    assert f["text"] == "Ask more" and f["week"]["conversations"] == 1 and f["usual"]["conversations"] == 1


def test_routes(tmp_path):
    conn = _lab(tmp_path)
    for n, d in enumerate((1, 8, 15), 1):
        _conv(conn, n, at(2026, 10, d, 10))
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    assert c.get("/api/octo/week").json()["current"]  # today's card, whatever today is
    assert c.get("/api/octo/week?week=2026-10-12").json()["counts"]["heard"] == 1
    assert c.get("/api/octo/week?week=2026-10-13").status_code == 422  # a Tuesday
    week = c.get("/api/octo/week").json()["week"]
    assert c.post("/api/octo/week/dismiss", json={"week": week}).json() == {"ok": True}
    assert c.get("/api/octo/week").json() is None
    assert c.post("/api/octo/week/dismiss", json={"week": "soon"}).status_code == 422
    q = c.get("/api/octo/people/p_han/quiet").json()
    assert q["usual_days"] == 7 and not q["muted"]
    assert c.post("/api/octo/people/p_han/quiet", json={"muted": True}).json()["muted"]
    assert c.get("/api/octo/people/p_jay/quiet").json()["text"] is None
    assert c.post("/api/octo/people/p_jay/quiet", json={"muted": True}).status_code == 404  # never the owner
    assert c.get("/api/octo/people/nope/quiet").status_code == 404
