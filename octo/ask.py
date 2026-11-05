"""Ask your conversations (B24, 21 Oct): a question in plain words, answered from the transcripts.

For what search (B12, octo/search.py) can't do: meaning rather than exact words. One model call per question.

- **Picked on the Mac, little sent.** The conversations a question needs are chosen here first, with no model: the
  people it names, the dates it names ("this month", "last week", "in October"), and the lines that share its words
  (search's reading: corrections applied, speakers left out taken out, set-aside and deleted conversations absent;
  drills and rehearsals are never conversations). Only those lines go, at most MAX_LINES of them; nothing picked,
  nothing asked. The page says which conversations went and how many lines.
- **Every moment checked in code** against the lines that were sent (insight.verify_quote), or dropped; a claim
  without one says so.
- **What leaves the Mac** (21 Oct): the question and the picked lines of several conversations at once, other
  people's words in them, to the owner's model (settings.reader, as the coach's).
- **Calls:** one per question, in the day's DAILY_CLAUDE_CALLS (a row in model_calls, pipeline._calls_since); a usage-limit
  failure holds the pipeline until it resets, as the coach's does.
- **What's kept:** an asks row per question, with no words: when, the model, its usage or error (for the day's count).
  The question, the answer and what was sent stay in this process's memory for LIVE_S, never on disk, unless the
  owner keeps them (Keep); a restart loses them. A kept one is deleted with any conversation it read (ask_sources),
  and when someone in one is forgotten, as coach notes are.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta

from . import insight as IN
from . import sessions as SV
from .db import calling, load_json, new_id, row, rows, tx
from .search import readable

# Lines sent per question. A review sends one whole conversation: in the first lab (27 Oct, 60 conversations) the median is
# 142 lines, the longest 382, the whole lab 7,751 (learnings, Ask). 400 keeps a question at most one long review's worth
# (~6,000 words, ~8k tokens, about 5% of that lab) while leaving room for stretches of ~10 conversations; MAX_CHARS
# stops a few very long lines going past that.
MAX_LINES = 400
MAX_CHARS = 48_000
CONTEXT = 3              # lines either side of a line that shares the question's words
COMMON = 0.2             # a word in more than this share of the candidate lines picks nothing ("work", "time")
LIVE_S = 30 * 60         # an answer not kept is gone from memory after this
MAX_QUESTION = 300
STOPPED_AFTER_S = 1500   # an ask still "running" after this was stopped (a restart)


class AskError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status  # 404 no such answer · 409 one is being asked · 422 can't ask now


# ---- picking, on the Mac ------------------------------------------------------------------------

STOP = set("""a an the and or but if so of to in on at by for from with about into over than then that this these those
it its is are was were be been being am do does did done have has had having i me my mine myself we us our you your he him
his she her they them their what when where which who whom whose why how all any some much many more most other again
ever never not no yes can could would should will shall may might must just only also very really ok okay
said say says saying tell told talk talked talking speak spoke mention mentioned asked ask ever last time times
conversation conversations call calls meeting meetings anything something everything thing things""".split())
MONTHS = {m: i for i, m in enumerate(("january february march april may june july august september october november "
                                      "december").split(), 1)}
MONTHS |= {m[:3]: i for m, i in list(MONTHS.items())}
DATE_WORDS = {"today", "yesterday", "week", "month", "days", "weeks", "months", "past", "this", "since", *MONTHS}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def _stem(w: str) -> str:
    w = w.removesuffix("'s")
    return w[:5] if len(w) > 5 else w  # "workload" finds "workload's"; "promised" finds "promise"


def people_named(conn, question: str) -> list[dict]:
    """The people the question names: a whole name or a first name, any case."""
    q = f" {' '.join(_words(question))} "
    out = []
    for p in rows(conn, "SELECT id, name FROM people WHERE is_self=0 AND name IS NOT NULL"):
        full = " ".join(_words(p["name"]))
        first = full.split(" ")[0] if full else ""
        if full and (f" {full} " in q or f" {full}'s " in q or (len(first) > 2 and (f" {first} " in q or f" {first}'s " in q))):
            out.append(p)
    return out


def dates_named(question: str, now: float) -> tuple[float, float, str] | None:
    """(from, to, as said) for the dates a question names, or None."""
    q = " ".join(_words(question))
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    end = today + timedelta(days=1)
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    if m := re.search(r"\b(?:last|past) (\d+) (day|week|month)s?\b", q):
        n = int(m[1]) * {"day": 1, "week": 7, "month": 30}[m[2]]
        return (today - timedelta(days=n - 1)).timestamp(), end.timestamp(), m[0]
    for said, a, b in (("today", today, end), ("yesterday", today - timedelta(days=1), today),
                       ("this week", monday, end), ("last week", monday - timedelta(days=7), monday),
                       ("this month", first, end),
                       ("last month", (first - timedelta(days=1)).replace(day=1), first)):
        if re.search(rf"\b{said}\b", q):
            return a.timestamp(), b.timestamp(), said
    if m := re.search(r"\b(?:in|since|during|from) (" + "|".join(MONTHS) + r")\b", q):
        month = MONTHS[m[1]]
        start = today.replace(day=1, month=month)
        if start > today:
            start = start.replace(year=start.year - 1)
        stop = end if m[0].startswith("since") else (start + timedelta(days=32)).replace(day=1)
        return start.timestamp(), stop.timestamp(), m[0]
    return None


def pick(conn, question: str, now: float | None = None) -> dict:
    """The lines a question needs, chosen here: {"conversations": [{session_id, at, with, lines, whole}], "lines": n,
    "people": names, "dates": as said, "left": conversations that matched but didn't fit}."""
    now = time.time() if now is None else now
    named = people_named(conn, question)
    dates = dates_named(question, now)
    name_words = {w for p in named for w in _words(p["name"])}
    stems = {_stem(w) for w in _words(question)
             if w not in STOP and w not in name_words and w not in DATE_WORDS and not w.isdigit() and len(w) > 2}
    found = []
    for sess, out in readable(conn):
        at = (sess["started_at"] or 0) + sess["start_s"] if sess["started_at"] else None
        if dates and not (at and dates[0] <= at < dates[1]):
            continue
        sp = {s["letter"]: s for s in SV.speakers(conn, sess["id"])}
        if named and not {p["id"] for p in named} & {s["person_id"] for s in sp.values()}:
            continue
        if out["lines"]:
            found.append((sess, at, out["lines"], sp))
    pats = {st: re.compile(rf"\b{re.escape(st)}") for st in stems}
    texts = [[ln["text"].casefold() for ln in lines] for _, _, lines, _ in found]
    df = {st: sum(1 for ts in texts for t in ts if p.search(t)) for st, p in pats.items()}
    n_lines = sum(len(ts) for ts in texts) or 1
    rare = {st: math.log(n_lines / (1 + df[st])) for st in stems if 0 < df[st] <= max(3, COMMON * n_lines)}
    scored = []
    for (sess, at, lines, sp), ts in zip(found, texts):
        hits = {i: sum(w for st, w in rare.items() if pats[st].search(t)) for i, t in enumerate(ts)}
        hits = {i: s for i, s in hits.items() if s > 0}
        scored.append((sess, at, lines, sp, hits))
    narrowed = bool(named or dates)
    if narrowed:
        scored.sort(key=lambda c: (not c[4], -(c[1] or 0)))  # those sharing its words first, then newest
    else:
        scored = sorted((c for c in scored if c[4]), key=lambda c: (-sum(c[4].values()), -(c[1] or 0)))
    budget, chars, convs = MAX_LINES, MAX_CHARS, []
    for sess, at, lines, sp, hits in scored:
        size = sum(len(ln["text"]) for ln in lines)
        if narrowed and len(lines) <= budget and size <= chars:
            take, whole = list(range(len(lines))), True
        else:
            whole = False
            centres = sorted(hits, key=lambda i: -hits[i]) or \
                [i for i, ln in enumerate(lines) if (sp.get(ln["speaker"]) or {}).get("person_id") in {p["id"] for p in named}]
            chosen: set[int] = set()
            for i in centres:
                window = {j for j in range(i - CONTEXT, i + CONTEXT + 1) if 0 <= j < len(lines)} - chosen
                cost = sum(len(lines[j]["text"]) for j in window)
                if len(chosen) + len(window) > budget or cost > chars - sum(len(lines[j]["text"]) for j in chosen):
                    break
                chosen |= window
            take = sorted(chosen)
        if not take:
            continue
        picked = [lines[i] for i in take]
        budget -= len(picked)
        chars -= sum(len(ln["text"]) for ln in picked)
        convs.append({"session_id": sess["id"], "at": at, "sp": sp, "lines": picked, "whole": whole,
                      "with": [SV.display(s) for s in sp.values() if s["name"] and not s["self"]]})
        if budget <= 0 or chars <= 0:
            break
    convs.sort(key=lambda c: c["at"] or 0)  # oldest first, so "last" and "since" read in order
    return {"conversations": convs, "lines": sum(len(c["lines"]) for c in convs), "people": [p["name"] for p in named],
            "dates": dates[2] if dates else None, "left": len(scored) - len(convs)}


# ---- asking ------------------------------------------------------------------------------------

QUOTE = {"type": "object", "properties": {"conversation": {"type": "string"}, "line": {"type": "integer"},
                                          "text": {"type": "string"}}, "required": ["conversation", "line", "text"]}
SCHEMA = {"type": "object", "properties": {
    "claims": {"type": "array", "items": {"type": "object", "properties": {
        "text": {"type": "string"}, "quotes": {"type": "array", "items": QUOTE}}, "required": ["text", "quotes"]}},
    "not_found": {"type": "string"}, "limits": {"type": "string"}},
    "required": ["claims", "not_found", "limits"]}

SYSTEM = """You answer {name}'s question about their own recorded conversations, from excerpts of the transcripts.

The excerpts were picked on {name}'s Mac by the people, the dates and the words in the question, so they may miss
what was said in other words or in conversations not sent. Each conversation has a key (c1, c2, …), its date and
who was in it; each line is numbered in square brackets; "…" between lines means lines were left out.

- claims: the answer, one plain sentence each, newest moment last. Each claim cites the moments it rests on:
  the conversation's key, the line number and the words copied exactly from that line ("..." may stand for words
  left out). Quotes are checked in code against the lines sent; one that isn't there is dropped.
- Say only what the excerpts show. Don't guess at what someone meant or felt beyond their words.
- not_found: if the excerpts don't answer the question, one sentence saying what they lack; else "".
- limits: one sentence on what this answer can't see (conversations or words that weren't sent, unsure speakers); or "".
Speaker names were matched by voice and may be wrong where marked unsure. Refer to people by name, never by key.

""" + IN.MATERIAL


def _prompt(question: str, picked: dict, owner: str) -> tuple[str, dict]:
    """The text sent, and each key's conversation."""
    text, keys = excerpts(picked["conversations"], owner)
    return f"Question: {question}\n\n{text}", keys


def excerpts(convs: list[dict], owner: str) -> tuple[str, dict]:
    """Picked conversations as the model reads them (c1, c2, …: date, who, numbered lines, "…" for a gap), and each
    key's conversation. A conversation's `note`, if any, is added to its heading. Prepare (B36) sends the same."""
    keys, parts = {}, []
    for k, c in enumerate(convs, 1):
        key = f"c{k}"
        keys[key] = c
        names = {L: (owner if s["self"] else SV.display(s) if s["name"] else "Unknown speaker") for L, s in c["sp"].items()}
        head = datetime.fromtimestamp(c["at"]).strftime("%A %-d %b %Y, %H:%M") if c["at"] else "date unknown"
        parts.append(f"{key} · {head} · with {', '.join(c['with']) or 'nobody named'}" + ("" if c["whole"] else " · excerpts")
                     + (f" · {c['note']}" if c.get("note") else ""))
        prev = None
        for ln in c["lines"]:
            if prev is not None and ln["id"] != prev + 1:
                parts.append("…")
            parts.append(IN.transcript([ln], names))
            prev = ln["id"]
        parts.append("")
    return "\n".join(parts), keys


def verify(q: dict, keys: dict, owner: str) -> dict | None:
    """One quote {conversation, line, text} checked against the lines sent under its key: the moment, or None."""
    c = keys.get(str(q.get("conversation", "")).strip())
    v = IN.verify_quote({"line": q.get("line"), "text": q.get("text", "")}, {ln["id"]: ln for ln in c["lines"]}) if c else None
    if not v:
        return None
    s = c["sp"].get(v["speaker"]) or {}
    return {"session_id": c["session_id"], "line": v["line"], "start": v["start"], "text": v["text"],
            "at": c["at"], "who": owner if s.get("self") else (SV.display(s) if s else "Unknown speaker"),
            "self": bool(s.get("self")), "uncertain": v["uncertain"]}


def check(raw: dict, keys: dict, owner: str) -> dict:
    """The answer with every quote checked against the lines sent; a quote that isn't there is dropped."""
    claims, dropped = [], 0
    for cl in raw.get("claims") or []:
        text = (cl.get("text") or "").strip()
        if not text:
            continue
        quotes = [v for q in cl.get("quotes") or [] if (v := verify(q, keys, owner))]
        dropped += len(cl.get("quotes") or []) - len(quotes)
        claims.append({"text": text, "quotes": quotes})
    return {"claims": claims, "dropped": dropped, "not_found": (raw.get("not_found") or "").strip(),
            "limits": (raw.get("limits") or "").strip()}


def _sent(picked: dict) -> dict:
    """What the page says went: which conversations, how many lines of each."""
    return {"lines": picked["lines"], "people": picked["people"], "dates": picked["dates"], "left": picked["left"],
            "conversations": [{"session_id": c["session_id"], "at": c["at"], "with": c["with"], "lines": len(c["lines"]),
                               "whole": c["whole"]} for c in picked["conversations"]]}


_LIVE: dict[str, dict] = {}  # id → {question, answer, sent, sources, at}: an answer not kept, in memory only
_LOCK = threading.Lock()


def _sweep(now: float) -> None:
    with _LOCK:
        for k in [k for k, v in _LIVE.items() if v["at"] < now - LIVE_S]:
            del _LIVE[k]


def why_not(conn, now: float | None = None) -> str | None:
    """Why a question can't be asked right now, or None."""
    from . import claude_code as CC
    from . import runs as RN
    from .settings import reader
    now = time.time() if now is None else now
    conn.execute("UPDATE asks SET status='failed', finished_at=?, error=? WHERE status='running' AND created_at < ?",
                 (now, "it stopped before it finished (the lab may have restarted): ask again", now - STOPPED_AFTER_S))
    if row(conn, "SELECT 1 FROM asks WHERE status='running'"):
        return "a question is being answered now"
    who = reader(conn)
    if who is None:
        return "choose a model for reviews first (Settings → Models and keys)"
    if who["kind"] == "claude-code":
        if not CC.claude_bin():
            return "Claude Code isn't installed (the `claude` command)"
        if why := RN.claude_waits(conn, now):
            return why
    elif who.get("connection") and RN.budget_blocked_until(conn, who["connection"], now):
        return "today's calls for this model are used: after midnight"
    return None


def start(conn, question: str, now: float | None = None) -> dict:
    """Pick the lines and record the question; returns its live entry. With nothing picked it's answered at once,
    with no call. Raises AskError if it can't be asked now."""
    from .settings import reader
    now = time.time() if now is None else now
    question = " ".join(question.split())
    if len(question) < 3 or len(question) > MAX_QUESTION:
        raise AskError(f"a question of 3 to {MAX_QUESTION} characters")
    _sweep(now)
    with tx(conn):
        if why := why_not(conn, now):
            raise AskError(why, 409 if why.startswith("a question is") else 422)
        picked = pick(conn, question, now)
        aid = new_id("ask")
        nothing = not picked["conversations"]
        conn.execute("INSERT INTO asks (id, created_at, status, reader, finished_at) VALUES (?, ?, ?, ?, ?)",
                     (aid, now, "done" if nothing else "running", json.dumps(reader(conn)), now if nothing else None))
        conn.executemany("INSERT OR IGNORE INTO ask_sources (ask_id, session_id) VALUES (?, ?)",
                         [(aid, c["session_id"]) for c in picked["conversations"]])
    live = {"id": aid, "question": question, "at": now, "status": "done" if nothing else "running", "sent": _sent(picked),
            "answer": {"claims": [], "dropped": 0, "limits": "",
                       "not_found": "Nothing on the Mac matched it, so nothing was sent: try other words, a name or a date."}
            if nothing else None, "error": None, "sources": {c["session_id"] for c in picked["conversations"]},
            "_picked": picked}
    with _LOCK:
        _LIVE[aid] = live
    return view(live)


def run(conn, ask_id: str, ask=None) -> dict:
    """The call for a started question: ask, check, keep the answer in memory. A failure is stored and shown."""
    from .settings import reader
    with _LOCK:
        live = _LIVE.get(ask_id)
    if not live or live["status"] != "running":
        raise AskError("no such question waiting to be answered", 404)
    who = load_json(row(conn, "SELECT reader FROM asks WHERE id=?", ask_id)["reader"]) or reader(conn) or {}
    if ask is None:
        from . import claude_code as CC
        from . import providers as PV
        ask = CC.ask if who.get("kind") == "claude-code" else \
            (lambda model, effort, system, prompt, schema: PV.ask(who, system, prompt, schema))
    me = row(conn, "SELECT name FROM people WHERE is_self=1 LIMIT 1")
    owner = (me["name"] if me else None) or "the owner"
    prompt, keys = _prompt(live["question"], live.pop("_picked"), owner)
    try:
        with calling(conn, "ask", ask_id):
            raw, usage = ask(who.get("model") or "claude-opus-5-5", who.get("effort") or "high",
                             SYSTEM.format(name=owner), prompt, SCHEMA)
        answer = check(raw, keys, owner)
    except Exception as e:  # noqa: BLE001 - stored, and shown on the page
        msg = "it took too long: ask again" if isinstance(e, subprocess.TimeoutExpired) else (str(e) or type(e).__name__)
        conn.execute("UPDATE asks SET status='failed', error=?, finished_at=? WHERE id=?", (msg[:2000], time.time(), ask_id))
        live.update(status="failed", error=msg)
        return view(live)
    conn.execute("UPDATE asks SET status='done', usage=?, finished_at=? WHERE id=?", (json.dumps(usage), time.time(), ask_id))
    with _LOCK:
        if ask_id in _LIVE:  # a delete or a Forget meanwhile took it away
            live.update(status="done", answer=answer)
    return view(live)


def view(live: dict) -> dict:
    return {k: v for k, v in live.items() if k not in ("sources", "_picked")}


def _kept(r: dict) -> dict:
    d = load_json(r["answer"]) or {}
    return {"id": r["id"], "question": r["question"], "at": r["created_at"], "status": "done", "kept_at": r["kept_at"],
            "answer": d.get("answer"), "sent": d.get("sent"), "error": None}


def summary(conn, now: float | None = None) -> dict:
    """The answers in memory (newest first), the kept ones, and why one can't be asked now."""
    now = time.time() if now is None else now
    _sweep(now)
    with _LOCK:
        live = [view(v) for v in sorted(_LIVE.values(), key=lambda v: -v["at"])]
    kept = [_kept(r) for r in rows(conn, "SELECT * FROM asks WHERE kept_at IS NOT NULL ORDER BY created_at DESC")]
    return {"live": live, "kept": kept, "why_not": why_not(conn, now), "max_lines": MAX_LINES, "live_minutes": LIVE_S // 60}


def get(conn, ask_id: str) -> dict:
    with _LOCK:
        if ask_id in _LIVE:
            return view(_LIVE[ask_id])
    r = row(conn, "SELECT * FROM asks WHERE id=? AND kept_at IS NOT NULL", ask_id)
    if not r:
        raise AskError("no such answer (one not kept is gone after "
                       f"{LIVE_S // 60} minutes, or when the lab restarts)", 404)
    return _kept(r)


def keep(conn, ask_id: str) -> dict:
    """The owner keeps it: the question, the answer and what was sent are stored, until they delete it."""
    with _LOCK:
        live = _LIVE.get(ask_id)
    if not live or live["status"] != "done":
        raise AskError("only an answer still on the page can be kept", 404)
    with tx(conn):
        if not row(conn, "SELECT 1 FROM asks WHERE id=?", ask_id):
            raise AskError("a conversation it read was deleted", 404)
        conn.execute("UPDATE asks SET kept_at=?, question=?, answer=? WHERE id=?",
                     (time.time(), live["question"], json.dumps({"answer": live["answer"], "sent": live["sent"]}), ask_id))
    with _LOCK:
        _LIVE.pop(ask_id, None)
    return get(conn, ask_id)


def delete(conn, ask_id: str) -> None:
    """Gone: its words from memory and the database. The row stays, without them, for the day's count."""
    with _LOCK:
        _LIVE.pop(ask_id, None)
    _clear(conn, [ask_id])


def _clear(conn, ids: list[str]) -> None:
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        marks = ",".join("?" * len(part))
        conn.execute(f"UPDATE asks SET kept_at=NULL, question=NULL, answer=NULL WHERE id IN ({marks})", part)
        conn.execute(f"DELETE FROM ask_sources WHERE ask_id IN ({marks})", part)


def drop_for(conn, session_ids: list[str]) -> int:
    """Every answer that read any of these conversations is deleted (a delete, or a Forget), kept or not."""
    if not session_ids:
        return 0
    gone = set(session_ids)
    with _LOCK:
        live = [k for k, v in _LIVE.items() if v["sources"] & gone]
        for k in live:
            del _LIVE[k]
    marks = ",".join("?" * len(session_ids))
    ids = [r["ask_id"] for r in rows(conn, f"SELECT DISTINCT ask_id FROM ask_sources WHERE session_id IN ({marks})", *session_ids)]
    _clear(conn, ids)
    return len(set(ids) | set(live))


def ask_in_background(db, ask_id: str) -> None:
    """The server's: the call off the request (it takes a minute or two), in this process, since the answer lives here."""
    def go() -> None:
        try:
            run(db(), ask_id)
        except AskError:
            pass
    threading.Thread(target=go, daemon=True, name="octo-ask").start()
