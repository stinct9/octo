"""The coach (docs/coach-plan.md §4.3): what the owner's reviews say across conversations, as one note.

6 Oct: *"if this app were a coach, what would it tell me to improve on"*. One Claude call on
demand, not a pipeline stage. It reads the reviews shown now (not transcripts), the owner's answers and
marks on them, their focus and how they answered it, and their numbers, and says what to **work on** (seen
in which conversations, with its moments), what to **try** (when … → instead …), how they'll **know**,
what to **keep**, what **changed** since the last note, and what it **can't tell** yet.

- Checked in code: every theme cites review items by the ids the prompt gave them, from at least two
  conversations; a theme that doesn't is dropped and listed. Its moments are those items' quotes,
  verified against the transcript when each review was made.
- Items the owner marked not right are left out before sending.
- What leaves the Mac (E1, 6 Oct): the reviews' text (names and quotes included), their marks and
  notes on them, their reflections in their own words (B15), their focus text and answers, their numbers and
  their principles; no transcript beyond
  the reviews' own quotes, no audio, no voiceprints.
- Every conversation a note read is recorded with it (coach_sources): deleting the conversation, or
  forgetting someone who was in it, deletes the note.
- A call counts toward the day's Claude calls (pipeline.DAILY_CLAUDE_CALLS) and waits out Claude's
  usage limit like the pipeline's own steps; a usage-limit failure here makes the pipeline wait too.
"""

from __future__ import annotations

import json
import subprocess
import time
from contextvars import ContextVar
from datetime import datetime

from . import claude_code as CC
from . import owner as O
from . import fillers as FL
from . import focus as F
from . import intention as IN
from . import measures as M
from . import review as R
from .db import load_json, new_id, row, rows, tx
from . import parts as PT
from .insight import MATERIAL, principles
from .rubric import text as rubric_text

MODEL, EFFORT = "claude-opus-5-5", "high"
MAX_CONVERSATIONS = 20       # the newest reviews it reads
MIN_CONVERSATIONS = 2        # a theme needs two conversations, so the coach needs at least two reviews
MAX_QUOTE = 240              # characters of a quote it's sent
QUOTES_PER_ITEM = 2
STOPPED_AFTER_S = CC.TIMEOUT_S + 300  # a note still "running" after this was stopped (the server restarted)
PARTS = ("work_on", "keep", "also0", "also1")
VERDICTS = ("right", "partly", "wrong")
NOVELTY = ("new", "knew")
# How he'll know: one of his numbers, or his own answer after each conversation. Not the delivery measures
# (B10): they aren't checked by ear yet, so the coach doesn't choose them.
KNOW = ("fillers", "questions", "talk_share", "talked_over", "focus")
# Which item of a theme a focus points at, so "Did it" marks something he can act on (A3).
ACTABLE = ("one_thing", "principle", "step", "try", "between", "reading")
# The owner's pronoun while the prompt is put together (octo/owner.py, product plan 4.2): the helpers
# that phrase marks and numbers read it from here.
_FORMS: ContextVar[dict] = ContextVar("coach_forms", default=O.forms({}))


def _p() -> dict:
    return _FORMS.get()


class CoachError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status  # 404 no such note · 409 one is running · 422 can't ask now


EVIDENCE = {"type": "array", "items": {"type": "string"}}
THEME = {"type": "object", "properties": {"theme": {"type": "string"}, "why": {"type": "string"}, "evidence": EVIDENCE},
         "required": ["theme", "why", "evidence"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "work_on": {"type": "object", "properties": {
            "theme": {"type": "string"}, "why": {"type": "string"}, "evidence": EVIDENCE,
            "try": {"type": "object", "properties": {"when": {"type": "string"}, "instead": {"type": "string"}},
                    "required": ["when", "instead"]},
            "know": {"type": "object", "properties": {"measure": {"type": "string", "enum": list(KNOW)},
                                                      "how": {"type": "string"}}, "required": ["measure", "how"]}},
            "required": ["theme", "why", "evidence", "try", "know"]},
        "keep": THEME,
        "also": {"type": "array", "items": THEME},
        "changed": {"type": "string"},
        "limits": {"type": "string"},
    },
    "required": ["work_on", "keep", "also", "changed", "limits"],
}


# ---- what it reads ----------------------------------------------------------------------------

def _day(ts: float) -> str:
    """'Tuesday 6 Oct'."""
    t = datetime.fromtimestamp(ts)
    return f"{t:%A} {t.day} {t:%b}"


def _when(ts: float) -> str:
    """'Tuesday 6 Oct 2026, 10:30'."""
    t = datetime.fromtimestamp(ts)
    return f"{t:%A} {t.day} {t:%b %Y, %H:%M}"


def reviews(conn) -> list[dict]:
    """The conversations the coach can read, newest first: since the lab began, with a review shown
    now that wasn't skipped as too short."""
    from .sessions import shown_review
    out = []
    for s in rows(conn, "SELECT s.id, s.start_s, s.end_s, s.reviewed_at, r.started_at, r.audio_deleted_at FROM sessions s "
                        "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active' AND r.started_at IS NOT NULL"):
        at = s["started_at"] + s["start_s"]
        if datetime.fromtimestamp(at).date() < FL.LAB_START:
            continue
        ins = shown_review(conn, s["id"])
        review = load_json(ins["output"]) if ins else None
        if not review or review.get("skipped"):
            continue
        out.append({"session": s, "at": at, "run": ins, "review": review})
    out.sort(key=lambda c: -c["at"])
    return out


