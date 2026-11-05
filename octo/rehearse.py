"""Rehearse a hard conversation, typed (B26, docs/backlog.md; 21 Oct: typed now, spoken later).

Before a difficult conversation the owner plays it through in text, with the model acting the other person: someone
from People, or someone they describe ("a report who's upset about a rating"). They say what's hard about it and what
they want from it (the person's open B15 intention fills that in, if there is one). When they stop, a review like a
conversation's, against their principles, and what to try in the real one.

- **The person is a guess.** For someone from People the model is given only what the reviews read in them, as their
  page shows it (octo/person.py: never a reading the owner marked not right), and told to make nothing about them up
  as fact. The page says it's the model's version of them. Someone described gets only the owner's description.
- **What leaves the Mac:** those readings (headline and observation, and the owner's note on one marked partly
  right), the owner's words (who, what's hard, what they want, their lines) and the exchange, to the reviews' own
  model (settings.reader, as the coach). Nothing else of the lab: no transcript, quote, number or audio.
- **Calls:** one per reply, and one for the review, each counted in the day's (a row in model_calls,
  pipeline._calls_since); a usage-limit failure is kept (error, error_at) so Claude's steps wait for its reset, as the coach's.
  Replies at medium effort on Claude Code, to keep the wait short (not measured); the review at the reader's.
- **Kept apart, like drills (K4):** its own table, never a session, so the list, search, the coach and Progress
  never see it. Deleted by the owner, or when its person is forgotten (voice.forget; merged with them on a rename).
  It's in the database, so it's exported with the lab.
- **The review's rules hold:** the owner's principles as they wrote them (octo/rubric.py), advice only where it is
  consistent with them, never how to win the room; quotes checked in code (review_any.check) against the typed lines.
"""

from __future__ import annotations

import json
import time

from . import owner as O
from . import review_any as RA
from .db import calling, load_json, new_id, row, rows, tx
from .insight import MATERIAL, principles, review_rules

MODEL, EFFORT, TURN_EFFORT = "claude-opus-5-5", "high", "medium"
MAX_LINE = 2000      # characters of one of the owner's lines
MAX_SETUP = 300      # of who, what's hard, what they want: a line each, like an intention
MAX_TURNS = 30       # of the owner's, in one rehearsal: each is a call
READINGS = 12        # the newest of the person's readings the model is given


class RehearsalError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status  # 404 no such rehearsal · 409 already reviewed · 422 can't now · 502 the call failed


def _clean(text: str | None, most: int, what: str) -> str:
    text = " ".join((text or "").split()) if most == MAX_SETUP else (text or "").strip()
    if not text:
        raise RehearsalError(f"{what}, in a few words")
    if len(text) > most:
        raise RehearsalError(f"{what}: at most {most} characters")
    return text


def _ask(who: dict, effort: str, system: str, prompt: str, schema: dict) -> tuple[dict, dict]:
    """One call to the reviews' model. Tests and the demo lab replace this."""
    if who["kind"] == "claude-code":
        from . import claude_code as CC
        return CC.ask(who.get("model") or MODEL, effort, system, prompt, schema)
    from . import providers as PV
    return PV.ask(who, system, prompt, schema)


# ---- who's who ------------------------------------------------------------------------------------

def _owner(conn) -> tuple[str, dict]:
    profile = O.load(conn)
    me = row(conn, "SELECT name FROM people WHERE is_self=1 LIMIT 1")
    return (me["name"] if me else None) or profile.get("name") or "the owner", profile


def readings(conn, person_id: str | None) -> list[dict]:
    """What the model is told about them: the newest of their page's readings (none for someone described)."""
    from .person import readings_of
    return readings_of(conn, person_id)[:READINGS] if person_id else []


def _rehearsal(conn, rid: str) -> dict | None:
    r = row(conn, "SELECT r.*, p.name FROM rehearsals r LEFT JOIN people p ON p.id = r.person_id WHERE r.id=?", rid)
    if r:
        r["turns"] = load_json(r["turns"]) or []
    return r


def _them(r: dict) -> str:
    return r["name"] or "the other person"


# ---- the other person's lines ---------------------------------------------------------------------

REPLY_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}


