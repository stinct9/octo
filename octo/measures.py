"""The owner's numbers that a focus or a coach note can be tracked with (6 Oct, docs/coach-plan.md §4.3).

- fillers: per 100 of their words, with their own list (octo/fillers.py, its measuring stick and band);
- questions: the questions they ask, per 10 minutes of talk (everyone's);
- talk_share: their share of the talk;
- talked_over: times they started while someone was talking and they stopped, per 10 minutes of talk;
- pauses, restarts, hedges, pace, longest: the delivery measures (octo/delivery.py, B10, 15 Oct), on the
  fillers' measuring stick;
- patience, turns, their_longest: listening (octo/delivery.py, B13, 19 Oct), on the same stick;
- pitch_range, loudness, pushed_pitch, pushed_loudness: voice (octo/tone.py, B27, 21 Oct), on the same stick.

The last three come from the numbers stage (questions from punctuation, talking over from turn edges:
both approximate, as the numbers table says) and the speakers the owner confirmed. Each is pooled over
conversations, never averaged per conversation, and the counts carry a rough 95% band for ordinary
variation (Poisson, as for fillers). Read-time only: nothing here re-runs anything.
"""

from __future__ import annotations

import math
from datetime import datetime

from .db import load_json, row, rows

DELIVERY = ("pauses", "restarts", "hedges", "pace", "longest")
LISTENING = ("patience", "turns", "their_longest")
VOICE = ("pitch_range", "loudness", "pushed_pitch", "pushed_loudness")
MEASURES = ("fillers", "questions", "talk_share", "talked_over", *DELIVERY, *LISTENING, *VOICE)
LABEL = {"fillers": "Fillers per 100 words", "questions": "Questions you ask per 10 minutes",
         "talk_share": "Your share of the talk", "talked_over": "Times you talked over someone per 10 minutes",
         "pauses": "Hesitation pauses per 100 words", "restarts": "Restarts per 100 words", "hedges": "Hedges per 100 words",
         "pace": "Words per minute while you speak", "longest": "Longest stretch without a pause, in seconds",
         "patience": "Seconds you wait before taking the turn", "turns": "Times the talk changes hands per minute",
         "their_longest": "The others' longest stretch before someone took the floor, in seconds",
         "pitch_range": "Your pitch range, in semitones", "loudness": "Your level against the others', in dB",
         "pushed_pitch": "Pitch range when pushed back, against the rest, in semitones",
         "pushed_loudness": "Level when pushed back, against the rest, in dB"}
BEFORE_MIN = 60.0  # "before" pools about this many minutes of talk before the moment (fillers: ~2,000 words)


def _one(conn, s: dict) -> dict | None:
    """One conversation's record (s: a sessions row with its recording's started_at), or None when
    the owner isn't confirmed in it or its numbers aren't ready."""
    from .runs import current
    from .sessions import speakers
    sp = speakers(conn, s["id"])
    mine = {x["letter"] for x in sp if x["self"]}
    num = current(conn, s["id"], "numbers")
    if not mine or not num or num["status"] != "done" or num["stale"]:
        return None
    nums = (load_json(num["output"]) or {}).get("speakers", {})
    talk = sum(x["talk_s"] for x in sp)
    if talk <= 0:
        return None
    return {"session_id": s["id"], "at": s["started_at"] + s["start_s"] if s["started_at"] else None, "minutes": talk / 60,
            "share": sum(x["talk_s"] for x in sp if x["self"]) / talk,
            "questions": sum(nums[k]["questions"] for k in mine if k in nums),
            "talked_over": sum(nums[k]["interrupted_others"] for k in mine if k in nums),
            "alone": len(sp) == len(mine)}


def conversations(conn) -> list[dict]:
    """Every active conversation since the lab began with the owner confirmed in it, oldest first: when,
    minutes of talk (everyone), their share, their questions and how often they talked over someone."""
    from .fillers import LAB_START
    out = []
    for s in rows(conn, "SELECT s.id, s.start_s, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                        "WHERE s.status='active' AND r.started_at IS NOT NULL"):
        if datetime.fromtimestamp(s["started_at"] + s["start_s"]).date() < LAB_START:
            continue
        if c := _one(conn, s):
            out.append(c)
    out.sort(key=lambda c: c["at"])
    return out


def _band(count: int, per: float, scale: float) -> tuple[float, float]:
    lo, hi = max(0.0, count - 1.96 * math.sqrt(count)), count + 1.96 * math.sqrt(count + 1)
    return round(scale * lo / per, 2), round(scale * hi / per, 2)


def pooled(cs: list[dict], measure: str) -> dict | None:
    """One number over these conversations together, with its band where it's a count."""
    if not cs:
        return None
    if measure == "talk_share":
        talk = sum(c["minutes"] for c in cs)
        mine = sum(c["minutes"] * c["share"] for c in cs)
        return {"value": round(mine / talk, 3), "lo": None, "hi": None, "conversations": len(cs), "minutes": round(talk, 1)}
    if measure in ("questions", "talked_over"):
        # Talking over someone needs someone else: conversations with only the owner in them don't count.
        cs = [c for c in cs if not (measure == "talked_over" and c["alone"])]
        talk = sum(c["minutes"] for c in cs)
        if talk <= 0:
            return None
        n = sum(c[measure] for c in cs)
        lo, hi = _band(n, talk, 10.0)
        return {"value": round(10 * n / talk, 2), "lo": lo, "hi": hi, "conversations": len(cs), "minutes": round(talk, 1),
                "count": n}
    raise ValueError(measure)


def _last_minutes(cs: list[dict], n: float = BEFORE_MIN) -> list[dict]:
    got, total = [], 0.0
    for c in reversed(cs):
        got.append(c)
        total += c["minutes"]
        if total >= n:
            break
    return got[::-1]


def since_before(conn, measure: str, started_at: float, ended_at: float | None = None) -> dict:
    """The number since a moment (a focus started, a coach note), against about an hour of talk
    (fillers: ~2,000 of his words) just before it. Same shape for every measure."""
    if measure == "fillers":
        from .fillers import focus_numbers
        got = focus_numbers(conn, started_at, ended_at)
        as_value = lambda p: p and {"value": p["rate"], "lo": p["lo"], "hi": p["hi"], "conversations": p["conversations"],
                                    "words": p["words"]}
        return {"measure": measure, "since": as_value(got["since"]), "before": as_value(got["before"])}
    if measure in DELIVERY or measure in LISTENING or measure in VOICE:
        from .delivery import focus_numbers as delivery_numbers
        return {"measure": measure, **delivery_numbers(conn, measure, started_at, ended_at)}
    cs = conversations(conn)
    since = [c for c in cs if c["at"] >= started_at and (ended_at is None or c["at"] < ended_at)]
    before = _last_minutes([c for c in cs if c["at"] < started_at])
    return {"measure": measure, "since": pooled(since, measure), "before": pooled(before, measure)}


def for_session(conn, measure: str, session_id: str) -> float | None:
    """This conversation's own number, for the focus check on its page."""
    if measure == "fillers":
        from .fillers import conversation, phrases
        s = row(conn, "SELECT s.*, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?",
                session_id)
        c = conversation(conn, s, phrases(conn)) if s else None
        return c["rate"] if c and c["on_line"] else None
    if measure in DELIVERY or measure in LISTENING or measure in VOICE:
        from .delivery import number
        return number(conn, measure, session_id)
    s = row(conn, "SELECT s.id, s.start_s, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                  "WHERE s.id=?", session_id)
    c = _one(conn, s) if s else None
    p = pooled([c], measure) if c else None
    return p["value"] if p else None