def entries(review: dict) -> list[dict]:
    """Each item of a review the coach may cite, with the ids the page and the ratings use
    (review.items): the one thing, readings, between the lines, principles (and an early local
    review's try-next-time), steps."""
    out = []
    if one := review.get("one_thing"):
        out.append({"item": "o", "kind": "one_thing", "label": "The one thing", "headline": one.get("headline", ""),
                    "body": one.get("why", ""), "quotes": one.get("quotes", [])})
    for i, r in enumerate(review.get("reading", [])):
        out.append({"item": f"r{i}", "kind": "reading", "label": f"Reading {r.get('person', 'them')}",
                    "headline": r.get("headline", ""), "body": r.get("observation", ""), "quotes": r.get("quotes", [])})
    for i, b in enumerate(review.get("between", [])):
        out.append({"item": f"l{i}", "kind": "between", "label": "Between the lines", "headline": b.get("headline", ""),
                    "body": b.get("observation", ""), "quotes": b.get("quotes", [])})
    for f in review.get("conduct", []):
        out.append({"item": f"p{f['principle']}", "kind": "principle", "label": f"Principle {f['principle']}",
                    "headline": f.get("title", ""), "body": f.get("observation", ""), "quotes": f.get("quotes", []),
                    "held": f.get("held")})
        if f.get("try_next_time"):
            out.append({"item": f"t{f['principle']}", "kind": "try", "label": f"Try next time (principle {f['principle']})",
                        "headline": f["try_next_time"], "body": "", "quotes": []})
    for i, n in enumerate(review.get("next", [])):
        out.append({"item": f"n{i}", "kind": "step", "label": "Step", "headline": n.get("title") or n.get("action") or "",
                    "body": n.get("detail", ""), "quotes": []})
    return out


def marks(fb: dict | None, kind: str) -> list[str]:
    """What the owner said about an item, in words for the prompt."""
    if not fb:
        return []
    p = _p()
    out = []
    if fb.get("verdict") == "partly":
        out.append(f"partly right, {p['they']} said" + (f": \"{fb['note'].strip()}\"" if (fb.get("note") or "").strip() else ""))
    elif fb.get("verdict") == "right":
        out.append(f"right, {p['they']} said")
    if fb.get("novelty") == "new":
        out.append(f"new to {p['them']}")
    elif fb.get("novelty") == "knew":
        out.append(f"{p['they']} knew it")
    if fb.get("acted"):
        out.append(f"{p['they']} did it" if kind == "step" else f"{p['they']} acted on it")
    if fb.get("let_go_at"):
        out.append(f"{p['they']} let it go")
    return out


def _quote(q: dict, names: dict[str, str], ends: dict[int, float]) -> dict:
    """A review's quote as the coach is sent it and the page plays it: its line's start and end."""
    text = " ".join((q.get("text") or "").split())
    return {"text": text if len(text) <= MAX_QUOTE else text[:MAX_QUOTE - 1].rstrip() + "…",
            "who": names.get(q.get("speaker") or "", "someone"), "line": q.get("line"), "start": q.get("start"),
            "end": ends.get(q.get("line")), "mine": names.get(q.get("speaker") or "") == "you"}


def _numbers_line(conn, s: dict, phr: list[str], pat) -> str:
    """His numbers in one conversation, in words."""
    parts = []
    if c := M._one(conn, s):
        parts.append(f"{c['share']:.0%} of the talk")
        parts.append(f"{c['questions']} question{'s' if c['questions'] != 1 else ''}")
        if not c["alone"]:
            parts.append(f"talked over someone {c['talked_over']} time{'s' if c['talked_over'] != 1 else ''}")
    f = FL.conversation(conn, s, phr, pat)
    if f and f["words"] and f["on_line"]:
        parts.append(f"fillers {f['rate']:.1f} per 100 of {_p()['their']} {f['words']} words")
    return ", ".join(parts)


def _focus_text(f: dict, tally: dict, numbers: dict | None) -> str:
    out = f"\"{f['text']}\", from {_day(f['started_at'])}" + (f" to {_day(f['ended_at'])}" if f["ended_at"] else "")
    p = _p()
    out += (f". In the conversations while it was on: {p['they']} did it in {tally['did']}, it came up and {p['they']} missed it in "
            f"{tally['missed']}, it didn't come up in {tally['na']}")
    if numbers and numbers.get("since"):
        out += f". {M.LABEL[numbers['measure']]}: {_value(numbers['measure'], numbers['since']['value'])} since it started"
        if numbers.get("before"):
            out += f", {_value(numbers['measure'], numbers['before']['value'])} before"
    return out + "."


def _value(measure: str, v: float) -> str:
    return f"{v:.0%}" if measure == "talk_share" else f"{v:.1f}"


