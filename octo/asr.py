"""Transcription: which language, and the words with their timings.

The pieces the transcribe stage (octo/stages.py) is built from: the language vote over Whisper's
guesses, the louder copy the models hear, the two word backends (Qwen3-ASR with its forced
aligner, and Whisper as used until 2 Oct), and the English-only rule. Measurements behind each:
docs/learnings.md.
"""

from __future__ import annotations

import numpy as np

from . import RATE
from . import audio as A


def overlap(a: tuple[float, float], spans: list[list[float]]) -> float:
    """Seconds of [a0, a1) covered by spans."""
    return sum(max(0.0, min(a[1], e) - max(a[0], s)) for s, e in spans)


def speech_windows(spans: list[list[float]], n: int = 5, size: float = 30.0) -> list[float]:
    """Start times of the `n` windows of `size` seconds with the most detected speech."""
    per: dict[int, float] = {}
    for a, b in spans:
        for w in range(int(a // size), int(b // size) + 1):
            per[w] = per.get(w, 0.0) + max(0.0, min(b, (w + 1) * size) - max(a, w * size))
    return [w * size for w in sorted(per, key=lambda w: -per[w])[:n] if per[w] > 0]


def vote_language(window_probs: list[dict], allowed: list[str]) -> tuple[str, dict]:
    """Average Whisper's language probabilities over the windows, among the owner's languages only."""
    votes = {lang: sum(p.get(lang, 0.0) for p in window_probs) / max(1, len(window_probs)) for lang in allowed}
    return max(votes, key=votes.get), {k: round(v, 3) for k, v in votes.items()}


def top_languages(window_probs: list[dict], n: int = 3) -> list[dict]:
    """Each window's n likeliest languages among all Whisper knows, so a later look can see what
    the vote among the owner's four leaves out (Urdu, say). Stored on new runs only (review 4 Oct, M25)."""
    return [{k: round(v, 3) for k, v in sorted(p.items(), key=lambda kv: -kv[1])[:n]} for p in window_probs]


def language_unsure(tr: dict | None) -> bool:
    """The owner's four languages got under half of Whisper's vote: what it heard may be none of them
    (7 of 18 runs on 4 Oct). Read from the stored votes, so it holds for older runs too."""
    votes = (tr or {}).get("language_votes")
    return bool(votes) and sum(votes.values()) < 0.5


def louder(samples: np.ndarray, spans: list[list[float]], params: dict) -> np.ndarray:
    """High-pass, then bring the speech to normalize_dbfs (gain capped). The recorder across a room
    is 25-35 dB quieter than a phone memo; Whisper copes worse with quiet audio, especially when
    guessing the language. Only Whisper's input changes; the stored audio stays as recorded."""
    x = A.highpass(samples, params["highpass_hz"])
    speech = np.concatenate([x[int(a * RATE):int(b * RATE)] for a, b in spans]) if spans else x
    rms = float(np.sqrt(np.mean(speech ** 2))) + 1e-9 if len(speech) else 1.0
    gain = min(10 ** (params["max_gain_db"] / 20), 10 ** (params["normalize_dbfs"] / 20) / rms)
    return np.clip(x * gain, -1, 1).astype(np.float32)


def speech_overlap_ok(a: float, b: float, spans: list[list[float]], params: dict) -> bool:
    """Speech recognisers invent text in silence ("Thank you.", "Thanks for watching"). Keep a
    stretch only if enough of it overlaps speech the detector actually heard."""
    return not spans or overlap((a, b), spans) / max(0.01, b - a) >= params["min_speech_overlap"]


# MLX in a worker (architecture review 5 Nov, risk 6): its free-buffer cache grew unbounded (a transcription held ~10 GB
# on a 16 GB Mac, learnings 4 Nov). Capped, a freed buffer goes back to the OS instead of waiting in the cache; the
# memory limit makes MLX wait for scheduled work before allocating past it. Both measured: docs/learnings.md, 5 Nov.
MLX_MEMORY_SHARE = 0.5      # of the Mac's RAM
MLX_CACHE_BYTES = 1 << 30   # 1 GB of freed buffers kept for reuse


def cap_mlx() -> tuple[int, int]:
    """Cap MLX for this process (a worker); returns (memory limit, cache limit) in bytes."""
    import subprocess

    import mlx.core as mx
    total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True).stdout)
    limits = int(total * MLX_MEMORY_SHARE), min(MLX_CACHE_BYTES, int(total * MLX_MEMORY_SHARE))
    mx.set_memory_limit(limits[0])
    mx.set_cache_limit(limits[1])
    return limits


def qwen_words(samples: np.ndarray, spans: list[list[float]], language: str, params: dict) -> tuple[list, list]:
    from mlx_qwen3_asr import transcribe as qwen

    r = qwen(samples, model=params["model"], language=LANGUAGE_NAMES[language], return_timestamps=True)
    words = punctuate([{"w": w["text"], "start": round(w["start"], 2), "end": round(w["end"], 2)}
                       for w in (r.segments or []) if w["text"].strip()], r.text or "")
    segments, rejected = [], []
    for i, seg in enumerate(group_words(words, params["segment_gap_s"])):
        text = " ".join(w["w"] for w in seg)
        a, b = seg[0]["start"], seg[-1]["end"]
        if not speech_overlap_ok(a, b, spans, params):
            # Kept whole: diarize gives it back if a speaker's turn covers it (review 4 Oct, M17).
            rejected.append({"start": a, "end": b, "text": text, "words": seg})
            continue
        segments.append({"id": i, "start": a, "end": b, "text": text, "words": seg})
    return segments, rejected


