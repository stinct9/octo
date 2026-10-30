"""C4 (coaching plan): the owner's own voice, matched surely enough, settles without asking."""

import json

from octo import pipeline as P
from octo import planning as PL
from octo import sessions as SV
from octo.db import connect, rows

from test_corrections import DZ


def _lab(tmp_path):
    conn = connect(tmp_path / "a.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r', 'phone', 'x', 9, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s', 'r', 0, 9, 5, 0)")
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('jay', 'Sam', 1, 0), ('g', 'Nisha', 0, 0)")
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz', 'diarize', 's', 'h', 'done', 1, ?, 0)", (json.dumps(DZ),))
    return conn


def _identify(conn, matches):
    out = {"diarize_run": "dz", "jay_present": True,
           "speakers": [{"speaker": k, "match": m} for k, m in matches.items()]}
    P._apply_identify(conn, {"target": "s", "output": json.dumps(out)})
    return {r["speaker"]: r["state"] for r in rows(conn, "SELECT speaker, state FROM session_speakers")}


def test_jays_voice_is_settled_when_the_match_is_sure_and_asked_about_when_not(tmp_path):
    conn = _lab(tmp_path)
    m = lambda pid, sim, margin: {"person_id": pid, "name": pid, "similarity": sim, "margin": margin, "ambiguous": False}
    states = _identify(conn, {"A": m("jay", 0.82, 0.5), "B": m("g", 0.95, 0.6), "C": None})
    assert states == {"A": "auto", "B": "suggested", "C": "unknown"}  # others are still asked about
    assert _identify(conn, {"A": m("jay", 0.64, 0.17)})["A"] == "suggested"  # below the bar: ask
    _identify(conn, {"A": m("jay", 0.82, 0.5), "B": None, "C": None})
    settled = PL.settled_speakers(conn, "s")
    assert settled and settled[0] == {"speaker": "A", "name": "Sam", "self": True}  # the review can run
    assert next(sp for sp in SV.speakers(conn, "s") if sp["letter"] == "A")["self"]


def test_an_auto_accepted_voice_adds_no_voiceprint(tmp_path):  # K2
    conn = _lab(tmp_path)
    _identify(conn, {"A": {"person_id": "jay", "name": "Sam", "similarity": 0.9, "margin": 0.6, "ambiguous": False}})
    assert rows(conn, "SELECT id FROM voiceprints") == []
