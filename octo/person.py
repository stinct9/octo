"""A page per person, for before you talk (docs/coach-plan.md §4.4): what the lab already holds about
the owner and one person, read from what's stored. No model call, nothing re-runs.

- the conversations with them, newest first (when, how long, who else, what happened in a line);
- open steps from those reviews (home's list, only these);
- what the reviews read in them ("Reading the others" items about them, newest first, as written;
  an item the owner marked not right is left out, one they marked partly right says so);
- the owner's numbers with them against everyone: share of the talk, questions and talking over per 10
  minutes (octo/measures.py), fillers per 100 words (octo/fillers.py), each per conversation too; and listening
  (B13): the owner's patience after them and their longest stretch, against everyone (octo/delivery.py);
- the focus they're practising, and where the coach's latest note saw its theme with them.

"With them" means they're a confirmed speaker in the conversation's current speakers.
"""

from __future__ import annotations

import statistics
from datetime import datetime

from . import delivery as D
from . import fillers as FL
from . import focus as F
from . import intention as IN
from . import measures as M
from . import review as R
from .db import load_json, row, rows

LAST = 12  # conversations listed; the numbers use them all
MIN_WAITS = 5  # times the owner took the turn after them, before patience with them is shown


def with_them(conn, person_id: str) -> list[dict]:
    """Active conversations they're confirmed in (current speakers), newest first."""
    got = rows(conn, "SELECT DISTINCT s.id, s.start_s, s.end_s, s.reviewed_at, r.started_at, r.created_at AS added FROM session_speakers ss "
                     "JOIN sessions s ON s.id = ss.session_id JOIN recordings r ON r.id = s.recording_id "
                     "JOIN stage_runs dz ON dz.id = ss.diarize_run AND dz.is_current=1 "
                     "WHERE ss.person_id=? AND ss.state IN ('confirmed', 'auto') AND s.status='active'", person_id)
    for s in got:
        s["at"] = s["started_at"] + s["start_s"] if s["started_at"] else None
    got.sort(key=lambda s: -(s["at"] or s["added"] or 0))
    return got


def _first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    for end in (". ", "? ", "! "):
        i = text.find(end)
        if 0 < i < 220:
            return text[:i + 1]
    return text if len(text) <= 220 else text[:219].rstrip() + "…"


def readings(conn, name: str, letters: dict[str, set[str]], convs: list[dict]) -> list[dict]:
    """What the reviews shown now read in them, newest first. A reading is theirs if it names them,
    or if its quotes are all their lines (a review made before a rename)."""
    from .sessions import shown_review
    out = []
    for s in convs:
        ins = shown_review(conn, s["id"])
        review = load_json(ins["output"]) if ins else None
        if not review or review.get("skipped"):
            continue
        fb = R.feedback_for(conn, ins["id"])
        mine = letters.get(s["id"], set())
        for i, r in enumerate(review.get("reading", [])):
            spoken = {q.get("speaker") for q in r.get("quotes", [])}
            if (r.get("person") or "").strip().lower() != name.lower() and not (spoken and spoken <= mine):
                continue
            f = fb.get(f"r{i}") or {}
            if f.get("verdict") == "wrong":
                continue
            q = next((q for q in r.get("quotes", []) if q.get("speaker") in mine), (r.get("quotes") or [None])[0])
            out.append({"session_id": s["id"], "insight_run": ins["id"], "item": f"r{i}", "at": s["at"],
                        "headline": r.get("headline", ""), "observation": r.get("observation", ""),
                        "quote": q and {"text": q["text"], "start": q["start"], "line": q.get("line")},
                        "partly": f.get("verdict") == "partly", "note": f.get("note") if f.get("verdict") == "partly" else None,
                        "new": f.get("novelty") == "new"})
    return out


def readings_of(conn, person_id: str) -> list[dict]:
    """Their readings as their page shows them, on their own (B26: all a rehearsal's model is told of them)."""
    from .sessions import speakers
    p = row(conn, "SELECT name, is_self FROM people WHERE id=?", person_id)
    if not p or p["is_self"]:
        return []
    convs = with_them(conn, person_id)
    letters = {s["id"]: {x["letter"] for x in speakers(conn, s["id"]) if x["person_id"] == person_id and x["state"] in ("confirmed", "auto")}
               for s in convs}
    return readings(conn, p["name"], letters, convs)


def numbers(conn, ids: set[str]) -> dict:
    """The owner with them against everyone, pooled, and per conversation with them (oldest first)."""
    allc = M.conversations(conn)
    mine = [c for c in allc if c["session_id"] in ids]
    phr = FL.phrases(conn)
    on = [c for c in FL.all_conversations(conn, phr) if c["on_line"]]
    fil_mine = [c for c in on if c["session_id"] in ids]
    words = sum(c["words"] for c in fil_mine)
    out = {m: {"with": M.pooled(mine, m), "everyone": M.pooled(allc, m)} for m in ("talk_share", "questions", "talked_over")}
    as_value = lambda p: p and {"value": p["rate"], "lo": p["lo"], "hi": p["hi"], "conversations": p["conversations"], "words": p["words"]}
    out["fillers"] = {"with": as_value(FL.pooled(fil_mine)) if words >= FL.MIN_GROUP_WORDS else None,
                      "everyone": as_value(FL.pooled(on)), "words_with": words, "min_words": FL.MIN_GROUP_WORDS}
    rate = {c["session_id"]: c["rate"] for c in fil_mine}
    out["each"] = [{"session_id": c["session_id"], "at": c["at"], "share": round(c["share"], 3),
                    "questions": round(10 * c["questions"] / c["minutes"], 1) if c["minutes"] else None,
                    "talked_over": round(10 * c["talked_over"] / c["minutes"], 1) if c["minutes"] else None,
                    "fillers": rate.get(c["session_id"])} for c in mine]
    return out


