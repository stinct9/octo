"""Tone of voice (B27, octo/tone.py): pitch range and loudness from the stored audio, against the owner's usual."""

import json

import numpy as np

from octo import HOME
from octo import audio as A
from octo import delivery as D
from octo import measures as M
from octo import tone as T
from octo.db import load_json, row
from test_delivery import DAY, T0, _conv, _lab, _sess, _talk, _w

R = A.RATE


def _glide(seconds: float, hz: float = 120.0, semitones: float = 0.0, amp: float = 0.1) -> np.ndarray:
    """A tone gliding evenly over `semitones` around `hz` (0: steady)."""
    t = np.arange(int(seconds * R)) / R
    f = hz * 2 ** (semitones * (t / seconds - 0.5) / 12)
    return (amp * np.sin(2 * np.pi * np.cumsum(f) / R)).astype(np.float32)


# ---- the two numbers on samples ------------------------------------------------------------------

def test_pitch_is_found_on_tones_and_not_on_noise():
    for hz in (80, 120, 220, 350):
        f0, _ = T.frames(_glide(12, hz))
        assert abs(np.nanmedian(f0) - hz) < 0.5 and np.mean(~np.isnan(f0)) > 0.95
    noise = (0.1 * np.random.default_rng(1).standard_normal(12 * R)).astype(np.float32)
    assert np.mean(~np.isnan(T.frames(noise)[0])) < 0.02
    assert T.pitch_range(T.frames(noise)[0]) is None  # under 10 voiced seconds: no number
    assert T.pitch_range(T.frames(_glide(30))[0]) < 0.05  # a steady voice: no range
    # an even glide over 10 semitones: 10th to 90th percentile is 8 of them
    assert abs(T.pitch_range(T.frames(_glide(30, semitones=10))[0]) - 8.0) < 0.1


def test_level_is_in_db_and_the_drift_below_5_hz_is_filtered_out():
    loud, soft = _glide(20, amp=0.1), _glide(20, amp=0.01)
    lv = lambda x: T.level(*T.frames(A.highpass(x)))
    assert abs(lv(loud) - lv(soft) - 20) < 0.1
    drift = loud + 0.5 * np.sin(2 * np.pi * 2 * np.arange(len(loud)) / R).astype(np.float32)
    assert abs(lv(drift) - lv(loud)) < 0.5  # the board mic's wander, 14 dB over the voice, is gone


# ---- whose frames, and pushed back ---------------------------------------------------------------

def _said(spk: str, text: str, t: float) -> list[dict]:
    return [{**w, "spk": spk} for w in _w(text, t, me=spk == "me")]


def test_pushed_back_is_his_turn_after_their_question_or_disagreement():
    words = (_said("B", "when will it ship?", 0) + _said("me", "next week I think", 2)          # a question
             + _said("B", "no that is too late", 5) + _said("me", "then we cut scope", 7.5)      # disagreement
             + _said("B", "okay sounds good", 10) + _said("me", "and the tests too", 12)        # neither
             + _said("B", "but why?", 15) + _said("me", "because", 25))                          # too late to be an answer
    sp = T.spans(words)
    assert [round(a) for a, _ in sp["pushed"]] == [2, 8]
    assert T.pushes_back("I don't think so") and T.pushes_back("No.") and not T.pushes_back("Nothing else")


def test_crosstalk_is_nobodys():
    words = _said("me", "one two three four", 0) + _said("B", "yes", 0.4)
    sp = T.spans(words)
    assert all(not (a < 0.72 and b > 0.4) for a, b in sp["me"])  # B's word is cut out of his
    assert sp["them"] == []


# ---- in a lab --------------------------------------------------------------------------------------

ASK = " ".join(["so tell me about the launch and the plan for it"] * 4) + "?"  # 40 words of Asha's, then a question
ON = "okay and what about the rest of the team and the others in it " * 3 + "then"  # neither


