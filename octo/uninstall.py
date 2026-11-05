"""Uninstall Octo: `octo uninstall`, and the Octo app's Uninstall Octo… (open-source plan §0, U1, 19 Oct).

Everything Octo put on this Mac, found the way the app finds the lab (the com.octo.lab login item), is listed, then
removed in this order: the app (and its Open at Login), the three login items (which stops the lab), the app's
preferences, the speech models the lab's steps use, the conversations if the owner says so, the files beside the lab in
~/Library/Application Support/octo, and last the code from the one-line install. The conversations are kept unless the
owner asks for them to go.

Left alone, and said so: a folder the owner cloned themselves, other models in the Hugging Face cache (other tools
share it), the Hugging Face sign-in, uv, Ollama and its models, and Apple's command line tools.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import sqlite3
import subprocess
from pathlib import Path

from . import HOME, ROOT
from . import launcher as LN
from . import login_item as LI

BASE = Path.home() / "Library" / "Application Support" / "octo"  # scripts/bootstrap.sh
LIBRARY = Path.home() / "Library"
BUNDLE = "online.octolab.Octo"  # macapp/Info.plist
# The lab's own folders under its home (octo/__init__.py, bench/__init__.py); in a clone, only these go.
LAB_PARTS = ("data", "audio/lab", "notes", "runs")
# Besides the preferences, what macOS may keep for the app (the web view is in memory only; none seen on 19 Oct).
APP_LEFTOVERS = (f"Preferences/{BUNDLE}.plist", f"Caches/{BUNDLE}", f"HTTPStorages/{BUNDLE}", f"WebKit/{BUNDLE}",
                 f"Saved Application State/{BUNDLE}.savedState")
# The relay's copy and key (octo/login_item.py, octo/device.py), and the serial file OpenSSL made beside the lab for
# the phone's certificate before 19 Oct (it names it after the CA's path up to its first dot: .../octo/lab.noindex/... -> .../octo/lab.srl).
BESIDE_LAB = {"relay.py": "the recorder relay's copy", "relay.key": "the recorder relay's key",
              "lab.srl": "the phone certificate's serial number"}


def installed(agents: Path = LI.AGENTS) -> tuple[Path, Path]:
    """(code, lab home) of the lab the login item runs, as macapp/Octo.swift and octo/__init__.py find it; this
    copy's when there's no login item (a terminal in a clone that was never installed)."""
    try:
        p = plistlib.loads((agents / "com.octo.lab.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return ROOT, HOME
    code = Path(p.get("WorkingDirectory") or ROOT).expanduser().resolve()
    named = (p.get("EnvironmentVariables") or {}).get("OCTO_HOME") or ""
    if not named and (code / ".octo-home").exists():
        named = (code / ".octo-home").read_text().strip()
    return code, Path(named).expanduser().resolve() if named else code


def repos(home: Path) -> list[str]:
    """What `octo models` lists for that lab, read from its database without writing to it; each step's own when
    there's no database to read."""
    from . import models as MD
    from . import settings as ST
    try:
        conn = sqlite3.connect((home / "data" / "octo.db").as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return MD.required(conn)
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        own = [r for step in ST.STEPS if step.choices for r in ST.default(step).hf]
        return list(dict.fromkeys(own + [MD.VOICEPRINT_FILE[0]]))


def plan(keep_data: bool, code: Path, home: Path, *, app: Path | None, agents: Path = LI.AGENTS, base: Path = BASE,
         library: Path = LIBRARY, hub: Path, models: list[str] = (), user: Path = Path.home()) -> tuple[list, list]:
    """(what's removed, what's kept): lists of (kind, what, path), removed in the order given. Kinds: app, agent
    (the login items), path (a file or folder), empty (a folder, only if nothing's left in it)."""
    out, kept = [], []
    if app:
        out.append(("app", "the Octo app", app))
    out += [("agent", f"the login item {label}", agents / f"{label}.plist") for label in LI.status()
            if (agents / f"{label}.plist").exists()]
    out += [("path", "the Octo app's preferences" if i == 0 else "what macOS kept for the Octo app", library / p)
            for i, p in enumerate(APP_LEFTOVERS) if (library / p).exists()]
    out += [("path", f"the speech model {r}", hub / ("models--" + r.replace("/", "--"))) for r in models
            if (hub / ("models--" + r.replace("/", "--"))).exists()]
    if keep_data or (home != base / "lab.noindex" and not (home / "data" / "octo.db").exists()):
        # a home elsewhere goes folder by folder, so only one with a lab in it: OCTO_HOME=~ must never take ~/notes
        kept.append(("path", "your conversations (recordings, transcripts, reviews, voiceprints, keys)", home))
    elif home == base / "lab.noindex":
        out.append(("path", "your conversations (recordings, transcripts, reviews, voiceprints, keys)", home))
    else:
        out += [("path", f"the lab's {part}/", home / part) for part in LAB_PARTS if (home / part).exists()]
        if (home / "audio").exists():
            out.append(("empty", "the lab's audio/", home / "audio"))
    out += [("path", what, base / f) for f, what in BESIDE_LAB.items() if (base / f).exists()]
    data_inside = keep_data and home.is_relative_to(code)
    ours = code == base / "app" or code.parent == base / "versions"  # the one-line install's (versions/ since 5 Nov)
    if ours and not data_inside:
        out += [("path", "Octo's code", p) for p in (base / "current", base / "previous", base / "versions", base / "app")
                if p.exists() or p.is_symlink()]
    elif code.exists():
        kept.append(("path", "Octo's code, a folder you made: delete it yourself if you don't want it" if not ours
                     else "Octo's code, which holds the conversations you kept", code))
    out.append(("empty", "Octo's folder", base))
    from .export import ASIDE_DAYS, asides
    kept += [("path", f"a lab set aside (deleted {ASIDE_DAYS} days after the date in its name)", p) for p in asides(home.parent)]
    if (hub.parent / "token").exists():
        kept.append(("path", "your Hugging Face sign-in (`hf auth logout` removes it)", hub.parent / "token"))
    if (user / ".local" / "bin" / "uv").exists():
        kept.append(("path", "uv, which other programs may use", user / ".local" / "bin" / "uv"))
    return out, kept


def forget_login(app: Path) -> None:
    """Open at Login is registered by the app itself (SMAppService), so the app is asked to unregister it."""
    if LN.native(app):
        subprocess.run([str(app / "Contents" / "MacOS" / "Octo"), "--forget-login"], capture_output=True, timeout=30)


def _remove(kind: str, path: Path, from_app: bool) -> bool:
    if kind == "app":
        if not from_app:  # the app unregisters itself before it runs this
            forget_login(path)
        return LN.remove(stop_running=not from_app)
    if kind == "agent":
        return bool(LI.remove()) or not path.exists()  # all three at once; the next two find theirs gone
    if kind == "empty":
        try:
            path.rmdir()
            return True
        except OSError:
            return False
    if path.name == f"{BUNDLE}.plist":
        subprocess.run(["defaults", "delete", BUNDLE], capture_output=True, timeout=30)  # cfprefsd's copy too
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
    return True


def run(remove: list, kept: list, from_app: bool = False, say=print, rm=_remove) -> int:
    failed = 0
    for kind, what, path in remove:
        try:
            if rm(kind, path, from_app):
                say(f"removed {what}: {short(path)}")
        except (OSError, subprocess.SubprocessError) as e:
            failed += 1
            say(f"couldn't remove {what} ({short(path)}): {e}")
    for _, what, path in kept:
        say(f"kept {what}: {short(path)}")
    return 1 if failed else 0


def short(path: Path) -> str:
    home = str(Path.home())
    return "~" + str(path)[len(home):] if str(path).startswith(home + os.sep) else str(path)


def describe(remove: list, kept: list) -> str:
    lines = ["Uninstalling Octo removes:"] + [f"  {what}: {short(p)}" for kind, what, p in remove if kind != "empty"]
    if kept:
        lines += ["and keeps:"] + [f"  {what}: {short(p)}" for _, what, p in kept]
    return "\n".join(lines)


def last_export(home: Path) -> str:
    """When the lab was last exported (octo export), and how many conversations came since; "" with no lab."""
    try:
        conn = sqlite3.connect((home / "data" / "octo.db").as_uri() + "?mode=ro", uri=True)
        try:
            r = conn.execute("SELECT value FROM app_config WHERE key='last_export_at'").fetchone()
            at = float(r[0]) if r else None
            since = conn.execute("SELECT count(*) FROM sessions WHERE status='active' AND created_at > ?",
                                 (at or 0,)).fetchone()[0]
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        return ""
    if at is None:
        return "Your conversations have never been exported: `octo export` makes one encrypted copy first."
    from datetime import datetime
    return (f"Last export: {datetime.fromtimestamp(at):%-d %b %Y %H:%M}" +
            (f"; {since} conversation{'s' if since != 1 else ''} since aren't in it." if since else ", nothing new since."))


def main(keep_data: bool | None, dry_run: bool = False, from_app: bool = False, set_aside: bool = False, ask=input,
         say=print) -> int:
    from . import models as MD
    code, home = installed()
    where = dict(app=LN.found(), hub=MD.cache_dir(), models=repos(home))
    note = last_export(home)
    if set_aside:
        if home.is_relative_to(code) or not (home / "data").exists():
            say(f"--set-aside needs a lab outside the code; {short(home)} isn't one: nothing removed")
            return 1
        keep_data = True
    if dry_run:
        say(describe(*plan(True if keep_data is None else keep_data, code, home, **where)) + (f"\n\n{note}" if note else ""))
        return 0
    if keep_data is None:
        say(describe(*plan(True, code, home, **where)) + (f"\n\n{note}" if note else ""))
        try:
            answer = ask(f"\nYour conversations stay in {short(home)} unless you type delete. Keep them? "
                         "[Enter keeps them; delete deletes them too; Ctrl-C stops] ")
        except (EOFError, KeyboardInterrupt):
            say("\nnothing removed")
            return 1
        keep_data = answer.strip().lower() != "delete"
    done = run(*plan(keep_data, code, home, **where), from_app=from_app, say=say)
    if set_aside:  # after the login items are gone, so nothing is writing into it
        from .export import aside_path
        aside = aside_path("reinstall", home.parent)
        os.rename(home, aside)
        say(f"set your conversations aside: {short(aside)} (a new install starts empty; `octo import` brings an export "
            "in; this folder is deleted 14 days after the date in its name)")
    return done
