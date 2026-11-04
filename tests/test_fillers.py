"""The owner's fillers over time (octo/fillers.py, docs/coach-plan.md §4.2): their own list, counted at read
time over stored lines; the measuring stick; the pooled line and its band; where they happen;
suggestions; the check by ear; a focus that carries the number."""

import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import fillers as FL
from octo import focus as FO
from octo import privacy as PV
from octo.api import make_router
from octo.db import connect, row, rows

DAY = 86400
T0 = datetime(2026, 10, 2, 10, 0).timestamp()
QWEN = {"language": "en", "language_votes": {"en": 0.9}, "model": "Qwen/Qwen3-ASR-1.7B", "segments": []}


def _lab(tmp_path):
    conn = connect(tmp_path / "t.db")
    for pid, name, me in (("p_jay", "Sam", 1), ("p_asha", "Asha", 0), ("p_ravi", "Ravi", 0)):
        conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES (?, ?, ?, 0)", (pid, name, me))
    return conn


def _conv(conn, n: int, at: float, script: list[tuple[str, str]], who: dict | None = None, tr: dict | None = None):
    """A conversation with these lines (letter, text), A being the owner unless `who` says otherwise."""
    who = who or {"A": "p_jay", "B": "p_asha"}
    sid, rec = f"ses_20261002_{n:08x}", f"rec_20261002_{n:08x}"
    lines, t = [], 0.0
    for i, (spk, text) in enumerate(script):
        d = max(1.0, len(text.split()) / 2.5)
        lines.append({"id": i, "speaker": spk, "start": t, "end": t + d, "text": text, "uncertain": False})
        t += d + 0.5
    talk = {}
    for ln in lines:
        talk[ln["speaker"]] = talk.get(ln["speaker"], 0.0) + ln["end"] - ln["start"]
    dz = {"speakers": sorted(talk), "talk_s": talk, "lines": lines, "overlapping_turns": [],
          "turns": [{"speaker": ln["speaker"], "start": ln["start"], "end": ln["end"]} for ln in lines]}
    conn.execute("INSERT INTO recordings (id, source, path, started_at, duration_s, created_at) VALUES (?, 'device', 'x.flac', ?, ?, 0)",
                 (rec, at, t + 1))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) VALUES (?, ?, 0, ?, 10, 'active', 0)",
                 (sid, rec, t + 1))
    for stage, out in (("transcribe", tr or QWEN), ("diarize", dz)):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                     "VALUES (?, ?, ?, 'h', 'done', 1, ?, 0)", (f"{stage}_{sid}", stage, sid, json.dumps(out)))
    for letter, pid in who.items():
        conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, diarize_run) VALUES (?, ?, ?, 'confirmed', ?)",
                     (sid, letter, pid, f"diarize_{sid}"))
    return sid


def _words(n: int, filler_every: int = 0, word: str = "plan", filler: str = "um") -> str:
    """n words, every `filler_every`-th one a filler."""
    return " ".join(filler if filler_every and (i + 1) % filler_every == 0 else word for i in range(n))


# ---- matching ----------------------------------------------------------------------------------

def test_matching_counts_held_hesitations_and_phrases_as_whole_words():
    phr = list(FL.DEFAULTS)
    pat = FL.matcher(phr)
    got = FL.find("Um, so basically, you know, it was kind of ummm a surprise for her umbrella.", pat, phr)
    assert [k for _, _, k in got] == ["um", "you know", "kind of", "um"]  # not "her", not "umbrella"
    assert FL.find("Uh huh, okay. Mm hmm. Uh-huh.", pat, phr) == []  # backchannels: listening, not hesitating
    assert [k for _, _, k in FL.find("I er, err, HMMM.", pat, phr)] == ["er", "er", "hmm"]
    text = "So, you know, it's fine"
    (a, b, _), = FL.find(text, pat, phr)
    assert text[a:b] == "you know"  # spans point into the line, for underlining


