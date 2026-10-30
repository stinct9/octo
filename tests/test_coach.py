"""The coach (octo/coach.py, docs/coach-plan.md §4.3): one Claude call across the reviews shown now,
with the owner's marks; themes kept only with items from two conversations; stored with what it read, so a
delete or a Forget takes the note with it; its calls count toward the day's cap. And the numbers a
focus or a note can be tracked with (octo/measures.py)."""

import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import claude_code as CC
from octo import coach as CO
from octo import focus as F
from octo import measures as M
from octo import notify as N
from octo import pipeline as P
from octo import sessions as SV
from octo import voice as V
from octo.api import make_router
from octo.db import connect, row, rows
from octo.privacy import delete_session

DAY = 86400
T0 = datetime(2026, 10, 2, 10, 0).timestamp()
LINES = [(0, "A", 0.0, 60.0, "So I think we should ship on Friday and sort the supplier later."),
         (1, "B", 60.0, 150.0, "I'm not sure the dates work for the supplier at all."),
         (2, "A", 150.0, 180.0, "Let me check and come back to you tomorrow."),
         (3, "B", 180.0, 300.0, "Fine, but the flag question is still open for me.")]


def q(line: int) -> dict:
    i, spk, a, b, text = LINES[line]
    return {"line": i, "text": text, "start": a, "end": b, "speaker": spk, "uncertain": False}


def review(n: int) -> dict:
    return {"kind": "review", "skipped": None, "summary": f"Conversation {n} about the supplier dates.",
            "one_thing": {"headline": f"You answered before Kiran finished ({n})", "why": "He hadn't said what worried him.",
                          "quotes": [q(0)], "unverified_quotes": 0, "flags": [], "next": 0},
            "reading": [{"person": "Kiran", "headline": "Kiran raises risks late", "observation": "o", "quotes": [q(1), q(3)],
                         "unverified_quotes": 0, "flags": []}],
            "between": [{"headline": f"The flag question stayed open ({n})", "observation": "o", "quotes": [q(3)],
                         "unverified_quotes": 0, "flags": []}],
            "conduct": [{"principle": 7, "title": "Stay calm", "held": "partly", "observation": "You pressed on.",
                         "quotes": [q(0)], "unverified_quotes": 0, "flags": [], "try_next_time": None}],
            "beneath": [], "no_moment": [], "dropped": [], "limits": "Tone is inferred from words.",
            "next": [{"title": "Ask Kiran what worries him", "detail": "d", "why": "w", "due": None}]}


def _lab(tmp_path, profile: dict | None = None):
    """A lab like the owner's: their profile is set (octo/owner.py, `octo owner set` when 4.2 is deployed)."""
    from octo import owner as O
    conn = connect(tmp_path / "t.db")
    for pid, name, me in (("p_jay", "Sam", 1), ("p_han", "Kiran", 0), ("p_pooja", "Tara", 0)):
        conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES (?, ?, ?, 0)", (pid, name, me))
    O.save(conn, profile or {"name": "Sam", "pronoun": "he", "about": "a product manager"})
    return conn


def _conv(conn, n: int, at: float, other: str = "p_han", out: dict | None = None, finished: float = 0.0) -> str:
    """A conversation with the owner (A) and one other (B): diarized, numbers, and a review shown now."""
    sid, rec = f"ses_20261002_{n:08x}", f"rec_20261002_{n:08x}"
    lines = [{"id": i, "speaker": s, "start": a, "end": b, "text": t, "uncertain": False} for i, s, a, b, t in LINES]
    dz = {"speakers": ["A", "B"], "talk_s": {"A": 90.0, "B": 210.0}, "lines": lines, "overlapping_turns": [],
          "turns": [{"speaker": ln["speaker"], "start": ln["start"], "end": ln["end"]} for ln in lines]}
    nums = {"speakers": {"A": {"share": 0.3, "turns": 2, "longest_turn_s": 60, "words": 25, "questions": 3,
                               "interrupted_others": 1, "was_interrupted": 0, "fillers_per_100_words": 0},
                         "B": {"share": 0.7, "turns": 2, "longest_turn_s": 120, "words": 20, "questions": 0,
                               "interrupted_others": 0, "was_interrupted": 1, "fillers_per_100_words": 0}}}
    conn.execute("INSERT INTO recordings (id, source, path, started_at, duration_s, created_at, audio_deleted_at) "
                 "VALUES (?, 'device', 'x.flac', ?, 600, 0, NULL)", (rec, at))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) VALUES (?, ?, 0, 600, 300, 'active', 0)",
                 (sid, rec))
    for stage, o in (("diarize", dz), ("numbers", nums), ("insight", out or review(n))):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at, finished_at) "
                     "VALUES (?, ?, ?, 'h', 'done', 1, ?, 0, ?)", (f"{stage}_{sid}", stage, sid, json.dumps(o), finished))
    for letter, pid in (("A", "p_jay"), ("B", other)):
        conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, diarize_run) VALUES (?, ?, ?, 'confirmed', ?)",
                     (sid, letter, pid, f"diarize_{sid}"))
    return sid


