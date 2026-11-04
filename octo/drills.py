"""Practice drills (B18, docs/backlog.md; coaching plan C7): a short take on a prompt from the owner's own list, and
how it went on B10's six delivery measures against their earlier takes.

Kept apart (K4): a drill is not a recording and not a conversation. It has its own table and its own audio folder
(audio/lab/drills/), so nothing that reads sessions (the list, reviews, search, the coach, Progress's counts) can
see it. It gets its own line on Progress, which starts with the two pitch memos (J6: the sessions kept out of the
counts, in_gate=0).

Heard by one local step: Qwen3-ASR in English with its aligner (octo/asr.py), in a worker the pipeline's runner
starts before anything else (the owner is waiting). No language vote and no speakers: every word is the owner's.

Then where the point came (coaching plan C5; 22 Oct, after the pilot on the pitch memos: learnings, Speech,
21 Oct): one call per take to the review's own model (settings.reader, as the coach), the take's words and its
prompt only. The quote is checked against what was heard and timed from the word times, or it's dropped; the
20-word version is shown with its count, since the pilot's ran over 6 times in 14. Within the 60 Claude calls a
day; a usage limit waits for its reset. Under MIN_WORDS, no call.

    python -m octo.drills <drill_id>      (the runner's worker) hear one take
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import AUDIO, HOME, ROOT
from . import audio as A
from . import delivery as D
from . import fillers as FL
from . import insight as IN
from .db import config, load_json, new_id, row, rows

FOLDER = AUDIO / "drills"
PROMPTS_DEFAULT = ["Explain one thing from your work in 90 s, answer first"]  # C7's, the only one to start
LATELY = 5              # takes pooled for "lately" on Progress
MIN_WORDS = 60          # of a take, before it's placed against the others (a 90 s take is ~200 words)
WORKER_TIMEOUT_S = 15 * 60
MODEL, EFFORT = "claude-opus-5-5", "high"  # when the reader names none (the first lab's Claude Code)
PARAMS = {"model": "Qwen/Qwen3-ASR-1.7B", "highpass_hz": 80.0, "normalize_dbfs": -20.0, "max_gain_db": 40.0,
          "min_speech_overlap": 0.3, "segment_gap_s": 1.0}  # the transcribe stage's, in English


class DrillError(ValueError):
    pass


# ---- the prompt list (edited like the filler list) ------------------------------------------------

def prompts(conn) -> list[str]:
    return config(conn, "drill_prompts", list(PROMPTS_DEFAULT))


def set_prompt(conn, text: str, on: bool) -> list[str]:
    p = " ".join((text or "").split())
    if not 3 <= len(p) <= 200:
        raise DrillError("a prompt of a few words, up to 200 characters")
    got = [x for x in prompts(conn) if x != p] + ([p] if on else [])
    conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('drill_prompts', ?, strftime('%s','now')) "
                 "ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (json.dumps(got),))
    return got


def today(conn, now: float | None = None) -> str | None:
    """One prompt a day, going round the list."""
    ps = prompts(conn)
    return ps[int(time.strftime("%j", time.localtime(now))) % len(ps)] if ps else None


# ---- a take ------------------------------------------------------------------------------------------

def add(conn, src: Path, prompt: str) -> str:
    """Store a take as FLAC (16 kHz mono, as recordings) and queue it to be heard."""
    did = new_id("drl")
    dst = FOLDER / f"{did}.flac"
    A.store(src, dst)
    conn.execute("INSERT INTO drills (id, prompt, path, duration_s, created_at) VALUES (?, ?, ?, ?, ?)",
                 (did, prompt, str(dst.relative_to(HOME)), A.duration(dst), time.time()))
    return did


def delete(conn, drill_id: str) -> bool:
    d = row(conn, "SELECT path FROM drills WHERE id=?", drill_id)
    if not d:
        return False
    (HOME / d["path"]).unlink(missing_ok=True)
    conn.execute("DELETE FROM drills WHERE id=?", (drill_id,))  # secure_delete: its words are gone from the file
    return True


def drop_old_audio(conn, cutoff: float, now: float) -> None:
    """privacy.drop_old_audio's rule for drills: the audio goes, the words and numbers stay."""
    for d in rows(conn, "SELECT id, path FROM drills WHERE audio_deleted_at IS NULL AND created_at < ?", cutoff):
        (HOME / d["path"]).unlink(missing_ok=True)
        conn.execute("UPDATE drills SET audio_deleted_at=? WHERE id=?", (now, d["id"]))