def test_the_list_starts_with_the_eight_and_stays_the_owners(tmp_path):
    conn = _lab(tmp_path)
    assert FL.phrases(conn) == list(FL.DEFAULTS)
    assert FL.set_phrase(conn, "  Basically ", True)[-1] == "basically"
    FL.set_phrase(conn, "um", False)
    assert "um" not in FL.phrases(conn)
    for p in FL.phrases(conn):
        FL.set_phrase(conn, p, False)
    assert FL.phrases(conn) == []  # removed ones stay removed: the defaults don't come back on their own
    FL.set_phrase(conn, "um", True)
    assert FL.phrases(conn) == ["um"]
    for bad in ("", "a b c d e", "123", "x" * 50):
        with pytest.raises(FL.FillerError):
            FL.set_phrase(conn, bad, True)


# ---- one conversation ----------------------------------------------------------------------------

def test_a_conversation_counts_jays_lines_only_with_his_list(tmp_path):
    conn = _lab(tmp_path)
    sid = _conv(conn, 1, T0, [("B", "Um, um, where are we?"), ("A", _words(60, 10)), ("B", "Okay, you know, fine."),
                              ("A", _words(60, 20, filler="you know"))])
    sess = row(conn, "SELECT s.*, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", sid)
    c = FL.conversation(conn, sess, FL.phrases(conn), detail=True)
    assert (c["words"], c["fillers"]) == (123, 9)  # 60 + 63 words: each "you know" is two words, one filler
    assert c["by_phrase"] == {"um": 6, "you know": 3}
    assert c["with"] == ["Asha"] and c["size"] == "solo"  # Asha's 4 s of talk: named, but not a voice in the room
    assert c["on_line"] and set(c["spans"]) == {1, 3}
    assert c["heaviest"][0]["lines"] == [1]  # 6 in 60 beats 3 in 63


def test_the_measuring_stick_says_why_a_conversation_is_off_the_line(tmp_path):
    conn = _lab(tmp_path)
    long = [("A", _words(120, 10)), ("B", "Right.")]
    cases = {
        "on": _conv(conn, 1, T0, long),
        "merged": _conv(conn, 2, T0, long, tr={**QWEN, "language": "hi", "merged": {"calls": 1}}),
        "whisper": _conv(conn, 3, T0, long, tr={**QWEN, "model": "mlx-community/whisper-large-v3-mlx"}),
        "hindi": _conv(conn, 4, T0, long, tr={**QWEN, "language": "hi", "language_votes": {"hi": 0.9}}),
        "not you": _conv(conn, 5, T0, long, who={"A": "p_ravi", "B": "p_asha"}),
        "old": _conv(conn, 6, datetime(2024, 3, 1).timestamp(), long),
        "short": _conv(conn, 7, T0, [("A", _words(40, 10))]),
    }
    got = {c["session_id"]: c["reason"] for c in FL.all_conversations(conn)}
    assert got[cases["on"]] is None
    assert "merged" in got[cases["merged"]] and "Whisper" in got[cases["whisper"]]
    assert got[cases["hindi"]] == "not in English" and got[cases["not you"]] == "you're not confirmed in it"
    assert got[cases["old"]] == "recorded before the lab began" and got[cases["short"]] == "under 100 of your words"


# ---- across conversations ---------------------------------------------------------------------

def test_the_line_pools_words_and_the_band_shows_ordinary_variation(tmp_path):
    conn = _lab(tmp_path)
    # 1 Oct fortnight: 5 per 100 words; later: 2 per 100, each conversation 400 of the owner's words.
    for i in range(4):
        _conv(conn, i, T0 + i * DAY, [("A", _words(400, 20)), ("B", "Okay.")])
    for i in range(4, 10):
        _conv(conn, i, T0 + (16 + i) * DAY, [("A", _words(400, 50)), ("B", "Okay.")])
    s = FL.summary(conn)
    t = s["trend"]
    assert len(t["points"]) == 10 and t["points"][0]["rate"] == 5.0
    assert t["baseline"]["rate"] == 5.0 and t["baseline"]["words"] == 1600
    assert t["now"]["words"] == 2000 and t["now"]["rate"] == 2.0  # the last five: about 2,000 words
    assert t["now"]["lo"] < 2.0 < t["now"]["hi"] and t["change"] == "lower"
    assert t["line"][-1]["rate"] == 2.0 and t["line"][4]["rate"] == round(100 * (80 + 8) / 2000, 2)


