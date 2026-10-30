"""The models the lab needs on this Mac, and fetching them (product plan 4.3, 7 Oct).

The lab runs offline (HF_HUB_OFFLINE, octo/__init__.py): every model must be in the Hugging Face cache
before a step uses it. `octo models` lists what the steps as chosen need; `octo models download` fetches
what's missing, in a child process allowed online.

What each is, its licence and size (checked 7 Oct 2026 on huggingface.co; sizes as cached on the owner's Mac):

| Repo | Licence | Size | Note |
|---|---|---|---|
| Qwen/Qwen3-ASR-1.7B | Apache-2.0 | 4.7 GB | English words |
| Qwen/Qwen3-ForcedAligner-0.6B | Apache-2.0 | 1.8 GB | word timings |
| mlx-community/whisper-large-v3-mlx | MIT | 3.1 GB | the language; Hindi drafts |
| pyannote/speaker-diarization-3.1 | MIT | <1 MB | who spoke when; **gated** |
| pyannote/segmentation-3.0 | MIT | 6 MB | its segmentation; **gated** |
| pyannote/wespeaker-voxceleb-resnet34-LM | CC-BY-4.0 | 27 MB | its speaker embeddings |
| Wespeaker/wespeaker-voxceleb-resnet293-LM | CC-BY-4.0 | 110 MB | voiceprints |

Gated means: sign in at huggingface.co, accept the model's conditions on its page (approved at once),
and log in here (`hf auth login`) before downloading. pyannote asks for that; the lab respects it rather
than shipping the files itself. CC-BY-4.0 asks for credit: the About page and README give it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

GATED = ("pyannote/speaker-diarization-3.1", "pyannote/segmentation-3.0")
VOICEPRINT_FILE = ("Wespeaker/wespeaker-voxceleb-resnet293-LM", "voxceleb_resnet293_LM.onnx")

# The exact versions the lab has run and been tested on (review 8 Oct, finding 18): what the owner's cache held on 8 Oct.
# A fresh install downloads these and points the cache's "main" at them, because the steps load "main", offline:
# without this a beta tester got whatever the repo held that day, and pyannote's checkpoints are pickles, so a
# changed repo could run code. Pinning the steps' own loading would change their parameters and redo every
# conversation; instead `octo doctor` says when a cached model isn't one of these. A new version is tested, then
# written here.
PINNED = {
    "Qwen/Qwen3-ASR-1.7B": "7278e1e70fe206f11671096ffdd38061171dd6e5",
    "Qwen/Qwen3-ForcedAligner-0.6B": "c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
    "mlx-community/whisper-large-v3-mlx": "49e6aa286ad60c14352c404340ded53710378a11",
    "pyannote/speaker-diarization-3.1": "84fd25912480287da0247647c3d2b4853cb3ee5d",
    "pyannote/segmentation-3.0": "e66f3d3b9eb0873085418a7b813d3b369bf160bb",
    "pyannote/wespeaker-voxceleb-resnet34-LM": "837717ddb9ff5507820346191109dc79c958d614",
    "pyannote/speaker-diarization-community-1": "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee",
    "Wespeaker/wespeaker-voxceleb-resnet293-LM": "6e6bffe5bf3d772a1f143dc6dbfea58a0799ea83",
}


def cache_dir() -> Path:
    from huggingface_hub.constants import HF_HUB_CACHE
    return Path(HF_HUB_CACHE)


def cached_revision(repo: str, hub: Path | None = None) -> str | None:
    """The version the steps load: the commit the cache's "main" points at."""
    ref = (hub or cache_dir()) / ("models--" + repo.replace("/", "--")) / "refs" / "main"
    try:
        return ref.read_text().strip() or None
    except OSError:
        return None


def drift(conn, hub: Path | None = None) -> list[dict]:
    """Cached models that aren't the tested version."""
    out = []
    for repo in required(conn):
        have, want = cached_revision(repo, hub), PINNED.get(repo)
        if have and want and have != want:
            out.append({"repo": repo, "have": have, "want": want})
    return out


