#!/bin/bash
# Install Octo's conversation lab on this Mac (Apple Silicon). Safe to run again: it skips what's done.
#
#   ./install.sh
#
# What it does, in order: checks the Mac and where this folder is; checks Apple's command line tools;
# gets uv (Astral's Python installer, one file in ~/.local/bin) if it's missing; makes the Python
# environment (.venv: Python 3.12 and the exact versions in requirements.lock); builds the web pages only
# if they aren't built already (a release has them; a git clone builds them with Node, fetched into .node if the
# Mac has none); starts the lab when you log in (per-user login items, no admin rights) and builds the Octo app; then opens the lab's getting-started page,
# where the speech models (about 10 GB, once) come down in the background. No Homebrew and no git. It never asks for your password itself; Apple's
# installer for its command line tools may, if they're missing.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n== %s\n' "$*"; }

say "This Mac"
if [ "$(uname -m)" != "arm64" ]; then
  echo "Octo needs an Apple Silicon Mac (M1 or later): its speech models run on Apple's GPU."
  exit 1
fi
ram_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
echo "Apple Silicon, ${ram_gb} GB memory, macOS $(sw_vers -productVersion)"
[ "$ram_gb" -ge 16 ] || echo "Note: tested with 16 GB; with less, long conversations may fail to transcribe."
# Before anything is downloaded (onboarding review, 28 Oct): the speech models need macOS 14, and about 12 GB come down.
if [ "$(sw_vers -productVersion | cut -d. -f1)" -lt 14 ]; then
  echo "Octo needs macOS 14 (Sonoma) or later: its speech models do. Update macOS, then run this again."
  exit 1
fi
free_gb=$(df -g . | awk 'NR==2 {print $4}')
if [ "$free_gb" -lt 15 ]; then
  echo "Only ${free_gb} GB free here. Octo downloads about 12 GB (Python, then the speech models) and keeps your"
  echo "recordings: free up at least 15 GB, then run this again."
  exit 1
fi

# macOS asks before a program reads these folders, and a lab started at login has no window to ask in: on
# 13 Oct it waited ~10 h on one such question with no sign (B7). Same list as octo/doctor.py's PROTECTED.
here="$(pwd -P)"
user="$(cd ~ && pwd -P)"
for f in Downloads Documents Desktop "Library/Mobile Documents"; do
  case "$here/" in
    "$user/$f/"*)
      [ "$f" = "Library/Mobile Documents" ] && f="iCloud Drive"
      echo "Octo can't run from $here, inside $f."
      echo "macOS asks before a program reads $f, and the lab, started at login, has no window to answer"
      echo "in: it waits with no sign (once for ten hours). Move this folder somewhere else, for example"
      echo "~/Library/Application Support/octo/app (where the one-line installer puts it), then run ./install.sh there."
      exit 1 ;;
  esac
done
echo "This folder: $here"

say "Apple's command line tools"
if clt="$(xcode-select -p 2>/dev/null)" && [ -d "$clt" ]; then
  echo "Installed: $clt"
else
  echo "Not installed. The recorder's relay runs on Apple's own Python (/usr/bin/python3), which is only a"
  echo "stub without them, so the lab couldn't reach a recorder. Phone memos work without them."
  echo "A window from Apple has opened: click Install, then Agree. It takes a few minutes; this waits for it."
  echo "(The Octo app is built with these tools.)"
  xcode-select --install >/dev/null 2>&1 || true
  # Waited for (beta, 31 Oct): without them the app would be the simple launcher, and someone new to Terminal wouldn't
  # know to come back and build it. Up to 30 minutes, then carry on and let the Setup page say what to do.
  for _ in $(seq 360); do xcode-select -p >/dev/null 2>&1 && break; sleep 5; done
  if xcode-select -p >/dev/null 2>&1; then echo "Installed."; else
    echo "Still not installed: carrying on. The Setup page will say how to finish (Octo app: Check This Mac)."; fi