def _three(conn) -> list[str]:
    """Conversations on 2, 3 and 4 Oct (the last with Tara), one before the lab began, and one too
    short for a review: the coach reads the first three, newest first."""
    sids = [_conv(conn, 1, T0), _conv(conn, 2, T0 + DAY), _conv(conn, 3, T0 + 2 * DAY, other="p_pooja")]
    _conv(conn, 4, datetime(2026, 9, 28, 10).timestamp())
    _conv(conn, 5, T0 + 3 * DAY, out={"kind": "review", "skipped": "too short", "conduct": []})
    return sids


def mark(conn, sid, item, **kw):
    conn.execute("INSERT OR IGNORE INTO feedback (insight_run, item, session_id, updated_at) VALUES (?, ?, ?, 0)",
                 (f"insight_{sid}", item, sid))
    for k, v in kw.items():
        conn.execute(f"UPDATE feedback SET {k}=? WHERE insight_run=? AND item=?", (v, f"insight_{sid}", item))


RAW = {"work_on": {"theme": "You answer before the other person has finished", "why": "It costs you what they meant.",
                   "evidence": ["c1.o", "c3.p7", "c9.x", "c1.o"],
                   "try": {"when": "When someone raises a risk,", "instead": "Ask what would change their mind."},
                   "know": {"measure": "talked_over", "how": "Fewer times talking over someone."}},
       "keep": {"theme": "You come back with what you promised", "why": "w", "evidence": ["c1.n0"]},
       "also": [{"theme": "Kiran raises risks late", "why": "w", "evidence": ["c2.r0", "c3.r0"]}],
       "changed": "", "limits": "Three conversations is few."}
USAGE = {"cost_usd_equivalent": 0.4, "duration_ms": 60000, "input_tokens": 9000, "output_tokens": 800}


def fake_ask(got: list):
    def ask(model, effort, system, prompt, schema):
        got.append({"model": model, "effort": effort, "system": system, "prompt": prompt, "schema": schema})
        return json.loads(json.dumps(RAW)), USAGE
    return ask


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr(CC, "claude_bin", lambda: "/usr/local/bin/claude")


# ---- what it reads ------------------------------------------------------------------------------

