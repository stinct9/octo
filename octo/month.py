"""A calendar to see it all from above (B37, docs/backlog.md; Jay, 30 Oct): a month of conversations, then one day, then
the conversation page. Read from the database when asked; no model, nothing stored.

- **A month** (`month`): per local day, how many conversations, minutes talked, how many have a review shown and how
  many of those are still to answer ("What here was new to you?" not answered: `sessions.reviewed_at`), and who was in
  them; and the month in numbers. Set-aside recordings and deleted conversations don't count (only active ones).
- **Fillers** (Jay, 30 Oct): per day and per conversation, the owner's fillers per 100 of their words, pooled over the
  conversations on the fillers' line only (fillers.all_conversations, kept per conversation; Progress's rule).
- **A day** (`day`): that day's conversations as the list shows them (`sessions.summary`) with the review's one thing,
  and the day's calendar events (B23 keeps two days either side, so older days have none), as one timeline.
Days are this Mac's local days, as home's agenda reads them (events.agenda).
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

from . import fillers as FI
from . import sessions as SV
from .db import load_json, row, rows


class MonthError(ValueError):
    pass


def _when(s: dict) -> float:
    return (s["started_at"] + s["start_s"]) if s["started_at"] else s["added"]


def _active(conn, lo: float, hi: float) -> list[dict]:
    """Active conversations that began in [lo, hi), by when they were recorded (or added, for a memo with no time)."""
    got = rows(conn, "SELECT s.*, r.source, r.original, r.started_at, r.created_at AS added FROM sessions s "
                     "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active' "
                     "AND coalesce(r.started_at + s.start_s, r.created_at) >= ? AND coalesce(r.started_at + s.start_s, r.created_at) < ?",
               lo, hi)
    # B35: one discussion in parts is one conversation, on its first part's day, its length theirs together.
    from .parts import groups
    whole = {g[0]["id"]: sum(p["end_s"] - p["start_s"] for p in g) for g in groups(conn)}
    later = {p["id"] for g in groups(conn) for p in g[1:]}
    got = [{**s, "end_s": s["start_s"] + whole.get(s["id"], s["end_s"] - s["start_s"])} for s in got if s["id"] not in later]
    return sorted(got, key=_when)


def _reviewed(conn, ids: list[str]) -> dict[str, bool]:
    """For each conversation with a review shown: whether it's been answered (reviewed_at). Others aren't in it."""
    out = {}
    for sid in ids:
        ins = SV.shown_review(conn, sid)
        if ins and not (load_json(ins["output"]) or {}).get("skipped"):
            out[sid] = bool(row(conn, "SELECT reviewed_at FROM sessions WHERE id=?", sid)["reviewed_at"])
    return out


def _people(conn, ids: list[str]) -> dict[str, list[str]]:
    """Each conversation's named people, the owner left out (confirmed or settled by the lab)."""
    out: dict[str, list[str]] = {i: [] for i in ids}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in rows(conn, f"SELECT DISTINCT ss.session_id, p.name FROM session_speakers ss JOIN people p ON p.id = ss.person_id "
                            f"WHERE ss.session_id IN ({','.join('?' * len(part))}) AND p.is_self=0 AND ss.state IN ('confirmed', 'auto') "
                            f"ORDER BY p.name", *part):
            out[r["session_id"]].append(r["name"])
    return out


def _first(conn) -> str | None:
    r = row(conn, "SELECT min(coalesce(r.started_at + s.start_s, r.created_at)) t FROM sessions s "
                  "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active'")
    return date.fromtimestamp(r["t"]).isoformat() if r and r["t"] else None


def _fillers(conn) -> dict[str, dict]:
    """Each conversation on the fillers' line: its words and fillers (the owner's)."""
    return {c["session_id"]: c for c in FI.all_conversations(conn) if c["on_line"]}


def _rate(cs: list[dict]) -> dict | None:
    w, f = sum(c["words"] for c in cs), sum(c["fillers"] for c in cs)
    return {"words": w, "fillers": f, "rate": round(100 * f / w, 1)} if w else None


