"""Gemma 4 (Google, open weights, audio in) on short WAV clips; prints {"model", "texts": {clip: text}}.

Runs in its own environment (.venv-gemma; mlx-vlm), so it imports nothing from octo:

    uv venv -p python3.12 .venv-gemma && VIRTUAL_ENV=.venv-gemma uv pip install mlx-vlm soundfile librosa
    .venv-gemma/bin/python bench/gemma_asr.py CLIP.wav... [--model mlx-community/gemma-4-E4B-it-qat-8bit]

Gemma takes at most 30 s of audio, so a longer clip is cut in two at its quietest moment near the
middle and the halves' text joined. The E4B model in bf16 is 16 GB, too big beside everything else
on a 16 GB Mac; the 8-bit build is Google's quantization-aware-trained one (qat).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000
MAX_S = 29.0
# Google's recommended ASR prompt (model card), with the language as the owner's conversations have it.
PROMPT = ("Transcribe the following speech segment in Hindi, with English words mixed in, into text: Hindi in "
          "Devanagari, English words in Latin script. Follow these specific instructions for formatting the "
          "answer: * Only output the transcription, with no newlines. * When transcribing numbers, write the "
          "digits, i.e. write 1.7 and not one point seven, and write 3 instead of three.")


def read(path: str) -> np.ndarray:
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


def pieces(x: np.ndarray) -> list[np.ndarray]:
    """Cut at the quietest half second in the middle third, until every piece fits."""
    if len(x) <= MAX_S * RATE:
        return [x]
    win = RATE // 2
    lo, hi = len(x) // 3, 2 * len(x) // 3
    energy = [float(np.mean(x[i:i + win].astype(np.float32) ** 2)) for i in range(lo, hi - win, RATE // 20)]
    cut = lo + int(np.argmin(energy)) * (RATE // 20) + win // 2
    return pieces(x[:cut]) + pieces(x[cut:])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--model", default="mlx-community/gemma-4-E4B-it-qat-8bit")
    args = ap.parse_args()

    from mlx_vlm import apply_chat_template, generate, load

    model, processor = load(args.model)
    prompt = apply_chat_template(processor, model.config, PROMPT, num_audios=1)
    texts, t0 = {}, time.time()
    with tempfile.TemporaryDirectory() as tmp:
        for c in args.clips:
            parts = []
            for k, x in enumerate(pieces(read(c))):
                f = Path(tmp) / f"{Path(c).stem}_{k}.wav"
                with wave.open(str(f), "wb") as w:
                    w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
                    w.writeframes(x.tobytes())
                r = generate(model, processor, prompt, audio=[str(f)], max_tokens=400, temperature=0.0)
                parts.append((r.text if hasattr(r, "text") else str(r)).strip())
            texts[c] = " ".join(parts)
            print(f"{Path(c).name}: {len(parts)} part(s), {len(texts[c].split())} words", file=sys.stderr, flush=True)
    json.dump({"model": args.model, "texts": texts, "seconds": round(time.time() - t0, 1)}, sys.stdout,
              ensure_ascii=False)


if __name__ == "__main__":
    main()