fi

say "uv (Python and its packages)"
UV="$(command -v uv || true)"
[ -n "$UV" ] || UV="$HOME/.local/bin/uv"
if [ ! -x "$UV" ]; then
  # Astral's own installer, a pinned version, into ~/.local/bin; it leaves your shell's settings alone.
  curl -LsSf https://astral.sh/uv/0.13.0/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
fi
echo "uv: $UV ($("$UV" --version))"

say "The lab's Python environment (.venv)"
# Python 3.12 from uv (downloaded once if the Mac has none); --seed keeps pip in .venv for `octo deploy`.
[ -x .venv/bin/python ] || "$UV" venv --clear --seed --python 3.12 .venv
# The versions the lab was tested with (requirements.lock, the one list of versions), then the lab itself.
"$UV" pip install --quiet --python .venv/bin/python -r requirements.lock
"$UV" pip install --quiet --python .venv/bin/python --no-deps -e .
echo "$(.venv/bin/python --version) in .venv"

say "The web pages"
# Built when ui/dist is missing or wasn't built from this ui/src (the stamp `bench serve` checks too).
want="$(.venv/bin/python -c 'from pathlib import Path; from bench.cli import _src_hash; print(_src_hash(Path("ui/src")))')"
if [ -f ui/dist/index.html ] && [ "$(cat ui/dist/.src-hash 2>/dev/null)" = "$want" ]; then
  echo "Already built (ui/dist matches ui/src)"
else
  # A git clone builds them with Node: the Mac's own if it has one, else a pinned copy in .node (the phone app uses it too).
  command -v npm >/dev/null 2>&1 || scripts/get_node.sh
  [ -x .node/bin/npm ] && export PATH="$PWD/.node/bin:$PATH"
  # Its file-by-file listing hidden; shown if the build fails.
  out="$(cd ui && npm ci --no-audit --no-fund --silent 2>&1 && npm run build --silent 2>&1)" || { echo "$out" | tail -30; exit 1; }
  echo "Built ui/dist"
fi

say "Speech models (about 10 GB, once)"
if [ "${OCTO_SKIP_LOGIN_ITEMS:-}" = "1" ]; then
  # No lab started by this script: fetch them here. Two need a Hugging Face sign-in, done on the setup page.
  .venv/bin/octo models download || echo "Some need a Hugging Face sign-in first: the setup page does it."
else
  echo "The lab downloads them in the background once it starts: the setup page shows how far it's got and"
  echo "asks you to sign in to Hugging Face for the two whose authors ask for that."
fi

say "Start the lab when you log in"
if [ "${OCTO_SKIP_LOGIN_ITEMS:-}" = "1" ]; then
  echo "Skipped (OCTO_SKIP_LOGIN_ITEMS=1): for testing an install beside a lab that's already running."
else
  .venv/bin/octo login-item install
  # The Octo app in Applications (built here when Apple's tools have swiftc), unless the one there is already this
  # version: rebuilding restarts it, which would cut off an update the app itself started.
  .venv/bin/python -c 'import sys; from octo import doctor as D; sys.exit(D.launcher()[0].mark != D.OK)' \
    || .venv/bin/octo launcher install
fi

say "Check"
.venv/bin/octo doctor --fresh || true

[ "${OCTO_UPDATE:-}" = "1" ] && exit 0  # `octo update` (octo/deploy.py): the lab restarted, nothing to open
echo
echo "The lab is at http://127.0.0.1:8765. Opening its Setup page…"
sleep 2
if [ "${OCTO_SKIP_LOGIN_ITEMS:-}" != "1" ]; then
  # The Octo app's own window when it was built (it lets itself in); else this browser, let in with the Mac's key.
  app="$(.venv/bin/python -c 'from octo import launcher as L; a = L.found(); print(a if a and L.native(a) else "")')"
  if [ -n "$app" ]; then open "$app" --args --page setup || true; else .venv/bin/octo open setup || true; fi
fi
