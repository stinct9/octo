"""Export and import the whole lab: `octo export`, `octo import` (docs/export-plan.md, 19 Oct).

An export is one file: macOS's own encrypted disk image (AES-256, the owner's password, `hdiutil`), holding the lab's
own files as they are under HOME plus a manifest (each file's size and SHA-256, the counts the owner knows). Every path
the database stores is relative to HOME, so it can be restored into any HOME. Left out: the daily backups (a Forget
or a Delete must not come back from them), playback clips (a cache), logs, locks, bench output, and this install's
mac.key.

The snapshot is taken with the lab stopped for a few seconds, and the audio list is read from the snapshot itself:
the code replaces audio files rather than editing them (a delete silences a copy and renames it over; chunks are
deleted once joined), so files and database taken at different moments would disagree (architecture review).

An import is untrusted input until checked: only the manifest's files are copied (regular files, relative paths under
the lab's own folders, hashed while copied, never longer than the manifest says), the database is checked (integrity,
no trigger or view the schema doesn't make, every stored audio path inside audio/lab/) and the folders are swapped in
one atomic rename. Nothing is merged: a lab with recordings is set aside first (`--replace`), never deleted.

Never run inside the lab server: a macOS permission prompt for a folder held the server for ~10 h on 13 Oct.
"""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import DATA, DB_PATH, HOME, ROOT
from . import db as DB
from . import files as FL
from . import login_item as LI

FORMAT = 1
OUT = Path.home() / "Octo exports"  # not Documents or Desktop, which iCloud may sync (security review)
CLOUD = (Path.home() / "Documents", Path.home() / "Desktop", Path.home() / "Library" / "Mobile Documents")
STAGE = HOME / ".export-staging.noindex"  # inside HOME: the same disk, so audio is hard-linked, not copied
IMPORTING = HOME.parent / "octo-importing.noindex"  # .noindex last: Spotlight reads only the last extension
MARK = ".octo-importing"  # in a folder an import is filling; a leftover without it is a lab the swap moved out
ASIDE = re.compile(r"^octo-before-[a-z]+-(\d{8}-\d{6})\.noindex$")
ASIDE_DAYS = 14  # as the daily backups: what was deleted in that lab is gone from this Mac after this
MIN_PASSWORD = 14
STOP_GB = 5  # privacy.STOP_GB: an export must never leave too little room to pull from the recorder

# What goes in: the lab's own folders, never the whole HOME (it can be the code checkout).
ROOTS = ("data", "audio/lab/recordings", "audio/lab/device", "audio/lab/drills", "notes", "runs")
# Left out of those folders. Bench output is every folder in data/ ending in "compare" except compare/ (blind A/B keys).
# filler: the owner's filler model (B20) is C compiled into the recorder's firmware, so one from an imported image
# would be untrusted code on the recorder; it's made again from the lab's own audio instead.
SKIP_DIRS = {"backups", "logs", "bench", "uploads", "filler"}
SKIP_FILES = {"octo.db", "octo.db-wal", "octo.db-shm", "mac.key", "device_status.json", "lab.db", "server.csr",
              "audio",  # "audio": privacy.rewriting's lock (data/audio.lock), named without its suffix in the code
              "iphone.json"}  # the iPhone app as this Mac's Xcode signed it (octo/iphone.py): a new Mac signs its own
SKIP_SUFFIXES = (".lock", ".log", ".tmp", ".tmp.wav", ".tmp.flac")
# Kept, named so the guard test knows them: the recorder's keys, the phone's CA, tokens and calendar links (as an
# owner moving Mac wants them back; the image is encrypted), principles, A/B keys.
KEPT = {"device.json", "phone", "ticktick.json", "providers.json", "calendar.json", "rubric.md", "compare", "sample-review.json",
        "notify.json", "tone.json"}
README = """This is an export of an Octo lab: its conversations, transcripts, reviews, people, voiceprints, settings
and keys, made {made}.

It's encrypted with the password chosen when it was made. Octo doesn't keep that password.

To bring it into Octo: octo import "<this file>"
Keep it somewhere safe: it holds voiceprints of people you recorded, and the keys of your recorder and phone.
"""


class ExportError(RuntimeError):
    pass


