"""Can the lab still save to its folder? (13 Oct)

On 13 Oct the lab stopped for ten hours without a word: a database write opened data/ to flush it, and
macOS held that open() until the owner answered a permission prompt for the Downloads folder. Nothing errors
while macOS waits, so every request and every step that saved just hung, and the runner that crashed
behind it waited an hour to try again.

`probe` does what blocked, in a subprocess with a time limit: write a file in data/, flush it, open the
folder and flush that. The server runs it every few minutes (`Watchdog` in octo/api.py); when it doesn't
finish in time, or macOS refuses, the owner gets a notification once, and the header says so (from memory, not
the database, so it answers while the database can't).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import DATA

TIMEOUT_S = 60.0

_SCRIPT = """
import os, sys
d = sys.argv[1]
p = os.path.join(d, ".octo-can-save")
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, b"ok")
os.fsync(fd)
os.close(fd)
fd = os.open(d, os.O_RDONLY)
os.fsync(fd)
os.close(fd)
os.unlink(p)
"""

STUCK = "stuck"     # it didn't finish in time: macOS is holding it (a permission prompt, most likely)
DENIED = "denied"   # macOS refused: the permission was turned off


def probe(folder: Path | None = None, timeout_s: float = TIMEOUT_S, python: str = sys.executable) -> str | None:
    """None when the lab can save to `folder` (data/), else STUCK or DENIED."""
    folder = folder or DATA
    proc = subprocess.Popen([python, "-c", _SCRIPT, str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:  # a process held inside open() may not go at once; don't wait on it
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return STUCK
    if proc.returncode == 0:
        return None
    text = err.decode(errors="replace")
    return DENIED if "PermissionError" in text or "Operation not permitted" in text else STUCK


MESSAGE = {
    STUCK: "Octo can't save to its folder, so nothing is being processed. Look for a macOS prompt about "
           "files or folders and allow it.",
    DENIED: "Octo isn't allowed to save to its folder, so nothing is being processed. Allow Python in System "
            "Settings → Privacy & Security → Files and Folders.",
}
RECOVERED = "Octo can save again; processing carries on."
