"""The review of a whole conversation, on whichever model reads for this lab (product plan 4.1, 7 Oct).

One review of the whole conversation, the way the owner of the first lab asked for on 2 Oct: how the
others acted across it, what went unsaid or unresolved, moments minutes apart that connect, the numbers,
then the owner's conduct against their principles and small next steps; since 6 Oct (E2 in
docs/coach-plan.md) the one thing, and held / partly / missed / depends on each principle.

- **The call** goes through octo/providers.py: Claude Code on the owner's own plan where the lab may use
  it (the first lab), Claude on the owner's API key, or an OpenAI-compatible service, as the review
  step's choice says (`params["reader"]`, never a key).
- **The system prompt** is about the lab's owner, from their profile (`params["owner"]`): their name,
  how to refer to them, a line on who they are and where their principles come from.
- **The answer is checked in code:** every quote must really be in the line it cites, or it's dropped,
  and an item left without its evidence is dropped too. A reading of another person needs two of their
  own lines from different moments; a conduct finding needs the owner's own words.
- The answer is kept with the run (`memo`), keyed by everything that produced it, so re-running an
  unchanged conversation costs nothing.

Until 19 Oct the first lab ran on octo/claude_review.py, the same review with its owner written into
the prompt, pinned byte for byte while the gate (D8) counted; the gate was retired that day and the
first lab moved here (product plan P8, open-source plan D3).
"""

from __future__ import annotations

import hashlib
import time
from datetime import date
from pathlib import Path

from . import providers as P
from .owner import forms
from .insight import MATERIAL, clock, memo_key, principles, review_rules, rubric_version, transcript, verify_quote

CODE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]

# How the owner did against a principle (6 Oct). "depends": it turns on what the transcript can't show.
HELD = ("held", "partly", "missed", "depends")

QUOTES = {"type": "array", "items": {"type": "object", "properties": {
    "line": {"type": "integer"}, "text": {"type": "string"}}, "required": ["line", "text"]}}

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "reading": {"type": "array", "items": {"type": "object", "properties": {
            "person": {"type": "string"}, "headline": {"type": "string"},
            "observation": {"type": "string"}, "quotes": QUOTES},
            "required": ["person", "headline", "observation", "quotes"]}},
        "between": {"type": "array", "items": {"type": "object", "properties": {
            "headline": {"type": "string"}, "observation": {"type": "string"}, "quotes": QUOTES},
            "required": ["headline", "observation", "quotes"]}},
        "one_thing": {"type": "object", "properties": {
            "headline": {"type": "string"}, "why": {"type": "string"}, "quotes": QUOTES,
            "next": {"type": "integer"}}, "required": ["headline", "why", "quotes", "next"]},
        "conduct": {"type": "array", "items": {"type": "object", "properties": {
            "principle": {"type": "integer"}, "held": {"type": "string", "enum": list(HELD)},
            "observation": {"type": "string"}, "quotes": QUOTES},
            "required": ["principle", "held", "observation", "quotes"]}},
        "no_moment": {"type": "array", "items": {"type": "integer"}},
        "next": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "detail": {"type": "string"}, "why": {"type": "string"},
            "due": {"type": "string"}}, "required": ["title", "detail", "why", "due"]}},
        "limits": {"type": "string"},
    },
    "required": ["summary", "one_thing", "reading", "between", "conduct", "no_moment", "next", "limits"],
}


# B15: whether an intention the owner set for this conversation happened. Only in a review that has one (with_intentions).
HAPPENED = ("yes", "partly", "no", "unclear")


