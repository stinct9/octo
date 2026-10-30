#!/bin/sh
# SPIKE (B19): builds CallTap.app, ad hoc signed like the Octo app (octo/launcher.py), into $1 (default: ./build).
# Run it with `open -W --stdout LOG --stderr LOG CallTap.app --args record PID SECONDS OUT.wav`, so macOS treats
# it as its own app for the permission prompts (run from a terminal, the terminal would be asked instead).
set -e
here=$(cd "$(dirname "$0")" && pwd)
app="${1:-$here/build}/CallTap.app"
mkdir -p "$app/Contents/MacOS"
cp "$here/Info.plist" "$app/Contents/Info.plist"
xcrun --sdk macosx swiftc -O -swift-version 5 -target arm64-apple-macos14.4 -o "$app/Contents/MacOS/CallTap" "$here/CallTap.swift"
codesign --force --sign - "$app"
echo "$app"
