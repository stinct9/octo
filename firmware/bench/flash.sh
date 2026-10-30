#!/bin/zsh
# Compile, upload and open a serial monitor for one bench sketch.
#   ./flash.sh 00_hello
# Ctrl-C leaves the monitor. PSRAM=opi is required: it's off by default for this board.
# Every sketch is quiet until you type a command in the monitor: g runs the test, i says
# which sketch is running, ? repeats the last result (see libraries/BenchProto).
set -e
cd "${0:A:h}"

sketch=${1:?usage: ./flash.sh <sketch-dir>}
fqbn=esp32:esp32:XIAO_ESP32S3:PSRAM=opi
port=${PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}

if [[ -z $port ]]; then
  echo "No /dev/cu.usbmodem* found. Check the cable is a data cable."
  echo "If it's plugged in and still missing: hold BOOT, tap RESET, release BOOT, retry."
  exit 1
fi

arduino-cli compile -b $fqbn --libraries libraries $sketch
arduino-cli upload -b $fqbn -p $port $sketch
# The board re-enumerates after upload; give USB a moment to come back.
sleep 2
arduino-cli monitor -p $port -c baudrate=115200
