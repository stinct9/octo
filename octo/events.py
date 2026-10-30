"""The owner's calendar, read only (B23, 27 Oct): one `events` table whatever the source.

Sources (Jay, 21 Oct): this Mac's accounts, read by the Octo app through EventKit and posted to the lab
(macapp/Octo.swift: the app holds the Calendars permission, so the launchd Python needs none), and calendar links
(ICS: Google's secret address, Outlook's published calendar), fetched here every few minutes. Direct sign-in to
Google and Microsoft is the next step (docs/backlog.md B23).

Two uses: a banner 10 min before an event with someone Octo knows, its name and time only (notify.py says why
that's the one banner with words in it), and an event's invitees offered as names for the speakers of a recording
it overlaps. A match of an invitee to a person is a guess until the owner names a speaker with it; then it's kept.
The link is a secret (anyone with it reads the calendar): kept in data/calendar.json (0600), never sent back.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import DATA

CONFIG = DATA / "calendar.json"  # {"links": [{"id", "url", "added_at"}], "mac": bool}
WINDOW_S = 2 * 86400  # read from 2 days back to 2 days ahead
KEEP_S = 60 * 86400  # events stay as long as the audio they could name (D5), then go
FETCH_EVERY_S = 300
BEFORE_S = 600  # the banner, 10 min before
MAX_LINKS = 5
MAX_ICS = 10_000_000
# One invite from anyone lands on the owner's calendar, so a hostile repeat (every second since 2020) must stay cheap
# (security review M2, 28 Oct): repeats faster than hourly are left out, and a link's repeats get a budget of steps.
MAX_EVENTS = 2000  # per link, as the Octo app's post
MAX_REPEATS = 50  # instances of one event in the window
MAX_STEPS = 200_000  # repeats walked, all events of one link together (dateutil walks from the first instance)
_status: dict[str, dict] = {}  # per link: last fetch, error, events (memory only)
mac_status: dict = {}  # the Octo app's last post: access, at, events (memory only)


class CalendarError(RuntimeError):
    pass


# ---- the links and settings ------------------------------------------------------------------------

def load() -> dict:
    from . import files as FL
    try:
        cfg = FL.read_json(CONFIG, {})
    except FL.Unreadable:
        cfg = {}
    return {"links": cfg.get("links") or [], "mac": bool(cfg.get("mac"))}


def _update(change) -> None:
    from . import files as FL
    with FL.locked(CONFIG):  # whole, readable by the owner only, one writer at a time (as ticktick.json)
        cfg = load()
        change(cfg)
        FL.write_private(CONFIG, json.dumps(cfg, indent=2))


def settings() -> dict:
    """What the Settings page shows: never a link itself, only its host and last four characters."""
    cfg = load()
    links = []
    for ln in cfg["links"]:
        st = _status.get(ln["id"], {})
        links.append({"id": ln["id"], "host": urllib.parse.urlsplit(ln["url"]).hostname, "ends": ln["url"][-4:],
                      "added_at": ln["added_at"], "fetched_at": st.get("at"), "events": st.get("events"),
                      "error": st.get("error")})
    return {"links": links, "mac": cfg["mac"], "mac_access": mac_status.get("access"),
            "mac_synced_at": mac_status.get("at"), "mac_events": mac_status.get("events")}


def clean_url(url: str) -> str:
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[9:]
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise CalendarError("Paste the calendar's https:// (or webcal://) address")
    return url


def add_link(conn, url: str, fetch=None, now: float | None = None) -> dict:
    """Fetch it once to check it's a calendar, then keep it and its events. Raises CalendarError."""
    url = clean_url(url)
    cfg = load()
    if len(cfg["links"]) >= MAX_LINKS:
        raise CalendarError(f"{MAX_LINKS} links at most")
    if any(ln["url"] == url for ln in cfg["links"]):
        raise CalendarError("That link is already here")
    text = (fetch or fetch_ics)(url)
    if "BEGIN:VCALENDAR" not in text:
        raise CalendarError("That address didn't answer with a calendar")
    link = {"id": hashlib.sha256(url.encode()).hexdigest()[:12], "url": url, "added_at": time.time()}
    _update(lambda c: c["links"].append(link))
    _sync_link(conn, link, text, now)
    return settings()