def test_usually_is_before_this_conversation_and_needs_enough_words(tmp_path):
    conn = _lab(tmp_path)
    for i in range(3):
        _conv(conn, i, T0 + i * DAY, [("A", _words(200, 20)), ("B", "Okay.")])
    on = [c for c in FL.all_conversations(conn) if c["on_line"]]
    assert FL.usual_before(on, on[1]["at"], on[1]["session_id"]) is None  # only 200 words before it
    u = FL.usual_before(on, on[2]["at"] + 1, "none")
    assert u["words"] == 600 and u["rate"] == 5.0


def test_where_they_happen_needs_enough_words(tmp_path):
    conn = _lab(tmp_path)
    for i in range(3):
        _conv(conn, i, T0 + i * DAY, [("A", _words(200, 10)), ("B", _words(20, word="sure"))])
    _conv(conn, 9, T0 + 3 * DAY, [("A", _words(150, 50)), ("B", "Okay then."), ("C", "Me too, sure.")],
          who={"A": "p_jay", "B": "p_ravi", "C": "p_asha"})
    g = FL.summary(conn)["groups"]
    assert [(x["name"], x["rate"]) for x in g["people"]] == [("Asha", round(100 * 63 / 750, 2))]  # Ravi: 150 words
    assert [x["size"] for x in g["sizes"]] == ["one"]  # the three-way one has 150 words: under the 300 shown


def test_suggestions_are_words_he_leans_on_more_than_the_others(tmp_path):
    conn = _lab(tmp_path)
    for i in range(3):
        _conv(conn, i, T0 + i * DAY, [("A", "So basically the plan is basically fine and basically done " + _words(100)),
                                      ("B", "Basically, okay. " + _words(60, word="right")), ("A", "So we wait."),
                                      ("B", "Fine.")])
    s = FL.summary(conn)["suggestions"]
    lean = {x["phrase"]: x for x in s["lean_on"]}
    assert lean["basically"] == {"phrase": "basically", "count": 9, "rate": 2.65, "others": 1.59}  # 9 in 339 words; 3 in 189
    assert s["turn_starts"][0]["word"] == "so" and s["turn_starts"][0]["share"] == 1.0
    FL.set_phrase(conn, "basically", True, "suggested")
    assert "basically" not in {x["phrase"] for x in FL.summary(conn)["suggestions"]["lean_on"]}


# ---- the check by ear -----------------------------------------------------------------------

