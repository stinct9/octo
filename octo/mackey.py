"""The Mac's own key (review 8 Oct, finding 4): the lab on 127.0.0.1:8765 answers its API only to a browser or
tool holding it, so another macOS user on this Mac, or a program that isn't ours, can't read conversations,
change the models or delete anything through the port.

The key is 32 random bytes in data/mac.key (0600, made once). A browser gets it once, from `octo open`, which
opens /let-in?k=... : the lab checks it, sets it as an HttpOnly cookie and goes on to the page. Tools on this
Mac (`octo doctor`) read the file. The phone listener doesn't use it: phones have their own pairing.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import quote

from . import DATA

FILE = DATA / "mac.key"
COOKIE = "octo_mac"
MAX_AGE = 400 * 86400  # the longest a browser keeps a cookie (Chrome's cap); renewed while the lab is used


def key(path: Path | None = None) -> str:
    """The key, made the first time. Two processes starting at once agree: the file appears whole or not at all
    (a hard link of a finished file), and whoever loses reads the winner's."""
    path = path or FILE
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
    finally:
        tmp.unlink(missing_ok=True)
    return path.read_text().strip()


def ok(value: str | None, path: Path | None = None) -> bool:
    return bool(value) and secrets.compare_digest(value, key(path))


def proof(challenge: str, value: str | None = None) -> str:
    """HMAC-SHA256 of the Octo app's challenge (`/mac-hello`), with a label of its own so it's never a value used
    for anything else."""
    return hmac.new((value or key()).encode(), b"octo-mac-hello:" + challenge.encode(), hashlib.sha256).hexdigest()


def let_in_url(base: str = "http://127.0.0.1:8765", to: str = "/") -> str:
    """The one-time link that lets this Mac's browser in. It holds the key: open it, don't print it."""
    return f"{base}/let-in?k={quote(key())}&to={quote(to, safe='/#')}"


def safe_target(to: str) -> str:
    """Where /let-in may send the browser on: a path of this lab only, never another site."""
    if not to.startswith("/") or to.startswith("//") or "\\" in to:
        return "/"
    return to


def open_command() -> str:
    """The command that lets a browser in, as typed in Terminal: this install's own `octo` (often not on PATH)."""
    import shlex
    import sys
    octo = Path(sys.executable).parent / "octo"
    if not octo.exists():
        return "octo open"
    home, text = str(Path.home()), str(octo)
    rest = text[len(home):]
    # ~ only when nothing needs quoting: the shell doesn't expand ~ before a quoted slash.
    short = "~" + rest if text.startswith(home + "/") and shlex.quote(rest) == rest else shlex.quote(text)
    return short + " open"


def cookie_header(path: Path | None = None) -> str:
    """For tools on this Mac that call the lab (`octo doctor`)."""
    return f"{COOKIE}={key(path)}"