def _trend_lines(conn) -> list[str]:
    """His numbers over time: the first fortnight against now, with whether the change is beyond
    ordinary variation."""
    out, p = [], _p()
    phr = FL.phrases(conn)
    t = FL.trend([c for c in FL.all_conversations(conn, phr) if c["on_line"]])
    if t["baseline"] and t["now"]:
        verdict = {"lower": "lower, beyond ordinary variation", "higher": "higher, beyond ordinary variation",
                   "within": "within ordinary variation"}[t["change"]]
        out.append(f"- Fillers per 100 words ({p['their']} own list: {', '.join(phr)}; counted in the transcripts, "
                   f"which drop some): first fortnight {t['baseline']['rate']:.1f} over {t['baseline']['words']} words, "
                   f"now {t['now']['rate']:.1f} over {p['their']} last {t['now']['words']} words: {verdict}.")
    elif t["baseline"]:
        out.append(f"- Fillers per 100 words ({p['their']} own list: {', '.join(phr)}): {t['baseline']['rate']:.1f} "
                   f"over {p['their']} first {t['baseline']['words']} words; not enough words since for a trend.")
    ck = FL.checks_summary(conn)
    if ck["checks"]:
        out.append(f"- {p['They']} counted {p['their']} fillers by ear in {ck['checks']} stretch{'es' if ck['checks'] > 1 else ''}: "
                   f"{p['they']} heard {ck['by_ear']}, the transcript has {ck['by_app']}.")
    cs = M.conversations(conn)
    if cs:
        end = cs[0]["at"] + FL.BASELINE_DAYS * 86400
        first, after = [c for c in cs if c["at"] < end], [c for c in cs if c["at"] >= end]
        for m in ("questions", "talk_share", "talked_over"):
            a, b = M.pooled(first, m), M.pooled(M._last_minutes(after), m) if after else None
            if not a:
                continue
            line = f"- {M.LABEL[m]}: {_value(m, a['value'])} in the first fortnight ({a['conversations']} conversations)"
            if b:
                line += f", {_value(m, b['value'])} lately ({b['conversations']})"
            out.append(line + ".")
    return out


def material(conn, now: float | None = None) -> dict:
    """Everything the coach reads, and the ids its answer may cite (`ids`: "c2.p7" → that item), with
    the owner's profile (`owner`) and the forms of their pronoun (`forms`)."""
    profile = O.load(conn)
    forms = O.forms(profile)
    token = _FORMS.set(forms)
    try:
        m = _material(conn, now, profile)
    finally:
        _FORMS.reset(token)
    return {**m, "owner": profile, "forms": forms}


def _material(conn, now: float | None, profile: dict) -> dict:
    from .sessions import diarized, display, speakers
    now = now or time.time()
    text = rubric_text()
    me = row(conn, "SELECT name FROM people WHERE is_self=1 LIMIT 1")
    phr = FL.phrases(conn)
    pat = FL.matcher(phr)
    convs, ids, left_out = [], {}, 0
    for k, c in enumerate(reviews(conn)[:MAX_CONVERSATIONS], 1):
        s, run = c["session"], c["run"]
        whole = PT.whole_lines(conn, s["id"])  # B35: one review of a discussion in parts, its letters and lines its own
        sp = list(whole[1].values()) if whole else speakers(conn, s["id"])
        names = {x["letter"]: "you" if x["self"] else display(x) for x in sp}
        others = [x["name"] for x in sp if not x["self"] and x["name"] and x["state"] in ("confirmed", "auto")]
        _, dz, _ = diarized(conn, s["id"])
        ends = {ln["id"]: ln["end"] for ln in (whole[0] if whole else dz["lines"] if dz else [])}
        fb = R.feedback_for(conn, run["id"])
        items, wrong = [], 0
        for e in entries(c["review"]):
            f = fb.get(e["item"]) or {}
            if f.get("verdict") == "wrong":  # he said it's not right: the coach never sees it
                wrong += 1
                continue
            key = f"c{k}.{e['item']}"
            quotes = [_quote(q, names, ends) for q in e["quotes"][:QUOTES_PER_ITEM]]
            items.append({**e, "id": key, "quotes": quotes, "marks": marks(f, e["kind"])})
            first = next((q for q in quotes if q["mine"]), quotes[0] if quotes else None)
            ids[key] = {"session_id": s["id"], "insight_run": run["id"], "item": e["item"], "at": c["at"], "conv": k,
                        "kind": e["kind"], "label": e["label"], "headline": e["headline"], "held": e.get("held"),
                        "quote": first}
        left_out += wrong
        foc = F.at(conn, c["at"])
        focus_then = None
        if foc and foc["session_id"] != s["id"]:
            got = row(conn, "SELECT answer FROM focus_checks WHERE focus_id=? AND session_id=?", foc["id"], s["id"])
            focus_then = {"text": foc["text"], "answer": got["answer"] if got else None}
        convs.append({"key": f"c{k}", "session_id": s["id"], "at": c["at"], "minutes": round((s["end_s"] - s["start_s"]) / 60),
                      "with": others, "answered": R.is_answered(conn, run["id"], c["review"], fb),
                      "summary": (c["review"].get("summary") or "").strip(), "numbers": _numbers_line(conn, s, phr, pat),
                      "items": items, "left_out": wrong, "focus": focus_then, "limits": (c["review"].get("limits") or "").strip(),
                      "reflection": (IN.reflection(conn, s["id"]) or {}).get("text")})
    fs = F.summary(conn)
    last = row(conn, "SELECT * FROM coach_notes WHERE status='done' ORDER BY created_at DESC LIMIT 1")
    return {"jay": me["name"] if me else (profile.get("name") or "you"), "today": now, "principles": principles(text), "conversations": convs,
            "ids": ids, "left_out": left_out, "focus": fs, "trend": _trend_lines(conn),
            "last": _last_note(conn, last) if last else None,
            "read": {"conversations": len(convs), "answered": sum(1 for c in convs if c["answered"]),
                     "items": len(ids), "left_out": left_out,
                     "from": min((c["at"] for c in convs), default=None), "to": max((c["at"] for c in convs), default=None)}}


