"""Reviewing a conversation, and the counts on the Progress page.

A conversation is **reviewed** once its insights have run (not skipped as too short) and the owner has
answered one question about it, "What here was new to you?": they pick the items that were (they
are marked new, `feedback.novelty`), or says nothing was (`review_answers.nothing_new`). That's the
rule since 6 Oct (the owner, E2 in docs/coach-plan.md). Before, every finding, reading, between-the-lines
point and step needed both questions answered, is it right and is it new to you; conversations
rated that way keep counting. "Is it right" is now an optional flag on any item. Once reviewed it
stays reviewed, even if insights re-run later (the new version asks the question again, but the
count isn't lost). Conversations the owner marked "not counted" (sessions.in_gate = 0) are left out.

The counts were the first lab's Phase 0 check (D8: 10 reviewed, 1 in 3 with something new, 2 acted on, by
5 Nov). All three were met on 19 Oct, and the owner retired the check that day (docs/gate-plan.md); the
counts stay, with no target or date. The beta reads the same counts for its testers (octo/beta.py).
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from .db import load_json, row, rows

COUNTING_SINCE = date(2026, 10, 1)  # when the first lab started counting reviews; the weekly table splits here
RECENT = 10  # "new" over the last this many reviewed, beside the total: whether novelty wears off

VERDICTS = ("right", "partly", "wrong")
NOVELTY = ("new", "knew")
USEFUL = ("yes", "no")


def items(insights: dict) -> list[dict]:
    """What there is to answer for in one insight run, in the order the page shows it.
    A Claude review (octo/review_any.py) adds the one thing (o, since 6 Oct), readings of the
    others (r), between the lines (l) and next steps (n). `required` is the rule for conversations
    rated item by item before 6 Oct (every required item rated); since then one question decides
    "reviewed" (review_answers)."""
    out = []
    if insights.get("one_thing"):
        out.append({"id": "o", "kind": "finding", "principle": None, "required": True})
    for i, _ in enumerate(insights.get("reading", [])):
        out.append({"id": f"r{i}", "kind": "finding", "principle": None, "required": True})
    for i, _ in enumerate(insights.get("between", [])):
        out.append({"id": f"l{i}", "kind": "finding", "principle": None, "required": True})
    for f in insights.get("conduct", []):
        out.append({"id": f"p{f['principle']}", "kind": "finding", "principle": f["principle"], "required": True})
        if f.get("try_next_time"):
            out.append({"id": f"t{f['principle']}", "kind": "try", "principle": f["principle"], "required": True})
    for i, _ in enumerate(insights.get("next", [])):
        out.append({"id": f"n{i}", "kind": "try", "principle": None, "required": True})
    for i, _ in enumerate(insights.get("beneath", [])):
        out.append({"id": f"b{i}", "kind": "question", "principle": None, "required": False})
    return out


def is_rated(item: dict, fb: dict | None) -> bool:
    if not fb:
        return False
    if item["kind"] == "question":
        return fb.get("useful") is not None
    return fb.get("verdict") is not None and fb.get("novelty") is not None


def feedback_for(conn, insight_run: str) -> dict[str, dict]:
    return {r["item"]: r for r in rows(conn, "SELECT * FROM feedback WHERE insight_run=?", insight_run)}


def left_to_rate(insights: dict, fb: dict[str, dict]) -> int:
    return sum(1 for it in items(insights) if it["required"] and not is_rated(it, fb.get(it["id"])))


def mark_reviewed_if_done(conn, session_id: str, insight_run: str) -> bool:
    """The rule from before 6 Oct: every required item rated. Since then `answer` marks it."""
    run = row(conn, "SELECT output FROM stage_runs WHERE id=?", insight_run)
    out = load_json(run["output"]) if run else None
    if not out or out.get("skipped") or not items(out) or left_to_rate(out, feedback_for(conn, insight_run)):
        return False
    conn.execute("UPDATE sessions SET reviewed_at=? WHERE id=? AND reviewed_at IS NULL", (time.time(), session_id))
    return True


# ---- the one question (since 6 Oct) ----------------------------------------------------------

class AnswerError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status  # 404: no such review or item; 422: not an answer


def answer_for(conn, insight_run: str) -> dict | None:
    """The owner's answer to "What here was new to you?" on this review: the items they picked, or nothing new."""
    got = row(conn, "SELECT nothing_new, answered_at FROM review_answers WHERE insight_run=?", insight_run)
    if not got:
        return None
    picked = [r["item"] for r in rows(conn, "SELECT item FROM feedback WHERE insight_run=? AND novelty='new' ORDER BY item",
                                      insight_run)]
    return {"nothing_new": bool(got["nothing_new"]), "new": picked, "answered_at": got["answered_at"]}


