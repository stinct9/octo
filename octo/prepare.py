"""Prepare for a conversation (B36, docs/backlog.md; Jay, 30 Oct): before talking to someone, a plan made from
everything the lab holds about them, including what others said about them where they weren't there.

Done by hand first for a coffee with someone Jay had met on one group call: she said 71 words in it, and 8 other
conversations mentioned her by name. Most of what read her came from those, so the mentions are the feature.

- **Picked on the Mac, no model** (`pick`): the conversations they were confirmed in, and the lines that **name them**
  in conversations they weren't in (whole name, first name, other spellings the owner adds), ±CONTEXT lines, read as
  search reads them (corrections, left out, set aside and deleted conversations absent). Mentions first, up to half the
  room; Ask's caps (ask.MAX_LINES, ask.MAX_CHARS).
- **The owner sees the mentions before anything is sent**, grouped by conversation, each ticked; unticked ones (another
  person with the same first name) aren't sent. The page says when someone else in People shares the first name.
- **One call** to the review's model (settings.reader), in the day's DAILY_CLAUDE_CALLS (pipeline._calls_since); a
  usage-limit failure holds Claude's steps, as Ask's. In a thread the server starts; the row says it's running.
- **Held to the owner's principles first:** ask to understand, never how to position or win the room. Every moment
  checked in code (ask.verify) or dropped; a reading resting only on mentions is marked second-hand, in code. What
  looks said in confidence comes back under "Be careful", never as something to raise (the model's guess, from the words).
- **What leaves the Mac** (Jay, 30 Oct): their conversations' lines and the lines naming them in others', other
  people's words about someone who wasn't there, plus the reviews' readings of them and the owner's principles.
- **Kept until the next conversation with them:** one per person, replaced once the next is made (the last stays while
  a new one runs or fails); "ready" until the conversation it was for (the first with them after it was made, or the
  calendar event it was made for) is READY_FOR_S under way, then shown beside that conversation's review ("What you
  planned") and deleted KEEP_AFTER_S after it began. It keeps ids and counts of what it read, never an unticked line's words. Deleted with any conversation it read (`drop_for`) and when
  the person is forgotten (voice.forget). In the database, so exported.
- *Save as my intention* writes its one-line aim to their B15 intention (the page does, through the intention route).
"""

from __future__ import annotations

import json
import re
import threading
import time

from . import ask as AK
from . import insight as IN
from . import owner as O
from . import sessions as SV
from .db import load_json, new_id, row, rows, tx
from .rehearse import _owner, _reader_of
from .rehearse import why_not as model_why_not
from .search import readable

MAX_ABOUT = 300          # characters of the owner's note on what the conversation is
MAX_NAMES = 5            # other spellings
CONTEXT = AK.CONTEXT     # lines either side of a line naming them
MENTIONS_SHARE = 0.5     # of the lines and characters, for mentions first; their own conversations fill the rest
READY_FOR_S = 3 * 3600   # a plan stays "ready" (banner, agenda, their page) this long into its conversation or meeting
KEEP_AFTER_S = 7 * 86400 # then kept this long, for "What you planned" beside that conversation's review, and deleted
STOPPED_AFTER_S = 1500   # a plan still "running" after this was stopped (a restart)
READINGS = 12            # the newest of the reviews' readings of them, as Rehearse sends
SHOWN = 3                # lines naming them shown per conversation on the page, before Prepare


class PrepareError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status  # 404 no such person · 409 one is being made · 422 can't now


# ---- picking, on the Mac --------------------------------------------------------------------------

def _person(conn, person_id: str) -> dict:
    p = row(conn, "SELECT id, name FROM people WHERE id=? AND is_self=0", person_id)
    if not p:
        raise PrepareError("no such person", 404)
    return p


HONORIFICS = {"dr", "mr", "mrs", "ms", "miss", "prof", "sir", "shri", "smt"}


def _first(name: str) -> str:
    """Their first name as written, past a title ("Dr. Rao" → "Rao")."""
    parts = [w for w in name.split() if w.rstrip(".").casefold() not in HONORIFICS]
    return parts[0] if parts else ""


def names_for(name: str, extra: list[str] | tuple[str, ...] = ()) -> list[str]:
    """What finds them in a line: the whole name, the first name (3+ letters), and the owner's other spellings (3+)."""
    out = []
    for n in [name, _first(name), *extra]:
        n = " ".join(str(n).split()).casefold()
        if len(n) > 2 and n not in out:
            out.append(n)
    return out


