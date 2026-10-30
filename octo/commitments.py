"""Commitments both ways (B14, Jay 19 Oct; coaching plan C5): what the owner promised, and what others
promised them, in each conversation. A step of its own after the review, so the review's prompt, inputs and
input hash stay exactly as they were and no review is made again.

- **What counts:** a promise of something concrete (send the deck, call the restaurant, decide by Friday),
  made by one person to another, one of them the owner. Not intentions, opinions or advice.
- **Same safety as the review:** the same transcript text (speakers left out of insights stay out, see
  octo/worker.py), on the model the step's choice names, through octo/providers.py (Claude Code runs with
  tools off, in an empty folder). Every quote is checked against the transcript in code, and must be the
  promiser's own line, or the commitment is dropped. It waits for confirmed speakers and for the review.
- **Next time:** the step is given the open commitments with the people in this conversation, from earlier
  ones, and says which came up (each with a checked quote).
- **History:** only conversations from the 14 days before the step shipped, and every one since
  (`app_config.commitments_from`, set once in db._migrate). Older ones are never queued.
- **The owner's marks** (Done / Dropped on their own, They did / They didn't on others') are kept in the
  `commitments` table, never counted as "acted" on Progress: these are what was said, not the review's advice.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime
from pathlib import Path

from . import providers as PV
from .db import config, load_json, new_id, row, rows
from .insight import MATERIAL, memo_key, transcript, verify_quote

CODE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]

MINE, THEIRS = ("done", "dropped"), ("did", "didnt")  # the owner's marks on their own, and on others'

QUOTE = {"type": "object", "properties": {"line": {"type": "integer"}, "text": {"type": "string"}}, "required": ["line", "text"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "commitments": {"type": "array", "items": {"type": "object", "properties": {
            "by": {"type": "string"}, "to": {"type": "string"}, "what": {"type": "string"}, "quote": QUOTE,
            "due": {"type": "string"}}, "required": ["by", "to", "what", "quote", "due"]}},
        "came_up": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "note": {"type": "string"}, "quote": QUOTE}, "required": ["id", "note", "quote"]}},
    },
    "required": ["commitments", "came_up"],
}


def system_prompt(owner: str) -> str:
    return f"""You find the commitments in a recorded conversation for {owner}: what {owner} promised the others, and what they promised {owner}.

A commitment is a promise to do something concrete: send, call, check, decide, deliver, be somewhere, by a time or not. It is said by one person to another, and one of the two is {owner}. Not a commitment: a vague intention ("we should catch up"), an opinion, advice, a question, something already done, or a promise between two other people.

The fields:
- commitments: each one, in the order said. "by" is who promised and "to" who it was promised to, each exactly as the transcript names them. "what" is the promise as a short task, at most twelve words, that reads right after the promiser's name and names people instead of "you" or "me" ("Send Asha a written update with both dates"). "quote" is the promiser's own line that makes the promise, with its line number; if they promised by agreeing ("Yes, Thursday"), that line. "due" is the date it was promised by as YYYY-MM-DD, worked out from the conversation's date and what was said ("by Thursday", "tomorrow"), or "" if no time was said.
- came_up: of the open commitments from earlier conversations listed with the transcript (if any), the ones this conversation talks about: their "id", a "note" of one sentence on what was said about it (done, late, changed, asked about), and the "quote" where it came up.

Quotes: copy the words exactly from the line you cite; "…" may join fragments of one line, in order. They are checked against the transcript in code, and a commitment whose quote isn't the promiser's own words is dropped. Lines marked "(speaker or words unsure)" may be misattributed: prefer others. When unsure whether something is a promise, leave it out.