def _last_note(conn, n: dict) -> dict:
    """The last note as the next one reads it: its themes, the owner's marks on them, the focus it led to."""
    out = load_json(n["output"]) or {}
    fb = {r["part"]: r for r in rows(conn, "SELECT * FROM coach_feedback WHERE note_id=?", n["id"])}
    parts = []
    for part, t in (("work_on", out.get("work_on")), ("keep", out.get("keep")),
                    *((f"also{i}", a) for i, a in enumerate(out.get("also") or []))):
        if not t:
            continue
        said = [w for w in ({"right": "right", "partly": "partly right", "wrong": "not right"}.get((fb.get(part) or {}).get("verdict")),
                            {"new": f"new to {_p()['them']}", "knew": f"{_p()['they']} knew it"}.get((fb.get(part) or {}).get("novelty"))) if w]
        parts.append({"part": part, "theme": t["theme"], "try": t.get("try"), "marks": said})
    foc = row(conn, "SELECT * FROM focus WHERE coach_note=? ORDER BY started_at DESC LIMIT 1", n["id"])
    return {"at": n["created_at"], "parts": parts,
            "focus": {"text": foc["text"], "tally": F.tally(conn, foc["id"]), "active": foc["ended_at"] is None} if foc else None}


# ---- the prompt -------------------------------------------------------------------------------

def system_prompt(jay: str, owner: dict | None = None, reviewer: str = "Claude") -> str:
    """The coach's instructions, about the lab's owner (octo/owner.py). With the owner's profile, word for word
    what they were before 7 Oct."""
    owner = owner or {}
    P = O.forms(owner)
    they, them, their, themself, s, es = P["they"], P["them"], P["their"], P["themself"], P["s"], P["es"]
    They, Their, hasnt, theyre = P["They"], P["Their"], P["hasnt"], P["theyre"]
    about = (owner.get("about") or "").strip().rstrip(".")
    who = f"{jay}, {about}" if about else jay
    source = (owner.get("principles_from") or "").strip().rstrip(".")
    wrote = f"principles {they} wrote {themself}" + (f" from {source}" if source else "")
    never = (owner.get("never") or "").strip().rstrip(".")
    never = f" Never suggest {never}." if never else ""
    marks = ", ".join(f'"{w}"' for w in (f"new to {them}", f"{they} knew it", f"partly right, {they} said",
                                          f"{they} acted on it", f"{they} let it go"))
    return f"""You coach {who}. {They} record{s} {their} conversations, and each one is reviewed by {reviewer} against {wrote}. {They} read{s} each review and mark{s} what was new to {them}, what wasn't right, and what {they} acted on. You read those reviews together, across conversations, and tell {them} what a good coach would after watching {them} for weeks: the one thing most worth working on now.

{Their} standard: {their} conduct, judged against {their} principles, comes first. Advice about being effective at work counts only when it is consistent with them.{never}

Write to {them} as "you", in plain, direct English. Be specific and honest. Don't flatter, don't hedge everything, and don't pad.

What you're given: {their} principles, in {their} words; what {theyre} practising ({their} focus) and how {they} answered after each conversation since; {their} numbers over time; the last note you gave {them}, with {their} marks on it; and the conversations, newest first, each with its review and, where {they} wrote one, how {they} said it went in {their} own words. Every review item has an id in brackets, like [c2.p7]: conversation 2, principle 7. {Their} marks follow an item after "·" ({marks}). Items {they} marked not right are already left out. A review {they} {hasnt} answered yet hasn't been checked by {them}: give it less weight.

The fields:
- work_on: the one pattern in how {they} show{s} up, seen across conversations, most worth working on now. Not one review restated: something that recurs. Prefer what {they} can change in {their} next conversations, and what matters most against {their} principles. "theme" is one line about what {they} do{es}, in plain words ("You answer before the question is finished"), not a principle's number and not a verdict on {them}. "why" is two to four sentences: what it costs {them} or the people {they} work{s} with, and why now. "evidence" is the ids of the items that show it, from at least two different conversations; only items that really show this pattern. "try" is one small practice that passes every principle: "when" names the moment it applies, recognisable as it happens, starting with "When" ("When someone pushes back on a date"); "instead" is what to do then, concrete enough to do next time, as an instruction ("ask what's driving the date before you answer"). "know" is how {they}'ll know it's working: "measure" is "fillers", "questions", "talk_share" or "talked_over" when one of {their} numbers would move with it, else "focus" (after each conversation {they} answer{s} whether it came up and whether {they} did it); "how" is one sentence on what to look for.
- keep: one thing {they} do{es} well, across at least two conversations, worth keeping: "theme", "why" (one or two sentences), "evidence". If nothing holds across two conversations, "theme" is "" and "evidence" is empty.
- also: up to two other patterns worth {their} attention, each across at least two conversations: "theme" one line, "why" one sentence, "evidence". Empty if there are none.
- changed: what's different since the last note, if there is one: whether its theme still shows up, what {their} focus answers and {their} numbers say, with their caveats. "" if there is no earlier note.
- limits: what you can't tell yet: too few conversations, a number within ordinary variation, readings {they} marked partly right, conversations {they} {hasnt} reviewed, what the reviews say they couldn't see.

Evidence ids are copied exactly as given, like "c2.p7". They are checked in code: an id that doesn't exist is ignored, and a theme without items from two different conversations is dropped. In "why", "changed" and "limits", refer to conversations by day and person, never by id. Use only the principles as {they} wrote them; don't add other teachings.

{MATERIAL}"""


