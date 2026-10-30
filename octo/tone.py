"""Tone of voice (B27, docs/backlog.md, 21 Oct): pitch range and loudness of the owner's confirmed lines, read from the
stored audio when the page is read (no model call, nothing re-runs, nothing leaves the Mac).

- pitch: F0 by YIN (de Cheveigné & Kawahara 2002) on 10 ms frames, 70-400 Hz, in numpy: librosa isn't installed and
  torchaudio's detect_pitch_frequency gives every frame a pitch, with no voiced or unvoiced; the range is the 10th to
  90th percentile of the voiced frames, in semitones (so a low and a high voice compare);
- loudness: the median level of the owner's voiced frames, high-passed at 80 Hz first (audio.highpass: the board mic
  drifts below 5 Hz), in dB against the others' voiced frames in the same conversation (learnings, Speech, 21 Oct:
  the recorder's distance moves the absolute level more than the voice does);
- when pushed back: the owner's turns right after someone else's question ("?") or disagreement (PUSHBACK), their
  pitch range and level against the rest of the owner's own frames in the same conversation, so distance cancels.

Only the owner's words with nobody else's words over them, and the others' likewise: crosstalk carries two voices.
Each conversation's numbers are kept in data/tone.json by what they're made from (the audio, the bounds, the
spans), so the usual doesn't re-read every conversation and outlives the audio's 60 days. Not checked by ear yet.
"""

from __future__ import annotations

import hashlib
import json
import time

import numpy as np

from . import DATA, HOME
from . import audio as A

RATE = A.RATE
HOP = 160                       # 10 ms
WIN = 400                       # 25 ms: YIN's integration window
FMIN, FMAX = 70.0, 400.0
THRESHOLD = 0.15                # YIN's dip: below this a frame is voiced
SILENCE_DB = -70.0              # quieter frames than this are never voiced
MIN_VOICED_S = 10.0             # voiced seconds before a number is given (pushed back: each side)
PUSHBACK = ("no", "but", "actually", "i disagree", "i don't think", "i don't agree", "not really", "not sure",
            "i'm not sure", "that's not", "why")
# Closer to the usual than this is "about usual" whatever the spread: about what an ear tells apart (1 dB, half a
# semitone), so a run of near-identical past values doesn't call 0.01 dB louder.
NEAR = {"pitch_range": 0.5, "loudness": 1.0, "pushed_pitch": 0.5, "pushed_loudness": 1.0}
REPLY_S = 8.0                   # questions.REPLY_S: a turn later than this isn't an answer
CACHE = DATA / "tone.json"
VERSION = 1                     # bump when a number's meaning changes: every kept number is read again
PAGE_BUDGET_S = 8.0             # a page measures what's missing for this long; `octo process` measures the rest


# ---- the two numbers on samples --------------------------------------------------------------

