"""Talk a conversation through (B31, docs/backlog.md; Jay, 28 Oct): after its review, the owner asks about it in their
own words ("what did their answer mean?", "how could I have asked that?") and the review's model answers, from the lines.

- **One thread per conversation**, kept (`talks`), continued across visits; Start over clears it. Only once the
  conversation has a review shown, not skipped: its transcript may go to the model only then (J2).
- **What leaves the Mac:** what the review sends (the conversation's lines as the page reads them, names, the
  principles and "How the review applies it"), plus the review's own text, the owner's reflection and marks, and the
  thread. One conversation, only when the owner asks.
- **Moments checked in code** (insight.verify_quote) against the lines, or dropped and counted.
- **Calls:** one per message, at the owner's effort for talking (Settings → Talk it through; medium until changed,
  Jay 29 Oct: high took a minute or two), in a thread the server starts (a minute or two: the phone's web
  view may not hold a request that long); counted in the day's (pipeline._calls_since); a usage-limit failure is kept
  (error, error_at) so Claude's steps wait for its reset. The state is in the row, so a restart loses nothing: a reply
  still running after STOPPED_AFTER_S reads as stopped and can be asked again.
- **Deleted** with the conversation (privacy._derived) and when anyone in it is forgotten (voice.forget).
"""

from __future__ import annotations

import json
import threading
import time

from . import corrections as C
from . import insight as IN
from . import owner as O
from . import review_any as RA
from . import sessions as SV
from .db import config, load_json, row, rows, tx
from .rehearse import _ask, _owner, _reader_of
from .rehearse import why_not as model_why_not

MAX_LINE = 2000          # characters of one of the owner's messages
MAX_TURNS = 30           # of the owner's, per conversation: each is a call that resends the whole transcript
STOPPED_AFTER_S = 1500   # a reply still "running" after this was stopped (a restart)


class TalkError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status  # 404 no such conversation · 409 a reply is being asked · 422 can't now · 502 the call failed


def _talk(conn, sid: str) -> dict | None:
    t = row(conn, "SELECT * FROM talks WHERE session_id=?", sid)
    if t:
        t["turns"] = load_json(t["turns"]) or []
    return t


def _review(conn, sid: str) -> dict | None:
    ins = SV.shown_review(conn, sid)
    out = load_json(ins["output"]) if ins else None
    return out if out and not out.get("skipped") else None


def _stopped(conn, now: float) -> None:
    conn.execute("UPDATE talks SET status='idle', error=?, error_at=? WHERE status='running' AND updated_at < ?",
                 ("it stopped before it finished (the lab may have restarted): ask again", now, now - STOPPED_AFTER_S))


def why_not(conn, sid: str, now: float | None = None) -> str | None:
    """Why the owner can't ask about this conversation right now, or None."""
    now = time.time() if now is None else now
    _stopped(conn, now)
    if not _review(conn, sid):
        return "it can be talked through once it has its review"
    if row(conn, "SELECT 1 FROM talks WHERE session_id=? AND status='running'", sid):
        return "a reply is being written"
    return model_why_not(conn, now)


# ---- what the model reads ---------------------------------------------------------------------------

def lines(conn, sid: str) -> tuple[list[dict], dict[str, dict]]:
    """The lines as the page reads them (corrections, left-out speakers out, a split's cut) and who each speaker is;
    for one discussion in parts (B35), every part's, as its review read them."""
    from .parts import whole_lines
    if whole := whole_lines(conn, sid):
        return whole
    sess = row(conn, "SELECT * FROM sessions WHERE id=?", sid)
    dz, out, fixes = SV.diarized(conn, sid)
    if not sess or not dz:
        return [], {}
    out = C.without(SV.trimmed(conn, sess, out), C.left_out(load_json(dz["output"]), fixes))
    return out["lines"], {s["letter"]: s for s in SV.speakers(conn, sid)}


def review_text(rv: dict) -> str:
    """The review as the page shows it, in words (its quotes are in the transcript already)."""
    parts = [f"What happened: {rv.get('summary') or ''}"]
    if ot := rv.get("one_thing"):
        parts.append(f"The one thing: {ot['headline']}. {ot.get('why') or ''}")
    parts += [f"Reading {x.get('person') or 'them'}: {x['headline']}. {x['observation']}" for x in rv.get("reading") or []]
    parts += [f"Between the lines: {x['headline']}. {x['observation']}" for x in rv.get("between") or []]
    parts += [f"Principle {x['principle']} ({x.get('held') or '?'}): {x['observation']}" for x in rv.get("conduct") or []]
    parts += [f"What to do: {x.get('title') or ''}. {x.get('detail') or ''}" for x in rv.get("next") or []]
    if rv.get("limits"):
        parts.append(f"Its limits: {rv['limits']}")
    return "\n".join(parts)