HELD_WORDS = {"held": "held", "partly": "partly held", "missed": "missed", "depends": "can't tell from the transcript"}


def _sentence(text: str) -> str:
    text = text.strip()
    return text + "." if text and text[-1] not in ".!?…\"'" else text


def _item_text(e: dict) -> str:
    head = e["label"] + (f", {HELD_WORDS.get(e['held'], e['held'])}" if e.get("held") else "")
    body = ": ".join(x for x in (e["headline"].strip().rstrip("."), _sentence(e["body"])) if x)
    body = _sentence(body)
    out = f"[{e['id']}] {head}: {body}"
    if e["quotes"]:
        out += " Moment" + ("s" if len(e["quotes"]) > 1 else "") + ": " + "; ".join(
            f"\"{q['text']}\" ({q['who']})" for q in e["quotes"]) + "."
    if e["marks"]:
        out += " · " + ", ".join(e["marks"])
    return out


def user_prompt(m: dict) -> str:
    jay = m["jay"]
    p = m.get("forms") or O.forms({})
    token = _FORMS.set(p)
    try:
        return _user_prompt(m, jay, p)
    finally:
        _FORMS.reset(token)


def _user_prompt(m: dict, jay: str, P: dict) -> str:
    ps = "\n\n".join(f"{p['n']}. {p['text']}" for p in m["principles"]) or "(none written yet)"
    parts = [f"Today is {_when(m['today'])}.", f"{jay}'s principles, in {P['their']} words:\n\n{ps}"]

    fs = m["focus"]
    foc = []
    if fs["active"]:
        foc.append(f"{P['Their']} focus now: " + _focus_text(fs["active"], fs["active"]["tally"], fs["active"].get("numbers")))
    else:
        foc.append(f"{P['They']} {P['have']} no focus now.")
    for f in fs["past"][:3]:
        foc.append("Before that: " + _focus_text(f, f["tally"], None))
    parts.append("\n".join(foc))

    parts.append(f"{P['Their']} numbers over time (approximate: questions come from punctuation, talking over from turn edges):\n"
                 + ("\n".join(m["trend"]) or "- Not enough conversations yet."))

    if m["last"]:
        n = m["last"]
        lines = [f"The last note you gave {P['them']} ({_when(n['at'])}):"]
        names = {"work_on": "work on", "keep": "keep", "also0": "also", "also1": "also"}
        for p in n["parts"]:
            line = f"- {names[p['part']]}: \"{p['theme']}\""
            if p.get("try"):
                line += f" (try: {try_text(p['try'])})"
            line += f" · {P['their']} marks: {', '.join(p['marks'])}" if p["marks"] else " · not marked"
            lines.append(line)
        if n["focus"]:
            t = n["focus"]["tally"]
            lines.append(f"- {P['They']} made it {P['their']} focus{' (still on)' if n['focus']['active'] else ''}: \"{n['focus']['text']}\"; "
                         f"did it {t['did']}, missed {t['missed']}, didn't come up {t['na']}.")
        parts.append("\n".join(lines))
    else:
        parts.append("There's no earlier note: this is the first.")

    convs = m["conversations"]
    head = f"Conversations, newest first ({len(convs)} with a review"
    head += f"; {m['left_out']} item{'s' if m['left_out'] != 1 else ''} {P['they']} marked not right left out):" if m["left_out"] else "):"
    blocks = [head]
    for c in convs:
        who = f"with {', '.join(c['with'])}" if c["with"] else "no one else named"
        state = f"{P['they']} answered this review" if c["answered"] else f"{P['they']} {P['hasnt']} answered this review yet"
        lines = [f"== {c['key']} · {_when(c['at'])} · {c['minutes']} min · {who} · {state} =="]
        if c["numbers"]:
            lines.append(f"{P['Their']} numbers: {c['numbers']}.")
        if c["focus"]:
            said = {"did": f"{P['they']} did it", "missed": f"it came up and {P['they']} missed it", "na": "it didn't come up",
                    None: f"{P['they']} {P['hasnt']} said"}[c["focus"]["answer"]]
            lines.append(f"{P['Their']} focus then, \"{c['focus']['text']}\": {said}.")
        if c.get("reflection"):  # B15: before reading the review, in their words
            lines.append(f"How it went, in {P['their']} words: \"{c['reflection']}\"")
        if c["summary"]:
            lines.append(f"What happened: {c['summary']}")
        lines += [_item_text(e) for e in c["items"]]
        if c["limits"]:
            lines.append(f"What the review couldn't see: {c['limits']}")
        blocks.append("\n".join(lines))
    parts.append("\n\n".join(blocks))
    return "\n\n".join(parts)