def role_prompt(owner: str, profile: dict, r: dict, read: list[dict]) -> str:
    p = O.forms(profile)
    they, them, their, s = p["they"], p["them"], p["their"], p["s"]
    if r["person_id"]:
        name = r["name"]
        known = "\n".join(f"- {x['headline'].strip().rstrip('.')}: {x['observation'].strip()}"
                          + (f" ({owner} said this is only partly right" + (f": \"{x['note']}\")" if x.get("note") else ")")
                             if x.get("partly") else "") for x in read) or "- (none yet: the reviews haven't read anything in them)"
        about = f"""You don't know {name}. All you have is what reviews of {owner}'s past conversations with {name} read in {name}, newest first. Each is one reviewer's reading of one conversation, not a fact:
{known}

Play the version of {name} these suggest, and nothing more. Make up no facts about {name}'s life, work, history or views beyond them; when the conversation needs one you weren't given, answer the way someone would without stating it, or ask."""
    else:
        name = "the other person"
        about = f"""The other person is someone {owner} described: "{r['who']}". You know nothing else about them. Play a believable person like that, and make up no facts that would be about a real, particular person."""
    return f"""You play {name} in a rehearsal. {owner} is practising, in text, a conversation with {name} that {they} find{s} hard, before having it for real. {owner} types what {they}'d say; you answer as {name} might.

{about}

What {owner} said is hard about it: "{r['hard']}"
What {they} want{s} from it: "{r['want']}"

Stay in the role. Write only what {name} says next, as {name} would say it in the room: usually one to four sentences of plain spoken English, no stage directions, no narration, and no advice to {owner}. Be realistic, not easy: where {name} would push back, push back, and don't come round faster than {name} would. If {owner} steps out of the rehearsal, answer briefly in the role. Never claim to be {name}: you're a model's guess at {them}, and {their} words are the rehearsal's only facts.

{MATERIAL}"""


def exchange(owner: str, r: dict) -> str:
    """The rehearsal so far, one numbered line per turn (the numbers are what the review's quotes cite)."""
    names = {"me": owner, "them": _them(r)[:1].upper() + _them(r)[1:]}
    return "\n".join(f"[{i}] {names[t['by']]}: {t['text']}" for i, t in enumerate(r["turns"]))


def _reader_of(who: dict) -> dict:
    """What a call is counted by (pipeline._calls_since): never a key."""
    return {k: who[k] for k in ("kind", "connection", "model") if who.get(k)}


def why_not(conn, now: float | None = None) -> str | None:
    """Why the model can't be asked right now, or None (the coach's reasons)."""
    from . import claude_code as CC
    from . import runs as RN
    from .settings import reader
    now = now or time.time()
    who = reader(conn)
    if who is None:
        return "choose a model for reviews first (Settings → Models and keys)"
    if who["kind"] == "claude-code":
        if not CC.claude_bin():
            return "Claude Code isn't installed (the `claude` command)"
        if why := RN.claude_waits(conn, now):
            return why
    elif who.get("connection") and RN.budget_blocked_until(conn, who["connection"], now):
        return "today's calls on this model's budget are used: after midnight"
    return None


def _call(conn, rid: str, effort: str | None, system: str, prompt: str, schema: dict) -> tuple[dict, dict, dict]:
    """`effort` None: the reader's own."""
    from .settings import reader
    if why := why_not(conn):
        raise RehearsalError(why)
    who = reader(conn)
    try:
        with calling(conn, "rehearse", rid):
            raw, usage = _ask(who, effort or who.get("effort") or EFFORT, system, prompt, schema)
    except Exception as e:  # noqa: BLE001 - kept, so a usage limit holds Claude's steps; shown on the page
        msg = str(e)[:2000] or type(e).__name__
        conn.execute("UPDATE rehearsals SET error=?, error_at=?, updated_at=? WHERE id=?", (msg, time.time(), time.time(), rid))
        raise RehearsalError(msg, 502) from e
    return raw, usage or {}, _reader_of(who)


