"""Small private files, written whole and under a lock (review 8 Oct, finding 11).

data/device.json got this on 8 Oct (finding 2). providers.json, the phone's settings, TickTick's token and the
recorder's last status each had their own fixed temporary name and no lock, and providers.json read a damaged
file as empty, so the next change saved over every key. Here, for all of them: a reader never sees half a file,
two writers never lose each other's change, and a file that exists but doesn't parse is never saved over.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Unreadable(RuntimeError):
    """The file is there but isn't the JSON it should be: left alone, for someone to look at."""


def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    """Whole or not at all: a temporary file of its own beside it, flushed to disk, then renamed over it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(path, mode)


@contextmanager
def locked(path: Path):
    """One writer at a time, across processes (the server's threads, a terminal command)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_json(path: Path, default: Any) -> Any:
    """The file's JSON; `default` if there's no file; Unreadable if there is one that doesn't parse."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return default
    try:
        return json.loads(text)
    except ValueError:
        raise Unreadable(f"{path} can't be read; left as it is")


def update_json(path: Path, change: Callable[[Any], Any], default: Any, mode: int = 0o600, indent: int | None = 2) -> Any:
    """Read, change and write back under the lock; returns what was written. `change` edits in place or returns the
    new value. Refuses (Unreadable) rather than save over a file that doesn't parse."""
    with locked(path):
        cur = read_json(path, default)
        new = change(cur)
        out = cur if new is None else new
        write_private(path, json.dumps(out, indent=indent), mode)
        return out


def trim_log(path: Path, keep: int = 1_000_000, held: bool = False) -> None:
    """A log that only grows (data/process.log, device.log) cut to about its last `keep` bytes once it's twice that
    (audit 14 Oct). Only between runs: whoever writes it mustn't have it open. `held`: a login item's log (data/logs/),
    which launchd keeps open for append for the server's life, is cut in place instead (architecture review 10, 28 Oct):
    a new file would leave launchd writing to the old one."""
    try:
        if path.stat().st_size <= 2 * keep:
            return
        with open(path, "r+b" if held else "rb") as f:
            f.seek(-keep, os.SEEK_END)
            tail = f.read().split(b"\n", 1)[-1]
            if held:  # appends land at the new end; a line written in between can come before the kept tail
                f.truncate(0)
                f.seek(0)
                f.write(tail)
                return
    except FileNotFoundError:
        return
    write_private(path, tail.decode(errors="replace"))
