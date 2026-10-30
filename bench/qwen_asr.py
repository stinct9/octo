"""Qwen3-ASR on one 16 kHz mono WAV; prints {"language", "chunks", "words", "seconds"} as JSON.

Runs in its own environment (.venv-qwen; MLX, no PyTorch), so it imports nothing from octo:

    uv venv -p python3.12 .venv-qwen && VIRTUAL_ENV=.venv-qwen uv pip install 'mlx-qwen3-asr[timestamps]'
    .venv-qwen/bin/python bench/qwen_asr.py FILE.wav [--model Qwen/Qwen3-ASR-1.7B] [--context "Asha ..."]

Word timings come from Qwen3-ForcedAligner, which officially covers 11 languages -- not Hindi.
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B")
    ap.add_argument("--language", default=None)
    ap.add_argument("--context", default="")
    args = ap.parse_args()

    from mlx_qwen3_asr import transcribe

    t0 = time.time()
    r = transcribe(args.wav, model=args.model, language=args.language, context=args.context,
                   return_timestamps=True, return_chunks=True)
    words = []
    for s in r.segments or []:
        get = s.get if isinstance(s, dict) else lambda k, d=None: getattr(s, k, d)
        text = (get("text") or get("word") or "").strip()
        if text:
            words.append({"w": text, "start": round(float(get("start")), 2), "end": round(float(get("end")), 2)})
    chunks = [{k: c.get(k) for k in ("start", "end", "language")} for c in (r.chunks or []) if isinstance(c, dict)]
    json.dump({"language": r.language, "text": r.text, "words": words, "chunks": chunks,
               "seconds": round(time.time() - t0, 1)}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