def is_answered(conn, insight_run: str, insights: dict, fb: dict[str, dict] | None = None) -> bool:
    """This review needs nothing more from the owner: the question is answered, or (before 6 Oct) every
    required item was rated."""
    if row(conn, "SELECT 1 FROM review_answers WHERE insight_run=?", insight_run):
        return True
    its = items(insights)
    return bool(its) and not left_to_rate(insights, fb if fb is not None else feedback_for(conn, insight_run))


def answer(conn, session_id: str, insight_run: str, new: list[str], nothing_new: bool, now: float | None = None) -> None:
    """Record the answer: the items picked are marked new and the rest aren't (a "knew it" from
    item-by-item rating stays as it was); or nothing was new. Either way the conversation is
    reviewed. Answering again changes the picks and keeps when it was first answered."""
    run = row(conn, "SELECT output FROM stage_runs WHERE id=? AND target=? AND stage='insight' AND status='done'",
              insight_run, session_id)
    out = load_json(run["output"]) if run else None
    if not out or out.get("skipped"):
        raise AnswerError("no review to answer for", 404)
    ids = [it["id"] for it in items(out) if it["kind"] != "question"]
    picked = set(new)
    if picked - set(ids):
        raise AnswerError("no such item in this review", 404)
    if nothing_new == bool(picked):
        raise AnswerError("pick what was new, or say nothing was")
    now = now or time.time()
    for it in ids:
        if it in picked:
            conn.execute("INSERT OR IGNORE INTO feedback (insight_run, item, session_id, updated_at) VALUES (?, ?, ?, ?)",
                         (insight_run, it, session_id, now))
            conn.execute("UPDATE feedback SET novelty='new', updated_at=? WHERE insight_run=? AND item=?", (now, insight_run, it))
        else:
            conn.execute("UPDATE feedback SET novelty=NULL, updated_at=? WHERE insight_run=? AND item=? AND novelty='new'",
                         (now, insight_run, it))
    conn.execute("INSERT INTO review_answers (insight_run, session_id, nothing_new, answered_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (insight_run) DO UPDATE SET nothing_new=excluded.nothing_new",
                 (insight_run, session_id, int(nothing_new), now))
    conn.execute("UPDATE sessions SET reviewed_at=? WHERE id=? AND reviewed_at IS NULL", (now, session_id))


# ---- the counts and the progress page ---------------------------------------------------------