def frames(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(F0 in Hz, NaN where unvoiced; level in dBFS) per 10 ms frame of float samples already high-passed."""
    maxlag = int(RATE / FMIN) + 1
    minlag = int(RATE / FMAX)
    span = WIN + maxlag
    n = max(0, (len(x) - span) // HOP + 1)
    f0, db = np.full(n, np.nan, np.float32), np.full(n, -120.0, np.float32)
    nfft = 1 << (2 * span - 1).bit_length()
    for a in range(0, n, 2000):  # 20 s of frames at a time: an hour at once would be a few GB
        idx = np.arange(a, min(n, a + 2000))[:, None] * HOP + np.arange(span)[None, :]
        fr = x[idx].astype(np.float64)
        head = fr[:, :WIN]
        db[a:a + len(fr)] = 10 * np.log10(np.mean(head ** 2, axis=1) + 1e-12)
        # d(tau) = E(0) + E(tau) - 2 r(tau), the energies over WIN samples starting at 0 and at tau
        r = np.fft.irfft(np.fft.rfft(fr, nfft) * np.conj(np.fft.rfft(head, nfft)), nfft)[:, :maxlag + 1]
        c = np.concatenate([np.zeros((len(fr), 1)), np.cumsum(fr ** 2, axis=1)], axis=1)
        e = c[:, WIN:WIN + maxlag + 1] - c[:, :maxlag + 1]
        d = np.maximum(e[:, :1] + e - 2 * r, 0)
        cum = np.cumsum(d[:, 1:], axis=1)
        dn = np.ones_like(d)
        dn[:, 1:] = d[:, 1:] * np.arange(1, maxlag + 1) / np.maximum(cum, 1e-12)
        band = dn[:, minlag:maxlag + 1]
        # the first dip below THRESHOLD, at its floor: the first local minimum that's under it
        low = np.ones(band.shape, bool)
        low[:, 1:] &= band[:, 1:] <= band[:, :-1]
        low[:, :-1] &= band[:, :-1] <= band[:, 1:]
        hit = low & (band < THRESHOLD)
        has = hit.any(axis=1) & (db[a:a + len(fr)] > SILENCE_DB)
        i = np.nonzero(has)[0]
        k = np.argmax(hit[i], axis=1)
        y0, y1, y2 = band[i, np.maximum(k - 1, 0)], band[i, k], band[i, np.minimum(k + 1, band.shape[1] - 1)]
        den = y0 - 2 * y1 + y2
        safe = np.where(den > 0, den, 1.0)
        tau = minlag + k + np.where(den > 0, 0.5 * (y0 - y2) / safe, 0.0)  # between samples, by a parabola
        f0[a + i] = RATE / tau
    return f0, db


def pitch_range(f0: np.ndarray) -> float | None:
    """10th to 90th percentile of the voiced frames, in semitones; None under MIN_VOICED_S."""
    v = f0[~np.isnan(f0)]
    if len(v) * HOP / RATE < MIN_VOICED_S:
        return None
    lo, hi = np.percentile(v, [10, 90])
    return float(12 * np.log2(hi / lo))


def level(f0: np.ndarray, db: np.ndarray) -> float | None:
    """The median level of the voiced frames, dBFS; None under MIN_VOICED_S."""
    v = db[~np.isnan(f0)]
    return float(np.median(v)) if len(v) * HOP / RATE >= MIN_VOICED_S else None


# ---- whose frames ---------------------------------------------------------------------------

def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _minus(spans: list[tuple[float, float]], cut: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Both sorted and merged (_merge), so one walk through each: was every span against every cut, a third of a
    conversation page's time with 70 conversations behind it (28 Oct)."""
    out, j = [], 0
    for a, b in spans:
        while j < len(cut) and cut[j][1] <= a:
            j += 1
        for k in range(j, len(cut)):
            c, d = cut[k]
            if c >= b:
                break
            if c > a:
                out.append((a, c))
            a = max(a, d)
            if a >= b:
                break
        if a < b:
            out.append((a, b))
    return out


def spans(words: list[dict]) -> dict:
    """The owner's and the others' clean spans (session seconds), and the owner's pushed-back ones."""
    import bisect
    from .delivery import floors
    starts = [w["start"] for w in words]
    me = _merge([(w["start"], w["end"]) for w in words if w["me"]])
    them = _merge([(w["start"], w["end"]) for w in words if not w["me"] and w["spk"] is not None])
    pushed = []
    fs = floors(words)
    for prev, f in zip(fs, fs[1:]):
        if f["spk"] != "me" or prev["spk"] == "me" or f["start"] - prev["end"] > REPLY_S:
            continue
        lo, hi = bisect.bisect_left(starts, prev["start"]), bisect.bisect_right(starts, prev["end"])
        said = [w["w"] for w in words[lo:hi] if not w["me"]]
        if said and (said[-1].rstrip().endswith("?") or pushes_back(" ".join(said))):
            pushed.append((f["start"], f["end"]))
    return {"me": _minus(me, them), "them": _minus(them, me), "pushed": _minus(_merge(pushed), them)}


def pushes_back(text: str) -> bool:
    """Someone else's turn that opens with disagreement ("No, …", "But …", "I don't think …")."""
    from .fillers import normalize
    t = normalize(text)
    return any(t == p or t.startswith(p + " ") for p in PUSHBACK)


def _inside(f: np.ndarray, sp: list[tuple[float, float]], start: float) -> np.ndarray:
    """A mask of the frames (from `start`, session seconds) whose centre falls in these spans."""
    t = start + (np.arange(len(f)) * HOP + WIN / 2) / RATE
    m = np.zeros(len(f), bool)
    for a, b in sp:
        m |= (t >= a) & (t < b)
    return m


def measure(x: np.ndarray, sp: dict, start: float = 0.0) -> dict:
    """The four numbers from a conversation's samples (float, untouched) and its spans."""
    f0, db = frames(A.highpass(x))
    mine, theirs = _inside(f0, sp["me"], start), _inside(f0, sp["them"], start)
    push = _inside(f0, sp["pushed"], start) & mine
    rest = mine & ~push
    sub = lambda m: (np.where(m, f0, np.nan), db)
    me_level, them_level = level(*sub(mine)), level(*sub(theirs))
    pr_push, pr_rest = pitch_range(sub(push)[0]), pitch_range(sub(rest)[0])
    lv_push, lv_rest = level(*sub(push)), level(*sub(rest))
    r = lambda v: None if v is None else round(v, 2)
    return {"pitch_range": r(pitch_range(sub(mine)[0])),
            "loudness": r(me_level - them_level) if me_level is not None and them_level is not None else None,
            "pushed_pitch": r(pr_push - pr_rest) if pr_push is not None and pr_rest is not None else None,
            "pushed_loudness": r(lv_push - lv_rest) if lv_push is not None and lv_rest is not None else None,
            "level_me": r(me_level), "level_them": r(them_level),
            "voiced_s": round(float((~np.isnan(f0[mine])).sum()) * HOP / RATE, 1),
            "pushed_s": round(float((~np.isnan(f0[push])).sum()) * HOP / RATE, 1)}


# ---- kept numbers, read at page time ------------------------------------------------------------

VOICE = ("pitch_range", "loudness", "pushed_pitch", "pushed_loudness")


def source(sess: dict, sp: dict) -> dict:  # sp: spans() of delivery.timed_words
    """What a conversation's voice numbers are made from: its audio, its bounds and the spans (no audio read)."""
    key = hashlib.sha256(json.dumps([VERSION, sess.get("path"), sess["start_s"], sess["end_s"], sp]).encode()).hexdigest()[:16]
    return {"key": key, "path": sess.get("path"), "start_s": sess["start_s"], "end_s": sess["end_s"],
            "gone": bool(sess.get("audio_deleted_at")) or not sess.get("path"), "spans": sp,
            "pushed": len(sp["pushed"])}


def _load() -> dict:
    try:
        got = json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}
    # from an import too: anything not shaped as written is measured again
    return {k: v for k, v in got.items() if isinstance(v, dict) and isinstance(v.get("key"), str)} if isinstance(got, dict) else {}