def with_intentions(schema: dict) -> dict:
    return {**schema, "required": [*schema["required"], "intention"], "properties": {**schema["properties"], "intention": {
        "type": "array", "items": {"type": "object", "properties": {
            "person": {"type": "string"}, "happened": {"type": "string", "enum": list(HAPPENED)},
            "observation": {"type": "string"}, "quotes": QUOTES}, "required": ["person", "happened", "observation", "quotes"]}}}}


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def system_prompt(owner: dict, rules: str, intentions: bool = False) -> str:
    name = owner.get("name") or "the owner"
    p = forms(owner)
    they, them, their, themself, s, were = p["they"], p["them"], p["their"], p["themself"], p["s"], p["were"]
    about = (owner.get("about") or "").strip().rstrip(".")
    source = (owner.get("principles_from") or "").strip().rstrip(".")
    who = f"{name}, {about}" if about else name
    wrote = f"principles {they} wrote {themself}" + (f" from {source}" if source else "")
    never = (owner.get("never") or "").strip().rstrip(".")
    never = f": never {never}" if never else ""
    how = f"How {they} want{s} the review done, in {their} words:\n{rules}\n\n" if rules.strip() else ""
    # B15: only when there's an intention, so every other review's prompt is what it was.
    intent = (f"\n- intention: for each intention {they} set before this conversation, whether it happened. \"person\" is the name "
              f"as given; \"happened\" is \"yes\", \"partly\", \"no\" or \"unclear\" (it turns on what the transcript can't show); "
              f"\"observation\" is one or two sentences, judged against {their} principles like everything else, never only by whether "
              f"{they} got what {they} wanted; \"quotes\" is the moment it rests on, with {their} own words when it happened."
              if intentions else "")
    return f"""You review a recorded conversation for {who}. {_cap(they)} use{s} these reviews to get better at how {they} show{s} up with people. {_cap(they)} measure{s} {their} conduct against {wrote}; advice counts only when it is consistent with them.

{how}Write to {them} as "you", in plain, direct English. Be specific and honest: say what {they} did well and what {they} missed. Don't flatter, don't hedge everything, and don't pad.

The fields:
- summary: what happened between {them} and the others, in 2 to 4 sentences.
- one_thing: the single thing from this conversation most worth {their} attention: what a perceptive colleague would tell {them} first. Usually about how {they} showed up or what {they} could do next; never a verdict on someone else. "headline" is one line; "why" is one or two sentences; "quotes" is the moment it rests on, with {their} own words when it's about what {they} did. "next" is the number, counting from 0, of the step in "next" that acts on it, or -1 if none does. Like the steps, it must pass every principle.
- reading: the people whose behaviour across the conversation helps {them} understand those people and respond well, often one or two, sometimes none. "person" is the name exactly as the transcript shows it. "headline" is one line; "observation" is 2 to 5 sentences on what they did across the conversation, what they seemed to care about, and how their words to {them} were likely meant. At least two quotes of that person's own lines, from different moments. A description, never a verdict on their character.
- between: what was left unsaid or unresolved, what was agreed on the surface but maybe not underneath, and moments minutes apart that connect (something asked earlier that {their} later answer missed). "headline" is one line; "observation" says what it means for {them}. Quotes from anyone's lines.
- conduct: only the principles with a real moment in this conversation. "principle" is its number. Say plainly whether {they} held to it, partly, or missed it, judged against the principle's own words, and give the same judgement in "held": "held", "partly" or "missed". If it applies only depending on something the transcript can't show (what {they} {were} thinking), say so, and "held" is "depends". The quotes must include {their} own words.
- no_moment: the numbers of the principles with no moment.
- next: one to four small, concrete things to do, checkable by the next time {they} talk{s} to that person. Each must pass every principle{never}. They go straight into {their} task list, so: "title" is the task itself, an imperative of at most eight words that makes sense on its own in a to-do app; "detail" is what exactly to do, one to three sentences; "why" is one sentence; "due" is the date it should be done by as YYYY-MM-DD, worked out from the conversation's date and anything said in it (a weekly meeting, a deadline), or "" if there's no natural date.
- limits: what the transcript couldn't show (tone is inferred from words, not heard), and any speaker labels that look wrong.{intent}

Quotes: copy the words exactly from the line you cite, with its line number; "…" may join fragments of one line, in order. Quotes are checked against the transcript in code; one that doesn't match is removed, and an item left without its evidence is dropped. Lines marked "(speaker or words unsure)" may be misattributed or misheard: prefer other lines. Use the numbers where they say something, with their caveats. Use only the principles as {they} wrote them; don't add other teachings.

{MATERIAL}"""


def numbers_table(nums: dict, names: dict[str, str]) -> str:
    rows = ["speaker | share of talk | turns | longest turn (s) | words | questions | interrupted others | was interrupted | fillers per 100 words"]
    for letter, n in nums["speakers"].items():
        rows.append(f"{names.get(letter, 'Speaker ' + letter)} | {n['share']:.0%} | {n['turns']} | {n['longest_turn_s']:.0f} | "
                    f"{n['words']} | {n['questions']} | {n['interrupted_others']} | {n['was_interrupted']} | "
                    f"{n['fillers_per_100_words']}")
    return "\n".join(rows) + ("\n\nApproximate: questions come from punctuation; interruptions from turn edges (±0.3 s); "
                              "fillers (um, uh, ah, you know, kind of, sort of) only where the transcription kept them.")


def when_text(started_at: float | None) -> str:
    """'Thursday 1 Oct 2026, 20:31' in the Mac's time zone, or ''."""
    if not started_at:
        return ""
    t = time.localtime(started_at)
    return time.strftime("%A ", t) + str(t.tm_mday) + time.strftime(" %b %Y, %H:%M", t)


