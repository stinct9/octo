"""English only for now (2 Oct 2026): which conversations' transcripts are used."""

import json

from octo.asr import language_paused


def test_english_conversations_are_used():
    assert language_paused({"language": "en", "language_votes": {"en": 0.898, "hi": 0.092, "kn": 0.0, "gu": 0.0}}) is None
    assert language_paused({"language": "en", "language_votes": {"en": 0.352, "hi": 0.057, "kn": 0.009, "gu": 0.018}}) is None
    assert language_paused({"language": "en", "language_votes": None}) is None
    assert language_paused(None) is None


def test_hindi_and_mixed_conversations_are_paused():
    # 19:24, Nisha's family: the conversation the three models all got wrong.
    assert language_paused({"language": "hi", "language_votes": {"en": 0.123, "hi": 0.834, "kn": 0.0, "gu": 0.001}}) == "Mostly Hindi"
    # The owner and Nisha, 11 min: English won the vote by 0.001.
    assert language_paused({"language": "en", "language_votes": {"en": 0.281, "hi": 0.28, "kn": 0.005, "gu": 0.002}}) \
        == "Hindi and English mixed"
    assert language_paused({"language": "kn", "language_votes": None}) == "In Kannada"


def test_punctuation_goes_back_on_the_aligned_words():
    from octo.asr import punctuate
    words = [{"w": w, "start": i, "end": i + 0.5} for i, w in enumerate(["Hi", "Nisha", "are", "you", "coming"])]
    got = punctuate(words, "Hi, Nisha. Are you coming?")
    assert [w["w"] for w in got] == ["Hi,", "Nisha.", "Are", "you", "coming?"]
    assert [w["start"] for w in got] == [0, 1, 2, 3, 4]
    # A lone dash has nothing to align; the transcript's words win when the counts match.
    assert [w["w"] for w in punctuate(words, "Hi, Nisha — are you coming?")] == ["Hi,", "Nisha", "are", "you", "coming?"]
    # The aligner lost a word: the rest still line up, in order.
    got = punctuate(words[:3] + words[4:], "Hi, Nisha. Are you coming?")
    assert [w["w"] for w in got] == ["Hi,", "Nisha.", "Are", "coming?"]


def test_words_group_at_pauses():
    from octo.asr import group_words
    ws = [{"w": "a", "start": 0, "end": 1}, {"w": "b", "start": 1.5, "end": 2}, {"w": "c", "start": 3.5, "end": 4}]
    assert [[w["w"] for w in g] for g in group_words(ws, 1.0)] == [["a", "b"], ["c"]]


def test_paused_reads_the_same_from_sqlite_as_from_the_whole_transcript(tmp_path):
    # Architecture review, 28 Oct: the list parsed every whole transcript to read three fields.
    from octo import sessions as SV
    from octo.db import connect, row
    conn = connect(tmp_path / "p.db")
    outs = [{"language": "en", "lines": [{"text": "hi"}]}, {"language": "hi"}, {"language": "en", "language_votes": {"en": 1, "hi": 3}},
            {"language": "hi", "language_votes": {"hi": 5}, "merged": True}, {"language": "kn", "language_votes": {"en": 5, "kn": 1}}]
    for i, out in enumerate(outs):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, output, queued_at) "
                     "VALUES (?, 'transcribe', 's', 'h', 'done', 0, '[]', '{}', ?, 0)", (f"tr{i}", json.dumps(out)))
        tr = row(conn, "SELECT * FROM stage_runs WHERE id=?", f"tr{i}")
        assert SV.paused(tr, conn) == SV.paused(tr), out
    assert [SV.paused(row(conn, "SELECT * FROM stage_runs WHERE id=?", f"tr{i}"), conn) for i in range(5)] == [
        None, "In Hindi", "Hindi and English mixed", None, None]
