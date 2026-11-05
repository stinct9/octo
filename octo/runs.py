"""The pipeline's runs as the pages and the planner both read them: a conversation's current run, the model a run calls,
whether a step is held and why (a failure's wait, Claude's usage limit and the day's calls, a signed-out Claude Code, a
refused key, a Mac short of memory), and whose speakers are settled and which review is pinned.

Moved out of octo/pipeline.py and octo/planning.py (architecture review 5 Nov, risk 8): `sessions`, the read model for
the list and the page, asked these of the planner, and the planner's modules reached `sessions` back through `parts`,
so the three were one import cycle. Here they sit below both; pipeline and planning re-export them by their old names.
Nothing here imports pipeline, planning or sessions (tests/test_layers.py).
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import frame as FR
from .corrections import apply, for_run
from .db import config, row, rows


def current(conn, target: str, stage: str, output: bool = True) -> dict | None:
    """The current run. output=False leaves its output out (has_output says if there is one): a transcript is ~120 KB,
    and the list read every one of them each time it was asked for, ~22 MB on the first lab (4 Nov)."""
    if output:
        return row(conn, "SELECT * FROM stage_runs WHERE target=? AND stage=? AND is_current=1", target, stage)
    if (every := FR.current(conn, _NO_OUTPUT)) is not None:  # the list's request: every current run, read once
        got = every.get((target, stage))
        return dict(got) if got else None
    return row(conn, f"SELECT {_NO_OUTPUT}, output IS NOT NULL AS has_output FROM stage_runs WHERE target=? AND stage=? "
                     "AND is_current=1", target, stage)


_NO_OUTPUT = "id, stage, target, input_hash, status, is_current, stale, upstream, params, error, queued_at, started_at, finished_at, progress"


# A failed run is tried again on a later pass, after a wait that doubles each time, and given up
# after MAX_ATTEMPTS (until its inputs change, or `octo retry` / Try again in the UI). Before 2 Oct a
# failure stopped the whole queue until the next hourly pass, and then failed again forever: four
# Ollama timeouts one night held up every other conversation.
MAX_ATTEMPTS = 3
RETRY_AFTER_S = 15 * 60

# Claude Code's answer when the owner's Pro plan is used up for now (4 Oct: "You've hit your session limit ·
# resets 10:40am (Asia/Calcutta)"). That says nothing about the run, so it isn't counted as an
# attempt: the run waits until the limit resets, and so does every other step that needs Claude.
# On 4 Oct three reviews and a Hindi transcript used up all three attempts in two hours, hours
# before the reset, and then waited for Try again.
# The weekly one (31 Oct: "You've hit your weekly limit · resets Nov 3 at 2:30am (Asia/Calcutta)") wasn't recognised
# until 4 Nov: its six failures were counted and two reviews waited for Try again.
LIMIT_WORDS = ("hit your session limit", "hit your usage limit", "hit your weekly limit", "hit your limit",
               "usage limit reached")
_RESETS = re.compile(r"resets\s+(?:([a-z]{3})[a-z]*\.?\s+(\d{1,2})\s+at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?"
                     r"(?:\s*\(([^)]+)\))?", re.I)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def limit_reset(error: str | None, failed_at: float | None) -> float | None:
    """When a usage-limit failure clears: the time in its message (the next one after it failed),
    or an hour after it failed if there's none. None if the error isn't Claude's usage limit."""
    if not error or not any(w in error.lower() for w in LIMIT_WORDS):
        return None
    failed_at = failed_at or time.time()
    m = _RESETS.search(error)
    hour, minute = (int(m[3]), int(m[4] or 0)) if m else (99, 0)
    if m and (m[5] or "").lower() == "pm" and hour < 12:
        hour += 12
    elif m and (m[5] or "").lower() == "am" and hour == 12:
        hour = 0
    month = _MONTHS.index(m[1].lower()) + 1 if m and m[1] and m[1].lower() in _MONTHS else None
    if hour > 23 or minute > 59 or (m and m[1] and not month):
        return failed_at + 3600
    try:
        zone = ZoneInfo(m[6].strip()) if m[6] else None
    except (ValueError, KeyError):  # not a zone name we know: the Mac's own time
        zone = None
    at = datetime.fromtimestamp(failed_at, zone)
    try:
        reset = at.replace(**({"month": month, "day": int(m[2])} if month else {}), hour=hour, minute=minute, second=0,
                           microsecond=0)
    except ValueError:  # no such day
        return failed_at + 3600
    if reset <= at:
        reset = reset.replace(year=reset.year + 1) if month else reset + timedelta(days=1)
    return reset.timestamp() + 60