# ---- checking the answer ----------------------------------------------------------------------

def check(raw: dict, m: dict) -> dict:
    """Keep each theme only with real items from at least two conversations; resolve its evidence
    to what the page shows (the conversation, the item, its moment)."""
    ids = m["ids"]
    dropped, unknown = [], []

    def themed(t: dict, section: str) -> dict | None:
        theme = " ".join((t.get("theme") or "").split())
        if not theme:
            return None
        keys = []
        for e in t.get("evidence") or []:
            k = str(e).strip().strip("[]").strip()
            if k in ids:
                if k not in keys:
                    keys.append(k)
            elif k and k not in unknown:
                unknown.append(k)
        convs = {ids[k]["session_id"] for k in keys}
        if len(convs) < MIN_CONVERSATIONS:
            dropped.append({"section": section, "theme": theme,
                            "reason": "seen in one conversation only" if convs else "no review item it could point to"})
            return None
        # Newest conversation first, taking one from each in turn, so the first moments shown already
        # span the conversations the theme is seen in; within one, the answer's order.
        rank = {k: (sum(1 for j in keys[:i] if ids[j]["conv"] == ids[k]["conv"]), ids[k]["conv"]) for i, k in enumerate(keys)}
        keys.sort(key=lambda k: rank[k])
        return {"theme": theme, "why": (t.get("why") or "").strip(), "conversations": len(convs),
                "evidence": [{"id": k, **{x: ids[k][x] for x in ("session_id", "insight_run", "item", "at", "kind", "label",
                                                                    "headline", "held", "quote")}} for k in keys]}

    raw_work = raw.get("work_on") or {}
    work = themed(raw_work, "work_on")
    if work:
        t = raw_work.get("try") or {}
        when, instead = (t.get("when") or "").strip().rstrip(",.;:"), (t.get("instead") or "").strip()
        work["try"] = {"when": when, "instead": instead} if when and instead else None
        k = raw_work.get("know") or {}
        work["know"] = {"measure": k.get("measure") if k.get("measure") in KNOW else "focus", "how": (k.get("how") or "").strip()}
    return {"work_on": work, "keep": themed(raw.get("keep") or {}, "keep"),
            "also": [a for a in (themed(x, "also") for x in (raw.get("also") or [])[:2]) if a],
            "changed": (raw.get("changed") or "").strip(), "limits": (raw.get("limits") or "").strip(),
            "dropped": dropped, "unknown_evidence": unknown}


# ---- asking, and the notes --------------------------------------------------------------------

def _stop_stale(conn, now: float) -> None:
    """A note still running long after any call could have finished was stopped (a restart)."""
    conn.execute("UPDATE coach_notes SET status='failed', finished_at=?, error=? WHERE status='running' AND created_at < ?",
                 (now, "it stopped before it finished (the lab may have restarted): ask again", now - STOPPED_AFTER_S))


def why_not(conn, now: float | None = None, n_reviews: int | None = None) -> str | None:
    """Why the coach can't be asked right now, or None."""
    from . import pipeline as P
    now = now or time.time()
    _stop_stale(conn, now)
    if row(conn, "SELECT 1 FROM coach_notes WHERE status='running'"):
        return "the coach is reading your reviews now"
    if (len(reviews(conn)) if n_reviews is None else n_reviews) < MIN_CONVERSATIONS:
        return f"the coach needs reviews of at least {MIN_CONVERSATIONS} conversations"
    if (blocked := P.claude_blocked_until(conn, now)) is not None:
        capped = P.claude_calls_since(conn, P._midnight(now)) >= P.DAILY_CLAUDE_CALLS
        return (f"today's {P.DAILY_CLAUDE_CALLS} Claude calls are used: after midnight" if capped else
                f"Claude's usage limit: after {datetime.fromtimestamp(blocked):%H:%M}")
    from .settings import reader
    who = reader(conn)
    if who is None:  # a new lab whose owner hasn't chosen a model yet (product plan 4.1)
        return "choose a model for reviews first (Pipeline page)"
    if who["kind"] == "claude-code" and not CC.claude_bin():
        return "Claude Code isn't installed (the `claude` command)"
    return None