def pattern(name: str, extra=()) -> re.Pattern | None:
    """The whole name and the owner's spellings in any case; the first name alone only capitalised, as transcripts
    write names ("Will" the person, not "we will ship"). None when nothing is long enough to search for."""
    whole = [n for n in [" ".join(name.split()), *extra] if len(n) > 2]
    first = _first(name)
    alts = [rf"(?i:{re.escape(n)})" for n in whole]
    if len(first) > 2 and first.casefold() != " ".join(name.split()).casefold():
        alts.append(re.escape(first[0].upper() + first[1:]))
    return re.compile(r"\b(?:" + "|".join(alts) + r")(?:'s)?\b") if alts else None


def _clean_names(extra) -> list[str]:
    got = [" ".join(str(n).split()) for n in extra or [] if str(n).strip()]
    if len(got) > MAX_NAMES or any(len(n) > 40 for n in got):
        raise PrepareError(f"at most {MAX_NAMES} other spellings, 40 characters each")
    return got


def shared_first(conn, p: dict) -> list[str]:
    """Others in People with the same first name: their mentions can't be told apart by name."""
    first = _first(p["name"]).casefold()
    return [o["name"] for o in rows(conn, "SELECT id, name FROM people WHERE is_self=0 AND id<>?", p["id"])
            if first and _first(o["name"]).casefold() == first]


def pick(conn, person_id: str, extra=(), skip=(), only=None) -> dict:
    """What a plan would read: {"person", "names", "shared_first", "with": [conversation], "mentions": [conversation],
    "sent", "lines", "left"}. A conversation: {session_id, at, with, sp, lines, whole, note, hits?, ticked?}. A mention
    is unticked (listed, not sent) when its session is in `skip`, or, given `only` (what the owner ticked), not in it: a
    conversation that turned up after the page was read is never sent unseen."""
    from .person import with_them
    p = _person(conn, person_id)
    extra = _clean_names(extra)
    names = names_for(p["name"], extra)
    pat = pattern(p["name"], extra)
    theirs = {s["id"] for s in with_them(conn, person_id)}
    skip, only = set(skip or ()), None if only is None else set(only)
    own, mentions = [], []
    for sess, out in readable(conn):
        lines = out["lines"]
        if not lines:
            continue
        hits = [] if sess["id"] in theirs or pat is None else [i for i, ln in enumerate(lines) if pat.search(ln["text"])]
        if sess["id"] not in theirs and not hits:
            continue  # the speakers are read only for conversations that are going to be listed
        at = (sess["started_at"] or 0) + sess["start_s"] if sess["started_at"] else None
        sp = {s["letter"]: s for s in SV.speakers(conn, sess["id"])}
        base = {"session_id": sess["id"], "at": at, "sp": sp, "all": lines,
                "with": [SV.display(s) for s in sp.values() if s["name"] and not s["self"]]}
        if sess["id"] in theirs:
            own.append(base | {"note": f"{p['name']} was in it"})
        else:
            ticked = sess["id"] not in skip and (only is None or sess["id"] in only)
            mentions.append(base | {"hits": hits, "ticked": ticked,
                                    "note": f"{p['name']} wasn't in it: only the lines naming them, and around them"})
    room = {"lines": AK.MAX_LINES, "chars": AK.MAX_CHARS}

    def take(c: dict, centres: list[int] | None, cap: float = 1.0) -> bool:
        """Into the room: whole when centres is None and it fits, else the windows around the centres that fit."""
        lines, lim_l, lim_c = c["all"], room["lines"] - AK.MAX_LINES * (1 - cap), room["chars"] - AK.MAX_CHARS * (1 - cap)
        size = sum(len(ln["text"]) for ln in lines)
        if centres is None and len(lines) <= lim_l and size <= lim_c:
            chosen, c["whole"] = set(range(len(lines))), True
        else:
            c["whole"], chosen, cost = False, set(), 0
            for i in centres if centres is not None else [i for i, ln in enumerate(lines)
                                                         if (c["sp"].get(ln["speaker"]) or {}).get("person_id") == person_id]:
                window = {j for j in range(i - CONTEXT, i + CONTEXT + 1) if 0 <= j < len(lines)} - chosen
                more = sum(len(lines[j]["text"]) for j in window)
                if len(chosen) + len(window) > lim_l or cost + more > lim_c:
                    break
                chosen |= window
                cost += more
        if not chosen:
            return False
        c["lines"] = [lines[i] for i in sorted(chosen)]
        room["lines"] -= len(c["lines"])
        room["chars"] -= sum(len(ln["text"]) for ln in c["lines"])
        return True

    ticked = [m for m in mentions if m["ticked"]]
    sent_m = {id(m) for m in ticked if take(m, m["hits"], MENTIONS_SHARE)}       # mentions first, up to half
    sent_o = [c for c in own if take(c, None)]                                    # newest first, whole while they fit
    sent_m |= {id(m) for m in ticked if id(m) not in sent_m and take(m, m["hits"])}  # then any mentions left
    for m in mentions:
        m["shown"] = [{"line": m["all"][i]["id"], "text": m["all"][i]["text"],
                       "who": SV.display(m["sp"][m["all"][i]["speaker"]]) if m["sp"].get(m["all"][i]["speaker"], {}).get("name")
                       else "Unknown speaker"} for i in m["hits"][:SHOWN]]
    used = [c for c in mentions if id(c) in sent_m] + sent_o
    return {"person": {"id": p["id"], "name": p["name"]}, "names": names, "shared_first": shared_first(conn, p),
            "with": own, "mentions": mentions, "sent": used,
            "lines": sum(len(c["lines"]) for c in used),
            "left": len(own) - len(sent_o) + len(ticked) - len(sent_m)}


