"""Conversation lab: recordings in, conversations found, transcribed, split by speaker, understood.

Plan: docs/conversation-lab-plan.md. Everything runs locally on the owner's Mac.
"""

import os
from pathlib import Path

# Local-only (CLAUDE.md): pyannote 4 sends usage telemetry to otel.pyannote.ai by default -- off.
os.environ["PYANNOTE_METRICS_ENABLED"] = "false"
# onnxruntime (WeSpeaker voiceprints; pyannote loads it too) queues usage events in
# ~/Library/Application Support/Microsoft/DeveloperTools/.onnxruntime and uploads them to
# mobile.events.data.microsoft.com. Off; checked 2 Oct: with this set nothing is written there.
# Its uploader thread also crashed two `speakers` workers on exit (2 Oct, onnxruntime 1.30).
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
# Claude Code (`claude -p`, for the review and the Hindi merge) reports its own usage and errors to
# Anthropic unless told not to (review 4 Oct, M44). Off: claude_code.clean_env keeps these two, and
# the children inherit them. The transcript itself goes to Claude by design (D11); this is the rest.
os.environ.setdefault("DISABLE_TELEMETRY", "1")
os.environ.setdefault("DISABLE_ERROR_REPORTING", "1")
# Models are fetched once, then never again: no network calls to Hugging Face at run time.
# Set OCTO_ALLOW_DOWNLOAD=1 for the one-off download of a new model.
if os.environ.get("OCTO_ALLOW_DOWNLOAD") != "1":
    os.environ["HF_HUB_OFFLINE"] = "1"

# Heavy work (the pipeline's runner and workers, drills, filler training) starts at background priority:
# efficiency cores and throttled I/O, inherited by its children, so it slows neither the owner's apps nor the
# lab's pages, which run at normal priority (5 Nov: the whole server was Background, every page ~6x slower).
BACKGROUND = ["/usr/sbin/taskpolicy", "-b"] if os.path.exists("/usr/sbin/taskpolicy") else []

ROOT = Path(__file__).resolve().parent.parent  # the code
# A release install runs from versions/<v>, reached through the `current` link beside it (octo/deploy.py `switch`):
# what's started from it later (the login items, the Octo app, commands it prints) names `current`, so it follows an
# update. Elsewhere (a clone, the live copy) it's the code itself.
LIVE = ROOT.parent.parent / "current" if ROOT.parent.name == "versions" else ROOT
HOME_FILE = ROOT / ".octo-home"  # gitignored: one line, the folder the live lab keeps its data in


def _home() -> tuple[Path, str]:
    """Where the lab keeps what it records (14 Oct: out of the checkout, out of ~/Downloads): OCTO_HOME, else
    the path in .octo-home (written into the live copy only, so `octo` from a terminal there finds the same lab
    as the login item), else the checkout itself, as before. The layout under it is the same either way
    (data/, audio/lab/, notes/), so the paths the database stores, relative to HOME, stay valid."""
    if os.environ.get("OCTO_HOME"):
        return Path(os.environ["OCTO_HOME"]).expanduser().resolve(), "OCTO_HOME"
    if HOME_FILE.exists() and HOME_FILE.read_text().strip():
        return Path(HOME_FILE.read_text().strip()).expanduser().resolve(), HOME_FILE.name
    return ROOT, "checkout"


HOME, HOME_FROM = _home()
DATA = HOME / "data"  # gitignored: SQLite database
DB_PATH = DATA / "octo.db"
AUDIO = HOME / "audio" / "lab"  # gitignored: recordings and per-session clips
NOTES = HOME / "notes"  # gitignored: markdown notes
RATE = 16000


def bin_path(name: str) -> Path:
    """A command of this install's environment, by the path that outlives an update (LIVE)."""
    import sys
    return LIVE / ".venv" / "bin" / name if LIVE != ROOT else Path(sys.executable).parent / name
