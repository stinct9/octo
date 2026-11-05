"""`octo doctor`: is this Mac ready to run the lab, and what to do about anything that isn't (product plan 4.3).

Each check gives ✓ (fine), ! (works, but worth knowing) or ✗ (something won't work), and for anything not
fine, the one thing to do. Nothing is changed: it only looks. Exit status 1 if any check is ✗.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import DATA, HOME, HOME_FROM, ROOT

OK, WARN, FAIL = "✓", "!", "✗"
MIN_RAM_GB, MIN_FREE_GB, MIN_MACOS = 16, 20, 14
PACKAGES = ("mlx", "mlx_whisper", "mlx_qwen3_asr", "pyannote.audio", "onnxruntime", "silero_vad", "anthropic", "fastapi")
LAB = "http://127.0.0.1:8765"
# macOS asks before a program reads these, and the lab started at login has no window to ask in: on 13 Oct it
# waited ~10 h on one such question (B7). install.sh refuses them too, from the same list.
PROTECTED = {"Downloads": "Downloads", "Documents": "Documents", "Desktop": "Desktop",
             "Library/Mobile Documents": "iCloud Drive"}


@dataclass
class Check:
    mark: str
    what: str
    detail: str = ""
    fix: str = ""


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def machine() -> list[Check]:
    out = []
    arm = platform.machine() == "arm64"
    out.append(Check(OK if arm else FAIL, "Apple Silicon", platform.machine(),
                     "" if arm else "Octo's speech models run on Apple Silicon (M1 or later) only"))
    ram = int(_sysctl("hw.memsize") or 0) / 2**30
    out.append(Check(OK if ram >= MIN_RAM_GB - 0.5 else WARN, "Memory", f"{ram:.0f} GB",
                     "" if ram >= MIN_RAM_GB - 0.5 else f"{MIN_RAM_GB} GB is what it's tested on; with less, long "
                     "conversations may fail to transcribe"))
    ver = platform.mac_ver()[0] or "?"
    major = int(ver.split(".")[0]) if ver[:1].isdigit() else 0
    out.append(Check(OK if major >= MIN_MACOS else FAIL, "macOS", ver,
                     "" if major >= MIN_MACOS else f"macOS {MIN_MACOS} or later (the speech models need it)"))
    free = shutil.disk_usage(DATA if DATA.exists() else ROOT).free / 1e9
    out.append(Check(OK if free >= MIN_FREE_GB else WARN, "Free disk", f"{free:.0f} GB",
                     "" if free >= MIN_FREE_GB else f"about 10 GB of models, then audio: keep {MIN_FREE_GB} GB free"))
    return out


def protected(path: Path, user: Path | None = None) -> str | None:
    """The protected folder `path` is in, by its name in Finder, or None."""
    p, user = Path(os.path.realpath(path)), Path(os.path.realpath(user or Path.home()))
    return next((name for sub, name in PROTECTED.items() if p == user / sub or user / sub in p.parents), None)


def places(root: Path = ROOT, home: Path = HOME, user: Path | None = None) -> list[Check]:
    out = []
    folders = [("The code's folder", root)] + ([] if home == root else [("The lab's data folder", home)])
    for what, path in folders:
        inside = protected(path, user)
        out.append(Check(FAIL if inside else OK, what, f"{path}" + (f", inside {inside}" if inside else ""),
                         f"move it out of {inside}: macOS asks before a program reads it, and the lab, started at "
                         "login, waits on that question with no sign; ~/Library/Application Support/octo is safe"
                         if inside else ""))
    return out


def _clt() -> str | None:
    try:
        p = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = p.stdout.strip()
    return path if p.returncode == 0 and path and os.path.isdir(path) else None


def apple_tools(clt=_clt) -> list[Check]:
    path = clt()
    return [Check(OK if path else WARN, "Apple's command line tools", path or "not installed",
                  "" if path else "xcode-select --install: the recorder's relay runs on Apple's Python "
                  "(/usr/bin/python3), only a stub without them; phone memos work without")]


def software(find=importlib.util.find_spec) -> list[Check]:
    py = sys.version_info
    out = [Check(OK if py >= (3, 12) else FAIL, "Python", f"{py.major}.{py.minor}.{py.micro}",
                 "" if py >= (3, 12) else "Python 3.12: run ./install.sh")]
    missing = [p for p in PACKAGES if _missing(p, find)]
    out.append(Check(FAIL if missing else OK, "Python packages", ", ".join(missing) + " missing" if missing else "all there",
                     "./install.sh" if missing else ""))
    dist = ROOT / "ui" / "dist" / "index.html"
    out.append(Check(OK if dist.exists() else FAIL, "Web pages built", "ui/dist" if dist.exists() else "not built",
                     "" if dist.exists() else "./install.sh (builds them if Node is here: cd ui && npm ci && npm run build)"))
    return out


def _missing(pkg: str, find) -> bool:
    try:
        return find(pkg) is None
    except (ImportError, ValueError):
        return True


def models(conn) -> list[Check]:
    from . import models as MD
    out = []
    for m in MD.status(conn):
        fix = "" if m["cached"] else ("octo models download" + (" (gated: sign in first, see `octo models`)" if m["gated"] else ""))
        out.append(Check(OK if m["cached"] else FAIL, f"Model {m['repo']}", "downloaded" if m["cached"] else "not downloaded", fix))
    for d in MD.drift(conn):  # review 8 Oct, finding 18: not the version the lab was tested on
        out.append(Check(WARN, f"Model {d['repo']}", f"version {d['have'][:8]}, tested {d['want'][:8]}",
                         "results may differ from the tested ones; ask before updating PINNED in octo/models.py"))
    return out


def running(launchctl=None, fetch=None, lsof=None, answers=None) -> list[Check]:
    launchctl = launchctl or (lambda: subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout)
    fetch = fetch or _fetch_status
    try:
        listed = launchctl()
    except (OSError, subprocess.TimeoutExpired):
        listed = ""
    out = []
    for label, what in (("com.octo.lab", "The lab starts at login"), ("com.octo.relay", "The recorder relay starts at login")):
        there = label in listed
        out.append(Check(OK if there else WARN, what, label if there else "not installed",
                         "" if there else "octo login-item install"))
    up = fetch()
    out.append(Check(OK if up else FAIL, "The lab answers", LAB if up else "no answer",
                     "" if up else "octo login-item install (or `bench serve` in a terminal)"))
    return out + port(up, lsof, answers or _answers)


def _fetch_status() -> bool:
    from . import mackey as MK
    req = urllib.request.Request(f"{LAB}/api/octo/status", headers={"Cookie": MK.cookie_header()})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def lab(conn) -> list[Check]:
    from . import owner as O
    from . import rubric as RB
    from . import settings as ST
    out = []
    name = O.load(conn).get("name")
    out.append(Check(OK if name else FAIL, "You", name or "not set", "" if name else f"{LAB}/#/setup, step 3"))
    n = len(RB.sections()["parsed"])
    out.append(Check(OK if n else FAIL, "Your principles", f"{n} in {RB.sections()['file']}" if n else "none written",
                     "" if n else f"{LAB}/#/setup, step 4"))
    blocked = ST.review_blocked(conn)
    choice = ST.selected(conn, ST.BY_STAGE["insight"])
    why = ST.unavailable(choice) if choice else None
    out.append(Check(FAIL if blocked or why else OK, "A model for reviews", choice.label if choice else "not chosen",
                     f"{LAB}/#/setup, step 5" if blocked else (why or "")))
    if (ST.reader(conn) or {}).get("kind") == "claude-code":
        from .claude_code import status
        cc = status()
        plan = f", {cc['plan']} plan" if cc["plan"] else ""
        out.append(Check(FAIL if cc["fix"] else OK, "Claude Code on this Mac (your Claude plan)",
                         f"{cc['version']}, logged in{plan}" if not cc["fix"] else cc["version"] or "not found",
                         cc["fix"] or ""))
    from . import iphone as IP  # optional, like the recorder: the Setup page walks through it
    from . import phone as PH
    have = IP.installed() or {}
    app = IP.setup_step(have, PH.devices(conn))  # the Setup page's own rule, so both say the same
    left = (have.get("expires") or 0) - time.time()
    detail = (app["note"] or (f"on {have['phone']}, opens until " + time.strftime("%-d %b", time.localtime(have["expires"]))
              if left > 0 and have.get("trusted") else "paired with this lab")) if app["done"] else "not yet"
    out.append(Check(OK if app["done"] and not app["note"] else WARN, "The Octo app on your iPhone", detail,
                     "" if app["done"] and not app["note"] else f"optional: {LAB}/#/setup, step 6"))
    me = conn.execute("SELECT 1 FROM people WHERE is_self=1").fetchone()
    out.append(Check(OK if me else WARN, "Your voice", "known" if me else "not yet",
                     "" if me else "add a conversation you're in and answer \"Is this you?\" on your speaker"))
    paired = (DATA / "device.json").exists()
    out.append(Check(OK if paired else WARN, "Recorder", "paired" if paired else "not paired",
                     "" if paired else "optional: phone voice memos work without it"))
    return out


def home(conn, agents=None) -> list[Check]:
    """Where the lab keeps its data (14 Oct, the move out of ~/Downloads): which folder and who said so, that only
    its owner can open it, that every recording the database names is there, and that the login item runs this code."""
    from .login_item import AGENTS
    out = [Check(OK, "The lab's folder", f"{HOME} (from {HOME_FROM})")]
    mine = HOME if HOME != ROOT else DATA  # a clone keeps its data inside the code, which needn't be private itself
    mode = mine.stat().st_mode & 0o777 if mine.exists() else None
    out.append(Check(OK if mode == 0o700 else WARN, "Only you can open it", oct(mode) if mode is not None else "missing",
                     "" if mode == 0o700 else f"chmod 700 '{mine}'"))
    rows = conn.execute("SELECT path FROM recordings WHERE audio_deleted_at IS NULL").fetchall()
    gone = sum(1 for r in rows if not (HOME / r[0]).exists())
    out.append(Check(FAIL if gone else OK, "Recordings where the database says", f"{len(rows) - gone} of {len(rows)}",
                     "" if not gone else f"{gone} missing under {HOME}: was the folder moved without OCTO_HOME?"))
    plist = (agents or AGENTS) / "com.octo.lab.plist"
    if plist.exists():
        try:
            item = plistlib.loads(plist.read_bytes())
        except (OSError, plistlib.InvalidFileException):
            item = {}
        cwd = item.get("WorkingDirectory")
        set_home = (item.get("EnvironmentVariables") or {}).get("OCTO_HOME")
        same = cwd == str(ROOT) and (set_home is None or os.path.realpath(set_home) == str(HOME))
        out.append(Check(OK if same else WARN, "The login item runs this copy", cwd or "?",
                         "" if same else "octo login-item install, from the copy that should run"))
    return out


# ---- what macOS may refuse without saying (open-source plan §3.1, F8–F13) ---------------------------------------

PORT = 8765
FIREWALL = "/usr/libexec/ApplicationFirewall/socketfilterfw"


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def listeners(lsof=None) -> list[tuple[str, str]]:
    """(program, address) of everything listening on the lab's port."""
    text = lsof() if lsof else _run("lsof", "-nP", f"-iTCP:{PORT}", "-sTCP:LISTEN", "-Fcn")
    out, name = [], "?"
    for line in text.splitlines():
        if line.startswith("c"):
            name = line[1:]
        elif line.startswith("n"):
            out.append((name, line[1:]))
    return out