def preview(picked: dict) -> dict:
    """The page's list before Prepare: what would go, nothing of it sent yet."""
    sent = {c["session_id"] for c in picked["sent"]}
    who = lambda c, ln: SV.display(c["sp"][ln["speaker"]]) if (c["sp"].get(ln["speaker"]) or {}).get("name") else "Unknown speaker"
    one = lambda c: {"session_id": c["session_id"], "at": c["at"], "with": c["with"],
                     "lines": len(c["lines"]) if c["session_id"] in sent else 0, "whole": bool(c.get("whole")),
                     # every line that would go, for the page's fold (never stored: _stored drops it)
                     "going": [{"line": ln["id"], "who": who(c, ln), "text": ln["text"]} for ln in c["lines"]]
                     if c["session_id"] in sent else []}
    first = _first(picked["person"]["name"])
    return {"person": picked["person"], "names": picked["names"], "shared_first": picked["shared_first"],
            "short_first": first if len(first) < 3 else None,  # too short to search for alone: the page says so
            "lines": picked["lines"], "left": picked["left"], "max_lines": AK.MAX_LINES,
            "with": [one(c) for c in picked["with"]],
            "mentions": [one(c) | {"ticked": c["ticked"], "hits": len(c["hits"]), "shown": c["shown"]} for c in picked["mentions"]]}


# ---- the plan ------------------------------------------------------------------------------------------

QUOTES = {"type": "array", "items": AK.QUOTE}
ITEM = {"type": "object", "properties": {"headline": {"type": "string"}, "observation": {"type": "string"}, "quotes": QUOTES},
        "required": ["headline", "observation", "quotes"]}
SCHEMA = {"type": "object", "properties": {
    "aim": {"type": "string"},
    "why": {"type": "string"},
    "thinking": {"type": "array", "items": ITEM},
    "open": {"type": "array", "items": ITEM},
    "structure": {"type": "array", "items": {"type": "object", "properties": {
        "minutes": {"type": "integer"}, "part": {"type": "string"}, "detail": {"type": "string"}},
        "required": ["minutes", "part", "detail"]}},
    "questions": {"type": "array", "items": {"type": "object", "properties": {
        "purpose": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}, "quotes": QUOTES},
        "required": ["purpose", "items", "quotes"]}},
    "careful": {"type": "array", "items": {"type": "object", "properties": {
        "text": {"type": "string"}, "quotes": QUOTES}, "required": ["text", "quotes"]}},
    "limits": {"type": "string"}},
    "required": ["aim", "why", "thinking", "open", "structure", "questions", "careful", "limits"]}


