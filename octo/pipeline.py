"""Decides what needs (re)running, queues it, and runs each stage in a worker subprocess.

The worker boundary is deliberate: Whisper, pyannote and the LLM each take gigabytes on a 16 GB
Mac, and the only reliable way to give that memory back is for the process to exit. It also
isolates crashes, and makes cancel a real kill.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import AUDIO, BACKGROUND, HOME, ROOT
from . import audio as A
from . import planning as PL
from . import review as R
from .db import load_json, new_id, row, rows, tx
from .parts import held, whole
# The runs' state and every hold live in octo/runs.py since 5 Nov (risk 8), below sessions; their old names stay here.
from .runs import (_NO_OUTPUT, DAILY_CLAUDE_CALLS, HEAVY_HOLD_S, HEAVY_STAGES, KEY_FIX, KEY_REFUSED_WORDS,  # noqa: F401
                   LIMIT_WORDS, MAX_ATTEMPTS, RETRY_AFTER_S, SIGN_IN, SIGN_IN_CHECK_S,
                   SIGNED_OUT_WORDS, TRANSIENT_MAX_S, TRANSIENT_WORDS, _calls_since, _midnight, budget_blocked_until,
                   claude_blocked_until, claude_calls_since, claude_signed_out, claude_waits, connection_refused,
                   current, due_at, heavy_held, held_back, key_refused, limit_reset,
                   run_reader, signed_in, signed_out, transient, transient_retry, uses_claude)
from .stages import STAGES, Stage, params_for

Log = Callable[[str], None]


# ---- recordings ---------------------------------------------------------------------------

class DuplicateRecording(ValueError):
    pass


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def add_recording(conn, src: Path, source: str, started_at: float | None = None, original: str | None = None,
                  speakers: bool = False) -> str:
    """A recording into the lab. A call from the Octo app (source "call", B19) keeps its two channels, the mic and the
    call app's sound; one recorded on the Mac's speakers has the others taken out of the mic (octo/calls.py)."""
    digest = file_hash(src)
    # Checked and added under one lock, across processes (audit 14 Oct): the same memo dropped twice, both
    # converting at once, passed the check twice and counted every conversation in it twice.
    with open(Path(conn.execute("PRAGMA database_list").fetchone()[2] + ".add.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if dup := row(conn, "SELECT id FROM recordings WHERE sha256=?", digest):
            raise DuplicateRecording(f"already added as {dup['id']}")
        rec_id = new_id("rec")
        dst = AUDIO / "recordings" / f"{rec_id}.flac"  # lossless, about a third of WAV (octo/audio.py)
        if source == "call":
            _store_call(src, dst, speakers)
        else:
            A.store(src, dst)
        conn.execute(
            "INSERT INTO recordings (id, source, path, original, started_at, duration_s, created_at, sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rec_id, source, str(dst.relative_to(HOME)), original or str(src), started_at, A.duration(dst), time.time(),
             digest))
    return rec_id


def _store_call(src: Path, dst: Path, speakers: bool) -> None:
    import soundfile as sf
    try:
        n = sf.info(str(src)).channels
    except (RuntimeError, sf.LibsndfileError):
        n = 0
    if n != 2:  # mixed down, the mic would be everyone and the lab would take them all for the owner
        raise ValueError("a call needs its two channels, the mic and the call app's sound")
    A.store(src, dst, channels=2)
    if speakers:
        from .calls import echo_cancel
        A.write_pcm(dst, echo_cancel(A.read_pcm(dst, channel="all")))


def compact_audio(conn, log: Log = print) -> dict[str, int]:
    """Re-store WAV recordings as FLAC (octo/audio.py): each one's samples are checked identical
    before its WAV is deleted. Holds the pipeline's lock, so no stage reads a file mid-swap."""
    done = {"converted": 0, "failed": 0, "wav_bytes": 0, "flac_bytes": 0, "busy": 0}
    with open(_lock_path(conn), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("the pipeline is running (or paused by holding its lock); try again when it's idle")
            done["busy"] = 1
            return done
        from .privacy import rewriting
        for rec in rows(conn, "SELECT id, path FROM recordings WHERE audio_deleted_at IS NULL AND path LIKE '%.wav'"):
            wav = HOME / rec["path"]
            if not wav.exists():
                continue
            flac = wav.with_suffix(".flac")
            with rewriting():  # a delete silencing the WAV mid-copy would leave its audio in the FLAC
                pcm = A.read_pcm(wav)
                A.write_pcm(flac, pcm)
                if not np.array_equal(A.read_pcm(flac), pcm):
                    flac.unlink(missing_ok=True)
                    log(f"{rec['id']}: FLAC didn't match the WAV; kept the WAV")
                    done["failed"] += 1
                    continue
                conn.execute("UPDATE recordings SET path=? WHERE id=?", (str(flac.relative_to(HOME)), rec["id"]))
                done["wav_bytes"] += wav.stat().st_size
                done["flac_bytes"] += flac.stat().st_size
                wav.unlink()
            done["converted"] += 1
            log(f"{rec['id']}: {wav.name} → {flac.name}")
    return done


# ---- what needs running ------------------------------------------------------------------

def input_hash(stage: Stage, params: dict, target: str, upstream: list[str]) -> str:
    blob = json.dumps({"stage": stage.name, "v": stage.version, "params": params, "target": target,
                       "upstream": sorted(upstream)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


PRESSURE_CRITICAL = 4  # kern.memorystatus_vm_pressure_level: 1 normal, 2 warning, 4 critical (risk 6; the hold: runs.py)


def pressure_level() -> int:
    try:
        return int(subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True,
                                  timeout=5).stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 1


def short_of() -> str | None:
    """Why the Mac can't take a heavy step now, or None."""
    from .privacy import HOLD_GB, free_gb
    if pressure_level() >= PRESSURE_CRITICAL:
        return "the Mac is short of memory"
    if (free := free_gb()) < HOLD_GB:
        return f"only {free} GB free on the disk"
    return None


def heavy_hold(conn, now: float) -> dict | None:
    """The runner's: held as before, or held now if the Mac is short of memory or disk."""
    if held := heavy_held(conn, now):
        return held
    if why := short_of():
        held = {"until": now + HEAVY_HOLD_S, "why": why}
        conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('heavy_hold', ?, ?) ON CONFLICT(key) "
                     "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (json.dumps(held), now))
        return held
    return None


def next_due(conn, now: float | None = None) -> float | None:
    """The soonest a failed run comes off hold, or new voiceprints have settled (identify waits
    while the owner is naming people), so the server can go round then rather than at its next hourly
    pass (which made the 15 and 30 min retries about an hour apart)."""
    now = now if now is not None else time.time()
    waiting = rows(conn, "SELECT DISTINCT stage, target, input_hash FROM stage_runs f WHERE status='failed' "
                         "AND finished_at > ? AND NOT EXISTS (SELECT 1 FROM stage_runs d WHERE d.target=f.target "
                         "AND d.stage=f.stage AND d.input_hash=f.input_hash AND d.status IN ('done','queued','running'))",
                   now - 2 * 86400)
    due = [d for w in waiting if now < (d := due_at(conn, w["stage"], w["target"], w["input_hash"])) < math.inf]
    if claude_signed_out(conn, now) is not None:  # look again soon: the owner may have signed in
        due.append(now + SIGN_IN_CHECK_S)
    if (settled := PL.voiceprints_settle_at(conn, now)) is not None:
        due.append(settled)
    if held := heavy_held(conn, now):  # look again when the hold on heavy steps ends
        due.append(held["until"])
    return min(due) if due else None


def retry(conn, target: str | None = None) -> int:
    """Forget past failures (of one conversation or recording, or all), so they run again now. The
    failed runs stay, marked 'retried', with their errors."""
    sql = "UPDATE stage_runs SET status='retried' WHERE status='failed'"
    return conn.execute(sql + " AND target=?", (target,)).rowcount if target else conn.execute(sql).rowcount


def wanted(conn, stage: Stage, target: str, upstream: list[str]) -> tuple[dict, str]:
    """The params a run of this stage would get now, and their input hash."""
    from .settings import overrides  # The owner's model choice for this step (the Pipeline page)
    params = {**params_for(stage, overrides(conn, stage.name)), **(stage.dynamic(conn, target) if stage.dynamic else {})}
    return params, input_hash(stage, params, target, upstream)


def _needed(conn, stage: Stage, target: str, upstream: list[str]) -> str | None:
    """Queue a run if there's no current one with the same inputs (and none already queued, and
    it isn't waiting after a failure)."""
    params, h = wanted(conn, stage, target, upstream)
    cur = current(conn, target, stage.name)
    if cur and cur["input_hash"] == h and not cur["stale"]:
        return None
    if row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage=? AND input_hash=? "
                 "AND status IN ('queued','running')", target, stage.name, h):
        return None
    if held_back(conn, stage.name, target, h):
        return None
    run_id = new_id("run")
    conn.execute(
        "INSERT INTO stage_runs (id, stage, target, input_hash, status, upstream, params, queued_at) "
        "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
        (run_id, stage.name, target, h, json.dumps(upstream), json.dumps(params), time.time()))
    return run_id


# D10 (discard conversations without the owner's voice) is paused: 1 Oct 2026, "for now let's exclude
# the filter, we'll come back to it". With the recorder across a room his voice scored 0.42 against
# a desk voiceprint, under the 0.45 "absent" bar. True puts the filter back on its next run.
KEEP_ONLY_JAYS = False
PAUSED_NOTE = "Your voice wasn't clearly heard; kept because the filter is paused."

_UP_TO_IDENTIFY = {"transcribe", "speakers", "diarize", "identify"}
AUDIO_STAGES = {"find_conversations", "transcribe", "speakers", "diarize", "identify"}  # the ones that listen


def _pending(conn, target: str, stage: str) -> bool:
    return row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage=? AND status IN ('queued','running')",
               target, stage) is not None


def _conversation(output: str | dict | None, start_s: float) -> dict | None:
    """The conversation starting at start_s in a find_conversations output."""
    out = load_json(output) if isinstance(output, str) else output
    return next((c for c in (out or {}).get("conversations", []) if abs(c["start"] - start_s) < 0.01), None)


def _same_conversation(conn, sess: dict, stage: str, fc_id: str) -> str:
    """The find_conversations run a stage of this conversation should build on: the current one,
    unless this conversation is exactly as it was in the run its current output used. Then that
    one, so finding conversations again (after the owner splits another conversation in the recording,
    say) doesn't redo this one: before 4 Oct a split re-ran every conversation in the recording,
    reviews included, and two recordings hold most of the owner's ratings."""
    cur = current(conn, sess["id"], stage)
    was = json.loads(cur["upstream"]) if cur else []
    if len(was) != 1 or was[0] == fc_id:
        return fc_id
    old, new = (row(conn, "SELECT output FROM stage_runs WHERE id=?", i) for i in (was[0], fc_id))
    a = _conversation(old["output"], sess["start_s"]) if old else None
    return was[0] if a is not None and a == _conversation(new["output"] if new else None, sess["start_s"]) else fc_id


def plan(conn) -> list[str]:
    """Queue every stage whose inputs changed or that never ran. A stage is only queued once
    everything it builds on is done and not itself waiting to re-run. Returns new run ids."""
    queued = []
    for rec in rows(conn, "SELECT * FROM recordings"):
        for name in list(STAGES):
            stage = STAGES[name]
            if stage.level == "recording":
                if rec["audio_deleted_at"]:
                    continue  # nothing to listen to any more (D5)
                rid = _needed(conn, stage, rec["id"], [])
                if rid:
                    queued.append(rid)
        fc = current(conn, rec["id"], "find_conversations")
        if not fc or _pending(conn, rec["id"], "find_conversations"):
            continue
        # Auto-discarded sessions (D10) still get identify re-run, so a new voiceprint or a better
        # rule can bring them back; nothing after identify runs on them.
        for sess in rows(conn, "SELECT * FROM sessions WHERE recording_id=? AND (status='active' OR "
                               "(status='discarded' AND kept=0 AND note LIKE 'Your voice not heard%'))", rec["id"]):
            blocked: set[str] = set()  # stages that can't run yet, and so neither can what builds on them
            for name in list(STAGES):
                stage = STAGES[name]
                if stage.level != "session":
                    continue
                if (sess["status"] == "discarded" and name not in _UP_TO_IDENTIFY) or blocked & set(stage.upstreams):
                    blocked.add(name)
                    continue
                if rec["audio_deleted_at"] and name in AUDIO_STAGES:
                    if not current(conn, sess["id"], name):  # keep what was worked out; can't redo it
                        blocked.add(name)
                    continue
                if name == "insight" and held(conn, sess["id"]):
                    blocked.add(name)  # B35: a later part of one discussion, or a group with answers and no redo asked
                    continue
                ups = (insight_ups(conn, sess["id"]) if name == "insight" else
                       [current(conn, rec["id"] if STAGES[u].level == "recording" else sess["id"], u) for u in stage.upstreams])
                if any(not up or up["status"] != "done" or _pending(conn, up["target"], up["stage"]) for up in ups):
                    blocked.add(name)  # upstream not ready, or about to change
                    continue
                if stage.ready and not stage.ready(conn, sess["id"]):
                    blocked.add(name)  # waiting on the owner (e.g. confirming speakers)
                    continue
                if name == "insight" and PL.pinned_review(conn, sess["id"]):
                    blocked.add(name)  # rated: kept until the owner asks for a redo
                    continue
                up_ids = [up["id"] for up in ups]
                if stage.upstreams == ("find_conversations",):
                    up_ids = [_same_conversation(conn, sess, name, up_ids[0])]
                rid = _needed(conn, stage, sess["id"], up_ids)
                if rid:
                    queued.append(rid)
                    blocked.add(name)  # later stages wait for this one
    return queued


# ---- after a run finishes ----------------------------------------------------------------

def _make_current(conn, run: dict) -> None:
    with tx(conn):
        old = current(conn, run["target"], run["stage"])
        conn.execute("UPDATE stage_runs SET is_current=0 WHERE target=? AND stage=?", (run["target"], run["stage"]))
        conn.execute("UPDATE stage_runs SET is_current=1, stale=0 WHERE id=?", (run["id"],))
        if old and old["id"] != run["id"]:
            # Anything built on the replaced output is now stale -- except a conversation that
            # finding conversations again left exactly as it was (see _same_conversation).
            for r in rows(conn, "SELECT id, target, upstream FROM stage_runs WHERE is_current=1"):
                if old["id"] in json.loads(r["upstream"]):
                    if run["stage"] == "find_conversations" and _unchanged(conn, r["target"], old, run):
                        continue
                    conn.execute("UPDATE stage_runs SET stale=1 WHERE id=?", (r["id"],))
        if run["stage"] == "find_conversations":
            _apply_conversations(conn, run)
        elif run["stage"] == "identify":
            _apply_identify(conn, run)
            from . import parts, split
            # Two in one (people plan 4.5), else one discussion in parts (B35): before anyone is asked. A split's parts
            # come back through identify, and are grouped then if they should be.
            if not split.auto(conn, run["target"]):
                parts.auto(conn, run["target"])
        elif run["stage"] == "commitments":
            from .commitments import apply
            apply(conn, run)
        elif run["stage"] == "diarize":
            from .corrections import adopt_left_out, carry
            if old and old["id"] != run["id"]:
                carry(conn, run["target"], old["id"], run["id"])
            adopt_left_out(conn, run["target"], run["id"])


def _unchanged(conn, session_id: str, old: dict, new: dict) -> bool:
    sess = row(conn, "SELECT start_s FROM sessions WHERE id=?", session_id)
    a = _conversation(old["output"], sess["start_s"]) if sess else None
    return a is not None and a == _conversation(new["output"], sess["start_s"])


def _apply_conversations(conn, run: dict) -> None:
    """Sessions follow the current conversation boundaries. If a re-run moves them, sessions that
    no longer match are marked superseded (kept, not deleted: feedback may point at them)."""
    out = load_json(run["output"])
    starts = []
    for c in out["conversations"]:
        starts.append(c["start"])
        conn.execute(
            "INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (recording_id, start_s) DO UPDATE SET end_s=excluded.end_s, speech_s=excluded.speech_s, "
            "status=CASE WHEN status='superseded' THEN 'active' ELSE status END",
            (new_id("ses"), run["target"], c["start"], c["end"], c["speech_s"], time.time()))
    marks = ",".join("?" * len(starts)) or "NULL"
    conn.execute(f"UPDATE sessions SET status='superseded' WHERE recording_id=? AND status='active' "
                 f"AND start_s NOT IN ({marks})", (run["target"], *starts))


def _auto_self(conn, m: dict) -> bool:
    from .voice import AUTO_SELF_MARGIN, AUTO_SELF_MIN
    me = row(conn, "SELECT is_self FROM people WHERE id=?", m["person_id"])
    return bool(me and me["is_self"]) and m["similarity"] >= AUTO_SELF_MIN and m.get("margin", 0) >= AUTO_SELF_MARGIN


def _auto_other(conn, m: dict) -> bool:
    """Someone else, matched as surely as the owner's own voice must be (C4), whose voice the lab knows from at
    least AUTO_OTHER_CONVERSATIONS conversations (people plan 4.4)."""
    from .voice import AUTO_OTHER_CONVERSATIONS, AUTO_OTHER_MARGIN, AUTO_OTHER_MIN, EMBEDDING_MODEL
    p = row(conn, "SELECT is_self FROM people WHERE id=?", m["person_id"])
    if not p or p["is_self"] or m["similarity"] < AUTO_OTHER_MIN or m.get("margin", 0) < AUTO_OTHER_MARGIN:
        return False
    sources = [r["source"] for r in rows(conn, "SELECT source FROM voiceprints WHERE person_id=? AND model=?",
                                         m["person_id"], EMBEDDING_MODEL)]
    heard_in = {src.split(":")[1] if src.startswith("label:") else src for src in sources}
    return len(heard_in) >= AUTO_OTHER_CONVERSATIONS


def _suggest(conn, sid: str, letter: str, m: dict | None, dz_run: str, owner_channel: bool = False) -> None:
    if owner_channel and (me := row(conn, "SELECT id FROM people WHERE is_self=1 ORDER BY created_at")):
        # A call's mic (B19): the owner, settled without asking like C4, unless the voice is surely someone else's
        # (someone else on the owner's Mac). Still theirs to change on the page, and no voiceprint until they confirm (K2).
        from .voice import AUTO_SELF_MARGIN, AUTO_SELF_MIN
        someone_else = m and m["person_id"] != me["id"] and m["similarity"] >= AUTO_SELF_MIN \
            and m.get("margin", 0) >= AUTO_SELF_MARGIN
        if not someone_else:
            conn.execute(
                "INSERT OR REPLACE INTO session_speakers (session_id, speaker, person_id, state, similarity, diarize_run) "
                "VALUES (?, ?, ?, 'auto', ?, ?)",
                (sid, letter, me["id"], m["similarity"] if m and m["person_id"] == me["id"] else None, dz_run))
            return
    state = "unknown" if not m else "ambiguous" if m.get("ambiguous") else "suggested"
    if state == "suggested" and (_auto_self(conn, m) or _auto_other(conn, m)):
        state = "auto"  # C4 for the owner's own voice, 4.4 for others: sure enough to settle without asking
    conn.execute(
        "INSERT OR REPLACE INTO session_speakers (session_id, speaker, person_id, state, similarity, diarize_run) "
        "VALUES (?, ?, ?, ?, ?, ?)", (sid, letter, m["person_id"] if m else None, state, m["similarity"] if m else None, dz_run))


def _merge_speakers(conn, sid: str, dz_run: str, merges: list[dict]) -> None:
    """Speakers identify named as the same known person (people plan 4.3) are merged, as a fix like
    the owner's own: Undo takes it back, and it isn't made again. Not against their answers: never a speaker they
    said isn't that person, or confirmed as someone else; one he confirmed is the one kept."""
    from .corrections import add, for_run
    if not merges:
        return
    fixes = for_run(conn, sid, dz_run)
    said = {r["speaker"]: r for r in rows(conn, "SELECT speaker, state, person_id FROM session_speakers "
                                                "WHERE session_id=? AND diarize_run=?", sid, dz_run)}
    for mg in merges:
        a, b, pid = mg["from"], mg["into"], mg["person_id"]
        if any((f["kind"] == "merge" and f["from"] in (a, b))
               or (f["kind"] == "declined" and f["of"] == "merge" and {f["letter"], f.get("into")} == {a, b}) for f in fixes):
            continue  # one of them already merged away (by the owner or the lab), or the owner undid this merge before
        answers = [said.get(x) for x in (a, b)]
        if any(r and (r["state"] == "rejected" or (r["state"] == "confirmed" and r["person_id"] != pid)) for r in answers):
            continue
        if (said.get(a) or {}).get("state") == "confirmed" and (said.get(b) or {}).get("state") != "confirmed":
            a, b = b, a  # keep the speaker the owner confirmed
        add(conn, sid, dz_run, "merge", {"from": a, "into": b, "by": "lab"})
        fixes = for_run(conn, sid, dz_run)


def _split_speakers(conn, sid: str, dz_run: str, speakers: list[dict]) -> None:
    """A speaker identify heard as two or more known people (people plan 4.2) is split, as a fix like
    the owner's own (Undo takes it back, and it isn't made again), and each part named by its own voice. Not a
    speaker the owner has answered for or merged: their word wins. A split already there (carried from an earlier run, or made
    on an earlier identify) stays as it is; its parts are named if they aren't yet."""
    from .corrections import add, for_run, next_letter
    if not any(sp.get("split") for sp in speakers):
        return
    fixes = for_run(conn, sid, dz_run)
    dz = load_json((row(conn, "SELECT output FROM stage_runs WHERE id=?", dz_run) or {}).get("output")) or {}
    taken = set(dz.get("speakers", [])) | {f["into"] for f in fixes if f["kind"] == "split"}
    state = lambda letter: (row(conn, "SELECT state FROM session_speakers WHERE session_id=? AND speaker=? "
                                      "AND diarize_run=?", sid, letter, dz_run) or {}).get("state")
    for sp in speakers:
        parts, letter = sp.get("split"), sp["speaker"]
        if not parts:
            continue
        intos = [f["into"] for f in fixes if f["kind"] == "split" and f["letter"] == letter]
        if not intos:
            if state(letter) in ("confirmed", "rejected") or any(
                    (f["kind"] == "merge" and (f["from"] == letter or (letter == f["into"] and f.get("by") != "lab")))
                    or (f["kind"] == "declined" and f["of"] == "split" and f["letter"] == letter) for f in fixes):
                continue  # The owner answered or merged it, it was merged away, or they undid this split before
            for part in parts[1:]:
                into = next_letter(taken)
                taken.add(into)
                add(conn, sid, dz_run, "split", {"letter": letter, "into": into, "spans": part["spans"], "by": "lab"})
                intos.append(into)
        for lt, part in zip([letter, *intos], parts):
            if state(lt) not in ("confirmed", "rejected"):
                _suggest(conn, sid, lt, part.get("match"), dz_run)


def _apply_identify(conn, run: dict) -> None:
    """Record who each speaker is (suggestions; the owner's confirmations win), and apply D10."""
    from .stages import remap_speakers
    out = load_json(run["output"])
    sid, dz_run = run["target"], out["diarize_run"]

    # Answers given against an older diarize run: carry them over by timing. Answers the owner already
    # gave on this run (between the new diarize and this identify) stay as he gave them; before
    # 4 Oct they were deleted, and his "not this person" answers were never carried.
    stale = rows(conn, "SELECT * FROM session_speakers WHERE session_id=? AND diarize_run IS NOT ? ", sid, dz_run)
    if stale:
        old_run = row(conn, "SELECT output FROM stage_runs WHERE id=?", stale[0]["diarize_run"])
        new_run = row(conn, "SELECT output FROM stage_runs WHERE id=?", dz_run)
        mapping = remap_speakers(load_json(old_run["output"])["turns"], load_json(new_run["output"])["turns"]) \
            if old_run and new_run else {}
        conn.execute("DELETE FROM session_speakers WHERE session_id=? AND diarize_run IS NOT ?", (sid, dz_run))
        for r in stale:
            if r["state"] in ("confirmed", "rejected") and r["speaker"] in mapping:
                conn.execute("INSERT OR IGNORE INTO session_speakers (session_id, speaker, person_id, state, similarity, "
                             "diarize_run) VALUES (?, ?, ?, ?, ?, ?)",
                             (sid, mapping[r["speaker"]], r["person_id"], r["state"], r["similarity"], dz_run))

    for sp in out["speakers"]:
        cur = row(conn, "SELECT state FROM session_speakers WHERE session_id=? AND speaker=?", sid, sp["speaker"])
        if cur and cur["state"] in ("confirmed", "rejected"):  # The owner's answers win over the voice match
            continue
        _suggest(conn, sid, sp["speaker"], sp["match"], dz_run, sp.get("owner_channel", False))
    _merge_speakers(conn, sid, dz_run, out.get("merges") or [])
    _split_speakers(conn, sid, dz_run, out["speakers"])

    # D10 -- a derived state, recalculated whenever identify re-runs; the owner's own "keep" always wins.
    sess = row(conn, "SELECT status, kept, note FROM sessions WHERE id=?", sid)
    auto_discarded = sess["status"] == "discarded" and (sess["note"] or "").startswith("Your voice not heard")
    if not KEEP_ONLY_JAYS:
        if auto_discarded:
            conn.execute("UPDATE sessions SET status='active', note=? WHERE id=?", (PAUSED_NOTE, sid))
        elif out["jay_present"] is False and sess["status"] == "active":
            conn.execute("UPDATE sessions SET note=? WHERE id=? AND note IS NULL", (PAUSED_NOTE, sid))
    elif out["jay_present"] is False and sess["status"] == "active" and not sess["kept"]:
        conn.execute("UPDATE sessions SET status='discarded', note=? WHERE id=?",
                     ("Your voice not heard (D10). Deleted with other raw audio after the retention period; "
                      "restore with: octo keep " + sid, sid))
    elif out["jay_present"] is not False and auto_discarded:
        conn.execute("UPDATE sessions SET status='active', note=? WHERE id=?",
                     (None if out["jay_present"] else "Not sure you're in this one: kept to be safe.", sid))
    elif out["jay_present"] is None and sess["status"] == "active":
        conn.execute("UPDATE sessions SET note=? WHERE id=? AND note IS NULL",
                     ("Not sure you're in this one: kept to be safe.", sid))


# ---- running -------------------------------------------------------------------------------

STOPPED = "it stopped while it ran (the lab or the Mac restarted)"


def recover(conn) -> int:
    """Runs left 'running' by a crash or kill count as a failed attempt, and the planner queues them again as any failure
    (architecture review 5 Nov): put straight back, a step that took the lab down each time (macOS ending it under
    memory pressure) came back for ever."""
    return conn.execute("UPDATE stage_runs SET status='failed', is_current=0, error=?, finished_at=? WHERE status='running'",
                        (STOPPED, time.time())).rowcount


WORKER_TIMEOUT_S = 3 * 3600  # no step has taken more than minutes; one that hangs mustn't hold the queue
_LOCK_FD: int | None = None    # the runner's lock, handed to each worker (review 4 Oct, M39)


def _in_worker(run_id: str) -> tuple[int, str]:
    """The worker inherits the runner's lock: if the runner is killed while a worker lives on, the
    lock stays held, and a new runner says "busy" instead of starting the same step a second time."""
    try:
        proc = subprocess.run([*BACKGROUND, sys.executable, "-m", "octo.worker", run_id], cwd=ROOT, capture_output=True, text=True,
                              pass_fds=(_LOCK_FD,) if _LOCK_FD is not None else (), timeout=WORKER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 1, f"the step took over {WORKER_TIMEOUT_S // 3600} h and was stopped"
    return proc.returncode, proc.stderr


def _still_valid(conn, run: dict) -> bool:
    """A queued run is obsolete if something it builds on was replaced while it waited, or if its
    inputs aren't what the step would use now: another model chosen on the Pipeline page, a new
    stage version, the rubric or speakers changed (2 Oct: switching insights to Claude left six
    queued qwen3:8b runs that would have taken hours)."""
    stage = STAGES.get(run["stage"])
    if stage is None:
        return False
    if stage.level == "session" and not row(conn, "SELECT 1 FROM sessions WHERE id=? AND status IN ('active', 'discarded')",
                                            run["target"]):
        return False  # set aside as not a conversation (B5), deleted, or superseded while it waited
    ups = json.loads(run["upstream"])
    if stage.level == "session" and stage.upstreams == ("find_conversations",):
        sess = row(conn, "SELECT * FROM sessions WHERE id=?", run["target"])
        fc = current(conn, sess["recording_id"], "find_conversations") if sess else None
        if not fc or ups != [_same_conversation(conn, sess, stage.name, fc["id"])]:
            return False
    elif not all(row(conn, "SELECT is_current FROM stage_runs WHERE id=? AND is_current=1", u) for u in ups):
        return False
    if run["stage"] == "insight" and (PL.pinned_review(conn, run["target"]) or held(conn, run["target"])):
        return False  # The owner rated the current one while this waited, or it became a part of one discussion (B35)
    if run["stage"] == "insight" and [u["id"] for u in insight_ups(conn, run["target"]) if u] != ups:
        return False
    return wanted(conn, stage, run["target"], ups)[1] == run["input_hash"]


def insight_ups(conn, session_id: str) -> list[dict | None]:
    """What the review builds on: its diarize run, or every part's in order when it reads one discussion (B35)."""
    ups = STAGES["insight"].upstreams
    return [current(conn, p["id"], u) for p in whole(conn, session_id) or [{"id": session_id}] for u in ups]


def review_outdated(conn, session_id: str) -> bool:
    """A review the owner has rated, kept although what it was made from has changed since (a speaker
    fix, a new name, a re-transcription): the page offers a redo."""
    ins = PL.pinned_review(conn, session_id)
    stage = STAGES.get("insight")
    ups = insight_ups(conn, session_id) if stage else []
    if not ins or not ups or not all(ups):
        return False
    from .privacy import review_held
    return review_held(conn, session_id, ins["id"]) or bool(ins["stale"]) or wanted(conn, stage, session_id, [u["id"] for u in ups])[1] != ins["input_hash"]


def _lock_path(conn) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2] + ".lock")


