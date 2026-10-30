#!/bin/sh
# Seeed's files the fit check reads, into seeed/ (gitignored): EB's PCB layout and M1 + Sense's 3D model,
# checked against the copies the 15 Oct check used. Needs .venv-fit (README) for the STEP -> GLB.
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-$PWD/.venv-fit/bin/python}"
mkdir -p seeed && cd seeed
curl -fsSLo eb.brd "https://files.seeedstudio.com/wiki/Seeeduino-XIAO-Expansion-Board/document/Seeeduino%20XIAO%20Expansion%20board_v1.0_200824.brd"
curl -fsSLo sense3d.zip "https://files.seeedstudio.com/wiki/SeeedStudio-XIAO-ESP32S3/res/seeed-studio-xiao-esp32s3-sense-3d_model.zip"
shasum -a 256 -c - <<SUMS
fa43130d0c58a59023a810174e4fb292ba2bf427ae24feb0da012a544de13e3f  eb.brd
773c16cb7518a3a18df926979f24c7e224f19309aad69962ec8a54454e288c59  sense3d.zip
SUMS
unzip -oq sense3d.zip "*.step"
"$PY" -I -c "import cascadio; cascadio.step_to_glb('Seeed Studio XIAO-ESP32-S3-Sense.step', 'sense.glb')"