def _answers(host: str = "127.0.0.1", port: int = PORT) -> bool:
    import socket
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


def port(up: bool, lsof=None, answers=_answers) -> list[Check]:
    """Another program on 8765 (19 Oct: Logitech's Options+ listens on [::1]:8765 on the owner's Mac, so localhost:8765 can
    reach it instead of the lab, and if it ever takes 127.0.0.1:8765 the lab can't start)."""
    held = listeners(lsof)
    ours = f"127.0.0.1:{PORT}"
    if not up and (taker := next((n for n, a in held if a in (ours, f"*:{PORT}")), None)):
        return [Check(FAIL, f"Port {PORT}", f"taken by {taker}",
                      f"quit {taker} (or move it to another port), then: octo login-item install")]
    if not up and answers():  # lsof lists only this user's programs: another user's is there and unnamed (install review, 5 Nov)
        return [Check(FAIL, f"Port {PORT}", "taken by another user's program (another Octo lab on this Mac?)",
                      "only one lab can run on a Mac at a time: they quit theirs or log out, then: octo login-item install")]
    others = sorted({f"{n} on {a.rsplit(':', 1)[0]}" for n, a in held if a != ours})
    if others:
        return [Check(WARN, f"Port {PORT}", "also used by " + ", ".join(others),
                      f"open the lab at 127.0.0.1:{PORT} or with the Octo app; localhost:{PORT} may reach the other program")]
    return []


