"""Audio in and out: any input → 16 kHz mono 16-bit; slices as float32 for the models.

A call recorded on the Mac (B19, octo/calls.py) is kept as two channels: the mic (the owner) and the call app's
sound (the others). Reading one without asking for a channel gives the two mixed, so everything that hears a
recording as one voice track (finding conversations, playback, deleting a part) works on calls unchanged.

Recordings are stored as FLAC (since 2 Oct): the same 16-bit samples, bit for bit, in about a
third of the space on the owner's recordings (464 MB of WAV → 167 MB; 26–39% for the recorder, 52–59%
for phone memos). A 5-minute slice near the end of an 82-minute recording reads in ~50 ms (7 ms
as WAV). Always-on recording kept 60 days (D5) is what made the space matter. Scratch files and
the recorder's chunks stay WAV.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

from . import RATE


def is_board_wav(path: Path, channels: int = 1) -> bool:
    try:
        with wave.open(str(path)) as w:
            return (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, channels, 2)
    except (wave.Error, EOFError):
        return False


def to_wav(src: Path, dst: Path, channels: int = 1) -> None:
    """Convert anything macOS can read (phone memos are .m4a) to 16 kHz mono (or `channels`) 16-bit WAV.

    Uses afconvert (built into macOS) rather than PyAV: faster-whisper 1.2.1 and PyAV 19
    don't get along, and afconvert needs nothing installed.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if is_board_wav(src, channels):
        dst.write_bytes(src.read_bytes())
        return
    r = subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", str(channels), str(src), str(dst)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not is_board_wav(dst, channels):
        raise ValueError(f"couldn't convert {src.name}: {r.stderr.strip() or 'unknown format'}")


def recorded_at(path: Path) -> float | None:
    """When an .m4a/.mp4 was recorded, from its MP4 'mvhd' header (seconds since 1904, UTC).
    Phone voice memos carry it; the file's own dates only say when it was AirDropped or copied.
    None if absent or implausible."""
    import struct

    try:
        with open(path, "rb") as f:
            head = f.read(4 << 20)  # mvhd sits in the moov box, near the start or the end
            if b"mvhd" not in head:
                f.seek(max(0, path.stat().st_size - (4 << 20)))
                head = f.read()
    except OSError:
        return None
    i = head.find(b"mvhd")
    if i < 0 or len(head) < i + 16:
        return None
    version = head[i + 4]
    t = struct.unpack(">Q", head[i + 8:i + 16])[0] if version == 1 else struct.unpack(">I", head[i + 8:i + 12])[0]
    unix = t - 2082844800  # 1904-01-01 → 1970-01-01
    return float(unix) if 978307200 < unix < 4102444800 else None  # 2001..2100, else not a real date


def _flac(path: Path) -> bool:
    return Path(path).suffix.lower() == ".flac"


def channels(path: Path) -> int:
    """2 for a call recorded on the Mac (B19), 1 for everything else."""
    if _flac(path):
        import soundfile as sf
        return sf.info(str(path)).channels
    with wave.open(str(path)) as w:
        return w.getnchannels()


def duration(path: Path) -> float:
    if _flac(path):
        import soundfile as sf
        info = sf.info(str(path))
        return info.frames / info.samplerate
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def read_pcm(path: Path, start_s: float = 0.0, end_s: float | None = None, channel: int | str | None = None) -> np.ndarray:
    """The stored 16-bit samples for [start_s, end_s), untouched. A two-channel recording (a call) gives its channels
    mixed, or one `channel` (0 the mic, 1 the call app), or "all" of them as they're stored (frames × channels)."""
    first = int(start_s * RATE)
    if _flac(path):
        import soundfile as sf
        with sf.SoundFile(str(path)) as f:
            f.seek(min(first, f.frames))
            n = (int(end_s * RATE) if end_s is not None else f.frames) - first
            pcm = f.read(max(0, n), dtype="int16", always_2d=True)
    else:
        with wave.open(str(path)) as w:
            w.setpos(min(first, w.getnframes()))
            n = (int(end_s * RATE) if end_s is not None else w.getnframes()) - first
            pcm = np.frombuffer(w.readframes(max(0, n)), np.int16).reshape(-1, w.getnchannels())
    if pcm.shape[1] == 1:
        return pcm[:, 0]
    if channel == "all":
        return pcm
    if channel is not None:
        return np.ascontiguousarray(pcm[:, channel])
    return np.round(pcm.mean(axis=1)).astype(np.int16)


def read(path: Path, start_s: float = 0.0, end_s: float | None = None, channel: int | str | None = None) -> np.ndarray:
    """Float32 samples in [-1, 1] for [start_s, end_s) (channels as read_pcm)."""
    return read_pcm(path, start_s, end_s, channel).astype(np.float32) / 32768.0