def transcribe(path: Path) -> list[dict]:
    """Qwen's segments for a take: English, everything kept (no speech detector to check it against)."""
    from .asr import louder, qwen_words
    samples = louder(A.read(path), [], PARAMS)
    segments, rejected = qwen_words(samples, [], "en", PARAMS)
    return segments + rejected


def hear(conn, drill_id: str) -> None:
    """The worker's job: hear one take, then mark it done or failed."""
    d = row(conn, "SELECT * FROM drills WHERE id=?", drill_id)
    if not d or d["status"] != "queued":
        return
    try:
        segs = transcribe(HOME / d["path"])
    except Exception as e:  # noqa: BLE001 - whatever it was, the page says so
        traceback.print_exc()
        conn.execute("UPDATE drills SET status='failed', error=? WHERE id=?", (f"{type(e).__name__}: {e}", drill_id))
        return
    conn.execute("UPDATE drills SET status='done', error=NULL, transcript=? WHERE id=?", (json.dumps(segs), drill_id))


def run_queued(conn, log=print, lock_fd: int | None = None) -> int:
    """The pipeline's runner calls this before each step: every queued take, oldest first, each in a worker (the
    model's memory goes back when it exits) holding the runner's lock, like a stage."""
    n = 0
    for d in rows(conn, "SELECT id FROM drills WHERE status='queued' ORDER BY created_at"):
        log(f"drill · {d['id']}")
        try:
            p = subprocess.run([sys.executable, "-m", "octo.drills", d["id"]], cwd=ROOT, capture_output=True, text=True,
                               pass_fds=(lock_fd,) if lock_fd is not None else (), timeout=WORKER_TIMEOUT_S)
            err = (p.stderr.strip().splitlines() or ["the worker stopped"])[-1]
        except subprocess.TimeoutExpired:
            err = f"took over {WORKER_TIMEOUT_S // 60} min and was stopped"
        conn.execute("UPDATE drills SET status='failed', error=? WHERE id=? AND status='queued'", (err, d["id"]))
        n += 1
    return n



# ---- where the point came (B18, 22 Oct) -----------------------------------------------------------

POINT_SCHEMA = {"type": "object", "properties": {
    "point_line": {"type": "integer"}, "point_quote": {"type": "string"},
    "build_up_right": {"type": "boolean"}, "build_up_why": {"type": "string"}, "short": {"type": "string"}},
    "required": ["point_line", "point_quote", "build_up_right", "build_up_why", "short"]}

POINT_SYSTEM = """You read a transcript of {name} speaking alone: a practice take on the prompt "{prompt}". {name} wants
to get to the point sooner. Find where the point is.

- point_quote: the one sentence that carries the point, copied exactly from line point_line. Copy the words as they
  are, fillers included; "..." may stand for words left out.
- build_up_right: whether building up before the point was the right call here (context the listener needed first),
  or whether the point should have come first. build_up_why: one sentence on why.
- short: the take said in at most 20 words, as {name} could have said it.
Quote only from the transcript. Line numbers are in square brackets."""


def _lines(d: dict) -> list[dict]:
    return [{"id": i, "speaker": "me", "start": x["start"], "end": x["end"], "text": x["text"]}
            for i, x in enumerate(segments(d))]


def check_point(raw: dict, lines: list[dict], words: list[dict]) -> dict:
    """The answer, its quote checked against what was heard and timed from the word times, or dropped."""
    by_id = {ln["id"]: ln for ln in lines}
    q = IN.verify_quote({"line": raw.get("point_line"), "text": raw.get("point_quote", "")}, by_id)
    short = " ".join((raw.get("short") or "").split())
    out = {"short": short, "short_words": len(short.split()), "build_up_right": bool(raw.get("build_up_right")),
           "build_up_why": (raw.get("build_up_why") or "").strip(), "quote": None}
    if q:
        at, measured = IN.quote_start(q, by_id[q["line"]], words)
        out["quote"] = {"text": q["text"], "line": q["line"], "at": round(at, 1), "measured": measured}
    else:
        out["dropped"] = "its quote isn't in what the lab heard"
    return out