# Claude Code signed out on this Mac (3 Nov: its login expired and for two days every review failed three times,
# then said "press Try again", which couldn't fix it). Like the usage limit it says nothing about the run: it isn't
# counted, and every step that needs Claude waits until `claude auth status` says it's signed in again, looked at
# once every SIGN_IN_CHECK_S; then they carry on by themselves.
SIGNED_OUT_WORDS = ("claude auth login", "log in with /login")
SIGN_IN_CHECK_S = 300
SIGN_IN = "Claude Code is signed out on this Mac: run claude auth login in Terminal, and reviews carry on by themselves"
_signed_in: tuple[float, bool] = (0.0, False)


def signed_out(error: str | None) -> bool:
    return bool(error) and any(w in error for w in SIGNED_OUT_WORDS)


def signed_in(now: float | None = None) -> bool:
    """Whether Claude Code is signed in on this Mac now (`claude auth status`), asked at most once a minute."""
    global _signed_in
    now = now if now is not None else time.time()
    if now - _signed_in[0] > 60:
        from . import claude_code as CC
        _signed_in = (now, CC.status()["logged_in"])
    return _signed_in[1]


def claude_signed_out(conn, now: float | None = None) -> float | None:
    """When the newest step that called Claude failed because Claude Code was signed out, while it still is; else None."""
    for r in rows(conn, "SELECT stage, params, status, error, finished_at FROM stage_runs WHERE status IN ('done','failed') "
                        "AND stage IN ('insight','commitments','transcribe') ORDER BY finished_at DESC LIMIT 100"):
        if uses_claude(r):
            return r["finished_at"] if r["status"] == "failed" and signed_out(r["error"]) and not signed_in(now) else None
    return None


# A key refused or out of credit (providers.py; architecture review 5 Nov): one hold on that connection's steps, as when
# Claude Code is signed out, not every queued step failing three times and waiting for Try again. It lasts until the
# owner changes Models and keys (providers.json written after the failure); the step is looked at again every 5 min.
KEY_REFUSED_WORDS = ("didn't accept the API key", "didn't accept the key", "out of credit")
KEY_FIX = "fix it under Settings → Models and keys, and the steps waiting carry on by themselves"


def key_refused(error: str | None) -> bool:
    return bool(error) and any(w in error for w in KEY_REFUSED_WORDS)


def connection_refused(conn, connection_id: str) -> float | None:
    """When the newest step on this connection failed because its key was refused, while Models and keys haven't changed
    since; else None."""
    for r in rows(conn, "SELECT stage, params, status, error, finished_at FROM stage_runs WHERE status IN ('done','failed') "
                        "AND stage IN ('insight','commitments','transcribe') ORDER BY finished_at DESC LIMIT 100"):
        if (run_reader(r) or {}).get("connection") == connection_id:
            if r["status"] != "failed" or not key_refused(r["error"]):
                return None
            from . import providers as PV
            try:
                changed = PV.CONFIG.stat().st_mtime
            except OSError:
                changed = 0.0
            return r["finished_at"] if changed <= (r["finished_at"] or 0) else None
    return None


# A provider's rate limit, outage or network failure (octo/providers.py TRANSIENT; review 8 Oct, finding 5) says
# nothing about the run either: it waits 15 min, doubling to 4 h, or what the service asked for if longer, and
# isn't counted. Before, an hour offline used up every queued review's three attempts.
TRANSIENT_WORDS = ("it will be tried again later",)
TRANSIENT_MAX_S = 4 * 3600
_ASKED = re.compile(r"it asked for (\d+) s")