def system_prompt(owner: str, profile: dict, rules: str) -> str:
    p = O.forms(profile)
    they, them, their, themself, s = p["they"], p["them"], p["their"], p["themself"], p["s"]
    about = (profile.get("about") or "").strip().rstrip(".")
    never = (profile.get("never") or "").strip().rstrip(".")
    how = f"How {they} want{s} reviews done, in {their} words (it holds here too):\n{rules}\n\n" if rules.strip() else ""
    return f"""You help {owner}{', ' + about if about else ''}, think through a conversation {they} had: what was said, what the others meant, how it went, and better ways {they} could have handled it or could handle the next one. {p['They']} already read its review; now {they} ask{s} about it in {their} own words, one message at a time.

{p['They']} measure{s} {their} conduct against principles {they} wrote {themself}. Advice counts only when it is consistent with them{', never ' + never if never else ''}: never suggest winning the room, positioning {themself}, or taking credit. If {they} ask for that, say plainly that it conflicts with {their} principles and offer what does fit. Use only the principles as {they} wrote them; add no other teachings.

{how}How to answer:
- Answer the question {they} asked, directly, then what helps beyond it. Plain, direct English to {them} as "you"; no flattery, no padding, no hedging everything.
- Ground each point in the lines. Keep what was said apart from what you infer, and say which is which ("that's an inference").
- You have the words only: you can't hear tone, pauses or faces. Speakers were matched by voice and words by a model, so a label or a word can be wrong; say so where it matters, and say what was probably meant.
- When {they} ask how {they} could have said something, give the words {they} could have used, short enough to say in the room.
- Disagree with {them}, or with the review, when the lines don't support it.
- Write plain text: short paragraphs, and "- " for a list. No headings, no bold, no tables.
- moments: the lines your answer leans on most, at most five, each with its line number and words copied exactly from that line ("..." may join fragments of one line, in order). They are checked in code; one that doesn't match is dropped.

{IN.MATERIAL}"""


def user_prompt(conn, sid: str, owner: str, profile: dict, items: list[dict], ls: list[dict], sp: dict[str, dict],
                turns: list[dict], rv: dict) -> str:
    from .intention import reflection
    from .marks import review_input
    their = O.forms(profile)["their"]
    sess = row(conn, "SELECT r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", sid)
    started = sess["started_at"] if sess else None
    names = {L: owner if s["self"] else SV.display(s) for L, s in sp.items()}
    when = RA.when_text(started)
    ps = "\n\n".join(f"{x['n']}. {x['text']}" for x in items)
    refl = reflection(conn, sid)
    marks = review_input(conn, sid)
    parts = [f"This conversation was on {when}." if when else "",
             f"{owner}'s principles, in {their} words:\n\n{ps}",
             f"Its review, as {owner} read it:\n{review_text(rv)}",
             f"How it went, in {owner}'s own words (written after it): \"{refl['text']}\"" if refl else "",
             RA.marks_text(owner, profile, marks, started).strip() if marks else "",
             f"Transcript, one line per utterance ([line] time speaker: words):\n{IN.transcript(ls, names)}",
             "The talk so far:\n" + "\n".join(f"{owner if t['by'] == 'me' else 'You'}: {t['text']}" for t in turns),
             f"Answer {owner}'s last message."]
    return "\n\n".join(p for p in parts if p)


SCHEMA = {"type": "object", "properties": {
    "reply": {"type": "string"},
    "moments": {"type": "array", "items": {"type": "object", "properties": {
        "line": {"type": "integer"}, "text": {"type": "string"}}, "required": ["line", "text"]}}},
    "required": ["reply", "moments"]}


def check(raw: dict, ls: list[dict], sp: dict[str, dict], owner: str) -> tuple[list[dict], int]:
    """The moments that really are in their lines, each with who said it; and how many were dropped."""
    by_id = {ln["id"]: ln for ln in ls}
    got, dropped = [], 0
    for q in raw.get("moments") or []:
        v = IN.verify_quote({"line": q.get("line"), "text": q.get("text", "")}, by_id)
        if not v:
            dropped += 1
            continue
        s = sp.get(v["speaker"]) or {}
        got.append({"line": v["line"], "start": v["start"], "text": v["text"], "uncertain": v["uncertain"],
                    "who": owner if s.get("self") else (SV.display(s) if s else "Unknown speaker"), "self": bool(s.get("self"))})
    return got, dropped


# ---- the owner's message, the reply ----------------------------------------------------------------

def say(conn, sid: str, text: str | None, now: float | None = None) -> dict:
    """The owner's message (or none: ask again after a failed reply); the reply is then run(). Returns the view."""
    now = time.time() if now is None else now
    if not row(conn, "SELECT 1 FROM sessions WHERE id=? AND status='active'", sid):
        raise TalkError("no such conversation", 404)
    text = (text or "").strip()
    if len(text) > MAX_LINE:
        raise TalkError(f"at most {MAX_LINE} characters")
    with tx(conn):
        if why := why_not(conn, sid, now):
            raise TalkError(why, 409 if why.startswith("a reply") else 422)
        t = _talk(conn, sid) or {"turns": []}
        turns = t["turns"]
        if text:
            if sum(1 for x in turns if x["by"] == "me") >= MAX_TURNS:
                raise TalkError(f"that's {MAX_TURNS} messages on this conversation: Start over to go on")
            turns.append({"by": "me", "text": text, "at": now})
        elif not turns or turns[-1]["by"] != "me":
            raise TalkError("what you'd like to talk through, in a few words")
        conn.execute("INSERT INTO talks (session_id, turns, status, created_at, updated_at) VALUES (?, ?, 'running', ?, ?) "
                     "ON CONFLICT (session_id) DO UPDATE SET turns=excluded.turns, status='running', updated_at=excluded.updated_at, "
                     "error=NULL, error_at=NULL", (sid, json.dumps(turns), now, now))
    return view(conn, sid)