def _audio(conn, sid: str, semitones: float, pushed_db: float = 0.0, me_amp: float = 0.05) -> None:
    """Writes the conversation's audio: the owner a glide on every line, Asha a steady 220 Hz; the owner's first line
    (the answer to her question) `pushed_db` louder."""
    dz = load_json(row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='diarize'", sid)["output"])
    end = max(ln["end"] for ln in dz["lines"]) + 1
    x = np.zeros(int(end * R), np.float32)
    first = True
    for ln in dz["lines"]:
        a, n = int(ln["start"] * R), int((ln["end"] - ln["start"]) * R)
        if ln["speaker"] == "A":
            amp = me_amp * (10 ** (pushed_db / 20) if first else 1)
            x[a:a + n] = _glide(n / R, 130, semitones, amp)[:n]
            first = False
        else:
            x[a:a + n] = _glide(n / R, 220, 0, 0.05)[:n]
    rel = f"audio/lab/{sid}.flac"
    A.write(HOME / rel, x)
    conn.execute("UPDATE recordings SET path=? WHERE id=(SELECT recording_id FROM sessions WHERE id=?)", (rel, sid))


def _voice(conn, sid) -> dict:
    return {r["measure"]: r for r in D.for_session(conn, _sess(conn, sid))["voice"]}


def test_voice_rows_against_his_usual_and_kept_after_the_audio_goes(tmp_path):
    conn = _lab(tmp_path)
    script = [("B", ASK), ("A", _talk(150)), ("B", ON), ("A", _talk(150))]
    for i, st in enumerate((4, 5, 6, 5, 4)):
        _audio(conn, _conv(conn, i, T0 + i * DAY, script), st)
    wide = _conv(conn, 9, T0 + 9 * DAY, script)
    _audio(conn, wide, 12, pushed_db=6.0, me_amp=0.2)
    v = _voice(conn, wide)
    assert list(v) == list(T.VOICE)
    assert v["pitch_range"]["state"] == "high" and v["pitch_range"]["conversations"] == 5
    assert abs(v["pitch_range"]["usual"] - 4.0) < 0.1  # the median conversation glides 5 semitones: reads as 4
    assert v["loudness"]["value"] > 10 and v["loudness"]["state"] == "high"  # 0.2 against her 0.05, louder than his usual 0
    assert abs(v["pushed_loudness"]["value"] - 6) < 0.5 and v["pushed_loudness"]["state"] == "high"
    assert v["pushed_pitch"]["state"] == "about"  # his answer and the rest glide the same
    got = D.for_session(conn, _sess(conn, wide))
    assert got["pushed_turns"] == 1 and got["voice_s"] > 90
    assert M.for_session(conn, "pitch_range", wide) == v["pitch_range"]["value"] and "pitch_range" in M.MEASURES

    kept = json.loads(T.CACHE.read_text())
    assert set(kept) == {f"ses_20261002_{n:08x}" for n in (0, 1, 2, 3, 4, 9)}
    # 60 days on: the audio's deleted, the numbers it gave stay, and the usual with them
    conn.execute("UPDATE recordings SET audio_deleted_at=1")
    for p in (HOME / "audio/lab").glob("ses_20261002_*.flac"):
        p.unlink()
    assert _voice(conn, wide)["pitch_range"]["value"] == v["pitch_range"]["value"]
    # one never measured before its audio went says so
    late = _conv(conn, 10, T0 + 10 * DAY, script)
    conn.execute("UPDATE recordings SET audio_deleted_at=1, path='audio/lab/gone.flac' WHERE id=?", ("rec_20261002_0000000a",))
    assert {r["state"] for r in _voice(conn, late).values()} == {"no_audio"}
    # deleting a conversation drops its numbers the next time any are kept
    conn.execute("UPDATE sessions SET status='set_aside' WHERE id=?", (wide,))
    new = _conv(conn, 11, T0 + 11 * DAY, script)
    _audio(conn, new, 5)
    _voice(conn, new)
    assert wide not in json.loads(T.CACHE.read_text())


def test_alone_has_a_pitch_range_but_no_loudness_against_the_others(tmp_path):
    conn = _lab(tmp_path)
    sid = _conv(conn, 0, T0, [("A", _talk(400))])
    _audio(conn, sid, 5)
    v = _voice(conn, sid)
    assert v["pitch_range"]["value"] is not None and v["pitch_range"]["state"] == "no_usual"
    assert v["loudness"]["value"] is None and v["loudness"]["state"] == "unmeasured"
    assert v["pushed_pitch"]["value"] is None


def test_a_page_measures_for_its_budget_and_a_bad_file_is_measured_again(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    sids = [_conv(conn, i, T0 + i * DAY, [("B", ASK), ("A", _talk(150))]) for i in range(3)]
    for s in sids:
        _audio(conn, s, 5)
    T.CACHE.write_text('{"x": 1, "ses_20261002_00000000": {"key": 3}}')
    cs = [D.conversation(conn, _sess(conn, s), D.lists(conn)) for s in sids]
    T.fill(conn, cs, budget_s=0.0)  # the first is always measured; the rest wait for the next read
    assert cs[0]["pitch_range"] is not None and cs[1]["pitch_range"] is None and not cs[1]["voice_gone"]
    assert set(json.loads(T.CACHE.read_text())) == {sids[0]}
    D.measure_voice(conn)  # `octo process`
    assert set(json.loads(T.CACHE.read_text())) == set(sids)


def test_a_hair_off_a_tight_usual_is_about_usual():
    c = {"words": 400, "on_line": True, "size": "one", "loudness": 0.4, "pushed_loudness": 1.5}
    before = [{"words": 400, "on_line": True, "size": "one", "loudness": 0.0, "pushed_loudness": 0.0} for _ in range(5)]
    assert D.row_for(c, before, "loudness")["state"] == "about"  # 0.4 dB: under what an ear tells apart
    assert D.row_for(c, before, "pushed_loudness")["state"] == "high"


def test_spans_less_others_walk_both_lists_once():
    me = [(0.0, 2.0), (3.0, 6.0), (7.0, 8.0)]
    them = [(1.0, 1.5), (2.5, 3.5), (5.0, 7.5), (9.0, 10.0)]
    assert T._minus(me, them) == [(0.0, 1.0), (1.5, 2.0), (3.5, 5.0), (7.5, 8.0)]
    assert T._minus(me, []) == me and T._minus([], them) == []