def find_point(conn, d: dict, who: dict, ask=None) -> None:
    """One call for one heard take; the answer or the failure is stored on it."""
    if ask is None:
        from . import claude_code as CC
        from . import providers as PV
        ask = CC.ask if who["kind"] == "claude-code" else \
            (lambda model, effort, system, prompt, schema: PV.ask(who, system, prompt, schema))
    lines = _lines(d)
    me = row(conn, "SELECT name FROM people WHERE is_self=1 LIMIT 1")
    name = (me["name"] if me else None) or "the speaker"
    try:
        raw, usage = ask(who.get("model") or MODEL, who.get("effort") or EFFORT,
                         POINT_SYSTEM.format(name=name, prompt=d["prompt"]), IN.transcript(lines, {"me": name}), POINT_SCHEMA)
        out = check_point(raw, lines, [w for x in segments(d) for w in x.get("words") or []])
    except Exception as e:  # noqa: BLE001 - stored, and shown on the take
        conn.execute("UPDATE drills SET point_error=?, point_at=? WHERE id=?", (str(e)[:2000] or type(e).__name__, time.time(), d["id"]))
        return
    conn.execute("UPDATE drills SET point=?, point_error=NULL, point_at=? WHERE id=?",
                 (json.dumps(out | {"reader": who, "usage": usage}), time.time(), d["id"]))


def find_points(conn, log=print, ask=None, now: float | None = None) -> int:
    """The runner's, after hearing: each heard take without its point, oldest first. A failure stays failed, except
    Claude's usage limit, tried again once it resets; nothing is asked while the day's calls are used."""
    from . import pipeline as P
    from .settings import reader
    now = time.time() if now is None else now
    who = reader(conn)
    if who is None:  # no model chosen for reviews yet
        return 0
    n, ls = 0, None
    for d in rows(conn, "SELECT * FROM drills WHERE status='done' AND point IS NULL ORDER BY created_at"):
        if d["point_error"] and ((r := P.limit_reset(d["point_error"], d["point_at"])) is None or r > now):
            continue
        if (who["kind"] == "claude-code" and P.claude_blocked_until(conn, now)) or \
                (who.get("connection") and P.budget_blocked_until(conn, who["connection"], now)):
            break
        ls = ls or D.lists(conn)
        t = take(d, ls)
        if not t or t["words"] < MIN_WORDS:
            conn.execute("UPDATE drills SET point=?, point_at=? WHERE id=?",
                         (json.dumps({"skipped": f"under {MIN_WORDS} words"}), now, d["id"]))
            continue
        log(f"drill point · {d['id']}")
        find_point(conn, d, who, ask)
        n += 1
    return n


def point_view(conn, d: dict) -> dict | None:
    """What the take's page shows of it."""
    if d["status"] != "done":
        return None
    p = load_json(d["point"]) if d["point"] else None
    if p and p.get("skipped"):
        return {"state": "skipped", "why": p["skipped"]}
    if p:
        return {"state": "done"} | {k: p.get(k) for k in ("quote", "dropped", "short", "short_words", "build_up_right", "build_up_why")}
    if d["point_error"]:
        from .pipeline import limit_reset
        return {"state": "waiting"} if limit_reset(d["point_error"], d["point_at"]) else {"state": "failed", "error": d["point_error"]}
    from .settings import reader
    return {"state": "waiting" if reader(conn) else "off"}

# ---- the numbers ------------------------------------------------------------------------------------

def segments(d: dict) -> list[dict]:
    return load_json(d["transcript"]) or []


def take(d: dict, ls: dict) -> dict | None:
    """A take's record in delivery's shape (delivery.value and pooled read it), or None until it's heard."""
    if d["status"] != "done":
        return None
    segs = segments(d)
    text = [s["text"] for s in segs]
    tw = sorted(({"w": w["w"], "start": w["start"], "end": w["end"], "me": True, "spk": "me"}
                 for s in segs for w in s.get("words") or []), key=lambda w: w["start"])
    t = D.timing(tw, ls["skip"]) if tw else None
    return {"drill_id": d["id"], "at": d["created_at"], "prompt": d["prompt"], "duration_s": d["duration_s"],
            "words": sum(FL.words(x) for x in text),
            "fillers": sum(len(FL.find(x, ls["fillers_pat"], ls["fillers"])) for x in text),
            "hedges": sum(len(FL.find(x, ls["hedges_pat"], ls["hedges"])) for x in text),
            "pauses": len(t["pauses"]) if t else None, "restarts": len(t["restarts"]) if t else None,
            "speak_s": t["speak_s"] if t else None, "timed_words": t["timed_words"] if t else None,
            "longest_s": round(t["longest"]["end"] - t["longest"]["start"], 1) if t and t["longest"] else None,
            "on_line": True, "size": "solo"}


