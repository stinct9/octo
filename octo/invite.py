"""Invite a beta tester by email (31 Oct; docs/beta-tester.md): Settings → Invite a tester, in the first lab only.

The lab sends one email through the Gmail account the first owner already has for the product (support@octolab.online), over
Gmail's SMTP with an app password: no server of ours and nothing new on Cloudflare, and the mail lands in that account's
Sent folder, so a tester's reply reaches him there. Sent only when he presses Invite.

Only for whoever runs the beta: `octo invite setup` keeps the account in this lab's data, typed in Terminal so the page
never has the password, and the Settings card and its routes exist only while it's kept. A public copy has this code
and nothing to send with.

data/invite.json (0600, exported like the TickTick token, never sent back to the page): the address, the app password,
the install line testers paste (it holds the bucket's private path), and who was invited when. Optionally an alias to send
as ("as", 4 Nov: sign in as one address, send from support@), which must be in that account's Gmail → Settings →
Accounts → Send mail as, or Gmail sends from the account's own address instead.
"""

from __future__ import annotations

import json
import re
import smtplib
import time
from email.message import EmailMessage

from . import DATA

CONFIG = DATA / "invite.json"
SMTP = ("smtp.gmail.com", 587)
PER_DAY = 20  # a typo'd loop or a stolen page can't turn the account into a spammer
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LINE = re.compile(r"^curl -fsSL https://[^\s'\"]+/install\.sh \| bash$")


class InviteError(RuntimeError):
    pass


def load() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save(cfg: dict) -> None:
    from . import files as FL
    with FL.locked(CONFIG):
        FL.write_private(CONFIG, json.dumps(cfg, indent=2))


def remove() -> None:
    CONFIG.unlink(missing_ok=True)


def status() -> dict:
    cfg = load()
    return {"ready": bool(cfg.get("password") and cfg.get("line")), "from": cfg.get("as") or cfg.get("from"),
            "link": bool(cfg.get("line")), "sent": cfg.get("sent", [])[-20:][::-1]}


def _login(sender: str, password: str, smtp=None):
    s = (smtp or smtplib.SMTP)(*SMTP, timeout=20)
    s.starttls()
    s.ehlo()
    # One way in: Google answers a refused PLAIN and then hangs up on smtplib's next try (LOGIN), which read as
    # "couldn't reach Gmail" instead of the refusal (4 Nov).
    s.esmtp_features["auth"] = "PLAIN"
    try:
        s.login(sender, password)
    except smtplib.SMTPAuthenticationError:
        s.close()  # not quit(): Google has hung up after refusing
        raise InviteError("Google refused that. Use an app password (16 letters) made for this account, not its own "
                          "password: Google Account → Security → 2-Step Verification → App passwords.") from None
    return s


def setup(sender: str, password: str, line: str, alias: str = "", smtp=None) -> dict:
    """Keep the address, app password and install line, once Gmail accepts the password. A blank password, line or alias
    keeps the one already kept, so the line can change without typing the password again; the account's own address as
    the alias sends from it again."""
    cfg = load()
    sender, password, line = sender.strip().lower(), password.replace(" ", "").strip(), line.strip()
    password, line = password or cfg.get("password", ""), line or cfg.get("line", "")
    alias = alias.strip().lower() or cfg.get("as", "")
    if not EMAIL.match(sender):
        raise InviteError("that isn't an email address")
    if alias and not EMAIL.match(alias):
        raise InviteError("the alias isn't an email address")
    if not LINE.match(line):
        raise InviteError("paste the whole install line, as beta_release.sh printed it: curl -fsSL https://…/install.sh | bash")
    if not password:
        raise InviteError("paste the account's app password")
    try:
        _login(sender, password, smtp).quit()
    except OSError as e:
        raise InviteError(f"couldn't reach Gmail ({e}): check this Mac is online") from None
    cfg = {**cfg, "from": sender, "password": password, "line": line}
    cfg.pop("as", None)
    save({**cfg, **({"as": alias} if alias and alias != sender else {})})
    return status()


def message(name: str, line: str, signed: str) -> str:
    """docs/beta-tester.md's message to a tester."""
    return f"""Hi {name},

Thank you for trying Octo. It needs a Mac with Apple's M chip (2020 or later), 16 GB of memory and about 35 GB free
(20 GB without the iPhone app). Set aside an hour; most of it is downloads you can leave running.

1. Open Terminal: press Command-Space, type Terminal, press Return.
2. Paste this line and press Return:

   {line}

3. If a window from Apple asks to install "command line developer tools", click Install, then Agree. Terminal waits
   for it.
4. When Terminal says it's done, the Octo app opens on Set up Octo. Follow it from top to bottom: it tells you what to
   do at each step and checks it worked. You can close Terminal then.

The line is just for you, so please don't pass it on. If anything goes wrong or looks confusing, reply to this email.

{signed}
"""


def send(name: str, to: str, signed: str = "", now=time.time, smtp=None) -> dict:
    cfg = load()
    name, to = " ".join(name.split())[:80], to.strip().lower()
    if not (cfg.get("password") and cfg.get("line")):
        raise InviteError("set up the sending account first")
    if not name or not EMAIL.match(to):
        raise InviteError("a name and an email address, please")
    sent = cfg.get("sent", [])
    if sum(1 for s in sent if s["at"] > now() - 86400) >= PER_DAY:
        raise InviteError(f"{PER_DAY} invitations in the last day already; try again tomorrow")
    msg = EmailMessage()
    msg["From"] = f"Octo <{cfg.get('as') or cfg['from']}>"
    msg["To"] = to
    msg["Subject"] = "Your invitation to try Octo"
    msg.set_content(message(name, cfg["line"], signed or "Octo"))
    try:
        s = _login(cfg["from"], cfg["password"], smtp)
        s.send_message(msg)
        s.quit()
    except smtplib.SMTPRecipientsRefused:
        raise InviteError("Gmail refused that address") from None
    except (OSError, smtplib.SMTPException) as e:
        raise InviteError(f"couldn't send it ({e}): check this Mac is online, then try again") from None
    save({**load(), "sent": [*sent, {"name": name, "email": to, "at": now()}]})
    return status()