def test_it_reads_the_reviews_shown_now_newest_first_with_jays_marks_and_never_what_he_said_is_wrong(tmp_path):
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)
    mark(conn, s3, "o", novelty="new")
    mark(conn, s3, "p7", verdict="partly", note="I was calm, just quick")
    mark(conn, s2, "l0", verdict="wrong")
    mark(conn, s1, "n0", acted=1)
    mark(conn, s2, "n0", let_go_at=5.0)
    from octo import rubric as RB
    RB.OWN.write_text("# Rubric\n\n## Conduct principles\n\n1. Keep your word, even when it costs you.\n")
    m = CO.material(conn, now=T0 + 3 * DAY)
    assert [c["session_id"] for c in m["conversations"]] == [s3, s2, s1]
    assert m["conversations"][0]["with"] == ["Tara"] and m["conversations"][2]["with"] == ["Kiran"]
    assert "c2.l0" not in m["ids"] and m["left_out"] == 1 and m["conversations"][1]["left_out"] == 1
    assert m["ids"]["c1.o"] == {"session_id": s3, "insight_run": f"insight_{s3}", "item": "o", "at": T0 + 2 * DAY, "conv": 1,
                                "kind": "one_thing", "label": "The one thing", "headline": "You answered before Kiran finished (3)",
                                "held": None, "quote": {**m["ids"]["c1.o"]["quote"], "mine": True, "who": "you"}}
    assert m["ids"]["c1.r0"]["quote"]["who"] == "Tara"  # nobody's own words: the first quote
    assert m["read"]["conversations"] == 3 and m["read"]["left_out"] == 1

    system, prompt = CO.system_prompt(m["jay"], m["owner"]), CO.user_prompt(m)
    assert system.startswith("You coach Sam,") and "two different conversations" in system
    assert "Keep your word, even when it costs you." in prompt  # the owner's principles, verbatim
    assert "[c1.o] The one thing: You answered before Kiran finished (3): He hadn't said what worried him." in prompt
    assert "· new to him" in prompt and 'partly right, he said: "I was calm, just quick"' in prompt
    assert "[c3.n0] Step: Ask Kiran what worries him: d. · he did it" in prompt
    assert "[c2.n0] Step: Ask Kiran what worries him: d. · he let it go" in prompt
    assert "The flag question stayed open (2)" not in prompt and "1 item he marked not right left out" in prompt
    assert "(3)" in prompt and "(1)" in prompt and "Conversation 5" not in prompt and "Conversation 4" not in prompt
    assert "His numbers: 30% of the talk, 3 questions, talked over someone 1 time." in prompt
    assert "he hasn't answered this review yet" in prompt and "There's no earlier note: this is the first." in prompt


def test_it_reads_his_reflection_beside_the_review(tmp_path):  # B15
    from octo import intention as IN
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)
    IN.reflect(conn, s3, "I rushed Tara, I knew it as I did it.")
    m = CO.material(conn, now=T0 + 3 * DAY)
    assert [c["reflection"] for c in m["conversations"]] == ["I rushed Tara, I knew it as I did it.", None, None]
    prompt = CO.user_prompt(m)
    assert 'How it went, in his words: "I rushed Tara, I knew it as I did it."' in prompt and prompt.count("How it went, in his words") == 1
    assert "how he said it went in his own words" in CO.system_prompt(m["jay"], m["owner"])


def test_a_theme_needs_items_from_two_conversations(tmp_path):
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)
    m = CO.material(conn)
    out = CO.check({**RAW, "work_on": {**RAW["work_on"], "evidence": ["c1.o", "c1.l0", "c1.p7", "c3.p7"]}}, m)
    # One from each conversation first, so the first moments shown span them.
    assert [e["id"] for e in out["work_on"]["evidence"]] == ["c1.o", "c3.p7", "c1.l0", "c1.p7"]
    out = CO.check(json.loads(json.dumps(RAW)), m)
    w = out["work_on"]
    assert [e["id"] for e in w["evidence"]] == ["c1.o", "c3.p7"] and w["conversations"] == 2
    assert w["evidence"][0]["session_id"] == s3 and w["evidence"][1]["item"] == "p7" and w["evidence"][1]["held"] == "partly"
    assert w["try"] == {"when": "When someone raises a risk", "instead": "Ask what would change their mind."}
    assert w["know"] == {"measure": "talked_over", "how": "Fewer times talking over someone."}
    assert CO.focus_text(w) == "When someone raises a risk, ask what would change their mind."
    assert out["keep"] is None and out["dropped"] == [{"section": "keep", "theme": "You come back with what you promised",
                                                        "reason": "seen in one conversation only"}]
    assert [a["theme"] for a in out["also"]] == ["Kiran raises risks late"] and out["unknown_evidence"] == ["c9.x"]

    bad = {**RAW, "work_on": {**RAW["work_on"], "evidence": ["nope"], "know": {"measure": "smiles", "how": ""}}}
    out = CO.check(bad, m)
    assert out["work_on"] is None and out["dropped"][0]["reason"] == "no review item it could point to"
    ok = CO.check({**RAW, "work_on": {**RAW["work_on"], "know": {"measure": "smiles", "how": ""}, "try": {"when": "", "instead": "x"}}}, m)
    assert ok["work_on"]["know"]["measure"] == "focus" and ok["work_on"]["try"] is None


