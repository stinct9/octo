#!/bin/bash
# Octo's one-line installer (plan §3.1, F1), attached to every GitHub Release and served at octolab.online:
#
#   curl -fsSL https://github.com/stinct9/octo/releases/latest/download/install.sh | bash
#
# Downloads a release (the code with its web pages built: no git, no Node) into its own folder,
# ~/Library/Application Support/octo/versions/<its version>, keeps what the lab records beside it in lab.noindex (only
# you can open it), and runs that release's install.sh, which makes it the version that runs (`current`) once it's
# built; the one before is kept for `octo update --back`. Run it again to repair or update: the same version is
# repaired in place (files it no longer has go), a new one gets its own folder, and nothing in lab.noindex is touched. OCTO_RELEASE names another release (a URL, or file:// for a local one).
set -euo pipefail

# Everything inside a function, so a download cut short runs nothing.
main() {
  local release="${OCTO_RELEASE:-https://github.com/stinct9/octo/releases/latest/download/octo.tar.gz}"
  local base="$HOME/Library/Application Support/octo"
  local lab="$base/lab.noindex"

  if [ "$(uname -m)" != "arm64" ]; then
    # An Apple Silicon Mac whose Terminal runs under Rosetta reads as Intel here (review 10 Nov).
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
      echo "This Mac is Apple Silicon, but Terminal is running under Rosetta (as an Intel app). Either quit Terminal,"
      echo "untick \"Open using Rosetta\" in its Get Info (Finder, Applications, Utilities) and open it again, or put"
      echo "arch -arm64 before bash in the same line, then run it again."
    else
      echo "Octo needs an Apple Silicon Mac (M1 or later): its speech models run on Apple's GPU."
    fi
    exit 1
  fi
  tmp="$(mktemp -d)"  # not local: the EXIT trap reads it after main returns
  trap 'rm -rf "$tmp"' EXIT
  echo "Downloading Octo from $release"
  local rc=0 code
  code="$(curl -fL --progress-bar -w '%{http_code}' "$release" -o "$tmp/octo.tar.gz")" || rc=$?
  if [ "$rc" -ne 0 ] && [ "${code:-000}" -ge 400 ]; then  # the server answered, with an error (404: no release there, or not public yet)
    echo "There's no Octo release at $release yet. If someone gave you this line, ask them for a new one."
    exit 1
  elif [ "$rc" -ne 0 ]; then
    echo "Couldn't download Octo from $release: check this Mac is online, then run the same line again."
    exit 1
  fi
  # A web page served with status 200 in its place (a link that moved) isn't something curl -f can tell.
  if [ "$(head -c 2 "$tmp/octo.tar.gz" | od -An -tx1 | tr -d ' \n')" != "1f8b" ]; then
    echo "What came from $release isn't an Octo release."
    exit 1
  fi
  # Checked against the SHA-256 in version.txt beside it (scripts/release_files.sh) before anything is unpacked.
  local want
  want="$(curl -fsSL "${release%/*}/version.txt" 2>/dev/null | awk '$1 == "sha256" { print $2 }' || true)"
  if [ -z "$want" ]; then
    echo "There's no checksum beside $release to check it against, so nothing was installed."
    echo "If someone gave you this line, ask them for a new one."
    exit 1
  elif [ "$(shasum -a 256 "$tmp/octo.tar.gz" | cut -d' ' -f1)" != "$want" ]; then
    echo "What came from $release doesn't match its checksum, so nothing was installed. Run the same line again;"
    echo "if it says this twice, don't use that link."
    exit 1
  fi
  # Unpacked aside first: a broken download leaves the installed copy as it was.
  mkdir "$tmp/octo"
  tar -xzf "$tmp/octo.tar.gz" -C "$tmp/octo" --strip-components 1
  [ -x "$tmp/octo/install.sh" ] || { echo "That download isn't an Octo release (no install.sh in it)."; exit 1; }
  # This Mac, the port, the disk: refused here, before anything already installed is touched.
  # (Only a release whose install.sh has --check: an older one would install from this temporary folder.)
  if grep -q -- '--check' "$tmp/octo/install.sh"; then
    local said
    said="$("$tmp/octo/install.sh" --check 2>&1)" || { echo "$said" | grep -v '^This folder: '; exit 1; }
  fi

  # Its own folder, named by its version: a failed install leaves the version that runs as it was.
  local ver app
  ver="$(head -1 "$tmp/octo/VERSION" 2>/dev/null || true)"
  [[ "$ver" =~ ^[A-Za-z0-9_+-][A-Za-z0-9_.+-]*$ ]] || ver="local-$(date +%Y%m%d-%H%M%S)"
  app="$base/versions/$ver"
  mkdir -p "$app"
  mkdir -p -m 700 "$lab"
  # Over the code, files the release no longer has removed (octo/deploy.py's update_release does the same); the
  # environment, Node and anything of the lab's kept. A lab already running starts no new step meanwhile (PAUSE).
  echo "$$" > "$app/.octo-deploying"
  rsync -a --checksum --delete --exclude=/.venv --exclude=/.node --exclude=/ui/node_modules --exclude=/.octo-home \
    --exclude=/.octo-release --exclude=/.octo-deploying --exclude=/data --exclude=/audio --exclude=/notes --exclude=/runs \
    "$tmp/octo/" "$app/" || { rm -f "$app/.octo-deploying"; echo "Couldn't copy Octo into $app."; exit 1; }
  rm -f "$app/.octo-deploying"
  # Where updates come from: `octo update` and the lab's "Octo update ready" read it (octo/deploy.py).
  echo "$release" > "$app/.octo-release"
  # The lab's data lives outside the code (octo/__init__.py reads this), so an update never reaches it.
  # (An install already here, before 5 Nov in app/, says where its lab is.)
  for old in "$base/current" "$base/app"; do
    [ -s "$app/.octo-home" ] || [ ! -s "$old/.octo-home" ] || cp "$old/.octo-home" "$app/.octo-home"
  done
  [ -s "$app/.octo-home" ] || echo "$lab" > "$app/.octo-home"
  # db.connect won't start a lab in a folder named on purpose unless told: here, in the folder this script
  # keeps it in, while it has none, a new one is what's meant.
  if [ "$(cat "$app/.octo-home")" = "$lab" ] && [ ! -e "$lab/data/octo.db" ]; then
    export OCTO_NEW_LAB=1
  fi
  echo "Installed in $app"
  "$app/install.sh"
}

main "$@"
