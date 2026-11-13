"""Forget takes their words (security review L9, 5 Nov): their lines read "(forgotten)" in every table, the owner's
stay, and the reviews that read them are held for Redo the review, never made again by themselves."""

from octo import pipeline as P
from octo import planning as PL
from octo import privacy as PR
from octo import voice as V
from octo.db import rows
from octo.sessions import diarized
from tests.test_delete_reach import _holding, lab  # noqa: F401 (the fixture)


def _lines(conn, person_id):
    """(their lines, everyone else's) as the pages read them, in the conversations they were in."""
    theirs, others = [], []
    for r in rows(conn, "SELECT session_id, speaker FROM session_speakers WHERE person_id=?", person_id):
        _, dz, _ = diarized(conn, r["session_id"])
        for ln in dz["lines"]:
            (theirs if ln["speaker"] == r["speaker"] else others).append((r["session_id"], ln))
    return theirs, others


def test_forgetting_someone_takes_their_words_from_every_table_and_holds_the_reviews(lab):  # noqa: F811
    conn = lab
    p = max(rows(conn, "SELECT id, name FROM people WHERE is_self=0"), key=lambda p: len(_lines(conn, p["id"])[0]))
    theirs, others = _lines(conn, p["id"])
    # The demo's lines repeat across conversations: only what nobody else said anywhere is theirs alone.
    mine = {(s, ln["id"]) for s, ln in theirs}
    elsewhere = {ln["text"].strip() for s in rows(conn, "SELECT id FROM sessions WHERE status='active'")
                 for ln in (diarized(conn, s["id"])[1] or {}).get("lines", []) if (s["id"], ln["id"]) not in mine}
    said = {ln["text"].strip() for _, ln in theirs if len(ln["text"].strip()) >= 12}
    kept = {ln["text"].strip() for _, ln in others if len(ln["text"].strip()) >= 12} - said
    said -= {t for t in said if any(t in e for e in elsewhere)}
    assert said and kept
    sid, ln = theirs[0]
    conn.execute("INSERT INTO question_checks (id, session_id, start_s, line, text, app, verdict, created_at) "
                 "VALUES ('qc_f', ?, ?, ?, ?, 'you', 'you', 0)", (sid, ln["start"], ln["id"], ln["text"]))
    sessions = sorted({s for s, _ in theirs})
    reviews = {s: P.current(conn, s, "insight") for s in sessions}
    assert any(reviews.values())

    V.forget(conn, p["name"])
    assert _holding(conn, sorted(said)) == set()  # nothing of what they said, anywhere
    assert _holding(conn, sorted(kept)) >= {"stage_runs.output"}  # the others' lines stay
    for s in sessions:
        _, dz, _ = diarized(conn, s)
        ids = {ln["id"] for t, ln in theirs if t == s}
        assert all(ln["text"] == PR.FORGOTTEN for ln in dz["lines"] if ln["id"] in ids)
    # Each review is kept as it was, offers a redo, and nothing queues it again until the owner asks.
    for s, ins in reviews.items():
        if ins:
            assert PL.pinned_review(conn, s)["id"] == ins["id"] and P.review_outdated(conn, s)
    queued = P.plan(conn)
    assert not rows(conn, f"SELECT 1 FROM stage_runs WHERE stage='insight' AND id IN ({','.join('?' * len(queued))})",
                    *queued) if queued else True
    s = next(s for s, ins in reviews.items() if ins)
    conn.execute("UPDATE sessions SET redo_review=? WHERE id=?", (reviews[s]["id"], s))  # Redo the review
    assert PL.pinned_review(conn, s) is None and not PR.review_held(conn, s, None)
    PR.delete_session(conn, s)
    assert s not in str(PR.config(conn, PR.HELD, {}))
