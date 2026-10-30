"""Mic capture from 02_mic, level analysis, cue detection, and local Whisper transcription."""

from __future__ import annotations

import array
import math
import statistics
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable

from .device import Board, BoardError
from .protocol import is_result, parse

SOUNDS = "/System/Library/Sounds"
RECORD_SECONDS = 10
# 02_mic's two recording lengths and the command for each.
RECORD_COMMANDS = {10: b"r", 35: b"R"}
SPEAK_AT = 3.0  # seconds into the recording

# Analysis windows matching the cues: the start Ping has died away by 0.75 s, and speech
# reliably starts ~0.75 s after the Glass cue at 3 s.
QUIET = (0.75, 2.75)
SPEECH = (3.75, 99.0)
# Where the Glass cue should land. afplay adds ~0.1-0.3 s of start-up latency.
CUE_WINDOW = (2.8, 3.7)


def _play(name: str) -> None:
    subprocess.run(["afplay", f"{SOUNDS}/{name}.aiff"], check=False)


def capture(board: Board, out: Path, on_phase: Callable[[str, float], None], cues: bool = True,
            seconds: int = RECORD_SECONDS) -> dict:
    """Record `seconds` (10 or 35) via 02_mic's `r`/`R` command and save a WAV.

    Not cancellable once started: the board streams 32 KB per second of audio after recording,
    and abandoning it would leave that data in the USB buffer to corrupt the next upload.

    on_phase(name, t) fires with "silent" at 0, "speak" at SPEAK_AT, "done" at the end, and
    "transfer" when the board starts sending. With cues the Mac plays Ping/Glass/Hero at the
    same moments; the mic records them, which proves the recording's timeline.
    """
    if seconds not in RECORD_COMMANDS:
        raise ValueError(f"02_mic records {' or '.join(map(str, RECORD_COMMANDS))} s, not {seconds}")
    board.drain(quiet=0.2, limit=2)
    board.send(RECORD_COMMANDS[seconds])
    t0 = time.time()
    timers: list[threading.Timer] = []

    def at(delay: float, phase: str, sound: str) -> None:
        def fire() -> None:
            on_phase(phase, time.time() - t0)
            if cues:
                _play(sound)
        t = threading.Timer(delay, fire)
        t.daemon = True
        t.start()
        timers.append(t)

    try:
        at(0, "silent", "Ping")
        at(SPEAK_AT, "speak", "Glass")
        at(seconds, "done", "Hero")
        while True:
            line = board.readline(seconds + 10)
            if line is None:
                raise BoardError("no recording from the board", hint="Is 02_mic running?")
            if line.startswith("WAVSTART"):
                _, length, rate = line.split()
                length, rate = int(length), int(rate)
                break
            data = parse(line)
            if data and is_result(data) and "error" in data:
                raise BoardError(f"mic error: {data['error']}")
        on_phase("transfer", time.time() - t0)
        pcm = board.read_exact(length, timeout=30 + seconds)
        result = None
        while result is None:  # WAVEND, then the @@ result
            line = board.readline(5)
            if line is None:
                raise BoardError("recording trailer missing")
            data = parse(line)
            if data and is_result(data):
                result = data
    finally:
        # Also on errors -- otherwise a late "done" phase and Hero cue fire into the next step.
        for t in timers:
            t.cancel()

    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return result


def _read(path: Path) -> tuple[int, array.array]:
    with wave.open(str(path)) as w:
        rate = w.getframerate()
        a = array.array("h", w.readframes(w.getnframes()))
    return rate, a


def _levels(a: array.array, win: int) -> list[float]:
    out = []
    for i in range(0, len(a) - win + 1, win):
        seg = a[i:i + win]
        m = sum(seg) / win
        r = math.sqrt(sum((x - m) ** 2 for x in seg) / win)
        out.append(round(20 * math.log10(max(r, 1e-9) / 32768), 1))
    return out


def _power_db(levels: list[float]) -> float:
    # Average in the power domain -- a mean of dB values under-weights loud moments.
    return 10 * math.log10(sum(10 ** (d / 10) for d in levels) / len(levels))


def find_cue(rate: int, a: array.array, window=CUE_WINDOW, rise_db: float = 10.0) -> float | None:
    """Onset time of the Glass cue: first 50 ms block in the window that jumps rise_db above
    the quiet section's median. None if not found (Mac muted, audio going to headphones)."""
    win = rate // 20
    lv = _levels(a, win)
    quiet = [d for i, d in enumerate(lv) if QUIET[0] <= i * 0.05 < QUIET[1]]
    if not quiet:
        return None
    floor = statistics.median(quiet)
    for i, d in enumerate(lv):
        t = i * 0.05
        if window[0] - 0.2 <= t <= window[1] and d >= floor + rise_db:
            return round(t, 2)
    return None


