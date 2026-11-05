"""`octo deploy [--now]` (or `octo update`): bring the lab up to origin/main and restart it (14 Oct).

A beta tester's lab came from a release (scripts/beta_release.sh; docs/beta-tester.md) and has no git: `octo update`, or
the Octo app's *Update Octo…*, which the menu offers once `update_ready` sees a newer version.txt beside the release,
downloads it from where it was installed (.octo-release), lays it over the code and runs its install.sh (`update_release`).

The lab runs from its own checkout (the live copy, ~/Developer/octo-live: main only); work happens in
another worktree (~/Developer/octo). This updates the live copy, whichever copy it's run from:
  1. find it: the folder the com.octo.lab login item runs in
  2. refuse unless it's on main with no changes to tracked files
  3. hold new steps back (PAUSE) and wait until no step is running, so code doesn't change under one (--now: don't
     wait); the hold ends just before the restart
  4. fast-forward to origin/main
  5. Python packages again if pyproject.toml or requirements.lock changed; the web UI again if ui/ did
  6. restart: all three login items if octo/relay.py or octo/login_item.py changed, else only the lab
  7. wait until the lab answers on 127.0.0.1:8765
It notes the commit it last started the lab on (.git/octo-deployed), so a deploy cut short (Ctrl-C
while waiting, a failed install) is finished by running it again.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .login_item import AGENTS

LAB = "com.octo.lab"
LAB_URL = "http://127.0.0.1:8765/"
PAUSE = ".octo-deploying"  # in the live copy while a deploy changes its code: the runner starts no step (audit 14 Oct)


def paused(repo: Path) -> bool:
    """A deploy is changing this copy's code. Its pid is in the file, so one that was killed holds nothing back."""
    try:
        pid = int((repo / PAUSE).read_text())
    except (OSError, ValueError):
        return False
    return _alive(pid)


class DeployError(Exception):
    pass


def lab_plist(agents: Path = AGENTS) -> dict:
    path = agents / f"{LAB}.plist"
    if not path.exists():
        raise DeployError("the lab isn't a login item yet: run `octo login-item install` in the live copy")
    return plistlib.loads(path.read_bytes())


def _env() -> dict:
    # The live copy finds its own home (.octo-home); one set for this copy mustn't leak into it.
    return {k: v for k, v in os.environ.items() if k != "OCTO_HOME"}


def _run(cmd: list, cwd: Path) -> str:
    try:
        proc = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, env=_env())
    except FileNotFoundError:
        raise DeployError(f"{cmd[0]} not found") from None
    if proc.returncode:
        raise DeployError(f"{' '.join(map(str, cmd))} failed:\n{(proc.stderr or proc.stdout).strip()[-2000:]}")
    return proc.stdout.strip()


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", repo, *args], repo)


def check_clean(repo: Path) -> None:
    branch = _git(repo, "branch", "--show-current")
    if branch != "main":
        raise DeployError(f"{repo} is on {branch or 'no branch'}, not main: the live copy runs main only")
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise DeployError(f"{repo} has changes to tracked files: develop in a worktree, never in the live copy")


def plan(changed: list[str]) -> dict[str, bool]:
    """What an update needs, from the files it changes."""
    return {
        "packages": any(f in ("pyproject.toml", "requirements.lock") for f in changed),
        "npm_ci": any(f in ("ui/package.json", "ui/package-lock.json") for f in changed),
        "ui": any(f.startswith("ui/") for f in changed),
        "login_items": any(f in ("octo/relay.py", "octo/login_item.py") for f in changed),
        "mac_app": any(f.startswith("macapp/") or f.endswith("AppIcon.appiconset/icon-1024.png") for f in changed),
        "phone": any(f.startswith("mobile/") for f in changed),
    }


def steps_running() -> int:
    """Steps run one per `python -m octo.worker` (pipeline._in_worker); any copy's count, to be safe."""
    proc = subprocess.run(["pgrep", "-f", "octo[.]worker"], capture_output=True, text=True)
    return len(proc.stdout.split())


def wait_idle(say, poll: float = 10) -> None:
    told = False
    while n := steps_running():
        if not told:
            say(f"waiting for {n} running step{'s' if n > 1 else ''} to finish (Ctrl-C stops; --now doesn't wait)")
            told = True
        time.sleep(poll)


def lab_pid() -> int | None:
    proc = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LAB}"], capture_output=True, text=True)
    m = re.search(r"^\s*pid = (\d+)", proc.stdout, re.M)
    return int(m.group(1)) if m else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def wait_up(old_pid: int | None, timeout: float = 180, url: str = LAB_URL) -> bool:
    """The old lab gone (it can linger a few seconds and still answer), then the new one answering."""
    end = time.monotonic() + timeout
    while old_pid and _alive(old_pid) and time.monotonic() < end:
        time.sleep(1)
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(url, timeout=5)
            return True
        except urllib.error.HTTPError:
            return True  # it answered; the page may want the Mac's key
        except OSError:
            time.sleep(3)
    return False


