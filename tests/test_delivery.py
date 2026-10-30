"""How the owner showed up (octo/delivery.py, B10): each counter, their usual and the state against their own spread,
the hedge list, and a focus that carries one of the new measures."""

import json
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import delivery as D
from octo import focus as FO
from octo import measures as M
from octo.api import make_router
from octo.db import connect
from octo.stages import utterances

DAY = 86400
T0 = datetime(2026, 10, 2, 10, 0).timestamp()


def _w(text: str, t: float = 0.0, me: bool = True, step: float = 0.4) -> list[dict]:
    """Timed words, one every `step` seconds from t."""
    return [{"w": w, "start": round(t + i * step, 2), "end": round(t + i * step + step * 0.8, 2), "me": me}
            for i, w in enumerate(text.split())]


# ---- the counters --------------------------------------------------------------------------------

def test_restarts_are_repeats_and_broken_off_words_not_ordinary_doubles():
    skip = {"um", "you know"}
    run = _w("I, I think we should, we should go. It was very very good. Um um, you know you know, "
             "we sh- we need it. So I, I, I said no no.")
    got = D.restarts(run, skip)
    assert len(got) == 4  # "I, I" · "we should, we should" · "sh-" · "I, I, I" (once); not very very, um um, no no
    assert got[0]["start"] == run[0]["start"] and got[0]["end"] == run[1]["end"]


def test_pauses_are_inside_his_sentences_with_nobody_else_in_them():
    words = (_w("So the plan is", 0.0) + _w("to ship on Friday.", 3.0)  # 1.5 s mid-sentence: a pause
             + _w("Then we test.", 7.0)  # after a full stop: not a pause
             + _w("Right", 9.5, me=False) + _w("and then", 10.2) + _w("we go", 11.8))  # someone between; then 0.9 s
    t = D.timing(words, set())
    assert [round(p["end"] - p["start"], 1) for p in t["pauses"]] == [1.5]
    # Stretches: "So the plan is" · "to ship on Friday." · "Then we test." · "and then we go" (0.9 s < PAUSE_S)
    assert t["longest"]["start"] == 10.2 and round(t["longest"]["end"], 2) == 12.52
    assert t["timed_words"] == 4 + 4 + 3 + 4
    assert round(t["speak_s"], 2) == round(1.52 + 1.52 + 1.12 + (12.52 - 10.2), 2)


def test_words_go_to_whoever_the_line_says_with_jays_corrections():
    words = [{"w": "hello", "start": 0.0, "end": 0.3}, {"w": "there", "start": 0.4, "end": 0.7},
             {"w": "hi", "start": 2.0, "end": 2.3}, {"w": "stray", "start": 9.0, "end": 9.2}]
    lines = [{"speaker": "A", "start": 0.0, "end": 0.7}, {"speaker": "B", "start": 2.0, "end": 2.3}]
    got = D.timed_words({"segments": [{"words": words[:3]}], "rejected_as_silence": [{"words": words[3:]}]}, lines, {"A"})
    assert [w["me"] for w in got] == [True, True, False, False]  # the stray word is in nobody's line
    assert D.timed_words({"segments": []}, lines, {"A"}) is None  # no word times: not measured


# ---- conversations in a lab ------------------------------------------------------------------------

def _lab(tmp_path):
    conn = connect(tmp_path / "t.db")
    for pid, name, me in (("p_jay", "Sam", 1), ("p_asha", "Asha", 0), ("p_ravi", "Ravi", 0)):
        conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES (?, ?, ?, 0)", (pid, name, me))
    return conn