def system_prompt(owner: str, profile: dict, rules: str) -> str:
    p = O.forms(profile)
    they, them, their, themself, s = p["they"], p["them"], p["their"], p["themself"], p["s"]
    about = (profile.get("about") or "").strip().rstrip(".")
    never = (profile.get("never") or "").strip().rstrip(".")
    how = f"How {they} want{s} reviews done, in {their} words (it holds here too):\n{rules}\n\n" if rules.strip() else ""
    return f"""You help {owner}{', ' + about if about else ''}, prepare for a conversation with one person. You get excerpts of {their} recorded conversations: some the person was in, and some they weren't, where others named them. Read how the person seems to think, then plan the conversation with {them}.

{p['They']} measure{s} {their} conduct against principles {they} wrote {themself}. The plan must be consistent with them{', never ' + never if never else ''}: aim to understand the person and do the work well; never how to position {themself}, impress, win the room or take credit. Use only the principles as {they} wrote them; add no other teachings.

{how}What to write, in plain direct English to {them} as "you":
- aim: one line in {owner}'s own voice, what {they} want{s} from this conversation (it becomes {their} intention), e.g. "Listen first, and understand how she sees X."
- why: why this conversation is happening, from the excerpts and {their} note; say it's a guess where it is.
- thinking: how the person seems to think: what they prioritise, how they decide, how they talk. Each point with the moments it rests on. Keep their own words apart from what others said about them, and say which ("Bill said…").
- open: what's unresolved around them that {owner} could ask about or help with.
- structure: the conversation's parts in order, each with minutes; a short conversation unless {their} note says otherwise. End with {owner} naming one next step {they} will own.
- questions: grouped by purpose, open questions about the person and the work, short enough to say aloud. No leading questions. Each group cites the moments its questions rest on, if any.
- careful: what was said in confidence (who said it, the moment), so {owner} doesn't repeat it, act on it or raise it; and labels or names that may be wrong. Nothing said in confidence goes into thinking, open or questions.
Don't use what others said about the person to flatter them or to seem well-informed.
- limits: what this can't see: few or no conversations with them, mentions that may be someone else with the same name, speakers matched by voice.
Moments: the conversation's key (c1, c2, …), the line number and words copied exactly from that line ("..." may join fragments). They're checked in code against the lines sent; one that isn't there is dropped. Refer to people by name, never by key. Say only what the excerpts show.

{IN.MATERIAL}"""


def user_prompt(owner: str, profile: dict, items: list[dict], person: str, about: str, read: list[dict],
                excerpts_text: str, minutes: int | None = None) -> str:
    their = O.forms(profile)["their"]
    ps = "\n\n".join(f"{x['n']}. {x['text']}" for x in items)
    readings = "\n".join(f"- {r['headline']}: {r['observation']}" + (f" ({owner} said partly right: {r['note']})" if r.get("partly") and r.get("note") else "")
                         for r in read)
    parts = [f"{owner}'s principles, in {their} words:\n\n{ps}" if ps else "",
             f"The conversation to prepare: with {person}." + (f" In {owner}'s words: \"{about}\"" if about else "")
             + (f" The calendar gives it {minutes} minutes: fit the structure to that." if minutes else ""),
             f"What the reviews of earlier conversations read in {person}:\n{readings}" if readings else "",
             f"Excerpts, oldest first:\n\n{excerpts_text}",
             f"Plan {owner}'s conversation with {person}."]
    return "\n\n".join(x for x in parts if x)


