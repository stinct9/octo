#!/bin/bash
# Make a release tarball from a folder of the code (a fresh `scripts/public_copy.py` copy for a public release):
# builds ui/dist and the recorder's firmware in it, so whoever installs needs no Node or Arduino toolchain, and packs it
# as octo/… for scripts/bootstrap.sh.
#
#   scripts/release.sh SOURCE_DIR OUT.tar.gz
set -euo pipefail
src="$(cd "$1" && pwd -P)"
out="$2"
[ -f "$src/install.sh" ] && [ -d "$src/ui/src" ] || { echo "$src isn't a copy of Octo's code"; exit 1; }
(cd "$src/ui" && npm ci --no-audit --no-fund --silent && npm run build --silent)
# The recorder's firmware, prebuilt, so makers need no Arduino toolchain (open-source plan I5): built without
# secrets.h (one build for every board, paired later), with this Mac's folders taken out of the file names the
# compiler embeds, and listed in SHA256SUMS, which `octo device flash` checks before anything reaches a board.
[ ! -e "$src/firmware/recorder/secrets.h" ] || { echo "$src has firmware/recorder/secrets.h: release from a public_copy.py copy"; exit 1; }
# Which version this is, for `octo update` (OCTO_VERSION: scripts/beta_release.sh gives the commit).
[ -z "${OCTO_VERSION:-}" ] || echo "$OCTO_VERSION" > "$src/VERSION"
if [ "${OCTO_NO_FIRMWARE:-}" != "1" ]; then  # the beta's releases skip it: the recorder isn't in the beta
command -v arduino-cli >/dev/null || { echo "a release needs arduino-cli to build the recorder's firmware"; exit 1; }
fw="$src/firmware/recorder/release"
work="$(mktemp -d)"
flags="-ffile-prefix-map=$src=octo -fmacro-prefix-map=$src=octo -ffile-prefix-map=$HOME=~ -fmacro-prefix-map=$HOME=~"
arduino-cli compile -b esp32:esp32:XIAO_ESP32S3:PSRAM=opi --libraries "$src/firmware/bench/libraries" \
  --build-property "compiler.c.extra_flags=$flags" --build-property "compiler.cpp.extra_flags=$flags" \
  --build-path "$work" --output-dir "$work/out" "$src/firmware/recorder" >/dev/null  # FQBN: bench/firmware.py
mkdir -p "$fw"
cp "$work/out/recorder.ino.bin" "$work/out/recorder.ino.merged.bin" "$fw/"
rm -rf "$work"
(cd "$fw" && shasum -a 256 recorder.ino.bin recorder.ino.merged.bin > SHA256SUMS)
if strings "$fw/recorder.ino.bin" | grep -qF "$HOME"; then echo "the firmware still names $HOME"; exit 1; fi
fi
# --no-mac-metadata: no AppleDouble (._*) files for the Mac that unpacks it.
top="$(basename "$src")"
tar -czf "$out" --no-mac-metadata -C "$(dirname "$src")" -s ",^$top,octo," --exclude node_modules --exclude .git \
  --exclude __pycache__ --exclude "$top/.venv" --exclude "$top/data" --exclude "$top/.octo-home" "$top"
echo "$out: $(du -h "$out" | cut -f1), ui/dist built from ui/src $(cut -c1-12 "$src/ui/dist/.src-hash")"