def _conv(conn, n: int, at: float, script: list[tuple[str, str]], timed: bool = True, model: str = "Qwen/Qwen3-ASR-1.7B",
          who: dict | None = None) -> str:
    """A conversation from (letter, text): words 0.4 s apart, a line per entry, 1.2 s between lines.
    A "|" in the owner's text is a 1.5 s silence mid-sentence."""
    who = who or {"A": "p_jay", "B": "p_asha"}
    sid, rec = f"ses_20261002_{n:08x}", f"rec_20261002_{n:08x}"
    words, t = [], 0.0
    for spk, text in script:
        for part in text.split("|"):
            ws = _w(part, t)
            words += [{**w, "speaker": spk, "sure": True, "p": 1.0} for w in ws]
            t = ws[-1]["end"] + 1.5
        t += -1.5 + 1.2
    lines = utterances([dict(w) for w in words], pause_s=1.0)
    talk: dict[str, float] = {}
    for ln in lines:
        talk[ln["speaker"]] = talk.get(ln["speaker"], 0.0) + ln["end"] - ln["start"]
    dz = {"speakers": sorted(talk), "talk_s": talk, "lines": lines, "overlapping_turns": [],
          "turns": [{"speaker": ln["speaker"], "start": ln["start"], "end": ln["end"]} for ln in lines]}
    tr = {"language": "en", "model": model,
          "segments": [{"words": [{"w": w["w"], "start": w["start"], "end": w["end"]} for w in words]}] if timed else []}
    conn.execute("INSERT INTO recordings (id, source, path, started_at, duration_s, created_at) VALUES (?, 'device', 'x.flac', ?, ?, 0)",
                 (rec, at, t + 1))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) VALUES (?, ?, 0, ?, 10, 'active', 0)",
                 (sid, rec, t + 1))
    for stage, out in (("transcribe", tr), ("diarize", dz)):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                     "VALUES (?, ?, ?, 'h', 'done', 1, ?, 0)", (f"{stage}_{sid}", stage, sid, json.dumps(out)))
    for letter, pid in who.items():
        conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, diarize_run) VALUES (?, ?, ?, 'confirmed', ?)",
                     (sid, letter, pid, f"diarize_{sid}"))
    return sid


def _sess(conn, sid):
    from octo.db import row
    return row(conn, "SELECT s.*, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", sid)


VOCAB = "we ship the plan on time and then test it with Asha".split()  # never a word twice running


def _talk(n_words: int, um_every: int = 0, pause_every: int = 0) -> str:
    """n of the owner's words, with an um every `um_every` and a mid-sentence pause every `pause_every`."""
    out = []
    for i in range(n_words):
        out.append("um" if um_every and (i + 1) % um_every == 0 else VOCAB[i % len(VOCAB)])
        if pause_every and (i + 1) % pause_every == 0 and i + 1 < n_words:
            out.append("|")
    return " ".join(out) + "."


def _rows(conn, sid) -> dict:
    return {r["measure"]: r for r in D.for_session(conn, _sess(conn, sid))["rows"]}


def test_state_is_against_his_own_spread_once_there_is_history(tmp_path):
    conn = _lab(tmp_path)
    for i, every in enumerate((20, 25, 20, 25, 20)):  # five before: 4–5 fillers per 100 words
        _conv(conn, i, T0 + i * DAY, [("B", "Shall we start?"), ("A", _talk(400, every, pause_every=50))])
    few = _conv(conn, 10, T0 + 10 * DAY, [("B", "Go on."), ("A", _talk(400, 100, pause_every=50))])
    many = _conv(conn, 11, T0 + 11 * DAY, [("B", "Go on."), ("A", _talk(400, 5, pause_every=50))])
    r = _rows(conn, few)
    assert r["fillers"]["value"] == 1.0 and r["fillers"]["state"] == "low" and r["fillers"]["conversations"] == 5
    assert r["fillers"]["usual"] == 4.6 and (r["fillers"]["lo"], r["fillers"]["hi"]) == (4.0, 5.0)
    assert r["pauses"]["value"] == 1.75 and r["pauses"]["state"] == "about"  # 7 pauses in 400 words, as before
    assert r["restarts"]["value"] == 0 and r["hedges"]["value"] == 0
    assert 150 <= r["pace"]["value"] < 151  # a word every 0.4 s
    assert r["longest"]["value"] == 19.9  # 50 words
    assert _rows(conn, many)["fillers"]["state"] == "high"
    assert _rows(conn, many)["fillers"]["conversations"] == 6  # the one before it counts; it doesn't count itself


def test_too_short_no_usual_off_the_line_and_unmeasured(tmp_path):
    conn = _lab(tmp_path)
    first = _conv(conn, 0, T0, [("A", _talk(400, 20))])
    assert {r["state"] for r in _rows(conn, first).values()} == {"no_usual"}
    short = _conv(conn, 1, T0 + DAY, [("A", _talk(150, 20))])
    assert _rows(conn, short)["fillers"]["state"] == "short"
    whisper = _conv(conn, 2, T0 + 2 * DAY, [("A", _talk(400, 20))], model="mlx-community/whisper-large-v3")
    got = D.for_session(conn, _sess(conn, whisper))
    assert got["rows"][0]["state"] == "off" and "Whisper" in got["reason"]
    untimed = _conv(conn, 3, T0 + 3 * DAY, [("A", _talk(400, 20))], timed=False)
    r = _rows(conn, untimed)
    assert r["pauses"]["state"] == "unmeasured" and r["pauses"]["value"] is None and r["fillers"]["value"] == 5.0


