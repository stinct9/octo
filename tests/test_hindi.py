"""Hindi and mixed conversations (4 Oct): two drafts merged by Claude, put back on Qwen's timings."""

import json
from dataclasses import replace

from octo import hindi as H
from octo import planning
from octo.asr import language_paused, other_language
from octo.db import rows
from tests.test_octo import lab  # noqa: F401 -- the pipeline fixture

import octo.pipeline as P


def words(*ws):
    return [{"w": w, "start": float(i), "end": i + 0.8} for i, w in enumerate(ws)]


def test_kept_words_keep_their_timings_and_new_ones_share_the_gap():
    got = H.align(words("haan", "मैं", "कल", "office", "जाऊँगा"), "हाँ, मैं कल ऑफिस नहीं office जाऊँगा?")
    assert [w["w"] for w in got] == ["हाँ,", "मैं", "कल", "ऑफिस", "नहीं", "office", "जाऊँगा?"]
    by = {w["w"]: w for w in got}
    assert (by["मैं"]["start"], by["office"]["start"], by["जाऊँगा?"]["start"]) == (1.0, 3.0, 4.0)  # punctuation ignored
    assert by["हाँ,"]["start"] == 0.0  # before the first kept word: from the segment's start
    assert 1.8 <= by["ऑफिस"]["start"] < by["नहीं"]["start"] < 3.0  # between कल (ends 2.8) and office
    assert [w["start"] for w in got] == sorted(w["start"] for w in got)


def test_unclear_marks_the_line_unsure_and_empty_text_drops_the_segment():
    got = H.align(words("a", "b", "c"), "a [unclear] c")
    assert [w.get("p") for w in got] == [None, 0.0, None]
    assert H.align(words("a", "b"), "  ") == []


def test_chunks_are_consecutive_segments_up_to_a_length():
    segs = [{"id": i, "start": s, "end": s + 5} for i, s in enumerate([0, 10, 30, 50, 100])]
    assert [[s["id"] for s in c] for c in H.chunks(segs, 40)] == [[0, 1, 2], [3], [4]]


def test_merge_rewrites_each_segment_keeps_what_claude_skipped_and_drops_what_it_emptied():
    qwen = [{"id": 0, "start": 0.0, "end": 2.8, "text": "मै कल", "words": words("मै", "कल")},
            {"id": 1, "start": 5.0, "end": 5.8, "text": "हम्म", "words": [{"w": "हम्म", "start": 5.0, "end": 5.8}]},
            {"id": 2, "start": 9.0, "end": 9.8, "text": "okay", "words": [{"w": "okay", "start": 9.0, "end": 9.8}]}]
    whisper = [{"id": 0, "start": 0.0, "end": 2.0, "text": "मैं कल", "words": [
        {"w": "मैं", "start": 0.0, "end": 0.8, "p": 0.9}, {"w": "कल", "start": 1.0, "end": 1.8, "p": 0.2}]}]
    prompts = []

    def ask(model, effort, system, prompt, schema):
        prompts.append(prompt)
        return {"segments": [{"id": 0, "text": "मैं कल?"}, {"id": 1, "text": ""}]}

    segs, info = H.merge(qwen, whisper, {**H.HINDI, "chunk_s": 60}, ask)
    assert [s["text"] for s in segs] == ["मैं कल?", "okay"]
    assert info["calls"] == 1 and info["model"] == "claude-opus-5-5"
    assert "कल(?)" in prompts[0] and "#1 [5.0] हम्म" in prompts[0]  # Whisper's unsure word marked; targets numbered


def test_merged_conversations_are_used_and_kannada_stays_paused():
    hi = {"language": "hi", "language_votes": {"en": 0.123, "hi": 0.834, "kn": 0.0, "gu": 0.001}}
    assert other_language(hi) == "hi"
    assert language_paused({**hi, "merged": {"model": "claude-opus-5-5"}}) is None
    assert other_language({"language": "en", "language_votes": {"en": 0.281, "hi": 0.28, "kn": 0.005, "gu": 0.002}}) == "hi"
    assert other_language({"language": "kn", "language_votes": None}) == "kn"
    assert other_language({"language": "en", "language_votes": None}) is None