def _week(ts: float) -> str:
    d = datetime.fromtimestamp(ts).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def checks(conn, until: float | None = None) -> dict:
    """Reviewed, with something new, acted on: as they stand now, or for conversations reviewed by `until`.

    "New to me" counts only answers on each conversation's current review, on items not marked
    wrong: an answer on a review since replaced, or on something the owner said is wrong, isn't evidence
    that the lab told them something new (review 4 Oct, M02). Since 6 Oct an item picked in answer to
    the one question is "new" without a verdict; before, both questions were asked of every item.
    Acted counts any review's items, once per item: the same step marked again on a redone review is
    still one thing acted on."""
    from .parts import heads
    reviewed = rows(conn, "SELECT s.id, s.provisional, s.reviewed_at FROM sessions s WHERE s.status='active' AND s.reviewed_at IS NOT NULL "
                          "AND s.in_gate=1 AND (? IS NULL OR s.reviewed_at < ?)", until, until)
    ids = [s["id"] for s in reviewed]
    # B35: one discussion in parts counts once, whichever of its parts were answered; their answers all count for it.
    head = (lambda h: lambda sid: h.get(sid, sid))(heads(conn))
    per: dict[str, dict] = {}  # each discussion as last reviewed, provisional if any part is
    for s in sorted(reviewed, key=lambda s: s["reviewed_at"]):
        per[head(s["id"])] = {**s, "provisional": s["provisional"] or (per.get(head(s["id"])) or {}).get("provisional")}
    marks = ",".join("?" * len(ids)) or "''"
    fb = rows(conn, f"SELECT f.*, sr.is_current FROM feedback f LEFT JOIN stage_runs sr ON sr.id = f.insight_run "
                    f"WHERE f.session_id IN ({marks})", *ids)
    now = [f for f in fb if f["is_current"]]
    with_new = {head(f["session_id"]) for f in now if f["novelty"] == "new" and f["verdict"] != "wrong"}
    acted = len({(f["session_id"], f["item"]) for f in fb if f["acted"]})
    # What the owner could have marked wrong: the items of each reviewed conversation's current review.
    shown = 0
    for r in rows(conn, f"SELECT output FROM stage_runs WHERE stage='insight' AND is_current=1 AND status='done' "
                        f"AND target IN ({marks})", *ids):
        out = load_json(r["output"])
        if out and not out.get("skipped"):
            shown += sum(1 for it in items(out) if it["kind"] != "question")
    nothing_new = row(conn, f"SELECT count(*) n FROM review_answers a JOIN stage_runs sr ON sr.id = a.insight_run "
                            f"WHERE sr.is_current=1 AND a.nothing_new=1 AND a.session_id IN ({marks})", *ids)["n"]
    n = len(per)
    recent = {h for h, s in sorted(per.items(), key=lambda kv: -kv[1]["reviewed_at"])[:RECENT]}
    return {
        "checks": {
            # Provisional ones count; how many is said beside the number (J9).
            "reviewed": {"value": n, "provisional": sum(1 for s in per.values() if s["provisional"])},
            "new_share": {"value": len(with_new), "of": n},
            "recent_new": {"value": len(with_new & recent), "of": len(recent)},
            "acted": {"value": acted},
        },
        "wrong": {"value": sum(1 for f in now if f["verdict"] == "wrong"), "of": shown},
        # Context for reading "new": how the owner answered on current reviews.
        # "knew" is from item-by-item rating (before 6 Oct); "nothing_new" from the one question since.
        "novelty": {"new": sum(1 for f in now if f["novelty"] == "new" and f["verdict"] != "wrong"),
                    "knew": sum(1 for f in now if f["novelty"] == "knew"), "nothing_new": nothing_new},
    }