def firewall(phone_on: bool, python: Path | None = None, fw=None) -> list[Check]:
    """The phone reaches the lab over the home network, and macOS's firewall, if on, can block that silently: a
    program started at login gets no "Accept incoming connections?" question (inferred: the plan's F9)."""
    if not phone_on:
        return []
    fw = fw or (lambda *args: _run(FIREWALL, *args))
    if "enabled" not in fw("--getglobalstate"):
        return [Check(OK, "Firewall", "off")]
    where = "System Settings → Network → Firewall → Options"
    if "enabled" in fw("--getblockall"):
        return [Check(FAIL, "Firewall", "blocks all incoming connections, so the phone can't reach the lab",
                      f"{where}: turn off \"Block all incoming connections\"")]
    python = python or Path(sys.executable).resolve()
    said = fw("--getappblocked", str(python))
    if "permitted" in said:
        return [Check(OK, "Firewall", "on; lets the lab in")]
    return [Check(FAIL if "blocked" in said else WARN, "Firewall",
                  "on, and blocks the lab" if "blocked" in said else "on, and hasn't been asked about the lab yet",
                  f"{where}: add {python} and set it to \"Allow incoming connections\"")]


def recorder_reached(last: dict | None, configured: bool) -> list[Check]:
    """The lab's own last word on the recorder, not this terminal's: Terminal may reach the local network when a
    program started at login can't (5 Oct, octo/relay.py)."""
    if not configured:
        return []
    last = last or {}
    if not last.get("error"):
        return [Check(OK, "The lab reaches the recorder", "yes" if last.get("seen_at") else "not asked yet")]
    seen = time.strftime("%-d %b %H:%M", time.localtime(last["seen_at"])) if last.get("seen_at") else "never"
    return [Check(WARN, "The lab reaches the recorder", f"not now (last seen {seen})",
                  "if it's on and on your Wi-Fi: System Settings → Privacy & Security → Local Network, allow "
                  "python3 (Octo's relay); otherwise it catches up when it's back")]