{MATERIAL}"""


def open_text(items: list[dict]) -> str:
    return "\n".join(f"- id {o['id']} ({o['when']}): {o['by']} promised {o['to']}: {o['what']}"
                     + (f" (by {o['due']})" if o.get("due") else "") for o in items)


def user_prompt(text: str, when: str, earlier: list[dict]) -> str:
    dated = f"This conversation was on {when}.\n\n" if when else ""
    before = f"Open commitments from earlier conversations with these people:\n{open_text(earlier)}\n\n" if earlier else ""
    return f"{dated}{before}Transcript, one line per utterance ([line] time speaker: words):\n{text}"


def _letters(names: dict[str, str], name: str) -> set[str]:
    return {k for k, v in names.items() if v.strip().lower() == (name or "").strip().lower()}


def check(raw: dict, by_id: dict[int, dict], names: dict[str, str], self_letters: set[str], earlier: list[dict]) -> dict:
    """The answer as kept: each commitment only with a verified quote of the promiser's own words, between the
    owner and someone else in the conversation; each "came up" only for an id it was given, with a verified quote."""
    out = {"commitments": [], "came_up": [], "dropped": []}
    seen = set()
    for c in raw.get("commitments") or []:
        what = (c.get("what") or "").strip()
        by, to = _letters(names, c.get("by")), _letters(names, c.get("to"))
        q = verify_quote(c.get("quote") or {}, by_id)
        reason = ("nothing promised" if not what else
                  "not between you and someone here" if not by or not to or by & to or bool(by & self_letters) == bool(to & self_letters) else
                  "its quote isn't in the transcript" if not q else
                  "its quote isn't the promiser's own words" if q["speaker"] not in by else None)
        if reason or (q["line"], what.lower()) in seen:
            if reason:
                out["dropped"].append({"what": what, "by": c.get("by"), "reason": reason})
            continue
        seen.add((q["line"], what.lower()))
        due = (c.get("due") or "").strip()
        try:
            due = date.fromisoformat(due).isoformat() if due else None
        except ValueError:
            due = None
        line = by_id[q["line"]]
        to_letter = sorted(to)[0]
        out["commitments"].append({"by_letter": q["speaker"], "to_letter": to_letter, "by": names[q["speaker"]],
                                   "to": names[to_letter], "by_self": q["speaker"] in self_letters, "what": what,
                                   "line": q["line"], "quote": q["text"], "start": line["start"], "end": line["end"],
                                   "due": due, "uncertain": q["uncertain"]})
    given = {o["id"] for o in earlier}
    for c in raw.get("came_up") or []:
        q = verify_quote(c.get("quote") or {}, by_id)
        if c.get("id") not in given or not q or any(x["id"] == c["id"] for x in out["came_up"]):
            continue
        out["came_up"].append({"id": c["id"], "note": (c.get("note") or "").strip(), "line": q["line"], "quote": q["text"],
                               "start": by_id[q["line"]]["start"], "end": by_id[q["line"]]["end"]})
    return out


def run(diarize: dict, params: dict, started_at: float | None = None, memo: dict | None = None, ask=PV.ask,
        on_progress=lambda msg: None) -> dict:
    """The step: one call for the whole conversation, its answer checked, kept with the run (`memo`) so an
    unchanged conversation costs nothing to redo."""
    from .review_any import when_text
    reader = params.get("reader") or {"kind": "claude-code", "model": params["model"], "effort": params.get("effort", "high")}
    names = {s["speaker"]: s["name"] for s in params["speakers"]}
    mine = {s["speaker"] for s in params["speakers"] if s["self"]}
    earlier = params.get("open") or []
    out = {"kind": "commitments", "model": reader["model"], "engine": reader["kind"], "skipped": None, "commitments": [],
           "came_up": [], "dropped": [], "seconds": 0.0, "reused": 0, "usage": None, "memo": {}}
    lines = diarize["lines"]
    if not lines or not mine:
        out["skipped"] = "no words" if not lines else "you're not in it"
        return out
    owner = names[sorted(mine)[0]]
    system = system_prompt(owner)
    prompt = user_prompt(transcript(lines, names), when_text(started_at), earlier)
    key = memo_key("commitments", CODE, {k: v for k, v in reader.items() if k != "api_key"}, system, prompt)
    t0 = time.time()
    memo = memo or {}
    if key in memo:
        raw, usage = memo[key]["raw"], memo[key]["usage"]
        out["reused"] = 1
    else:
        on_progress(f"looking for commitments ({reader['model']})")
        raw, usage = ask(reader, system, prompt, SCHEMA)
    out["memo"][key] = {"raw": raw, "usage": usage}
    out.update(check(raw, {ln["id"]: ln for ln in lines}, names, mine, earlier))
    out["usage"] = usage
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ---- what the planner reads (octo/planning.py) --------------------------------------------------

def _at(conn, session_id: str) -> float | None:
    r = row(conn, "SELECT coalesce(r.started_at + s.start_s, r.created_at) AS at FROM sessions s "
                  "JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", session_id)
    return r["at"] if r else None


def in_history(conn, session_id: str) -> bool:
    """From the 14 days before the step shipped, or since (Jay, 19 Oct): older conversations are never queued."""
    at = _at(conn, session_id)
    return at is not None and at >= config(conn, "commitments_from", 0)


def people_in(conn, session_id: str) -> dict[str, str]:
    """Speaker letter → person id, for the speakers confirmed (or named without asking) in the current diarize run."""
    dz = row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", session_id)
    return {r["speaker"]: r["person_id"] for r in rows(
        conn, "SELECT speaker, person_id FROM session_speakers WHERE session_id=? AND diarize_run=? AND person_id IS NOT NULL "
              "AND state IN ('confirmed', 'auto')", session_id, dz["id"] if dz else None)}


def _day(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%a %-d %b") if ts else "an earlier day"


def open_with(conn, session_id: str, person_ids: set[str]) -> list[dict]:
    """Open commitments between the owner and these people, from conversations before this one."""
    at = _at(conn, session_id) or time.time()
    marks = ",".join("?" * len(person_ids))
    if not marks:
        return []
    found = rows(conn, f"SELECT c.*, coalesce(r.started_at + s.start_s, r.created_at) AS at FROM commitments c "
                       f"JOIN sessions s ON s.id = c.session_id JOIN recordings r ON r.id = s.recording_id "
                       f"WHERE c.state IS NULL AND c.session_id != ? AND s.status='active' "
                       f"AND (c.by_person IN ({marks}) OR c.to_person IN ({marks})) ORDER BY at, c.start_s",
                 session_id, *person_ids, *person_ids)
    return [{"id": c["id"], "when": _day(c["at"]), "by": c["by_name"], "to": c["to_name"], "what": c["what"], "due": c["due"]}
            for c in found if (c["at"] or 0) < at]


def ready(conn, session_id: str) -> bool:
    """After the review (its current run done; for a part of one discussion, the whole's), within the history, and not once the owner has marked any of
    this conversation's commitments: like a rated review, those stay as they are."""
    from .planning import insight_ready
    from .settings import BY_STAGE, selected
    if not in_history(conn, session_id) or not insight_ready(conn, session_id) or not selected(conn, BY_STAGE["commitments"]):
        return False
    from .parts import group
    g = group(conn, session_id)  # B35: a part of one discussion waits for the review of the whole, on its first part
    if not row(conn, "SELECT 1 FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1 AND status='done'",
               g[0]["id"] if g else session_id):
        return False
    return not row(conn, "SELECT 1 FROM commitments WHERE session_id=? AND (state IS NOT NULL OR ticktick IS NOT NULL OR checked IS NOT NULL)", session_id)


def inputs(conn, session_id: str) -> dict:
    """Who's who (with their people, so a promise lands on the right page), the owner's corrections, and the open
    commitments with them. That list is taken once: the run made with it keeps it, so marking a commitment in
    another conversation doesn't send this one round again."""
    from .planning import corrections_input, settled_speakers
    people = people_in(conn, session_id)
    speakers = [{**s, "person": people.get(s["speaker"])} for s in settled_speakers(conn, session_id) or []]
    cur = row(conn, "SELECT params FROM stage_runs WHERE target=? AND stage='commitments' AND is_current=1", session_id)
    was = load_json(cur["params"]) if cur else None
    earlier = was["open"] if was and "open" in was else \
        open_with(conn, session_id, {s["person"] for s in speakers if s["person"] and not s["self"]})
    return {"speakers": speakers, **corrections_input(conn, session_id), "open": earlier}


# ---- after a run (octo/pipeline.py) -------------------------------------------------------------

def apply(conn, run: dict) -> None:
    """The run's commitments into the table. The owner's marked ones (and any sent to TickTick or checked) stay; the rest
    are replaced, and a new one saying the same as a kept one isn't added twice."""
    out = load_json(run["output"]) or {}
    params = load_json(run["params"]) or {}
    people = {s["speaker"]: s.get("person") for s in params.get("speakers", [])}
    sid = run["target"]
    conn.execute("DELETE FROM commitments WHERE session_id=? AND state IS NULL AND ticktick IS NULL AND checked IS NULL", (sid,))
    kept = {(r["by_self"], r["what"].lower()) for r in rows(conn, "SELECT by_self, what FROM commitments WHERE session_id=?", sid)}
    for c in out.get("commitments", []):
        if (int(c["by_self"]), c["what"].lower()) in kept:
            continue
        conn.execute("INSERT INTO commitments (id, session_id, run_id, by_self, by_person, to_person, by_name, to_name, what, "
                     "line, quote, start_s, end_s, due, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (new_id("com"), sid, run["id"], int(c["by_self"]), people.get(c["by_letter"]), people.get(c["to_letter"]),
                      c["by"], c["to"], c["what"], c["line"], c["quote"], c["start"], c["end"], c["due"], time.time()))