EFFORTS = ("low", "medium", "high")


def effort(conn) -> str:
    """How hard the model thinks before each reply (app_config `talk_effort`): less is quicker, more goes deeper."""
    e = config(conn, "talk_effort", "medium")
    return e if e in EFFORTS else "medium"


def set_effort(conn, e: str) -> dict:
    if e not in EFFORTS:
        raise TalkError(f"one of {', '.join(EFFORTS)}")
    conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('talk_effort', ?, strftime('%s','now')) "
                 "ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (json.dumps(e),))
    return {"effort": effort(conn)}


def run(conn, sid: str) -> dict:
    """The reply to the owner's last message: one call, its moments checked. A failure is kept and shown."""
    from .rubric import text as rubric_text
    from .settings import reader
    t = _talk(conn, sid)
    if not t or t["status"] != "running":
        raise TalkError("no message waiting for a reply", 404)
    rv = _review(conn, sid)
    who = reader(conn)
    rubric = rubric_text()
    owner, profile = _owner(conn)
    ls, sp = lines(conn, sid)
    try:
        if not rv or not who:
            raise TalkError("its review, or the model for reviews, went while it was asked")
        prompt = user_prompt(conn, sid, owner, profile, IN.principles(rubric), ls, sp, t["turns"], rv)
        raw, usage = _ask(who, effort(conn), system_prompt(owner, profile, IN.review_rules(rubric)), prompt, SCHEMA)
        moments, dropped = check(raw, ls, sp, owner)
    except Exception as e:  # noqa: BLE001 - kept, so a usage limit holds Claude's steps; shown on the page
        msg = str(e)[:2000] or type(e).__name__
        conn.execute("UPDATE talks SET status='idle', error=?, error_at=?, updated_at=? WHERE session_id=?",
                     (msg, time.time(), time.time(), sid))
        return view(conn, sid)
    with tx(conn):  # cleared, or the conversation deleted, while it was asked: nothing to add to
        cur = _talk(conn, sid)
        if cur and cur["status"] == "running":
            cur["turns"].append({"by": "model", "text": (raw.get("reply") or "").strip() or "…", "at": time.time(),
                                 "moments": moments, "dropped": dropped, "reader": _reader_of(who), "usage": usage or {}})
            conn.execute("UPDATE talks SET turns=?, status='idle', error=NULL, error_at=NULL, updated_at=? WHERE session_id=?",
                         (json.dumps(cur["turns"]), time.time(), sid))
    return view(conn, sid)


def run_in_background(db, sid: str) -> None:
    """The server's: the call off the request, in a thread of this process (the row says it's running)."""
    def go() -> None:
        try:
            run(db(), sid)
        except TalkError:
            pass
    threading.Thread(target=go, daemon=True, name="octo-talk").start()


# ---- the page, the count, deleting ----------------------------------------------------------------------

def view(conn, sid: str) -> dict:
    from .pipeline import limit_reset
    why = why_not(conn, sid)
    t = _talk(conn, sid) or {"turns": [], "status": "idle", "error": None, "error_at": None}
    return {"turns": [{k: x[k] for k in ("by", "text", "at", "moments", "dropped") if k in x} for x in t["turns"]],
            "running": t["status"] == "running", "error": t["error"], "waiting_until": limit_reset(t["error"], t["error_at"]),
            "why_not": None if why == "a reply is being written" else why, "max_turns": MAX_TURNS, "max_line": MAX_LINE}


def clear(conn, sid: str) -> dict:
    """Start over: the thread is gone (a reply being asked lands nowhere)."""
    conn.execute("DELETE FROM talks WHERE session_id=?", (sid,))
    return view(conn, sid)


def calls_since(conn, since: float, which) -> int:
    """Its calls for the day's count (pipeline._calls_since): each reply made since `since`."""
    return sum(1 for r in rows(conn, "SELECT turns FROM talks WHERE updated_at >= ?", since)
               for x in load_json(r["turns"]) or [] if x.get("reader") and x["at"] >= since and which(x["reader"]))


def drop_for(conn, session_ids: list[str]) -> None:
    """Deleted with a conversation, or when someone in it is forgotten: the replies name and quote them."""
    for i in range(0, len(session_ids), 500):
        part = session_ids[i:i + 500]
        conn.execute(f"DELETE FROM talks WHERE session_id IN ({','.join('?' * len(part))})", part)