def remove_link(conn, link_id: str) -> dict:
    _update(lambda c: c.update(links=[ln for ln in c["links"] if ln["id"] != link_id]))
    conn.execute("DELETE FROM events WHERE source=?", (f"ics:{link_id}",))
    _status.pop(link_id, None)
    return settings()


def set_mac(conn, on: bool) -> dict:
    _update(lambda c: c.update(mac=bool(on)))
    if not on:
        conn.execute("DELETE FROM events WHERE source='mac'")
        mac_status.clear()
    return settings()


# ---- reading a calendar link (RFC 5545, the parts calendars use) -------------------------------------

def fetch_ics(url: str, timeout: float = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Octo"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(MAX_ICS + 1)
    except urllib.error.HTTPError as e:
        raise CalendarError(f"The calendar answered {e.code}: the link may have been reset") from e
    except (urllib.error.URLError, OSError) as e:
        raise CalendarError(f"The calendar couldn't be reached: {getattr(e, 'reason', e)}") from e
    if len(data) > MAX_ICS:
        raise CalendarError("That calendar is over 10 MB")
    return data.decode("utf-8", errors="replace")


def _prop(line: str) -> tuple[str, dict, str] | None:
    """NAME;PARAM=a;PARAM="b:c":value → (NAME, {PARAM: ...}, value)."""
    quoted = False
    for i, c in enumerate(line):
        if c == '"':
            quoted = not quoted
        elif c == ":" and not quoted:
            break
    else:
        return None
    parts = re.findall(r'(?:"[^"]*"|[^;])+', line[:i])
    if not parts:
        return None
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, line[i + 1:]


def _text(value: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value).strip()


def _zone(tzid: str | None):
    if tzid:
        try:
            return ZoneInfo(tzid)
        except (KeyError, ValueError, OSError):
            # shortcut: Outlook's Windows names ("India Standard Time") and VTIMEZONE blocks aren't read, so those
            # events are taken as this Mac's time; map the names if an owner abroad meets it.
            pass
    return datetime.now().astimezone().tzinfo


def _when(value: str, params: dict) -> datetime | None:
    """A date-time, aware; None for an all-day date (not a meeting anyone talks in)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return None
    try:
        dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc if value.endswith("Z") else _zone(params.get("TZID")))


def _duration(value: str) -> timedelta | None:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def _person(value: str, params: dict) -> dict:
    email = value[7:] if value.lower().startswith("mailto:") else ""
    return {"name": params.get("CN", "").strip(), "email": email.strip().lower()}


def parse_ics(text: str, lo: float, hi: float) -> list[dict]:
    """Events overlapping [lo, hi]: {"uid", "title", "start", "end", "invitees": [{"name", "email"}]}, recurring ones
    expanded (RRULE, EXDATE, a moved or cancelled instance), all-day ones left out."""
    lines = re.sub(r"\r?\n[ \t]", "", text).splitlines()
    raw, cur = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            cur = {"invitees": [], "exdates": []}
        elif line == "END:VEVENT" and cur is not None:
            raw.append(cur)
            cur = None
        elif cur is not None and (p := _prop(line)):
            name, params, value = p
            if name in ("DTSTART", "DTEND", "RECURRENCE-ID"):
                cur[name] = _when(value, params)
                cur["all_day"] = cur.get("all_day") or (name == "DTSTART" and cur[name] is None)
            elif name == "DURATION":
                cur["DURATION"] = _duration(value)
            elif name in ("SUMMARY", "UID", "STATUS", "RRULE"):
                cur[name] = _text(value) if name == "SUMMARY" else value.strip()
            elif name == "EXDATE":
                cur["exdates"] += [d.timestamp() for v in value.split(",") if (d := _when(v, params))]
            elif name in ("ATTENDEE", "ORGANIZER") and params.get("PARTSTAT", "").upper() != "DECLINED" \
                    and params.get("CUTYPE", "INDIVIDUAL").upper() == "INDIVIDUAL":
                cur["invitees"].append(_person(value, params))
    moved = {(e.get("UID"), e["RECURRENCE-ID"].timestamp()) for e in raw if e.get("RECURRENCE-ID")}
    out, steps = [], 0
    for e in raw:
        start = e.get("DTSTART")
        if e.get("all_day") or start is None:
            continue
        length = (e["DTEND"] - start) if e.get("DTEND") else (e.get("DURATION") or timedelta(0))
        starts = [start]
        if e.get("RRULE") and not e.get("RECURRENCE-ID"):
            from dateutil.rrule import rrulestr
            if re.search(r"FREQ=(SECONDLY|MINUTELY)", e["RRULE"].upper()):
                continue  # no meeting repeats that often
            try:
                rule = rrulestr(e["RRULE"], dtstart=start)
                first, last, starts = datetime.fromtimestamp(lo, timezone.utc) - length, datetime.fromtimestamp(hi, timezone.utc), []
                for s in rule:
                    steps += 1
                    if s > last or steps > MAX_STEPS or len(starts) >= MAX_REPEATS:
                        break
                    if s >= first:
                        starts.append(s)
            except (ValueError, TypeError):
                pass  # a rule dateutil can't read: its first instance only
            starts = [s for s in starts if s.timestamp() not in e["exdates"] and (e.get("UID"), s.timestamp()) not in moved]
        if e.get("STATUS", "").upper() == "CANCELLED":
            continue
        seen, invitees = set(), []
        for p in e["invitees"]:
            k = invitee_key(p)
            if k and k not in seen:
                seen.add(k)
                invitees.append(p)
        for s in starts:
            a, b = s.timestamp(), (s + length).timestamp()
            if a < hi and b > lo:
                out.append({"uid": e.get("UID") or "", "title": e.get("SUMMARY") or "", "start": a, "end": b,
                            "invitees": invitees[:200]})
    return out[:MAX_EVENTS]


# ---- storing -------------------------------------------------------------------------------------------

def store(conn, source: str, events: list[dict], lo: float, hi: float, now: float | None = None) -> int:
    """This source's events in [lo, hi] as it has them now: new and changed ones written (a banner already posted
    stays posted), ones it no longer has in the window deleted. Older ones stay until KEEP_S."""
    from .db import tx
    now = time.time() if now is None else now
    with tx(conn):
        for e in events:
            eid = hashlib.sha256(f"{source}|{e['uid']}|{int(e['start'])}".encode()).hexdigest()[:16]
            conn.execute("INSERT INTO events (id, source, title, start, end, invitees, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                         "ON CONFLICT(id) DO UPDATE SET title=excluded.title, end=excluded.end, "
                         "invitees=excluded.invitees, seen_at=excluded.seen_at",
                         (eid, source, e["title"][:300], e["start"], e["end"], json.dumps(e["invitees"][:200]), now))
        conn.execute("DELETE FROM events WHERE source=? AND start < ? AND end > ? AND seen_at < ?", (source, hi, lo, now))
        conn.execute("DELETE FROM events WHERE end < ?", (now - KEEP_S,))
    return len(events)


def _sync_link(conn, link: dict, text: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    n = store(conn, f"ics:{link['id']}", parse_ics(text, now - WINDOW_S, now + WINDOW_S), now - WINDOW_S, now + WINDOW_S, now)
    _status[link["id"]] = {"at": now, "tried": now, "events": n, "error": None}


def sync_links(conn, fetch=None, now: float | None = None) -> None:
    """Each link not fetched in the last FETCH_EVERY_S. A failure is kept for the Settings page; events stay."""
    now = time.time() if now is None else now
    for link in load()["links"]:
        st = _status.get(link["id"], {})
        if now - st.get("tried", 0) < FETCH_EVERY_S:
            continue
        try:
            _sync_link(conn, link, (fetch or fetch_ics)(link["url"]), now)
        except CalendarError as e:
            _status[link["id"]] = {**st, "error": str(e)}
        _status[link["id"]]["tried"] = now


def _s(v, n: int) -> str:
    return v[:n] if isinstance(v, str) else ""


def from_mac(conn, body: dict, now: float | None = None) -> dict:
    """The Octo app's post of this Mac's calendars (EventKit): what macOS allows, and the window's events."""
    now = time.time() if now is None else now
    access = body.get("access") if body.get("access") in ("full", "denied", "ask") else "denied"
    if not load()["mac"]:
        return {"wanted": False}
    events = []
    for e in (body.get("events") or [])[:2000]:
        try:
            a, b = float(e["start"]), float(e["end"])
        except (KeyError, TypeError, ValueError):
            continue
        invitees = [{"name": _s(p.get("name"), 100).strip(), "email": _s(p.get("email"), 200).strip().lower()}
                    for p in (e.get("invitees") or [])[:200] if isinstance(p, dict)]
        events.append({"uid": _s(e.get("id"), 300), "title": _s(e.get("title"), 300), "start": a, "end": max(a, b),
                       "invitees": invitees})
    if access == "full":
        store(conn, "mac", events, now - WINDOW_S, now + WINDOW_S, now)
    mac_status.update(access=access, at=now, events=len(events) if access == "full" else None)
    return {"wanted": True}


