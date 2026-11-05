"""Preparing for a conversation (B36, octo/prepare.py) on a made-up lab, the model mocked: what's picked (their
conversations, and lines naming them where they weren't), an unticked mention never sent, moments checked and
second-hand marked in code, the day's count and a usage limit, the banner opening the plan, and the plan gone with a
conversation it read, with Forget, and a day after the next conversation with them."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import claude_code as CC
from octo import events as EV
from octo import pipeline as P
from octo import prepare as PP
from octo import privacy as PR
from octo import settings as ST
from octo import voice as V
from octo.api import make_router
from octo.db import connect, load_json, row

from test_coach import DAY, T0, _conv, _lab, _three
from tests.calls import through_reader

CLAUDE = {"kind": "claude-code", "model": "claude-opus-5-5", "effort": "high"}
PLAN = {"aim": "Listen first.", "why": "An introduction.",
        "thinking": [{"headline": "Others see him as careful about dates", "observation": "Tara said so.",
                      "quotes": [{"conversation": "ABSENT", "line": 3, "text": "Kiran said the flag question"}]},
                     {"headline": "He worries about the supplier", "observation": "His own words.",
                      "quotes": [{"conversation": "WITH", "line": 1, "text": "not sure the dates work"},
                                 {"conversation": "WITH", "line": 9, "text": "I never said this"}]}],
        "open": [], "structure": [{"minutes": 5, "part": "Hello", "detail": "Who you are."}],
        "questions": [{"purpose": "His view", "items": ["What matters most this quarter?", " "]}],
        "careful": [{"text": "Said in confidence", "quotes": []}], "limits": ""}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)  # s1, s2 with Kiran; s3 with Tara, who names him
    dz = load_json(row(conn, "SELECT output FROM stage_runs WHERE id=?", f"diarize_{s3}")["output"])
    dz["lines"][3]["text"] = "Kiran said the flag question is still open for me."
    conn.execute("UPDATE stage_runs SET output=? WHERE id=?", (json.dumps(dz), f"diarize_{s3}"))
    conn.execute("UPDATE recordings SET audio_deleted_at=1")  # nothing to silence on a delete
    monkeypatch.setattr(ST, "reader", lambda conn: CLAUDE)
    monkeypatch.setattr(CC, "claude_bin", lambda: "claude")
    seen, answers, started = [], [], []

    def ask(who, effort, system, prompt, schema):
        seen.append({"system": system, "prompt": prompt})
        key = lambda note: next((ln.split(" · ")[0] for ln in prompt.splitlines() if ln.startswith("c") and note in ln), "c0")
        a = answers.pop(0) if answers else json.loads(json.dumps(PLAN).replace("ABSENT", key("wasn't in it"))
                                                       .replace("WITH", key("Kiran was in it")))
        if isinstance(a, Exception):
            raise a
        return a, {"output_tokens": 40}
    monkeypatch.setattr(PP, "_ask", through_reader(ask))
    monkeypatch.setattr(PP, "run_in_background", lambda db, pid: started.append(pid))
    db = lambda: connect(tmp_path / "t.db")  # noqa: E731
    app = FastAPI()
    app.include_router(make_router(db, processor=SimpleNamespace(kick=lambda: None)))
    return SimpleNamespace(conn=conn, seen=seen, answers=answers, started=started, s=(s1, s2, s3), client=TestClient(app))


def test_it_picks_their_conversations_and_the_lines_naming_them_elsewhere(lab):
    s1, s2, s3 = lab.s
    got = lab.client.get("/api/octo/people/p_han/prepare/pick").json()
    assert {c["session_id"] for c in got["with"]} >= {s1, s2} and s3 not in {c["session_id"] for c in got["with"]}
    assert all(c["whole"] for c in got["with"])
    [m] = got["mentions"]
    assert m["session_id"] == s3 and m["ticked"] and m["hits"] == 1 and m["shown"][0]["who"] == "Tara"
    own = sum(c["lines"] for c in got["with"])
    assert m["lines"] == 4 and got["lines"] == own + 4  # the mention ±3 lines is the whole short conversation
    assert got["names"] == ["kiran"] and got["shared_first"] == []
    off = lab.client.get("/api/octo/people/p_han/prepare/pick", params={"skip": [s3]}).json()
    assert not off["mentions"][0]["ticked"] and off["mentions"][0]["lines"] == 0 and off["lines"] == own
    assert lab.client.get("/api/octo/people/p_jay/prepare/pick").status_code == 404  # never the owner


def test_a_plan_is_made_once_asked_its_moments_checked_and_second_hand_marked(lab):
    c = lab.client
    v = c.post("/api/octo/people/p_han/prepare", json={"about": "first 1:1 after the reorg", "names": [], "skip": []}).json()
    assert v["plan"]["status"] == "running" and lab.started == ["p_han"] and lab.seen == []
    assert c.post("/api/octo/people/p_han/prepare", json={}).status_code == 409  # one at a time
    v = PP.run(lab.conn, "p_han")
    plan = v["plan"]["plan"]
    sent = lab.seen[0]
    assert "wasn't in it: only the lines naming them" in sent["prompt"] and "first 1:1 after the reorg" in sent["prompt"]
    assert "never how to position" in sent["system"] and "said in confidence" in sent["system"]
    first, second = plan["thinking"]
    assert first["second_hand"] and len(first["quotes"]) == 1      # only from a conversation he wasn't in
    assert not second["second_hand"] and len(second["quotes"]) == 1  # his own words; the made-up quote dropped
    assert plan["dropped"] == 1 and plan["questions"][0]["items"] == ["What matters most this quarter?"]
    assert P.claude_calls_since(lab.conn, 0) == 1
    assert c.get("/api/octo/people/p_han").json()["prepared_at"] == v["plan"]["at"]


def test_an_unticked_mention_is_never_sent_its_words_never_kept_and_deleting_it_deletes_the_plan(lab):
    s3 = lab.s[2]
    PP.start(lab.conn, "p_han", None, mentions=[])  # the owner unticked the only mention
    PP.run(lab.conn, "p_han")
    assert "Kiran said the flag question" not in lab.seen[0]["prompt"]
    assert "flag question" not in row(lab.conn, "SELECT sent FROM preparations")["sent"]  # ids and counts only
    PR.delete_session(lab.conn, s3)  # it names a conversation it listed: gone with it
    assert not PP.ready(lab.conn, "p_han")


def test_a_conversation_turning_up_after_the_page_was_read_isnt_sent_unseen(lab):
    PP.start(lab.conn, "p_han", None, mentions=["ses_seen_on_the_page"])  # s3 wasn't on the owner's list
    PP.run(lab.conn, "p_han")
    assert "Kiran said the flag question" not in lab.seen[0]["prompt"]


def test_names_too_short_or_also_words_arent_matched_everywhere():
    assert PP.pattern("Al") is None and PP.names_for("Al") == []
    will = PP.pattern("Will Patel")
    assert will.search("Will said the dates slip") and not will.search("we will ship on Friday")
    assert will.search("ask will patel first")  # the whole name, any case
    assert PP._first("Dr. Rao") == "Rao" and PP.pattern("Dr. Rao").search("Rao's team")


def test_a_call_is_counted_even_when_its_plan_was_deleted_while_running(lab):
    PP.start(lab.conn, "p_han", None)
    real = PP._ask

    def ask_then_delete(*a):
        PP.delete(lab.conn, "p_han")
        return real(*a)
    PP._ask = ask_then_delete
    try:
        PP.run(lab.conn, "p_han")
    finally:
        PP._ask = real
    assert P.claude_calls_since(lab.conn, 0) == 1 and not PP.ready(lab.conn, "p_han")


def test_the_last_plan_stays_while_a_new_one_runs_or_fails(lab):
    PP.start(lab.conn, "p_han", None)
    PP.run(lab.conn, "p_han")
    PP.start(lab.conn, "p_han", "a second go")
    v = PP.view(lab.conn, "p_han")
    assert v["plan"]["status"] == "running" and v["previous"]["status"] == "done"
    lab.answers.append(RuntimeError("it took too long"))
    v = PP.run(lab.conn, "p_han")
    assert v["plan"]["status"] == "failed" and v["previous"]["plan"]["aim"] == "Listen first."
    assert PP.ready(lab.conn, "p_han") and P.claude_calls_since(lab.conn, 0) == 1  # the failed call made no call: none counted
    lab.answers.append({**PLAN, "structure": [{"minutes": "5-10", "part": "Hello", "detail": "d"}]})
    PP.start(lab.conn, "p_han", None)
    v = PP.run(lab.conn, "p_han")
    assert v["previous"] is None and v["plan"]["plan"]["structure"][0]["minutes"] == 5
    assert lab.conn.execute("SELECT count(*) FROM preparations").fetchone()[0] == 1


def test_a_usage_limit_holds_claudes_steps_and_a_new_plan_replaces_the_last(lab):
    lab.answers.append(RuntimeError("Claude usage limit reached; resets at 11pm"))
    PP.start(lab.conn, "p_han", None)
    v = PP.run(lab.conn, "p_han")
    assert v["plan"]["status"] == "failed" and "usage limit" in v["plan"]["error"]
    assert P.claude_blocked_until(lab.conn, time.time()) is not None
    lab.conn.execute("UPDATE preparations SET error_at=error_at-86400")
    PP.start(lab.conn, "p_han", None)
    PP.run(lab.conn, "p_han")
    assert lab.conn.execute("SELECT count(*) FROM preparations").fetchone()[0] == 1


def test_the_banner_before_a_meeting_opens_the_plan_once_its_ready(lab):
    now = time.time()
    lab.conn.execute("INSERT INTO invitee_matches (invitee, person_id, created_at) VALUES ('kiran@example.com', 'p_han', 0)")
    lab.conn.execute("INSERT INTO events (id, source, title, start, end, invitees, seen_at) VALUES ('e1', 'mac', '1:1', ?, ?, ?, 0)",
                     (now + 300, now + 1800, json.dumps([{"name": "Kiran", "email": "kiran@example.com"}])))
    assert EV.due(lab.conn, now)[0]["page"] == "/people/p_han"
    assert PP.view(lab.conn, "p_han")["event"]["title"] == "1:1"  # it fills the note
    PP.start(lab.conn, "p_han", None)
    PP.run(lab.conn, "p_han")
    assert EV.due(lab.conn, now)[0]["page"] == "/people/p_han/prepare"


def test_it_goes_with_a_conversation_it_read_with_forget_and_after_its_conversation_or_event(lab):
    s1 = lab.s[0]
    PP.start(lab.conn, "p_han", None)
    PP.run(lab.conn, "p_han")
    PR.delete_session(lab.conn, s1)
    assert not PP.ready(lab.conn, "p_han")

    PP.start(lab.conn, "p_han", None)
    PP.run(lab.conn, "p_han")
    lab.conn.execute("UPDATE preparations SET created_at=?", (T0 + 5 * DAY,))
    assert PP.ready(lab.conn, "p_han")  # no conversation with him since it was made: kept
    _conv(lab.conn, 9, T0 + 6 * DAY)  # the conversation it was for, long over
    assert not PP.ready(lab.conn, "p_han")  # the banner's check sweeps, no page needed

    PP.start(lab.conn, "p_han", None)
    PP.run(lab.conn, "p_han")
    lab.conn.execute("UPDATE preparations SET event_end=?", (time.time() - 2 * DAY,))  # its meeting, unrecorded, two days ago
    PP.sweep(lab.conn)
    assert not PP.ready(lab.conn, "p_han")

    lab.answers.append({**PLAN, "careful": [{"text": "What Kiran said about the flag was in confidence", "quotes": []}]})
    PP.start(lab.conn, "p_pooja", None)  # Tara's plan names Kiran
    PP.run(lab.conn, "p_pooja")
    PP.start(lab.conn, "p_han", None)
    PP.run(lab.conn, "p_han")
    V.forget(lab.conn, "Kiran")
    assert not PP.ready(lab.conn, "p_han")
    assert not PP.ready(lab.conn, "p_pooja")  # it names Kiran: Forget reaches it too


def test_check_keeps_confidences_out_of_what_to_say_and_marks_others_words_second_hand():
    sp = {"A": {"self": True, "name": "Sam", "person_id": "p_jay", "state": "confirmed"}, "B": {"self": False, "name": "Kiran", "person_id": "p_han", "state": "confirmed"}}
    lines = [{"id": 1, "speaker": "B", "start": 0, "text": "the reorg lands in November, keep it quiet", "uncertain": False},
             {"id": 2, "speaker": "A", "start": 5, "text": "Kiran always wants the dates first", "uncertain": False},
             {"id": 3, "speaker": "B", "start": 9, "text": "I need the dates before I commit", "uncertain": False}]
    keys = {"c1": {"session_id": "s", "at": 0, "sp": sp, "lines": lines}}
    q = lambda n, t: {"conversation": "c1", "line": n, "text": t}
    got = PP.check({"aim": "a", "why": "", "structure": [], "limits": "",
                    "careful": [{"text": "The reorg was told in confidence", "quotes": [q(1, "the reorg lands in November")]}],
                    "thinking": [{"headline": "He plans the reorg", "observation": "o", "quotes": [q(1, "the reorg lands")]},
                                 {"headline": "Others say he wants dates", "observation": "o", "quotes": [q(2, "always wants the dates")]},
                                 {"headline": "He wants dates", "observation": "o", "quotes": [q(3, "I need the dates")]},
                                 {"headline": "Made up", "observation": "o", "quotes": [q(3, "nothing like this")]}],
                    "open": [], "questions": [{"purpose": "The reorg", "items": ["When does it land?"], "quotes": [q(1, "the reorg")]},
                                              {"purpose": "Dates", "items": ["What would you need?"], "quotes": []}]},
                   keys, "Sam", "p_han")
    assert [t["headline"] for t in got["thinking"]] == ["Others say he wants dates", "He wants dates"]
    assert [t["second_hand"] for t in got["thinking"]] == [True, False]  # Sam's words about him vs his own
    assert [g["purpose"] for g in got["questions"]] == ["Dates"]  # nothing to say aloud rests on a confidence
    assert got["withheld"] == 2 and got["unbacked"] == 1 and got["dropped"] == 1


def test_someone_not_in_people_is_added_by_name_and_prepared_for_from_mentions_only(lab):
    s1 = lab.s[0]
    dz = load_json(row(lab.conn, "SELECT output FROM stage_runs WHERE id=?", f"diarize_{s1}")["output"])
    dz["lines"][2]["text"] = "Let me check with Farhan and come back to you tomorrow."
    lab.conn.execute("UPDATE stage_runs SET output=? WHERE id=?", (json.dumps(dz), f"diarize_{s1}"))
    got = lab.client.post("/api/octo/people/prepare", json={"name": " Farhan ", "invitee": "farhan@example.com"}).json()
    pid = got["person_id"]
    assert row(lab.conn, "SELECT name FROM people WHERE id=?", pid)["name"] == "Farhan"
    assert row(lab.conn, "SELECT person_id FROM invitee_matches WHERE invitee='farhan@example.com'")["person_id"] == pid
    assert lab.client.post("/api/octo/people/prepare", json={"name": "Farhan"}).json()["person_id"] == pid  # the same person
    assert lab.client.post("/api/octo/people/prepare", json={"name": "Sam"}).status_code == 422  # the owner
    pick = lab.client.get(f"/api/octo/people/{pid}/prepare/pick").json()
    assert pick["with"] == [] and [m["session_id"] for m in pick["mentions"]] == [s1]
    assert [g["who"] for g in pick["mentions"][0]["going"]][:1] and pick["mentions"][0]["going"][2]["text"].startswith("Let me check")
    PP.start(lab.conn, pid, None)
    PP.run(lab.conn, pid)
    assert "going" not in row(lab.conn, "SELECT sent FROM preparations WHERE person_id=?", pid)["sent"]  # the page's only
    assert PP.ready(lab.conn, pid)


def test_what_you_planned_shows_beside_the_conversation_it_was_for_then_goes(lab):
    PP.start(lab.conn, "p_han", "first 1:1")
    PP.run(lab.conn, "p_han")
    made = time.time() - 2 * 3600
    lab.conn.execute("UPDATE preparations SET created_at=?", (made,))
    sid = _conv(lab.conn, 9, made + 3600)  # the conversation it was for, an hour later
    lab.conn.execute("UPDATE recordings SET audio_deleted_at=1")
    got = lab.client.get(f"/api/octo/sessions/{sid}").json()["planned"]
    assert [(p["name"], p["aim"]) for p in got] == [("Kiran", "Listen first.")]
    assert lab.client.get(f"/api/octo/sessions/{lab.s[0]}").json()["planned"] == []  # an earlier one: not its plan
    assert PP.ready(lab.conn, "p_han")  # an hour into it: still ready
    lab.conn.execute("UPDATE preparations SET created_at=?", (made - 4 * 3600,))
    lab.conn.execute("UPDATE recordings SET started_at=? WHERE id=?", (made - 3 * 3600, f"rec_20261002_{9:08x}"))
    assert not PP.ready(lab.conn, "p_han") and PP.view(lab.conn, "p_han")["plan"]["used"]["session_id"] == sid
    lab.conn.execute("UPDATE preparations SET created_at=?", (made - 9 * 86400,))
    lab.conn.execute("UPDATE recordings SET started_at=? WHERE id=?", (made - 8 * 86400, f"rec_20261002_{9:08x}"))
    PP.sweep(lab.conn)
    assert lab.conn.execute("SELECT count(*) FROM preparations").fetchone()[0] == 0  # kept a week after, then gone


def test_the_meetings_length_reaches_the_model_and_the_agenda_says_who_has_a_plan(lab):
    now = time.time()
    lab.conn.execute("INSERT INTO invitee_matches (invitee, person_id, created_at) VALUES ('kiran@example.com', 'p_han', 0)")
    lab.conn.execute("INSERT INTO events (id, source, title, start, end, invitees, seen_at) VALUES ('e1', 'mac', '1:1', ?, ?, ?, 0)",
                     (now + 3600, now + 3600 + 1800, json.dumps([{"name": "Kiran", "email": "kiran@example.com"},
                                                                {"name": "Zoe New", "email": "zoe@example.com"}])))
    [e] = EV.agenda(lab.conn, now)
    assert [(p["name"], p["prepared"]) for p in e["people"]] == [("Kiran", False), ("Zoe New", False)]
    assert e["people"][1]["person_id"] is None and e["people"][1]["key"] == "zoe@example.com"
    PP.start(lab.conn, "p_han", None)
    PP.run(lab.conn, "p_han")
    assert "The calendar gives it 30 minutes" in lab.seen[0]["prompt"]
    assert EV.agenda(lab.conn, now)[0]["people"][0]["prepared"]