def check(raw: dict, keys: dict, owner: str, person_id: str | None = None) -> dict:
    """The plan with every moment checked against the lines sent (or dropped). A reading or what's open with no moment
    left is dropped and counted (`unbacked`); one resting only on conversations they weren't in is marked second-hand.
    Anything citing a moment the plan itself calls careful (said in confidence) is dropped from thinking, open and the
    questions (`withheld`): a question is what the owner says aloud. Second-hand: no moment is the person's own words."""
    dropped = 0

    def quotes(qs) -> list[dict]:
        nonlocal dropped
        got = [v for q in qs or [] if (v := AK.verify(q, keys, owner))]
        dropped += len(qs or []) - len(got)
        return got

    careful = [{"text": (x.get("text") or "").strip(), "quotes": quotes(x.get("quotes"))}
               for x in raw.get("careful") or [] if (x.get("text") or "").strip()]
    secret = {(q["session_id"], q["line"]) for k in careful for q in k["quotes"]}
    touches = lambda qs: any((q["session_id"], q["line"]) in secret for q in qs)
    said = {(c["session_id"], ln["id"]): (c["sp"].get(ln["speaker"]) or {}).get("person_id") for c in keys.values() for ln in c["lines"]}
    own_words = lambda q: person_id is not None and said.get((q["session_id"], q["line"])) == person_id
    unbacked = withheld = 0

    def items(xs) -> list[dict]:
        nonlocal unbacked, withheld
        out = []
        for x in xs or []:
            if not (x.get("headline") or x.get("observation")):
                continue
            q = quotes(x.get("quotes"))
            if not q:
                unbacked += 1
            elif touches(q):
                withheld += 1
            else:
                out.append({"headline": (x.get("headline") or "").strip(), "observation": (x.get("observation") or "").strip(),
                            "quotes": q, "second_hand": not any(own_words(m) for m in q)})
        return out

    groups = []
    for g in raw.get("questions") or []:
        qs = [q.strip() for q in g.get("items") or [] if q.strip()]
        if not qs:
            continue
        if touches(quotes(g.get("quotes"))):
            withheld += len(qs)
            continue
        groups.append({"purpose": (g.get("purpose") or "").strip(), "items": qs})
    plan = {"aim": (raw.get("aim") or "").strip()[:300], "why": (raw.get("why") or "").strip(),
            "thinking": items(raw.get("thinking")), "open": items(raw.get("open")),
            "structure": [{"minutes": _minutes(x.get("minutes")), "part": (x.get("part") or "").strip(),
                           "detail": (x.get("detail") or "").strip()} for x in raw.get("structure") or [] if x.get("part")],
            "questions": groups, "careful": careful, "limits": (raw.get("limits") or "").strip()}
    return plan | {"dropped": dropped, "unbacked": unbacked, "withheld": withheld}


def _minutes(v) -> int:
    """A part's minutes; a model that writes "5-10" or "about 5" loses the number, not the plan."""
    try:
        return max(0, int(float(v or 0)))
    except (TypeError, ValueError):
        m = re.search(r"\d+", str(v))
        return int(m[0]) if m else 0


def close_first(conn, person_id: str) -> list[dict]:
    """Open steps from conversations with them and open commitments both ways: from the lab, no model."""
    from . import commitments as CM
    from .person import with_them
    ids = {s["id"] for s in with_them(conn, person_id)}
    steps = [{"kind": "step", "text": t["title"], "due": t["due"], "session_id": t["session_id"]}
             for t in SV.open_tasks(conn) if t["session_id"] in ids]
    promised = [{"kind": "commitment", "text": c["what"], "due": c["due"], "session_id": c["session_id"], "mine": c["mine"]}
                for c in CM.for_person(conn, person_id)]
    return steps + promised


# ---- one per person: start, run, view -------------------------------------------------------------------

def _row(conn, person_id: str) -> dict | None:
    """The newest: running, failed or done."""
    return row(conn, "SELECT * FROM preparations WHERE person_id=? ORDER BY created_at DESC LIMIT 1", person_id)


def _done(conn, person_id: str) -> dict | None:
    return row(conn, "SELECT * FROM preparations WHERE person_id=? AND status='done' ORDER BY created_at DESC LIMIT 1", person_id)


def _began(conn, r: dict) -> tuple[float | None, str | None]:
    """When the conversation a plan was for began, and which: the first one with them that started after the plan was
    made, or the calendar event it was made for, whichever comes first (an unrecorded meeting still ends it)."""
    from .person import with_them
    after = [s for s in with_them(conn, r["person_id"]) if s["at"] and s["at"] > r["created_at"]]
    conv = min(after, key=lambda s: s["at"]) if after else None
    ev = r["event_end"]
    if conv and (not ev or conv["at"] <= ev):
        return conv["at"], conv["id"]
    return ev, None


def _sweep(conn, now: float) -> None:
    """A stopped run reads as failed. A plan is deleted KEEP_AFTER_S after the conversation it was for began (_began),
    a failed one too. From the page, the banner and the hourly pass."""
    conn.execute("UPDATE preparations SET status='failed', error=?, error_at=?, finished_at=? WHERE status='running' AND created_at < ?",
                 ("it stopped before it finished (the lab may have restarted): prepare again", now, now, now - STOPPED_AFTER_S))
    for r in rows(conn, "SELECT id, person_id, created_at, event_end FROM preparations WHERE status <> 'running'"):
        at, _ = _began(conn, r)
        if at and now > at + KEEP_AFTER_S:
            conn.execute("DELETE FROM preparations WHERE id=?", (r["id"],))