# ---- who the invitees are ------------------------------------------------------------------------------

def invitee_key(p: dict) -> str:
    return p.get("email") or (f"name:{p['name'].lower()}" if p.get("name") else "")


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\W_\d]+", s.lower()) if len(t) > 1]


def _close(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) > 3 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85)


def guess(p: dict, people: list[dict]) -> dict | None:
    """The one person whose every name part is (nearly) in the invitee's name or email; the fullest if several."""
    words = _tokens(p.get("name", "")) + _tokens(p.get("email", "").split("@")[0])
    fits = [(len(t), q) for q in people if (t := _tokens(q["name"])) and all(any(_close(a, b) for b in words) for a in t)]
    fits.sort(key=lambda x: -x[0])
    if not fits or (len(fits) > 1 and fits[0][0] == fits[1][0]):
        return None
    return fits[0][1]


def offer_name(p: dict) -> str | None:
    """A name a speaker can be given (api.NAME): the calendar's, or the email's, cleaned."""
    name = p.get("name") or " ".join(w.capitalize() for w in re.split(r"[._-]+", p.get("email", "").split("@")[0]))
    name = re.sub(r"\s+", " ", re.sub(r"[^\w .'-]", " ", name)).strip()[:40].strip()
    return name if name and name[0].isalpha() else None


