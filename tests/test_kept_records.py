"""What the read-time pages keep in memory (fillers.memoized, sessions._PAUSED, _TALK and _PREVIEW, planning._LETTERS; architecture review 1, 28 Oct)
gives the same answers as reading everything again: on the demo lab, then after a correction, a filler-list change and a
speaker answer. And Delete and Forget drop what's kept."""

import json

import pytest

from octo import corrections as C
from octo import delivery as D
from octo import fillers as FL
from octo import eta as E
from octo import person as PE
from octo import planning as PL
from octo import privacy as PR
from octo import questions as QS
from octo import search as SE
from octo import sessions as SV
from octo import settings as ST
from octo import voice as V
from octo.db import connect, row, rows


@pytest.fixture
def lab(tmp_path, monkeypatch):
    from tests import demo_lab as DL
    monkeypatch.setattr(DL, "ROOT", tmp_path)
    monkeypatch.setattr(DL, "DEMO", tmp_path / "data" / "demo")
    monkeypatch.setattr(DL, "DB", tmp_path / "data" / "demo" / "octo.db")
    DL.build()
    conn = connect(DL.DB)
    conn.execute("UPDATE recordings SET audio_deleted_at=1")  # nothing to silence: the demo's audio isn't under HOME
    _cold()
    return conn


def _cold() -> None:
    FL.forget()
    SV._PAUSED.clear()
    SV._TALK.clear()
    PL._LETTERS.clear()
    E._MERGED.clear()


def _pages(conn) -> str:
    sids = [r["id"] for r in rows(conn, "SELECT id FROM sessions WHERE status='active' ORDER BY id")]
    got = {"search": [SE.search(conn, q) for q in ("the", "ledger", "you know")],
           "questions": QS.summary(conn), "fillers": FL.summary(conn), "delivery": D.all_conversations(conn),
           "people": [PE.page(conn, p["id"]) for p in rows(conn, "SELECT id FROM people ORDER BY id")],
           "counts": ST.counts(conn, "transcribe"), "speakers": [SV.speakers(conn, s) for s in sids],
           "paused": [SV.paused(SV.current(conn, s, "transcribe"), conn) for s in sids],
           "list": [{k: v for k, v in SV.summary(conn, s).items() if k != "todo"} | {"todo": SV.todo(conn, s["id"])["kind"]}
                    for s in rows(conn, "SELECT s.*, r.source, r.original, r.started_at FROM sessions s "
                                        "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active' ORDER BY s.id")],
           "settled": [PL.settled_speakers(conn, s) for s in sids], "rates": E.rates(conn)}
    for s in got["search"]:
        s.pop("ms")
    return json.dumps(got, sort_keys=True, default=str)


def _same_kept_and_cold(conn) -> str:
    warm = _pages(conn)
    warm_again = _pages(conn)  # every memo filled
    _cold()
    cold = _pages(conn)
    assert warm == warm_again == cold
    return cold


def test_kept_records_give_the_same_answers_after_each_change(lab):
    conn = lab
    before = _same_kept_and_cold(conn)
    _pages(conn)  # warm, so each change below is read against kept records

    # a correction: the other person's letter merged into the owner's in the Asha conversation
    sid = row(conn, "SELECT target FROM stage_runs WHERE id LIKE 'dz_%' AND output LIKE '%Leadership asked me%'")["target"]
    dz = SV.current(conn, sid, "diarize")
    C.add(conn, sid, dz["id"], "merge", {"from": "B", "into": "A"})
    fixed = _same_kept_and_cold(conn)
    assert fixed != before

    # the owner's filler list
    FL.set_phrase(conn, "basically", True)
    listed = _same_kept_and_cold(conn)
    assert listed != fixed

    # a speaker answer: the Kabir conversation's suggested speaker confirmed as someone else
    kabir = row(conn, "SELECT session_id FROM session_speakers WHERE state='suggested'")["session_id"]
    conn.execute("UPDATE session_speakers SET person_id='per_dev', state='confirmed' WHERE session_id=? AND state='suggested'",
                 (kabir,))
    assert _same_kept_and_cold(conn) != listed


def test_delete_and_forget_drop_what_is_kept(lab):
    conn = lab
    _pages(conn)
    sid = next(k for m in FL.MEMOS for k in m)
    PR.delete_session(conn, sid)
    assert not any(sid in m for m in FL.MEMOS)
    _pages(conn)
    assert any(FL.MEMOS)
    V.forget(conn, "Asha")
    assert not any(FL.MEMOS)


def test_a_run_read_without_its_output_has_everything_else(lab):
    from octo.pipeline import _NO_OUTPUT, current
    cols = [r["name"] for r in rows(lab, "PRAGMA table_info(stage_runs)")]
    assert sorted(_NO_OUTPUT.split(", ") + ["output"]) == sorted(cols)  # a column added to stage_runs goes in _NO_OUTPUT
    sid = row(lab, "SELECT target FROM stage_runs WHERE stage='diarize' AND is_current=1")["target"]
    full, light = current(lab, sid, "diarize"), current(lab, sid, "diarize", output=False)
    assert {k: v for k, v in full.items() if k != "output"} == {k: v for k, v in light.items() if k != "has_output"}
    assert light["has_output"] == 1