def away(phone_on: bool, conn, ts=None) -> list[Check]:
    """Away from home through Tailscale (19 Oct): optional, so never ✗; only asked when phone access is on."""
    if not phone_on:
        return []
    from . import phone as PH
    ts = PH.tailscale_status() if ts is None else ts
    devices = PH.devices(conn)
    nxt = PH.away_step(ts, phone_on, devices, PH._settings().get("ca_renewed_at"))
    got = PH.away_setup(ts, phone_on, devices, PH._settings().get("ca_renewed_at"))  # the Setup page's own rule
    if got["done"]:
        when = time.strftime("%-d %b %H:%M", time.localtime(got["seen"]))
        if got["note"]:
            return [Check(WARN, "Away from home (Tailscale)", f"reached {when}, not now",
                          got["note"] + (f" ({got['link']})" if got["link"] else ""))]
        return [Check(OK, "Away from home (Tailscale)", f"{ts['ips'][0]}, last reached {when}")]
    if nxt["step"] == "try_away":
        return [Check(OK, "Away from home (Tailscale)", f"{ts['ips'][0]}, not tried away yet")]
    return [Check(WARN, "Away from home (Tailscale)", "not set up" if nxt["step"] == "install_mac" else "not yet",
                  nxt["do"] + (f" ({nxt['link']})" if nxt["link"] else ""))]