def restart(repo: Path, login_items: bool) -> None:
    if login_items:
        _run([repo / ".venv" / "bin" / "octo", "login-item", "install"], repo)
    else:
        _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAB}"], repo)


def deploy(now: bool = False, say=print, agents: Path = AGENTS, allow_older: bool = False) -> int:
    plist = lab_plist(agents)
    repo = Path(plist["WorkingDirectory"])
    if not (repo / ".git").exists() and (repo / RELEASE).exists():
        return update_release(repo, now, say, allow_older=allow_older)
    check_clean(repo)
    if (repo / ".node" / "bin").is_dir():  # a clone's own Node (install.sh), for the pages
        os.environ["PATH"] = f"{repo / '.node' / 'bin'}:{os.environ.get('PATH', '')}"
    marker = Path(repo, _git(repo, "rev-parse", "--git-path", "octo-deployed"))
    _git(repo, "fetch", "--quiet", "origin", "main")
    old, new = _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "origin/main")
    started = marker.read_text().strip() if marker.exists() else ""
    if old == new == started:
        say(f"the lab is already on {_git(repo, 'log', '-1', '--format=%h %s')}")
        return 0
    if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", old, new]).returncode:
        raise DeployError(f"main in {repo} has commits that aren't on origin/main: sort that out by hand")
    # Waiting alone wasn't enough: on a busy queue the runner starts the next step a moment after the last one ends.
    (repo / PAUSE).write_text(f"{os.getpid()}\n")
    try:
        if not now:
            wait_idle(say)
        try:  # from what the lab last started on, so a deploy cut short redoes what it skipped
            changed = _git(repo, "diff", "--name-only", started or old, new).splitlines()
        except DeployError:
            changed = _git(repo, "diff", "--name-only", old, new).splitlines()
        if old != new:
            _git(repo, "merge", "--ff-only", "--quiet", new)
        todo = plan(changed)
        if todo["packages"]:
            say("installing Python packages (requirements.lock)")
            python = repo / ".venv" / "bin" / "python"
            _run([python, "-m", "pip", "install", "--quiet", "-r", "requirements.lock"], repo)
            _run([python, "-m", "pip", "install", "--quiet", "--no-deps", "-e", "."], repo)
        if todo["ui"]:
            say("building the web UI")
            if todo["npm_ci"]:
                _run(["npm", "ci", "--no-audit", "--no-fund"], repo / "ui")
            _run(["npm", "run", "build"], repo / "ui")
    finally:
        (repo / PAUSE).unlink(missing_ok=True)  # before the restart: the new lab's first pass must find it gone
    pid = lab_pid()
    say("restarting the login items" if todo["login_items"] else "restarting the lab")
    restart(repo, todo["login_items"])
    if not wait_up(pid):
        raise DeployError(f"the lab didn't answer within 3 minutes: see {plist.get('StandardErrorPath', 'lab.log')}")
    if todo["mac_app"]:  # after the lab: a failed build keeps the app that's there and only warns
        say("rebuilding the Octo app")
        try:
            say(_run([repo / ".venv" / "bin" / "octo", "launcher", "install"], repo).strip())
        except DeployError as e:
            say(f"the Octo app wasn't rebuilt: {e}")
    if todo["phone"]:  # not built here: the phone may not be near, and Xcode signs it (octo/iphone.py)
        say("the iPhone app changed: `octo phone install` puts the new one on your phone")
    marker.write_text(new + "\n")
    say(f"the lab is on {_git(repo, 'log', '-1', '--format=%h %s')} ({len(changed)} files changed)")
    return 0


# ---- an install from a release (the one-line installer; beta testers) --------------------------------------------

RELEASE = ".octo-release"  # the release's URL, written by scripts/bootstrap.sh
# Cloudflare refuses Python's own User-Agent (403 for "Python-urllib"), so the release is asked for as Octo.
AGENT = {"User-Agent": "octo-update"}
KEEP = (".venv", ".node", "ui/node_modules", ".octo-home", RELEASE, PAUSE, "data", "audio", "notes", "runs")


