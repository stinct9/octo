#!/bin/bash
# The files of a ready-built release, from a folder of the code (a scripts/public_copy.py copy, or the public
# repository's checkout): the same for beta testers (scripts/beta_release.sh, to R2) and for anyone (the public
# repository's .github/workflows/release.yml, to its GitHub Releases).
#   octo.tar.gz  scripts/release.sh's release, without the recorder's firmware
#   install.sh   the one-line installer (scripts/bootstrap.sh), pointing at URL, where octo.tar.gz will be served
#   version.txt  its version, then "sha256 <octo.tar.gz's SHA-256>": a lab installed from URL offers "Octo update ready"
#                when the one beside it is newer, and the installer and `octo update` check the download against it
# A version is ORDER+HASH, e.g. 2026.11.05-2+3f2a9c1b2d4e: the day it was released (UTC) and that day's counter, so
# versions sort (octo/deploy.py `order`), then the copy's content.
#
#   scripts/release_files.sh SOURCE_DIR                      prints the content hash only
#   scripts/release_files.sh --next PREVIOUS_VERSION         prints the next ORDER (PREVIOUS_VERSION may be empty)
#   scripts/release_files.sh SOURCE_DIR OUT_DIR URL ORDER    writes the three files into OUT_DIR
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd -P)"
if [ "$1" = --next ]; then
  today="$(date -u +%Y.%m.%d)" prev="${2:-}"
  prev="${prev%%+*}"
  if [ "${prev%-*}" = "$today" ]; then echo "$today-$((${prev##*-} + 1))"; else echo "$today-1"; fi
  exit 0
fi
# The content hash is the copy's (its .git aside), so a release that only touched what isn't copied is no update.
hash="$(cd "$1" && find . -path ./.git -prune -o -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 \
  | shasum -a 256 | cut -c1-12)"
[ $# -ge 4 ] || { echo "$hash"; exit 0; }
version="$4+$hash"
mkdir -p "$2"
OCTO_NO_FIRMWARE=1 OCTO_VERSION="$version" "$here/release.sh" "$1" "$2/octo.tar.gz"
sed "s|https://github.com/stinct9/octo/releases/latest/download/octo.tar.gz|$3|" "$here/bootstrap.sh" > "$2/install.sh"
grep -qF "$3" "$2/install.sh" || { echo "install.sh doesn't point at $3"; exit 1; }
printf '%s\nsha256 %s\n' "$version" "$(shasum -a 256 "$2/octo.tar.gz" | cut -d' ' -f1)" > "$2/version.txt"
