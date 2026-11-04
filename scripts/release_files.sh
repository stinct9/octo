#!/bin/bash
# The three files of a ready-built release, from a folder of the code (a scripts/public_copy.py copy, or the public
# repository's checkout): the same for beta testers (scripts/beta_release.sh, to R2) and for anyone (the public
# repository's .github/workflows/release.yml, to its GitHub Releases).
#   octo.tar.gz  scripts/release.sh's release, without the recorder's firmware
#   install.sh   the one-line installer (scripts/bootstrap.sh), pointing at URL, where octo.tar.gz will be served
#   version.txt  its version: a lab installed from URL offers "Octo update ready" when the one beside it differs
#
#   scripts/release_files.sh SOURCE_DIR                   prints the version only
#   scripts/release_files.sh SOURCE_DIR OUT_DIR URL       writes the three files into OUT_DIR
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd -P)"
# The version is the copy's content (its .git aside), so a merge that only touched what isn't copied is no update.
version="$(cd "$1" && find . -path ./.git -prune -o -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 \
  | shasum -a 256 | cut -c1-12)"
[ $# -ge 3 ] || { echo "$version"; exit 0; }
mkdir -p "$2"
OCTO_NO_FIRMWARE=1 OCTO_VERSION="$version" "$here/release.sh" "$1" "$2/octo.tar.gz"
sed "s|https://github.com/stinct9/octo/releases/latest/download/octo.tar.gz|$3|" "$here/bootstrap.sh" > "$2/install.sh"
grep -qF "$3" "$2/install.sh" || { echo "install.sh doesn't point at $3"; exit 1; }
echo "$version" > "$2/version.txt"