def listening(conn, letters: dict[str, set[str]]) -> dict:
    """Patience after them and their longest stretch, against everyone, on the fillers' measuring stick. Medians:
    of every wait, and of each conversation's longest (theirs, or anyone else's)."""
    on = [c for c in D.all_conversations(conn) if c["on_line"] and c["gaps"] is not None]
    med = lambda xs, n: {"value": round(statistics.median(xs), 2), "lo": None, "hi": None, "conversations": n} if xs else None
    mine = [c for c in on if letters.get(c["session_id"])]
    waits = [g for c in mine for k, g in c["gaps"] if k in letters[c["session_id"]]]
    theirs = [max(v for k, v in c["their_longest"].items() if k in letters[c["session_id"]])
              for c in mine if set(c["their_longest"]) & letters[c["session_id"]]]
    return {"patience": {"with": med(waits, len(mine)) if len(waits) >= MIN_WAITS else None,
                         "everyone": med([g for c in on for _, g in c["gaps"]], sum(1 for c in on if c["gaps"])), "waits_with": len(waits), "min_waits": MIN_WAITS},
            "their_longest": {"with": med(theirs, len(theirs)),
                              "everyone": med([v for c in on if (v := D.value(c, "their_longest")) is not None], len(on))}}


def _coach(conn, ids: set[str]) -> dict | None:
    """Where the coach's latest note saw what to work on with them."""
    n = row(conn, "SELECT id, created_at, output FROM coach_notes WHERE status='done' ORDER BY created_at DESC LIMIT 1")
    work = (load_json(n["output"]) or {}).get("work_on") if n else None
    if not work:
        return None
    moments = [e for e in work["evidence"] if e["session_id"] in ids]
    return {"note_id": n["id"], "at": n["created_at"], "theme": work["theme"], "moments": len(moments),
            "try": work.get("try")} if moments else None


def page(conn, person_id: str) -> dict | None:
    """Everything the person page shows, or None if there's no such person."""
    from .sessions import open_tasks, shown_review, speakers, todo
    p = row(conn, "SELECT * FROM people WHERE id=?", person_id)
    if not p:
        return None
    convs = with_them(conn, person_id)
    ids = {s["id"] for s in convs}
    letters, listed = {}, []
    for k, s in enumerate(convs):
        sp = speakers(conn, s["id"])
        letters[s["id"]] = {x["letter"] for x in sp if x["person_id"] == person_id and x["state"] in ("confirmed", "auto")}
        if k >= LAST:
            continue
        ins = shown_review(conn, s["id"])
        review = load_json(ins["output"]) if ins else None
        one = (review or {}).get("one_thing") or {}
        listed.append({"session_id": s["id"], "at": s["at"], "minutes": round((s["end_s"] - s["start_s"]) / 60),
                       "others": [x["name"] for x in sp if not x["self"] and x["person_id"] != person_id and x["name"]
                                  and x["state"] in ("confirmed", "auto")],
                       "summary": _first_sentence((review or {}).get("summary") or "") if review and not review.get("skipped") else "",
                       "one_thing": one.get("headline"), "todo": todo(conn, s["id"])})
    steps = [t for t in open_tasks(conn) if t["session_id"] in ids]
    since = datetime.fromtimestamp(convs[-1]["at"]).date().isoformat() if convs and convs[-1]["at"] else None
    return {"id": p["id"], "name": p["name"], "self": bool(p["is_self"]),
            "conversations": listed, "count": len(convs), "first_at": since,
            "last_at": convs[0]["at"] if convs else None,
            "steps": steps, "readings": readings(conn, p["name"], letters, convs) if not p["is_self"] else [],
            "numbers": numbers(conn, ids) | listening(conn, letters), "focus": F.view(conn, F.active(conn)), "coach": _coach(conn, ids),
            "intention": IN.open_for(conn, person_id), "last_intention": _last_intention(conn, person_id),
            "prepared_at": _prepared_at(conn, person_id)}


def _prepared_at(conn, person_id: str) -> float | None:
    """B36: when the plan for the next conversation with them was made, if one is ready."""
    from .prepare import _done, ready
    return _done(conn, person_id)["created_at"] if ready(conn, person_id) else None


def _last_intention(conn, person_id: str) -> dict | None:
    """B15: the last intention a conversation with them took, and what its review said of it."""
    from .sessions import shown_review
    got = IN.last_taken(conn, person_id)
    if not got:
        return None
    ins = shown_review(conn, got["session_id"])
    said = next((i for i in ((load_json(ins["output"]) or {}).get("intention") or [] if ins else []) if i["text"] == got["text"]), None)
    return {**got, "happened": said and said["happened"], "reviewed": bool(ins)}