# ---- what the pages show ---------------------------------------------------------------------------

_SELECT = ("SELECT c.*, coalesce(pb.name, c.by_name) AS by_now, coalesce(pt.name, c.to_name) AS to_now, "
           "coalesce(r.started_at + s.start_s, r.created_at) AS at FROM commitments c "
           "JOIN sessions s ON s.id = c.session_id JOIN recordings r ON r.id = s.recording_id "
           "LEFT JOIN people pb ON pb.id = c.by_person LEFT JOIN people pt ON pt.id = c.to_person ")


def view(c: dict) -> dict:
    return {"id": c["id"], "session_id": c["session_id"], "at": c["at"], "mine": bool(c["by_self"]),
            "by": c["by_now"], "to": c["to_now"], "by_person": c["by_person"], "to_person": c["to_person"],
            "what": c["what"], "quote": c["quote"], "line": c["line"], "start": c["start_s"], "end": c["end_s"],
            "due": c["due"], "state": c["state"], "ticktick": load_json(c["ticktick"]), "checked": c["checked"]}


def get(conn, commitment_id: str) -> dict | None:
    c = row(conn, _SELECT + "WHERE c.id=?", commitment_id)
    return view(c) if c else None


def for_session(conn, session_id: str) -> dict:
    """The conversation page's block: its commitments, those from before that came up here, and how the step stands."""
    items = [view(c) for c in rows(conn, _SELECT + "WHERE c.session_id=? ORDER BY c.start_s, c.id", session_id)]
    cur = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='commitments' AND is_current=1 AND status='done'",
              session_id)
    came = []
    for u in ((load_json(cur["output"]) or {}).get("came_up") or []) if cur else []:
        c = get(conn, u["id"])
        if c:  # deleted or forgotten since: gone from here too
            came.append({**c, "note": u["note"], "here": {"line": u["line"], "quote": u["quote"], "start": u["start"],
                                                          "end": u["end"]}})
    last = row(conn, "SELECT status, error FROM stage_runs WHERE target=? AND stage='commitments' ORDER BY queued_at DESC LIMIT 1",
               session_id)
    status = (last["status"] if last and last["status"] in ("queued", "running", "failed") else
              "done" if cur else "old" if not in_history(conn, session_id) else "waiting")
    return {"items": items, "came_up": came, "status": status, "error": last["error"] if status == "failed" else None}