def marks_text(name: str, owner: dict, marks: list[int], started_at: float | None) -> str:
    """B16: the moments the owner marked as they happened, in the transcript's times (and the clock's, when known)."""
    p = forms(owner)
    at = [clock(s) + (time.strftime(" (%H:%M by the clock)", time.localtime(started_at + s)) if started_at else "") for s in marks]
    return (f"{name} marked {'this moment' if len(at) == 1 else 'these moments'} as {p['they']} talked, by tapping just after "
            f"something mattered to {p['them']}: at about {', '.join(at)} in the transcript's times. What mattered is in the "
            f"minute before each mark: read that minute closely, and let the review say what happened there.\n\n")


def user_prompt(owner: dict, name: str, items: list[dict], nums: str, text: str, when: str = "",
                intentions: list[dict] | None = None, marks: str = "") -> str:
    p = forms(owner)
    ps = "\n\n".join(f"{i['n']}. {i['text']}" for i in items)
    dated = f"This conversation was on {when}.\n\n" if when else ""
    intent = (f"Before this conversation, {name} wrote what {p['they']} wanted to do next time with these people, in {p['their']} words:\n"
              + "\n".join(f"- Next time with {i['person']}, I want to: {i['text']}" for i in intentions) + "\n\n") if intentions else ""
    return f"""{dated}{name}'s principles, in {p['their']} words:

{ps}

{intent}{marks}Numbers for this conversation:
{nums}

Transcript, one line per utterance ([line] time speaker: words):
{text}"""


def _quotes(raw: list[dict], by_id: dict[int, dict]) -> tuple[list[dict], int]:
    got, seen = [], set()
    for q in raw or []:
        v = verify_quote(q, by_id)
        if v and (v["line"], v["text"]) not in seen:
            seen.add((v["line"], v["text"]))
            got.append(v)
    return got, len(raw or []) - len(got)


def _flags(quotes: list[dict]) -> list[str]:
    return [f"speaker or words unsure at {clock(q['start'])}" for q in quotes if q["uncertain"]]


def check(raw: dict, items: list[dict], by_id: dict[int, dict], names: dict[str, str], jay_letters: set[str]) -> dict:
    out = {"summary": (raw.get("summary") or "").strip(), "one_thing": None, "reading": [], "between": [],
           "conduct": [], "no_moment": [], "dropped": [], "next": [], "limits": (raw.get("limits") or "").strip()}
    for r in raw.get("reading", []):
        letters = {k for k, v in names.items() if v.strip().lower() == r.get("person", "").strip().lower()} - jay_letters
        quotes, missing = _quotes(r.get("quotes"), by_id)
        theirs = [q for q in quotes if q["speaker"] in letters]
        if len({q["line"] for q in theirs}) < 2:
            out["dropped"].append({"section": "reading", "headline": r.get("headline", ""),
                                   "reason": f"fewer than two verified lines of {r.get('person') or 'that person'}'s own"})
            continue
        out["reading"].append({"person": r["person"].strip(), "headline": r.get("headline", "").strip(),
                               "observation": r.get("observation", "").strip(), "quotes": quotes,
                               "unverified_quotes": missing, "flags": _flags(quotes)})
    for b in raw.get("between", []):
        quotes, missing = _quotes(b.get("quotes"), by_id)
        if not quotes:
            out["dropped"].append({"section": "between", "headline": b.get("headline", ""), "reason": "no verified quote"})
            continue
        out["between"].append({"headline": b.get("headline", "").strip(), "observation": b.get("observation", "").strip(),
                               "quotes": quotes, "unverified_quotes": missing, "flags": _flags(quotes)})
    by_n = {p["n"]: p for p in items}
    for c in raw.get("conduct", []):
        p = by_n.get(c.get("principle"))
        if not p or any(f["principle"] == p["n"] for f in out["conduct"]):
            continue
        quotes, missing = _quotes(c.get("quotes"), by_id)
        if not any(q["speaker"] in jay_letters for q in quotes):
            out["dropped"].append({"section": "conduct", "principle": p["n"], "reason": "no verified quote of your own words"})
            continue
        out["conduct"].append({"principle": p["n"], "principle_id": p["id"], "title": p["title"],
                               "held": c.get("held") if c.get("held") in HELD else None,
                               "observation": c.get("observation", "").strip(), "quotes": quotes,
                               "unverified_quotes": missing, "flags": _flags(quotes), "try_next_time": None})
    found = {f["principle"] for f in out["conduct"]}
    out["no_moment"] = [p["n"] for p in items if p["n"] not in found]
    out["next"] = []
    kept: dict[int, int] = {}  # the answer's step numbers → the steps kept here
    for i, n in enumerate(raw.get("next", [])):
        title = (n.get("title") or n.get("action") or "").strip()
        if not title:
            continue
        kept[i] = len(out["next"])
        due = (n.get("due") or "").strip()
        try:
            due = date.fromisoformat(due).isoformat() if due else None
        except ValueError:
            due = None
        out["next"].append({"title": title, "detail": (n.get("detail") or "").strip(),
                            "why": (n.get("why") or "").strip(), "due": due})
    # The one thing (6 Oct): kept with at least one verified quote, like everything else; "next"
    # points at its step as numbered here (a step without a title was dropped above), or None.
    one = raw.get("one_thing") or {}
    headline = (one.get("headline") or "").strip()
    if headline:
        quotes, missing = _quotes(one.get("quotes"), by_id)
        if quotes:
            step = one.get("next")
            out["one_thing"] = {"headline": headline, "why": (one.get("why") or "").strip(), "quotes": quotes,
                                "unverified_quotes": missing, "flags": _flags(quotes),
                                "next": kept.get(step) if isinstance(step, int) else None}
        else:
            out["dropped"].append({"section": "one_thing", "headline": headline, "reason": "no verified quote"})
    return out