def transient(error: str | None) -> bool:
    return bool(error) and any(w in error for w in TRANSIENT_WORDS)


def transient_retry(fails: list[dict]) -> float | None:
    """When a run whose latest failure was transient goes again (newest first); None if it wasn't transient."""
    if not fails or not transient(fails[0]["error"]):
        return None
    n = next((i for i, f in enumerate(fails) if not transient(f["error"])), len(fails))  # in a row
    wait = min(RETRY_AFTER_S * 2 ** (n - 1), TRANSIENT_MAX_S)
    if m := _ASKED.search(fails[0]["error"]):
        wait = max(wait, int(m[1]))
    return (fails[0]["finished_at"] or 0) + wait


def due_at(conn, stage: str, target: str, input_hash: str) -> float:
    """When a run with these inputs may run (again): 0 if it never failed, inf once given up."""
    fails = rows(conn, "SELECT error, finished_at FROM stage_runs WHERE target=? AND stage=? AND input_hash=? "
                       "AND status='failed' ORDER BY finished_at DESC", target, stage, input_hash)
    if not fails:
        return 0.0
    reset = limit_reset(fails[0]["error"], fails[0]["finished_at"])
    if reset is not None:
        return reset
    if signed_out(fails[0]["error"]) or key_refused(fails[0]["error"]):  # held while still signed out, or the key refused
        return (fails[0]["finished_at"] or 0) + SIGN_IN_CHECK_S
    if (again := transient_retry(fails)) is not None:
        return again
    counted = [f for f in fails if limit_reset(f["error"], f["finished_at"]) is None and not transient(f["error"])
               and not signed_out(f["error"]) and not key_refused(f["error"])]
    if len(counted) >= MAX_ATTEMPTS:
        return math.inf
    return (counted[0]["finished_at"] or 0) + RETRY_AFTER_S * 2 ** (len(counted) - 1)


def held_back(conn, stage: str, target: str, input_hash: str, now: float | None = None) -> bool:
    """True while a run with these inputs is waiting to be retried, or has been given up on."""
    return due_at(conn, stage, target, input_hash) > (now if now is not None else time.time())


# Heavy steps (the speech models: ~10 GB on a 16 GB Mac, learnings 5 Nov) wait while macOS says memory is critical, or the
# disk is nearly full, and look again after HEAVY_HOLD_S (architecture review 5 Nov, risk 6). The hold is kept in
# app_config so the pages, in the server's process, can say why.
HEAVY_STAGES = ("transcribe", "speakers")
HEAVY_HOLD_S = 5 * 60


def heavy_held(conn, now: float | None = None) -> dict | None:
    """The hold on heavy steps, while it lasts: {"until", "why"}."""
    held = config(conn, "heavy_hold")
    return held if held and held["until"] > (now if now is not None else time.time()) else None


# Automatic Claude calls a day (review 4 Oct, M09): a review is one call, commitments one (B14), a Hindi merge one per chunk
# (3599a4cb took 37 on 4 Oct). The owner's Pro limit is shared with their own Claude use; past this, Claude
# steps wait for midnight and everything else carries on.
DAILY_CLAUDE_CALLS = 60


def claude_calls_since(conn, since: float) -> int:
    """Calls to Claude Code since `since`, whatever made them (model_calls: reviews, Hindi merges, commitments, the coach,
    practice takes, Ask, rehearsals, Talk it through, plans), billed failures included."""
    return _calls_since(conn, since, kind="claude-code")


def _calls_since(conn, since: float, kind: str | None = None, connection: str | None = None) -> int:
    """Model calls since `since` (one row each in model_calls, written by claude_code.ask and providers.ask; architecture
    review 5 Nov, risk 7), to Claude Code or to one connection."""
    return row(conn, "SELECT count(*) n FROM model_calls WHERE at >= ? AND (? IS NULL OR json_extract(reader, '$.kind') = ?) "
                     "AND (? IS NULL OR json_extract(reader, '$.connection') = ?)", since, kind, kind,
               connection, connection)["n"]


