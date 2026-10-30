"""What the planner reads from the database for a stage, beyond its upstream outputs.

Stage functions (octo/stages.py) never touch the database: they get their inputs and return an
output, in a worker. These are the other half, run by the planner (octo/pipeline.py) when it
decides whether a stage needs to run: extra inputs folded into a stage's input hash (`dynamic`:
the owner's splits, the voiceprint set, their corrections, the rubric) and whether it may run yet
(`ready`: insights wait for confirmed speakers and an English transcript).
"""

from __future__ import annotations

import json
import time

from .asr import language_paused, other_language
from .corrections import apply, for_run, version
from .db import row, rows


def splits_input(conn, target: str) -> dict:
    from . import split
    s = split.for_recording(conn, target)
    return {"splits": s} if s else {}  # nothing when there are none: no re-runs for every recording


def pinned_review(conn, session_id: str) -> dict | None:
    """The current review, once the owner has answered it, rated any of it or sent one of its steps to
    TickTick. It stays the one shown, and nothing re-runs it, until they ask for a redo
    (sessions.redo_review). Before 4 Oct a speaker fix, a rename or a newly confirmed voice replaced
    a rated review with a fresh one, and the page went back to "To rate" (review 4 Oct, M07)."""
    ins = row(conn, "SELECT id, stale, input_hash FROM stage_runs WHERE target=? AND stage='insight' "
                    "AND is_current=1 AND status='done'", session_id)
    if not ins or row(conn, "SELECT 1 FROM sessions WHERE id=? AND redo_review=?", session_id, ins["id"]):
        return None
    if row(conn, "SELECT 1 FROM feedback WHERE insight_run=? LIMIT 1", ins["id"]) or \
            row(conn, "SELECT 1 FROM review_answers WHERE insight_run=? LIMIT 1", ins["id"]) or \
            row(conn, "SELECT 1 FROM ticktick_tasks WHERE insight_run=? LIMIT 1", ins["id"]):
        return ins
    return None


def voiceprints_input(conn, target: str) -> dict:
    """The voiceprint set and naming bars, so a new print or bar re-runs identify. Not for a
    conversation where the owner has answered for every speaker: their answers win over any match, so a
    re-run there changes nothing (2 Oct: 136 identify runs for 11 conversations). It keeps the
    version its current run was made with, and identify waits until the speakers change. Nor for
    one whose review they have rated: a new suggestion there would send it back to "confirm"."""
    from .voice import MATCH_MARGIN, MATCH_MIN, voiceprints_version
    fresh = {"voiceprints": voiceprints_version(conn), "match": [MATCH_MIN, MATCH_MARGIN]}
    dz_id, fixes = corrections_now(conn, target)
    cur = row(conn, "SELECT params, upstream FROM stage_runs WHERE target=? AND stage='identify' AND is_current=1", target)
    if not dz_id or not cur or json.loads(cur["upstream"]) != [dz_id]:
        return fresh
    was = json.loads(cur["params"])
    if not all(k in was for k in fresh):
        return fresh
    kept = {k: was[k] for k in fresh}
    if pinned_review(conn, target):
        return kept
    # Nor while the owner is naming people: each name is a new print, and each print re-identified every
    # conversation (6 Oct: seven names in three minutes queued three rounds of 36). The new set is
    # used once it has been still for REIDENTIFY_SETTLE_S; the server wakes then (pipeline.next_due).
    if voiceprints_settle_at(conn) is not None:
        return kept
    # Nor for one older than two weeks, or discarded: each new voiceprint re-identified every such
    # conversation (191 identify runs for 18 conversations by 4 Oct; hours a day in Phase 1). Nor
    # for a fragment with under REIDENTIFY_MIN_SPEECH_S of speech: 30 of those 36 were, and a new
    # print rarely changes a few seconds' match. They keep their answers; `octo redo SESSION
    # identify` asks again (review 4 Oct, M11).
    old = row(conn, "SELECT s.status, s.speech_s, coalesce(r.started_at + s.start_s, r.created_at) AS at FROM sessions s "
                    "JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", target)
    if old and (old["status"] != "active" or (old["at"] or 0) < time.time() - IDENTIFY_DAYS * 86400
                or old["speech_s"] < REIDENTIFY_MIN_SPEECH_S):
        return kept
    dz = row(conn, "SELECT output FROM stage_runs WHERE id=?", dz_id)
    letters = apply(json.loads(dz["output"]), fixes)["speakers"] if dz and dz["output"] else []
    answered = {r["speaker"] for r in rows(conn, "SELECT speaker FROM session_speakers WHERE session_id=? AND diarize_run=? "
                                                 "AND state IN ('confirmed', 'rejected')", target, dz_id)}
    if not letters or not set(letters) <= answered:
        return fresh
    return kept


