"""Deleting things (plan D5): a part of a conversation, a whole one, work that slipped in, and raw
audio after 60 days.

Deleting means gone, not hidden: the audio is overwritten with silence in the recording (other
conversations in the same recording keep theirs), and every stored version of the transcript,
speakers, numbers and insights for that conversation is removed, along with voiceprints that
were taken from it, the owner's ratings of its insights, and every coach note that read it (octo/coach.py).
"""

from __future__ import annotations

import json
import os
import time

from pathlib import Path

from . import AUDIO, DATA, HOME
from . import audio as A
from . import files as FL
from .db import config, load_json, row, rows

RAW_AUDIO_DAYS = 60  # D5: raw audio kept 60 days (1 Oct 2026; was 14) so newer models can redo recent ones
# B29 (23 Oct): a lab with labconfig's keep_recordings keeps its recordings instead (the filler cue retrains on them);
# Forget then silences that person's speech in them, since nothing else would take it away.
CLIPS = AUDIO / "sessions"  # each conversation's own slice, cut for playback
CLIP_DAYS = 7  # a clip not played for this long is deleted; it's cut again on the next play (M13)
BENCH = DATA / "bench"  # bench tools' output since 5 Oct, swept like raw audio (M15)


# Free space (review 4 Oct, M13): the disk was 87% full. Below WARN_GB the header says so; below
# STOP_GB nothing more is pulled from the recorder (its card holds the audio until there's room).
WARN_GB, STOP_GB = 20, 5
HOLD_GB = 2  # below this, heavy steps and the daily backup wait (architecture review 5 Nov): room for SQLite and swap


def free_gb() -> float:
    import shutil
    return round(shutil.disk_usage(HOME).free / 1e9, 1)


# ---- playback clips ---------------------------------------------------------------------------
# Named by the conversation's bounds (review 4 Oct, M12): after a split moved a boundary, the old
# clip under the same name still held the other conversation, so deleting that one left its audio
# playable. A moved boundary now means a new name, and any delete clears the whole recording's clips.

def clip_path(sess: dict) -> Path:
    # "-l": made louder for listening (13 Oct); clips cut before then go as unplayed ones do
    return CLIPS / f"{sess['id']}-{round(sess['start_s'] * 1000)}-{round(sess['end_s'] * 1000)}-l.wav"


def clip(sess: dict) -> Path:
    """The conversation's audio, cut once, made louder to listen to (audio.for_listening) and kept while it's
    played (line times are relative to it: same length, same timing)."""
    path = clip_path(sess)
    if path.exists():
        os.utime(path)  # played: keep it another CLIP_DAYS
        return path
    tmp = path.with_name(path.stem + ".tmp.wav")  # whole or not at all, even if two requests race
    with rewriting():
        A.write(tmp, A.for_listening(A.read(HOME / sess["path"], sess["start_s"], sess["end_s"])))
        os.replace(tmp, path)
    return path


def drop_clips(conn, recording_id: str) -> None:
    """Every clip of every conversation in this recording, whatever bounds it was cut at."""
    for s in rows(conn, "SELECT id FROM sessions WHERE recording_id=?", recording_id):
        for f in CLIPS.glob(f"{s['id']}*.wav"):
            f.unlink(missing_ok=True)


def drop_idle_clips(now: float | None = None, days: int = CLIP_DAYS) -> int:
    now = now or time.time()
    gone = 0
    for f in CLIPS.glob("*.wav"):
        if f.stat().st_mtime < now - days * 86400:
            f.unlink(missing_ok=True)
            gone += 1
    return gone


def rewriting():
    """One read-change-write of a recording at a time, across the server's threads and other processes (audit
    14 Oct): two deletes in one recording each read the original, and the second write put back what the first
    had silenced. Cutting a clip takes it too, so no clip is cut from audio that's being silenced."""
    return FL.locked(DATA / "audio")


def _silence(rec_path, start_s: float, end_s: float) -> None:
    """Overwrite [start_s, end_s) of a recording with silence (a full rewrite: headers vary)."""
    silence_spans(rec_path, [(start_s, end_s)])