def analyse(path: Path, quiet=QUIET, speech=SPEECH) -> dict:
    rate, a = _read(path)
    n = len(a)
    if n == 0:
        return {"seconds": 0.0, "rate": rate, "empty": True}
    peak = max(abs(min(a)), max(a)) or 1
    run = best = 1
    for i in range(1, n):
        run = run + 1 if a[i] == a[i - 1] else 1
        best = max(best, run)

    levels = _levels(a, rate // 4)
    q = [d for i, d in enumerate(levels) if quiet[0] <= i * 0.25 < quiet[1]]
    sp = sorted(d for i, d in enumerate(levels) if speech[0] <= i * 0.25 < speech[1])
    loud = sp[len(sp) // 2:]  # louder half: skips pauses between words
    quiet_db = round(_power_db(q), 1) if q else None
    speech_db = round(_power_db(loud), 1) if loud else None
    return {
        "seconds": round(n / rate, 2),
        "rate": rate,
        "peak_dbfs": round(20 * math.log10(peak / 32768), 1),
        "clipped": sum(1 for x in a if abs(x) >= 32767),
        "longest_flat_ms": round(best / rate * 1000, 1),
        "levels": levels,
        "window_s": 0.25,
        "quiet_window": list(quiet),
        "speech_window": [speech[0], min(speech[1], n / rate)],
        "quiet_db": quiet_db,
        "speech_db": speech_db,
        "snr_db": round(speech_db - quiet_db, 1) if quiet_db is not None and speech_db is not None else None,
        "cue_at": find_cue(rate, a),
    }


class ModelCache:
    """faster-whisper models, loaded once and kept in memory (several GB for large-v3).

    warm() starts loading in the background so it overlaps the board steps. Loading can't be
    interrupted, but waiting for it can.
    """

    def __init__(self) -> None:
        self._models: dict[str, object] = {}
        self._loading: dict[str, threading.Thread] = {}
        self._errors: dict[str, Exception] = {}
        self._lock = threading.Lock()
        self.notes: dict[str, str] = {}

    def warm(self, name: str) -> None:
        with self._lock:
            if name in self._models or name in self._loading:
                return
            t = threading.Thread(target=self._load, args=(name,), daemon=True)
            self._loading[name] = t
            t.start()

    def _load(self, name: str) -> None:
        from faster_whisper import WhisperModel
        try:
            # int8 on CPU: faster-whisper can't use the Apple GPU, and int8 is the fast CPU path.
            # local_files_only keeps it offline once cached -- the project is local-only.
            try:
                model = WhisperModel(name, device="cpu", compute_type="int8", local_files_only=True)
            except Exception:
                self.notes[name] = "not cached yet: downloading (large-v3 is ~3 GB, one time)"
                model = WhisperModel(name, device="cpu", compute_type="int8")
            with self._lock:
                self._models[name] = model
        except Exception as e:  # surfaced to whoever waits
            with self._lock:
                self._errors[name] = e
        finally:
            with self._lock:
                self._loading.pop(name, None)

    def get(self, name: str, check: Callable[[], None], on_wait: Callable[[float], None]) -> object:
        self.warm(name)
        t0 = time.time()
        while True:
            with self._lock:
                if name in self._models:
                    return self._models[name]
                if name in self._errors:
                    raise BoardError(f"Whisper model {name} failed to load: {self._errors.pop(name)}")
            check()
            on_wait(time.time() - t0)
            time.sleep(0.5)

    def loaded(self, name: str) -> bool:
        with self._lock:
            return name in self._models


def _whisper_input(path: Path):
    """Samples for a 16 kHz mono 16-bit WAV (what the board records), else the path.

    Handing faster-whisper samples skips its PyAV decoder: faster-whisper 1.2.1 passes
    open(metadata_errors=...), which PyAV 19 no longer accepts. Other formats still need PyAV.
    """
    import numpy as np
    try:
        with wave.open(str(path)) as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2):
                return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    except wave.Error:
        pass
    return str(path)


def transcribe(model, path: Path, language: str | None, check: Callable[[], None]) -> dict:
    t0 = time.time()
    segments, info = model.transcribe(_whisper_input(path), language=language, beam_size=5)
    segs = []
    for s in segments:  # lazy: each iteration does real work, so cancel is checked between them
        check()
        segs.append({"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip(),
                     "logprob": round(s.avg_logprob, 2), "no_speech": round(s.no_speech_prob, 2)})
    return {
        "mode": "auto" if language is None else language,
        "language": info.language,
        "language_p": round(info.language_probability, 2),
        "seconds": round(time.time() - t0, 1),
        "text": " ".join(s["text"] for s in segs),
        "mean_logprob": round(sum(s["logprob"] for s in segs) / len(segs), 2) if segs else None,
        "segments": segs,
    }
