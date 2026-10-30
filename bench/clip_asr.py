"""Transcribe short WAV clips with a Hugging Face model; prints {clip: text} as JSON.

Runs in .venv-nemo (it has torch, transformers and onnxruntime), so it imports nothing from octo:

    .venv-nemo/bin/python bench/clip_asr.py MODEL --language hi CLIP.wav...

Models:
- Trelis/whisper-hinglish-preview: Whisper large-v3 fine-tuned for Hindi with English mixed in
  (Devanagari for Hindi, Latin for English).
- ai4bharat/indic-conformer-600m-multilingual: AI4Bharat's Conformer for the 22 Indian languages.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave

import numpy as np


def load(path: str) -> np.ndarray:
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def whisper(model_id: str, language: str, clips: list[str]) -> dict[str, str]:
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    proc = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(model_id, torch_dtype=torch.float16).to(device).eval()
    out = {}
    for c in clips:
        # Clips can run past Whisper's 30 s window: long-form (sequential) decoding, not truncation.
        x = proc(load(c), sampling_rate=16000, return_tensors="pt", truncation=False, padding="longest",
                 return_attention_mask=True)
        feats = x.input_features.to(device, torch.float16)
        with torch.inference_mode():
            ids = model.generate(feats, attention_mask=x.attention_mask.to(device), language=language,
                                 task="transcribe", return_timestamps=True)
        out[c] = proc.batch_decode(ids, skip_special_tokens=True)[0].strip()
    return out


def indic_conformer(model_id: str, language: str, clips: list[str]) -> dict[str, str]:
    import torch
    from transformers import AutoModel

    model = AutoModel.from_pretrained(model_id, trust_remote_code=True)
    out = {}
    for c in clips:
        wav = torch.from_numpy(load(c)).unsqueeze(0)
        with torch.inference_mode():
            out[c] = str(model(wav, language, "rnnt")).strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--language", default="hi")
    args = ap.parse_args()
    t0 = time.time()
    fn = indic_conformer if "conformer" in args.model else whisper
    texts = fn(args.model, args.language, args.clips)
    json.dump({"model": args.model, "seconds": round(time.time() - t0, 1), "texts": texts}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