def silence_spans(rec_path, spans: list[tuple[float, float]]) -> None:
    """Every span of a recording silenced in one rewrite."""
    with rewriting():
        x = A.read(rec_path, channel="all")  # a call keeps both its channels
        x = x.copy()
        changed = False
        for start_s, end_s in spans:
            a, b = max(0, int(start_s * 16000)), min(len(x), int(end_s * 16000))
            if b > a:
                x[a:b] = 0.0
                changed = True
        if changed:
            # Write beside it, then swap in one step: a stage reading the recording at the same moment
            # sees the old file or the new one, never half of each.
            tmp = rec_path.with_name(rec_path.stem + ".tmp" + rec_path.suffix)  # same format as the recording
            A.write(tmp, x)
            os.replace(tmp, rec_path)


def _derived(conn, session_id: str, keep_session_row: bool = True) -> None:
    """Every stored version of what was worked out from this conversation."""
    from .fillers import forget
    forget(session_id)  # what the read-time pages keep of it in memory (search's lines, the counts)
    conn.execute("DELETE FROM feedback WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM review_answers WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM filler_checks WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM question_checks WHERE session_id=?", (session_id,))  # they quote its words
    conn.execute("DELETE FROM ticktick_tasks WHERE session_id=?", (session_id,))  # the tasks in TickTick stay
    conn.execute("DELETE FROM focus_checks WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM focus WHERE session_id=?", (session_id,))  # its words came from this conversation
    conn.execute("DELETE FROM commitments WHERE session_id=?", (session_id,))  # B14: they quote it
    from .coach import drop_for
    drop_for(conn, [session_id])  # a coach note that read it may quote or paraphrase it (6 Oct)
    from . import ask
    ask.drop_for(conn, [session_id])  # B24: an answer that read it quotes it
    from . import talk
    talk.drop_for(conn, [session_id])  # B31: the thread quotes it
    from . import prepare
    prepare.drop_for(conn, [session_id])  # B36: a plan that read it quotes it
    from . import parts
    parts.drop_reading(conn, session_id)  # B35: a review of a whole discussion it was a part of holds its words
    conn.execute("DELETE FROM corrections WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM session_speakers WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM voiceprints WHERE source LIKE ?", (f"label:{session_id}:%",))
    conn.execute("DELETE FROM stage_runs WHERE target=?", (session_id,))
    if session_id in (held := config(conn, HELD, {})):  # L9: a held review goes with its runs
        _held(conn, {k: v for k, v in held.items() if k != session_id})
    rec = row(conn, "SELECT recording_id FROM sessions WHERE id=?", session_id)
    if rec:
        drop_clips(conn, rec["recording_id"])


def delete_session(conn, session_id: str, work: bool = False) -> list[str]:
    """The conversation. One discussion in parts (B35): from its first part (the list shows the group as that one), every
    part; from a later part's own page, only that part (its links go, and the review of the whole that read it, through
    `_derived`; the rest stays). Returns what was deleted, in order."""
    if not row(conn, "SELECT 1 FROM sessions WHERE id=?", session_id):
        raise KeyError(session_id)
    from .parts import group
    g = [p["id"] for p in group(conn, session_id)]
    gone = g if g and g[0] == session_id else [session_id]
    for sid in gone:
        _delete_one(conn, sid, work)
    return gone


