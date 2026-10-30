"""Calls on the Mac (B19, docs/backlog.md; the spike: docs/learnings.md, "Calls on the Mac").

The Octo app records a call as one 16 kHz two-channel file: channel 0 the mic (the owner), channel 1 the call app's
sound (the others). The lab keeps both (octo/audio.py) and the steps read them apart:
- on speakers the mic also hears the others, 55-63 ms late at about -14 dB: WebRTC's echo canceller (AEC3, through
  LiveKit's package, run here on the Mac when the call is added) takes them ~13 dB down, with the app channel as
  what the speakers played;
- the mic's words are the owner's; pyannote separates only the others' channel;
- identify names the mic's speaker as the owner without asking (state "auto"), unless their voice is clearly someone
  else's.
A recording is a call when its file has two channels; nothing else changes for a mono one.
"""

from __future__ import annotations

import re

import numpy as np

from . import RATE

MIC, APP = 0, 1
OWNER = "A"  # the mic's speaker; pyannote's speakers on the app channel are named from B

FRAME = RATE // 100  # AEC3 takes 10 ms frames
AEC_LATENCY = 128  # samples AEC3's output trails its input at 16 kHz (8 ms, measured in the spike)


def echo_cancel(pcm: np.ndarray) -> np.ndarray:
    """A call recorded on speakers (frames × 2, int16) with the others taken out of the mic channel: AEC3 run over the
    whole call, the app channel as the far end. Echo cancelling only: no gain control, no noise suppression, no
    high-pass (the spike: others -13.2 dB, the owner alone -0.2 dB). The app channel is left as it was."""
    from livekit import rtc

    apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=False, high_pass_filter=False,
                                    auto_gain_control=False)
    mic, far = np.ascontiguousarray(pcm[:, MIC]), np.ascontiguousarray(pcm[:, APP])
    out = mic.copy()  # a last part frame shorter than 10 ms stays as recorded
    for s in range(0, len(mic) - FRAME + 1, FRAME):
        apm.process_reverse_stream(rtc.AudioFrame(far[s:s + FRAME].tobytes(), RATE, 1, FRAME))
        near = rtc.AudioFrame(mic[s:s + FRAME].tobytes(), RATE, 1, FRAME)
        apm.set_stream_delay_ms(0)  # the speakers and the mic share the Mac's clock; AEC3 finds the room's delay
        apm.process_stream(near)
        out[s:s + FRAME] = np.frombuffer(bytes(near.data), np.int16)
    out = np.concatenate([out[AEC_LATENCY:], np.zeros(AEC_LATENCY, np.int16)])
    return np.stack([out, far], axis=1)


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


ECHO_WINDOW_S = 0.25  # the speakers' echo reaches the mic 55-63 ms late; the aligner's word times wander a little more


def drop_echo(owner: list[dict], others: list[dict]) -> tuple[list[dict], list[dict]]:
    """The mic's words that are the others' words heard through the speakers: the same word on the app channel at
    the same moment. AEC3 leaves the others ~13 dB down, so a faint one can still be transcribed on the owner's side.
    Returns (kept, dropped). With headphones nothing is heard twice, so nothing is dropped."""
    heard = {}
    for w in others:
        heard.setdefault(_norm(w["w"]), []).append((w["start"] + w["end"]) / 2)
    kept, dropped = [], []
    for w in owner:
        mid = (w["start"] + w["end"]) / 2
        same = _norm(w["w"]) and any(abs(mid - m) <= ECHO_WINDOW_S for m in heard.get(_norm(w["w"]), ()))
        (dropped if same else kept).append(w)
    return kept, dropped


def owner_turns(words: list[dict], speaker: str, pause_s: float = 0.5) -> list[tuple[str, float, float]]:
    """The owner's turns from their own words on the mic: words with no pause over `pause_s` are one turn."""
    out: list[list] = []
    for w in sorted(words, key=lambda w: w["start"]):
        if out and w["start"] - out[-1][2] <= pause_s:
            out[-1][2] = max(out[-1][2], w["end"])
        else:
            out.append([speaker, w["start"], w["end"]])
    return [(s, round(a, 2), round(b, 2)) for s, a, b in out]


def louder_channel(pcm: np.ndarray, start_s: float, end_s: float) -> int:
    """Which channel carries more sound in [start_s, end_s) of a conversation's two channels: for words that came
    from the two mixed (a Hindi or mixed call is transcribed from the mix, octo/hindi.py)."""
    a, b = int(start_s * RATE), max(int(start_s * RATE) + 1, int(end_s * RATE))
    seg = pcm[a:b].astype(np.float64)
    if not len(seg):
        return APP
    return MIC if (seg[:, MIC] ** 2).mean() > (seg[:, APP] ** 2).mean() else APP


def channel(dz: dict, speaker: str) -> int | None:
    """The channel a speaker of a diarize run is heard on: the mic for the owner, the app for the others; None when
    the conversation isn't a call (its one channel)."""
    return None if not dz.get("owner") else MIC if speaker == dz["owner"] else APP


def is_call(inputs: dict) -> bool:
    """Whether a stage's conversation comes from a call's two channels."""
    from pathlib import Path

    from . import audio as A
    path = inputs.get("path")
    return bool(path) and Path(path).exists() and A.channels(Path(path)) == 2