def start(conn, now: float | None = None) -> str:
    """A new note, waiting to run (run() does the call). Raises CoachError if it can't be asked now."""
    now = now or time.time()
    with tx(conn):
        why = why_not(conn, now)
        if why:
            raise CoachError(why, 409 if why.startswith("the coach is reading") else 422)
        nid = new_id("coach")
        from .settings import reader
        model = (reader(conn) or {}).get("model", MODEL)
        conn.execute("INSERT INTO coach_notes (id, created_at, status, model) VALUES (?, ?, 'running', ?)", (nid, now, model))
    return nid


def run(conn, note_id: str, ask=None, now: float | None = None) -> dict:
    """Read, ask Claude, check, store. Returns the note's row; a failure is stored and raised."""
    from .settings import reader
    who = reader(conn) or {}
    reviewer = "an AI model" if who.get("kind") == "openai" else "Claude"  # what the reviews were made with
    if ask is None:
        from . import providers as PV
        if who and who["kind"] != "claude-code":  # the owner's own model (product plan 4.1)
            def ask(model, effort, system, prompt, schema):
                return PV.ask(who, system, prompt, schema)
        else:
            ask = CC.ask
    try:
        with tx(conn):  # what it read is recorded with it before the call: a delete meanwhile drops the note
            if not row(conn, "SELECT 1 FROM coach_notes WHERE id=? AND status='running'", note_id):
                raise CoachError("no such note waiting to be asked", 404)
            m = material(conn, now)
            if len(m["conversations"]) < MIN_CONVERSATIONS:
                raise CoachError(f"the coach needs reviews of at least {MIN_CONVERSATIONS} conversations")
            conn.executemany("INSERT OR IGNORE INTO coach_sources (note_id, session_id) VALUES (?, ?)",
                             [(note_id, c["session_id"]) for c in m["conversations"]])
            conn.execute("UPDATE coach_notes SET read=? WHERE id=?", (json.dumps(m["read"]), note_id))
        raw, usage = ask(MODEL, EFFORT, system_prompt(m["jay"], m.get("owner"), reviewer), user_prompt(m), SCHEMA)
        out = check(raw, m)
    except Exception as e:
        msg = (f"Claude took longer than {CC.TIMEOUT_S // 60} minutes: ask again" if isinstance(e, subprocess.TimeoutExpired)
               else str(e))
        conn.execute("UPDATE coach_notes SET status='failed', error=?, finished_at=? WHERE id=? AND status='running'",
                     (msg[:2000], time.time(), note_id))
        raise
    conn.execute("UPDATE coach_notes SET status='done', output=?, usage=?, finished_at=? WHERE id=? AND status='running'",
                 (json.dumps(out), json.dumps(usage), time.time(), note_id))
    return row(conn, "SELECT * FROM coach_notes WHERE id=?", note_id)


def drop_for(conn, session_ids: list[str]) -> int:
    """Delete every note that read any of these conversations (a delete, or a Forget)."""
    if not session_ids:
        return 0
    marks = ",".join("?" * len(session_ids))
    return conn.execute(f"DELETE FROM coach_notes WHERE id IN (SELECT note_id FROM coach_sources WHERE session_id IN ({marks}))",
                        session_ids).rowcount


def rate(conn, note_id: str, part: str, verdict: str | None = None, novelty: str | None = None) -> dict:
    """The owner's marks on one part of a note: right / partly / wrong and new / knew ("" clears one)."""
    n = row(conn, "SELECT output FROM coach_notes WHERE id=? AND status='done'", note_id)
    out = load_json(n["output"]) if n else None
    if not out:
        raise CoachError("no such note", 404)
    present = {"work_on": out.get("work_on"), "keep": out.get("keep"),
               **{f"also{i}": a for i, a in enumerate(out.get("also") or [])}}
    if part not in PARTS or not present.get(part):
        raise CoachError("no such part in this note", 404)
    if verdict not in (None, "", *VERDICTS) or novelty not in (None, "", *NOVELTY):
        raise CoachError("not a mark")
    now = time.time()
    conn.execute("INSERT OR IGNORE INTO coach_feedback (note_id, part, updated_at) VALUES (?, ?, ?)", (note_id, part, now))
    if verdict is not None:
        conn.execute("UPDATE coach_feedback SET verdict=?, updated_at=? WHERE note_id=? AND part=?", (verdict or None, now, note_id, part))
    if novelty is not None:
        conn.execute("UPDATE coach_feedback SET novelty=?, updated_at=? WHERE note_id=? AND part=?", (novelty or None, now, note_id, part))
    return feedback(conn, note_id)


def feedback(conn, note_id: str) -> dict[str, dict]:
    return {r["part"]: {"verdict": r["verdict"], "novelty": r["novelty"]}
            for r in rows(conn, "SELECT * FROM coach_feedback WHERE note_id=?", note_id)}


def try_text(t: dict) -> str:
    """'When someone raises a risk, ask what would change their mind.'"""
    instead = t["instead"].strip().rstrip(".")
    return f"{t['when']}, {instead[:1].lower() + instead[1:]}."