def check_intentions(raw: dict, intentions: list[dict], by_id: dict[int, dict], jay_letters: set[str]) -> tuple[list[dict], list[dict]]:
    """Each intention as set, with what the review said of it. "Yes" or "partly" needs the owner's own verified words;
    without them the verdict is dropped and the intention shows unjudged."""
    said = {(r.get("person") or "").strip().lower(): r for r in raw.get("intention") or []}
    out, dropped = [], []
    for it in intentions:
        r = said.get(it["person"].strip().lower()) or {}
        quotes, missing = _quotes(r.get("quotes"), by_id)
        happened = r.get("happened") if r.get("happened") in HAPPENED else None
        if happened in ("yes", "partly") and not any(q["speaker"] in jay_letters for q in quotes):
            dropped.append({"section": "intention", "headline": it["text"], "reason": "no verified quote of your own words"})
            happened, r, quotes = None, {}, []
        out.append({"person": it["person"], "text": it["text"], "happened": happened, "observation": (r.get("observation") or "").strip(),
                    "quotes": quotes, "unverified_quotes": missing, "flags": _flags(quotes)})
    return out, dropped


def run(diarize: dict, names: dict[str, str], self_letters: set[str], params: dict, rubric_text: str,
        on_progress=lambda msg: None, memo: dict | None = None, ask=P.ask) -> dict:
    from .stages import count
    reader = params["reader"]
    owner = params.get("owner") or {}
    items = principles(rubric_text)
    memo = memo or {}
    out = {"kind": "review", "model": reader["model"], "engine": reader["kind"], "effort": reader.get("effort"),
           "rubric": rubric_version(rubric_text), "skipped": None, "summary": "", "one_thing": None, "reading": [],
           "between": [], "conduct": [], "no_moment": [], "dropped": [], "beneath": [], "next": [], "limits": "",
           "seconds": 0.0, "reused": 0, "usage": None, "memo": {}}
    own_talk = sum(t["end"] - t["start"] for t in diarize["turns"] if t["speaker"] in self_letters)
    if not items:
        out["skipped"] = "no principles written yet"
        return out
    if own_talk < params["min_jay_speech_s"]:
        out["skipped"] = f"too short: you spoke for {own_talk:.0f} s (insights need {params['min_jay_speech_s']:.0f} s)"
        return out
    lines = diarize["lines"]
    by_id = {ln["id"]: ln for ln in lines}
    name = owner.get("name") or next((names[k] for k in self_letters if k in names), "the owner")
    owner = {**owner, "name": name}
    nums = numbers_table(count(diarize, {"min_overlap_s": 0.3}), names)
    intentions = params.get("intentions") or []  # B15
    system = system_prompt(owner, review_rules(rubric_text), bool(intentions))
    marks = marks_text(name, owner, params["marks"], params.get("started_at")) if params.get("marks") else ""  # B16: "" without
    prompt = user_prompt(owner, name, items, nums, transcript(lines, names), when_text(params.get("started_at")), intentions, marks)
    key = memo_key("review_any", CODE, {k: v for k, v in reader.items() if k != "api_key"}, system, prompt)
    t0 = time.time()
    if key in memo:
        raw, usage = memo[key]["raw"], memo[key]["usage"]
        out["reused"] = 1
    else:
        on_progress(f"reading the whole conversation ({reader['model']})")
        raw, usage = ask(reader, system, prompt, with_intentions(SCHEMA) if intentions else SCHEMA)
    out["memo"][key] = {"raw": raw, "usage": usage}
    out.update(check(raw, items, by_id, names, self_letters))
    if intentions:
        out["intention"], dropped = check_intentions(raw, intentions, by_id, self_letters)
        out["dropped"] += dropped
    out["usage"] = usage
    if usage and usage.get("model"):
        out["model"] = usage["model"]  # what answered (a refusal fallback can hand it to another model)
    out["seconds"] = round(time.time() - t0, 1)
    return out