def progress(conn, principles: list[dict]) -> dict:
    counts = {"since": COUNTING_SINCE.isoformat(), **checks(conn)}
    # When the last conversation was recorded: days without input should be visible (M03).
    last = row(conn, "SELECT max(r.started_at + s.start_s) at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                     "WHERE s.status='active'")
    counts["last_conversation_at"] = last["at"] if last else None

    # Which principles had evidence, across conversations whose insights ran.
    # Only the review each conversation shows: up to date, or rated and kept (M32: a stale local run
    # was counted as a sixth conversation with insights).
    from .planning import pinned_review
    runs = rows(conn, "SELECT sr.id, sr.output, sr.stale, sr.target FROM stage_runs sr JOIN sessions s ON s.id = sr.target "
                      "WHERE sr.stage='insight' AND sr.is_current=1 AND sr.status='done' AND s.status='active'")
    from .sessions import shown_review  # B35: a later part whose review is the whole's, on its first part, isn't another
    runs = [r for r in runs if (not r["stale"] or pinned_review(conn, r["target"]))
            and shown_review(conn, r["target"], output=False)]
    outs = [o for o in (load_json(r["output"]) for r in runs) if o and not o.get("skipped")]
    # By the principle's words where the run recorded them, so a renumbered rubric still lines up.
    same = lambda f, p: f["principle_id"] == p["id"] if f.get("principle_id") and p.get("id") else f["principle"] == p["n"]
    coverage = [{"n": p["n"], "title": p["title"],
                 "conversations": sum(1 for o in outs if any(same(f, p) for f in o["conduct"]))}
                for p in principles]

    # By principle (review 4 Oct, M29): each principle in the owner's words, and what each review shown now
    # said about it, newest first, with his rating. Read from the reviews as they are: no model call.
    # Read once for every review, not per review (architecture review 5 Nov, risk 5).
    when = {x["id"]: x["at"] for x in rows(conn, "SELECT s.id, r.started_at + s.start_s AS at FROM sessions s "
                                                   "JOIN recordings r ON r.id = s.recording_id")}
    named: dict[str, list[tuple[str, int]]] = {}  # conversation → (name, is_self) confirmed in its current diarize run
    for x in rows(conn, "SELECT ss.session_id, ss.speaker, p.name, p.is_self FROM session_speakers ss JOIN people p "
                        "ON p.id = ss.person_id JOIN stage_runs dz ON dz.id = ss.diarize_run AND dz.is_current=1 "
                        "WHERE ss.state IN ('confirmed', 'auto') ORDER BY ss.session_id, ss.speaker"):
        named.setdefault(x["session_id"], []).append((x["speaker"], x["name"], x["is_self"]))
    rated: dict[str, dict] = {}
    for f in rows(conn, "SELECT insight_run, item, verdict FROM feedback"):
        rated.setdefault(f["insight_run"], {})[f["item"]] = {"item": f["item"], "verdict": f["verdict"]}
    seen = []
    for r in runs:
        if r["stale"] and not pinned_review(conn, r["target"]):
            continue
        o = load_json(r["output"])
        if not o or o.get("skipped"):
            continue
        others = list(dict.fromkeys(name for _, name, me in named.get(r["target"], []) if not me))
        seen.append((when.get(r["target"]), r["target"], others, o, rated.get(r["id"], {})))
    seen.sort(key=lambda x: -(x[0] or 0))
    by_principle = [{"n": p["n"], "text": p.get("text", p["title"]), "seen": [
        {"session_id": sid, "at": at, "with": others, "title": f.get("title"), "observation": f.get("observation"),
         "item": f"p{f['principle']}", "verdict": (fb.get(f"p{f['principle']}") or {}).get("verdict"),
         "held": f.get("held")}  # held / partly / missed / depends since 6 Oct; None before
        for at, sid, others, o, fb in seen for f in o.get("conduct", []) if same(f, p)]} for p in principles]

    # Neutral counts per week, for the owner only.
    weeks: dict[str, dict] = {}
    numbers = {x["target"]: x["sp"] for x in rows(conn, "SELECT target, json_extract(output, '$.speakers') sp FROM stage_runs "
                                                       "WHERE stage='numbers' AND is_current=1 AND status='done'")}
    for s in rows(conn, "SELECT s.id, r.started_at + s.start_s AS at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                        "WHERE s.status='active' AND r.started_at IS NOT NULL"):
        # The owner's letters in the current diarize run, as the numbers have them after their merges: when two
        # of his letters were merged, the first one found was often the merged-away one (M32, a23d51f1).
        me = [k for k, _, mine in named.get(s["id"], []) if mine]
        w = weeks.setdefault(_week(s["at"]), {"week": _week(s["at"]), "conversations": 0, "share": [], "questions": 0, "talked_over": 0})
        w["conversations"] += 1
        nums = load_json(numbers[s["id"]]) if s["id"] in numbers else {}
        mine = [nums[k] for k in me if k in nums]
        if mine:
            w["share"].append(sum(m["share"] for m in mine))
            w["questions"] += sum(m["questions"] for m in mine)
            w["talked_over"] += sum(m["interrupted_others"] for m in mine)
    weekly = [{**w, "share": round(sum(w["share"]) / len(w["share"]), 3) if w["share"] else None}
              for w in sorted(weeks.values(), key=lambda w: w["week"], reverse=True)]
    return {"counts": counts, "coverage": coverage, "by_principle": by_principle, "weekly": weekly,
            "insight_conversations": len(outs)}