# ---- asking -------------------------------------------------------------------------------------

def test_asking_stores_the_note_with_what_it_read_and_counts_as_a_claude_call(tmp_path, claude):
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)
    nid = CO.start(conn)
    with pytest.raises(CO.CoachError) as e:
        CO.start(conn)
    assert e.value.status == 409
    got = []
    n = CO.run(conn, nid, ask=fake_ask(got))
    assert n["status"] == "done" and got[0]["model"] == "claude-opus-5-5" and got[0]["effort"] == "high"
    assert got[0]["schema"] is CO.SCHEMA
    assert {r["session_id"] for r in rows(conn, "SELECT session_id FROM coach_sources WHERE note_id=?", nid)} == {s1, s2, s3}
    assert P.claude_calls_since(conn, 0) == 1 and SV.claude_usage(conn, 0)["calls"] == 1

    s = CO.summary(conn)
    assert s["latest"]["id"] == nid and s["running"] is None and s["failed"] is None and s["can_ask"]
    assert s["new_since"] == 0 and s["earlier"] == [] and s["conversations"] == 3
    v = s["latest"]
    assert v["read"]["conversations"] == 3 and not v["seen"]
    assert v["note"]["work_on"]["evidence"][0]["with"] == ["Tara"] and v["note"]["work_on"]["evidence"][0]["audio"]
    assert v["note"]["work_on"]["focus_text"].startswith("When someone raises a risk, ask")
    assert v["numbers"]["measure"] == "talked_over" and v["numbers"]["since"] is None and v["numbers"]["before"]["value"] == 2.0

    # A review made after the note: one new since it. The next note reads the last one and its marks.
    _conv(conn, 6, T0 + 4 * DAY, finished=n["created_at"] + 10)
    assert CO.summary(conn)["new_since"] == 1
    CO.rate(conn, nid, "work_on", "right", "new")
    nid2 = CO.start(conn)
    CO.run(conn, nid2, ask=fake_ask(got))
    assert 'work on: "You answer before the other person has finished"' in got[1]["prompt"]
    assert "his marks: right, new to him" in got[1]["prompt"] and "Kiran raises risks late" in got[1]["prompt"]
    s = CO.summary(conn)
    assert s["latest"]["id"] == nid2 and [x["id"] for x in s["earlier"]] == [nid]


def test_a_failed_call_is_kept_and_a_usage_limit_holds_the_pipeline_too(tmp_path, claude):
    conn = _lab(tmp_path)
    _three(conn)
    nid = CO.start(conn)

    def limit(*a):
        raise RuntimeError("Claude Code: You've hit your usage limit · resets 5pm (Asia/Kolkata)")
    with pytest.raises(RuntimeError):
        CO.run(conn, nid, ask=limit)
    n = row(conn, "SELECT * FROM coach_notes WHERE id=?", nid)
    assert n["status"] == "failed" and "usage limit" in n["error"]
    assert P.claude_blocked_until(conn) is not None
    s = CO.summary(conn)
    assert s["failed"]["id"] == nid and s["latest"] is None and not s["can_ask"]
    assert s["why_not"].startswith("Claude's usage limit: after ")
    assert P.claude_calls_since(conn, 0) == 0  # a failed call isn't counted as one made


