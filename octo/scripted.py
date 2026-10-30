"""Scripted multi-voice conversations with known ground truth (who spoke when, what was said).

Regression checks only: macOS voices are clean and never talk over each other, so they flatter
speaker separation (plan §6). Real accuracy comes from hand-labelled real conversations.

  python -m octo.scripted OUT.wav      writes OUT.wav + OUT.truth.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from . import RATE
from . import audio as A

# Two similar male Indian-English voices and one female: the hard case for telling people apart.
FAMILY_DINNER = [
    ("Rishi", "So what's the plan for Sunday? I was thinking we could all go to the temple in the morning."),
    ("Tara", "That sounds nice, but the traffic on Sunday mornings is terrible near Basavanagudi."),
    ("Aman", "We could leave early, maybe by seven. I can drive if you want."),
    ("Rishi", "Seven is fine for me. Amma, would that be too early for you?"),
    ("Tara", "No, seven is fine. I just don't want to rush breakfast again like last time."),
    ("Rishi", "Fair enough. Last time I kept everyone waiting because of that work call."),
    ("Aman", "Yes, and you were on the phone for half the drive."),
    ("Rishi", "I know. This time the phone stays in my pocket, I promise."),
    ("Tara", "Good. And after the temple, shall we have lunch at home or eat out?"),
    ("Aman", "Let's eat at home. I'll help with the cooking this time."),
    ("Rishi", "Then I'll handle the shopping on Saturday evening. Just send me the list."),
    ("Tara", "I'll send it tonight. Don't forget the curry leaves again."),
]


def synth(voice: str, text: str) -> np.ndarray:
    with tempfile.TemporaryDirectory() as d:
        aiff, wav = Path(d) / "x.aiff", Path(d) / "x.wav"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        A.to_wav(aiff, wav)
        return A.read(wav)


def build(script=FAMILY_DINNER, gap_s=(0.25, 0.9), lead_s=1.0, seed=0) -> tuple[np.ndarray, list[dict]]:
    rng = np.random.default_rng(seed)
    room = lambda s: rng.normal(0, 0.002, int(s * RATE)).astype(np.float32)  # quiet room tone
    parts, truth, t = [room(lead_s)], [], lead_s
    for voice, text in script:
        x = synth(voice, text)
        # trim the synthesiser's own leading/trailing silence so truth times are tight
        idx = np.flatnonzero(np.abs(x) > 0.01)
        x = x[idx[0]:idx[-1] + 1] if len(idx) else x
        truth.append({"speaker": voice, "start": round(t, 3), "end": round(t + len(x) / RATE, 3), "text": text})
        parts.append(x + room(len(x) / RATE))
        t += len(x) / RATE
        g = float(rng.uniform(*gap_s))
        parts.append(room(g))
        t += g
    parts.append(room(lead_s))
    return np.concatenate(parts), truth


if __name__ == "__main__":
    out = Path(sys.argv[1])
    samples, truth = build()
    A.write(out, samples)
    out.with_suffix(".truth.json").write_text(json.dumps(truth, indent=1))
    print(f"wrote {out} ({len(samples) / RATE:.1f} s, {len(truth)} turns)")
