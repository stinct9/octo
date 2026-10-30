#!/bin/bash
# A pinned Node in .node for this copy of Octo, when the Mac has none: the pages of a git clone (install.sh) and the
# iPhone app (octo/iphone.py) are built with it. Checked against its checksum; run again, it does nothing.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .node/bin/npm ] && exit 0
node=node-v24.22.1-darwin-arm64
echo "Getting Node (27 MB, once) into .node"
curl -fL --progress-bar "https://nodejs.org/dist/v24.22.1/$node.tar.xz" -o .node.tar.xz
echo "654ba79de6155647e22321932b45f2a1ccb705976cc337c9b8e278beb085a628  .node.tar.xz" | shasum -a 256 -c --status \
  || { rm -f .node.tar.xz; echo "That Node download didn't match its checksum; try again."; exit 1; }
mkdir -p .node && tar -xJf .node.tar.xz -C .node --strip-components 1 && rm .node.tar.xz