def test_when_it_cannot_be_asked(tmp_path, claude, monkeypatch):
    conn = _lab(tmp_path)
    _conv(conn, 1, T0)
    assert CO.why_not(conn) == "the coach needs reviews of at least 2 conversations"
    with pytest.raises(CO.CoachError) as e:
        CO.start(conn)
    assert e.value.status == 422
    _conv(conn, 2, T0 + DAY)
    assert CO.why_not(conn) is None
    monkeypatch.setattr(CC, "claude_bin", lambda: None)
    assert CO.why_not(conn) == "Claude Code isn't installed (the `claude` command)"
    monkeypatch.setattr(CC, "claude_bin", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(P, "DAILY_CLAUDE_CALLS", 1)
    conn.execute("INSERT INTO coach_notes (id, created_at, status, model, usage, finished_at) VALUES ('c_old', 0, 'done', 'm', '{}', ?)",
                 (datetime.now().timestamp(),))
    assert CO.why_not(conn) == "today's 1 Claude calls are used: after midnight"
    monkeypatch.setattr(P, "DAILY_CLAUDE_CALLS", 60)
    # A note left "running" by a restart doesn't hold the coach up for ever.
    conn.execute("INSERT INTO coach_notes (id, created_at, status, model) VALUES ('c_stuck', ?, 'running', 'm')",
                 (datetime.now().timestamp() - CO.STOPPED_AFTER_S - 5,))
    assert CO.why_not(conn) is None
    assert row(conn, "SELECT status FROM coach_notes WHERE id='c_stuck'")["status"] == "failed"


# ---- deleting -----------------------------------------------------------------------------------

def test_deleting_a_conversation_or_forgetting_someone_in_it_deletes_the_notes_that_read_it(tmp_path, claude, monkeypatch):
    monkeypatch.setattr("octo.privacy._silence", lambda *a: None)
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)
    nid = CO.start(conn)
    CO.run(conn, nid, ask=fake_ask([]))
    CO.rate(conn, nid, "work_on", "right")
    CO.make_focus(conn, nid)
    delete_session(conn, s2)
    for t in ("coach_notes", "coach_sources", "coach_feedback"):
        assert not rows(conn, f"SELECT * FROM {t}"), t

    nid = CO.start(conn)
    CO.run(conn, nid, ask=fake_ask([]))
    assert not V.forget(conn, "Ravi")  # nobody by that name: nothing changes
    assert row(conn, "SELECT 1 FROM coach_notes WHERE id=?", nid)
    assert V.forget(conn, "Kiran")
    assert not row(conn, "SELECT 1 FROM coach_notes WHERE id=?", nid)  # Kiran was in what it read


def test_forgetting_someone_it_never_read_keeps_the_note(tmp_path, claude):
    conn = _lab(tmp_path)
    _conv(conn, 1, T0)
    _conv(conn, 2, T0 + DAY)
    nid = CO.start(conn)
    CO.run(conn, nid, ask=fake_ask([]))
    assert V.forget(conn, "Tara")
    assert row(conn, "SELECT status FROM coach_notes WHERE id=?", nid)["status"] == "done"


# ---- marks and the focus ------------------------------------------------------------------------

def test_marks_and_making_it_the_focus(tmp_path, claude):
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)
    nid = CO.start(conn)
    CO.run(conn, nid, ask=fake_ask([]))
    assert CO.rate(conn, nid, "work_on", "partly", "knew") == {"work_on": {"verdict": "partly", "novelty": "knew"}}
    assert CO.rate(conn, nid, "work_on", verdict="")["work_on"] == {"verdict": None, "novelty": "knew"}
    with pytest.raises(CO.CoachError) as e:
        CO.rate(conn, nid, "keep", "right")  # dropped: nothing to mark
    assert e.value.status == 404
    with pytest.raises(CO.CoachError) as e:
        CO.rate(conn, nid, "work_on", "maybe")
    assert e.value.status == 422

    f = CO.make_focus(conn, nid)
    # The most recent item it cites that he can act on: the one thing of 4 Oct. Tracked with its number.
    assert (f["session_id"], f["insight_run"], f["item"], f["measure"], f["coach_note"]) == (s3, f"insight_{s3}", "o", "talked_over", nid)
    assert f["text"] == "When someone raises a risk, ask what would change their mind."
    F.check(conn, f["id"], s1, "did")
    assert row(conn, "SELECT acted FROM feedback WHERE insight_run=? AND item='o'", f"insight_{s3}")["acted"] == 1
    v = CO.view(conn, row(conn, "SELECT * FROM coach_notes WHERE id=?", nid))
    assert v["focus"]["active"] and v["focus"]["tally"]["did"] == 1
    assert F.view(conn, F.active(conn))["numbers"]["measure"] == "talked_over"
    g = CO.make_focus(conn, nid, text="Wait for the end of the question", measure="")
    assert g["text"] == "Wait for the end of the question" and g["measure"] is None
    with pytest.raises(CO.CoachError):
        CO.make_focus(conn, nid, measure="smiles")


