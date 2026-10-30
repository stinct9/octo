"""SPIKE (B20, step 3b): the PyTorch model as an int8 TFLite model for the board, and its features in numpy exactly
as the board computes them. Checks both against PyTorch's scores on the check windows, then writes kws/model.h
(gitignored: it's trained on the owner's voice, so it stays on this Mac like a voiceprint).

Runs in a scratch venv with tensorflow 2.19 and numpy (not the lab's):
    TFVENV/bin/python spikes/b20-filler-cue/convert.py BOARD_DIR [HEADER]
BOARD_DIR is export.py's (data/bench/b20-fillers/board[-VARIANT] under the lab's HOME). HEADER defaults to kws/model.h;
for the recorder (rec-24 on): the lab's data/filler/octo_filler_model.h, which `octo device flash` copies in.
"""
import json
import sys
from pathlib import Path

import numpy as np
import tensorflow as tf

SR, N_FFT, WIN_LEN, HOP, N_MELS, FRAMES = 16000, 512, 480, 320, 40, 51
HEADER = Path(__file__).with_name("kws") / "model.h"


def window() -> np.ndarray:
    """torch.hann_window(480) (periodic), centred in 512 as torch.stft pads it."""
    w = np.zeros(N_FFT, np.float32)
    n = np.arange(WIN_LEN)
    w[(N_FFT - WIN_LEN) // 2:][:WIN_LEN] = 0.5 - 0.5 * np.cos(2 * np.pi * n / WIN_LEN)
    return w


def features(x: np.ndarray, fb: np.ndarray) -> np.ndarray:
    """One 1 s window (float, ±1) → (40, 51) as train.features: torchaudio's MelSpectrogram (centre, reflect padding,
    power 2), log(+1e-6), minus the mean. The board's kws.ino does the same, step for step."""
    p = np.pad(x, N_FFT // 2, mode="reflect")
    w = window()
    frames = np.stack([p[i * HOP:i * HOP + N_FFT] * w for i in range(FRAMES)])
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2          # (51, 257)
    f = np.log(spec @ fb + 1e-6).T                            # (40, 51)
    return (f - f.mean()).astype(np.float32)


def keras_model(sd: dict, down: bool = False) -> tf.keras.Model:
    width = len(sd["0.bias"])
    L = tf.keras.layers

    def bn(prefix):
        b = L.BatchNormalization(epsilon=1e-5)
        layers.append((b, [sd[prefix + ".weight"], sd[prefix + ".bias"], sd[prefix + ".running_mean"],
                           sd[prefix + ".running_var"]]))
        return b

    layers = []
    inp = L.Input((N_MELS, FRAMES, 1), batch_size=1)  # a fixed batch: no SHAPE/PACK ops for Flatten
    x = L.ZeroPadding2D(((2, 2), (1, 1)))(inp)  # torch's padding=(2, 1), not TF's "same"
    conv = L.Conv2D(width, (5, 3), strides=2)
    layers.append((conv, [sd["0.weight"].transpose(2, 3, 1, 0), sd["0.bias"]]))
    x = L.ReLU()(bn("1")(conv(x)))
    for b in range(3, 7):
        if down and b == 3:  # stride 2 with torch's padding 1 on every side (TF's "same" pads only the end)
            x = L.ZeroPadding2D(1)(x)
            dw = L.DepthwiseConv2D(3, strides=2)
        else:
            dw = L.DepthwiseConv2D(3, padding="same")
        layers.append((dw, [sd[f"{b}.0.weight"].transpose(2, 3, 0, 1), sd[f"{b}.0.bias"]]))
        x = L.ReLU()(bn(f"{b}.1")(dw(x)))
        pw = L.Conv2D(width, 1)
        layers.append((pw, [sd[f"{b}.3.weight"].transpose(2, 3, 1, 0), sd[f"{b}.3.bias"]]))
        x = L.ReLU()(bn(f"{b}.4")(pw(x)))
    x = L.Flatten()(L.AveragePooling2D(tuple(x.shape[1:3]))(x))  # AVERAGE_POOL_2D: TFLM's MEAN took 26 ms
    dense = L.Dense(2)
    layers.append((dense, [sd["9.weight"].T, sd["9.bias"]]))
    out = L.Softmax()(dense(x))
    m = tf.keras.Model(inp, out)
    for layer, w in layers:
        layer.set_weights(w)
    return m


def main(d: Path, header: Path = HEADER) -> None:
    meta = json.loads((d / "meta.json").read_text())
    THRESHOLD = meta["threshold"]
    sd = dict(np.load(d / "final.npz"))
    fb = np.load(d / "fb.npy").astype(np.float32)
    xw = np.load(d / "check_x.npy").astype(np.float32) / 32768
    p_torch = np.load(d / "check_p.npy")
    F = np.stack([features(x, fb) for x in xw])[..., None]

    m = keras_model(sd, meta["down"])
    p_keras = np.array([m(f[None]).numpy()[0, 1] for f in F])
    print(f"numpy features + Keras against PyTorch: max |diff| {np.abs(p_keras - p_torch).max():.4f}")

    calib = np.load(d / "calib.npy").transpose(0, 2, 3, 1)
    conv = tf.lite.TFLiteConverter.from_keras_model(m)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = lambda: ([c[None]] for c in calib)
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = conv.inference_output_type = tf.int8
    blob = conv.convert()
    (d / "model.tflite").write_bytes(blob)

    it = tf.lite.Interpreter(model_content=blob)
    it.allocate_tensors()
    i, o = it.get_input_details()[0], it.get_output_details()[0]
    (si, zi), (so, zo) = i["quantization"], o["quantization"]
    p_int8 = []
    for f in F:
        it.set_tensor(i["index"], np.clip(np.round(f[None] / si + zi), -128, 127).astype(np.int8))
        it.invoke()
        p_int8.append((it.get_tensor(o["index"]).reshape(-1)[1].astype(np.float32) - zo) * so)
    p_int8 = np.array(p_int8)
    agree = ((p_int8 >= THRESHOLD) == (p_torch >= THRESHOLD)).mean()
    print(f"int8: {len(blob)} bytes; against PyTorch max |diff| {np.abs(p_int8 - p_torch).max():.4f}, "
          f"same side of {THRESHOLD}: {agree:.0%}; ops {sorted({op['op_name'] for op in it._get_ops_details()})}")
    print(f"input scale {si} zero {zi}; output scale {so} zero {zo}")
    np.save(d / "check_p_int8.npy", p_int8)
    if agree < 0.97:  # B22's loop installs what this writes: a conversion that disagrees isn't one
        sys.exit(f"int8 agrees with PyTorch on only {agree:.0%} of the check windows: no header written")

    header.parent.mkdir(exist_ok=True)
    with open(header, "w") as h:
        h.write("// Generated by convert.py from the owner's voice: never commit (see .gitignore).\n#pragma once\n")
        h.write(f"const float KWS_THRESHOLD = {THRESHOLD}f;\n")
        h.write(f"alignas(16) const unsigned char kws_model[{len(blob)}] = {{\n")
        for k in range(0, len(blob), 20):
            h.write("  " + ",".join(str(b) for b in blob[k:k + 20]) + ",\n")
        h.write("};\n")
        h.write(f"const float kws_fb[{fb.shape[0]}][{fb.shape[1]}] = {{\n")
        for row in fb:
            h.write("  {" + ",".join(f"{v:.8e}f" for v in row) + "},\n")
        h.write("};\n")
    print(f"wrote {header}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).expanduser(), *(Path(a).expanduser() for a in sys.argv[2:3]))