def sweep(conn, now: float | None = None) -> None:
    """The hourly pass's (cli.py): plans past their conversation go even if no page is opened."""
    _sweep(conn, time.time() if now is None else now)


def why_not(conn, person_id: str, now: float | None = None) -> str | None:
    now = time.time() if now is None else now
    _sweep(conn, now)
    if row(conn, "SELECT 1 FROM preparations WHERE person_id=? AND status='running'", person_id):
        return "a plan is being made"
    return model_why_not(conn, now)


def _stored(picked: dict) -> dict:
    """What a plan keeps of what it read: ids, dates, who was there and counts; never a line's words (an unticked
    mention's lines would outlive the conversation they came from)."""
    pv = preview(picked)
    drop = lambda c: {k: v for k, v in c.items() if k not in ("shown", "going")}
    return pv | {"with": [drop(c) for c in pv["with"]], "mentions": [drop(m) for m in pv["mentions"]]}


def _sources(conn, prep_id: str, picked: dict, also=()) -> None:
    """Every conversation the plan names, sent or listed: deleting any of them deletes the plan."""
    conn.execute("DELETE FROM preparation_sources WHERE prep_id=?", (prep_id,))
    ids = {c["session_id"] for c in picked["with"] + picked["mentions"]} | set(also)
    conn.executemany("INSERT OR IGNORE INTO preparation_sources (prep_id, session_id) VALUES (?, ?)", [(prep_id, i) for i in ids])


def start(conn, person_id: str, about: str | None, extra=(), mentions=None, now: float | None = None) -> dict:
    """Pick, record a running plan, and return the view; run() makes it. `mentions`: the mention conversations the owner
    left ticked (None: all). A plan already made stays until the new one is."""
    from .settings import reader
    now = time.time() if now is None else now
    about = " ".join((about or "").split())
    if len(about) > MAX_ABOUT:
        raise PrepareError(f"at most {MAX_ABOUT} characters about the conversation")
    extra = _clean_names(extra)
    if why := why_not(conn, person_id, now):
        raise PrepareError(why, 409 if why.startswith("a plan is") else 422)
    picked = pick(conn, person_id, extra, only=mentions)  # outside the write lock: it reads every transcript
    if not picked["sent"]:
        raise PrepareError("nothing in the lab with them or about them to read yet")
    ev = next_event(conn, person_id, now)
    with tx(conn):
        _person(conn, person_id)
        if why := why_not(conn, person_id, now):  # again, under the lock: a second tap meanwhile
            raise PrepareError(why, 409 if why.startswith("a plan is") else 422)
        conn.execute("DELETE FROM preparations WHERE person_id=? AND status <> 'done'", (person_id,))
        pid = new_id("prep")
        conn.execute("INSERT INTO preparations (id, person_id, about, names, status, created_at, reader, sent, event_title, "
                     "event_start, event_end) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)",
                     (pid, person_id, about, json.dumps(extra), now, json.dumps(reader(conn)), json.dumps(_stored(picked)),
                      ev and ev["title"], ev and ev["start"], ev and ev["end"]))
        _sources(conn, pid, picked)
    return view(conn, person_id)


def _ask(who: dict, effort: str, system: str, prompt: str, schema: dict) -> tuple[dict, dict]:
    """One call to the reviews' model. Tests and the demo lab replace this."""
    from .rehearse import _ask as ask
    return ask(who, effort, system, prompt, schema)