def who(conn, invitees: list[dict]) -> list[dict]:
    """Each invitee who isn't the owner: {"key", "name", "email", "person_id", "person", "sure"}, sure once the owner
    kept the match; person None for someone Octo doesn't know (name is then what to offer)."""
    from .db import rows
    people = rows(conn, "SELECT id, name, is_self FROM people")
    by_id = {p["id"]: p for p in people}
    kept = {r["invitee"]: r["person_id"] for r in rows(conn, "SELECT invitee, person_id FROM invitee_matches")}
    out = []
    for p in invitees:
        key = invitee_key(p)
        person = by_id.get(kept.get(key)) if key in kept else guess(p, people)
        if person and person["is_self"]:
            continue
        name = person["name"] if person else offer_name(p)
        if key and name:
            out.append({"key": key, "name": name, "email": p.get("email") or None, "calendar_name": p.get("name") or None,
                        "person_id": person and person["id"], "person": person and person["name"], "sure": key in kept})
    return out


def keep_match(conn, key: str, person_id: str) -> None:
    conn.execute("INSERT OR REPLACE INTO invitee_matches (invitee, person_id, created_at) VALUES (?, ?, ?)",
                 (key, person_id, time.time()))


def for_session(conn, sess: dict) -> dict | None:
    """The events a conversation overlaps, and their invitees to offer as its speakers (deduplicated)."""
    from .db import rows
    if not sess.get("started_at"):
        return None
    a, b = sess["started_at"] + sess["start_s"], sess["started_at"] + sess["end_s"]
    evs = rows(conn, "SELECT title, start, end, invitees FROM events WHERE start < ? AND end > ? ORDER BY start", b, a)
    if not evs:
        return None
    seen, invitees = set(), []
    for e in evs:
        for p in json.loads(e["invitees"]):
            if (k := invitee_key(p)) and k not in seen:
                seen.add(k)
                invitees.append(p)
    return {"events": [{"title": e["title"], "start": e["start"], "end": e["end"]} for e in evs],
            "invitees": who(conn, invitees)}