def latest(release_url: str, timeout: float = 20) -> tuple[str, str] | None:
    """What version.txt beside the release says (scripts/release_files.sh): its version and octo.tar.gz's SHA-256 ("" in a
    release from before 5 Nov), or None when it can't be read (offline, or no release there)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(release_url.rsplit("/", 1)[0] + "/version.txt", headers=AGENT),
                                    timeout=timeout) as r:
            lines = r.read(500).decode().split("\n")
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    sha = next((ln.split()[1].lower() for ln in lines[1:] if ln.split()[:1] == ["sha256"] and len(ln.split()) > 1), "")
    return (lines[0].strip(), sha) if lines[0].strip() else None


def order(version: str) -> tuple[int, ...] | None:
    """2026.11.05-2+3f2a9c1b2d4e (scripts/release_files.sh): the date and that day's counter, or None for a version
    from before 5 Nov (a content hash alone)."""
    m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})-(\d+)(\+\S*)?", version)
    return tuple(int(g) for g in m.groups()[:4]) if m else None


def newer(new: str, old: str) -> bool:
    """Whether new comes after old. A content hash alone (before 5 Nov) comes before any dated version; two of them
    only differ."""
    a, b = order(new), order(old)
    if a and b:
        return a > b
    return bool(a) if a or b else new != old


def installed(repo: Path) -> str:
    try:
        return (repo / "VERSION").read_text().strip()
    except OSError:
        return ""


VERSIONS = "versions"


def versioned(repo: Path) -> bool:
    """A release install made by the one-line installer (…/octo/app before 5 Nov, …/octo/current since) with its lab
    kept outside it: an update goes into versions/<v> beside it and runs only once its own install.sh has built it
    (`switch`), so a failed install leaves the running version as it was (5 Nov review, risk 3)."""
    home = repo / ".octo-home"
    if repo.name not in ("app", "current") or not home.exists():
        return False
    return not Path(home.read_text().strip()).expanduser().resolve().is_relative_to(repo.resolve())


def _link(link: Path, target: Path) -> None:
    """link -> target, replaced in one rename: anything that follows it sees the old one or the new, never neither."""
    tmp = link.with_name(f".{link.name}.new")
    tmp.unlink(missing_ok=True)
    os.symlink(os.path.relpath(target, link.parent), tmp)
    os.replace(tmp, link)


def switch(new: Path, base: Path | None = None) -> None:
    """Make `new` the version that runs: `current` points at it (the login items and the Octo app go through it),
    `previous` at the one that ran before (`octo update --back`), every other version goes, and `app` (where the code
    was before 5 Nov) follows `current` once the old folder has gone. install.sh calls this
    once a versions/<v> is built; `back` with the one before. Never a folder with a lab's data in it."""
    base = base or new.parent.parent
    cur = base / "current"
    old = cur.resolve() if cur.is_symlink() else base / "app" if (base / "app").is_dir() else None
    new = new.resolve()
    if old and old != new:
        _link(base / "previous", old)
    _link(cur, new)
    keep = {new, (base / "previous").resolve()}
    for d in [*sorted((base / VERSIONS).glob("*")), base / "app"]:
        if d.is_dir() and not d.is_symlink() and d.resolve() not in keep and not (d / "data").exists():
            shutil.rmtree(d)
    if not (base / "app").exists() and not (base / "app").is_symlink():
        (base / "app").symlink_to("current")  # what the docs and older notes name: …/octo/app/.venv/bin/octo


def schema_of(code: Path) -> int | None:
    m = re.search(r"^SCHEMA_VERSION = (\d+)", (code / "octo" / "db.py").read_text(), re.M) if (code / "octo" / "db.py").exists() else None
    return int(m[1]) if m else None


def back(say=print, agents: Path = AGENTS) -> int:
    """`octo update --back`: the version that ran before this one, unless this one has already moved the database on."""
    import sqlite3
    from .uninstall import installed as where
    repo = Path(lab_plist(agents)["WorkingDirectory"])
    prev = repo.parent / "previous"
    if not prev.is_dir():
        raise DeployError("there's no earlier version kept to go back to (only an install from a release keeps one)")
    try:
        conn = sqlite3.connect((where(agents)[1] / "data" / "octo.db").as_uri() + "?mode=ro", uri=True)
        have = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
    except sqlite3.Error:
        have = 0
    want = schema_of(prev)
    if want is not None and have > want:
        raise DeployError(f"this version moved the lab's database on (schema {have}; {installed(prev)} reads {want}), so "
                          f"the one before can't open it: nothing was changed. data/backups/pre-schema-{want}.db is the "
                          "database from before, without what has happened since")
    switch(prev.resolve(), repo.parent)
    say("restarting the lab on it")
    _run([repo / ".venv" / "bin" / "octo", "login-item", "install"], repo)
    say(f"Octo is back on {installed(repo)}")
    return 0


