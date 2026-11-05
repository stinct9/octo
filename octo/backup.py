"""A daily copy of the database (review 4 Oct, M14; the owner's call J4, 5 Oct: on this Mac, 14 days).

octo.db holds every rating, correction and voiceprint, the counts' evidence. The hourly pass makes
one copy a day (VACUUM INTO: safe while the server and workers write, WAL mode) into data/backups/.
It keeps the last DAILY days' copies and one a week (Sunday's) until KEEP_DAYS, by the date in the
name, so a Forget or a Delete is gone from the backups within 14 days too, even after days the Mac was
off. It's on the same disk: it covers a bad migration or a mistaken delete, not a dead disk or a lost Mac.
Since 28 Oct (architecture review 4): VACUUM INTO, not the backup API, so a copy leaves out free pages
(outputs prune.py cleared), and 8 copies, not 14: the copies were ~23 MB each, 14 of them ten times the lab.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from datetime import date
from pathlib import Path

from . import DATA, HOME

DEST = DATA / "backups"
DAILY = 7
KEEP_DAYS = 14


def backups(dest: Path = DEST) -> list[Path]:
    return sorted(dest.glob("octo-????-??-??.db"))


def _kept(b: Path, today: date) -> bool:
    day = date.fromisoformat(b.name[5:15])
    age = (today - day).days
    return age < DAILY or (age < KEEP_DAYS and day.weekday() == 6)


def daily(conn: sqlite3.Connection, dest: Path = DEST, today: date | None = None) -> Path | None:
    """Back up today's database unless that's done; returns the new file, or None."""
    today = today or date.today()
    target = dest / f"octo-{today.isoformat()}.db"
    made = None
    if not target.exists():
        from .privacy import HOLD_GB, free_gb
        if free_gb() < HOLD_GB:  # a copy of the database could fill the disk (review 5 Nov); tried again next hour
            raise RuntimeError(f"held: {free_gb()} GB free on the disk, and the backup waits for {HOLD_GB} GB")
        dest.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        conn.execute("VACUUM INTO ?", (str(tmp),))
        os.chmod(tmp, 0o600)  # it holds voiceprints: readable by the owner only
        os.replace(tmp, target)
        made = target
    for old in backups(dest):
        if not _kept(old, today):
            old.unlink(missing_ok=True)
    for old in dest.glob("pre-schema-*.db"):  # db.snapshot's, before a migration: 14 days too, as Forget promises
        if time.time() - old.stat().st_mtime > KEEP_DAYS * 86400:
            old.unlink(missing_ok=True)
    return made


def last(dest: Path = DEST) -> dict | None:
    b = backups(dest)
    return {"file": b[-1].name, "at": b[-1].stat().st_mtime, "kept": len(b)} if b else None


# ---- off this Mac (tech review 5 Nov, risk 9): the copies above are on the same disk ----------------------------

EXPORT_DAYS = 30  # an export older than this doesn't count as a copy off the Mac; and the reminder comes monthly
_tm: dict = {"at": 0.0, "why": None}


def time_machine(home: Path = HOME, run=subprocess.run) -> str | None:
    """None when Time Machine backs the lab's folder up to a disk; else why not, in a few words. Asked at most every
    10 min: Setup asks every 8 s while it's open, the notifier every 5 min."""
    if run is subprocess.run and time.time() - _tm["at"] < 600:
        return _tm["why"]
    try:
        dest = run(["tmutil", "destinationinfo"], capture_output=True, text=True, timeout=15).stdout
        if "No destinations" in dest or ":" not in dest:
            why = "Time Machine has no backup disk"
        elif "[Excluded]" in run(["tmutil", "isexcluded", str(home)], capture_output=True, text=True, timeout=15).stdout:
            why = "Time Machine leaves the lab's folder out"
        else:
            why = None
    except (OSError, subprocess.TimeoutExpired):
        why = "Time Machine couldn't be asked"
    if run is subprocess.run:
        _tm.update(at=time.time(), why=why)
    return why


def last_export(conn) -> float | None:
    from .db import config
    return config(conn, "last_export_at")  # octo/export.py writes it once an export is made and checked


def off_mac(conn, now: float | None = None, tm=None) -> str | None:
    """None when something copies the lab off this Mac: Time Machine, or an export in the last EXPORT_DAYS. Else what
    to say: nothing does."""
    now = time.time() if now is None else now
    why = (tm or time_machine)()
    if why is None:
        return None
    at = last_export(conn)
    if at and now - at < EXPORT_DAYS * 86400:
        return None
    return f"{why}, and {'the last export is over ' + str(EXPORT_DAYS) + ' days old' if at else 'the lab was never exported'}"


def export_due(conn, now: float | None = None, tm=None) -> str | None:
    """The notifier's key, "export:<YYYY-MM>", once a month while nothing copies the lab off this Mac and the lab has
    had recordings for EXPORT_DAYS (a lab of a day isn't nagged). Gone once an export is made."""
    from datetime import datetime
    from .db import row
    now = time.time() if now is None else now
    first = (row(conn, "SELECT min(created_at) AS at FROM recordings") or {}).get("at")
    if not first or now - first < EXPORT_DAYS * 86400 or off_mac(conn, now, tm) is None:
        return None
    return "export:" + datetime.fromtimestamp(now).strftime("%Y-%m")
