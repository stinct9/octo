"""The calendar from above (B37, octo/month.py) on a made-up lab: a month's days with counts, minutes, reviews still to
answer and people; set-aside and deleted conversations not counted; a day as one timeline with its calendar events."""

import json
from datetime import date

import pytest

from octo import month as MO
from octo.db import connect

from test_coach import DAY, T0, _conv, _lab, _three


@pytest.fixture
def conn(tmp_path):
    c = _lab(tmp_path)
    _three(c)  # 2, 3 and 4 Oct (the last with Tara), 28 Sep, and a short one on 5 Oct with no review
    return c


def test_a_month_counts_each_day_and_leaves_out_what_isnt_a_conversation(conn):
    conn.execute("UPDATE sessions SET reviewed_at=1 WHERE id='ses_20261002_00000001'")
    _conv(conn, 7, T0 + 3600)  # a second on 2 Oct
    conn.execute("UPDATE sessions SET status='set_aside', aside='tv' WHERE id='ses_20261002_00000007'")
    got = MO.month(conn, "2026-10", now=T0 + 10 * DAY)
    days = {d["date"]: d for d in got["days"]}
    assert len(got["days"]) == 31 and got["prev"] == "2026-09" and got["next"] == "2026-11"
    assert days["2026-10-02"] == {"date": "2026-10-02", "count": 1, "minutes": 10, "reviews": 1, "to_answer": 0, "people": ["Kiran"],
                                   "fillers": None}
    assert days["2026-10-03"]["to_answer"] == 1 and days["2026-10-04"]["people"] == ["Tara"]
    assert days["2026-10-05"]["count"] == 1 and days["2026-10-05"]["reviews"] == 0  # skipped as too short: no review
    assert got["totals"] == {"conversations": 4, "minutes": 40, "reviews": 3, "answered": 1, "people": 2, "fillers": None}
    assert got["first"] == "2026-09-28" and got["today"] == date.fromtimestamp(T0 + 10 * DAY).isoformat()
    with pytest.raises(MO.MonthError):
        MO.month(conn, "2026-13")


def test_a_day_is_one_timeline_of_its_conversations_and_calendar_events(conn):
    at = T0 + DAY  # 3 Oct, 10:00
    conn.execute("INSERT INTO events (id, source, title, start, end, invitees, seen_at) VALUES ('e1', 'mac', 'Supplier sync', ?, ?, ?, 0)",
                 (at - 600, at + 1200, json.dumps([{"name": "Kiran"}])))
    conn.execute("INSERT INTO events (id, source, title, start, end, invitees, seen_at) VALUES ('e2', 'mac', 'Lunch', ?, ?, '[]', 0)",
                 (at + 3 * 3600, at + 4 * 3600))
    got = MO.day(conn, "2026-10-03", now=at)
    assert [(i["kind"], i.get("title") or i["id"]) for i in got["items"]] == \
        [("event", "Supplier sync"), ("conversation", "ses_20261002_00000002"), ("event", "Lunch")]
    sync, conv, lunch = got["items"]
    assert sync["conversation"] == conv["id"] and lunch["conversation"] is None
    assert conv["todo"] and conv["one_thing"] is not None and conv["speakers"]
    assert got["prev"] == "2026-10-02" and got["next"] == "2026-10-04" and got["events_kept"]
    assert MO.day(conn, "2026-10-20", now=at)["items"] == []
    assert MO.day(conn, now=at)["date"] == "2026-10-03"  # no day asked: today
    with pytest.raises(MO.MonthError):
        MO.day(conn, "yesterday")


def test_fillers_are_pooled_per_day_and_per_conversation_from_those_on_the_line(conn, monkeypatch):
    rec = lambda sid, w, f, on=True: {"session_id": sid, "words": w, "fillers": f, "on_line": on}
    monkeypatch.setattr(MO.FI, "all_conversations", lambda c: [
        rec("ses_20261002_00000001", 400, 8), rec("ses_20261002_00000002", 200, 10),
        rec("ses_20261002_00000003", 900, 90, on=False)])  # off the line (say, Hindi): never counted
    _conv(conn, 7, T0 + 3600)  # a second on 2 Oct, not on the line
    days = {d["date"]: d for d in MO.month(conn, "2026-10", now=T0 + 10 * DAY)["days"]}
    assert days["2026-10-02"]["fillers"] == {"words": 400, "fillers": 8, "rate": 2.0}
    assert days["2026-10-04"]["fillers"] is None
    got = MO.month(conn, "2026-10", now=T0 + 10 * DAY)["totals"]["fillers"]
    assert got == {"words": 600, "fillers": 18, "rate": 3.0}
    day = MO.day(conn, "2026-10-02", now=T0)
    assert [i["fillers"] for i in day["items"] if i["kind"] == "conversation"] == [{"words": 400, "fillers": 8, "rate": 2.0}, None]
    assert day["totals"]["fillers"] == {"words": 400, "fillers": 8, "rate": 2.0}


def test_routes_answer_for_the_phone_and_the_mac(tmp_path):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router
    c = _lab(tmp_path)
    _three(c)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=SimpleNamespace(kick=lambda: None)))
    client = TestClient(app)
    assert client.get("/api/octo/calendar/month", params={"month": "2026-10"}).json()["totals"]["conversations"] == 4
    assert client.get("/api/octo/calendar/month", params={"month": "bad"}).status_code == 422
    assert client.get("/api/octo/calendar/day", params={"date": "2026-10-02"}).json()["totals"]["conversations"] == 1
