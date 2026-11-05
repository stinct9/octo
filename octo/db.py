"""SQLite store. One file, WAL mode, safe for the CLI, the server and worker subprocesses at once.

Stage outputs are immutable: re-running a stage creates a new stage_run and moves the
"current" pointer; downstream runs built on the old one are marked stale. Anything that cites a
transcript (insights, feedback) points at a specific stage_run, so re-runs never orphan it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from . import AUDIO, DATA, DB_PATH, HOME, HOME_FROM, NOTES, ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,            -- device | phone | bench | file
    path        TEXT NOT NULL,            -- 16 kHz mono WAV under audio/lab/
    original    TEXT,                     -- where it came from
    started_at  REAL,                     -- wall-clock start if known
    duration_s  REAL NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (      -- one conversation found inside a recording
    id           TEXT PRIMARY KEY,
    recording_id TEXT NOT NULL REFERENCES recordings(id),
    start_s      REAL NOT NULL,
    end_s        REAL NOT NULL,
    speech_s     REAL NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',   -- active | discarded (owner not heard, D10) | superseded | set_aside (B5) | deleted
    note         TEXT,                             -- why, when discarded
    created_at   REAL NOT NULL,
    UNIQUE (recording_id, start_s)
);
CREATE TABLE IF NOT EXISTS stage_runs (
    id          TEXT PRIMARY KEY,
    stage       TEXT NOT NULL,
    target      TEXT NOT NULL,            -- recording or session id
    input_hash  TEXT NOT NULL,            -- stage version + params + upstream run ids
    status      TEXT NOT NULL,            -- queued | running | done | failed | obsolete
    is_current  INTEGER NOT NULL DEFAULT 0,
    stale       INTEGER NOT NULL DEFAULT 0,
    upstream    TEXT NOT NULL DEFAULT '[]',   -- JSON list of stage_run ids this was built on
    params      TEXT NOT NULL DEFAULT '{}',
    output      TEXT,                     -- JSON
    error       TEXT,
    queued_at   REAL NOT NULL,
    started_at  REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS people (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    is_self    INTEGER NOT NULL DEFAULT 0,   -- the owner
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS voiceprints (    -- only for people the owner has labelled (D1)
    id         TEXT PRIMARY KEY,
    person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    embedding  BLOB NOT NULL,                -- float32 vector
    seconds    REAL NOT NULL,                -- speech it was computed from
    source     TEXT NOT NULL,                -- "enrol:<file>" or "label:<session>:<speaker>"
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session_speakers (   -- who each diarized speaker is
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    speaker     TEXT NOT NULL,                  -- A, B, C… from the current diarize run
    person_id   TEXT REFERENCES people(id) ON DELETE SET NULL,
    state       TEXT NOT NULL,                  -- suggested | ambiguous | confirmed | unknown
    similarity  REAL,
    diarize_run TEXT,                           -- which diarize run the letter refers to
    PRIMARY KEY (session_id, speaker)
);
CREATE TABLE IF NOT EXISTS feedback (          -- the owner's answers on one insight, tied to that insight version
    insight_run TEXT NOT NULL REFERENCES stage_runs(id),
    item        TEXT NOT NULL,                  -- p8 finding on principle 8 · t8 its try-next-time · b0 a question
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    verdict     TEXT,                           -- right | partly | wrong
    novelty     TEXT,                           -- new | knew
    useful      TEXT,                           -- beneath-the-surface questions: yes | no
    acted       INTEGER NOT NULL DEFAULT 0,     -- "I acted on this"
    note        TEXT,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (insight_run, item)
);
CREATE TABLE IF NOT EXISTS review_answers (    -- the one question per conversation, since 6 Oct (octo/review.py)
    insight_run TEXT PRIMARY KEY REFERENCES stage_runs(id),
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    nothing_new INTEGER NOT NULL DEFAULT 0,      -- 1: "Nothing new this time"; else the items picked (feedback.novelty)
    answered_at REAL NOT NULL                    -- first answered; changing the picks keeps it
);
CREATE TABLE IF NOT EXISTS filler_words (      -- the owner's own filler list (octo/fillers.py, 6 Oct)
    phrase     TEXT PRIMARY KEY,                 -- lower case: "um", "you know", "basically"
    source     TEXT NOT NULL,                    -- default | suggested | typed
    added_at   REAL NOT NULL,
    removed_at REAL                              -- off the list; kept so the defaults don't come back
);
CREATE TABLE IF NOT EXISTS question_checks (   -- B4: the owner's ear on a question the app counted (octo/questions.py)
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    start_s     REAL NOT NULL,                   -- the question, in conversation time
    line        INTEGER NOT NULL,                -- its line then (lines are renumbered when worked out again)
    text        TEXT NOT NULL,                   -- the question as transcribed then
    app         TEXT NOT NULL,                   -- who the app said spoke next: you | other | nobody
    verdict     TEXT NOT NULL,                   -- answered | unanswered | not_question
    created_at  REAL NOT NULL,
    UNIQUE (session_id, start_s)
);
CREATE TABLE IF NOT EXISTS filler_checks (     -- the filler check by ear (S0): the owner's count against the app's
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    start_s     REAL NOT NULL,                   -- the stretch, in conversation time
    end_s       REAL NOT NULL,
    by_ear      INTEGER NOT NULL,                -- the owner's taps
    by_app      INTEGER NOT NULL,                -- the app's count in his lines there, with his list then
    words       INTEGER NOT NULL,
    phrases     TEXT NOT NULL,                   -- that list (JSON)
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS corrections (       -- the owner's who-said-what fixes (octo/corrections.py)
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    diarize_run TEXT NOT NULL,                  -- the run whose letters and line ids they refer to
    kind        TEXT NOT NULL,                  -- merge {"from","into"} | line {"line","to"}
    data        TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (           -- paired phones (octo/phone.py); only a hash of each token
    id           TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    paired_at    REAL NOT NULL,
    last_used_at REAL
);
CREATE TABLE IF NOT EXISTS device_boots (      -- the recorder's boots (octo/device.py)
    boot        INTEGER PRIMARY KEY,
    manifest    TEXT NOT NULL DEFAULT '',       -- the device's manifest for that boot, as pulled so far
    removed_at  REAL                            -- its directory deleted from the card
);
CREATE TABLE IF NOT EXISTS device_chunks (      -- 60 s chunks pulled from the recorder
    boot          INTEGER NOT NULL,
    seq           INTEGER NOT NULL,
    seg           INTEGER,                      -- continuous stretch of recording within the boot
    start_ms      INTEGER,                      -- ms after boot
    samples       INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    speech_s      REAL,                         -- voice activity, decides where recordings are cut
    path          TEXT,                         -- until joined into a recording
    pulled_at     REAL NOT NULL,
    deleted_on_device_at REAL,
    recording_id  TEXT,                         -- the recording it went into, or 'silent' (deleted)
    PRIMARY KEY (boot, seq)
);
CREATE TABLE IF NOT EXISTS device_bad_chunks (  -- chunks that failed their checksum, per sync (octo/device.py)
    boot   INTEGER NOT NULL,
    seq    INTEGER NOT NULL,
    seg    INTEGER,                         -- its stretch waits while it's being retried
    tries  INTEGER NOT NULL,
    PRIMARY KEY (boot, seq)
);
CREATE TABLE IF NOT EXISTS splits (            -- "two discussions here" (octo/split.py)
    recording_id TEXT NOT NULL REFERENCES recordings(id),
    at_s         REAL NOT NULL,                 -- recording time
    created_at   REAL NOT NULL,
    by           TEXT NOT NULL DEFAULT 'owner', -- owner | lab (people plan 4.5: when the change is clear)
    undone_at    REAL,                          -- joined back: kept, so the lab doesn't split there again
    PRIMARY KEY (recording_id, at_s)
);
CREATE TABLE IF NOT EXISTS parts (             -- one discussion in parts (octo/parts.py, B35): read as one at the review
    first_id     TEXT NOT NULL,                 -- a conversation (sessions.id)
    second_id    TEXT NOT NULL,                 -- the one right after it, recorded apart: an hour cut, a pause, another recording
    by           TEXT NOT NULL DEFAULT 'owner', -- owner | lab
    kind         TEXT,                          -- the lab's reason: hour | pause | recordings
    created_at   REAL NOT NULL,
    undone_at    REAL,                          -- "Not one discussion": kept, so the lab never groups them again
    PRIMARY KEY (first_id, second_id)
);
CREATE INDEX IF NOT EXISTS parts_second ON parts (second_id);
CREATE TABLE IF NOT EXISTS ticktick_tasks (    -- review tasks the owner sent to TickTick (octo/ticktick.py)
    insight_run TEXT NOT NULL REFERENCES stage_runs(id),
    item        TEXT NOT NULL,                  -- n0, n1… in that review's "What to do"
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    task_id     TEXT NOT NULL,
    project_id  TEXT,
    url         TEXT,
    added_at    REAL NOT NULL,
    PRIMARY KEY (insight_run, item)
);
CREATE TABLE IF NOT EXISTS settings (          -- the owner's model choice per pipeline step (octo/settings.py)
    stage       TEXT PRIMARY KEY,
    choice      TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_config (        -- the lab's own settings, one JSON value per key (product plan, 7 Oct)
    key         TEXT PRIMARY KEY,              -- e.g. claude_code: this lab may use the Mac's Claude Code login
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rubric_versions (    -- each rubric file seen, and what of it the review reads (planning.py)
    version   TEXT PRIMARY KEY,                 -- insight.rubric_version: the whole file
    sections  TEXT NOT NULL                     -- the principles and "How the review applies it" only
);
CREATE TABLE IF NOT EXISTS gate_result (       -- the gate's counts as they stood on its end date (octo/review.py)
    gate_end    TEXT PRIMARY KEY,                -- 2026-11-05
    result      TEXT NOT NULL,                   -- JSON: counts, checks, passed
    stored_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS focus (               -- what the owner is practising (octo/focus.py)
    id           TEXT PRIMARY KEY,
    text         TEXT NOT NULL,
    session_id   TEXT REFERENCES sessions(id) ON DELETE SET NULL,  -- the review it came from, if any
    insight_run  TEXT,
    item         TEXT,                                            -- n0, p8, l1… in that review
    started_at   REAL NOT NULL,
    ended_at     REAL,
    marked_acted INTEGER NOT NULL DEFAULT 0                       -- 1: a "did" check marked the item acted on
);
CREATE TABLE IF NOT EXISTS focus_checks (        -- did the focus come up in this conversation?
    focus_id    TEXT NOT NULL REFERENCES focus(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    answer      TEXT NOT NULL,                   -- did | missed | na
    updated_at  REAL NOT NULL,
    PRIMARY KEY (focus_id, session_id)
);
CREATE TABLE IF NOT EXISTS coach_notes (         -- the coach: one Claude call on demand (octo/coach.py, 6 Oct)
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    status      TEXT NOT NULL,                   -- running | done | failed
    model       TEXT NOT NULL,
    read        TEXT,                            -- JSON: how many conversations, items, from when to when
    output      TEXT,                            -- JSON: the note as checked (themes, evidence, dropped)
    usage       TEXT,                            -- JSON, from Claude Code
    error       TEXT,
    finished_at REAL,
    seen_at     REAL                             -- the owner opened it (home stops saying "new")
);
CREATE TABLE IF NOT EXISTS coach_sources (       -- every conversation a note read: deleting one deletes the note
    note_id     TEXT NOT NULL REFERENCES coach_notes(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL,
    PRIMARY KEY (note_id, session_id)
);
CREATE TABLE IF NOT EXISTS coach_feedback (      -- the owner's marks on a note: not the gate, whether the coach earns its place
    note_id     TEXT NOT NULL REFERENCES coach_notes(id) ON DELETE CASCADE,
    part        TEXT NOT NULL,                   -- work_on | keep | also0 | also1
    verdict     TEXT,                            -- right | partly | wrong
    novelty     TEXT,                            -- new | knew
    updated_at  REAL NOT NULL,
    PRIMARY KEY (note_id, part)
);
CREATE TABLE IF NOT EXISTS solved (              -- B38, goal 2: who has solved this, in the owner's words (octo/solved.py)
    focus_id    TEXT UNIQUE REFERENCES focus(id) ON DELETE CASCADE,
    note_id     TEXT UNIQUE REFERENCES coach_notes(id) ON DELETE CASCADE,  -- on that note's "work on"
    who         TEXT NOT NULL,
    what        TEXT NOT NULL,
    source      TEXT,                            -- a book's or a talk's title, if the owner gave one
    updated_at  REAL NOT NULL,
    CHECK ((focus_id IS NULL) != (note_id IS NULL))
);
CREATE TABLE IF NOT EXISTS commitments (       -- B14: what was promised, both ways (octo/commitments.py)
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    run_id       TEXT NOT NULL,                  -- the commitments run that found it
    by_self      INTEGER NOT NULL,               -- 1: the owner promised; 0: someone promised the owner
    by_person    TEXT,                           -- people.id, or NULL for an unnamed voice (no FK: a merge of
    to_person    TEXT,                           --   two people moves these, forgetting one deletes the row)
    by_name      TEXT NOT NULL,                  -- as the transcript named them then
    to_name      TEXT NOT NULL,
    what         TEXT NOT NULL,
    line         INTEGER NOT NULL,               -- the quote's line in that run's transcript
    quote        TEXT NOT NULL,
    start_s      REAL NOT NULL,                  -- the line, in conversation time (to hear it)
    end_s        REAL NOT NULL,
    due          TEXT,                           -- YYYY-MM-DD, only if a date was said
    state        TEXT,                           -- NULL open · done | dropped (the owner's) · did | didnt (theirs)
    state_at     REAL,
    ticktick     TEXT,                           -- JSON {"task_id", "project_id", "url"} once sent
    created_at   REAL NOT NULL,
    checked      TEXT,                           -- the owner's check of the step (#/commitments/check): right | partly | wrong
    checked_at   REAL
);
CREATE INDEX IF NOT EXISTS commitments_session ON commitments (session_id);
CREATE TABLE IF NOT EXISTS intentions (        -- B15: "Next time with <person>, I want to…" (octo/intention.py)
    id           TEXT PRIMARY KEY,
    person_id    TEXT NOT NULL,                  -- no FK (as commitments): a merge moves it, forgetting them deletes it
    text         TEXT NOT NULL,
    created_at   REAL NOT NULL,
    session_id   TEXT,                           -- NULL: open, one per person; else the conversation that took it,
    taken_at     REAL                            --   whose review reads it
);
CREATE UNIQUE INDEX IF NOT EXISTS intentions_open ON intentions (person_id) WHERE session_id IS NULL;
CREATE INDEX IF NOT EXISTS intentions_session ON intentions (session_id);
CREATE TABLE IF NOT EXISTS reflections (       -- B15: "How did that go, in your words?"; the coach reads it
    session_id   TEXT PRIMARY KEY REFERENCES sessions(id),
    text         TEXT NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS marks (             -- B16: "this mattered", marked as it happened (octo/marks.py)
    id           TEXT PRIMARY KEY,
    at           REAL,                           -- seconds since 1970, by the Mac's clock (the recorder's boot clock, set by it);
                                                 --   NULL: deleted with its conversation, the row kept so the manifest isn't read again
    source       TEXT NOT NULL,                  -- recorder (a double tap) | phone (the Mark button)
    boot         INTEGER,                        -- the recorder's: its boot and milliseconds, so a re-read adds nothing
    at_ms        INTEGER,
    created_at   REAL NOT NULL,
    UNIQUE (boot, at_ms)
);
CREATE INDEX IF NOT EXISTS marks_at ON marks (at);
CREATE TABLE IF NOT EXISTS drills (            -- B18: a practice take, never a conversation (K4; octo/drills.py)
    id              TEXT PRIMARY KEY,
    prompt          TEXT NOT NULL,                -- what the owner was asked to talk about, as it read then
    path            TEXT NOT NULL,                -- FLAC under audio/lab/drills/, relative to HOME
    duration_s      REAL NOT NULL,
    created_at      REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',   -- queued | done | failed
    error           TEXT,
    transcript      TEXT,                         -- JSON: Qwen's segments with their timed words
    audio_deleted_at REAL,                        -- raw audio dropped after RAW_AUDIO_DAYS (D5); the numbers stay
    point           TEXT,                         -- JSON: where the point came (B18, 22 Oct): one call, its quote checked
    point_error     TEXT,                         -- why that call failed (a usage limit is tried again once it resets)
    point_at        REAL                          -- when it was answered, or failed
);
CREATE TABLE IF NOT EXISTS asks (                -- B24: questions to the conversations; their words only once kept
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    status      TEXT NOT NULL,                   -- running | done | failed
    reader      TEXT,                            -- JSON: the model it went to (settings.reader): the day's count
    usage       TEXT,                            -- JSON; NULL when nothing was picked, so nothing was asked
    error       TEXT,
    finished_at REAL,
    kept_at     REAL,                            -- the owner kept it; until then the words live in memory only
    question    TEXT,                            -- only while kept
    answer      TEXT                             -- JSON, only while kept: the checked answer and what was sent
);
CREATE TABLE IF NOT EXISTS ask_sources (         -- every conversation a question sent lines of: deleting one deletes it
    ask_id      TEXT NOT NULL REFERENCES asks(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL,
    PRIMARY KEY (ask_id, session_id)
);
CREATE INDEX IF NOT EXISTS ask_sources_session ON ask_sources (session_id);
CREATE TABLE IF NOT EXISTS rehearsals (        -- B26: a typed rehearsal, never a conversation (K4; octo/rehearse.py)
    id           TEXT PRIMARY KEY,
    person_id    TEXT REFERENCES people(id) ON DELETE CASCADE,  -- who the model plays; NULL: someone described
    who          TEXT,                           -- that description, in the owner's words
    hard         TEXT NOT NULL,                  -- what's hard about it, in the owner's words
    want         TEXT NOT NULL,                  -- what they want from it
    turns        TEXT NOT NULL DEFAULT '[]',     -- JSON: [{"by": "me" | "them", "text", "at", "reader"?, "usage"?}]
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    review       TEXT,                           -- JSON: the review once stopped, its quotes checked
    review_at    REAL,
    error        TEXT,                           -- the last call's failure (a usage limit holds Claude's steps)
    error_at     REAL
);
CREATE TABLE IF NOT EXISTS events (           -- B23: the owner's calendar, read only, whatever the source (octo/events.py)
    id           TEXT PRIMARY KEY,               -- the source, its own id and the start: one row per instance
    source       TEXT NOT NULL,                  -- mac (the Octo app's EventKit) | ics:<link id>
    title        TEXT NOT NULL,                  -- as the calendar has it
    start        REAL NOT NULL,
    end          REAL NOT NULL,
    invitees     TEXT NOT NULL DEFAULT '[]',     -- JSON: [{"name", "email"}], the owner and those who declined left out
    seen_at      REAL NOT NULL,                  -- the source last had it
    announced_at REAL                            -- its "before you talk" banner went
);
CREATE INDEX IF NOT EXISTS events_start ON events (start);
CREATE TABLE IF NOT EXISTS invitee_matches (  -- B23: this invitee is this person, as the owner said once
    invitee      TEXT PRIMARY KEY,               -- their email, or "name:" and their name when there's none
    person_id    TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS talks (            -- B31: the owner talking a conversation through with the review's model (octo/talk.py)
    session_id   TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    turns        TEXT NOT NULL DEFAULT '[]',     -- JSON: [{"by": "me" | "model", "text", "at", "moments"?, "dropped"?, "reader"?, "usage"?}]
    status       TEXT NOT NULL DEFAULT 'idle',   -- idle | running (a reply being asked, in the server's thread)
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    error        TEXT,                           -- the last call's failure (a usage limit holds Claude's steps)
    error_at     REAL
);
CREATE TABLE IF NOT EXISTS preparations (     -- B36: a plan for the next conversation with someone (octo/prepare.py)
    id           TEXT PRIMARY KEY,
    person_id    TEXT NOT NULL,                  -- no FK (as intentions): forgetting them deletes it; one per person
    about        TEXT,                           -- the owner's words for what the conversation is
    names        TEXT NOT NULL DEFAULT '[]',     -- JSON: other spellings the owner added, to find mentions
    status       TEXT NOT NULL,                  -- running | done | failed
    created_at   REAL NOT NULL,
    finished_at  REAL,
    reader       TEXT,                           -- JSON: the model it went to (pipeline._calls_since)
    usage        TEXT,                           -- JSON, once done
    plan         TEXT,                           -- JSON: the plan, its quotes checked
    sent         TEXT,                           -- JSON: which conversations went, how many lines, with them or mentioning them (ids and counts, no words)
    event_title  TEXT,                           -- the calendar event it was made for, if any
    event_start  REAL,                           --   when it starts (its length shapes the plan)
    event_end    REAL,                           --   and when it ends (ends the plan if no conversation does)
    error        TEXT,                           -- a failure (a usage limit holds Claude's steps)
    error_at     REAL
);
CREATE TABLE IF NOT EXISTS preparation_sources (  -- B36: the conversations a plan read; deleting one deletes the plan
    prep_id      TEXT NOT NULL REFERENCES preparations(id) ON DELETE CASCADE,
    session_id   TEXT NOT NULL,
    PRIMARY KEY (prep_id, session_id)
);
CREATE INDEX IF NOT EXISTS preparation_sources_session ON preparation_sources (session_id);
-- Every call to a model, made or billed (architecture review 5 Nov, risk 7): written by claude_code.ask and providers.ask,
-- which every call goes through, and counted for the day's calls (pipeline._calls_since). A call refused before any work
-- (a key refused, a rate limit, an outage, Claude's usage limit) leaves none. `ref` is the row it was for (a run, a note,
-- a question), never a conversation's or a person's id. No words; kept 30 days.
CREATE TABLE IF NOT EXISTS model_calls (
    at       REAL NOT NULL,
    reader   TEXT NOT NULL,                      -- JSON: {"kind", "model", "connection"?}; never a key
    purpose  TEXT NOT NULL DEFAULT '',           -- the step or feature: insight, coach, ask, …
    ref      TEXT,
    ok       INTEGER NOT NULL                    -- 1 answered; 0 failed after it may have been billed (a timeout, cut off)
);
CREATE INDEX IF NOT EXISTS model_calls_at ON model_calls (at);
CREATE INDEX IF NOT EXISTS coach_sources_session ON coach_sources (session_id);
CREATE INDEX IF NOT EXISTS stage_runs_target ON stage_runs (target, stage);
CREATE INDEX IF NOT EXISTS stage_runs_status ON stage_runs (status);
-- Child tables read per conversation, and scanned by every delete's foreign-key check (architecture review 9, 28 Oct)
CREATE INDEX IF NOT EXISTS corrections_session ON corrections (session_id, diarize_run);
CREATE INDEX IF NOT EXISTS feedback_session ON feedback (session_id);
CREATE INDEX IF NOT EXISTS review_answers_session ON review_answers (session_id);
CREATE INDEX IF NOT EXISTS filler_checks_session ON filler_checks (session_id);
CREATE INDEX IF NOT EXISTS ticktick_tasks_session ON ticktick_tasks (session_id);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"


# The schema's version, kept in the database (PRAGMA user_version). Bump it with every change to SCHEMA or
# _migrate: a database already at it opens without either, and one behind is brought up to it once, under one
# write lock (review 8 Oct, finding 1). Before, every connection ran both, unguarded: the server opens several
# at once when it starts, and on a new database (or after an upgrade adding a column) one connection's ALTER
# raced another's ("duplicate column", reproduced 102 times in 30 new databases).
SCHEMA_VERSION = 24  # 2: lab_origin (8 Oct, finding 8); 3: splits by the lab, joined back (9 Oct); 4: set aside (13 Oct, B5);
# 5: question checks by ear (13 Oct, B4); 6: commitments, and from when they're looked for (20 Oct, B14);
# 7: intentions and reflections (21 Oct, B15); 8: marks (21 Oct, B16); 9: drills (21 Oct, B18)
# 10: the owner's check of each commitment (21 Oct, B14)
# 11: where the point came in each practice take (22 Oct, B18)
# 12: questions to the conversations, and what each read (27 Oct, B24)
# 13: rehearsals (27 Oct, B26)
# 14: calendar events and kept invitee matches (27 Oct, B23)
# 15: talking a conversation through (28 Oct, B31)
# 16: model calls whose rows were deleted, kept for the day's count (28 Oct, audit)
# 17: indexes on the child tables read per conversation (28 Oct, architecture review)
# 18: joins, one discussion in two conversations (29 Oct, B35)
# 19: plans for the next conversation with someone, and what each read (30 Oct, B36)
# 20: parts of one discussion, joined at the review; 18's joins carried over and dropped (30 Oct, B35)
# 21: the first owner's name out of stored values: lab_origin, who split or joined, D10's note (31 Oct, D9)
# 22: preparations' event columns for a lab made at 19 before B36's fixes (5 Nov, found by tests/test_db_upgrade.py)
# 23: one row per model call (model_calls), 16's calls_gone and its triggers dropped (5 Nov, architecture review risk 7)
# 24: who has solved this, on a focus or a coach note (5 Nov, B38)


def private() -> None:
    """The lab's folders open to its owner only, and what it makes from here on too (security review M1, 28 Oct):
    another macOS user on this Mac could otherwise read every transcript and recording off the disk, port or no port."""
    os.umask(0o077)
    for d in ([HOME] if HOME != ROOT else []) + [DATA, AUDIO.parent, NOTES, HOME / "runs"]:
        if d.exists():
            d.chmod(0o700)
    for f in DATA.glob("octo.db*"):
        f.chmod(0o600)


_made_private = False


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if path == DB_PATH and not path.exists() and HOME_FROM != "checkout" and os.environ.get("OCTO_NEW_LAB") != "1":
        # A home set on purpose with no lab in it is a typo or a folder that moved, not a new lab: an empty one
        # would start quietly, with a new lab's settings (14 Oct, review of the move out of ~/Downloads).
        raise RuntimeError(f"no lab at {path} ({HOME_FROM} says {HOME}); OCTO_NEW_LAB=1 starts a new one there")
    path.parent.mkdir(parents=True, exist_ok=True)
    global _made_private
    if path == DB_PATH and not _made_private:  # once a process (review 5 Nov): a glob and 8 chmods on every request before
        private()
        _made_private = True
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # autocommit; explicit txns below
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # Deleted rows are overwritten, not left in free pages (review 8 Oct, finding 14): a withdrawn agreement, a
    # forgotten person or a deleted conversation is gone from this file, not just unlisted. The daily backups
    # keep theirs for up to 14 days (octo/backup.py). Per connection, so every connection sets it.
    conn.execute("PRAGMA secure_delete=ON")
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > SCHEMA_VERSION:
        # Made by a newer Octo (an update, then `octo update --back` or an older copy): this code doesn't know its tables,
        # and would write to them as if it did (5 Nov review, risk 3).
        conn.close()
        raise RuntimeError(f"the lab's database ({path}) is from a newer Octo (schema {have}; this code reads up to "
                           f"{SCHEMA_VERSION}): not opened, nothing changed. Run the newer Octo again (`octo update`)")
    if have < SCHEMA_VERSION:
        if have:
            snapshot(conn, path, have)
        _prepare(conn)
    return conn


def snapshot(conn: sqlite3.Connection, path: Path, version: int) -> Path | None:
    """The database as it was before a migration changes it (5 Nov review, risk 3): data/backups/pre-schema-<n>.db
    beside the daily copies, swept with them after 14 days (octo/backup.py). A copy made after another connection had
    already migrated (VACUUM can't run inside the migration's lock) says so in its user_version, and is dropped."""
    if not path.exists():  # a database in memory
        return None
    dest = path.parent / "backups" / f"pre-schema-{version}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}")
    tmp.unlink(missing_ok=True)
    conn.execute("VACUUM INTO ?", (str(tmp),))
    os.chmod(tmp, 0o600)  # it holds voiceprints: readable by the owner only
    copy = sqlite3.connect(tmp)
    same = copy.execute("PRAGMA user_version").fetchone()[0] == version
    copy.close()
    if not same:
        tmp.unlink()
        return None
    os.replace(tmp, dest)
    return dest


def _statements(script: str) -> Iterator[str]:
    """SCHEMA one statement at a time (executescript would commit the transaction it runs in)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf
            buf = ""