def skipped(rel: str) -> bool:
    """Is this file (relative to HOME, posix) left out of an export?"""
    parts = rel.split("/")
    if parts[0] == "data" and len(parts) > 2 and (parts[1] in SKIP_DIRS or parts[1].endswith("compare") and parts[1] != "compare"):
        return True
    name = parts[-1]
    return name in SKIP_FILES or name.endswith(SKIP_SUFFIXES) or name.startswith(".") or name.startswith("join_b")


def safe_rel(rel: str, roots=ROOTS) -> bool:
    """A plain relative path inside one of the lab's folders: nothing absolute, no "..", nothing to normalise."""
    p = PurePosixPath(rel)
    return (isinstance(rel, str) and not p.is_absolute() and ".." not in p.parts and str(p) == rel
            and any(rel.startswith(r.rstrip("/") + "/") for r in roots))


def short(path: Path) -> str:
    home = str(Path.home())
    return "~" + str(path)[len(home):] if str(path).startswith(home + os.sep) else str(path)


# ---- the password ------------------------------------------------------------------------------------------------

def passphrase(words: Path = Path("/usr/share/dict/words"), n: int = 5) -> str:
    try:
        pool = [w for w in words.read_text().split() if w.isalpha() and w.islower() and 4 <= len(w) <= 7]
    except OSError:
        pool = []
    return "-".join(secrets.choice(pool) for _ in range(n)) if len(pool) > 1000 else secrets.token_urlsafe(18)


def new_password(ask=getpass.getpass, say=print) -> str:
    while True:
        pw = ask(f"A password for the export (at least {MIN_PASSWORD} characters; Enter makes one for you): ")
        if not pw:
            pw = passphrase()
            say(f"\n  {pw}\n\nOcto doesn't keep this password, and without it nobody can open the export, not even you.\n"
                "Save it in your password manager now.")
            if ask("Type it once to show it's saved: ") == pw:
                return pw
            say("That wasn't it; let's start again.")
        elif len(pw) < MIN_PASSWORD:
            say(f"That's {len(pw)} characters; it needs at least {MIN_PASSWORD} (or press Enter for one made for you).")
        elif ask("Again: ") == pw:
            return pw
        else:
            say("They didn't match.")


def stdin_password() -> str:
    return sys.stdin.readline().rstrip("\n")


# ---- the disk image ----------------------------------------------------------------------------------------------

def _hdiutil(*args, pw: str) -> subprocess.CompletedProcess:
    # The password on stdin only (never argv, where ps shows it), and without a newline: -stdinpass keeps one as part
    # of the password, so the image wouldn't open when the owner types it in Finder (measured 19 Oct).
    return subprocess.run(["hdiutil", *map(str, args)], input=pw.encode(), capture_output=True, timeout=3600)