def _midnight(now: float, days: int = 0) -> float:
    d = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return (d + timedelta(days=days)).timestamp()


def claude_blocked_until(conn, now: float | None = None) -> float | None:
    """While Claude's usage limit holds (a run failed on it and it hasn't reset), when it resets;
    or, once today's DAILY_CLAUDE_CALLS are used, midnight."""
    now = now if now is not None else time.time()
    resets = [r for f in rows(conn, "SELECT error, finished_at FROM stage_runs WHERE status='failed' AND finished_at > ? "
                                    "UNION ALL SELECT error, finished_at FROM coach_notes WHERE status='failed' AND finished_at > ? "
                                    "UNION ALL SELECT point_error, point_at FROM drills WHERE point_error IS NOT NULL AND point_at > ? "
                                    "UNION ALL SELECT error, finished_at FROM asks WHERE status='failed' AND finished_at > ? "
                                    "UNION ALL SELECT error, error_at FROM rehearsals WHERE error IS NOT NULL AND error_at > ? "
                                    "UNION ALL SELECT error, error_at FROM talks WHERE error IS NOT NULL AND error_at > ? "
                                    "UNION ALL SELECT error, error_at FROM preparations WHERE error IS NOT NULL AND error_at > ?",
                              now - 86400, now - 86400, now - 86400, now - 86400, now - 86400, now - 86400, now - 86400)
              if (r := limit_reset(f["error"], f["finished_at"])) is not None and r > now]
    if claude_calls_since(conn, _midnight(now)) >= DAILY_CLAUDE_CALLS:
        resets.append(_midnight(now, 1))
    if claude_signed_out(conn, now) is not None:
        resets.append(now + SIGN_IN_CHECK_S)
    return max(resets) if resets else None


def claude_waits(conn, now: float | None = None) -> str | None:
    """Why something that needs Claude can't go now, in the owner's words, or None."""
    now = now if now is not None else time.time()
    if claude_signed_out(conn, now) is not None:
        return SIGN_IN
    if (blocked := claude_blocked_until(conn, now)) is None:
        return None
    if claude_calls_since(conn, _midnight(now)) >= DAILY_CLAUDE_CALLS:
        return f"today's {DAILY_CLAUDE_CALLS} Claude calls are used: after midnight"
    return f"Claude's usage limit: after {datetime.fromtimestamp(blocked):%H:%M}"


def run_reader(run: dict) -> dict | None:
    """The model a run calls: its reader ({"kind": "claude-code" | "anthropic" | "openai", "connection": …}), or None
    for a step that runs on the Mac (a local model's review too)."""
    p = json.loads(run["params"] or "{}")
    if run["stage"] == "insight":
        if p.get("reader"):
            return p["reader"]
        return {"kind": "claude-code"} if str(p.get("model", "")).startswith("claude") else None
    if run["stage"] == "commitments":  # B14: on the model its choice names, Claude Code by default (octo/commitments.py)
        return p.get("reader") or ({"kind": "claude-code"} if str(p.get("model", "")).startswith("claude") else None)
    if run["stage"] == "transcribe" and "hindi" in p:
        return p["hindi"].get("reader") or {"kind": "claude-code"}  # the first lab merges with Claude Code
    return None


def uses_claude(run: dict) -> bool:
    """A step through Claude Code, on the owner's Claude plan (any lab that chose it): its usage limit and
    DAILY_CLAUDE_CALLS apply to these only. Before 8 Oct (review, finding 15) they applied to any model, so a buyer's own key, or a server on their Mac,
    stopped at 60 calls a day with a message about Claude."""
    who = run_reader(run)
    return bool(who) and who["kind"] == "claude-code"


def budget_blocked_until(conn, connection_id: str, now: float | None = None) -> float | None:
    """A connection's own budget (its "daily_calls", octo/providers.py), once today's are used: midnight."""
    from . import providers as PV
    budget = (PV.connection(connection_id) or {}).get("daily_calls")
    if not budget:
        return None
    now = now if now is not None else time.time()
    used = _calls_since(conn, _midnight(now), connection=connection_id)
    return _midnight(now, 1) if used >= budget else None