def highpass(samples: np.ndarray, hz: float = 80.0) -> np.ndarray:
    """Remove everything below `hz`. The board's PDM mic lets its zero level wander at < 5 Hz,
    and in the owner's first enrolment take that drift carried ~23 dB more energy than their voice: loudness
    normalisation scaled to the drift and Silero found 0 s of speech in 30 s of reading (30.4 s
    after this filter). No speech lives below 80 Hz."""
    if len(samples) < 64:
        return samples
    from scipy.signal import butter, sosfiltfilt

    sos = butter(4, hz, "highpass", fs=RATE, output="sos")
    block, margin = 600 * RATE, 2 * RATE
    if len(samples) <= 2 * block:
        return sosfiltfilt(sos, samples).astype(np.float32)
    # Long audio in 10-minute blocks (review 4 Oct, M26): sosfiltfilt works in float64 with copies,
    # several GB for a 4 h stretch. Each block runs with 2 s either side, dropped after: the filter
    # settles in well under that, so the joins are seamless.
    out = np.empty(len(samples), np.float32)
    for a in range(0, len(samples), block):
        lo, hi = max(0, a - margin), min(len(samples), a + block + margin)
        y = sosfiltfilt(sos, samples[lo:hi])
        out[a:min(len(samples), a + block)] = y[a - lo:a - lo + min(block, len(samples) - a)]
    return out


LISTEN_DBFS = -18.0  # the loud parts of speech, as a phone memo or a podcast plays them
LISTEN_MAX_GAIN_DB = 40.0
LISTEN_CEILING = 0.89  # -1 dBFS


def for_listening(samples: np.ndarray) -> np.ndarray:
    """A copy to listen to: high-passed, the speech brought up to LISTEN_DBFS (never down, gain capped),
    and a limiter so a door or a cough at that gain doesn't clip. The recorder across a table puts the
    loud parts of speech at -44 to -61 dBFS (the owner's clips, 13 Oct), so a clip had to be played at full
    volume (13 Oct). Only playback changes; the stored recording stays as recorded."""
    from scipy.ndimage import maximum_filter1d, uniform_filter1d

    x = highpass(samples)
    n = RATE // 20  # 50 ms frames: their loudest tenth is speech in a conversation
    frames = x[:len(x) // n * n].reshape(-1, n)
    if not len(frames):
        return x
    level = float(np.percentile(np.sqrt((frames.astype(np.float64) ** 2).mean(1)), 90)) + 1e-9
    gain = min(10 ** (LISTEN_MAX_GAIN_DB / 20), max(1.0, 10 ** (LISTEN_DBFS / 20) / level))
    y = x * gain
    # The limiter: each 10 ms takes the gain that keeps its peak under the ceiling, reached 20 ms early
    # and let go over 50 ms, so it turns down around a bang instead of squaring it off.
    hop = RATE // 100
    m = -(-len(y) // hop)
    peaks = np.abs(np.pad(y, (0, m * hop - len(y)))).reshape(m, hop).max(1)
    g = np.minimum(1.0, LISTEN_CEILING / np.maximum(peaks, 1e-9))
    g = 1.0 / maximum_filter1d(1.0 / g, size=5)  # the quietest gain within ±20 ms
    g = np.minimum(g, uniform_filter1d(g, size=5))
    g = np.interp(np.arange(len(y)), np.arange(m) * hop + hop / 2, g)
    return np.clip(y * g, -1, 1).astype(np.float32)


def write(path: Path, samples: np.ndarray) -> None:
    """Float32 samples to 16-bit WAV or FLAC (by suffix). The inverse of read(): read then write
    gives back the same file contents. (Before 2 Oct it scaled by 32767, so each rewrite -- deleting
    part of a conversation -- left the whole recording a little quieter.)"""
    write_pcm(path, np.clip(np.round(np.asarray(samples, np.float64) * 32768.0), -32768, 32767).astype(np.int16))


def write_pcm(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _flac(path):
        import soundfile as sf
        sf.write(str(path), pcm.astype(np.int16), RATE, subtype="PCM_16", format="FLAC")
        return
    with wave.open(str(path), "wb") as w:
        w.setnchannels(pcm.shape[1] if pcm.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def store(src: Path, dst: Path, channels: int = 1) -> None:
    """Any input → 16 kHz mono (or `channels`) 16-bit at `dst` (FLAC when it ends .flac)."""
    if not _flac(dst):
        to_wav(src, dst, channels)
        return
    tmp = dst.with_name(dst.stem + ".tmp.wav")
    try:
        to_wav(src, tmp, channels)
        write_pcm(dst, read_pcm(tmp, channel="all"))
    finally:
        tmp.unlink(missing_ok=True)


def scratch_wav() -> Path:
    return Path(tempfile.mkstemp(suffix=".wav")[1])