def test_the_check_listens_where_jay_talks_most_and_compares_counts(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    sid = _conv(conn, 1, T0, [("B", _words(300)), ("A", _words(100, 10)), ("A", _words(100, 10)), ("B", _words(50))])
    w = FL.check_window(conn, sid, length=180)
    lines = rows(conn, "SELECT output FROM stage_runs WHERE stage='diarize'")
    first_jay = json.loads(lines[0]["output"])["lines"][1]["start"]
    assert w["start"] == round(first_jay - 0.5, 2) and w["jay_s"] > 70
    got = FL.save_check(conn, sid, w["start"], w["end"], by_ear=30)
    assert (got["by_app"], got["words"]) == (20, 200) and got["summary"]["share"] == round(20 / 30, 2)
    monkeypatch.setattr(PV, "_silence", lambda *a: None)
    PV.delete_session(conn, sid)
    assert FL.checks_summary(conn)["checks"] == 0  # deleting the conversation takes its checks


# ---- routes and the measured focus ---------------------------------------------------------

def _client(tmp_path):
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    return TestClient(app)


def test_the_live_cue_gets_the_owners_list_and_the_mac_switch_starts_off(tmp_path):
    conn = _lab(tmp_path)
    c = _client(tmp_path)
    c.post("/api/octo/fillers/words", json={"phrase": "basically", "on": True})
    got = c.get("/api/octo/fillers/live").json()
    assert got["mac"] is False and got["phrases"][-1] == "basically" and "you know" in got["phrases"]
    assert c.post("/api/octo/fillers/live", json={"mac": True}).json()["mac"] is True
    assert c.get("/api/octo/fillers/live").json()["mac"] is True
    assert c.post("/api/octo/fillers/live", json={"mac": False}, headers={"origin": "https://evil.example"}).status_code == 403
    from octo.phone_api import PHONE_ROUTES
    assert ("GET", "/api/octo/fillers/live") in PHONE_ROUTES and ("POST", "/api/octo/fillers/live") not in PHONE_ROUTES  # the phone keeps its own


def test_routes(tmp_path):
    conn = _lab(tmp_path)
    sid = _conv(conn, 1, T0, [("A", _words(150, 10)), ("B", "Okay.")])
    c = _client(tmp_path)
    assert c.get("/api/octo/fillers").json()["trend"]["points"][0]["fillers"] == 15
    assert c.post("/api/octo/fillers/words", json={"phrase": "plan", "on": True}).json()["trend"]["points"][0]["fillers"] == 150
    assert c.post("/api/octo/fillers/words", json={"phrase": "!!", "on": True}).status_code == 422
    assert c.post("/api/octo/fillers/words", json={"phrase": "plan"}, headers={"origin": "https://evil.example"}).status_code == 403
    chk = c.get("/api/octo/fillers/check").json()
    assert chk["session_id"] == sid and chk["window"]["start"] == 0.0
    assert c.post("/api/octo/fillers/check", json={"session_id": sid, "start_s": 0, "end_s": 200, "by_ear": 20}).json()["by_app"] == 150
    assert c.post("/api/octo/fillers/check", json={"session_id": sid, "start_s": 5, "end_s": 1, "by_ear": 2}).status_code == 422


def test_a_focus_can_carry_the_fillers_number(tmp_path):
    conn = _lab(tmp_path)
    for i in range(3):
        _conv(conn, i, T0 + i * DAY, [("A", _words(400, 20)), ("B", "Okay.")])
    for i in range(3, 5):
        _conv(conn, i, T0 + i * DAY, [("A", _words(400, 40)), ("B", "Okay.")])
    f = FO.start(conn, "Pause instead of um", now=T0 + 2.5 * DAY, measure="fillers")
    v = FO.view(conn, f)
    assert v["measure"] == "fillers" and v["numbers"]["since"]["value"] == 2.5 and v["numbers"]["before"]["value"] == 5.0
    with pytest.raises(FO.FocusError):
        FO.start(conn, "x", measure="smiles")
    assert FO.summary(conn)["active"]["numbers"]["since"]["conversations"] == 2


def test_the_recorders_cues_land_on_the_lines_they_followed(tmp_path):
    """B20 (rec-24): a cue in a boot's manifest is placed by that boot's clock, kept only inside the conversation,
    and put on the line under the moment just before it (it comes ~0.2 s after the phrase)."""
    import json as _json
    from octo.db import connect
    from octo.fillers import recorder_cues
    conn = connect(tmp_path / "t.db")
    clock_ms, at_ms = 1_800_000_000_000, 10_000  # the Mac set the clock 10 s into the boot
    lines = [_json.dumps(d) for d in ({"boot": 5}, {"clock_ms": clock_ms, "at_ms": at_ms},
                                       {"filler": "you know", "score": 0.97, "at_ms": 61_000},    # 41 s in: inside
                                       {"filler": "you know", "score": 0.99, "at_ms": 400_000})]  # after the end
    conn.execute("INSERT INTO device_boots (boot, manifest) VALUES (5, ?)", ("\n".join(lines) + "\n{cut",))
    start = (clock_ms - at_ms) / 1000  # the boot's 0 ms on the wall clock
    sess = {"source": "device", "started_at": start + 20, "start_s": 0.0, "end_s": 300.0}
    ln = [{"id": 1, "start": 30.0, "end": 39.0}, {"id": 2, "start": 39.5, "end": 41.0}, {"id": 3, "start": 45.0, "end": 50.0}]
    got = recorder_cues(conn, sess, ln)
    assert got == [{"at": 41.0, "score": 0.97, "filler": "you know", "line": 2}]
    assert recorder_cues(conn, {**sess, "source": "phone"}, ln) is None
    assert recorder_cues(conn, {**sess, "started_at": start + 500}, ln) is None