def month(conn, ym: str | None = None, now: float | None = None) -> dict:
    """{"month": "2026-10", "today", "first" (the lab's first conversation's day), "days": [{"date", "count", "minutes",
    "reviews", "to_answer", "people"}] for every day of the month, "totals": {...}, "prev", "next"}."""
    now = time.time() if now is None else now
    if ym is None:
        ym = date.fromtimestamp(now).strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", ym):
        raise MonthError("a month as YYYY-MM")
    start = date.fromisoformat(f"{ym}-01")
    end = (start + timedelta(days=32)).replace(day=1)
    lo, hi = datetime(start.year, start.month, 1).timestamp(), datetime(end.year, end.month, 1).timestamp()
    convs = _active(conn, lo, hi)
    ids = [s["id"] for s in convs]
    reviewed, people = _reviewed(conn, ids), _people(conn, ids)
    fill = _fillers(conn)
    days = {(start + timedelta(days=k)).isoformat(): {"count": 0, "minutes": 0.0, "reviews": 0, "to_answer": 0, "people": [],
                                                      "fillers": []} for k in range((end - start).days)}
    for s in convs:
        d = days[date.fromtimestamp(_when(s)).isoformat()]
        d["count"] += 1
        d["minutes"] += (s["end_s"] - s["start_s"]) / 60
        if s["id"] in reviewed:
            d["reviews"] += 1
            d["to_answer"] += 0 if reviewed[s["id"]] else 1
        d["people"] += [p for p in people[s["id"]] if p not in d["people"]]
        if s["id"] in fill:
            d["fillers"].append(fill[s["id"]])
    everyone = {p for s in convs for p in people[s["id"]]}
    return {"month": ym, "today": date.fromtimestamp(now).isoformat(), "first": _first(conn),
            "prev": (start - timedelta(days=1)).strftime("%Y-%m"), "next": end.strftime("%Y-%m"),
            "days": [{"date": k, **v, "minutes": round(v["minutes"]), "fillers": _rate(v["fillers"])} for k, v in days.items()],
            "totals": {"conversations": len(convs), "minutes": round(sum((s["end_s"] - s["start_s"]) / 60 for s in convs)),
                       "reviews": len(reviewed), "answered": sum(reviewed.values()), "people": len(everyone),
                       "fillers": _rate([fill[s["id"]] for s in convs if s["id"] in fill])}}


def _neighbour(conn, t: float, later: bool) -> str | None:
    """The nearest day before (or after) with a conversation."""
    op, order = (">=", "ASC") if later else ("<", "DESC")
    r = row(conn, f"SELECT coalesce(r.started_at + s.start_s, r.created_at) t FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                  f"WHERE s.status='active' AND coalesce(r.started_at + s.start_s, r.created_at) {op} ? ORDER BY t {order} LIMIT 1", t)
    return date.fromtimestamp(r["t"]).isoformat() if r else None


def day(conn, iso: str | None = None, now: float | None = None) -> dict:
    """{"date", "today", "items": [{"kind": "conversation" | "event", "at", ...}] in time order, "totals", "prev", "next",
    "events_kept": whether the calendar still holds that day}. A conversation item is the list's row plus `one_thing`;
    an event is its title, times, people and the conversation recorded during it (the most overlap), if any."""
    now = time.time() if now is None else now
    iso = iso or date.fromtimestamp(now).isoformat()
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        raise MonthError("a day as YYYY-MM-DD") from None
    lo = datetime(d.year, d.month, d.day).timestamp()
    hi = (datetime(d.year, d.month, d.day) + timedelta(days=1)).timestamp()
    convs = _active(conn, lo, hi)
    fill = _fillers(conn) if convs else {}
    items = []
    for s in convs:
        ins = SV.shown_review(conn, s["id"])
        rv = load_json(ins["output"]) if ins else None
        one = ((rv or {}).get("one_thing") or {}).get("headline") if rv and not rv.get("skipped") else None
        items.append({"kind": "conversation", "at": _when(s), **SV.summary(conn, s), "one_thing": one,
                      "fillers": _rate([fill[s["id"]]]) if s["id"] in fill else None})
    from .events import who
    span = [(it["started_at"] or it["at"], (it["started_at"] or it["at"]) + it["duration_s"], it["id"]) for it in items]
    for e in rows(conn, "SELECT id, title, start, end, invitees FROM events WHERE start < ? AND end > ? ORDER BY start", hi, lo):
        best = max(span, key=lambda c: min(c[1], e["end"]) - max(c[0], e["start"]), default=None)
        conv = best[2] if best and min(best[1], e["end"]) - max(best[0], e["start"]) > 0 else None
        items.append({"kind": "event", "at": e["start"], "id": e["id"], "title": e["title"], "start": e["start"], "end": e["end"],
                      "people": [{"name": p["name"], "person_id": p["person_id"]} for p in who(conn, load_json(e["invitees"]) or [])],
                      "conversation": conv})
    items.sort(key=lambda it: (it["at"], it["kind"] != "event"))
    today = date.fromtimestamp(now)
    return {"date": iso, "today": today.isoformat(), "items": items,
            "totals": {"conversations": len(convs), "minutes": round(sum((s["end_s"] - s["start_s"]) / 60 for s in convs)),
                       "fillers": _rate([fill[s["id"]] for s in convs if s["id"] in fill])},
            "prev": _neighbour(conn, lo, False), "next": _neighbour(conn, hi, True),
            "events_kept": abs((d - today).days) <= 2}
