"""Search across conversations (B12, octo/search.py) on a made-up lab: read as the page reads it."""

import json

from octo import corrections as C
from octo.db import connect
from octo.search import _find, search


def _conv(conn, sid, at, lines, speakers, review=None, status="active"):
    conn.execute("INSERT INTO recordings (id, path, source, started_at, duration_s, created_at) VALUES (?, 'a', 'phone', ?, 600, 0)",
                 (f"r_{sid}", at))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) VALUES (?, ?, 0, 600, 60, ?, 0)",
                 (sid, f"r_{sid}", status))
    letters = sorted({ln["speaker"] for ln in lines})
    dz = {"speakers": letters, "lines": lines, "talk_s": {s: 10.0 for s in letters},
          "turns": [{"speaker": ln["speaker"], "start": ln["start"], "end": ln["end"]} for ln in lines]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES (?, 'diarize', ?, 'h', 'done', 1, ?, 0)", (f"dz_{sid}", sid, json.dumps(dz)))
    for letter, pid in speakers.items():
        conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, diarize_run) VALUES (?, ?, ?, 'confirmed', ?)",
                     (sid, letter, pid, f"dz_{sid}"))
    if review:
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                     "VALUES (?, 'insight', ?, 'h', 'done', 1, ?, 0)", (f"in_{sid}", sid, json.dumps(review)))


def L(i, who, start, text):
    return {"id": i, "speaker": who, "start": start, "end": start + 3, "text": text, "uncertain": False}


def _lab(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('me', 'Jay', 1, 0), ('ravi', 'Ravi', 0, 0), "
                 "('meera', 'Meera', 0, 0)")
    _conv(conn, "ses_old", 1000, [L(0, "A", 0, "How is your workload this week?"), L(1, "B", 4, "My Workload is heavy, honestly.")],
          {"A": "me", "B": "ravi"},
          review={"skipped": None, "one_thing": {"headline": "Ask about the workload sooner", "why": "", "quotes": [{"line": 1}]},
                  "reading": [], "between": [], "conduct": [],
                  "next": [{"title": "Check Ravi's workload on Friday", "detail": "", "why": ""}]})
    _conv(conn, "ses_new", 2000, [L(0, "A", 0, "The workload can wait."), L(1, "B", 5, "Workload again, Meera here."),
                                  L(2, "C", 9, "A stranger mentions workload.")], {"A": "me", "B": "meera"})
    _conv(conn, "ses_tv", 3000, [L(0, "A", 0, "Workload on the TV")], {}, status="set_aside")
    return conn


def test_lines_and_review_text_newest_first(tmp_path):
    got = search(_lab(tmp_path), "workload")
    assert [(r["session_id"], r["kind"], r["who"]) for r in got["results"]] == [
        ("ses_new", "line", "Jay"), ("ses_new", "line", "Meera"), ("ses_new", "line", "Speaker C"),
        ("ses_old", "line", "Jay"), ("ses_old", "line", "Ravi"),
        ("ses_old", "review", "The one thing"), ("ses_old", "review", "Step")]
    assert got["total"] == 7 and got["scanned"] == 2  # the TV recording, set aside, isn't read
    one = got["results"][5]
    assert one["line"] == 1 and one["match"] == "workload"  # the one thing opens at its quote's line
    assert got["results"][4]["match"] == "Workload" and got["results"][4]["start"] == 4
    assert got["results"][4]["with"] == ["Ravi"]  # the conversation: when, and with whom


def test_filters_a_person_and_you_or_them(tmp_path):
    conn = _lab(tmp_path)
    ravi = search(conn, "workload", person="ravi")["results"]
    assert [(r["who"], r["kind"]) for r in ravi] == [("Ravi", "line"), ("The one thing", "review"), ("Step", "review")]
    assert [r["who"] for r in search(conn, "workload", who="you")["results"]] == ["Jay", "Jay"]
    assert [r["who"] for r in search(conn, "workload", who="them")["results"]] == ["Meera", "Speaker C", "Ravi"]


def test_the_owners_corrections_are_applied(tmp_path):
    conn = _lab(tmp_path)
    C.add(conn, "ses_new", "dz_ses_new", "line", {"line": 2, "to": "B"})  # the stranger's line was Meera's
    assert [r["who"] for r in search(conn, "stranger")["results"]] == ["Meera"]
    C.add(conn, "ses_new", "dz_ses_new", "exclude", {"letter": "B"})  # then left out of insights
    assert search(conn, "stranger")["results"] == []
    assert [r["who"] for r in search(conn, "workload", who="them")["results"]] == ["Ravi"]


def test_too_short_and_no_match(tmp_path):
    conn = _lab(tmp_path)
    assert search(conn, " w ")["results"] == [] and search(conn, "zebra")["total"] == 0


def test_a_few_words_either_side():
    m = _find("one two three four five six seven eight NEEDLE nine ten eleven twelve thirteen fourteen fifteen", "needle")
    assert m == {"before": "… three four five six seven eight ", "match": "NEEDLE", "after": " nine ten eleven twelve thirteen fourteen …"}
    assert _find("needle", "needle") == {"before": "", "match": "needle", "after": ""}
    assert _find("a haystack", "stack") == {"before": "a hay", "match": "stack", "after": ""}