def test_only_hindi_conversations_get_the_hindi_path_and_it_settles(lab):  # noqa: F811
    seen = []

    def tr(inputs, params):
        hindi = inputs["start_s"] < 5  # the fixture's first conversation is the Hindi one
        seen.append((inputs["start_s"], "hindi" in params))
        out = {"language": "hi" if hindi else "en", "model": "fake", "segments": [], "rejected_as_silence": [],
               "language_votes": {"en": 0.1, "hi": 0.9} if hindi else {"en": 0.9, "hi": 0.1}}
        if hindi and "hindi" in params:
            return {**out, "merged": {"model": params["hindi"]["merge_model"]}}
        return {**out, "paused": language_paused(out)} if language_paused(out) else out

    lab["stages"]["transcribe"] = replace(lab["stages"]["transcribe"], fn=tr, dynamic=planning.hindi_input)
    P.add_recording(lab["conn"], lab["wav"], "bench")
    lab["run"]()  # language heard: Hindi paused, English done
    assert lab["run"]() == {"done": 0, "failed": 0, "skipped": 0, "busy": 0}  # J2: no merge before the owner is in it
    assert sorted(seen) == [(1.0, False), (10.0, False)]
    lab["mp"].setattr(planning, "jay_talk_s", lambda conn, sid: 25.0)  # he confirms himself (25 s of talk)
    lab["run"]()  # the Hindi one again, now with the Hindi settings
    lab["mp"].setattr(planning, "jay_talk_s", lambda conn, sid: 0.0)  # merged: stays merged whatever happens
    assert lab["run"]() == {"done": 0, "failed": 0, "skipped": 0, "busy": 0}  # and then it's settled
    assert sorted(seen) == [(1.0, False), (1.0, True), (10.0, False)]  # English ran once, never with them
    outs = [json.loads(r["output"]) for r in rows(lab["conn"], "SELECT output FROM stage_runs WHERE is_current=1 AND stage='transcribe'")]
    assert sorted(bool(o.get("merged")) for o in outs) == [False, True]


def test_jays_talk_counts_only_speakers_confirmed_as_him_in_the_current_run(tmp_path):
    from octo.db import connect
    from test_corrections import DZ
    conn = connect(tmp_path / "j.db")
    conn.execute("INSERT INTO recordings (id, path, source, duration_s, created_at) VALUES ('r', 'x', 'phone', 10, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s', 'r', 0, 10, 9, 0)")
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz', 'diarize', 's', 'h', 'done', 1, ?, 0)", (json.dumps(DZ),))
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('j', 'Sam', 1, 0), ('g', 'Nisha', 0, 0)")
    assert planning.jay_talk_s(conn, "s") == 0.0
    conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, diarize_run) VALUES "
                 "('s', 'A', 'j', 'suggested', 'dz'), ('s', 'B', 'g', 'confirmed', 'dz')")
    assert planning.jay_talk_s(conn, "s") == 0.0  # only suggested
    conn.execute("UPDATE session_speakers SET state='confirmed' WHERE speaker='A'")
    assert planning.jay_talk_s(conn, "s") == 4.0


# ---- 4 Oct review, batch E ----------------------------------------------------------------------

def test_unclear_with_a_danda_is_still_unclear():  # M24
    words = [{"w": "हाँ", "start": 0.0, "end": 0.5}, {"w": "ठीक", "start": 0.5, "end": 1.0}]
    out = H.align(words, "हाँ [unclear]।")
    assert [w.get("p") for w in out] == [None, 0.0]


def test_a_segment_claude_didnt_return_is_counted_and_marked_unsure():  # M24
    seg = lambda i, t: {"id": i, "start": float(i), "end": i + 1.0, "text": t, "words": [{"w": t, "start": float(i), "end": i + 1.0}]}
    qwen = [seg(0, "नमस्ते"), seg(1, "ठीक")]
    params = {**H.HINDI, "chunk_s": 600}
    out, merged = H.merge(qwen, [], params, lambda *a: {"segments": [{"id": 0, "text": "नमस्ते जी"}]})
    assert merged["kept_draft"] == [1]
    assert out[1]["text"] == "ठीक" and out[1]["words"][0]["p"] == 0.2  # its line shows ⚠
    assert all("p" not in w for w in out[0]["words"])


def test_quotes_match_whole_words_and_devanagari_vowel_signs():  # M24
    from octo.insight import verify_quote
    lines = {1: {"id": 1, "start": 0.0, "speaker": "A", "text": "I know that, क़िला देखा"},
             2: {"id": 2, "start": 5.0, "speaker": "A", "text": "मेरा नाम"}}
    assert verify_quote({"line": 1, "text": "know that"}, lines)
    assert not verify_quote({"line": 1, "text": "no"}, lines)  # not inside "know"
    assert not verify_quote({"line": 2, "text": "मीरा"}, lines)  # another vowel sign: another word
    assert verify_quote({"line": 1, "text": "क़िला"}, lines)  # decomposed nukta vs precomposed U+095B
    assert verify_quote({"line": 1, "text": "I know … देखा"}, lines)