def test_groups_higher_is_said_only_on_a_group_and_only_where_true(tmp_path):
    conn = _lab(tmp_path)
    group = {"A": "p_jay", "B": "p_asha", "C": "p_ravi"}
    hi = "Hi there, how are you doing today, and what did you make of the plan we sent round on Monday?"
    for i in range(3):
        _conv(conn, i, T0 + i * DAY, [("B", hi), ("A", _talk(400, 50))])
        _conv(conn, 10 + i, T0 + i * DAY + 3600, [("B", hi),
                                                 ("C", hi), ("A", _talk(400, 10))], who=group)
    g = _conv(conn, 20, T0 + 5 * DAY, [("B", hi), ("C", hi),
                                       ("A", _talk(400, 10))], who=group)
    one = _conv(conn, 21, T0 + 5 * DAY + 60, [("B", hi), ("A", _talk(400, 10))])
    assert _rows(conn, g)["fillers"]["groups_higher"] and not _rows(conn, g)["pace"]["groups_higher"]
    assert not _rows(conn, one)["fillers"]["groups_higher"]


def test_hedges_are_his_own_list(tmp_path):
    conn = _lab(tmp_path)
    sid = _conv(conn, 0, T0, [("A", "I think it is maybe fine, just a bit late. " + _talk(100))])
    got = D.for_session(conn, _sess(conn, sid))
    assert got["hedges"]["by_phrase"] == {"i think": 1, "maybe": 1, "just": 1, "a bit": 1}
    assert D.set_hedge(conn, "Kind of", True)[-1] == "kind of"
    assert "just" not in D.set_hedge(conn, "just", False)
    assert D.for_session(conn, _sess(conn, sid))["hedges"]["by_phrase"] == {"i think": 1, "maybe": 1, "a bit": 1}


def test_hedge_route_and_a_focus_that_carries_a_new_measure(tmp_path):
    conn = _lab(tmp_path)
    for i in range(3):
        _conv(conn, i, T0 + i * DAY, [("A", _talk(300, pause_every=30))])
    later = _conv(conn, 5, T0 + 5 * DAY, [("A", _talk(300, pause_every=100))])
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    assert c.post("/api/octo/delivery/hedges", json={"phrase": "perhaps"}).json()["phrases"][-1] == "perhaps"
    assert c.post("/api/octo/delivery/hedges", json={"phrase": "!!"}).status_code == 422
    assert c.post("/api/octo/focus", json={"text": "Finish the sentence", "measure": "pauses"}).status_code == 200
    f = FO.active(conn)
    conn.execute("UPDATE focus SET started_at=? WHERE id=?", (T0 + 4 * DAY, f["id"]))
    n = M.since_before(conn, "pauses", T0 + 4 * DAY)
    assert n["before"]["value"] == 3.0 and n["before"]["conversations"] == 3  # 9 pauses in 300 words, each time
    assert n["since"]["value"] == 0.67 and M.for_session(conn, "pauses", later) == 0.67
    assert c.get(f"/api/octo/sessions/{later}").json()["delivery"]["rows"][1]["measure"] == "pauses"


# ---- listening (B13) -----------------------------------------------------------------------------

def _by(spk: str, text: str, t: float) -> list[dict]:
    """Timed words by one speaker; A is the owner."""
    return [{**w, "spk": spk} for w in _w(text, t, me=spk == "A")]