IDENTIFY_DAYS = 14  # a new voiceprint re-identifies conversations from the last two weeks only
REIDENTIFY_MIN_SPEECH_S = 30.0  # ... and only those with at least this much speech
REIDENTIFY_SETTLE_S = 120.0  # ... once no print has been added for this long


def voiceprints_settle_at(conn, now: float | None = None) -> float | None:
    """While a voiceprint added in the last REIDENTIFY_SETTLE_S holds back re-identifying, when
    that ends; None once the set has been still that long."""
    from .voice import EMBEDDING_MODEL, MIN_PRINT_S
    last = row(conn, "SELECT max(created_at) AS t FROM voiceprints WHERE model=? AND seconds >= ?",
               EMBEDDING_MODEL, MIN_PRINT_S)["t"]
    now = now if now is not None else time.time()
    return last + REIDENTIFY_SETTLE_S if last is not None and last + REIDENTIFY_SETTLE_S > now else None


def corrections_now(conn, session_id: str) -> tuple[str | None, list[dict]]:
    dz = row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", session_id)
    return (dz["id"], for_run(conn, session_id, dz["id"])) if dz else (None, [])


def corrections_input(conn, session_id: str) -> dict:
    return {"corrections": version(corrections_now(conn, session_id)[1])}


def settled_speakers(conn, session_id: str, need_self: bool = True, with_person: bool = False) -> list[dict] | None:
    """Who each speaker is, once that's settled for the current diarize run: the owner confirmed, and no
    suggestion waiting for an answer. Unnamed voices may stay "Speaker C". None if not settled. A part of one
    discussion (B35) needn't have the owner in it, and says who each named speaker is (`person`)."""
    dz = row(conn, "SELECT id, output FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", session_id)
    if not dz or not dz["output"]:
        return None
    letters = apply(json.loads(dz["output"]), for_run(conn, session_id, dz["id"]))["speakers"]
    got = {r["speaker"]: r for r in rows(conn, "SELECT ss.speaker, ss.state, p.id AS person, p.name, p.is_self FROM session_speakers ss "
                                               "LEFT JOIN people p ON p.id = ss.person_id "
                                               "WHERE ss.session_id=? AND ss.diarize_run=?", session_id, dz["id"])}
    if any(got.get(k, {}).get("state") in ("suggested", "ambiguous") for k in letters):
        return None
    # The owner only among the speakers still there after the owner's merges: a letter merged away doesn't count (M22).
    # "auto" (C4): the owner's own voice accepted without asking; it settles like a confirmation.
    known = lambda k: k in got and got[k]["state"] in ("confirmed", "auto")
    if need_self and not any(known(k) and got[k]["is_self"] for k in letters):
        return None
    return [{"speaker": k, "name": got[k]["name"] if known(k) else f"Speaker {k}",
             "self": bool(known(k) and got[k]["is_self"]),
             **({"person": got[k]["person"] if known(k) else None} if with_person else {})} for k in letters]


def hindi_input(conn, session_id: str) -> dict:
    """The Hindi settings (octo/hindi.py), only for a conversation whose transcript was heard as
    Hindi or mixed: already merged, or paused **and with the owner confirmed in it** for at least as long
    as a review needs. Nothing for English ones, so adding the Hindi path (4 Oct) re-ran no English
    conversation.

    The merge sends the two drafts' text to Claude. Until 5 Oct it did so for every Hindi
    conversation as soon as it was heard (40 of 45 calls on 4 Oct were for conversations the owner wasn't
    confirmed in); now text leaves only once they are, as for reviews (J2, 5 Oct; review M06). They
    confirms themselves on the paused draft; the merge and then the review follow."""
    from .hindi import HINDI
    from .settings import reader
    tr = row(conn, "SELECT output, params FROM stage_runs WHERE target=? AND stage='transcribe' AND is_current=1", session_id)
    out = json.loads(tr["output"]) if tr and tr["output"] else None
    if not out or other_language(out) != "hi":
        return {}
    if out.get("merged"):
        # Merged once, it stands (review 8 Oct, finding 6): the merge was paid for, so switching the review's model
        # or removing its connection neither redoes it nor undoes it. A change to the Hindi settings themselves
        # (octo/hindi.py) still does. The first lab merges with Claude Code: its params are HINDI, as before.
        made = (json.loads(tr["params"] or "{}") or {}).get("hindi") or {}
        settings = lambda h: {k: v for k, v in h.items() if k not in ("reader", "merge_model")}  # noqa: E731
        if made and settings(made) == settings(HINDI):
            return {"hindi": made}
    if out.get("merged") or (language_paused(out) and jay_talk_s(conn, session_id) >= MERGE_MIN_JAY_S):
        # What merges (product plan 4.1, 7 Oct): Claude Code in the first lab, exactly as before, so nothing
        # of his re-runs; in a new lab, the owner's chosen model, or nothing until there is one.
        who = reader(conn)
        if who is None:
            return {}
        if who["kind"] == "claude-code":
            return {"hindi": HINDI}
        return {"hindi": {**HINDI, "merge_model": who["model"], "reader": who}}
    return {}


