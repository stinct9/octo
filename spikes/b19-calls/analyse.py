"""SPIKE (B19): what a CallTap file says. Channel 1 is the mic, channel 2 the tapped app.

Per window: how late the app's sound reaches the mic (cross-correlation peak, ±500 ms), how strongly (the
correlation, and the app's level in the mic as a gain in dB), and the levels. Over the file: drift = how much
that lag moves from the first window to the last, and the length against the wall clock if given.

    python -I analyse.py FILE.wav [WINDOW_S] [EXPECTED_S]
    python -I analyse.py --cancel FILE.wav FIT_FROM FIT_TO   (owner quiet between FIT_FROM and FIT_TO seconds)

--cancel asks whether the app channel can take the others back out of the mic on speakers: an echo path (FIR,
frequency domain, the first 256 ms kept) fitted on the first half of the quiet stretch, judged on its second half, then the
level left in each 10 s window.
"""
import sys

import numpy as np
import soundfile as sf


def db(x):
    return 20 * np.log10(max(x, 1e-12))


def lag(mic, app, sr, span=0.5):
    """(lag in ms of app inside mic, peak normalised correlation, app→mic gain in dB at that lag)."""
    n = len(mic)
    size = 1 << int(np.ceil(np.log2(2 * n)))
    c = np.fft.irfft(np.fft.rfft(mic, size) * np.conj(np.fft.rfft(app, size)), size)
    k = int(span * sr)
    c = np.concatenate([c[-k:], c[:k + 1]])
    i = int(np.argmax(np.abs(c)))
    norm = np.sqrt((mic ** 2).sum() * (app ** 2).sum()) or 1
    gain = c[i] / ((app ** 2).sum() or 1)  # least-squares gain of the app's sound inside the mic at that lag
    return (i - k) * 1000 / sr, c[i] / norm, db(abs(gain))


def tracked(mic, app, sr, centre_ms, span_ms=2.0):
    """The lag of one echo path: the correlation peak within ±span_ms of centre_ms, refined to a fraction of a
    sample by a parabola. The strongest peak hops between paths (speakers and desk: 55, 61, 63 ms on the MacBook),
    so drift is read on one of them."""
    n = len(mic)
    size = 1 << int(np.ceil(np.log2(2 * n)))
    c = np.abs(np.fft.irfft(np.fft.rfft(mic, size) * np.conj(np.fft.rfft(app, size)), size))
    lo, hi = int((centre_ms - span_ms) * sr / 1000), int((centre_ms + span_ms) * sr / 1000)
    i = lo + int(np.argmax(c[lo:hi + 1]))
    y0, y1, y2 = c[i - 1], c[i], c[i + 1]
    d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) else 0
    return (i + d) * 1000 / sr


def echo_path(mic, app, n=8192):
    """Transfer function app→mic averaged over 50%-overlapped Hann frames (Welch cross-spectrum / auto-spectrum)."""
    w = np.hanning(n)
    sma = saa = 0
    for s in range(0, len(mic) - n, n // 2):
        A, M = np.fft.rfft(app[s:s + n] * w), np.fft.rfft(mic[s:s + n] * w)
        sma, saa = sma + M * np.conj(A), saa + (A * np.conj(A)).real
    h = np.fft.irfft(sma / (saa + 1e-9), n)
    h[n // 2:] = 0  # causal: the mic hears the speakers after they play
    return h


def cancel(path, fit_from, fit_to):
    x, sr = sf.read(path)
    mic, app = x[:, 0], x[:, 1]
    a, b = int(fit_from * sr), int(fit_to * sr)
    mid = (a + b) // 2
    h = echo_path(mic[a:mid], app[a:mid])
    left = mic - np.convolve(app, h)[:len(mic)]
    rms = lambda v: db(np.sqrt((v ** 2).mean()))
    print(f"held-out quiet stretch {mid / sr:.0f}-{fit_to:.0f} s: mic {rms(mic[mid:b]):.1f} dBFS, "
          f"after cancelling {rms(left[mid:b]):.1f} dBFS ({rms(left[mid:b]) - rms(mic[mid:b]):+.1f} dB)")
    step = 10 * sr
    print(" start_s  mic_dBFS  left_dBFS")
    for s in range(0, len(mic) - step + 1, step):
        print(f"{s / sr:8.0f} {rms(mic[s:s + step]):9.1f} {rms(left[s:s + step]):10.1f}")


def main():
    if sys.argv[1] == "--cancel":
        return cancel(sys.argv[2], float(sys.argv[3]), float(sys.argv[4]))
    path = sys.argv[1]
    win = float(sys.argv[2]) if len(sys.argv) > 2 else 30
    x, sr = sf.read(path)
    assert x.ndim == 2 and x.shape[1] == 2, "expected 2 channels"
    print(f"{path}: {sr} Hz, {len(x) / sr:.2f} s, {x.shape[1]} ch")
    if len(sys.argv) > 3:
        exp = float(sys.argv[3])
        print(f"length vs expected {exp:.0f} s: {len(x) / sr - exp:+.3f} s")
    step = int(win * sr)
    rows = []
    for s in range(0, len(x) - step + 1, step):
        m, a = x[s:s + step, 0], x[s:s + step, 1]
        ms, r, g = lag(m, a, sr)
        rows.append((s / sr, ms, r, g, db(np.sqrt((m ** 2).mean())), db(np.sqrt((a ** 2).mean()))))
    print(" start_s   lag_ms   corr  gain_dB  mic_dBFS  app_dBFS")
    for t, ms, r, g, md, ad in rows:
        print(f"{t:8.0f} {ms:8.2f} {r:6.3f} {g:8.1f} {md:9.1f} {ad:9.1f}")
    good = [r for r in rows if abs(r[2]) > 0.2]
    if len(good) >= 2:
        first = good[0][1]
        path = [(t, tracked(x[int(t * sr):int(t * sr) + step, 0], x[int(t * sr):int(t * sr) + step, 1], sr, first))
                for t, *_ in good]
        ls = [l for _, l in path]
        slope = np.polyfit([t for t, _ in path], ls, 1)[0] * 1000  # ms per 1000 s
        print(f"drift on the {first:.1f} ms path: first {ls[0]:.3f}, last {ls[-1]:.3f} ms over {path[-1][0]:.0f} s; "
              f"range {min(ls):.3f}..{max(ls):.3f}; fitted {slope:+.3f} ms per 1000 s")


if __name__ == "__main__":
    main()