def make_image(folder: Path, out: Path, pw: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.stem}.part.dmg")
    part.unlink(missing_ok=True)
    try:
        r = _hdiutil("create", "-quiet", "-srcfolder", folder, "-volname", "Octo export", "-fs", "APFS",
                     "-encryption", "AES-256", "-stdinpass", "-format", "ULFO", part, pw=pw)
        if r.returncode:
            raise ExportError(f"hdiutil couldn't make the image: {r.stderr.decode().strip()}")
        detach_leftover(part)
        os.chmod(part, 0o600)
        os.replace(part, out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def detach_leftover(image: Path) -> None:
    """`hdiutil create` now and then exits 0 with its own image still attached, and leaves it so: attaching it to check
    it then fails with EAGAIN ("Resource temporarily unavailable"; 1 run of tests/test_export.py in ~15, 20 Oct)."""
    info = plistlib.loads(subprocess.run(["hdiutil", "info", "-plist"], capture_output=True).stdout)
    for im in info.get("images", []):
        if Path(im["image-path"]).resolve() == image.resolve() and im.get("system-entities"):
            dev = im["system-entities"][0]["dev-entry"]
            if subprocess.run(["hdiutil", "detach", "-quiet", dev], capture_output=True).returncode:
                subprocess.run(["hdiutil", "detach", "-quiet", "-force", dev], capture_output=True)


@contextmanager
def mounted(image: Path, pw: str):
    """Read-only, out of Finder, owners respected, at a mount point inside a 0700 folder: no other user can read it."""
    tmp = Path(tempfile.mkdtemp(prefix="octo-export-"))
    point = tmp / "lab"
    r = _hdiutil("attach", "-quiet", "-readonly", "-nobrowse", "-noautoopen", "-owners", "on", "-stdinpass",
                 "-mountpoint", point, image, pw=pw)
    if r.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ExportError(f"couldn't open {short(image)}: a wrong password, or not an Octo export")
    try:
        yield point
    finally:
        if subprocess.run(["hdiutil", "detach", "-quiet", str(point)], capture_output=True).returncode:
            subprocess.run(["hdiutil", "detach", "-quiet", "-force", str(point)], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


# ---- files and the manifest --------------------------------------------------------------------------------------

def check_file(src: Path, size: int, sha: str, dst: Path | None = None) -> None:
    """A regular file of exactly `size` bytes with this SHA-256, copied to dst (0600) while it's read, if given."""
    st = os.lstat(src)
    if not stat.S_ISREG(st.st_mode) or st.st_size != size:
        raise ExportError(f"{src.name} isn't the file the manifest lists")
    h, n = hashlib.sha256(), 0
    out = os.fdopen(os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") if dst else None
    try:
        with open(src, "rb") as f:
            while chunk := f.read(1 << 20):
                n += len(chunk)
                if n > size:
                    raise ExportError(f"{src.name} is longer than the manifest says")
                h.update(chunk)
                if out:
                    out.write(chunk)
    finally:
        if out:
            out.close()
    if h.hexdigest() != sha:
        raise ExportError(f"{src.name} doesn't match its checksum: the export is damaged")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def counts(db: Path) -> dict:
    c = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        one = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
        return {"conversations": one("SELECT count(*) FROM sessions WHERE status='active'"),
                "reviewed": one("SELECT count(*) FROM sessions WHERE status='active' AND reviewed_at IS NOT NULL"),
                "people": one("SELECT count(*) FROM people"),
                "voiceprints": one("SELECT count(*) FROM voiceprints"),
                "recordings with audio": one("SELECT count(*) FROM recordings WHERE audio_deleted_at IS NULL")}
    finally:
        c.close()


def snapshot(stage: Path, home: Path, db: Path) -> list[str]:
    """The lab into stage: the database by VACUUM INTO, the audio it lists hard-linked, the small files copied.
    Returns stored audio paths with no file (already reported by the doctor)."""
    stage.mkdir(mode=0o700)
    (stage / ".pid").write_text(str(os.getpid()))
    (stage / "data").mkdir(mode=0o700)
    snap = stage / "data" / "octo.db"
    src = sqlite3.connect(db)
    try:
        src.execute("VACUUM INTO ?", (str(snap),))  # consistent, without free pages (secure_delete keeps them zeroed anyway)
    finally:
        src.close()
    c = sqlite3.connect(snap)
    try:
        c.execute("PRAGMA journal_mode=DELETE")  # readable from the read-only image; the import turns WAL back on
        audio = [r[0] for r in c.execute("SELECT path FROM recordings WHERE audio_deleted_at IS NULL "
                                         "UNION SELECT path FROM device_chunks WHERE path IS NOT NULL "
                                         "UNION SELECT path FROM drills WHERE audio_deleted_at IS NULL")]
    finally:
        c.close()
    missing = []
    for rel in audio:
        if not safe_rel(rel, ("audio/lab",)):
            raise ExportError(f"a stored audio path isn't inside the lab: {rel}")
        f = home / rel
        if not f.is_file() or f.is_symlink():
            missing.append(rel)
            continue
        (stage / rel).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.link(f, stage / rel)
        except OSError:  # another disk
            shutil.copy2(f, stage / rel)
    for root in ("data", "notes", "runs"):
        for folder, dirs, names in os.walk(home / root):
            here = Path(folder)
            dirs[:] = [d for d in dirs if not skipped(f"{(here / d).relative_to(home).as_posix()}/x")]
            for name in names:
                f = here / name
                rel = f.relative_to(home).as_posix()
                if skipped(rel) or not f.is_file() or f.is_symlink():
                    continue
                (stage / rel).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(f, stage / rel)
    return missing


def manifest(stage: Path, made_at: float, missing: list[str]) -> dict:
    files = {}
    for f in sorted(stage.rglob("*")):
        rel = f.relative_to(stage).as_posix()
        if f.is_file() and safe_rel(rel):
            files[rel] = {"size": f.stat().st_size, "sha256": _sha(f)}
    db = stage / "data" / "octo.db"
    version = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True).execute("PRAGMA user_version").fetchone()[0]
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return {"format": FORMAT, "made_at": made_at, "schema": version, "octo": commit.stdout.strip(),
            "counts": counts(db), "files": files, "missing_audio": missing}


def read_manifest(point: Path) -> dict:
    try:
        man = json.loads((point / "manifest.json").read_text())
    except (OSError, ValueError):
        raise ExportError("this image has no Octo manifest: not an Octo export") from None
    if man.get("format") != FORMAT:
        raise ExportError(f"this export's format ({man.get('format')}) is newer than this Octo's: update Octo first "
                          "(run the install line again)")
    for rel, e in man["files"].items():
        # Only what an export writes (security review L8, 28 Oct): a crafted image can't place data/filler/'s model, which
        # `octo device flash` builds into the recorder, or anything else the export leaves out. The database is its own snapshot.
        if not safe_rel(rel) or (skipped(rel) and rel != "data/octo.db") or not isinstance(e.get("size"), int) or e["size"] < 0:
            raise ExportError(f"the manifest lists a file an export can't hold: {rel!r}")
    if "data/octo.db" not in man["files"]:
        raise ExportError("the export has no database")
    return man


def check_db(db: Path) -> int:
    """An imported database is untrusted: refuse what would let it act outside the lab. Returns its schema version."""
    c = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        c.execute("PRAGMA trusted_schema=OFF")
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ExportError("the export's database is damaged")
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version > DB.SCHEMA_VERSION:
            raise ExportError("this export was made by a newer Octo: update Octo first (run the install line again)")
        fresh = sqlite3.connect(":memory:")
        for s in DB._statements(DB.SCHEMA):
            fresh.execute(s)
        q = "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view')"
        if extra := set(c.execute(q)) - set(fresh.execute(q)):
            raise ExportError(f"the export's database has code Octo doesn't make: {sorted(e[1] for e in extra)}")
        drills = " UNION SELECT path FROM drills" if version >= 9 else ""  # B18
        for (p,) in c.execute("SELECT path FROM recordings UNION SELECT path FROM device_chunks WHERE path IS NOT NULL" + drills):
            if not safe_rel(p, ("audio/lab",)):
                raise ExportError(f"the export's database points outside the lab: {p!r}")
        return version
    finally:
        c.close()


def check(image: Path, pw: str, expect: dict | None = None) -> dict:
    """Open the image and check every file, the database, and (after an export) that it holds what was written."""
    with mounted(image, pw) as point:
        man = read_manifest(point)
        for rel, e in man["files"].items():
            check_file(point / rel, e["size"], e["sha256"])
        check_db(point / "data" / "octo.db")
        if expect and (man["files"] != expect["files"] or counts(point / "data" / "octo.db") != expect["counts"]):
            raise ExportError("the image doesn't hold what was written into it")
    return man


# ---- the lab: stopped, held, swapped -----------------------------------------------------------------------------

def _launchctl(verb: str, plist: Path) -> None:
    subprocess.run(["launchctl", verb, f"gui/{os.getuid()}", str(plist)], capture_output=True)


def agents(only: str | None = None) -> list[Path]:
    return [LI.AGENTS / f"{label}.plist" for label in LI.status()
            if (LI.AGENTS / f"{label}.plist").exists() and label == (only or label)]


def holding(db: Path) -> list[int]:
    """Processes with the database open (a terminal `octo process`, a worker)."""
    paths = [str(p) for p in (db, Path(f"{db}-wal")) if p.exists()]
    if not paths:  # lsof with no file lists every process
        return []
    out = subprocess.run(["lsof", "-t", "--", *paths], capture_output=True, text=True)
    return sorted({int(p) for p in out.stdout.split()})


def wait_free(db: Path, timeout: float = 30) -> None:
    end = time.monotonic() + timeout
    while (pids := holding(db)) and time.monotonic() < end:
        time.sleep(0.5)
    if pids:
        raise ExportError(f"something still has the lab's database open (process {', '.join(map(str, pids))}): "
                          "stop it and try again")


@contextmanager
def lab_held(code: Path, keep_stopped: bool, say=print):
    """New steps held back (deploy's PAUSE), running ones waited for, the lab stopped; started again after unless
    keep_stopped (`--stop`: nothing more is pulled from the recorder, which keeps it on its card)."""
    from . import deploy as DP
    plists = agents("com.octo.lab")
    pause = code / DP.PAUSE
    pause.write_text(str(os.getpid()))
    try:
        DP.wait_idle(say)
        for p in plists:
            _launchctl("bootout", p)
        wait_free(DB_PATH)
        with FL.locked(DATA / "audio"):  # privacy.rewriting: a terminal delete waits
            yield
    finally:
        if not keep_stopped:
            for p in plists:
                _launchctl("bootstrap", p)
        pause.unlink(missing_ok=True)


def swap(a: Path, b: Path) -> None:
    """Exchange two folders in one step (macOS renamex_np, RENAME_SWAP): never a moment with no lab."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renamex_np(os.fsencode(a), os.fsencode(b), 0x2):
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), str(b))


def aside_path(kind: str, parent: Path | None = None) -> Path:
    return (parent or HOME.parent) / f"octo-before-{kind}-{datetime.now():%Y%m%d-%H%M%S}.noindex"


def asides(parent: Path | None = None) -> list[Path]:
    parent = parent or HOME.parent
    return sorted(p for p in parent.glob("octo-before-*.noindex") if ASIDE.match(p.name)) if parent.exists() else []


def _alive(pidfile: Path) -> bool:
    try:
        os.kill(int(pidfile.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def sweep(now: float | None = None, say=print) -> None:
    """Leftovers: a staging folder or half-filled import whose process is gone (plaintext copies of audio a Delete
    may since have reached), a lab an interrupted swap left out (set aside), and asides older than ASIDE_DAYS."""
    if STAGE.exists() and not _alive(STAGE / ".pid"):
        shutil.rmtree(STAGE, ignore_errors=True)
    if IMPORTING.exists() and not _alive(IMPORTING / MARK):
        if (IMPORTING / MARK).exists():
            shutil.rmtree(IMPORTING, ignore_errors=True)
        else:
            os.rename(IMPORTING, aside_path("import"))
    for p in asides():
        made = datetime.strptime(ASIDE.match(p.name).group(1), "%Y%m%d-%H%M%S").timestamp()
        if made < (now or time.time()) - ASIDE_DAYS * 86400:
            shutil.rmtree(p, ignore_errors=True)
            say(f"deleted {short(p)}, set aside {ASIDE_DAYS} days ago")


def recordings(db: Path) -> int:
    if not db.exists():
        return 0
    c = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        return c.execute("SELECT count(*) FROM recordings").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        c.close()


def _installed_here() -> Path:
    from .uninstall import installed
    code, home = installed()
    if home != HOME:
        raise ExportError(f"the lab that runs at login is in {short(home)}, not this copy's {short(HOME)}: "
                          f"run this from {short(code)}")
    return code


def _summary(c: dict) -> str:
    return ", ".join(f"{v} {k}" for k, v in c.items())


# ---- export ------------------------------------------------------------------------------------------------------

def target(path: str | None, now: datetime) -> Path:
    name = f"Octo export {now:%Y-%m-%d %H%M}.dmg"
    p = Path(path).expanduser().resolve() if path else OUT
    return p / name if p.is_dir() or not p.suffix else p


def export(path: str | None = None, keep_stopped: bool = False, password: str | None = None, yes: bool = False,
           ask=input, ask_pw=getpass.getpass, say=print) -> Path:
    code = _installed_here()
    if not DB_PATH.exists():
        raise ExportError(f"there's no lab in {short(HOME)} to export")
    now = datetime.now()
    out = target(path, now)
    if not yes and any(out.is_relative_to(c) for c in CLOUD):  # yes: the Octo app asked already
        say(f"{short(out.parent)} may be synced to iCloud: the export (voiceprints, recordings, keys) would leave this Mac.")
        if ask("Put it there anyway? [y/N] ").strip().lower() != "y":
            raise ExportError(f"nothing made; the default folder is {short(OUT)}")
    if out.exists():
        raise ExportError(f"{short(out)} is already there")
    size = sum(f.stat().st_size for r in ROOTS if (HOME / r).exists() for f in (HOME / r).rglob("*") if f.is_file())
    free = shutil.disk_usage(out.parent if out.parent.exists() else Path.home()).free
    if free < size + STOP_GB * 1e9:
        raise ExportError(f"not enough space: the export needs about {size / 1e9:.1f} GB plus {STOP_GB} GB kept free, "
                          f"and {free / 1e9:.1f} GB is free")
    pw = password if password is not None else new_password(ask_pw, say)
    if len(pw) < MIN_PASSWORD:
        raise ExportError(f"the password needs at least {MIN_PASSWORD} characters")
    sweep(say=say)
    say("holding the lab still for a moment …")
    try:
        with lab_held(code, keep_stopped, say):
            missing = snapshot(STAGE, HOME, DB_PATH)
        say("the lab is stopped and stays stopped (the recorder keeps what it records on its card)" if keep_stopped
            else "the lab is running again")
        man = manifest(STAGE, now.timestamp(), missing)
        (STAGE / "manifest.json").write_text(json.dumps(man, indent=1))
        (STAGE / "README.txt").write_text(README.format(made=f"{now:%-d %b %Y %H:%M}"))
        (STAGE / ".metadata_never_index").touch()
        say(f"writing {short(out)} ({sum(e['size'] for e in man['files'].values()) / 1e9:.2f} GB) …")
        make_image(STAGE, out, pw)
    finally:
        shutil.rmtree(STAGE, ignore_errors=True)
    say("checking it …")
    try:
        check(out, pw, expect=man)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES ('last_export_at', ?, ?)",
                     (json.dumps(now.timestamp()), time.time()))
        conn.commit()
    finally:
        conn.close()
    say(f"\nmade and checked: {short(out)}, {out.stat().st_size / 1e9:.2f} GB\n"
        f"  holds: {_summary(man['counts'])}\n"
        "  kept: the database, recordings from the last 60 days, the recorder's and phone's keys, your settings, "
        "tokens and principles, notes\n"
        "  left out: the daily backups, playback clips, logs, bench output, this Mac's browser key\n"
        + (f"  {len(missing)} recording(s) the database lists had no audio file (see octo doctor)\n" if missing else "")
        + "Anything recorded or changed after now isn't in it. It's encrypted; keep the password in your password "
        "manager, and the file somewhere other than this disk if it's your copy for a lost Mac.")
    return out


# ---- import ------------------------------------------------------------------------------------------------------

def _urls(obj) -> list[str]:
    if isinstance(obj, dict):
        return [v for k, v in obj.items() if k == "base_url" and isinstance(v, str) and v] + \
               [u for v in obj.values() for u in _urls(v)]
    return [u for v in obj for u in _urls(v)] if isinstance(obj, list) else []


def describe(man: dict, point: Path) -> str:
    c = sqlite3.connect(f"{(point / 'data' / 'octo.db').as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        cfg = {k: json.loads(v) for k, v in c.execute("SELECT key, value FROM app_config")}
        review = c.execute("SELECT choice FROM settings WHERE stage='insight'").fetchone()
    finally:
        c.close()
    try:
        urls = _urls(json.loads((point / "data" / "providers.json").read_text()))
    except (OSError, ValueError):
        urls = []
    made = datetime.fromtimestamp(man["made_at"])
    # Claude Code: the review's choice, or nothing chosen in a lab where it's the default (octo/settings.py)
    cc = review[0] == "claude-opus-5-5" if review else bool(cfg.get("claude_code"))
    return "\n".join([
        f"An export made {made:%-d %b %Y %H:%M} (Octo {man.get('octo') or '?'}): {_summary(man['counts'])}.",
        f"Anything deleted, forgotten or withdrawn after {made:%-d %b %H:%M} comes back with it.",
        f"Its settings: lab {cfg.get('lab_origin', '?')}, voiceprints need agreement: "
        f"{'yes' if cfg.get('voiceprints_need_consent') else 'no'}, review through Claude Code: "
        f"{'yes' if cc else 'no'}.",
        "Model services it sends transcripts to: " + ", ".join(urls + (["Claude Code"] if cc else []) or ["none yet"])
        + "."])


def preview(image: str, password: str) -> dict:
    """What `octo import` would say before it asks, changing nothing (the Octo app shows it in its own alert)."""
    with mounted(Path(image).expanduser().resolve(), password) as point:
        man = read_manifest(point)
        check_db(point / "data" / "octo.db")
        return {"text": describe(man, point), "have": recordings(DB_PATH), "made_at": man["made_at"],
                "counts": man["counts"]}


def import_lab(image: str, replace: bool = False, password: str | None = None, yes: bool = False,
               ask=input, ask_pw=getpass.getpass, say=print) -> int:
    from . import deploy as DP
    if HOME == ROOT:
        raise ExportError("this copy keeps its lab inside the code: import into an installed Octo (the install line)")
    _installed_here()
    image = Path(image).expanduser().resolve()
    have = recordings(DB_PATH)
    if have and not replace:
        raise ExportError(f"this lab already has {have} recordings. Nothing is merged: --replace sets this lab aside "
                          f"(beside it, {ASIDE_DAYS} days) and brings the export in")
    pw = password if password is not None else ask_pw(f"Password for {image.name}: ")
    sweep(say=say)
    old_umask = os.umask(0o077)
    try:
        with mounted(image, pw) as point:
            man = read_manifest(point)
            check_db(point / "data" / "octo.db")
            size = sum(e["size"] for e in man["files"].values())
            free = shutil.disk_usage(HOME.parent).free
            if free < size + STOP_GB * 1e9:
                raise ExportError(f"not enough space: {size / 1e9:.1f} GB plus {STOP_GB} GB kept free; {free / 1e9:.1f} GB free")
            say(describe(man, point))
            if have:
                say(f"This lab's {have} recordings are set aside, not merged.")
            if not yes and ask("Bring it in? [y/N] ").strip().lower() != "y":
                say("nothing changed")
                return 1
            IMPORTING.mkdir(mode=0o700)
            (IMPORTING / MARK).write_text(str(os.getpid()))
            say(f"copying {size / 1e9:.2f} GB, checking each file …")
            for rel, e in man["files"].items():
                (IMPORTING / rel).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                check_file(point / rel, e["size"], e["sha256"], IMPORTING / rel)
        db = IMPORTING / "data" / "octo.db"
        check_db(db)
        c = sqlite3.connect(db)
        c.execute("PRAGMA journal_mode=WAL")  # the export turned it off to be readable from the image
        c.close()
        if (DATA / "mac.key").exists():  # this install's browser and app stay let in
            shutil.copy2(DATA / "mac.key", IMPORTING / "data" / "mac.key")
        plists = agents()
        say("stopping the lab …")
        for p in plists:
            _launchctl("bootout", p)
        try:
            wait_free(DB_PATH)
            if HOME.exists():
                swap(IMPORTING, HOME)
            else:
                os.rename(IMPORTING, HOME)
            (HOME / MARK).unlink(missing_ok=True)
            if IMPORTING.exists():
                if have:
                    aside = aside_path("import")
                    os.rename(IMPORTING, aside)
                    shutil.rmtree(aside / "audio" / "lab" / "sessions", ignore_errors=True)  # playback clips: a cache
                    say(f"this lab's earlier contents are set aside in {short(aside)} for {ASIDE_DAYS} days")
                else:
                    shutil.rmtree(IMPORTING)
        finally:
            for p in plists:
                _launchctl("bootstrap", p)
    except BaseException:
        if IMPORTING.exists() and (IMPORTING / MARK).exists():
            shutil.rmtree(IMPORTING, ignore_errors=True)
        raise
    finally:
        os.umask(old_umask)
    say("starting the lab …")
    up = DP.wait_up(None) if plists else True
    say(f"\nbrought in: {_summary(counts(DB_PATH))}" + ("" if up else "\nthe lab hasn't answered yet: octo doctor") +
        "\nStill to do:\n"
        "  - speech models: octo models (they download once; the lab waits for them)\n"
        "  - the Claude sign-in for reviews: claude auth login, if this Mac doesn't have it\n"
        "  - notifications and Open at Login: in the Octo app\n"
        "  - the recorder picks up by itself and pulls what it kept on its card\n"
        "  - the phone: on a new Mac, scan the Phone page's code again\n"
        "  - on a new Mac: remove Octo from the old one (octo uninstall), or both will pull from the recorder")
    return 0