def run(conn, person_id: str) -> dict:
    """The call for a running plan: pick again as it was asked (only what was ticked), ask, check, store what this
    pick read (it may differ from the page's by a conversation added meanwhile) and which conversations it named."""
    from .person import readings_of
    from .rubric import text as rubric_text
    r = _row(conn, person_id)
    if not r or r["status"] != "running":
        raise PrepareError("no plan waiting to be made", 404)
    who = load_json(r["reader"]) or {}
    sent = load_json(r["sent"]) or {}
    only = [m["session_id"] for m in sent.get("mentions") or [] if m["ticked"]]
    owner, profile = _owner(conn)
    usage = None
    try:
        picked = pick(conn, person_id, load_json(r["names"]) or [], only=only)
        text, keys = AK.excerpts(sorted(picked["sent"], key=lambda c: c["at"] or 0), owner)
        rubric = rubric_text()
        read = readings_of(conn, person_id)[:READINGS]
        minutes = round((r["event_end"] - r["event_start"]) / 60) if r["event_start"] and r["event_end"] else None
        prompt = user_prompt(owner, profile, IN.principles(rubric), picked["person"]["name"], r["about"] or "", read, text, minutes)
        raw, usage = _ask(who, who.get("effort") or "high", system_prompt(owner, profile, IN.review_rules(rubric)), prompt, SCHEMA)
        usage = usage or {}
        plan = check(raw, keys, owner, person_id)
    except Exception as e:  # noqa: BLE001 - kept, so a usage limit holds Claude's steps; shown on the page
        msg = str(e)[:2000] or type(e).__name__
        _ended(conn, r, who, usage, "UPDATE preparations SET status='failed', error=?, error_at=?, finished_at=?, usage=? WHERE id=?",
               (msg, time.time(), time.time(), None if usage is None else json.dumps(usage), r["id"]))
        return view(conn, person_id)
    with tx(conn):
        _ended(conn, r, who, usage, "UPDATE preparations SET status='done', plan=?, usage=?, reader=?, sent=?, finished_at=? "
               "WHERE id=? AND status='running'", (json.dumps(plan), json.dumps(usage), json.dumps(_reader_of(who)),
                                                   json.dumps(_stored(picked)), time.time(), r["id"]))
        if row(conn, "SELECT 1 FROM preparations WHERE id=? AND status='done'", r["id"]):
            _sources(conn, r["id"], picked, {x["session_id"] for x in read})  # the readings came from these
            conn.execute("DELETE FROM preparations WHERE person_id=? AND id<>?", (person_id, r["id"]))  # the one before
    return view(conn, person_id)


def _ended(conn, r: dict, who: dict, usage: dict | None, sql: str, args: tuple) -> None:
    """Store how a run ended; a call that was made is counted even when its row was deleted (Delete, Forget, a deleted
    conversation) or stopped meanwhile, so deleting and preparing again can't get past the day's calls."""
    if conn.execute(sql, args).rowcount or usage is None:
        return
    if row(conn, "SELECT 1 FROM preparations WHERE id=?", r["id"]):  # stopped by the sweep: the row counts it
        conn.execute("UPDATE preparations SET usage=?, reader=?, finished_at=? WHERE id=?",
                     (json.dumps(usage), json.dumps(_reader_of(who)), time.time(), r["id"]))
    else:
        conn.execute("INSERT INTO calls_gone (at, n, reader) VALUES (?, 1, ?)", (time.time(), json.dumps(_reader_of(who))))


def run_in_background(db, person_id: str) -> None:
    """The server's: the call off the request (a minute or two), in a thread of this process."""
    def go() -> None:
        try:
            run(db(), person_id)
        except PrepareError:
            pass
    threading.Thread(target=go, daemon=True, name="octo-prepare").start()


def next_event(conn, person_id: str, now: float | None = None) -> dict | None:
    """The next event (today or tomorrow, not over) with them, from the calendar (B23): its title fills the note, and
    the plan ends a day after it."""
    from .events import agenda
    now = time.time() if now is None else now
    return next(({"title": e["title"], "start": e["start"], "end": e["end"]} for e in agenda(conn, now)
                 if e["end"] > now and any(p["person_id"] == person_id for p in e["people"])), None)


def _plan_view(conn, r: dict) -> dict:
    from .pipeline import limit_reset
    at, sid = _began(conn, r) if r["status"] == "done" else (None, None)
    return {"used": {"at": at, "session_id": sid} if at and time.time() >= at else None,"id": r["id"], "status": r["status"], "at": r["created_at"], "finished_at": r["finished_at"],
            "about": r["about"], "names": load_json(r["names"]) or [], "sent": load_json(r["sent"]),
            "event": r["event_title"], "plan": load_json(r["plan"]), "error": r["error"],
            "waiting_until": limit_reset(r["error"], r["error_at"])}


def view(conn, person_id: str) -> dict:
    """The page: the newest plan (running, failed or done), the last one made while a new one runs or failed, what to
    close first, the next event with them, and their open intention (Save as my intention replaces it)."""
    from .intention import open_for
    p = _person(conn, person_id)
    why = why_not(conn, person_id)
    r, done = _row(conn, person_id), _done(conn, person_id)
    intention = open_for(conn, person_id)
    return {"person": {"id": p["id"], "name": p["name"]}, "plan": _plan_view(conn, r) if r else None,
            "previous": _plan_view(conn, done) if done and r and done["id"] != r["id"] else None,
            "close_first": close_first(conn, person_id), "event": next_event(conn, person_id),
            "intention": intention["text"] if intention else None,
            "why_not": None if why == "a plan is being made" else why, "max_about": MAX_ABOUT, "max_names": MAX_NAMES}