def test_language_unsure_when_jays_four_get_under_half_the_vote():  # M25
    from octo.asr import language_unsure, top_languages
    assert language_unsure({"language_votes": {"en": 0.2, "hi": 0.15, "kn": 0.05, "gu": 0.0}})
    assert not language_unsure({"language_votes": {"en": 0.6, "hi": 0.1, "kn": 0.0, "gu": 0.0}})
    assert not language_unsure({"language": "en"})  # no votes stored: nothing to say
    assert top_languages([{"en": 0.3, "ur": 0.5, "hi": 0.15, "ar": 0.05}], 2) == [{"ur": 0.5, "en": 0.3}]


def test_real_routing_with_the_models_faked(tmp_path, monkeypatch):  # M25: stages.transcribe itself
    import mlx.core as mx
    import mlx_whisper.audio as WA
    import numpy as np
    from mlx_whisper.transcribe import ModelHolder

    from octo import audio as A
    from octo import claude_code as CC
    from octo import stages as S

    wav = tmp_path / "c.wav"
    A.write(wav, np.zeros(16000 * 40, np.float32))
    heard = {}

    class Model:
        dims = type("D", (), {"n_mels": 128})()

        def detect_language(self, mel):
            return None, heard["probs"]

    monkeypatch.setattr(ModelHolder, "get_model", lambda *a, **k: Model())
    monkeypatch.setattr(WA, "log_mel_spectrogram", lambda *a, **k: mx.zeros((3000, 128)))
    monkeypatch.setattr(WA, "pad_or_trim", lambda x, *a, **k: x)
    calls = []
    monkeypatch.setattr(S, "whisper_words", lambda s, sp, lang, p: (calls.append(("whisper", lang)) or ([], [])))
    monkeypatch.setattr(S, "qwen_words", lambda s, sp, lang, p: (calls.append(("qwen", lang)) or ([], [])))
    monkeypatch.setattr(CC, "ask", lambda *a, **k: (calls.append(("claude",)) or ({"segments": []}, {"duration_ms": 900})))
    params = {**S.STAGES["transcribe"].params, "language": None}
    inputs = {"path": wav, "start_s": 0.0, "end_s": 40.0, "upstream": [{"conversations": [{"start": 0.0, "speech": [[1.0, 39.0]]}]}]}

    def run(probs, hindi=False):
        heard["probs"], calls[:] = probs, []
        p = {**params, "hindi": {**H.HINDI, "chunk_s": 600}} if hindi else params
        return S.transcribe(inputs, p)

    out = run({"en": 0.9, "hi": 0.05})
    assert calls == [("qwen", "en")] and out["language_windows"][0] == {"en": 0.9, "hi": 0.05}
    out = run({"en": 0.1, "hi": 0.8})  # Hindi heard; the planner hasn't passed the Hindi settings (J2)
    assert calls == [] and out["paused"] == "Mostly Hindi"
    out = run({"en": 0.45, "hi": 0.4}, hindi=True)  # mixed, English leading: Hindi drafts, then Claude
    assert calls[:2] == [("whisper", "hi"), ("qwen", "hi")] and out["mixed"] == "Hindi and English mixed"
    assert out["merged"]["usage"]["duration_ms"] == 0  # no segments, no call; the sum is still kept
    out = run({"en": 0.05, "kn": 0.7})
    assert calls == [] and out["paused"] == "Mostly Kannada"


def test_an_edit_outside_what_the_review_reads_keeps_the_rubric_version(tmp_path, monkeypatch):  # M30
    from octo import insight as I
    from octo.db import connect
    rubric = tmp_path / "rubric.md"
    body = "## Conduct principles\n\n1. Speak truthfully.\n\n## How the review applies it\n\nGently.\n\n## Examples\n\n"
    rubric.write_text(body)
    monkeypatch.setattr(I, "RUBRIC", rubric)
    conn = connect(tmp_path / "r.db")
    v = planning.rubric_input(conn, "s")
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, params, queued_at) "
                 "VALUES ('i', 'insight', 's', 'h', 'done', 1, ?, 0)", (json.dumps({"rubric": v}),))
    rubric.write_text(body + "Sam's example, written later.\n")  # a section no review reads
    assert planning.rubric_input(conn, "s") == v
    rubric.write_text(body.replace("Speak truthfully.", "Speak truthfully and kindly."))  # a principle changed
    assert planning.rubric_input(conn, "s") != v