def _delete_one(conn, session_id: str, work: bool) -> None:
    sess = row(conn, "SELECT s.*, r.path, r.audio_deleted_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                     "WHERE s.id=?", session_id)
    if not sess["audio_deleted_at"]:
        _silence(HOME / sess["path"], sess["start_s"], sess["end_s"])
    from .parts import delete_for
    delete_for(conn, session_id)
    _derived(conn, session_id)
    # B15: the owner's words about it. Not in _derived: a part deleted is worked out again, and they still stand.
    conn.execute("DELETE FROM reflections WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM intentions WHERE session_id=?", (session_id,))
    from .marks import delete_for
    delete_for(conn, session_id)  # B16: before the row turns 'deleted', while the marks still land here
    # The row stays (as 'deleted') so re-finding conversations in the recording can't bring it back.
    conn.execute("UPDATE sessions SET status='deleted', note=?, reviewed_at=NULL WHERE id=?",
                 ("work: purged" if work else "deleted by you", session_id))


def delete_range(conn, session_id: str, start_s: float, end_s: float) -> None:
    """Silence part of a conversation (times relative to it) and work it all out again."""
    sess = row(conn, "SELECT s.*, r.path, r.audio_deleted_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                     "WHERE s.id=? AND s.status='active'", session_id)
    if not sess:
        raise KeyError(session_id)
    if sess["audio_deleted_at"]:
        raise ValueError("this conversation's audio is already gone; delete the whole conversation instead")
    if not 0 <= start_s < end_s:
        raise ValueError("the range is empty")
    # Speakers the owner left out of insights stay left out: kept by time, found again in the new run
    # (corrections.adopt_left_out). Before 4 Oct the choice went with the old run.
    from .corrections import for_run, left_out_spans
    dz = row(conn, "SELECT id, output FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", session_id)
    spans = left_out_spans(load_json(dz["output"]), for_run(conn, session_id, dz["id"])) if dz and dz["output"] else []
    if sess["left_out_spans"]:  # a second delete before the new run landed
        spans += load_json(sess["left_out_spans"])
    rated = row(conn, "SELECT 1 FROM feedback WHERE session_id=? LIMIT 1", session_id) or \
        row(conn, "SELECT 1 FROM review_answers WHERE session_id=? LIMIT 1", session_id)
    _silence(HOME / sess["path"], sess["start_s"] + start_s, sess["start_s"] + min(end_s, sess["end_s"] - sess["start_s"]))
    from .marks import delete_for
    delete_for(conn, session_id, start_s, end_s)  # B16: a mark in the part pointed at its words
    _derived(conn, session_id)  # every old version held the deleted words; the pipeline redoes it from the audio
    # Its answers went with the old review, so it no longer counts as reviewed until the new one is answered.
    conn.execute("UPDATE sessions SET left_out_spans=?, reviewed_at=CASE WHEN ? THEN NULL ELSE reviewed_at END "
                 "WHERE id=?", (json.dumps(spans) if spans else None, bool(rated), session_id))


def person_spans(conn, person_id: str) -> dict[str, list[tuple[float, float]]]:
    """Where a person speaks, by recording (path → spans in recording time): their lines as the conversation shows
    them (corrections applied), in recordings that still have audio. Read before Forget unlinks them."""
    from .sessions import diarized
    out: dict[str, list[tuple[float, float]]] = {}
    for r in rows(conn, "SELECT ss.session_id, ss.speaker, s.start_s, r.path FROM session_speakers ss "
                        "JOIN sessions s ON s.id = ss.session_id JOIN recordings r ON r.id = s.recording_id "
                        "WHERE ss.person_id=? AND r.audio_deleted_at IS NULL", person_id):
        _, dz, _ = diarized(conn, r["session_id"])
        for ln in (dz or {}).get("lines", []):
            if ln["speaker"] == r["speaker"]:
                out.setdefault(r["path"], []).append((r["start_s"] + ln["start"], r["start_s"] + ln["end"]))
    return out


def silence_person(conn, spans: dict[str, list[tuple[float, float]]]) -> None:
    """person_spans silenced, and every playback clip of those recordings dropped."""
    for path, sp in spans.items():
        silence_spans(HOME / path, sp)
        for r in rows(conn, "SELECT id FROM recordings WHERE path=?", path):
            drop_clips(conn, r["id"])


def drop_old_audio(conn, now: float | None = None, days: int = RAW_AUDIO_DAYS) -> list[str]:
    """Delete raw audio added more than `days` ago. Transcripts, numbers and insights stay;
    stages that need the audio simply don't re-run for those recordings. Also recorder chunks that
    never became a recording, and bench output (review 4 Oct, M15): copies outside the database
    that the 60-day promise covers too. A lab that keeps its recordings (B29) keeps them; the rest still goes."""
    from .labconfig import flag
    now = now or time.time()
    cutoff = now - days * 86400
    gone = []
    for rec in [] if flag(conn, "keep_recordings") else rows(
            conn, "SELECT * FROM recordings WHERE audio_deleted_at IS NULL AND created_at < ?", cutoff):
        (HOME / rec["path"]).unlink(missing_ok=True)
        drop_clips(conn, rec["id"])
        conn.execute("UPDATE recordings SET audio_deleted_at=? WHERE id=?", (now, rec["id"]))
        gone.append(rec["id"])
    for c in rows(conn, "SELECT boot, seq, path FROM device_chunks WHERE recording_id IS NULL AND path IS NOT NULL "
                        "AND pulled_at < ?", cutoff):
        (HOME / c["path"]).unlink(missing_ok=True)
        conn.execute("UPDATE device_chunks SET recording_id='expired', path=NULL WHERE boot=? AND seq=?", (c["boot"], c["seq"]))
    from .drills import drop_old_audio as drills
    drills(conn, cutoff, now)  # B18: practice takes, the same 60 days
    for f in BENCH.rglob("*") if BENCH.exists() else []:
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
    return gone


# ---- Forget takes their words (security review L9, Jay 5 Nov) ----------------------------------------------------
# Their lines read "(forgotten)" in every stored run of the conversations they were in: the transcript, who said what,
# the reviews' and commitments' quotes, the Hindi drafts. The owner's lines stay. A review that read them isn't made
# again by itself (Jay's reviews never re-run unasked): it's held, and its page offers Redo the review.

FORGOTTEN = "(forgotten)"
from .runs import FORGOTTEN_REVIEWS as HELD, review_held  # noqa: E402,F401  (L9; read models check it too)


def _inside(t: float, spans: list) -> bool:
    return any(a <= t < b for a, b in spans)


def _blank(x, texts: list[str], spans: list, others: list):
    """x with their words taken out: words timed inside their lines (and nobody else's), lines or quotes that are theirs
    by time or by text. `texts` longest first; only those of 12+ characters are matched inside other text."""
    def theirs_at(d: dict) -> bool:
        if not spans or not isinstance(d.get("start"), (int, float)):
            return False
        mid = (d["start"] + (d.get("end") or d["start"])) / 2
        return _inside(mid, spans) and not _inside(mid, others)

    if isinstance(x, str):
        for t in texts:
            if len(t) >= 12:
                x = x.replace(t, FORGOTTEN)
        return x
    if isinstance(x, list):
        return [_blank(v, texts, spans, others) for v in x
                if not (isinstance(v, dict) and "w" in v and theirs_at(v))]
    if not isinstance(x, dict):
        return x
    d = {k: _blank(v, texts, spans, others) for k, v in x.items()}
    if isinstance(x.get("text"), str):
        t = x["text"].strip()
        words = x.get("words") if isinstance(x.get("words"), list) else None
        if words is not None and len(d["words"]) != len(words):  # some of a segment's words were theirs
            d["text"] = " ".join(w["w"] for w in d["words"]) or FORGOTTEN
        # shortcut: a quote under 12 characters in a review (no time to place it) stays; the review is held for a redo.
        if t and (theirs_at(d) if words is None else not d["words"]) or (len(t) >= 12 and any(t in s for s in texts)):
            d["text"] = FORGOTTEN
            if words is not None:
                d["words"] = []
    return d


def forget_words(conn, person_id: str) -> list[str]:
    """Their words out of every stored run of the conversations they were in (and of the discussions those are parts
    of), and question checks that quote them. Before Forget unlinks them. Returns the conversations whose review is
    now held."""
    from .parts import with_groups
    from .pipeline import current
    from .sessions import diarized
    letters: dict[str, set[str]] = {}
    for r in rows(conn, "SELECT session_id, speaker FROM session_speakers WHERE person_id=?", person_id):
        letters.setdefault(r["session_id"], set()).add(r["speaker"])
    texts, timed = set(), {}
    for sid, mine in letters.items():
        _, dz, _ = diarized(conn, sid)
        spans, others = [], []
        for ln in (dz or {}).get("lines", []):
            (spans if ln["speaker"] in mine else others).append((ln["start"], ln["end"]))
            if ln["speaker"] in mine and ln["text"].strip():
                texts.add(ln["text"].strip())
        timed[sid] = (spans, others)
    texts = sorted(texts, key=len, reverse=True)
    held, now_held = config(conn, HELD, {}), []
    for sid in with_groups(conn, list(letters)):
        spans, others = timed.get(sid, ([], []))
        for run in rows(conn, "SELECT id, stage, output FROM stage_runs WHERE target=? AND output IS NOT NULL", sid):
            # Times are this conversation's own only in its transcript and who said what (a review of a whole runs on)
            sp = spans if run["stage"] in ("transcribe", "diarize") else []
            out = json.dumps(_blank(json.loads(run["output"]), texts, sp, others))
            if out != run["output"]:
                conn.execute("UPDATE stage_runs SET output=? WHERE id=?", (out, run["id"]))
        for q in rows(conn, "SELECT id, start_s, text FROM question_checks WHERE session_id=?", sid):
            if _inside(q["start_s"], spans) or q["text"].strip() in texts:
                conn.execute("UPDATE question_checks SET text=? WHERE id=?", (FORGOTTEN, q["id"]))
        if ins := current(conn, sid, "insight", output=False):
            held[sid] = ins["id"]
            now_held.append(sid)
    _held(conn, held)
    return now_held


def _held(conn, held: dict) -> None:
    conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, strftime('%s','now')) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                 (HELD, json.dumps(held)))
