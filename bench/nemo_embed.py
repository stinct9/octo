"""Speaker embeddings from a NeMo model (TitaNet), for bench/voicecompare.py.

Runs in .venv-nemo (see bench/nemo_diar.py for the install), so it imports nothing from octo:

    .venv-nemo/bin/python bench/nemo_embed.py IN.npz OUT.npz [--model nvidia/speakerverification_en_titanet_large]

IN.npz holds 16 kHz float32 waveforms by key; OUT.npz gets one embedding per key. NVIDIA's own
code, not an ONNX export: sherpa-onnx's TitaNet gave embeddings only 0.92-0.95 cosine to these.
"""

from __future__ import annotations

import argparse
import logging

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--model", default="nvidia/speakerverification_en_titanet_large")
    args = ap.parse_args()

    import torch
    from nemo.collections.asr.models import EncDecSpeakerLabelModel
    from nemo.utils import logging as nemo_logging

    nemo_logging.setLevel(logging.ERROR)
    model = EncDecSpeakerLabelModel.from_pretrained(args.model, map_location="cpu")
    model.eval()
    out = {}
    with np.load(args.src) as waves, torch.no_grad():
        for key in waves.files:
            w = torch.from_numpy(waves[key].astype(np.float32))[None]
            _, emb = model.forward(input_signal=w, input_signal_length=torch.tensor([w.shape[1]]))
            out[key] = emb.numpy().reshape(-1).astype(np.float32)
    np.savez(args.dst, **out)


if __name__ == "__main__":
    main()
