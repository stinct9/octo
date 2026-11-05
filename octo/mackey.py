"""The Mac's own key (review 8 Oct, finding 4): the lab on 127.0.0.1:8765 answers its API only to a browser or
tool holding it, so another macOS user on this Mac, or a program that isn't ours, can't read conversations,
change the models or delete anything through the port.

The key is 32 random bytes in data/mac.key (0600, made once; `octo open --rotate` replaces it). A browser gets it
once, from `octo open`, which opens /let-in?c=... with a code made from the key, good for one use within a minute
(security review L4, 28 Oct: the key itself was in the link, so in browser history and history sync): the lab checks
it, sets the key as an HttpOnly cookie and goes on to the page. The Octo app makes its codes the same way. Tools on
this Mac (`octo doctor`) read the file. The phone listener doesn't use it: phones have their own pairing.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
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


CODE_S = 60  # how long a let-in code works
_spent: dict[str, float] = {}  # nonces used, while their code could still work (the server's memory: one use each)


def _sign(text: str, value: str | None = None) -> str:
    return hmac.new((value or key()).encode(), b"octo-let-in:" + text.encode(), hashlib.sha256).hexdigest()


def let_in_code(now: float | None = None, value: str | None = None) -> str:
    """when.nonce.HMAC(key, label + when.nonce): made by `octo open` (another process) or the Octo app, checked by
    the lab with the same key. Never the key: a copy from browser history is spent or out of date."""
    text = f"{int(now or time.time())}.{secrets.token_hex(12)}"
    return f"{text}.{_sign(text, value)}"


def redeem(code: str, now: float | None = None, value: str | None = None) -> bool:
    """Whether the code is good: made with this key, within CODE_S, and not used before. Using it spends it."""
    when, _, rest = code.partition(".")
    nonce, _, sig = rest.partition(".")
    now = now or time.time()
    if not (when.isdigit() and nonce and sig) or not now - CODE_S <= int(when) <= now + 5:
        return False
    if not secrets.compare_digest(sig, _sign(f"{when}.{nonce}", value)):
        return False
    for n, at in list(_spent.items()):
        if at < now - CODE_S - 10:
            del _spent[n]
    if nonce in _spent:
        return False
    _spent[nonce] = int(when)
    return True


def let_in_url(base: str = "http://127.0.0.1:8765", to: str = "/") -> str:
    """The link that lets this Mac's browser in, once, within a minute. Open it; there's no need to print it."""
    return f"{base}/let-in?c={let_in_code()}&to={quote(to, safe='/#')}"


def rotate(path: Path | None = None) -> str:
    """A new key in place of the old one: every browser and the Octo app's window are signed out (the lab reads the
    file on each request), and a code made with the old key no longer works."""
    path = path or FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return key(path)


def safe_target(to: str) -> str:
    """Where /let-in may send the browser on: a path of this lab only, never another site."""
    if not to.startswith("/") or to.startswith("//") or "\\" in to:
        return "/"
    return to


def open_command() -> str:
    """The command that lets a browser in, as typed in Terminal: this install's own `octo` (often not on PATH)."""
    import shlex

    from . import bin_path
    octo = bin_path("octo")
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