def focus_text(work: dict) -> str:
    """The try line as a focus, for him to edit; the theme if there's no try line."""
    return try_text(work["try"]) if work.get("try") else work["theme"]


def make_focus(conn, note_id: str, text: str | None = None, measure: str | None = None) -> dict:
    """Practise what the note says to work on. The focus points at the most recent item in its
    evidence that he can act on, so "Did it" after a conversation marks that item acted on (A3)."""
    n = row(conn, "SELECT output FROM coach_notes WHERE id=? AND status='done'", note_id)
    work = (load_json(n["output"]) or {}).get("work_on") if n else None
    if not work:
        raise CoachError("no such note, or nothing in it to work on", 404)
    live = [e for e in work["evidence"] if row(conn, "SELECT 1 FROM stage_runs WHERE id=?", e["insight_run"])]
    live.sort(key=lambda e: (-(e["at"] or 0), ACTABLE.index(e["kind"]) if e["kind"] in ACTABLE else len(ACTABLE)))
    e = live[0] if live else None
    measure = measure if measure is not None else (work["know"]["measure"] if work["know"]["measure"] in M.MEASURES else None)
    try:
        return F.start(conn, text if text is not None else focus_text(work), e and e["session_id"], e and e["insight_run"],
                       e and e["item"], measure=measure or None, coach_note=note_id)
    except F.FocusError as err:
        raise CoachError(str(err))


def _enrich(conn, ev: dict) -> dict:
    """An evidence item as the page shows it now: with whom, and whether its audio is still kept."""
    from .sessions import speakers
    s = row(conn, "SELECT r.audio_deleted_at FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?",
            ev["session_id"])
    sp = speakers(conn, ev["session_id"])
    return {**ev, "with": [x["name"] for x in sp if not x["self"] and x["name"] and x["state"] in ("confirmed", "auto")],
            "audio": bool(s and not s["audio_deleted_at"])}


def view(conn, n: dict) -> dict:
    """One note as the Coach page shows it."""
    out = load_json(n["output"]) if n["output"] else None
    if out:
        out = {**out, "work_on": out["work_on"] and {**out["work_on"], "evidence": [_enrich(conn, e) for e in out["work_on"]["evidence"]]},
               "keep": out["keep"] and {**out["keep"], "evidence": [_enrich(conn, e) for e in out["keep"]["evidence"]]},
               "also": [{**a, "evidence": [_enrich(conn, e) for e in a["evidence"]]} for a in out["also"]]}
        if out["work_on"]:
            out["work_on"]["focus_text"] = focus_text(out["work_on"])
    foc = row(conn, "SELECT * FROM focus WHERE coach_note=? ORDER BY started_at DESC LIMIT 1", n["id"])
    know = out["work_on"]["know"]["measure"] if out and out["work_on"] else None
    return {"id": n["id"], "created_at": n["created_at"], "finished_at": n["finished_at"], "status": n["status"],
            "error": n["error"], "model": n["model"], "read": load_json(n["read"]), "note": out,
            "feedback": feedback(conn, n["id"]), "seen": n["seen_at"] is not None,
            "focus": {"id": foc["id"], "text": foc["text"], "started_at": foc["started_at"], "active": foc["ended_at"] is None,
                      "tally": F.tally(conn, foc["id"])} if foc else None,
            # How he'll know: that number since this note, against before it.
            "numbers": M.since_before(conn, know, n["created_at"]) if know in M.MEASURES else None}


def summary(conn, now: float | None = None) -> dict:
    """The coach card and page: the latest note, one being asked or failed since, the earlier ones,
    how many reviews are new since, and whether it can be asked now."""
    now = now or time.time()
    _stop_stale(conn, now)
    notes = rows(conn, "SELECT * FROM coach_notes ORDER BY created_at DESC LIMIT 30")
    done = [n for n in notes if n["status"] == "done"]
    latest = done[0] if done else None
    newer = [n for n in notes if not latest or n["created_at"] > latest["created_at"]]
    running = next((n for n in newer if n["status"] == "running"), None)
    failed = next((n for n in newer if n["status"] == "failed"), None) if not running else None
    revs = reviews(conn)
    new_since = sum(1 for c in revs if (c["run"]["finished_at"] or 0) > latest["created_at"]) if latest else len(revs)
    why = why_not(conn, now, len(revs))
    return {"latest": view(conn, latest) if latest else None,
            "running": {"id": running["id"], "created_at": running["created_at"]} if running else None,
            "failed": {"id": failed["id"], "error": failed["error"], "at": failed["finished_at"]} if failed else None,
            "earlier": [{"id": n["id"], "created_at": n["created_at"],
                         "theme": ((load_json(n["output"]) or {}).get("work_on") or {}).get("theme")} for n in done[1:]],
            "new_since": new_since, "conversations": len(revs), "reads": min(len(revs), MAX_CONVERSATIONS),
            "can_ask": why is None, "why_not": why,
            "min_conversations": MIN_CONVERSATIONS}


def seen(conn, note_id: str) -> None:
    conn.execute("UPDATE coach_notes SET seen_at=? WHERE id=? AND status='done' AND seen_at IS NULL", (time.time(), note_id))