def test_the_routes(tmp_path, claude):
    db = tmp_path / "t.db"
    conn = _lab(tmp_path)
    _three(conn)
    asked = []
    app = FastAPI()
    app.include_router(make_router(lambda: connect(db), processor=type("P", (), {"kick": lambda self: None})(),
                                   coach=asked.append))
    c = TestClient(app)
    assert c.get("/api/octo/coach").json()["latest"] is None
    s = c.post("/api/octo/coach", json={}).json()
    assert len(asked) == 1 and s["running"]["id"] == asked[0] and not s["can_ask"]
    assert c.post("/api/octo/coach", json={}).status_code == 409
    assert c.post("/api/octo/coach", json={}, headers={"origin": "https://evil.example"}).status_code == 403

    CO.run(conn, asked[0], ask=fake_ask([]))
    s = c.get("/api/octo/coach").json()
    nid = s["latest"]["id"]
    assert nid == asked[0] and s["running"] is None and s["can_ask"]
    assert f"coach:{nid}" in N.waiting(conn)
    assert c.post(f"/api/octo/coach/{nid}/seen", json={}).json() == {"ok": True}
    assert c.get(f"/api/octo/coach/{nid}").json()["seen"] and f"coach:{nid}" not in N.waiting(conn)
    assert N.message({f"coach:{nid}"}) == "the coach's note is ready"
    r = c.post(f"/api/octo/coach/{nid}/feedback", json={"part": "work_on", "verdict": "right", "novelty": "new"})
    assert r.json() == {"feedback": {"work_on": {"verdict": "right", "novelty": "new"}}}
    assert c.post(f"/api/octo/coach/{nid}/feedback", json={"part": "nope", "verdict": "right"}).status_code == 404
    got = c.post(f"/api/octo/coach/{nid}/focus", json={}).json()
    assert got["focus"]["active"]["coach_note"] == nid and got["note"]["focus"]["active"]
    assert c.get("/api/octo/coach/coach_nope").status_code == 404


# ---- the numbers a focus or a note is tracked with ----------------------------------------------

def test_questions_talk_share_and_talking_over_are_pooled_over_conversations(tmp_path):
    conn = _lab(tmp_path)
    s1, s2, s3 = _three(conn)
    cs = M.conversations(conn)
    # Not the one before the lab began; the one too short for a review still has its numbers.
    assert [c["session_id"] for c in cs] == [s1, s2, s3, "ses_20261002_00000005"]
    assert cs[0]["minutes"] == 5.0 and cs[0]["share"] == 0.3 and cs[0]["questions"] == 3 and not cs[0]["alone"]
    p = M.pooled(cs, "questions")
    assert p["value"] == 6.0 and p["count"] == 12 and p["lo"] < 6.0 < p["hi"]
    assert M.pooled(cs, "talk_share")["value"] == 0.3 and M.pooled(cs, "talked_over")["value"] == 2.0
    got = M.since_before(conn, "questions", T0 + 1.5 * DAY)
    assert got["since"]["conversations"] == 2 and got["before"]["conversations"] == 2
    assert M.for_session(conn, "talk_share", s2) == 0.3
    f = F.start(conn, "Ask one more question", measure="questions", now=T0 + 1.5 * DAY)
    assert F.view(conn, f)["numbers"]["since"]["value"] == 6.0
    assert F.for_session(conn, s3, T0 + 2 * DAY)["here"] == 6.0


def test_the_coach_writes_about_the_owner_as_they_asked(tmp_path):
    """A buyer's lab (product plan 4.2): her profile and pronoun, not the owner's."""
    conn = _lab(tmp_path, {"name": "Asha", "pronoun": "she", "about": "a school principal",
                           "principles_from": "the Gita", "never": "flattering the inspector"})
    _three(conn)
    m = CO.material(conn)
    system = CO.system_prompt("Asha", m["owner"])
    assert system.startswith("You coach Asha, a school principal. She records her conversations")
    assert "principles she wrote herself from the Gita" in system and "Never suggest flattering the inspector." in system
    assert "product manager" not in system and " him" not in system
    assert "in her words" in CO.user_prompt(m)
    they = CO.system_prompt("Sam", {"pronoun": "they"})
    assert "They record their conversations" in they and "what they're practising" in they and "Never suggest" not in they
