"""Questions people ask the owner, and whether they spoke next (B4, 13 Oct, docs/backlog.md), counted at read time.

13 Oct: *"Measure if I'm able to respond to questions well - and if I'm resolving the query anyone has
asked me."* Their decision (13 Oct): start with a local count, no model, and only add a Claude pass that
judges "resolved" once this shows there's something to measure.

So this is the rough first layer, over the lines already stored, nothing re-run:
- a **question** is a line of someone else's with a question mark in it (the transcription's punctuation,
  as for the numbers stage's questions); their questions in a row, before anyone else speaks, are one;
- **who spoke next**: the first line by anyone else that starts within REPLY_S of the question's end
  (or overlaps it): the owner ("you"), someone else ("other"), or nobody in time ("nobody").

It can't tell whether a question was meant for the owner (in a group, many aren't), whether what they said answered
it, or whether it was resolved: that's the model pass, later. The page and the numbers say so. Only in
conversations the owner is confirmed in, and never "wrong" or "right": who spoke next, that's all.
"""

from __future__ import annotations

import time

from . import fillers as FL
from .db import new_id, row, rows

REPLY_S = 8.0  # someone who starts talking later than this after a question isn't answering it


def _lines(conn, session_id: str) -> tuple[list[dict], set[str], int]:
    """(lines with the owner's corrections, the owner's letters, how many others talk ≥ 5 s); no lines when the
    transcript isn't used (a paused language)."""
    from .sessions import diarized, paused, speakers
    _, out, _ = diarized(conn, session_id)
    tr = row(conn, "SELECT id, target, output, status FROM stage_runs WHERE target=? AND stage='transcribe' AND is_current=1", session_id)
    if not out or (tr and paused(tr, conn)):
        return [], set(), 0
    mine = {s["letter"] for s in speakers(conn, session_id) if s["self"]}
    others = sum(1 for k, v in (out.get("talk_s") or {}).items() if v >= 5.0 and k not in mine)
    return sorted(out["lines"], key=lambda ln: ln["start"]), mine, others


def moments(lines: list[dict], mine: set[str]) -> list[dict]:
    """Others' questions in order, each with who spoke next. Pure: tested on made-up lines."""
    out: list[dict] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln["speaker"] in mine or "?" not in ln["text"]:
            i += 1
            continue
        # the asker's questions in a row, until someone else speaks, are one question
        j = i
        while j + 1 < len(lines) and lines[j + 1]["speaker"] == ln["speaker"]:
            j += 1
        last_q = max(k for k in range(i, j + 1) if "?" in lines[k]["text"])
        end = lines[last_q]["end"]
        nxt = next((x for x in lines[i + 1:] if x["speaker"] != ln["speaker"]), None)
        if nxt is not None and nxt["start"] - end <= REPLY_S:
            outcome = "you" if nxt["speaker"] in mine else "other"
            reply = nxt
        else:
            outcome, reply = "nobody", None
        out.append({"line": lines[last_q]["id"], "start": ln["start"], "end": end, "by": ln["speaker"], "outcome": outcome,
                    "reply": reply["id"] if reply else None, "after_s": round(reply["start"] - end, 1) if reply else None})
        i = j + 1
    return out


def for_session(conn, session_id: str) -> dict | None:
    """One conversation's questions from others, as its page shows them; None when the owner isn't confirmed in
    it or it has no lines to read."""
    lines, mine, others = _lines(conn, session_id)
    if not lines or not mine:
        return None
    ms = moments(lines, mine)
    count = {k: sum(1 for m in ms if m["outcome"] == k) for k in ("you", "other", "nobody")}
    return {"asked": len(ms), **count, "size": "one" if others <= 1 else "group", "moments": ms, "reply_s": REPLY_S}


def summary(conn) -> dict:
    """Pooled over every conversation the owner is confirmed in, by size: in a 1:1 the other person's questions
    are nearly all to them; in a group many aren't, so the two are never added together."""
    by = {"one": {"asked": 0, "you": 0, "other": 0, "nobody": 0, "conversations": 0},
          "group": {"asked": 0, "you": 0, "other": 0, "nobody": 0, "conversations": 0}}
    keep = ("asked", "you", "other", "nobody", "size")  # the counts only: the moments are the page's
    for q in FL.memoized(conn, _COUNTS, lambda s: (q := for_session(conn, s["id"])) and {"at": 0, **{k: q[k] for k in keep}}):
        if not q["asked"]:
            continue
        b = by[q["size"]]
        b["conversations"] += 1
        for k in ("asked", "you", "other", "nobody"):
            b[k] += q[k]
    return by


_COUNTS = FL.memo()  # each conversation's questions, kept until what they're made from changes (fillers.memoized)


# ---- the check by ear (B4): does "who spoke next" hold up? -------------------------------------

VERDICTS = ("answered", "unanswered", "not_question")


def to_check(conn) -> dict:
    """The 1:1 questions to hear, "nobody spoke next" first (the share that looked high), then the rest,
    newest conversation first; and what the owner has said so far, against what the app said."""
    from .sessions import speakers
    done = {(r["session_id"], round(r["start_s"], 2)): r for r in rows(conn, "SELECT * FROM question_checks")}
    items = []
    for s in rows(conn, "SELECT s.id, s.start_s, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                        "WHERE s.status='active' ORDER BY coalesce(r.started_at + s.start_s, 0) DESC"):
        q = for_session(conn, s["id"])
        if not q or q["size"] != "one":
            continue
        lines, _, _ = _lines(conn, s["id"])
        by_id = {ln["id"]: ln for ln in lines}
        names = {sp["letter"]: sp["name"] for sp in speakers(conn, s["id"])}
        for m in q["moments"]:
            got = done.get((s["id"], round(m["start"], 2)))
            items.append({"session_id": s["id"], "at": (s["started_at"] or 0) + s["start_s"], **m,
                          "text": by_id[m["line"]]["text"], "by_name": names.get(m["by"]),
                          "reply_end": by_id[m["reply"]]["end"] if m["reply"] is not None else None,
                          "verdict": got["verdict"] if got else None})
    items.sort(key=lambda i: (i["outcome"] != "nobody",))  # stable: newest first within each
    return {"items": items, "summary": checks_summary(conn)}


def save_check(conn, session_id: str, start: float, verdict: str | None) -> dict:
    """The owner's ear on one question (None takes it back)."""
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"not one of {', '.join(VERDICTS)}")
    q = for_session(conn, session_id)
    m = next((m for m in (q or {}).get("moments", []) if abs(m["start"] - start) < 0.01), None)
    if not m:
        raise KeyError(session_id)
    conn.execute("DELETE FROM question_checks WHERE session_id=? AND abs(start_s - ?) < 0.01", (session_id, start))
    if verdict:
        lines, _, _ = _lines(conn, session_id)
        text = next(ln["text"] for ln in lines if ln["id"] == m["line"])
        conn.execute("INSERT INTO question_checks (id, session_id, start_s, line, text, app, verdict, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (new_id("qc"), session_id, m["start"], m["line"], text, m["outcome"],
                                                         verdict, time.time()))
    return checks_summary(conn)


def checks_summary(conn) -> dict:
    """What the owner heard, against what the app said: {app outcome: {verdict: n}}."""
    out: dict[str, dict[str, int]] = {}
    for r in rows(conn, "SELECT app, verdict, count(*) n FROM question_checks GROUP BY app, verdict"):
        out.setdefault(r["app"], {})[r["verdict"]] = r["n"]
    return {"by_app": out, "checked": sum(sum(v.values()) for v in out.values())}