def update_release(repo: Path, now: bool = False, say=print, fetch=None, allow_older: bool = False) -> int:
    url = (repo / RELEASE).read_text().strip()
    got_latest = (fetch or latest)(url)
    if got_latest is None:
        raise DeployError("couldn't reach where Octo was installed from: check this Mac is online, then try again")
    new, sha = got_latest
    if new == installed(repo):
        say(f"Octo is up to date ({new})")
        return 0
    if not newer(new, installed(repo)) and not allow_older:
        raise DeployError(f"the release there ({new}) is older than this one ({installed(repo)}): nothing was changed. "
                          "`octo update --allow-older` goes back to it")
    if not sha:
        raise DeployError("there's no checksum beside the new version to check it against: nothing was changed")
    if not re.fullmatch(r"[\w+-][\w.+-]*", new):  # it names a folder
        raise DeployError(f"the release there says its version is {new!r}, which isn't one: nothing was changed")
    dest = repo.parent / VERSIONS / new if versioned(repo) else repo
    (repo / PAUSE).write_text(f"{os.getpid()}\n")
    try:
        if not now:
            wait_idle(say)
        with tempfile.TemporaryDirectory() as tmp:
            say("downloading the new version")
            got = Path(tmp) / "octo.tar.gz"
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=60) as r, open(got, "wb") as f:
                    shutil.copyfileobj(r, f)
            except OSError as e:
                raise DeployError(f"the download stopped ({e}): check this Mac is online, then try again") from None
            if got.read_bytes()[:2] != b"\x1f\x8b":
                raise DeployError("what came down isn't an Octo release: nothing was changed")
            # Checked against version.txt's SHA-256 before anything is unpacked (5 Nov review, risk 2).
            if hashlib.sha256(got.read_bytes()).hexdigest() != sha:
                raise DeployError("the download doesn't match its checksum: nothing was changed. Try again; if it says "
                                  "this twice, don't update from there")
            (Path(tmp) / "octo").mkdir()
            _run(["tar", "-xzf", got, "-C", Path(tmp) / "octo", "--strip-components", "1"], repo)
            if not (Path(tmp) / "octo" / "install.sh").exists():
                raise DeployError("that download isn't an Octo release (no install.sh): nothing was changed")
            # Into its own folder beside the running one (a clone or an install elsewhere: over the code, files it no
            # longer has removed); the environment, Node and the lab's own folders kept.
            dest.mkdir(parents=True, exist_ok=True)
            _run(["rsync", "-a", "--checksum", "--delete", *(f"--exclude=/{k}" for k in KEEP), f"{tmp}/octo/", f"{dest}/"], repo)
            for f in (".octo-home", RELEASE):
                if dest != repo:
                    shutil.copyfile(repo / f, dest / f)
    finally:
        (repo / PAUSE).unlink(missing_ok=True)
    say("installing it (packages, the login items, the Octo app if it changed)")
    proc = subprocess.run([str(dest / "install.sh")], cwd=dest, capture_output=True, text=True,
                          env={**_env(), "OCTO_UPDATE": "1"})
    if proc.returncode:
        if dest != repo and dest.resolve() not in (repo.resolve(), (repo.parent / "previous").resolve()):
            shutil.rmtree(dest, ignore_errors=True)
            raise DeployError("the new version didn't install, so the one you had is still running:\n"
                              f"{(proc.stdout + proc.stderr).strip()[-2000:]}")
        raise DeployError(f"the new version didn't install:\n{(proc.stdout + proc.stderr).strip()[-2000:]}")
    say(f"Octo is now on {new}")
    return 0


# ---- is there something newer? (beta, 31 Oct: the lab offers, the owner chooses) --------------------------------

CHECK_EVERY_S = 3600
_UPDATE = {"at": 0.0, "behind": 0}


def update_ready(repo: Path, now=time.time) -> int:
    """Whether there's something newer, from memory: for a release install, 1 when version.txt beside it is newer than
    this VERSION (`newer`); for a clone, how many commits its origin/main is ahead. Asked at most hourly, in a thread, so asking
    never waits on the network; offline it says 0. No server of ours: where it was installed from."""
    if now() - _UPDATE["at"] >= CHECK_EVERY_S and ((repo / ".git").exists() or (repo / RELEASE).exists()):
        _UPDATE["at"] = now()
        threading.Thread(target=_check, args=(repo,), daemon=True, name="octo-update-check").start()
    return _UPDATE["behind"]


def _check(repo: Path) -> None:
    if not (repo / ".git").exists():
        new = latest((repo / RELEASE).read_text().strip())
        if new is not None:
            _UPDATE["behind"] = int(newer(new[0], installed(repo)))
        return
    try:  # never a prompt: no terminal to answer it in (the clone's own key, core.sshCommand, needs none)
        env = {**_env(), "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", "origin", "main"], env=env, capture_output=True,
                       timeout=60, check=True)
        if _git(repo, "branch", "--show-current") == "main":
            _UPDATE["behind"] = int(_git(repo, "rev-list", "--count", "HEAD..origin/main"))
    except (subprocess.SubprocessError, OSError, DeployError, ValueError):
        pass
