"""A conversation as the owner sees it: who's who, what's left to do, the summary row and the full page.

Shared by the web API (octo/api.py) and the CLI (`octo show`), so both apply the owner's corrections and
the English-only rule the same way.
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime
from pathlib import Path

from . import aside as AS
from . import commitments as CM
from . import corrections as C
from . import delivery as D
from . import events as EV
from . import fillers as FL
from . import focus as F
from . import intention as IN
from . import marks as MK
from . import models as MD
from . import parts as PT
from . import runs as RN
from . import questions as QS
from . import review as R
from . import split as S
from . import ticktick as TT
from .asr import language_paused, language_unsure, other_language
from . import frame as FR
from .db import load_json, row, rows
from .labconfig import flag
from .runs import MERGE_MIN_JAY_S, current, jay_talk_s, pinned_review, review_held, settled_speakers
from .privacy import RAW_AUDIO_DAYS
from .stages import STAGES, dropped

# The steps a conversation waits on. Not commitments (B14): it runs after the review, needs nothing of the owner,
# and a conversation reads "to rate" while it runs; its block on the page says how it stands.
SESSION_STAGES = [name for name, s in STAGES.items() if s.level == "session" and name != "commitments"]


def diarized(conn, session_id: str) -> tuple[dict | None, dict | None, list[dict]]:
    """(current diarize run, its output with the owner's corrections applied, the corrections)."""
    dz = current(conn, session_id, "diarize")
    if not dz or dz["status"] != "done":
        return None, None, []
    fixes = C.for_run(conn, session_id, dz["id"])
    return dz, C.apply(load_json(dz["output"]), fixes), fixes


def speakers(conn, session_id: str) -> list[dict]:
    """Every speaker in the current diarize run, with who they are and how sure that is."""
    dz = current(conn, session_id, "diarize", output=False)
    if not dz or dz["status"] != "done":
        return []
    out = _talk(conn, session_id, dz["id"])
    matches = {s["speaker"]: s for s in (_identified(conn, session_id) or {}).get("speakers") or []}
    every = FR.session_speakers(conn)  # the list's request: every conversation's, read once
    known = {r["speaker"]: r for r in (every.get(session_id, []) if every is not None else rows(
        conn, "SELECT ss.speaker, ss.state, ss.diarize_run, p.id AS person_id, p.name, p.is_self FROM session_speakers ss "
              "LEFT JOIN people p ON p.id = ss.person_id WHERE ss.session_id=?", session_id))}
    total = sum(out["talk_s"].values()) or 1.0
    result = []
    for letter in out["speakers"]:
        k = known.get(letter) if known.get(letter, {}).get("diarize_run") == dz["id"] else None
        state = k["state"] if k and k["name"] else "unknown"
        m = (matches.get(letter) or {}).get("match") or {}
        result.append({
            "letter": letter,
            "name": k["name"] if k and k["name"] else None,
            "person_id": k["person_id"] if k and k["name"] else None,  # their page (octo/person.py), once named
            "state": state,  # confirmed | suggested | ambiguous | unknown
            "self": bool(k and k["is_self"] and state in ("confirmed", "auto")),
            "alternatives": m.get("alternatives", []) if state == "ambiguous" else [],
            "talk_s": out["talk_s"].get(letter, 0.0),
            "share": round(out["talk_s"].get(letter, 0.0) / total, 3),
        })
    return result


def _talk(conn, session_id: str, dz_id: str) -> dict:
    """Its speakers and talk time with the corrections, read from the diarize output only when the run or the corrections
    change (a run's output is written once)."""
    fixes = C.for_run(conn, session_id, dz_id)
    key = (FR.lab(conn), session_id)
    made = (dz_id, json.dumps(fixes, sort_keys=True, default=str))
    hit = _TALK.get(key)
    if not hit or hit[0] != made:
        full = C.apply(load_json(row(conn, "SELECT output FROM stage_runs WHERE id=?", dz_id)["output"]), fixes)
        hit = _TALK[key] = (made, {"speakers": full["speakers"], "talk_s": full["talk_s"]})
    return hit[1]


# conversation → (its diarize run and corrections, its speakers and talk time) (28 Oct): one per conversation, so a run
# superseded is dropped when its successor is read (review 5 Nov)
_TALK: dict[tuple[str, str], tuple[tuple[str, str], dict]] = {}


def _identified(conn, session_id: str) -> dict | None:
    """The current identify run's matches and whether the owner was heard ({upstream, speakers, jay_present}), read with
    json_extract: not its whole output (architecture review 5 Nov, risk 5). None until it's done."""
    ident = current(conn, session_id, "identify", output=False)
    if not ident or ident["status"] != "done":
        return None
    if (f := FR.get(conn)) is not None:  # the list asks twice a row: once a request
        key = ("identified", ident["id"])
        if key not in f:
            f[key] = _read_identified(conn, ident)
        return f[key]
    return _read_identified(conn, ident)


def _read_identified(conn, ident: dict) -> dict:
    got = row(conn, "SELECT json_extract(output, '$.speakers') sp, json_extract(output, '$.jay_present') jp FROM stage_runs "
                    "WHERE id=?", ident["id"])
    return {"upstream": ident["upstream"], "speakers": load_json(got["sp"]) or [],
            "jay_present": None if got["jp"] is None else bool(got["jp"])}


def display(sp: dict) -> str:
    if sp["name"] and sp["state"] in ("confirmed", "auto"):
        return sp["name"]
    if sp["name"] and sp["state"] == "ambiguous":
        return " or ".join([sp["name"], *sp["alternatives"]]) + "?"
    if sp["name"]:
        return f"{sp['name']}?"
    return f"Speaker {sp['letter']}"


def paused(tr: dict | None, conn=None) -> str | None:
    """Why this conversation's transcript isn't used (a language that's paused), or None. With the connection, SQLite
    reads the three fields it needs: the list asked this of every row, and parsing every whole transcript (word times
    and all) was half its time (architecture review, 28 Oct)."""
    if not (tr and tr["status"] == "done" and tr.get("output", tr.get("has_output"))):
        return None
    if conn is None or "id" not in tr.keys():
        return language_paused(load_json(tr["output"]))
    key = (_lab(conn), tr["target"]) if "target" in tr.keys() else (_lab(conn), tr["id"])
    if key in _PAUSED and _PAUSED[key][0] == tr["id"]:  # a done run's output is written once (worker.py)
        return _PAUSED[key][1]
    got = row(conn, "SELECT json_extract(output, '$.language', '$.language_votes', '$.merged') f FROM stage_runs WHERE id=?",
              tr["id"])
    language, votes, merged = json.loads(got["f"]) if got else (None, None, None)
    why = language_paused({"language": language, "language_votes": votes, "merged": merged})
    _PAUSED[key] = (tr["id"], why)
    return why


def _lab(conn) -> str:
    """Which database: another lab (a test's, the demo's) has its own runs under the same ids."""
    return FR.lab(conn)


# conversation → (its transcribe run, paused): json_extract still parses the whole transcript (28 Oct); one per conversation,
# so a superseded run is dropped when its successor is read (review 5 Nov)
_PAUSED: dict[tuple[str, str], tuple[str, str | None]] = {}


def todo(conn, session_id: str) -> dict:
    """The one thing left to do for this conversation (the list shows it on each row). One discussion in parts (B35):
    the head's says what the whole waits on (a later part's speakers, say, with `part`); a later part reads "part"
    once the whole review is shown on the head."""
    g = PT.group(conn, session_id)
    if not g:
        return _todo(conn, session_id)
    own = lambda t: t["kind"] in ("failed", "confirm", "paused") or (t["kind"] == "processing" and t["stage"] != "insight")  # noqa: E731
    if g[0]["id"] != session_id:  # what it waits on itself, else its review is the whole's
        t = _todo(conn, session_id)
        return t if own(t) or shown_review(conn, session_id) else {"kind": "part", "head": g[0]["id"]}
    for n, p in enumerate(g[1:], 2):
        t = _todo(conn, p["id"])
        if own(t):
            return {**t, "part": n, "part_id": p["id"]}
    if PT.held(conn, session_id):  # parts answered before they were grouped: as far as the furthest of them got
        got = [_todo(conn, p["id"])["kind"] for p in g]
        mine = _todo(conn, session_id)
        return {"kind": "reviewed"} if "reviewed" in got else mine if mine["kind"] != "processing" else \
            {"kind": "review"} if "review" in got else {"kind": "short", "reason": "its review waits for a redo of the whole"}
    return _todo(conn, session_id, g)


def _todo(conn, session_id: str, g: list[dict] | None = None) -> dict:
    asides = FR.set_aside(conn)  # the list's request: read once for every row
    aside = ({"aside": asides[session_id]} if session_id in asides else None) if asides is not None else \
        row(conn, "SELECT aside FROM sessions WHERE id=? AND status='set_aside'", session_id)
    if aside:  # not a conversation, as the owner marked it (B5): nothing left to do
        return {"kind": "aside", "what": aside["aside"]}
    runs = {name: current(conn, session_id, name, output=False) for name in SESSION_STAGES}
    waits = FR.pending(conn)
    pending = [name for name in SESSION_STAGES if ((session_id, name) in waits if waits is not None else row(
        conn, "SELECT id FROM stage_runs WHERE target=? AND stage=? AND status IN ('queued','running')", session_id, name))]
    if pending:
        step = SESSION_STAGES.index(pending[0]) + 1
        live = row(conn, "SELECT progress FROM stage_runs WHERE target=? AND stage=? AND status='running'", session_id, pending[0])
        detail = live["progress"] if live else None
        waiting = row(conn, "SELECT * FROM stage_runs WHERE target=? AND stage=? AND status='queued'", session_id, pending[0])
        blocked = RN.claude_blocked_until(conn)
        if not live and waiting and blocked and RN.uses_claude(waiting):
            capped = RN.claude_calls_since(conn, RN._midnight(time.time())) >= RN.DAILY_CLAUDE_CALLS
            detail = (RN.SIGN_IN if RN.claude_signed_out(conn) is not None else
                      f"today's {RN.DAILY_CLAUDE_CALLS} Claude calls are used · resumes after midnight" if capped else
                      f"waiting for Claude's usage limit to reset (about {_clock(blocked - 60)})")
        elif not live and waiting and waiting["stage"] in RN.HEAVY_STAGES and (held := RN.heavy_held(conn)):
            detail = f"waiting: {held['why']} · tries again after {_clock(held['until'])}"  # review 5 Nov, risk 6
        elif not live and waiting and (models := MD.missing_for(conn, waiting["stage"])):
            detail = "waiting for the speech models: Set up Octo shows how far they've come" if models else None
        elif not live and waiting and (c := (RN.run_reader(waiting) or {}).get("connection")) \
                and RN.connection_refused(conn, c):
            detail = f"waiting: the key for this model was refused · {RN.KEY_FIX}"
        elif not live and waiting and (c := (RN.run_reader(waiting) or {}).get("connection")) \
                and RN.budget_blocked_until(conn, c):
            from . import providers as PV
            budget = (PV.connection(c) or {})
            detail = f"today's {budget.get('daily_calls')} calls on {budget.get('label') or c} are used · resumes after midnight"
        # When it'll be ready for the owner (B3): through its review, or to the point where it needs them.
        from . import eta as E
        when = E.cached(conn)["sessions"].get(session_id) or {}
        return {"kind": "processing", "stage": pending[0], "step": step, "of": len(SESSION_STAGES),
                "detail": detail, "queued": live is None, "eta": when.get("at"), "late": bool(when.get("late")),
                "then": when.get("then")}
    # A failed run is never "current" (run_pending un-currents it), so look at the latest attempt.
    failed = FR.failed_last(conn)
    for name in SESSION_STAGES:
        last = failed.get((session_id, name)) if failed is not None else \
            row(conn, "SELECT status, error, input_hash, finished_at FROM stage_runs WHERE target=? AND stage=? "
                      "ORDER BY queued_at DESC LIMIT 1", session_id, name)
        if last and last["status"] == "failed":
            err = last["error"] or ""
            reset = RN.limit_reset(err, last["finished_at"])
            hint = ("Ollama isn't running: start it, or install the login item (octo login-item install)"
                    if name == "insight" and ("Connection refused" in err or "urlopen error" in err) else
                    RN.SIGN_IN if RN.signed_out(err) else
                    f"The key was refused: {RN.KEY_FIX}" if RN.key_refused(err) else
                    None if reset is None else
                    f"Claude's usage limit was reached · tries again after {_clock(reset - 60)}" if reset > time.time() else
                    "Claude's usage limit has reset · trying again shortly")
            due = RN.due_at(conn, name, session_id, last["input_hash"])
            return {"kind": "failed", "stage": name, "error": err, "hint": hint, "auto_retry": due != math.inf}
    why = paused(runs.get("transcribe"), conn)
    if why:
        # Hindi waits for the owner to confirm themselves on the draft before it's merged (J2): ask them.
        tr_out = load_json(current(conn, session_id, "transcribe")["output"])
        if other_language(tr_out) == "hi" and runs.get("diarize") and runs["diarize"]["status"] == "done" \
                and jay_talk_s(conn, session_id) < MERGE_MIN_JAY_S:
            if without_jay(conn, session_id):
                return {"kind": "not_you"}
            if settled_speakers(conn, session_id) is None:
                return {"kind": "confirm", "hindi": True}
        return {"kind": "paused", "reason": why}
    if not runs.get("diarize") or runs["diarize"]["status"] != "done":
        return {"kind": "processing", "stage": "diarize", "step": SESSION_STAGES.index("diarize") + 1, "of": len(SESSION_STAGES)}
    if g and settled_speakers(conn, session_id, need_self=False) is None:
        return {"kind": "confirm"}
    if (not PT.letters(conn, g)[0]) if g else settled_speakers(conn, session_id) is None:  # g: the owner in none of it
        return {"kind": "not_you"} if g or without_jay(conn, session_id) else {"kind": "confirm"}
    ins = runs.get("insight")
    if not ins or ins["status"] != "done" or (ins["stale"] and not pinned_review(conn, session_id)):
        # Not queued yet: it will be on the runner's next pass.
        return {"kind": "processing", "stage": "insight", "step": len(SESSION_STAGES), "of": len(SESSION_STAGES),
                "queued": True}
    out = _review_shape(conn, ins["id"])
    if out["skipped"]:
        return {"kind": "short", "reason": skip_text(out["skipped"])}
    if not any(it["kind"] != "question" for it in R.items(out)):
        return {"kind": "short", "reason": "Nothing in this review to answer"}
    # Reviewed: the one question answered on the review shown (since 6 Oct), or every item rated
    # before then. A redone review asks again, though Progress keeps its count (sessions.reviewed_at).
    if R.is_answered(conn, ins["id"], out) and \
            row(conn, "SELECT 1 FROM sessions WHERE id=? AND reviewed_at IS NOT NULL", session_id):
        return {"kind": "reviewed"}
    return {"kind": "review"}


# What the list reads of a review: whether it was skipped and what there is to answer (R.items); not its memo or text.
_SHAPE = ("skipped", "kind", "one_thing", "reading", "between", "conduct", "next", "beneath")


def _review_shape(conn, run_id: str) -> dict:
    fields = f"json_extract(output, {', '.join(repr('$.' + k) for k in _SHAPE)})"
    every = FR.once(conn, "review_shapes", lambda: {r["id"]: r["f"] for r in rows(
        conn, f"SELECT id, {fields} f FROM stage_runs WHERE stage='insight' AND is_current=1 AND status='done'")})
    got = every.get(run_id) if every is not None and run_id in every else \
        row(conn, f"SELECT {fields} f FROM stage_runs WHERE id=?", run_id)["f"]
    return {k: v for k, v in zip(_SHAPE, json.loads(got)) if v is not None or k == "skipped"}


def without_jay(conn, session_id: str) -> bool:
    """Nothing left to answer, and the owner isn't in it: no suggestion waiting, no speaker confirmed as
    them, and either every speaker confirmed as someone else or no voice that sounds like them. Such
    a conversation keeps its transcript and numbers but gets no review, so it isn't asking the owner for
    anything (review 4 Oct, M18). Read at display time: the planner and the review don't change,
    and it isn't the D10 filter (nothing is dropped). Naming a speaker as them undoes it."""
    dz = current(conn, session_id, "diarize", output=False)
    if not dz or dz["status"] != "done":
        return False
    out = _talk(conn, session_id, dz["id"])
    every = FR.session_speakers(conn)
    got = {r["speaker"]: r for r in every.get(session_id, []) if r["diarize_run"] == dz["id"]} if every is not None else \
        {r["speaker"]: r for r in rows(conn, "SELECT ss.speaker, ss.state, p.is_self FROM session_speakers ss "
                                             "LEFT JOIN people p ON p.id = ss.person_id "
                                             "WHERE ss.session_id=? AND ss.diarize_run=?", session_id, dz["id"])}
    mine = [got.get(k) or {} for k in out["speakers"]]
    if any(g.get("state") in ("suggested", "ambiguous") or (g.get("state") in ("confirmed", "auto") and g.get("is_self")) for g in mine):
        return False
    if mine and all(g.get("state") in ("confirmed", "auto") for g in mine):  # auto: named without asking (4.4)
        return True
    ident = _identified(conn, session_id)
    return bool(ident and ident["upstream"] and dz["id"] in load_json(ident["upstream"]) and ident["jay_present"] is False)


def skip_text(text: str) -> str:
    """Rounding made "you spoke for 20 s (insights need 20 s)" out of 19.6 s (review 4 Oct, M33).
    Reworded here, at display time, so the reviews already stored read the same."""
    m = re.fullmatch(r"too short: you spoke for (\d+) s \(insights need (\d+) s\)", text)
    if m and int(m[1]) >= int(m[2]):
        return f"too short: you spoke for just under {m[2]} s, and insights need {m[2]} s"
    return text


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def redoing(conn, session_id: str) -> bool:
    """Transcript or speaker split about to be replaced (e.g. after a split): don't suggest from it."""
    return any(row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage=? AND (status IN ('queued','running') "
                         "OR (is_current=1 AND stale=1))", session_id, st) for st in ("transcribe", "speakers", "diarize")) or \
        not current(conn, session_id, "transcribe")


# B25: a call recorded on the iPhone, shared from Notes, whose note is "Call with <name>" (Apple's title).
# shortcut: the file's name is a secondary source's word, not Apple's; change this once a real one has been shared.
PHONE_CALL = re.compile(r"^call with ", re.I)


def source(sess: dict) -> str:
    """Where it came from, as the pages say it: a phone file named like a call recording is a phone call (B25)."""
    if sess["source"] == "phone" and PHONE_CALL.match(Path(sess.get("original") or "").name):
        return "phone call"
    return sess["source"]


def summary(conn, sess: dict) -> dict:
    sp = speakers(conn, sess["id"])
    preview, words = _preview(conn, sess["id"])
    td = todo(conn, sess["id"])
    g = PT.whole(conn, sess["id"])  # B35: the list shows one discussion in parts once, on its first
    who = [{"letter": s["letter"], "name": display(s), "state": s["state"], "self": s["self"]} for s in sp]
    for n, p in enumerate(g[1:], 2):  # a later part's letters are its own: "C2" is its speaker C
        who += [{"letter": f"{s['letter']}{n}", "name": display(s), "state": s["state"], "self": s["self"]}
                for s in speakers(conn, p["id"]) if s["self"] or display(s) not in {w["name"] for w in who}]
    return {
        "id": sess["id"],
        "source": source(sess),
        "started_at": sess["started_at"] + sess["start_s"] if sess["started_at"] else None,
        "duration_s": round(sum(p["end_s"] - p["start_s"] for p in g) if g else sess["end_s"] - sess["start_s"], 1),
        "speech_s": round(sum(p["speech_s"] for p in g), 1) if g else sess["speech_s"],
        "part_count": len(g) or 1,
        "speakers": [w for i, w in enumerate(who) if not w["self"] or not any(x["self"] for x in who[:i])],
        "preview": preview,
        "todo": td,
        # A guess that it isn't a conversation (B5): offered in "Sort them", never acted on by itself.
        "maybe_aside": sess.get("status", "active") == "active"
                       and AS.maybe(sess, sp, words, without_jay=td["kind"] == "not_you"),
    }


def _preview(conn, session_id: str) -> tuple[str | None, int]:
    """The list row's first line and how many words were said: read from the diarize output (~40 KB) only when its run,
    the corrections or the transcript's run change (4 Nov: the list read and corrected every one on every visit)."""
    dz = current(conn, session_id, "diarize", output=False)
    if not dz or dz["status"] != "done":
        return None, 0
    tr = current(conn, session_id, "transcribe", output=False)
    fixes = C.for_run(conn, session_id, dz["id"])
    key = json.dumps([_lab(conn), dz["id"], tr and tr["id"], fixes], sort_keys=True, default=str)
    hit = _PREVIEW.get(session_id)
    if not hit or hit[0] != key:
        out = C.apply(load_json(row(conn, "SELECT output FROM stage_runs WHERE id=?", dz["id"])["output"]), fixes)
        lines = out["lines"] if not paused(tr, conn) else []
        hit = _PREVIEW[session_id] = (key, (next((ln["text"] for ln in lines if len(ln["text"]) > 30 and not ln["uncertain"]), None),
                                            sum(len(ln["text"].split()) for ln in out["lines"])))
    return hit[1]


_PREVIEW = FL.memo()  # session → (what it's made from, (preview, words)): a line's words, so Delete and Forget drop it


def feedback_view(conn, insight_run: str) -> dict[str, dict]:
    return {k: {"verdict": v["verdict"], "novelty": v["novelty"], "useful": v["useful"], "acted": bool(v["acted"]),
                "note": v["note"], "let_go": bool(v.get("let_go_at"))} for k, v in R.feedback_for(conn, insight_run).items()}


def trimmed(conn, sess: dict, dz_out: dict | None) -> dict | None:
    """Until it's redone after a split, the old transcript runs past the new end: cut there, as the page shows it."""
    length = sess["end_s"] - sess["start_s"]
    if dz_out and any(abs(at - sess["end_s"]) < 0.01 for at in S.for_recording(conn, sess["recording_id"])):
        dz_out = {**dz_out, "lines": [ln for ln in dz_out["lines"] if ln["start"] < length + 1.0],
                  "turns": [t for t in dz_out["turns"] if t["start"] < length + 1.0]}
    return dz_out


def detail(conn, sess: dict) -> dict:
    """One conversation as the conversation page shows it."""
    session_id = sess["id"]
    _, dz_out, fixes = diarized(conn, session_id)
    dz_out = trimmed(conn, sess, dz_out)
    tr = current(conn, session_id, "transcribe")
    num = current(conn, session_id, "numbers")
    ins = current(conn, session_id, "insight")
    why = paused(tr, conn)
    if why:  # speakers and talk time stay; words, and anything read from them, don't
        ins = None
        if dz_out:
            dz_out = {**dz_out, "lines": []}
    shown = shown_review(conn, session_id) if ins else None
    return {
        **summary(conn, sess),
        "duration_s": round(sess["end_s"] - sess["start_s"], 1),  # its own audio's, which the page plays (B35: not its parts')
        "language": load_json(tr["output"])["language"] if tr and tr["status"] == "done" else None,
        "language_unsure": bool(tr and tr["status"] == "done" and language_unsure(load_json(tr["output"]))),
        # Why its review counts only provisionally, until the owner clears it (J9, 5 Oct), or None.
        "provisional": sess["provisional"],
        # Set aside as not a conversation (B5): tv | music | other, or None.
        "aside": sess["aside"] if sess["status"] == "set_aside" else None,
        "speakers": speakers(conn, session_id),
        # B23: the calendar events it overlaps, and their invitees to offer as its speakers (offered only).
        "calendar": EV.for_session(conn, sess),
        "lines": dz_out["lines"] if dz_out else [],
        "turns": dz_out["turns"] if dz_out else [],
        "corrections": sum(1 for f in fixes if f["kind"] != "exclude"),
        "left_out": sorted({f["letter"] for f in fixes if f["kind"] == "exclude"}),
        "same_person_asks": C.same_person_asks(conn, session_id),
        # What the silence filter dropped and diarize didn't give back, to hear whether it was speech (M17).
        # Per line, the merged words neither draft has nearby: underlined on the page (M24).
        "unsupported": _by_line(unsupported(load_json(tr["output"]) if tr and tr["status"] == "done" else None),
                                dz_out["lines"] if dz_out else []),
        "dropped": dropped(load_json(tr["output"]), dz_out["turns"]) if tr and tr["status"] == "done" and dz_out and not why else [],
        "audio_deleted": bool(sess["audio_deleted_at"]),
        "numbers": load_json(num["output"]) if num and num["status"] == "done" and not num["stale"] else None,
        "insights": _shown_insights(shown),
        "insight_run": shown["id"] if shown else None,
        # review_outdated: the planner's to say (pipeline.review_outdated), added by the route (risk 8)
        "review_forgotten": bool(shown and review_held(conn, session_id, shown["id"])),  # L9: it read someone forgotten
        "feedback": feedback_view(conn, ins["id"]) if ins else {},
        # The one question, "What here was new to you?" (since 6 Oct), as answered on the review shown.
        "answer": R.answer_for(conn, shown["id"]) if shown else None,
        "ticktick": TT.sent(conn, ins["id"]) if ins else {},
        "ticktick_connected": TT.status()["connected"],
        "focus": F.for_session(conn, session_id, sess["started_at"] + sess["start_s"] if sess["started_at"] else None),
        # B15: how it went in the owner's words, and the intentions this conversation took (the review says of each).
        "reflection": IN.reflection(conn, session_id),
        "intentions": IN.for_session(conn, session_id),
        "planned": _planned(conn, session_id),  # B36: plans made for this conversation, beside its review (not read by it)
        # How he showed up (B10): six delivery numbers against his usual, and what each opens to.
        "delivery": (dl := D.for_session(conn, sess)),
        # The owner's fillers here with their own list: where in their lines (to underline), the heaviest stretches.
        "fillers": dl and dl["fillers"],
        # The recorder's own "you know" cues (B20, rec-24), on the lines they followed: what it caught and missed.
        "recorder_cues": FL.recorder_cues(conn, sess, dz_out["lines"] if dz_out else []),
        # B16: the moments the owner marked as they happened, each on the line it followed.
        "marks": MK.on_lines(MK.for_session(conn, session_id), dz_out["lines"] if dz_out else []),
        # What was promised both ways (B14, octo/commitments.py), and earlier open ones that came up here.
        "commitments": None if why or sess["status"] == "set_aside" else CM.for_session(conn, session_id),
        # Others' questions and who spoke next (B4, octo/questions.py): a count, not a judgement.
        "questions": None if why or sess["status"] == "set_aside" else QS.for_session(conn, session_id),
        "reviewed_at": sess["reviewed_at"],
        "in_gate": bool(sess["in_gate"]),
        "audio_days": None if flag(conn, "keep_recordings") else RAW_AUDIO_DAYS,  # None: kept (B29)
        "paused": why,
        # Hindi or mixed, merged by Claude from two drafts (octo/hindi.py): the page says so.
        "merged": bool(tr and tr["status"] == "done" and tr["output"] and (load_json(tr["output"]) or {}).get("merged")),
        "split": S.here(conn, sess),  # split from the one before or after: by whom, for Join back
        "split_suggestions": S.suggestions(conn, sess, dz_out["turns"],
                                           {sp["letter"] for sp in speakers(conn, session_id) if sp["self"]})
                             if dz_out and not redoing(conn, session_id) else [],
        # B35: the parts of one discussion it's one of (on the first, the later parts' lines), or its neighbours.
        "parts": PT.page(conn, sess),
    }


def claude_usage(conn, since: float) -> dict:
    """What went to Claude since then: reviews, Hindi merges and the coach's notes, from what each
    run recorded (review 4 Oct, M09). A reused review made no call; merges before 5 Oct didn't record theirs."""
    calls = secs = tokens = 0
    unknown = 0
    for r in rows(conn, "SELECT stage, output FROM stage_runs WHERE status='done' AND finished_at >= ? "
                        "AND stage IN ('insight', 'transcribe', 'commitments')", since):
        out = load_json(r["output"]) or {}
        if r["stage"] != "transcribe":  # a review or commitments (B14): one call each
            u, n = out.get("usage"), 1 if out.get("usage") else 0
        else:
            m = out.get("merged") or {}
            u, n = m.get("usage"), m.get("calls", 0)
            if n and not u:
                unknown += n
        if u:
            calls += n
            secs += (u.get("duration_ms") or 0) / 1000
            tokens += (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
    for r in rows(conn, "SELECT usage FROM coach_notes WHERE status='done' AND usage IS NOT NULL AND finished_at >= ? UNION ALL "
                        "SELECT usage FROM asks WHERE status='done' AND usage IS NOT NULL AND finished_at >= ? "
                        "AND json_extract(reader, '$.kind') = 'claude-code'", since, since):  # B24's questions too
        u = load_json(r["usage"]) or {}
        calls += 1
        secs += (u.get("duration_ms") or 0) / 1000
        tokens += (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
    return {"calls": calls, "minutes": round(secs / 60, 1), "tokens": tokens, "unrecorded_calls": unknown}


def shown_review(conn, session_id: str, output: bool = True) -> dict | None:
    """The review the page shows: done and up to date, or rated and kept until the owner asks for a redo. A later part
    of one discussion has none of its own once the whole one is shown on the first (B35): one review per discussion.
    output=False leaves the review's text out (Progress asks only which is shown)."""
    ins = _shown(conn, session_id, output)
    g = PT.group(conn, session_id) if ins else []
    return None if g and g[0]["id"] != session_id and PT.is_whole(_shown(conn, g[0]["id"], False)) else ins


def _shown(conn, session_id: str, output: bool = True) -> dict | None:
    ins = current(conn, session_id, "insight", output=output)
    return ins if ins and ins["status"] == "done" and (not ins["stale"] or pinned_review(conn, session_id)) else None


OLDER_DAYS = 14  # home's "Let the older ones go" (B40): steps and promises from conversations older than this


def open_tasks(conn) -> list[dict]:
    """Every "What to do" step from the reviews shown now that the owner hasn't marked done or let go, for
    the home page (review 4 Oct, M01): until then the only way to mark one acted was inside each past
    conversation. Since 5 Nov (review item 5) the owner's own open commitments too, as "you promised"
    (`kind` "promise"), each once: one that is the same promise as a step is the step (`promise` names it).
    Newest conversation first (B40, 12 Nov; due dates first until then, and old steps buried new ones)."""
    out, paired = [], set()
    for s in rows(conn, "SELECT s.id, s.start_s, s.in_gate, s.reviewed_at, r.started_at FROM sessions s "
                        "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active'"):
        ins = shown_review(conn, s["id"])
        review = load_json(ins["output"]) if ins else None
        if not review or review.get("skipped") or not review.get("next"):
            continue
        fb = R.feedback_for(conn, ins["id"])
        sent = TT.sent(conn, ins["id"])
        pairs = CM.paired_in(conn, s["id"], review)
        paired |= {c["id"] for c in pairs.values()}
        for i, t in enumerate(review["next"]):
            item = f"n{i}"
            if (fb.get(item) or {}).get("acted") or (fb.get(item) or {}).get("let_go_at"):
                continue  # done, or let go (6 Oct): either way, off the list
            out.append({"kind": "step", "session_id": s["id"], "run": ins["id"], "item": item,
                        "at": s["started_at"] + s["start_s"] if s["started_at"] else None,
                        "title": t.get("title") or t.get("action") or "", "due": t.get("due"),
                        "rated": R.is_rated({"kind": "try"}, fb.get(item)),
                        "counts": bool(s["in_gate"]),
                        "ticktick": sent[item]["url"] if item in sent else None,
                        "promise": pairs[item]["id"] if item in pairs else None})
    for c in rows(conn, CM._SELECT + "WHERE c.by_self=1 AND c.state IS NULL AND s.status='active'"):
        if c["id"] in paired:
            continue
        v = CM.view(c)
        out.append({"kind": "promise", "session_id": v["session_id"], "run": None, "item": v["id"], "at": v["at"],
                    "title": v["what"], "due": v["due"], "to": v["to"], "rated": False,
                    "counts": bool(row(conn, "SELECT in_gate FROM sessions WHERE id=?", v["session_id"])["in_gate"]),
                    "ticktick": (v["ticktick"] or {}).get("url"), "promise": v["id"]})
    out.sort(key=lambda t: -(t["at"] or 0))
    return out


def _shown_insights(run: dict | None) -> dict | None:
    out = load_json(run["output"]) if run else None
    if out and out.get("skipped"):
        out["skipped"] = skip_text(out["skipped"])
    return out


def unsupported(tr: dict | None, near_s: float = 15.0) -> list[tuple[float, str]]:
    """In a Hindi transcript merged by Claude: words found in neither local draft within near_s
    seconds, as (time, word). Allowed sound-alike fixes land here too, so it's a pointer for the
    ear, not a verdict (review 4 Oct, M24). Spelling only: a word in another script counts as unsupported."""
    from .hindi import UNCLEAR, _key
    if not tr or not tr.get("merged") or not tr.get("drafts"):
        return []
    drafts = sorted((w["start"], _key(w["w"])) for d in tr["drafts"].values() for seg in d for w in seg.get("words", []))
    out = []
    for seg in tr.get("segments", []):
        for w in seg.get("words", []):
            k = _key(w["w"])
            if not k or UNCLEAR in w["w"]:
                continue
            if not any(dk == k for t, dk in drafts if abs(t - w["start"]) <= near_s):
                out.append((w["start"], w["w"]))
    return out


def _by_line(words: list[tuple[float, str]], lines: list[dict]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for t, w in words:
        ln = next((x for x in lines if x["start"] - 0.05 <= t <= x["end"] + 0.05), None)
        if ln:
            out.setdefault(ln["id"], []).append(w)
    return out


def _planned(conn, session_id: str) -> list[dict]:
    from .prepare import planned_for
    return planned_for(conn, session_id)