FORGOTTEN_REVIEWS = "forgotten_reviews"  # app_config: {conversation: the review run held since someone in it was forgotten}


def review_held(conn, session_id: str, run_id: str | None) -> bool:
    """This review read someone since forgotten (L9): kept as it is until the owner asks for a redo."""
    return bool(run_id) and config(conn, FORGOTTEN_REVIEWS, {}).get(session_id) == run_id


def pinned_review(conn, session_id: str) -> dict | None:
    """The current review, once the owner has answered it, rated any of it or sent one of its steps to
    TickTick. It stays the one shown, and nothing re-runs it, until they ask for a redo
    (sessions.redo_review). Before 4 Oct a speaker fix, a rename or a newly confirmed voice replaced
    a rated review with a fresh one, and the page went back to "To rate" (review 4 Oct, M07)."""
    ins = row(conn, "SELECT id, stale, input_hash FROM stage_runs WHERE target=? AND stage='insight' "
                    "AND is_current=1 AND status='done'", session_id)
    if not ins or row(conn, "SELECT 1 FROM sessions WHERE id=? AND redo_review=?", session_id, ins["id"]):
        return None
    if review_held(conn, session_id, ins["id"]):  # it read someone since forgotten (L9): kept until a redo is asked
        return ins
    if row(conn, "SELECT 1 FROM feedback WHERE insight_run=? LIMIT 1", ins["id"]) or \
            row(conn, "SELECT 1 FROM review_answers WHERE insight_run=? LIMIT 1", ins["id"]) or \
            row(conn, "SELECT 1 FROM ticktick_tasks WHERE insight_run=? LIMIT 1", ins["id"]):
        return ins
    return None


def corrections_now(conn, session_id: str) -> tuple[str | None, list[dict]]:
    dz = row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", session_id)
    return (dz["id"], for_run(conn, session_id, dz["id"])) if dz else (None, [])


def settled_speakers(conn, session_id: str, need_self: bool = True, with_person: bool = False) -> list[dict] | None:
    """Who each speaker is, once that's settled for the current diarize run: the owner confirmed, and no
    suggestion waiting for an answer. Unnamed voices may stay "Speaker C". None if not settled. A part of one
    discussion (B35) needn't have the owner in it, and says who each named speaker is (`person`)."""
    dz = current(conn, session_id, "diarize", output=False)
    if not dz or not dz["has_output"]:
        return None
    letters = _letters(conn, session_id, dz["id"], for_run(conn, session_id, dz["id"]))
    every = FR.session_speakers(conn)  # the list's request: every conversation's, read once
    got = {r["speaker"]: {**r, "person": r["person_id"]} for r in every.get(session_id, []) if r["diarize_run"] == dz["id"]} \
        if every is not None else \
        {r["speaker"]: r for r in rows(conn, "SELECT ss.speaker, ss.state, p.id AS person, p.name, p.is_self FROM session_speakers ss "
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


def _letters(conn, session_id: str, dz_id: str, fixes: list[dict]) -> list[str]:
    """The speakers of a diarize run as corrected. A done run's output is written once, so only the corrections can change
    them: the list asked this of every conversation, reading and correcting each ~40 KB output every time (4 Nov)."""
    key, made = (FR.lab(conn), session_id), (dz_id, json.dumps(fixes, sort_keys=True, default=str))
    hit = _LETTERS.get(key)
    if not hit or hit[0] != made:
        out = json.loads(row(conn, "SELECT output FROM stage_runs WHERE id=?", dz_id)["output"])
        hit = _LETTERS[key] = (made, apply(out, fixes)["speakers"])
    return list(hit[1])


# (lab, conversation) → ((its diarize run, its corrections), its letters): one per conversation, so a superseded run is
# dropped when its successor is read (review 5 Nov)
_LETTERS: dict[tuple[str, str], tuple[tuple[str, str], list[str]]] = {}


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
