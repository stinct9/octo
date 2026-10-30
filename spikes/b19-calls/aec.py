"""SPIKE (B19): WebRTC's echo canceller (AEC3, through LiveKit's Python package) run after the fact on a CallTap file.

The app channel is the far end ("reverse stream"), the mic the near end; out comes the mic with the others taken out.
Needs `livekit` (Apache 2.0), which the lab doesn't use: run it from a scratch venv.

    python -I aec.py IN.wav OUT.wav FAR_ONLY_FROM FAR_ONLY_TO NEAR_ONLY_FROM NEAR_ONLY_TO

FAR_ONLY: the owner quiet, the others talking (how much of them it removes). NEAR_ONLY: the others quiet, the owner
talking (whether the owner is left alone). Then a made-up double talk with a known answer: the recorded far-only mic
plus the recorded near-only mic, summed (sound adds in the air), so the owner's part is known and what's left of the
others can be measured while both talk.
"""
import sys

import numpy as np
import soundfile as sf
from livekit import rtc

SR = 16000
FRAME = SR // 100  # AEC3 takes 10 ms frames
LATENCY = 128  # samples AEC3's output trails its input at 16 kHz (8 ms, measured on made-up audio: correlation 0.92)


def cancel(mic, far, noise=False):
    apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=noise, high_pass_filter=False,
                                    auto_gain_control=False)
    to16 = lambda v: (np.clip(v, -1, 1) * 32767).astype(np.int16)
    m16, f16, out = to16(mic), to16(far), np.zeros(len(mic), np.int16)
    for s in range(0, len(mic) - FRAME + 1, FRAME):
        r = rtc.AudioFrame(f16[s:s + FRAME].tobytes(), SR, 1, FRAME)
        apm.process_reverse_stream(r)
        n = rtc.AudioFrame(m16[s:s + FRAME].tobytes(), SR, 1, FRAME)
        apm.set_stream_delay_ms(0)  # the Mac's output and the mic share one clock here; AEC3 finds the acoustic delay
        apm.process_stream(n)
        out[s:s + FRAME] = np.frombuffer(bytes(n.data), np.int16)
    out = np.concatenate([out[LATENCY:], np.zeros(LATENCY, np.int16)])
    return out.astype(np.float64) / 32767


def rms(v):
    return 20 * np.log10(max(np.sqrt(np.mean(v ** 2)), 1e-9))


def mag(v, n=512):
    """Magnitude spectrogram (32 ms Hann frames, half overlap). AEC3 changes the waveform's phase but not its spectrum
    (the owner alone: waveform correlation 0.28, log-spectrogram correlation 1.000), and ASR and voiceprints read the
    spectrum, so everything is judged on magnitudes."""
    w = np.hanning(n)
    return np.abs(np.array([np.fft.rfft(v[s:s + n] * w) for s in range(0, len(v) - n, n // 2)]))[:, 8:192]  # 250 Hz-6 kHz


def energy_db(m):
    return 10 * np.log10(np.sum(m ** 2) + 1e-12)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    ff, ft, nf, nt = (int(float(a) * SR) for a in sys.argv[3:7])
    x, sr = sf.read(src)
    assert sr == SR and x.shape[1] == 2
    mic, far = x[:, 0], x[:, 1]
    out = cancel(mic, far)
    sf.write(dst, np.stack([mic, far, out], 1), SR, subtype="PCM_16")
    warm = ff + 5 * SR  # leave AEC3 5 s to converge
    a, b = mag(mic[warm:ft]), mag(out[warm:ft])
    print(f"others alone {warm / SR:.0f}-{ft / SR:.0f} s: {energy_db(b) - energy_db(a):+.1f} dB")
    a, b = mag(mic[nf:nt]), mag(out[nf:nt])
    print(f"owner alone {nf / SR:.0f}-{nt / SR:.0f} s: {energy_db(b) - energy_db(a):+.1f} dB, log-spectrogram correlation "
          f"{np.corrcoef(np.log(a + 1e-6).ravel(), np.log(b + 1e-6).ravel())[0, 1]:.3f}")

    # Made-up double talk with a known answer: warm-up on the far-only stretch, then far-only mic + near-only mic.
    # AEC3's gain on each time-frequency cell (|out| / |mix|) is applied to the two known parts, so what it kept of
    # the owner and left of the others can be read separately.
    n = min(ft - warm, nt - nf)
    owner, others = mic[nf:nf + n], mic[warm:warm + n]
    mixed = np.concatenate([mic[ff:warm], others + owner])
    ref = np.concatenate([far[ff:warm], far[warm:warm + n]])
    for noise in (False, True):
        got = cancel(mixed, ref, noise)[-n:]
        g = np.minimum(mag(got) / (mag(mixed[-n:]) + 1e-9), 2)
        o, t = mag(owner), mag(others)
        kept, left = energy_db(g * o) - energy_db(o), energy_db(g * t) - energy_db(t)
        print(f"both talking ({n / SR:.0f} s{', noise suppression on' if noise else ''}): owner {kept:+.1f} dB, "
              f"others {left:+.1f} dB; owner over others {energy_db(o) - energy_db(t):+.1f} dB raw -> "
              f"{energy_db(g * o) - energy_db(g * t):+.1f} dB after")


if __name__ == "__main__":
    main()
