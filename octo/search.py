"""Search across conversations (B12, 19 Oct): the transcript lines and the review text, read at search time.

No index: an index would be a second copy of every transcript that Delete, Forget, Leave out and the backups
would all have to reach. Each conversation's current diarize run is read with the owner's corrections applied,
as the conversation page reads it (octo/sessions.py); set-aside conversations and speakers left out are skipped.
No model call, nothing leaves the Mac. Each conversation's readable lines are kept in memory only (fillers.memoized),
made again when what they're made from changes, and dropped by Delete and Forget (fillers.forget).
"""

from __future__ import annotations

import re
import time

from . import corrections as C
from . import fillers as FL
from . import sessions as SV
from .db import load_json, rows
from .pipeline import current

WORDS = 6  # either side of the match
LIMIT = 200


def search(conn, q: str, person: str | None = None, who: str | None = None) -> dict:
    """Lines and review text containing `q` (any case), newest conversation first, then in order.
    `person`: lines said by that person (people.id), and review text of conversations they're in.
    `who`: "you" or "them", the owner's lines or everyone else's; review text isn't anyone's, so it's left out."""
    t0 = time.monotonic()
    needle = q.strip().casefold()
    out: list[dict] = []
    total = scanned = 0
    if len(needle) < 2:
        return {"results": [], "total": 0, "scanned": 0, "ms": 0}
    # B35: a line in a later part of one discussion opens on the whole's page, numbered as its review numbers it.
    from .parts import PART_LINES, groups
    whole = {s["id"]: (g[0]["id"], k * PART_LINES) for g in groups(conn) for k, s in enumerate(g)}
    for sess, lines_out in readable(conn):
        sid = sess["id"]
        scanned += 1
        line_hits = [(ln, m) for ln in lines_out["lines"] if (m := _find(ln["text"], needle))]
        review = SV.shown_review(conn, sid)
        review_hits = [] if who or not review else _review_hits(load_json(review["output"]) or {}, needle)
        if not line_hits and not review_hits:
            continue
        sp = {s["letter"]: s for s in SV.speakers(conn, sid)}  # read only for a conversation with a hit
        at = sess["started_at"] + sess["start_s"] if sess["started_at"] else None
        conv = {"session_id": sid, "at": at, "with": [SV.display(s) for s in sp.values() if s["name"] and not s["self"]]}
        if person and not any(s["person_id"] == person for s in sp.values()):
            review_hits = []
        for ln, m in line_hits:
            s = sp.get(ln["speaker"]) or {}
            if (person and s.get("person_id") != person) or (who == "you" and not s.get("self")) \
                    or (who == "them" and s.get("self")):
                continue
            total += 1
            if len(out) < LIMIT:
                head, base = whole.get(sid, (sid, 0))
                out.append({"kind": "line", **conv, "session_id": head, "line": base + ln["id"], "start": ln["start"],
                            "who": SV.display(s) if s else "Unknown", "self": bool(s.get("self")), **m})
        for section, line, m in review_hits:
            total += 1
            if len(out) < LIMIT:
                out.append({"kind": "review", **conv, "line": line, "who": section, "self": False, **m})
    return {"results": out, "total": total, "scanned": scanned, "ms": round((time.monotonic() - t0) * 1000)}


def readable(conn):
    """Each active conversation with words, newest first, and its lines as the page reads them: corrections applied,
    speakers left out taken out. Ask (B24, octo/ask.py) reads them the same way. Kept per conversation until what
    they're made from changes (fillers.memoized): parsing every transcript was most of a search (28 Oct)."""
    for c in sorted(FL.memoized(conn, _LINES, lambda s: _readable(conn, s)), key=lambda c: -c["at"]):
        yield c["sess"], c["lines"]


def _readable(conn, sess: dict) -> dict | None:
    dz, lines_out, fixes = SV.diarized(conn, sess["id"])
    if not dz or SV.paused(current(conn, sess["id"], "transcribe"), conn):
        return None  # a paused language shows no words, and has no review
    out = C.without(SV.trimmed(conn, sess, lines_out), C.left_out(load_json(dz["output"]), fixes))
    return {"at": (sess["started_at"] or 0) + sess["start_s"], "sess": sess, "lines": {"lines": out["lines"]}}  # only what's read


_LINES = FL.memo()


def _review_hits(r: dict, needle: str) -> list[tuple[str, int | None, dict]]:
    """The one thing, the insights (readings, between the lines, principles) and the steps: (section, its first
    quote's line, the match)."""
    if r.get("skipped"):
        return []
    parts = [("The one thing", r.get("one_thing"), ("headline", "why"))]
    parts += [("Insight", x, ("headline", "observation")) for x in r.get("reading", []) + r.get("between", [])]
    parts += [("Insight", x, ("observation",)) for x in r.get("conduct", [])]
    parts += [("Step", x, ("title", "detail", "why")) for x in r.get("next", [])]
    hits = []
    for section, item, fields in parts:
        if not item:
            continue
        m = next((m for f in fields if (m := _find(item.get(f) or "", needle))), None)
        if m:
            hits.append((section, next((q["line"] for q in item.get("quotes") or []), None), m))
    return hits


def _find(text: str, needle: str) -> dict | None:
    """The first match and a few words either side, as before / match / after."""
    i = text.casefold().find(needle)
    if i < 0:
        return None
    j = i + len(needle)  # casefold can change a string's length (ß → ss); close enough for a snippet
    head = re.findall(r"\S+\s*", text[:i])  # a word cut by the match counts as one
    after = re.match(r"\S*(?:\s+\S+){0,%d}" % WORDS, text[j:])[0]
    return {"before": ("… " if len(head) > WORDS else "") + "".join(head[-WORDS:]), "match": text[i:j],
            "after": after + (" …" if len(after) < len(text) - j else "")}