def start(conn, person_id: str | None, who: str | None, hard: str, want: str) -> str:
    """A new rehearsal with someone from People or someone described. No call yet: the owner speaks first."""
    hard, want = _clean(hard, MAX_SETUP, "what's hard about it"), _clean(want, MAX_SETUP, "what you want from it")
    if person_id:
        if not row(conn, "SELECT 1 FROM people WHERE id=? AND is_self=0", person_id):
            raise RehearsalError("no such person", 404)
        who = None
    else:
        who = _clean(who, MAX_SETUP, "who they are")
    rid, now = new_id("reh"), time.time()
    conn.execute("INSERT INTO rehearsals (id, person_id, who, hard, want, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (rid, person_id, who, hard, want, now, now))
    return rid


def say(conn, rid: str, text: str | None) -> dict:
    """The owner's line (or none: ask again after a failed reply), then the other person's. Returns the view."""
    r = _rehearsal(conn, rid)
    if not r:
        raise RehearsalError("no such rehearsal", 404)
    if r["review"]:
        raise RehearsalError("this rehearsal is over: start another", 409)
    if text and text.strip():
        if sum(1 for t in r["turns"] if t["by"] == "me") >= MAX_TURNS:
            raise RehearsalError(f"that's {MAX_TURNS} lines: stop here for the review")
        if why := why_not(conn):
            raise RehearsalError(why)
        r["turns"].append({"by": "me", "text": _clean(text, MAX_LINE, "what you'd say"), "at": time.time()})
        conn.execute("UPDATE rehearsals SET turns=?, updated_at=? WHERE id=?", (json.dumps(r["turns"]), time.time(), rid))
    if not r["turns"] or r["turns"][-1]["by"] != "me":
        raise RehearsalError("what you'd say, in a few words")
    owner, profile = _owner(conn)
    raw, usage, reader = _call(conn, rid, TURN_EFFORT, role_prompt(owner, profile, r, readings(conn, r["person_id"])),
                               f"The conversation so far:\n{exchange(owner, r)}", REPLY_SCHEMA)
    reply = " ".join((raw.get("reply") or "").split()) or "…"
    with tx(conn):  # deleted, or its person forgotten, while it was asked: nothing to add to
        cur = _rehearsal(conn, rid)
        if cur:
            cur["turns"].append({"by": "them", "text": reply, "at": time.time(), "reader": reader, "usage": usage})
            conn.execute("UPDATE rehearsals SET turns=?, error=NULL, error_at=NULL, updated_at=? WHERE id=?",
                         (json.dumps(cur["turns"]), time.time(), rid))
    return view(conn, rid) or {}


# ---- the review ------------------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": RA.SCHEMA["properties"]["summary"],
        "one_thing": RA.SCHEMA["properties"]["one_thing"],
        "conduct": RA.SCHEMA["properties"]["conduct"],
        "no_moment": RA.SCHEMA["properties"]["no_moment"],
        "next": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "detail": {"type": "string"}, "why": {"type": "string"}},
            "required": ["title", "detail", "why"]}},
        "limits": RA.SCHEMA["properties"]["limits"],
    },
    "required": ["summary", "one_thing", "conduct", "no_moment", "next", "limits"],
}


def review_prompt(owner: str, profile: dict, r: dict, rules: str) -> str:
    p = O.forms(profile)
    they, them, their, themself, s = p["they"], p["them"], p["their"], p["themself"], p["s"]
    about = (profile.get("about") or "").strip().rstrip(".")
    source = (profile.get("principles_from") or "").strip().rstrip(".")
    wrote = f"principles {they} wrote {themself}" + (f" from {source}" if source else "")
    never = (profile.get("never") or "").strip().rstrip(".")
    never = f", never {never}," if never else ""
    how = f"How {they} want{s} reviews done, in {their} words:\n{rules}\n\n" if rules.strip() else ""
    other = (f"{r['name']}, played from what reviews of past conversations read in {r['name']}: a guess at {r['name']}, not {r['name']}"
             if r["person_id"] else f"someone {they} described: \"{r['who']}\"")
    return f"""You review a rehearsal for {owner}{', ' + about if about else ''}. Before a conversation {they} find{s} hard, {they} played it through in text with an AI model acting the other person ({other}). {p['They']} measure{s} {their} conduct against {wrote}; advice counts only when it is consistent with them.

{how}The other person's lines were written by the model. Never read anything into them about the real person, and never judge them: judge only {owner}'s own lines, and what {they} could carry into the real conversation. What's hard about it, in {their} words: "{r['hard']}". What {they} want{s} from it: "{r['want']}".

Write to {them} as "you", in plain, direct English. Be specific and honest: say what {they} did well and what {they} missed. Don't flatter, don't hedge everything, and don't pad.

The fields:
- summary: how the rehearsal went, and whether it got where {they} wanted, in 2 or 3 sentences.
- one_thing: the single thing from this rehearsal most worth carrying into the real conversation. "headline" is one line; "why" is one or two sentences; "quotes" is the moment it rests on, with {their} own words. "next" is the number, counting from 0, of the step in "next" that acts on it, or -1 if none does.
- conduct: only the principles with a real moment in {their} lines. "principle" is its number. Say plainly whether {they} held to it, partly, or missed it, judged against the principle's own words, and give the same judgement in "held": "held", "partly" or "missed"; "depends" if it turns on what text can't show. The quotes must include {their} own words.
- no_moment: the numbers of the principles with no moment.
- next: one to three things to try in the real conversation. Each must pass every principle{never} and never be about winning the room, positioning {themself} or taking credit. "title" is an imperative of at most eight words; "detail" is what exactly to do, one or two sentences; "why" is one sentence.
- limits: what a typed rehearsal with a model can't show (tone, timing, how the real person will answer), in a sentence or two.

Quotes: copy the words exactly from the line you cite, with its line number; "…" may join fragments of one line, in order. Quotes are checked in code; one that doesn't match is removed, and an item left without its evidence is dropped. Use only the principles as {they} wrote them; don't add other teachings.

{MATERIAL}"""