def test_patience_is_the_wait_when_he_takes_the_turn_and_talking_over_is_not():
    words = (_by("B", "what do you think", 0.0)      # ends 1.52
             + _by("A", "I think we wait", 2.32)      # 0.8 s after: patience
             + _by("B", "fine and then", 4.0)         # ends 4.92
             + _by("A", "we ship it", 5.02)           # 0.1 s: reads as zero
             + _by("B", "but the vendor said", 6.3)   # ends 7.82
             + _by("A", "no wait please", 7.6)        # 0.22 s before they finish: talking over
             + _by("B", "okay so then", 8.5) + _by("A", "right", 9.52))  # 0.1 s early: zero, not talking over
    got = D.listening(words)
    assert [g["gap"] for g in got["gaps"]] == [0.8, 0.0, 0.0] and got["over"] == 1
    assert all(g["spk"] == "B" for g in got["gaps"])
    # Two words of his over them while they carry on is a backchannel, not taking the floor.
    assert D.listening(_by("B", "but the vendor said", 0.0) + _by("A", "no wait", 1.3) + _by("B", "it is late", 2.2))["over"] == 0


def test_their_longest_stretch_ends_when_he_takes_the_floor_not_at_a_backchannel():
    words = (_by("B", "so the plan is to ship", 0.0) + _by("A", "mm", 2.6)   # a backchannel: B carries on
             + _by("B", "on Friday and then test", 3.2)                     # B: 0.0 to 4.92
             + _by("A", "can I stop you there", 5.2)                        # he takes the floor: B cut off
             + _by("C", "yes", 7.6) + _by("B", "the vendor", 8.4))          # C's one word, but B isn't C: a change
    got = D.listening(words)
    assert got["longest"]["B"]["s"] == 5.1 and got["longest"]["B"]["start"] == 0.0
    assert set(got["longest"]) == {"B", "C"}
    fl = [f["spk"] for f in D.floors(words)]
    assert fl == ["B", "me", "C", "B"] and got["changes"] == 3
    assert round(got["span_s"], 2) == 9.12


def test_listening_rows_in_the_glance_and_none_when_alone(tmp_path):
    conn = _lab(tmp_path)
    ask = "Shall we start then, and what did you make of the plan we sent round on Monday?"  # 6.7 s of Asha
    for i in range(5):
        _conv(conn, i, T0 + i * DAY, [("B", ask), ("A", _talk(150)), ("B", ask), ("A", _talk(150)), ("B", ask), ("A", _talk(100))])
    sid = _conv(conn, 9, T0 + 9 * DAY, [("B", ask), ("A", _talk(400))])
    got = D.for_session(conn, _sess(conn, sid))
    r = {x["measure"]: x for x in got["listening"]}
    assert list(r) == ["patience", "turns", "their_longest"]
    assert r["patience"]["value"] == 1.2 and r["patience"]["usual"] == 1.2  # the 1.2 s between lines
    assert r["patience"]["state"] == "about" and r["turns"]["state"] == "low"  # one change against five
    assert got["taken"] == 1 and got["moments"]["their_longest"][0]["name"] == "Asha"
    assert M.for_session(conn, "their_longest", sid) == r["their_longest"]["value"]
    alone = _conv(conn, 10, T0 + 10 * DAY, [("A", _talk(400))])
    assert D.for_session(conn, _sess(conn, alone))["listening"] == []


def test_every_conversations_record_is_kept_until_what_it_is_made_from_changes(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    sid = _conv(conn, 0, 1_790_000_000, [("B", "So what's the plan?"), ("A", _talk(120, um_every=10))])
    made = []
    real = D.conversation
    monkeypatch.setattr(D, "conversation", lambda *a, **k: made.append(1) or real(*a, **k))
    first = D.all_conversations(conn)
    assert len(made) == 1 and first[0]["fillers"] == 12
    first[0]["fillers"] = 0  # a caller changing its copy doesn't change the kept record
    assert D.all_conversations(conn)[0]["fillers"] == 12 and len(made) == 1  # nothing changed: read once
    # the owner says A isn't them: who's who changed, so it's made again
    conn.execute("UPDATE session_speakers SET person_id='p_ravi' WHERE session_id=? AND speaker='A'", (sid,))
    assert D.all_conversations(conn)[0]["words"] == 0 and len(made) == 2
    # a correction (here, any new one) makes it again too
    conn.execute("INSERT INTO corrections (id, session_id, diarize_run, kind, data, created_at) VALUES "
                 "('fix_1', ?, ?, 'merge', '{\"from\": \"Z\", \"into\": \"B\"}', 0)", (sid, f"diarize_{sid}"))
    D.all_conversations(conn)
    assert len(made) == 3
    conn.execute("UPDATE sessions SET status='set_aside' WHERE id=?", (sid,))
    assert D.all_conversations(conn) == [] and sid not in D._RECORDS