def memos(conn, ls: dict) -> list[dict]:
    """The pitch memos (J6) in the same shape: the drill line's start."""
    got = [c for s in rows(conn, "SELECT s.*, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                                 "WHERE s.status='active' AND s.in_gate=0") if (c := D.conversation(conn, s, ls))]
    return [{**c, "on_line": True, "memo": True} for c in got if c["words"]]


def line(conn, ls: dict | None = None) -> list[dict]:
    """The pitch memos, then every heard take, oldest first."""
    ls = ls or D.lists(conn)
    takes = [t for d in rows(conn, "SELECT * FROM drills ORDER BY created_at") if (t := take(d, ls))]
    return sorted(memos(conn, ls), key=lambda c: c["at"] or 0) + takes


def _row(c: dict, before: list[dict], m: str) -> dict:
    r = D.row_for(c, before, m, min_words=MIN_WORDS)
    last = next((v for x in reversed(before) if (v := D.value(x, m)) is not None), None)
    return {**r, "last": None if last is None else round(last, 2)}


def lead(conn) -> str | None:
    """The focus's measure, when it's one of the six: it leads."""
    from .focus import active
    f = active(conn)
    return f["measure"] if f and f.get("measure") in D.MEASURES else None


def public(d: dict) -> dict:
    return {k: d[k] for k in ("id", "prompt", "duration_s", "created_at", "status", "error")} | \
        {"audio": d["audio_deleted_at"] is None}


def detail(conn, drill_id: str) -> dict | None:
    """One take: what was said, and each of the six against every take before it (the pitch memos first)."""
    d = row(conn, "SELECT * FROM drills WHERE id=?", drill_id)
    if not d:
        return None
    ls = D.lists(conn)
    out = public(d) | {"text": " ".join(s["text"] for s in segments(d)), "rows": [], "words": None,
                       "min_words": MIN_WORDS, "point": point_view(conn, d)}
    t = take(d, ls)
    if t:
        everything = line(conn, ls)
        before = [x for x in everything if (x["at"] or 0) < t["at"] and x.get("drill_id") != t["drill_id"]]
        m0 = lead(conn)
        order = ([m0] if m0 else []) + [m for m in D.MEASURES if m != m0]
        out |= {"rows": [_row(t, before, m) for m in order], "words": t["words"], "lead": m0}
    return out


def summary(conn) -> dict:
    """The Practice page: today's prompt, the list, the focus's measure, and the takes, newest first."""
    ls = D.lists(conn)
    takes = rows(conn, "SELECT * FROM drills ORDER BY created_at DESC")
    return {"today": today(conn), "prompts": prompts(conn), "lead": lead(conn),
            "takes": [public(d) | {"words": (t or {}).get("words")} for d in takes for t in [take(d, ls)]]}


def progress(conn) -> dict:
    """Progress's drill line: each of the six, take by take, the pitch memos first. Never in its counts."""
    ls = D.lists(conn)
    pts = line(conn, ls)
    r2 = lambda v: None if v is None else round(v, 2)
    memo, takes = [p for p in pts if p.get("memo")], [p for p in pts if not p.get("memo")]
    return {"takes": len(takes), "memos": len(memo), "lately_n": min(LATELY, len(takes)),
            "rows": [{"measure": m, "start": r2(D.pooled(memo, m)), "lately": r2(D.pooled(takes[-LATELY:], m)),
                      "latest": r2(D.value(takes[-1], m)) if takes else None} for m in D.MEASURES],
            "points": [{"at": p["at"], "memo": bool(p.get("memo")), "words": p["words"],
                        **{m: r2(D.value(p, m)) for m in D.MEASURES}} for p in pts]}


if __name__ == "__main__":
    from .db import connect
    hear(connect(), sys.argv[1])