def required(conn) -> list[str]:
    """Hugging Face repos the steps need, as chosen on the Pipeline page (each step's own when nothing is)."""
    from . import settings as ST
    out: list[str] = []
    for step in ST.STEPS:
        choice = ST.selected(conn, step)
        for repo in (choice.hf if choice else ()):
            if repo not in out:
                out.append(repo)
    if VOICEPRINT_FILE[0] not in out:
        out.append(VOICEPRINT_FILE[0])
    return out


def missing_for(conn, stage: str) -> list[str]:
    """The models this step needs that aren't downloaded yet (its choice on the Pipeline page; identify's voiceprints).
    The runner holds its queued runs until they're in, instead of failing a new lab's first conversation (onboarding
    review 7, 28 Oct); the Fetcher's `done` sets them going."""
    from . import settings as ST
    step = ST.BY_STAGE.get(stage)
    choice = ST.selected(conn, step) if step else None
    repos = [*(choice.hf if choice else ()), *([VOICEPRINT_FILE[0]] if stage == "identify" else [])]
    return [r for r in repos if not cached(r)]


def cached(repo: str) -> bool:
    from .settings import _hf_cached
    return _hf_cached(repo)


def status(conn) -> list[dict]:
    return [{"repo": r, "cached": cached(r), "gated": r in GATED} for r in required(conn)]


_FETCH = """
import json, sys
from huggingface_hub import hf_hub_download, snapshot_download
from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
from pathlib import Path
repo, file, rev = sys.argv[1], (sys.argv[2] or None), (sys.argv[3] or None)
try:
    hf_hub_download(repo, file, revision=rev) if file else snapshot_download(repo, revision=rev)
    if rev:  # the steps load "main", offline: point it at the tested version (octo/models.py PINNED)
        from huggingface_hub.constants import HF_HUB_CACHE
        ref = Path(HF_HUB_CACHE) / ("models--" + repo.replace("/", "--")) / "refs" / "main"
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(rev)
    print(json.dumps({"ok": True}))
except GatedRepoError:
    print(json.dumps({"ok": False, "gated": True}))
except RepositoryNotFoundError:
    print(json.dumps({"ok": False, "error": "not found (or gated and not logged in)", "gated": True}))
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)[:300]}))
"""


def download(repo: str, run=subprocess.run) -> dict:
    """Fetch one repo into the cache, online, in a child process (this one stays offline)."""
    file = VOICEPRINT_FILE[1] if repo == VOICEPRINT_FILE[0] else ""
    env = {**os.environ, "HF_HUB_OFFLINE": "0"}
    r = run([sys.executable, "-c", _FETCH, repo, file, PINNED.get(repo, "")], capture_output=True, text=True, env=env)
    try:
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"ok": False, "error": (r.stderr or r.stdout or "no answer").strip()[-300:]}


def gated_help(repo: str) -> str:
    return (f"{repo} is gated: sign in at https://huggingface.co, open https://huggingface.co/{repo} and accept its "
            "conditions (approved at once), then run `hf auth login` here with a read token and try again.")


# ---- from the setup page (open-source plan I3, I4) -------------------------------------------------------------

# For progress only: the sizes in the table above, in GB.
SIZE_GB = {"Qwen/Qwen3-ASR-1.7B": 4.7, "Qwen/Qwen3-ForcedAligner-0.6B": 1.8, "mlx-community/whisper-large-v3-mlx": 3.1,
           "pyannote/speaker-diarization-3.1": 0.001, "pyannote/segmentation-3.0": 0.006,
           "pyannote/wespeaker-voxceleb-resnet34-LM": 0.027, "Wespeaker/wespeaker-voxceleb-resnet293-LM": 0.11}