def agenda(conn, now: float | None = None) -> list[dict]:
    """Home's agenda (B34): today's and tomorrow's events (this Mac's local days), who's in each, and once one has
    started, the conversation recorded during it (the one overlapping it most): {"id", "title", "start", "end",
    "people": [{"name", "person_id", "key", "prepared"}], "conversation"}: key is the invitee's (to prepare for someone
    Octo doesn't know yet, B36), prepared whether a plan for them is ready."""
    from .db import row, rows
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    lo, hi = today.timestamp(), (today + timedelta(days=2)).timestamp()
    from .prepare import ready_ids
    prepared = ready_ids(conn, now)  # B36: "Your plan" beside a person with one
    out = []
    for e in rows(conn, "SELECT id, title, start, end, invitees FROM events WHERE start < ? AND end > ? ORDER BY start, title",
                  hi, lo):
        conv = e["start"] <= now and row(
            conn, "SELECT s.id FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.status='active' "
                  "AND r.started_at + s.start_s < ? AND r.started_at + s.end_s > ? "
                  "ORDER BY min(r.started_at + s.end_s, ?) - max(r.started_at + s.start_s, ?) DESC LIMIT 1",
            e["end"], e["start"], e["end"], e["start"])
        out.append({"id": e["id"], "title": e["title"], "start": e["start"], "end": e["end"],
                    "people": [{"name": p["name"], "person_id": p["person_id"], "key": p["key"],
                                "prepared": p["person_id"] in prepared} for p in who(conn, json.loads(e["invitees"]))],
                    "conversation": conv["id"] if conv else None})
    return out


# ---- before you talk -------------------------------------------------------------------------------------

def due(conn, now: float | None = None) -> list[dict]:
    """Events starting in the next BEFORE_S (and a little), not announced, with someone Octo knows:
    {"id", "text", "page"}. The text is the event's name and time only (notify.py: the one exception)."""
    from . import prepare as PP
    from .db import rows
    now = time.time() if now is None else now
    out = []
    for e in rows(conn, "SELECT id, title, start, invitees FROM events WHERE announced_at IS NULL AND start > ? AND start <= ? "
                        "ORDER BY start", now, now + BEFORE_S + 30):
        known = [p for p in who(conn, json.loads(e["invitees"])) if p["person_id"]]
        if known:
            pid = known[0]["person_id"]
            out.append({"id": e["id"], "text": banner(e["title"], e["start"], now),
                        "page": f"/people/{pid}/prepare" if PP.ready(conn, pid) else f"/people/{pid}"})  # B36: the plan, once made
    return out


def banner(title: str, start: float, now: float) -> str:
    return f"{title or '(No title)'} in {max(1, round((start - now) / 60))} min"


def tick(conn, send=None, fetch=None, now: float | None = None) -> list[str]:
    """Every minute from the server: links fetched when due, then a banner for each event about to start."""
    from . import notify as N
    now = time.time() if now is None else now
    sync_links(conn, fetch, now)
    posted = []
    for d in due(conn, now):
        conn.execute("UPDATE events SET announced_at=? WHERE id=?", (now, d["id"]))
        (send or N.post)(d["text"], page=d["page"])
        posted.append(d["text"])
    return posted