def stop(conn, rid: str) -> dict:
    """The review of the rehearsal: one call, its quotes checked against the lines typed. Returns the view."""
    from .rubric import text as rubric_text
    r = _rehearsal(conn, rid)
    if not r:
        raise RehearsalError("no such rehearsal", 404)
    if r["review"]:
        raise RehearsalError("this rehearsal has its review", 409)
    if not any(t["by"] == "them" for t in r["turns"]):
        raise RehearsalError("say something first: the review needs an exchange")
    rubric = rubric_text()
    items = principles(rubric)
    owner, profile = _owner(conn)
    if not items:
        out = {"skipped": "no principles written yet (Set up Octo)"}
    else:
        lines = [{"id": i, "speaker": t["by"], "start": 0.0, "text": t["text"]} for i, t in enumerate(r["turns"])]
        ps = "\n\n".join(f"{x['n']}. {x['text']}" for x in items)
        prompt = f"{owner}'s principles, in {O.forms(profile)['their']} words:\n\n{ps}\n\nThe rehearsal ([line] who: words):\n{exchange(owner, r)}"
        raw, usage, reader = _call(conn, rid, None, review_prompt(owner, profile, r, review_rules(rubric)), prompt, SCHEMA)
        out = RA.check(raw, items, {ln["id"]: ln for ln in lines}, {"me": owner, "them": _them(r)}, {"me"})
        out.update(reader=reader, usage=usage)
    conn.execute("UPDATE rehearsals SET review=?, review_at=?, error=NULL, error_at=NULL, updated_at=? WHERE id=?",
                 (json.dumps(out), time.time(), time.time(), rid))
    return view(conn, rid) or {}


# ---- what the pages show, the count, deleting ---------------------------------------------------------

def _public(r: dict) -> dict:
    return {"id": r["id"], "person_id": r["person_id"], "name": r["name"], "who": r["who"], "created_at": r["created_at"],
            "lines": sum(1 for t in r["turns"] if t["by"] == "me"), "reviewed": bool(r["review"])}


def view(conn, rid: str) -> dict | None:
    r = _rehearsal(conn, rid)
    if not r:
        return None
    from .runs import limit_reset
    review = load_json(r["review"])
    if review:
        review = {k: v for k, v in review.items() if k not in ("reader", "usage")}
    waiting = limit_reset(r["error"], r["error_at"])
    return _public(r) | {
        "hard": r["hard"], "want": r["want"], "turns": [{"by": t["by"], "text": t["text"], "at": t["at"]} for t in r["turns"]],
        "review": review, "error": r["error"], "waiting_until": waiting, "max_turns": MAX_TURNS,
        "readings": [{k: x.get(k) for k in ("session_id", "at", "headline", "observation", "partly", "note")}
                     for x in readings(conn, r["person_id"])], "why_not": why_not(conn)}


def summary(conn) -> dict:
    """The rehearsals, newest first, and who can be chosen (with their open intention, to start from)."""
    people = rows(conn, "SELECT p.id, p.name, i.text AS intention FROM people p LEFT JOIN intentions i ON i.person_id = p.id "
                        "AND i.session_id IS NULL WHERE p.is_self=0 AND p.name IS NOT NULL ORDER BY p.name COLLATE NOCASE")
    got = [r for x in rows(conn, "SELECT id FROM rehearsals ORDER BY created_at DESC") if (r := _rehearsal(conn, x["id"]))]
    return {"rehearsals": [_public(r) for r in got], "people": people, "why_not": why_not(conn)}


def delete(conn, rid: str) -> bool:
    return conn.execute("DELETE FROM rehearsals WHERE id=?", (rid,)).rowcount > 0  # secure_delete: gone from the file