_ACCOUNT = """
import json, os, sys
from huggingface_hub import HfApi, auth_check, login
from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
token = os.environ.pop("OCTO_HF_TOKEN", "")
try:
    if token:
        login(token=token, add_to_git_credential=False, skip_if_logged_in=False)
    me = HfApi().whoami()
    name = me["name"]
    role = ((me.get("auth") or {}).get("accessToken") or {}).get("role")
except Exception as e:
    said = str(e)
    signed_out = type(e).__name__ == "LocalTokenNotFoundError"  # never signed in on this Mac: not an error
    refused = any(w in said for w in ("401", "Unauthorized", "Invalid", "invalid"))
    print(json.dumps({"name": None, "error": None if signed_out else
                      "that token didn't work: copy it again from Settings → Access Tokens" if refused or token and not
                      said else "couldn't reach Hugging Face: check this Mac is online"}))
    sys.exit()
access = {}
for repo in sys.argv[1:]:
    try:
        auth_check(repo)
        access[repo] = "ok"
    except (GatedRepoError, RepositoryNotFoundError):  # a gated repo you haven't accepted can answer either
        access[repo] = "accept"
    except Exception as e:
        access[repo] = "error: " + str(e)[:200]
print(json.dumps({"name": name, "role": role, "access": access}))
"""


def account(token: str = "", run=subprocess.run) -> dict:
    """Who this Mac is signed in to Hugging Face as, and whether that account may download each gated model.
    With a token, signs in first (it's kept where `hf auth login` keeps it, ~/.cache/huggingface/token).
    Online, in a child process, as `download` is; the token goes in its environment, never its arguments."""
    env = {**os.environ, "HF_HUB_OFFLINE": "0", **({"OCTO_HF_TOKEN": token} if token else {})}
    r = run([sys.executable, "-c", _ACCOUNT, *GATED], capture_output=True, text=True, env=env, timeout=60)
    try:
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"name": None, "error": "couldn't reach Hugging Face"}


def _size_gb(repo: str) -> float:
    blobs = cache_dir() / ("models--" + repo.replace("/", "--")) / "blobs"
    try:
        return sum(f.stat().st_size for f in blobs.iterdir()) / 1e9
    except OSError:
        return 0.0


class Fetcher:
    """Downloads what the steps need in the background, one model at a time (open-source plan I4): the lab is up
    and the setup page usable while about 10 GB come down, instead of the installer holding a terminal for it.
    A gated model the account can't have yet waits for the Hugging Face step, then `start()` again. When every
    model is in, `done` runs: failed steps are tried again, so a memo added early doesn't wait for Try again."""

    def __init__(self, db: Callable, fetch: Callable[[str], dict] = download, done: Callable[[], None] | None = None):
        self.db, self.fetch, self.done = db, fetch, done
        self.running = False
        self.now: str | None = None
        self.result: dict[str, dict] = {}
        self._lock = threading.Lock()

    def missing(self) -> list[str]:
        return [r for r in required(self.db()) if not cached(r)]

    def start(self) -> bool:
        with self._lock:
            if self.running or not self.missing():
                return False
            self.running = True
        threading.Thread(target=self._run, daemon=True, name="octo-models").start()
        return True

    def _run(self) -> None:
        try:
            for repo in self.missing():
                self.now = repo
                self.result[repo] = self.fetch(repo)
            self.now = None
            if not self.missing() and self.done:
                self.done()
        finally:
            self.now, self.running = None, False

    def report(self) -> dict:
        models = []
        for repo in required(self.db()):
            r = self.result.get(repo) or {}
            state = ("downloading" if repo == self.now else "ready" if cached(repo) else
                     "needs_access" if r.get("gated") else "failed" if r else "waiting")
            models.append({"repo": repo, "state": state, "gated": repo in GATED, "error": r.get("error"),
                           "size_gb": SIZE_GB.get(repo), "have_gb": round(_size_gb(repo), 2) if state == "downloading" else None})
        return {"running": self.running, "models": models}