def run_pending(conn, log: Log = print, execute: Callable[[str], tuple[int, str]] = _in_worker) -> dict[str, int]:
    """Plan → run the oldest queued stage (in a worker subprocess) → apply → repeat until done.

    One runner at a time (the CLI, or the server's background runner): a second one would put the
    first one's running job back on the queue and run it twice. The runner re-plans after every
    stage, so work queued while it's busy is picked up without a second runner."""
    counts = {"done": 0, "failed": 0, "skipped": 0, "busy": 0}
    with open(_lock_path(conn), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("another octo process is already running; it will pick this up")
            counts["busy"] = 1
            return counts
        global _LOCK_FD
        _LOCK_FD = lock.fileno()
        try:
            return _drain(conn, log, execute, counts)
        finally:
            _LOCK_FD = None


def next_run(conn, now: float | None = None) -> dict | None:
    """Earlier stages first, then oldest. Quick jobs (finding conversations, recognising voices)
    never wait behind an hour-long insight pass, and everything reaches "confirm who's who" -- the
    step that needs the owner -- as soon as it can.

    Except a Claude review, which goes right after finding conversations: it takes a minute, and
    it's what the owner is waiting for once they have confirmed speakers (4 Oct: two waited 25 min behind
    Hindi transcripts and then hit the usage limit). A local model's review keeps its place last.
    While Claude's usage limit holds, steps that need Claude wait and the rest carry on; so do steps whose speech models
    are still downloading (a new lab)."""
    from . import models as MD
    order = " ".join(f"WHEN '{name}' THEN {i}" for i, name in enumerate(STAGES))
    blocked = claude_blocked_until(conn, now)
    budgets: dict[str, float | None] = {}  # each connection's own budget, asked once per call
    downloading: dict[str, bool] = {}  # each step's models, looked at once per call
    heavy: list = []  # the hold on heavy steps, looked at once per call
    for run in rows(conn, "SELECT * FROM stage_runs WHERE status='queued' ORDER BY CASE "
                          "WHEN stage='find_conversations' THEN 0 "
                          "WHEN stage='insight' AND json_extract(params, '$.model') LIKE 'claude%' THEN 1 ELSE 2 END, "
                          f"CASE stage {order} ELSE 99 END, "
                          # within a step, the newest conversation first (M11): it's the one the owner is waiting on
                          "(SELECT coalesce(r.started_at + s.start_s, r.created_at) FROM sessions s JOIN recordings r "
                          "ON r.id = s.recording_id WHERE s.id = stage_runs.target) DESC, queued_at"):
        if blocked and uses_claude(run):
            continue
        if run["stage"] not in downloading:
            downloading[run["stage"]] = bool(MD.missing_for(conn, run["stage"]))
        if downloading[run["stage"]]:
            continue
        if run["stage"] in HEAVY_STAGES:
            if not heavy:
                heavy.append(heavy_hold(conn, now if now is not None else time.time()))
            if heavy[0]:
                continue
        who = run_reader(run) or {}
        if who.get("connection"):
            if who["connection"] not in budgets:
                budgets[who["connection"]] = budget_blocked_until(conn, who["connection"], now) \
                    or connection_refused(conn, who["connection"])
            if budgets[who["connection"]]:
                continue
        return run
    return None


def _empty_review(output: str | None) -> bool:
    """A Claude review with no summary, nothing to rate and no reason. (A local model's pass can
    honestly find no moments; it lists the principles under no_moment.)"""
    out = load_json(output) if output else None
    return isinstance(out, dict) and out.get("kind") == "review" and not out.get("skipped") \
        and not out.get("summary") and not R.items(out)


def _drain(conn, log: Log, execute: Callable[[str], tuple[int, str]], counts: dict[str, int]) -> dict[str, int]:
    recover(conn)
    while True:
        from .deploy import paused
        from .drills import find_points, run_queued
        if not paused(ROOT):
            run_queued(conn, log, _LOCK_FD)  # B18: a practice take first, the owner is waiting on it
            find_points(conn, log)  # and where its point came
        plan(conn)
        run = next_run(conn)
        if not run:
            return counts
        if not _still_valid(conn, run):
            conn.execute("UPDATE stage_runs SET status='obsolete', finished_at=? WHERE id=?", (time.time(), run["id"]))
            counts["skipped"] += 1
            continue
        if paused(ROOT):  # `octo deploy` is changing this copy's code; the restarted lab carries on
            log("held back: the code is being updated (octo deploy)")
            return counts
        log(f"{run['stage']} · {run['target']}")
        t0 = time.time()
        code, stderr = execute(run["id"])
        run_id, run = run["id"], row(conn, "SELECT * FROM stage_runs WHERE id=?", run["id"])
        if run is None:  # The owner deleted the conversation (or part of it) while this was running
            log(f"  dropped: {run_id} was deleted while it ran")
            counts["skipped"] += 1
            continue
        if run["status"] == "done" and run["stage"] == "insight" and _empty_review(run["output"]):
            # Nothing to read or rate, and no reason given: an answer that went wrong, not a review.
            # Kept as done, it would show as "reviewed" but never count, and be reused as it is.
            conn.execute("UPDATE stage_runs SET status='failed', error=? WHERE id=?",
                         ("the review came back empty", run["id"]))
            run = row(conn, "SELECT * FROM stage_runs WHERE id=?", run["id"])
        if run["status"] == "done" and run["output"] is not None:
            # The worker saved its result, then exited non-zero: a crash on the way out (native
            # teardown, 2 Oct). The result is whole -- written in one UPDATE -- so keep it.
            _make_current(conn, run)
            counts["done"] += 1
            from .eta import expected
            want = expected(conn, run)  # beside what it took: how good the ETA is (B3)
            log(f"  done in {time.time() - t0:.1f} s" + (f" (expected {want:.0f} s)" if want else "")
                + (f" (worker exited {code} after saving; kept)" if code else ""))
        else:
            err = run.get("error") or (stderr.strip().splitlines() or ["worker exited"])[-1]
            if run["status"] != "failed":
                conn.execute("UPDATE stage_runs SET status='failed', error=?, finished_at=? WHERE id=?",
                             (str(err), time.time(), run["id"]))
            conn.execute("UPDATE stage_runs SET finished_at=? WHERE id=? AND finished_at IS NULL", (time.time(), run["id"]))
            counts["failed"] += 1
            log(f"  FAILED: {err}")
            conn.execute("UPDATE stage_runs SET is_current=0 WHERE id=?", (run["id"],))
            # Carry on with everything else; held_back() decides when this one is tried again.