def for_person(conn, person_id: str) -> list[dict]:
    """Open commitments both ways with this person, oldest first."""
    return [view(c) for c in rows(conn, _SELECT + "WHERE c.state IS NULL AND s.status='active' AND "
                                         "(c.by_person=? OR c.to_person=?) ORDER BY at, c.start_s", person_id, person_id)]


def mark(conn, commitment_id: str, state: str | None) -> dict:
    """Done / Dropped on the owner's own, They did / They didn't on others'; None opens it again."""
    c = get(conn, commitment_id)
    if not c:
        raise KeyError(commitment_id)
    if state is not None and state not in (MINE if c["mine"] else THEIRS):
        raise ValueError(f"{state!r} isn't a mark for {'your' if c['mine'] else 'their'} commitment")
    conn.execute("UPDATE commitments SET state=?, state_at=? WHERE id=?", (state, time.time() if state else None, commitment_id))
    return get(conn, commitment_id)


def to_ticktick(conn, commitment_id: str, add=None) -> dict:
    """One of the owner's own into their TickTick, once, like a "What to do" step (octo/ticktick.py)."""
    from . import ticktick as TT
    from .review_any import when_text
    c = get(conn, commitment_id)
    if not c:
        raise KeyError(commitment_id)
    if not c["mine"]:
        raise ValueError("only your own commitments go to TickTick")
    if c["ticktick"]:
        return c
    task = {"title": c["what"], "detail": f"You said: “{c['quote']}”", "why": f"You promised {c['to']}.", "due": c["due"]}
    got = (add or TT.add)(task, when_text(c["at"]) or c["session_id"], c["session_id"])
    conn.execute("UPDATE commitments SET ticktick=? WHERE id=?", (json.dumps(got), commitment_id))
    return get(conn, commitment_id)


# ---- the owner's check of the step (Jay, 19 Oct: the first ten by eye before they're trusted) -----------------------

VERDICTS = ("right", "partly", "wrong")
FIRST = 10


def to_check(conn) -> dict:
    """Every commitment in an active conversation, oldest first (the first ten are the ones to check), and the counts."""
    items = [view(c) for c in rows(conn, _SELECT + "WHERE s.status='active' ORDER BY at, c.start_s, c.id")]
    counts = {v: sum(i["checked"] == v for i in items) for v in VERDICTS}
    return {"items": items, "counts": counts, "checked": sum(counts.values()), "first": FIRST}


def judge(conn, commitment_id: str, verdict: str | None) -> dict:
    """Right / partly / wrong: whether the step got this one right. None takes it back. Changes nothing else."""
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"{verdict!r} isn't right, partly or wrong")
    if not get(conn, commitment_id):
        raise KeyError(commitment_id)
    conn.execute("UPDATE commitments SET checked=?, checked_at=? WHERE id=?",
                 (verdict, time.time() if verdict else None, commitment_id))
    return to_check(conn)