def whisper_words(samples: np.ndarray, spans: list[list[float]], language: str, params: dict) -> tuple[list, list]:
    """Whisper large-v3 on the GPU (mlx-whisper), as used until 2 Oct (transcribe v3)."""
    import mlx_whisper

    r = mlx_whisper.transcribe(samples, path_or_hf_repo=params["model"], language=language,
                               word_timestamps=True, condition_on_previous_text=False)
    segments, rejected = [], []
    for i, s in enumerate(r["segments"]):
        text = s["text"].strip()
        if not text:
            continue
        if not speech_overlap_ok(s["start"], s["end"], spans, params):
            rejected.append({"start": round(s["start"], 2), "end": round(s["end"], 2), "text": text,
                             "words": [{"w": w["word"].strip(), "start": round(w["start"], 2), "end": round(w["end"], 2)}
                                       for w in s.get("words", [])]})
            continue
        segments.append({
            "id": i, "start": round(s["start"], 2), "end": round(s["end"], 2), "text": text,
            "avg_logprob": round(s.get("avg_logprob", 0.0), 3),
            "no_speech_prob": round(s.get("no_speech_prob", 0.0), 3),
            "words": [{"w": w["word"].strip(), "start": round(w["start"], 2), "end": round(w["end"], 2),
                       "p": round(w.get("probability", 0.0), 3)} for w in s.get("words", [])],
        })
    return segments, rejected


def punctuate(words: list[dict], text: str) -> list[dict]:
    """Put the transcript's punctuation back on the aligned words: the aligner strips it, and lines
    break at sentence ends and questions are counted from "?". Words are matched in order on their
    stripped form; a word that can't be matched keeps the aligner's spelling."""
    import difflib

    from mlx_qwen3_asr.forced_aligner import ForcedAlignTextProcessor as T

    tokens = [t for t in text.split() if T.clean_token(t)]
    if len(tokens) == len(words):
        return [{**w, "w": t} for w, t in zip(words, tokens)]
    out = [dict(w) for w in words]
    m = difflib.SequenceMatcher(a=[w["w"].lower() for w in words], b=[T.clean_token(t).lower() for t in tokens],
                                autojunk=False)
    for blk in m.get_matching_blocks():
        for k in range(blk.size):
            out[blk.a + k]["w"] = tokens[blk.b + k]
    return out


def group_words(words: list[dict], gap_s: float) -> list[list[dict]]:
    """Runs of words with no pause longer than gap_s between them."""
    out: list[list[dict]] = []
    for w in words:
        if out and w["start"] - out[-1][-1]["end"] <= gap_s:
            out[-1].append(w)
        else:
            out.append([w])
    return out


# 2 Oct 2026: languages other than English are paused. Since 4 Oct Hindi and mixed
# conversations are transcribed another way (octo/hindi.py); Kannada and Gujarati stay paused. On 9 stretches of a Hindi family
# conversation, Whisper large-v3, Qwen3-ASR-1.7B and a Hinglish Whisper fine-tune were all "none is
# usable" (bench/asrcompare). Those conversations keep their speakers and talk time, but show no
# transcript and get no insights; their audio is kept (D5) so they can be redone with a better model.
TRANSCRIBE_LANGUAGES = ("en",)
LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "kn": "Kannada", "gu": "Gujarati"}


def language_paused(tr: dict | None) -> str | None:
    """Why a transcript isn't used ("Mostly Hindi", "Hindi and English mixed"), or None. English
    must carry at least twice the weight of the other languages in the vote: a mixed conversation
    transcribed as English comes out as Hindi words misheard as English ones. Hindi and mixed
    conversations merged by Claude (octo/hindi.py, 4 Oct) are used."""
    if not tr or tr.get("merged"):
        return None
    votes, lang = tr.get("language_votes"), tr.get("language")
    if not votes:
        return None if lang in TRANSCRIBE_LANGUAGES else f"In {LANGUAGE_NAMES.get(lang, lang)}"
    on = sum(v for k, v in votes.items() if k in TRANSCRIBE_LANGUAGES)
    off = {k: v for k, v in votes.items() if k not in TRANSCRIBE_LANGUAGES}
    if on >= 2 * sum(off.values()):
        return None
    top = LANGUAGE_NAMES.get(max(off, key=off.get), "another language")
    return f"Mostly {top}" if lang not in TRANSCRIBE_LANGUAGES else f"{top} and English mixed"


def other_language(tr: dict) -> str | None:
    """The language other than English with the most weight in the vote (or the one heard)."""
    votes, lang = tr.get("language_votes"), tr.get("language")
    off = {k: v for k, v in (votes or {}).items() if k not in TRANSCRIBE_LANGUAGES}
    if off:
        return max(off, key=off.get)
    return lang if lang not in TRANSCRIBE_LANGUAGES else None