def launcher(runs=None, octo: Path | None = None, app=None, native=None, verified=None, stamp=None,
             compiler=None) -> list[Check]:
    from . import launcher as LN
    app = app if app is not None else LN.found()
    if not app:
        return [Check(WARN, "The Octo app", "not in Applications", "octo launcher install (opens the lab in its own window)")]
    if (native or LN.native)(app):
        if not (verified or LN.verified)(app):
            return [Check(WARN, "The Octo app", "its signature doesn't match its files", "octo launcher install")]
        if (LN.info(app) or {}).get("OctoSource") != (stamp or LN.source_hash)():
            return [Check(WARN, "The Octo app", "built from older source", "octo launcher install")]
        return [Check(OK, "The Octo app", str(app))]
    if (runs or LN.runs)(app) != LN.script(octo or LN.octo_bin()):
        return [Check(WARN, "The Octo app", "opens another copy of Octo", "octo launcher install, from this copy")]
    if (compiler or LN.swiftc)():
        return [Check(WARN, "The Octo app", "the simple launcher (no window, menu bar or Octo banners)", "octo launcher install")]
    return [Check(OK, "The Octo app", f"{app}, the simple launcher: Apple's command line tools would let it build the full app")]


def permissions(conn) -> list[Check]:
    from . import device as D
    from . import phone as PH
    return [*launcher(), *firewall(PH.enabled()), *away(PH.enabled(), conn),
            *recorder_reached(D.last_status(), D.load_config() is not None)]


def run(conn, fresh: bool = False) -> list[Check]:
    """`fresh`: install.sh's check, just after installing (onboarding review 5, 28 Oct). The models are still coming
    down and the setup steps are the Setup page's to do, so they're left out rather than shown as ✗."""
    return [*machine(), *places(), *apple_tools(), *software(), *([] if fresh else models(conn)), *running(),
            *permissions(conn), *home(conn), *([] if fresh else lab(conn))]


def spelled(fix: str) -> str:
    """`octo` isn't on PATH (install.sh leaves the shell's settings alone): a fix printed to type says where it is."""
    from .launcher import octo_bin
    return re.sub(r"(?<![\w./'-])octo (?=[a-z])", lambda m: f"'{octo_bin()}' ", fix)


def report(checks: list[Check], fresh: bool = False) -> str:
    lines = []
    for c in checks:
        line = f"{c.mark} {c.what}" + (f": {c.detail}" if c.detail else "")
        if c.fix:
            line += f"\n    → {spelled(c.fix)}"
        lines.append(line)
    bad = sum(c.mark == FAIL for c in checks)
    lines.append(f"\n{bad} thing{'s' if bad != 1 else ''} to fix." if bad else
                 "\nThe speech models are coming down in the background (about 10 GB, once). The Setup page, opening now,\n"
                 "shows how far they've got and takes you through the rest." if fresh else "")
    return "\n".join(lines).rstrip() + "\n"


def as_json(checks: list[Check]) -> str:
    return json.dumps([c.__dict__ for c in checks], indent=2)