def _prepare(conn: sqlite3.Connection) -> None:
    """Bring the database up to SCHEMA_VERSION: tables, then columns added since, in one transaction.
    Another connection doing the same waits for this one (BEGIN IMMEDIATE), then finds nothing to do."""
    for _ in range(50):  # WAL can't be set inside a transaction, and a new file answers "locked" at once
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as e:
            if "locked" not in str(e):
                raise
            time.sleep(0.05)
    with tx(conn):
        if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        for stmt in _statements(SCHEMA):
            conn.execute(stmt)
        _migrate(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was created (CREATE IF NOT EXISTS won't). Runs inside
    _prepare's transaction; bump SCHEMA_VERSION when adding to it."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "note" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN note TEXT")
    if "kept" not in cols:  # The owner restored it: never auto-discard again
        conn.execute("ALTER TABLE sessions ADD COLUMN kept INTEGER NOT NULL DEFAULT 0")
    if "reviewed_at" not in cols:  # when every insight was first rated (the gate counts these)
        conn.execute("ALTER TABLE sessions ADD COLUMN reviewed_at REAL")
    if "in_gate" not in cols:  # 0: kept, but not counted toward the gate (e.g. the first lab's two work memos)
        conn.execute("ALTER TABLE sessions ADD COLUMN in_gate INTEGER NOT NULL DEFAULT 1")
    if "redo_review" not in cols:  # the rated review the owner asked to redo (planning.pinned_review)
        conn.execute("ALTER TABLE sessions ADD COLUMN redo_review TEXT")
    if "left_out_spans" not in cols:  # speakers left out of insights, by time, across a "delete a part"
        conn.execute("ALTER TABLE sessions ADD COLUMN left_out_spans TEXT")
    if "provisional" not in cols:  # why its review counts only provisionally, until the owner clears it (J9)
        conn.execute("ALTER TABLE sessions ADD COLUMN provisional TEXT")
    if "aside" not in cols:  # not a conversation, as the owner marked it: tv | music | other (B5, 13 Oct)
        conn.execute("ALTER TABLE sessions ADD COLUMN aside TEXT")
    rec_cols = {r["name"] for r in conn.execute("PRAGMA table_info(recordings)")}
    if "sha256" not in rec_cols:  # the same memo added twice is refused
        conn.execute("ALTER TABLE recordings ADD COLUMN sha256 TEXT")
    if "audio_deleted_at" not in rec_cols:  # raw audio dropped after RAW_AUDIO_DAYS (D5); the rest stays
        conn.execute("ALTER TABLE recordings ADD COLUMN audio_deleted_at REAL")
    vp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(voiceprints)")}
    if "model" not in vp_cols:  # which embedding model made it; prints from different models can't be compared
        conn.execute("ALTER TABLE voiceprints ADD COLUMN model TEXT")
        conn.execute("UPDATE voiceprints SET model='pyannote/wespeaker-voxceleb-resnet34-LM'")  # the only one used so far
    # 2 Oct: the speaker model choice moved from diarize to the new speakers stage.
    conn.execute("UPDATE OR IGNORE settings SET stage='speakers' WHERE stage='diarize'")
    focus_cols = {r["name"] for r in conn.execute("PRAGMA table_info(focus)")}
    if "measure" not in focus_cols:  # a number the focus is tracked with (6 Oct): octo/measures.py, or NULL
        conn.execute("ALTER TABLE focus ADD COLUMN measure TEXT")
    if "coach_note" not in focus_cols:  # the coach note it came from (6 Oct), if any
        conn.execute("ALTER TABLE focus ADD COLUMN coach_note TEXT REFERENCES coach_notes(id) ON DELETE SET NULL")
    fb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(feedback)")}
    if "let_go_at" not in fb_cols:  # a "What to do" step the owner let go (6 Oct): off home's list, not acted on
        conn.execute("ALTER TABLE feedback ADD COLUMN let_go_at REAL")
    run_cols = {r["name"] for r in conn.execute("PRAGMA table_info(stage_runs)")}
    if "progress" not in run_cols:  # e.g. "principle 5 of 14", written by the worker as it goes
        conn.execute("ALTER TABLE stage_runs ADD COLUMN progress TEXT")
    # Which lab this is, decided once (octo/labconfig.py; review 8 Oct, finding 8): the first lab (reviews before the
    # product, 7 Oct) keeps his Claude Code login (P5), his gate (D8) and every labelled voiceprint (D1); any
    # other gets the product's. Each setting follows from that, from one table, instead of being decided the
    # first time the code met it by "had reviews", which would make a buyer's lab the first lab for any setting added
    # later. The three rows decided on 7 Oct stay, and say which lab it was.
    from . import labconfig
    labconfig.decide(conn)
    split_cols = {r["name"] for r in conn.execute("PRAGMA table_info(splits)")}
    if "by" not in split_cols:  # who split there (9 Oct): the owner, or the lab when the people change clearly
        conn.execute("ALTER TABLE splits ADD COLUMN by TEXT NOT NULL DEFAULT 'owner'")
    if "undone_at" not in split_cols:  # joined back; the row stays so the lab doesn't split there again
        conn.execute("ALTER TABLE splits ADD COLUMN undone_at REAL")
    # B14: commitments are looked for in conversations from the 14 days before the step came, and every one since;
    # older ones are never queued (19 Oct). Set once, when this lab first meets the step.
    conn.execute("INSERT OR IGNORE INTO app_config (key, value, updated_at) VALUES ('commitments_from', ?, ?)",
                 (json.dumps(round(time.time() - 14 * 86400)), time.time()))
    people_cols = {r["name"] for r in conn.execute("PRAGMA table_info(people)")}
    if "consent" not in people_cols:  # (4.6) when and how they agreed to be recognised by voice; NULL: not recorded
        try:
            conn.execute("ALTER TABLE people ADD COLUMN consent TEXT")
        except sqlite3.OperationalError as e:  # another connection added it a moment ago
            if "duplicate column" not in str(e):
                raise
    commitment_cols = {r["name"] for r in conn.execute("PRAGMA table_info(commitments)")}
    for col, kind in (("checked", "TEXT"), ("checked_at", "REAL")):  # (B14, 21 Oct) the owner's check of the step
        if col not in commitment_cols:
            conn.execute(f"ALTER TABLE commitments ADD COLUMN {col} {kind}")
    drill_cols = {r["name"] for r in conn.execute("PRAGMA table_info(drills)")}
    for col, kind in (("point", "TEXT"), ("point_error", "TEXT"), ("point_at", "REAL")):  # (B18, 22 Oct) time to point
        if col not in drill_cols:
            conn.execute(f"ALTER TABLE drills ADD COLUMN {col} {kind}")
    prep_cols = {r["name"] for r in conn.execute("PRAGMA table_info(preparations)")}
    for col, kind in (("event_title", "TEXT"), ("event_start", "REAL"), ("event_end", "REAL")):
        # (B36's fixes, 30 Oct, 271b303) added to CREATE TABLE only: a lab made at schema 19 before them had none.
        # Found by tests/test_db_upgrade.py on 5 Nov.
        if col not in prep_cols:
            conn.execute(f"ALTER TABLE preparations ADD COLUMN {col} {kind}")
    # B35 (30 Oct): one discussion in parts, not in one stretch of audio. A join made by 18's octo/join.py becomes two
    # parts again (find_conversations re-runs without it and agrees), grouped; an undone one stays decided.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='joins'").fetchone():
        for j in conn.execute("SELECT * FROM joins WHERE session_id IS NOT NULL AND second_id IS NOT NULL").fetchall():
            if j["undone_at"] is None:
                conn.execute("UPDATE sessions SET end_s=? WHERE id=?", (j["a_s"], j["session_id"]))
                conn.execute("UPDATE sessions SET status='active' WHERE id=? AND status='superseded'", (j["second_id"],))
            conn.execute("INSERT OR IGNORE INTO parts (first_id, second_id, by, kind, created_at, undone_at) "
                         "VALUES (?, ?, ?, 'joined', ?, ?)", (j["session_id"], j["second_id"], j["by"], j["created_at"],
                                                              j["undone_at"]))
        conn.execute("DROP TABLE joins")
    # D9 (31 Oct): no one's name in what's stored. Matched by what else each can be, so the old name needn't be written here.
    conn.execute("""UPDATE app_config SET value='"first"' WHERE key='lab_origin' AND value != '"product"'""")
    conn.execute("UPDATE splits SET by='owner' WHERE by != 'lab'")
    conn.execute("UPDATE parts SET by='owner' WHERE by != 'lab'")
    conn.execute("UPDATE sessions SET note='Your voice' || substr(note, instr(note, ' voice not heard') + 6) "
                 "WHERE note LIKE '%''s voice not heard%'")
    # 23 (5 Nov): the day's calls move into model_calls, so today's count carries over; then 16's table and triggers go.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calls_gone'").fetchone():
        conn.executemany("INSERT INTO model_calls (at, reader, purpose, ok) VALUES (?, ?, ?, 1)",
                         _calls_before_22(conn, time.time() - 2 * 86400))
        for t in ("stage_runs", "talks", "rehearsals", "coach_notes", "drills", "preparations", "asks"):
            conn.execute(f"DROP TRIGGER IF EXISTS {t}_calls_gone")
        conn.execute("DROP TABLE calls_gone")


def _calls_before_22(conn: sqlite3.Connection, since: float) -> list[tuple[float, str, str]]:
    """(at, reader, purpose) for each call the rows before schema 22 recorded since `since`, as pipeline._calls_since
    counted them then: a review or commitments one, a Hindi merge one per chunk, and the rest one per row or turn."""
    def run_reader(r: dict) -> dict | None:  # runs.run_reader as it was on 5 Nov, kept here: db imports nothing above it
        p = json.loads(r["params"] or "{}")
        if r["stage"] in ("insight", "commitments"):
            return p.get("reader") or ({"kind": "claude-code"} if str(p.get("model", "")).startswith("claude") else None)
        if r["stage"] == "transcribe" and "hindi" in p:
            return p["hindi"].get("reader") or {"kind": "claude-code"}
        return None
    out: list[tuple[float, str, str]] = []
    for r in rows(conn, "SELECT stage, params, finished_at, json_extract(output, '$.usage') AS usage, "
                        "json_extract(output, '$.merged.calls') AS calls FROM stage_runs WHERE status='done' "
                        "AND finished_at >= ? AND stage IN ('insight', 'transcribe', 'commitments') AND json_valid(output)",
                  since):
        who = run_reader(r)
        n = (r["calls"] or 0) if r["stage"] == "transcribe" else int(r["usage"] not in (None, "{}", "[]", 0, ""))
        out += [(r["finished_at"], json.dumps(who), r["stage"])] * (n if who else 0)
    for sql, purpose in (
            ("SELECT point_at, json_extract(point, '$.reader') FROM drills WHERE json_valid(point) "
             "AND json_extract(point, '$.usage') IS NOT NULL AND json_extract(point, '$.reader') IS NOT NULL AND point_at >= ?", "drill"),
            ("SELECT finished_at, reader FROM asks WHERE status='done' AND usage IS NOT NULL AND finished_at >= ?", "ask"),
            ("SELECT finished_at, reader FROM preparations WHERE usage IS NOT NULL AND finished_at >= ?", "prepare"),
            ("SELECT finished_at, '{\"kind\": \"claude-code\"}' FROM coach_notes WHERE status='done' AND usage IS NOT NULL "
             "AND finished_at >= ?", "coach"),
            ("SELECT json_extract(value, '$.at'), json_extract(value, '$.reader') FROM talks, json_each(CASE WHEN "
             "json_valid(turns) THEN turns ELSE '[]' END) WHERE json_extract(value, '$.reader') IS NOT NULL "
             "AND json_extract(value, '$.at') >= ?", "talk"),
            ("SELECT json_extract(value, '$.at'), json_extract(value, '$.reader') FROM rehearsals, json_each(CASE WHEN "
             "json_valid(turns) THEN turns ELSE '[]' END) WHERE json_extract(value, '$.reader') IS NOT NULL "
             "AND json_extract(value, '$.at') >= ?", "rehearse"),
            ("SELECT review_at, json_extract(review, '$.reader') FROM rehearsals WHERE json_valid(review) "
             "AND json_extract(review, '$.reader') IS NOT NULL AND review_at >= ?", "rehearse")):
        out += [(at, who, purpose) for at, who in conn.execute(sql, (since,)).fetchall() if who]
    for g in rows(conn, "SELECT * FROM calls_gone WHERE at >= ?", since):  # rows deleted since (16's triggers)
        who = run_reader(g) if g["stage"] else load_json(g["reader"])
        out += [(g["at"], json.dumps(who), g["stage"] or "")] * (g["n"] if who else 0)
    return out


# Who a model call is for (model_calls): the database it's counted in, the step or feature, and its row. Set around a call
# by whoever makes it (`calling`); a call made without one is counted in CALLS_DB, the lab's own.
_CALLING: ContextVar[tuple[str | None, str, str | None]] = ContextVar("calling", default=(None, "", None))
CALLS_DB: Path | None = DB_PATH
CALLS_KEPT_S = 30 * 86400


@contextmanager
def calling(conn: sqlite3.Connection, purpose: str, ref: str | None = None) -> Iterator[None]:
    token = _CALLING.set((conn.execute("PRAGMA database_list").fetchone()[2], purpose, ref))
    try:
        yield
    finally:
        _CALLING.reset(token)


def model_call(reader: dict, ok: bool) -> None:
    """One call to a model made, or failed after it may have been billed: a row in model_calls (risk 7, 5 Nov)."""
    path, purpose, ref = _CALLING.get()
    path = path or (str(CALLS_DB) if CALLS_DB and CALLS_DB.exists() else None)
    if not path:
        return
    who = {k: reader[k] for k in ("kind", "model", "connection") if reader.get(k)}
    now = time.time()
    with closing(sqlite3.connect(path, timeout=30, isolation_level=None)) as c:
        c.execute("INSERT INTO model_calls (at, reader, purpose, ref, ok) VALUES (?, ?, ?, ?, ?)",
                  (now, json.dumps(who), purpose, ref, int(ok)))
        c.execute("DELETE FROM model_calls WHERE at < ?", (now - CALLS_KEPT_S,))


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def config(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    """One of the lab's own settings (app_config), as stored JSON, or `default`."""
    r = conn.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def row(conn: sqlite3.Connection, sql: str, *args: Any) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def rows(conn: sqlite3.Connection, sql: str, *args: Any) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def load_json(value: str | None) -> Any:
    return json.loads(value) if value else None