MERGE_MIN_JAY_S = 20.0  # the review's own floor (stages.py, insight: min_jay_speech_s)


def jay_talk_s(conn, session_id: str) -> float:
    """Seconds of talk by the speaker(s) confirmed as the owner in the current diarize run, 0 if none."""
    dz_id, fixes = corrections_now(conn, session_id)
    dz = row(conn, "SELECT output FROM stage_runs WHERE id=?", dz_id) if dz_id else None
    if not dz or not dz["output"]:
        return 0.0
    talk = apply(json.loads(dz["output"]), fixes)["talk_s"]
    mine = {r["speaker"] for r in rows(conn, "SELECT ss.speaker FROM session_speakers ss JOIN people p ON p.id = ss.person_id "
                                             "WHERE ss.session_id=? AND ss.diarize_run=? AND ss.state IN ('confirmed', 'auto') AND p.is_self=1",
                                       session_id, dz_id)}
    return sum(talk.get(k, 0.0) for k in mine)


def insight_input(conn, session_id: str) -> dict:
    from .intention import take
    from .marks import review_input
    from .parts import letters, whole
    intentions = take(conn, session_id)  # B15: only when there is one, so no other conversation's input moves
    g = whole(conn, session_id)
    if g:  # B35: one review of the whole discussion, on its first part; a conversation alone is as it was
        speakers, parts = letters(conn, g)
        marks = sorted(int(p["offset_s"]) + m for p in parts for m in review_input(conn, p["id"]))
        fixes = {p["id"]: corrections_input(conn, p["id"])["corrections"] for p in parts}
        return {"speakers": speakers, "rubric": rubric_input(conn, session_id), "corrections": fixes, "parts": parts,
                **({"intentions": intentions} if intentions else {}), **({"marks": marks} if marks else {})}
    marks = review_input(conn, session_id)  # B16: likewise
    return {"speakers": settled_speakers(conn, session_id) or [], "rubric": rubric_input(conn, session_id),
            **corrections_input(conn, session_id), **({"intentions": intentions} if intentions else {}),
            **({"marks": marks} if marks else {})}


def rubric_input(conn, session_id: str) -> str:
    """The rubric's version for this conversation's review. The review reads two sections of the
    file: the principles and "How the review applies it". An edit anywhere else (filling in "Doing
    well at work" or "Examples", which reach no review) keeps the version the current review was
    made with, so it doesn't re-run every review for nothing (review 4 Oct, M30). Each version seen
    is remembered with a hash of those two sections; an edit to them re-runs reviews as before."""
    import hashlib

    from .insight import principles, review_rules, rubric_version
    from .rubric import text as rubric_text
    text = rubric_text()  # data/rubric.md in a buyer's lab; the first lab's pipeline/rubric.md otherwise (octo/rubric.py)
    now = rubric_version(text)
    read = hashlib.sha256(json.dumps([[p["text"] for p in principles(text)], review_rules(text)]).encode()).hexdigest()[:12]
    conn.execute("INSERT OR IGNORE INTO rubric_versions (version, sections) VALUES (?, ?)", (now, read))
    cur = row(conn, "SELECT params FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1", session_id)
    was = json.loads(cur["params"] or "{}").get("rubric") if cur else None
    if was and was != now and row(conn, "SELECT 1 FROM rubric_versions WHERE version=? AND sections=?", was, read):
        return was
    return now


def insight_ready(conn, session_id: str) -> bool:
    from .settings import review_blocked
    if review_blocked(conn):  # a new lab whose owner hasn't chosen a model yet (product plan 4.1)
        return False
    from .parts import letters, whole
    g = whole(conn, session_id)  # B35: every part heard and settled, the owner in at least one
    for sid in [s["id"] for s in g] or [session_id]:
        tr = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='transcribe' AND is_current=1", sid)
        if tr and tr["output"] and language_paused(json.loads(tr["output"])):
            return False
    return bool(letters(conn, g)[0]) if g else settled_speakers(conn, session_id) is not None


def commitments_input(conn, session_id: str) -> dict:
    """B14: who's who with their people, the owner's corrections, and the open commitments with them (octo/commitments.py)."""
    from .commitments import inputs
    return inputs(conn, session_id)


def commitments_ready(conn, session_id: str) -> bool:
    """B14: after the review, in the last 14 days' history, until the owner marks one (octo/commitments.py)."""
    from .commitments import ready
    return ready(conn, session_id)
