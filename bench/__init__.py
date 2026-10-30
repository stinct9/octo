"""Bench-test library for the XIAO ESP32S3 Sense: firmware, serial, audio, transcription.

Used by the web UI (bench.server) and the CLI scripts in tools/.
"""

from pathlib import Path

from octo import HOME

ROOT = Path(__file__).resolve().parent.parent
SKETCHES = ROOT / "firmware" / "bench"
AUDIO = HOME / "audio"  # bench takes (the enrolment take too) live with the lab's data, not the code
RUNS = HOME / "runs"