def fill(conn, cs: list[dict], budget_s: float | None = None) -> None:
    """Puts the four numbers on each conversation record (delivery.conversation's, `voice_src` its spans): kept
    ones, else
    measured from the stored audio and kept (for up to `budget_s`, the first ones first); None with `voice_gone` when
    the audio's gone and nothing was kept."""
    from .db import row, rows
    from .files import write_private
    kept, changed, t0 = _load(), False, time.monotonic()
    for c in cs:
        s = c.get("voice_src") and row(conn, "SELECT r.path, r.audio_deleted_at, s.start_s, s.end_s FROM sessions s "
                                             "JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", c["session_id"])
        src = s and source(s, c["voice_src"])
        got = kept.get(c["session_id"])
        late = budget_s is not None and changed and time.monotonic() - t0 > budget_s  # the first always
        if src and not (got and got.get("key") == src["key"]) and not src["gone"] and not late:
            try:
                x = A.read(HOME / src["path"], src["start_s"], src["end_s"])
            except (OSError, RuntimeError):  # shortcut: a file missing without audio_deleted_at reads as gone
                x = None
            if x is not None:
                got = {"key": src["key"], **measure(x, src["spans"], 0.0)}
                kept[c["session_id"]] = got
                changed = True
        ok = bool(src and got and got.get("key") == src["key"])
        c.update({m: got[m] if ok else None for m in VOICE})
        c["voice_gone"] = bool(src and not ok and src["gone"])
        c["voice_s"] = got.get("voiced_s") if ok else None
        c["pushed_s"] = got.get("pushed_s") if ok else None
        c["pushed_turns"] = src["pushed"] if src else 0
    if changed:
        # Numbers of conversations deleted or set aside go with them.
        active = {r["id"] for r in rows(conn, "SELECT id FROM sessions WHERE status='active'")}
        write_private(CACHE, json.dumps({k: v for k, v in kept.items() if k in active}))
