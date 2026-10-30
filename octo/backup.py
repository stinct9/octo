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
import time
from datetime import date
from pathlib import Path

from . import DATA

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
    return made


def last(dest: Path = DEST) -> dict | None:
    b = backups(dest)
    return {"file": b[-1].name, "at": b[-1].stat().st_mtime, "kept": len(b)} if b else None
