"""Start the lab when the owner logs in: `octo login-item install | remove | status`.

Three per-user LaunchAgents (no admin rights, nothing system-wide):
  com.octo.ollama   `ollama serve` on 127.0.0.1 only, and only while a step runs a local model (5 Nov): choosing one
                    starts it (`start_ollama`), and the next install lets it go once none does
  com.octo.lab      `bench serve` (the web UI; the phone listener too, if switched on)
  com.octo.relay    octo/relay.py under Apple's Python, on 127.0.0.1 only: the lab's way to the recorder,
                    which macOS's Local Network privacy blocks for it (5 Oct)
Logs go to data/logs/. `remove` unloads and deletes all three; nothing else is touched.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from . import DATA, HOME, LIVE, ROOT, bin_path

AGENTS = Path.home() / "Library" / "LaunchAgents"
# The relay's copy: Apple's Python, run by launchd, may not read ~/Downloads (macOS's Files and Folders
# privacy), where the project lives. `install` copies octo/relay.py here each time.
RELAY_COPY = Path.home() / "Library" / "Application Support" / "octo" / "relay.py"


def _plists() -> dict[str, dict]:
    logs = DATA / "logs"
    ollama = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
    # Umask 077: what the lab writes (recordings, transcripts, voiceprints, keys) is the owner's alone (14 Oct).
    common = {"RunAtLoad": True, "KeepAlive": True, "ProcessType": "Background", "Umask": 0o077}
    # The lab's home said outright, besides .octo-home, so the login item can't fall back to the checkout.
    env = {} if HOME == ROOT else {"EnvironmentVariables": {"OCTO_HOME": str(HOME)}}
    return {
        "com.octo.ollama": {**common, "Label": "com.octo.ollama", "ProgramArguments": [ollama, "serve"],
                            "EnvironmentVariables": {"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_FLASH_ATTENTION": "1",
                                                     "OLLAMA_KV_CACHE_TYPE": "q8_0"},
                            "StandardOutPath": str(logs / "ollama.log"), "StandardErrorPath": str(logs / "ollama.log")},
        # The server at normal priority (Standard: launchd's default, what a terminal's process gets; Interactive is
        # for an app's own UI). Background made every page ~6x slower (5 Nov); its heavy work lowers itself (BACKGROUND).
        "com.octo.lab": {**common, "Label": "com.octo.lab", "ProcessType": "Standard",
                         "ProgramArguments": [str(bin_path("bench")), "serve", "--no-browser"],
                         "WorkingDirectory": str(LIVE), **env,
                         "StandardOutPath": str(logs / "lab.log"), "StandardErrorPath": str(logs / "lab.log")},
        "com.octo.relay": {**common, "Label": "com.octo.relay",
                           "ProgramArguments": ["/usr/bin/python3", str(RELAY_COPY)],
                           "StandardOutPath": str(logs / "relay.log"), "StandardErrorPath": str(logs / "relay.log")},
    }


def install() -> list[str]:
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    AGENTS.mkdir(parents=True, exist_ok=True)
    RELAY_COPY.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "octo" / "relay.py", RELAY_COPY)
    # Its key beside it (0600, made once; review 8 Oct, finding 17): only the lab may use the relay.
    from .device import RELAY_KEY
    from .mackey import key as make_key
    assert RELAY_KEY.parent == RELAY_COPY.parent
    make_key(RELAY_KEY)
    done = []
    plists = _plists()
    if not wants_ollama():  # it would hold memory for nothing (tech review 5 Nov, risk 6)
        _unload("com.octo.ollama")
        del plists["com.octo.ollama"]
    for label, plist in plists.items():
        if label == "com.octo.ollama" and not has_ollama():
            continue  # no Ollama on this Mac: only local review models need it (install it, then run this again)
        _load(label, plist)
        done.append(label)
    return done


def wants_ollama() -> bool:
    """Whether any step runs a local model in this lab. A lab that can't be opened yet (a fresh install) has none."""
    from . import settings as ST
    from .db import connect
    try:
        conn = connect()
    except Exception:
        return False
    try:
        return any((c := ST.selected(conn, step)) and c.ollama for step in ST.STEPS)
    finally:
        conn.close()


def start_ollama() -> bool:
    """Ollama's login item, for a local model being chosen. False when Ollama isn't on this Mac."""
    if not has_ollama():
        return False
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    AGENTS.mkdir(parents=True, exist_ok=True)
    _load("com.octo.ollama", _plists()["com.octo.ollama"])
    return True


def has_ollama() -> bool:
    return os.access(_plists()["com.octo.ollama"]["ProgramArguments"][0], os.X_OK)


def _load(label: str, plist: dict) -> None:
    path = AGENTS / f"{label}.plist"
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
    path.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True)


def _unload(label: str) -> bool:
    path = AGENTS / f"{label}.plist"
    if not path.exists():
        return False
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
    path.unlink()
    return True


def remove() -> list[str]:
    done = []
    for label in _plists():
        if _unload(label):
            done.append(label)
    RELAY_COPY.unlink(missing_ok=True)
    return done


def status() -> dict[str, bool]:
    return {label: (AGENTS / f"{label}.plist").exists() for label in _plists()}