def _is_ready(conn, r: dict, now: float) -> bool:
    at, _ = _began(conn, r)
    return not at or now < at + READY_FOR_S


def ready(conn, person_id: str) -> bool:
    """A finished plan for them, for a conversation still to come (or under way): the banner before a meeting opens it
    rather than their page, their page and the agenda say "Your plan"."""
    now = time.time()
    _sweep(conn, now)
    r = _done(conn, person_id)
    return bool(r) and _is_ready(conn, r, now)


def ready_ids(conn, now: float | None = None) -> set[str]:
    """Everyone with a ready plan, at once (the agenda)."""
    now = time.time() if now is None else now
    _sweep(conn, now)
    return {r["person_id"] for r in rows(conn, "SELECT id, person_id, created_at, event_end FROM preparations WHERE status='done'")
            if _is_ready(conn, r, now)}


def planned_for(conn, session_id: str) -> list[dict]:
    """"What you planned" beside a conversation's review: the plans whose conversation it was (made before it began, for
    someone confirmed in it). Their aim and questions only; read here, so the review's input is unchanged."""
    out = []
    for r in rows(conn, "SELECT p.*, pe.name FROM preparations p JOIN people pe ON pe.id = p.person_id "
                        "WHERE p.status='done' AND p.person_id IN (SELECT person_id FROM session_speakers WHERE session_id=? "
                        "AND state IN ('confirmed', 'auto'))", session_id):
        if _began(conn, r)[1] == session_id:
            plan = load_json(r["plan"]) or {}
            out.append({"person_id": r["person_id"], "name": r["name"], "at": r["created_at"], "aim": plan.get("aim") or "",
                        "questions": plan.get("questions") or [], "about": r["about"]})
    return out


def delete(conn, person_id: str) -> None:
    conn.execute("DELETE FROM preparations WHERE person_id=?", (person_id,))  # secure_delete; sources cascade


def calls_since(conn, since: float, which) -> int:
    """Its calls for the day's count (pipeline._calls_since): each call made since `since`, the plan made or not."""
    return sum(1 for r in rows(conn, "SELECT reader FROM preparations WHERE usage IS NOT NULL AND finished_at >= ?", since)
               if which(load_json(r["reader"]) or {}))


def someone_new(conn, name: str, invitee: str | None = None) -> str:
    """Prepare for someone Octo has never heard (a first meeting with someone others talk about): they're added to People by
    name, with no voiceprint, so their page, Forget and naming them as a speaker later all work as for anyone. A calendar
    invitee's match is kept too, so the agenda knows them from then on. Returns their id."""
    from . import events as EV
    from . import voice as V
    name = " ".join((name or "").split())
    if not 2 <= len(name) <= 60:
        raise PrepareError("their name, 2 to 60 characters")
    with tx(conn):
        p = V.person_by_name(conn, name)
        if p and p["is_self"]:
            raise PrepareError("that's you")
        p = p or V.ensure_person(conn, name)
        if invitee:
            EV.keep_match(conn, invitee, p["id"])
    return p["id"]


def forget_name(conn, name: str) -> int:
    """Forget: plans for others that name them (a reading, a quote, who was there), not only those that read a
    conversation they spoke in."""
    pat = pattern(name)
    if pat is None:
        return 0
    ids = [r["id"] for r in rows(conn, "SELECT id, plan, sent FROM preparations")
           if pat.search(r["plan"] or "") or pat.search(r["sent"] or "")]
    for i in ids:
        conn.execute("DELETE FROM preparations WHERE id=?", (i,))
    return len(ids)


def drop_for(conn, session_ids: list[str]) -> int:
    """Every plan that read any of these conversations (a delete, or a Forget of someone in one) is deleted."""
    n = 0
    for i in range(0, len(session_ids), 500):
        part = session_ids[i:i + 500]
        n += conn.execute(f"DELETE FROM preparations WHERE id IN (SELECT prep_id FROM preparation_sources "
                          f"WHERE session_id IN ({','.join('?' * len(part))}))", part).rowcount
    return n
