"""B23: the calendar (octo/events.py). Calendar links read and kept like a token; events from either source in one
table; a banner before you talk with the event's name and time only; invitees offered as speakers, a match kept once
the owner names someone with it, and gone when that person is forgotten."""

import json
import subprocess
from datetime import datetime, timezone

import pytest

from octo import api as API
from octo import events as EV
from octo import notify as N
from octo import voice as V
from octo.db import connect, row, rows
from tests.test_commitments import make

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc).timestamp()
ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:one@x\r
DTSTART;TZID=Asia/Kolkata:20261027T150000\r
DTEND;TZID=Asia/Kolkata:20261027T153000\r
SUMMARY:1:1 with Priya\\, budget\r
ORGANIZER;CN=Sam:mailto:sam@x.com\r
ATTENDEE;CN="Priya Sharma";PARTSTAT=ACCEPTED:mailto:Priya.Sharma@x.com\r
ATTENDEE;CN=Ravi;PARTSTAT=DECLINED:mailto:ravi@x.com\r
ATTENDEE;CUTYPE=ROOM;CN=Room 4:mailto:room4@x.com\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:allday@x\r
DTSTART;VALUE=DATE:20261027\r
SUMMARY:Holiday\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:standup@x\r
DTSTART:20261020T043000Z\r
DURATION:PT15M\r
RRULE:FREQ=DAILY\r
EXDATE:20261028T043000Z\r
SUMMARY:Standup\r
ATTENDEE;CN=Asha:mailto:asha@x.com\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:standup@x\r
RECURRENCE-ID:20261027T043000Z\r
DTSTART:20261027T050000Z\r
DTEND:20261027T051500Z\r
SUMMARY:Standup (moved)\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:gone@x\r
DTSTART:20261027T100000Z\r
DTEND:20261027T110000Z\r
STATUS:CANCELLED\r
SUMMARY:Cancelled\r
END:VEVENT\r
END:VCALENDAR\r
"""
URL = "https://calendar.google.com/calendar/ical/sam%40x.com/private-0123456789abcdef/basic.ics"


@pytest.fixture(autouse=True)
def own_calendar(tmp_path, monkeypatch):
    monkeypatch.setattr(EV, "CONFIG", tmp_path / "calendar.json")
    monkeypatch.setattr(EV, "_status", {})
    monkeypatch.setattr(EV, "mac_status", {})


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "e.db")


def test_a_calendar_link_is_read_with_its_invitees_repeats_and_changes():
    evs = EV.parse_ics(ICS, NOW - EV.WINDOW_S, NOW + EV.WINDOW_S)
    one = next(e for e in evs if e["uid"] == "one@x")
    assert one["title"] == "1:1 with Priya, budget" and one["end"] - one["start"] == 1800
    assert datetime.fromtimestamp(one["start"], timezone.utc).hour == 9 and one["start"] == NOW + 30 * 60  # 15:00 IST
    # The organiser counts; who declined and the room don't.
    assert one["invitees"] == [{"name": "Sam", "email": "sam@x.com"}, {"name": "Priya Sharma", "email": "priya.sharma@x.com"}]
    stand = sorted((e["start"], e["title"]) for e in evs if e["uid"] == "standup@x")
    days = [datetime.fromtimestamp(s, timezone.utc).strftime("%d %H:%M") for s, _ in stand]
    # 25 Oct 09:00 to 29 Oct 09:00 is the window; the 28th excluded, the 27th moved to 05:00.
    assert days == ["26 04:30", "27 05:00", "29 04:30"]
    assert ("Standup (moved)" in dict(stand).values()) and all(e["title"] != "Holiday" and e["title"] != "Cancelled" for e in evs)


def test_storing_keeps_what_was_announced_drops_what_the_source_no_longer_has(conn):
    lo, hi = NOW - EV.WINDOW_S, NOW + EV.WINDOW_S
    ev = {"uid": "a", "title": "Sync", "start": NOW + 300, "end": NOW + 900, "invitees": []}
    old = {"uid": "b", "title": "Long ago", "start": NOW - 5 * 86400, "end": NOW - 5 * 86400 + 600, "invitees": []}
    EV.store(conn, "ics:l1", [ev, {**ev, "uid": "c"}], lo, hi, NOW)
    conn.execute("INSERT INTO events (id, source, title, start, end, seen_at) VALUES ('old', 'ics:l1', 'x', ?, ?, 0)",
                 (old["start"], old["end"]))
    conn.execute("UPDATE events SET announced_at=1 WHERE title='Sync'")
    EV.store(conn, "ics:l1", [{**ev, "title": "Sync (renamed)"}], lo, hi, NOW + 60)
    got = rows(conn, "SELECT id, title, announced_at FROM events ORDER BY title")
    assert [(r["title"], r["announced_at"]) for r in got] == [("Sync (renamed)", 1), ("x", None)]  # c gone; outside kept


def test_a_link_is_kept_like_a_token_and_never_sent_back(conn):
    with pytest.raises(EV.CalendarError):
        EV.add_link(conn, "http://example.com/a.ics", fetch=lambda u: ICS)
    with pytest.raises(EV.CalendarError):
        EV.add_link(conn, "https://example.com/a.ics", fetch=lambda u: "<html>")
    s = EV.add_link(conn, URL.replace("https://", "webcal://"), fetch=lambda u: ICS, now=NOW)
    assert oct(EV.CONFIG.stat().st_mode & 0o777) == "0o600" and json.loads(EV.CONFIG.read_text())["links"][0]["url"] == URL
    assert URL not in json.dumps(s) and "private-0123" not in json.dumps(s)
    assert s["links"][0]["host"] == "calendar.google.com" and s["links"][0]["ends"] == ".ics" and s["links"][0]["events"] == 4
    with pytest.raises(EV.CalendarError):
        EV.add_link(conn, URL, fetch=lambda u: ICS)  # the same link twice
    EV.remove_link(conn, s["links"][0]["id"])
    assert EV.load()["links"] == [] and rows(conn, "SELECT id FROM events") == []


def test_a_failed_fetch_keeps_the_events_and_says_why(conn):
    link = EV.add_link(conn, URL, fetch=lambda u: ICS, now=NOW)["links"][0]

    def down(u):
        raise EV.CalendarError("The calendar answered 404: the link may have been reset")
    EV.sync_links(conn, fetch=down, now=NOW + 60)  # not due yet
    assert EV.settings()["links"][0]["error"] is None
    EV.sync_links(conn, fetch=down, now=NOW + EV.FETCH_EVERY_S + 1)
    assert "404" in EV.settings()["links"][0]["error"] and len(rows(conn, "SELECT id FROM events")) == link["events"]


def test_the_macs_calendars_only_when_the_owner_turned_them_on(conn):
    body = {"access": "full", "events": [{"id": "E1", "title": "Weekly", "start": NOW + 3600, "end": NOW + 5400,
                                          "invitees": [{"name": "Priya", "email": "PRIYA@x.com"}, "junk"]}, {"title": "no times"}]}
    assert EV.from_mac(conn, body, NOW) == {"wanted": False} and rows(conn, "SELECT id FROM events") == []
    EV.set_mac(conn, True)
    EV.from_mac(conn, body, NOW)
    ev = row(conn, "SELECT * FROM events")
    assert ev["source"] == "mac" and json.loads(ev["invitees"]) == [{"name": "Priya", "email": "priya@x.com"}]
    EV.from_mac(conn, {"access": "denied"}, NOW + 60)  # macOS said no: what was read stays until turned off
    assert EV.settings()["mac_access"] == "denied" and row(conn, "SELECT id FROM events")
    EV.set_mac(conn, False)
    assert rows(conn, "SELECT id FROM events") == [] and EV.settings()["mac_access"] is None


def test_invitees_are_matched_fuzzily_the_owner_left_out_and_a_kept_match_wins(conn):
    make(conn)  # Sam (the owner) and Asha
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('per_priya', 'Priya', 0, 0), "
                 "('per_ps', 'Priya Sharma', 0, 0), ('per_ravi', 'Ravi', 0, 0), ('per_rk', 'Ravi K', 0, 0)")
    got = EV.who(conn, [{"name": "Sam", "email": "sam@x.com"}, {"name": "", "email": "priya.sharma@x.com"},
                        {"name": "Ashaa", "email": ""}, {"name": "Ravi Kumar", "email": "rk@x.com"},
                        {"name": "Lena O'Brien (Ext)", "email": "lena@y.com"}])
    assert [(g["key"], g["person"], g["name"], g["sure"]) for g in got] == [
        ("priya.sharma@x.com", "Priya Sharma", "Priya Sharma", False),  # the fullest fit
        ("name:ashaa", "Asha", "Asha", False),  # near enough
        ("rk@x.com", None, "Ravi Kumar", False),  # Ravi and Ravi K fit as well as each other: nobody
        ("lena@y.com", None, "Lena O'Brien Ext", False)]  # nobody known: a name to offer
    EV.keep_match(conn, "rk@x.com", "per_rk")
    assert EV.who(conn, [{"name": "Ravi Kumar", "email": "rk@x.com"}])[0]["person"] == "Ravi K"
    assert EV.who(conn, [{"name": "Ravi Kumar", "email": "rk@x.com"}])[0]["sure"]
    V.forget(conn, "Ravi K")
    assert rows(conn, "SELECT * FROM invitee_matches") == []


def test_before_you_talk_the_banner_is_the_events_name_and_time_only(conn):
    make(conn)
    conn.execute("INSERT INTO intentions (id, person_id, text, created_at) VALUES ('int_1', 'per_asha', 'Ask about her son', 0)")
    EV.store(conn, "mac", [
        {"uid": "a", "title": "Catch-up", "start": NOW + 590, "end": NOW + 1800, "invitees": [{"name": "Asha", "email": ""}]},
        {"uid": "b", "title": "Dentist", "start": NOW + 300, "end": NOW + 1800, "invitees": []},  # nobody known
        {"uid": "c", "title": "Later", "start": NOW + 3600, "end": NOW + 4000, "invitees": [{"name": "Asha", "email": ""}]},
        {"uid": "d", "title": "Just me", "start": NOW + 300, "end": NOW + 900, "invitees": [{"name": "Sam", "email": ""}]},
    ], NOW - EV.WINDOW_S, NOW + EV.WINDOW_S, NOW)
    sent = []
    assert EV.tick(conn, send=lambda text, page: sent.append((text, page)), now=NOW) == ["Catch-up in 10 min"]
    assert sent == [("Catch-up in 10 min", "/people/per_asha")]  # nothing of hers but where the click goes
    assert EV.tick(conn, send=lambda text, page: sent.append((text, page)), now=NOW + 60) == []  # once
    assert EV.tick(conn, send=lambda text, page: sent.append((text, page)), now=NOW + 3600 - 600)[0] == "Later in 10 min"


def test_every_other_banner_stays_counts_only(conn):
    """The calendar's banner is notify's one exception (its docstring says so); its own texts never name anyone."""
    assert "One exception (B23" in N.__doc__
    text = N.message({"review:ses_20261019_00000001", "confirm:ses_x", "failed:ses_y", "coach:n1", "week:2026-10-26"}, NOW)
    assert text == "Your week is ready · 1 conversation ready to review · 1 with speakers to confirm · 1 failed: press Try again" \
                   " · the coach's note is ready"


def test_the_apps_banner_carries_the_page_to_open(monkeypatch):
    for name, value in (("_app_texts", []), ("_app_seq", 0), ("_app_got", 0), ("_app_waiting", 1), ("_app_left", 0.0),
                        ("_app_mode", "on")):
        monkeypatch.setattr(N, name, value)
    N.post("Catch-up in 10 min", late=lambda *a: None, page="/people/per_asha")
    N.post("1 failed: press Try again", late=lambda *a: None)
    assert N.app_wait("on", 0, timeout=0) == (1, "Catch-up in 10 min") and N.app_page(1) == "/people/per_asha"
    assert N.app_page(2) is None


def test_a_conversation_offers_its_events_invitees_and_naming_one_keeps_the_match(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router
    conn = connect(tmp_path / "t.db")
    sid = make(conn)  # an hour ago, 70 s
    at = row(conn, "SELECT started_at FROM recordings")["started_at"]
    conn.execute("UPDATE session_speakers SET person_id=NULL, state='unknown' WHERE speaker='B'")
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('per_priya', 'Priya', 0, 0)")
    EV.store(conn, "mac", [{"uid": "a", "title": "1:1 with Priya", "start": at - 60, "end": at + 1800,
                            "invitees": [{"name": "Priya S", "email": "ps@x.com"}, {"name": "Sam", "email": "sam@x.com"}]},
                           {"uid": "b", "title": "Elsewhere", "start": at + 3600, "end": at + 4000,
                            "invitees": [{"name": "Nobody", "email": "n@x.com"}]}], at - 86400, at + 86400, at)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    monkeypatch.setattr(API.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    cal = c.get(f"/api/octo/sessions/{sid}").json()["calendar"]
    assert [e["title"] for e in cal["events"]] == ["1:1 with Priya"]
    assert [(p["key"], p["person"], p["sure"]) for p in cal["invitees"]] == [("ps@x.com", "Priya", False)]
    # An invitee this conversation didn't offer keeps nothing.
    c.post(f"/api/octo/sessions/{sid}/speakers/B", json={"action": "name", "name": "Nobody", "invitee": "n@x.com"})
    assert rows(conn, "SELECT * FROM invitee_matches") == []
    r = c.post(f"/api/octo/sessions/{sid}/speakers/B", json={"action": "name", "name": "Priya", "invitee": "ps@x.com"})
    assert r.status_code == 200 and row(conn, "SELECT person_id FROM invitee_matches WHERE invitee='ps@x.com'")["person_id"] == "per_priya"
    assert c.get(f"/api/octo/sessions/{sid}").json()["calendar"]["invitees"][0]["sure"] is True
    # Settings: the link goes in, never out.
    monkeypatch.setattr(EV, "fetch_ics", lambda u, timeout=20: ICS)
    s = c.post("/api/octo/settings/calendar/links", json={"url": URL}).json()
    assert URL not in json.dumps(s) and URL not in c.get("/api/octo/settings/calendar").text
    assert c.post("/api/octo/settings/calendar/links", json={"url": "ftp://x/y.ics"}).status_code == 400
    assert c.post("/api/octo/settings/calendar/mac", json={"on": True}).json()["mac"] is True
    assert c.post("/api/octo/mac-app/calendar", json={"access": "ask"}).json() == {"wanted": True}


def test_homes_agenda_is_today_and_tomorrow_with_who_and_what_was_recorded(conn):
    sid = make(conn, at=NOW - 3600)  # Sam and Asha, 70 s from an hour before NOW
    at = NOW - 3600
    local = datetime.fromtimestamp(NOW).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    EV.store(conn, "mac", [
        {"uid": "past", "title": "1:1 with Asha", "start": at - 60, "end": at + 1800, "invitees": [{"name": "Asha", "email": "a@x.com"}]},
        {"uid": "missed", "title": "Unrecorded", "start": at + 2400, "end": at + 3000, "invitees": []},
        {"uid": "soon", "title": "Team", "start": NOW + 600, "end": NOW + 1200,
         "invitees": [{"name": "Sam", "email": "sam@x.com"}, {"name": "Lena", "email": "lena@y.com"}]},
        {"uid": "tmrw", "title": "Tomorrow's", "start": local + 86400 + 3600, "end": local + 86400 + 5400, "invitees": []},
        {"uid": "later", "title": "Day after", "start": local + 2 * 86400 + 3600, "end": local + 2 * 86400 + 5400, "invitees": []},
        {"uid": "before", "title": "Yesterday", "start": local - 7200, "end": local - 3600, "invitees": []},
    ], NOW - 3 * 86400, NOW + 3 * 86400, NOW)
    got = EV.agenda(conn, NOW)
    assert [(e["title"], e["conversation"]) for e in got] == [
        ("1:1 with Asha", sid), ("Unrecorded", None), ("Team", None), ("Tomorrow's", None)]  # not yet started: none
    who = lambda e: [{k: p[k] for k in ("name", "person_id", "prepared")} for p in e["people"]]
    assert who(got[0]) == [{"name": "Asha", "person_id": "per_asha", "prepared": False}]
    assert who(got[2]) == [{"name": "Lena", "person_id": None, "prepared": False}]  # the owner left out, a stranger by name
    assert got[2]["people"][0]["key"]  # B36: to prepare for her, she's added by this
    conn.execute("UPDATE sessions SET status='set_aside'")
    assert EV.agenda(conn, NOW)[0]["conversation"] is None


def test_a_hostile_repeating_invite_stays_cheap():
    # Security review M2, 28 Oct: FREQ=SECONDLY from months back was 345,600 instances and seconds of CPU a fetch.
    import time as T
    ics = ("BEGIN:VCALENDAR\r\n"
           "BEGIN:VEVENT\r\nUID:s@x\r\nDTSTART:20200101T000000Z\r\nDURATION:PT1S\r\nRRULE:FREQ=SECONDLY\r\nSUMMARY:x\r\nEND:VEVENT\r\n"
           "BEGIN:VEVENT\r\nUID:h@x\r\nDTSTART:19900101T000000Z\r\nDURATION:PT1M\r\nRRULE:FREQ=HOURLY\r\nSUMMARY:y\r\nEND:VEVENT\r\n"
           "END:VCALENDAR\r\n")
    t0 = T.time()
    got = EV.parse_ics(ics, NOW - EV.WINDOW_S, NOW + EV.WINDOW_S)
    assert T.time() - t0 < 5 and len(got) <= EV.MAX_REPEATS and {e["uid"] for e in got} <= {"h@x"}


def test_the_calendar_fetch_never_goes_to_this_mac_or_the_home_network(monkeypatch):
    """Security review L5: the pasted link and every redirect must be https to a public address."""
    import urllib.request
    addrs = {"cal.example.com": "93.184.215.14", "router.example.com": "192.168.1.1", "me.example.com": "127.0.0.1",
             "meta.example.com": "169.254.169.254", "v6.example.com": "::ffff:10.0.0.1", "ts.example.com": "100.101.1.2"}
    monkeypatch.setattr(EV.socket, "getaddrinfo", lambda host, *a, **k: [(0, 0, 0, "", (addrs[host], 443))])
    EV.check_host("https://cal.example.com/x.ics")
    for host in ("router", "me", "meta", "v6", "ts"):
        with pytest.raises(EV.CalendarError, match="home network"):
            EV.fetch_ics(f"https://{host}.example.com/x.ics")  # refused before anything is sent
    req = urllib.request.Request("https://cal.example.com/x.ics")
    hop = EV._Redirects().redirect_request(req, None, 302, "Found", {}, "https://cal.example.com/y.ics")
    assert hop.full_url == "https://cal.example.com/y.ics"
    for to in ("http://cal.example.com/y.ics", "https://router.example.com/", "https://me.example.com/"):
        with pytest.raises(EV.CalendarError):
            EV._Redirects().redirect_request(req, None, 302, "Found", {}, to)
