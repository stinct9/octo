"""Nemotron 3 Diarization on one 16 kHz mono WAV; prints {"turns", "seconds", "device"} as JSON.

Runs in its own environment (.venv-nemo: NVIDIA NeMo is a heavy install and pins its own
versions), so it imports nothing from octo:

    uv venv -p python3.12 .venv-nemo && VIRTUAL_ENV=.venv-nemo uv pip install Cython packaging 'nemo-toolkit[asr]'
    .venv-nemo/bin/python bench/nemo_diar.py FILE.wav [--device cpu|mps]

The model card lists only NVIDIA GPUs; on a Mac this is untested by NVIDIA.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

# The model card's offline configuration: a 30.4 s input buffer (lowest error).
OFFLINE = {"spkcache_len": 264, "fifo_len": 40, "chunk_len": 340, "chunk_right_context": 40,
           "spkcache_update_period": 300}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--model", default="nvidia/Nemotron-3-Diarization")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    import logging

    import torch
    from nemo.collections.asr.models import SortformerEncLabelModel
    from nemo.utils import logging as nemo_logging

    nemo_logging.setLevel(logging.ERROR)
    model = SortformerEncLabelModel.from_pretrained(args.model, map_location=args.device)
    model.eval()
    for k, v in OFFLINE.items():
        setattr(model.sortformer_modules, k, v)
    model._check_streaming_parameters()

    t0 = time.time()
    with torch.inference_mode():
        segs = model.diarize(audio=[args.wav], batch_size=1, verbose=False)[0]
    took = time.time() - t0
    turns = []
    for s in segs:  # "begin end speaker_N"
        a, b, spk = s.split() if isinstance(s, str) else s
        turns.append({"speaker": str(spk), "start": round(float(a), 2), "end": round(float(b), 2)})
    json.dump({"turns": sorted(turns, key=lambda t: t["start"]), "seconds": round(took, 1), "device": args.device},
              sys.stdout)


if __name__ == "__main__":
    main()
