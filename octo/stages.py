"""Pipeline stages. Each is a pure function of its inputs (no database: what the planner reads for
them is in octo/planning.py; transcription pieces in octo/asr.py), run in a worker subprocess
(octo/worker.py), so memory used by a model is really freed when the stage ends.

Bump a stage's VERSION when its logic changes: that changes the input hash, so `octo process`
re-runs it and marks everything built on the old output stale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from . import RATE
from . import audio as A
from . import planning as P
from .asr import (language_paused, louder, other_language, qwen_words, speech_windows, top_languages, vote_language,
                  whisper_words)
from .calls import APP, MIC, OWNER, channel, drop_echo, is_call, louder_channel, owner_turns

# ---- find conversations -------------------------------------------------------------------


def group_speech(speech: list[tuple[float, float]], gap_s: float, min_speech_s: float) -> list[dict]:
    """Merge speech segments separated by less than gap_s into conversations; drop
    conversations with under min_speech_s of speech (a cough, a door, a TV blip).
    Each conversation keeps its speech spans (recording time), used later to reject
    transcript lines Whisper invents in silence."""
    out: list[dict] = []
    for start, end in sorted(speech):
        if out and start - out[-1]["end"] < gap_s:
            out[-1]["end"] = max(out[-1]["end"], end)
            out[-1]["speech_s"] += end - start
            out[-1]["speech"].append([round(start, 2), round(end, 2)])
        else:
            out.append({"start": start, "end": end, "speech_s": end - start, "speech": [[round(start, 2), round(end, 2)]]})
    return [
        {"start": round(c["start"], 2), "end": round(c["end"], 2), "speech_s": round(c["speech_s"], 1),
         "speech": c["speech"]}
        for c in out if c["speech_s"] >= min_speech_s
    ]


MAX_CONVERSATION_S = 3600.0  # an always-on evening with the TV on was one 4 h session (review 4 Oct, M26)


def cap_length(conversations: list[dict], max_s: float, min_speech_s: float) -> list[dict]:
    """Split a conversation longer than max_s at its longest pause in the second half of the hour,
    again for what's left. Transcribe, the Hindi merge and speakers each take a conversation whole,
    and the language is voted once per conversation. Not a VERSION change: only new runs see it,
    and no conversation so far is over 35 min."""
    out = []
    for c in conversations:
        while c["end"] - c["start"] > max_s:
            spans = c["speech"]
            gaps = [(b[0] - a[1], (a[1] + b[0]) / 2, i) for i, (a, b) in enumerate(zip(spans, spans[1:]))
                    if c["start"] + max_s / 2 <= (a[1] + b[0]) / 2 <= c["start"] + max_s]
            if gaps:
                _, at, i = max(gaps)
                first, rest = spans[:i + 1], spans[i + 1:]
            else:  # an hour without a pause: cut at the hour, through the span it falls in
                at = c["start"] + max_s
                first = [s for s in spans if s[0] < at]
                rest = [s for s in spans if s[1] > at]
                if first and first[-1][1] > at:
                    first[-1] = [first[-1][0], round(at, 2)]
                if rest and rest[0][0] < at:
                    rest[0] = [round(at, 2), rest[0][1]]
            a = {"start": c["start"], "end": round(first[-1][1], 2), "speech": first,
                 "speech_s": round(sum(e - s for s, e in first), 1)}
            if a["speech_s"] >= min_speech_s:
                out.append(a)
            c = {"start": round(rest[0][0], 2), "end": c["end"], "speech": rest,
                 "speech_s": round(sum(e - s for s, e in rest), 1)}
        out.append(c)
    return [c for c in out if c["speech_s"] >= min_speech_s]


def detect_speech(samples: np.ndarray, params: dict, model: Any) -> list[tuple[float, float]]:
    """Silero speech spans (seconds) in one block of audio. Also used on the recorder's chunks to
    decide where recordings can be cut (octo/device.py), so both agree on what counts as speech."""
    import torch
    from silero_vad import get_speech_timestamps

    if not len(samples):
        return []
    # Normalise loudness first. Board recordings are quiet (speech peaks ~-30 dBFS) and a fan at
    # 8 dB below speech hid a human voice from the detector entirely at raw level. Gain is capped
    # so digital silence isn't blown up into noise. High-pass first, or the mic's slow drift sets
    # the gain (see audio.highpass).
    samples = A.highpass(samples, params["highpass_hz"])
    rms = float(np.sqrt(np.mean(samples ** 2))) + 1e-9
    samples = np.clip(samples * min(10 ** (params["max_gain_db"] / 20), 10 ** (params["normalize_dbfs"] / 20) / rms), -1, 1)
    return [(seg["start"], seg["end"]) for seg in get_speech_timestamps(
        torch.from_numpy(samples), model, sampling_rate=RATE, threshold=params["threshold"], return_seconds=True)]


def apply_splits(conversations: list[dict], splits: list[float], min_speech_s: float) -> list[dict]:
    """find_conversations' output, cut at the owner's split points (recording time; octo/split.py)."""
    out = []
    for c in conversations:
        cuts = [s for s in sorted(splits) if c["start"] < s < c["end"]]
        edges = [c["start"], *cuts, c["end"]]
        for a, b in zip(edges, edges[1:]):
            speech = [[max(x, a), min(y, b)] for x, y in c["speech"] if min(y, b) > max(x, a)]
            speech_s = round(sum(y - x for x, y in speech), 1)
            if cuts and speech_s < min_speech_s:
                continue
            out.append({**c, "start": round(a, 2), "end": round(b, 2), "speech": speech, "speech_s": speech_s})
    return out


def find_conversations(inputs: dict, params: dict) -> dict:
    """Voice-activity detection over the whole recording, in 10-minute blocks so a day-long
    recording never has to fit in memory at once."""
    from silero_vad import load_silero_vad

    model = load_silero_vad()
    path, total = inputs["path"], inputs["duration_s"]
    block = 600.0
    speech: list[tuple[float, float]] = []
    t = 0.0
    while t < total:
        speech += [(t + a, t + b) for a, b in detect_speech(A.read(path, t, min(total, t + block)), params, model)]
        t += block
    conversations = cap_length(group_speech(speech, params["gap_s"], params["min_speech_s"]),
                               params.get("max_conversation_s", MAX_CONVERSATION_S), params["min_speech_s"])
    pad = params["pad_s"]
    for c in conversations:  # a little context either side
        c["start"] = round(max(0.0, c["start"] - pad), 2)
        c["end"] = round(min(total, c["end"] + pad), 2)
    if params.get("splits"):  # The owner's "two discussions here" (octo/split.py)
        conversations = apply_splits(conversations, params["splits"], params["min_speech_s"])
    return {"speech_s": round(sum(e - s for s, e in speech), 1), "conversations": conversations}


# ---- transcribe ---------------------------------------------------------------------------


def transcribe(inputs: dict, params: dict) -> dict:
    """Language from Whisper large-v3, words from the chosen model: Qwen3-ASR-1.7B (v4, 2 Oct 2026) or Whisper.

    Language (v3, 1 Oct 2026): Whisper on its own guesses once, from the first 30 s. Recorder
    conversations often start with near-silence, and it guessed Norwegian for an English meeting
    (en 0.38 vs nn 0.31) and English for a conversation mostly in Hindi. Now the guess comes from
    the most speech-heavy windows, louder, and only among the owner's languages.

    Words (v4): Qwen3-ASR (MLX), with Qwen3-ForcedAligner for word timings. The owner's blind test on
    10 stretches of the 19:58 meetings: Qwen 7, Whisper 3; where Qwen won, Whisper had dropped
    about a tenth of the words. Only English goes through (TRANSCRIBE_LANGUAGES): on Hindi neither
    was usable, so paused conversations get the language and nothing else.

    Hindi and mixed (4 Oct): when the planner passes `hindi` (octo/planning.py, only for a
    conversation already heard as Hindi or mixed), Whisper and Qwen both write a Hindi draft and
    Claude merges the two from their text (octo/hindi.py). English conversations never get the
    parameter, so their runs are untouched. A new Hindi conversation runs twice: once to hear the
    language (paused, seconds), then with `hindi`."""
    import gc

    import mlx.core as mx
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.transcribe import ModelHolder

    # Speech the detector heard in this conversation, in session time.
    conv = next((c for c in inputs["upstream"][0]["conversations"] if abs(c["start"] - inputs["start_s"]) < 0.01), None)
    spans = [[a - inputs["start_s"], b - inputs["start_s"]] for a, b in (conv or {}).get("speech", [])]
    samples = louder(A.read(inputs["path"], inputs["start_s"], inputs["end_s"]), spans, params)

    language, votes = params["language"], None
    if language is None:
        model = ModelHolder.get_model(params["language_model"], mx.float16)
        probs = []
        for start in speech_windows(spans) or [0.0]:
            mel = log_mel_spectrogram(mx.array(samples[int(start * RATE):int((start + 30) * RATE)]),
                                      n_mels=model.dims.n_mels, padding=N_SAMPLES)
            probs.append(model.detect_language(pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16))[1])
        language, votes = vote_language(probs, params["languages"])
    out = {"language": language, "language_votes": votes, "model": params["model"], "segments": [],
           "rejected_as_silence": []}
    if votes is not None:
        out["language_windows"] = top_languages(probs)
    paused = language_paused(out)
    if paused and params.get("hindi") and other_language(out) == "hi":
        return {**out, **hindi(samples, spans, params, inputs.get("progress", lambda m: None)), "mixed": paused}
    if paused:
        return {**out, "paused": paused}
    if "whisper" in params["model"]:
        words = whisper_words
    else:
        ModelHolder.model = ModelHolder.model_path = None  # free Whisper before Qwen loads (16 GB Mac)
        gc.collect()
        mx.clear_cache()
        words = qwen_words
    if not is_call(inputs):
        segments, rejected = words(samples, spans, language, params)
        return {**out, "segments": segments, "rejected_as_silence": rejected}
    # A call (B19): each channel on its own, the mic's words the owner's and the app's the others', merged by time.
    segments, rejected = [], []
    for ch in (MIC, APP):
        seg, rej = words(louder(A.read(inputs["path"], inputs["start_s"], inputs["end_s"], ch), spans, params),
                         spans, language, params)
        for x in seg + rej:
            x["ch"] = ch
            x["words"] = [{**w, "ch": ch} for w in x.get("words", [])]
        segments += seg
        rejected += rej
    segments.sort(key=lambda x: x["start"])
    for i, x in enumerate(segments):
        x["id"] = i
    return {**out, "segments": segments, "rejected_as_silence": sorted(rejected, key=lambda x: x["start"])}


def hindi(samples: np.ndarray, spans: list[list[float]], params: dict, progress: Callable[[str], None]) -> dict:
    """Two local drafts told "Hindi", then Claude's merge on Qwen's timings (octo/hindi.py)."""
    import gc

    import mlx.core as mx
    from mlx_whisper.transcribe import ModelHolder

    from . import hindi as H
    from . import providers as PV
    from .claude_code import ask as cc_ask  # not the frozen review file (K5, M30)

    h = params["hindi"]

    def ask(model, effort, system, prompt, schema, timeout):
        if h.get("reader"):  # the owner's own model (product plan 4.1); the first lab: Claude Code as before
            return PV.ask(h["reader"], system, prompt, schema, timeout=timeout)
        return cc_ask(model, effort, system, prompt, schema=schema, timeout=timeout)
    progress("Hindi draft 1 of 2: Whisper")
    whisper, _ = whisper_words(samples, spans, "hi", {**params, "model": params["language_model"]})
    ModelHolder.model = ModelHolder.model_path = None  # free Whisper before Qwen loads (16 GB Mac)
    gc.collect()
    mx.clear_cache()
    progress("Hindi draft 2 of 2: Qwen")
    qwen, rejected = qwen_words(samples, spans, "hi", {**params, "model": h["base_model"]})
    usage: list[dict] = []

    def ask_merge(model, effort, system, prompt, schema):
        answer, u = ask(model, effort, system, prompt, schema=schema, timeout=600)
        usage.append(u or {})
        return answer

    segments, merged = H.merge(qwen, whisper, h, ask_merge, progress)
    # What the merge cost (review 4 Oct, M09): it was thrown away, so nothing showed where the Pro plan went.
    merged["usage"] = {k: round(sum(u.get(k) or 0 for u in usage), 4)
                       for k in ("cost_usd_equivalent", "duration_ms", "input_tokens", "output_tokens")}
    return {"segments": segments, "rejected_as_silence": rejected, "merged": merged,
            "drafts": {"whisper": whisper, "qwen": qwen}}


# ---- diarize: who spoke when, and which words are whose -------------------------------------


def assign_words(words: list[dict], turns: list[tuple[str, float, float]], max_gap_s: float = 0.5) -> list[dict]:
    """Give each word the speaker whose (non-overlapping) turn covers most of it. Words that fall
    in a small gap between turns go to the nearest turn; farther than max_gap_s, speaker unknown.
    Uses Whisper's own word timings -- no wav2vec alignment (no Kannada model, breaks on
    code-switching; plan §4)."""
    out = []
    for w in words:
        best, best_ov = None, 0.0
        for spk, s, e in turns:
            ov = min(w["end"], e) - max(w["start"], s)
            if ov > best_ov:
                best, best_ov = spk, ov
        sure = best is not None and best_ov >= 0.5 * max(0.01, w["end"] - w["start"])
        mid = (w["start"] + w["end"]) / 2
        if best is None:
            # The aligner gives some words no length (237 of 4840 in one conversation, 2 Oct): they
            # overlap nothing, but one inside a turn is that speaker's. Before this they were measured
            # from the turn's edges and most of them, deep inside long turns, got no speaker.
            best = next((spk for spk, s, e in turns if s <= mid <= e), None)
            sure = best is not None
        if best is None:
            dist, near = min(((min(abs(mid - s), abs(mid - e)), spk) for spk, s, e in turns), default=(None, None))
            if dist is not None and dist <= max_gap_s:
                best = near
        out.append({**w, "speaker": best, "sure": sure})
    return out


def fill_by_voice(words: list[dict], centroids: dict[str, np.ndarray], embed: Callable[[float, float], np.ndarray],
                  params: dict) -> tuple[list[dict], list[tuple[str, float, float]]]:
    """Stretches of words pyannote gave no speaker go to the speaker whose voice they match.

    pyannote misses whole stretches of clear speech: in one 32-minute conversation (2 Oct), 37 lines
    of up to 13 s, Silero-confirmed and at conversation loudness, sat outside every turn.
    A stretch is consecutive speaker-less words with no pause over `pause_s`; it's matched (same
    voice model as voiceprints) against each speaker's own turns, and given to the best one only when
    it is clearly that one: similarity >= fill_min_similarity and fill_min_margin ahead of the next.
    Measured on held-out lines pyannote did assign, in 4 conversations (885b8c34, 2ae17b6f, 5272c938,
    e4889f3d; 2-10 speakers): those two bars (0.5, 0.10) agreed with pyannote on all ~550 they took,
    74-100% of lines; margin alone was 96-98%. Below the bars a stretch stays unassigned.
    Returns the words, and new turns (speaker, start, end) for the filled stretches."""
    stretches: list[list[int]] = []
    for i, w in enumerate(words):
        if w["speaker"] is not None:
            continue
        if stretches and stretches[-1][-1] == i - 1 and w["start"] - words[i - 1]["end"] <= params["fill_pause_s"]:
            stretches[-1].append(i)
        else:
            stretches.append([i])
    out = [dict(w) for w in words]
    added: list[tuple[str, float, float]] = []
    for idx in stretches:
        start, end = words[idx[0]]["start"], words[idx[-1]]["end"]
        if end - start < params["fill_min_s"] or not centroids:
            continue
        e = embed(start, end)
        ranked = sorted(((float(np.dot(e, c) / (np.linalg.norm(e) * np.linalg.norm(c) + 1e-9)), spk)
                         for spk, c in centroids.items()), reverse=True)
        sim, spk = ranked[0]
        margin = sim - (ranked[1][0] if len(ranked) > 1 else 0.0)
        if sim < params["fill_min_similarity"] or margin < params["fill_min_margin"]:
            continue
        for i in idx:
            out[i].update(speaker=spk, sure=True, by_voice=True)
        added.append((spk, round(start, 2), round(end, 2)))
    return out, added


def _uncovered(spans: list[tuple[str, float, float]], turns: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """Parts of each span that no turn covers, so turns stay one speaker at a time."""
    out = []
    for spk, s, e in spans:
        cur = s
        for _, ts, te in sorted(turns, key=lambda t: t[1]):
            if te <= cur or ts >= e:
                continue
            if ts > cur:
                out.append((spk, round(cur, 2), round(ts, 2)))
            cur = max(cur, te)
        if cur < e:
            out.append((spk, round(cur, 2), round(e, 2)))
    return [t for t in out if t[2] - t[1] > 0.01]


def utterances(words: list[dict], pause_s: float = 1.0, long_words: int = 30) -> list[dict]:
    """Consecutive words by the same speaker become one line. A line also ends at a pause, or at
    the end of a sentence once it's long: a 6-minute monologue was one 816-word line, unreadable
    and too coarse for insight quotes to cite."""
    lines: list[dict] = []
    for w in words:
        prev = lines[-1] if lines else None
        breaks = prev is not None and (
            w["start"] - prev["end"] > pause_s
            or (len(prev["words"]) >= long_words and prev["words"][-1]["w"].endswith((".", "?", "!"))))
        if prev and prev["speaker"] == w["speaker"] and not breaks:
            line = lines[-1]
            line["end"] = w["end"]
            line["words"].append(w)
        else:
            lines.append({"speaker": w["speaker"], "start": w["start"], "end": w["end"], "words": [w]})
    for i, line in enumerate(lines):
        ws = line.pop("words")
        line["id"] = i
        line["text"] = " ".join(w["w"] for w in ws).strip()
        # Flag lines where speaker or words are shaky, so insights don't lean on them.
        if any(w.get("by_voice") for w in ws):
            line["by_voice"] = True  # speaker from fill_by_voice, not pyannote
        line["uncertain"] = sum(not w["sure"] for w in ws) > len(ws) / 3 or min((w["p"] for w in ws if w.get("p") is not None), default=1.0) < 0.3
        line["start"], line["end"] = round(line["start"], 2), round(line["end"], 2)
    return lines


def speakers(inputs: dict, params: dict) -> dict:
    """Who spoke when: pyannote on the GPU (MPS), at 3.1's default clustering threshold (0.705).
    Audio only, so a new transcription model doesn't redo it (split from diarize, 2 Oct).

    History: 0.5 was chosen on 2026-09-30 from synthetic Mac voices (the default merged two similar
    male voices, 82% right; 0.5 got 99.9%). Real speech on 2026-10-01 reversed it: at 0.5 the owner reading
    alone came out as 3 speakers, and a 40 s phone clip of the owner + one other person as 5; at 0.705 those were 1 and 2
    (other 25 s, the owner 10 s), and 10 min / 6 min solo memos stayed 1 at every setting. Similar real
    voices are still untested -- if two family members merge, that's the case to re-measure.

    2026-10-02, on the owner's 7 confirmed conversations (`python -m bench.diarcompare`): community-1 merged
    two people the owner had confirmed as different (a couple, in a family conversation) at its default and at 0.5/0.4;
    Nemotron 3 Diarization merged two colleagues and caps at 8 speakers. 3.1 stays: it over-splits
    rather than merges, and an extra speaker is one Merge click to fix."""
    import torch
    from pyannote.audio import Pipeline

    call = is_call(inputs)  # B19: only the others' channel; the mic is the owner (OWNER), named before them
    samples = A.read(inputs["path"], inputs["start_s"], inputs["end_s"], APP if call else None)
    pipe = Pipeline.from_pretrained(params["model"])
    pipe.to(torch.device("mps" if torch.backends.mps.is_available() else "cpu"))
    if params.get("clustering_threshold") is not None:  # None: the model's own (community-1 clusters differently)
        base = pipe.parameters(instantiated=True)
        pipe.instantiate({**base, "clustering": {**base["clustering"], "threshold": params["clustering_threshold"]}})
    out = pipe({"waveform": torch.from_numpy(samples).unsqueeze(0), "sample_rate": RATE})
    ann = getattr(out, "exclusive_speaker_diarization", None) or out.speaker_diarization
    # The full diarization keeps overlapping speech (two people at once), which the exclusive one
    # drops; interruptions are counted from it.
    full = out.speaker_diarization if hasattr(out, "speaker_diarization") else out

    # Speakers named A, B, C… in order of first appearance.
    raw = sorted(((seg.start, seg.end, spk) for seg, _, spk in ann.itertracks(yield_label=True)))
    names: dict[str, str] = {}
    for _, _, spk in raw:
        names.setdefault(spk, chr(ord("B" if call else "A") + len(names)))
    return {"model": params["model"], "clustering_threshold": params["clustering_threshold"],
            **({"owner": OWNER} if call else {}),
            "speakers": [OWNER] * call + list(names.values()),
            "turns": [{"speaker": names[spk], "start": round(s, 2), "end": round(e, 2)} for s, e, spk in raw],
            "overlapping_turns": [{"speaker": names[spk], "start": round(seg.start, 2), "end": round(seg.end, 2)}
                                  for seg, _, spk in sorted(full.itertracks(yield_label=True), key=lambda t: t[0].start)
                                  if spk in names]}


def diarize(inputs: dict, params: dict, embedder: Callable[[], Any] | None = None) -> dict:
    """Who said what: each transcribed word goes to the speaker heard at that moment (`speakers`);
    stretches of words pyannote left without anyone go to the speaker whose voice they clearly
    match (fill_by_voice, v5). Then words become lines. The output is what every later step and the
    conversation view read, with the owner's corrections applied on top (octo/corrections.py)."""
    tr, sp = inputs["upstream"][0], inputs["upstream"][1]
    if sp.get("owner"):
        return _diarize_call(inputs, params, embedder)
    turns = [(t["speaker"], t["start"], t["end"]) for t in sp["turns"]]
    overlapping = list(sp["overlapping_turns"])
    back = readmitted(tr.get("rejected_as_silence") or [], turns, params.get("readmit_overlap", 0.3))
    words = assign_words(sorted([w for seg in tr["segments"] for w in seg["words"]] + back, key=lambda w: w["start"]), turns)
    words, turns, overlapping = _fill(inputs, params, embedder, words, turns, overlapping, sp["speakers"])
    lines = utterances(words)
    talk = {n: round(sum(e - s for spk, s, e in turns if spk == n), 1) for n in sp["speakers"]}
    return {"model": sp["model"], "clustering_threshold": sp["clustering_threshold"],
            "speakers": sorted(sp["speakers"]), "talk_s": talk,
            "turns": [{"speaker": a, "start": s, "end": e} for a, s, e in turns],
            "overlapping_turns": overlapping, "lines": lines}


def _fill(inputs: dict, params: dict, embedder, words: list[dict], turns: list, overlapping: list, speakers: list[str],
          channel: int | None = None) -> tuple[list[dict], list, list]:
    """fill_by_voice on the conversation's audio (or one channel of a call), with the turns it adds."""
    if not (any(w["speaker"] is None for w in words) and speakers):
        return words, turns, overlapping
    if embedder is None:
        from .voice import DIARIZE_EMBEDDING, Embedder
        emb = Embedder(DIARIZE_EMBEDDING)  # fill_min_similarity was set on ResNet34; voiceprints moved on
    else:
        emb = embedder()
    samples = A.read(inputs["path"], inputs["start_s"], inputs["end_s"], channel)
    centroids = {spk: got[0] for spk in speakers
                 if (got := emb.embed_turns(samples, [(s, e) for n, s, e in turns if n == spk]))}
    words, filled = fill_by_voice(words, centroids, lambda s, e: emb.embed(samples[int(s * RATE):int(e * RATE)]), params)
    extra = _uncovered(filled, turns)
    turns = sorted(turns + extra, key=lambda t: t[1])
    overlapping = sorted(overlapping + [{"speaker": a, "start": s, "end": e} for a, s, e in extra],
                         key=lambda t: t["start"])
    return words, turns, overlapping


def _diarize_call(inputs: dict, params: dict, embedder) -> dict:
    """A call (B19): the mic's words are the owner's, minus those that are the others' words heard through the
    speakers (calls.drop_echo); the app's words go to pyannote's speakers on the app channel, as in a conversation.
    Words from the two mixed (a Hindi or mixed call) go to the louder channel at that moment."""
    tr, sp = inputs["upstream"][0], inputs["upstream"][1]
    owner = sp["owner"]
    others = [s for s in sp["speakers"] if s != owner]
    turns = [(t["speaker"], t["start"], t["end"]) for t in sp["turns"]]
    words = [w for seg in tr["segments"] for w in seg["words"]]
    if any("ch" not in w for w in words):
        pcm = A.read_pcm(inputs["path"], inputs["start_s"], inputs["end_s"], "all")
        words = [w if "ch" in w else {**w, "ch": louder_channel(pcm, w["start"], w["end"])} for w in words]
    theirs = [w for w in words if w["ch"] == APP] + readmitted(
        [r for r in tr.get("rejected_as_silence") or [] if r.get("ch") == APP], turns, params.get("readmit_overlap", 0.3))
    mine, echo = drop_echo([w for w in words if w["ch"] == MIC], theirs)
    theirs = assign_words(sorted(theirs, key=lambda w: w["start"]), turns)
    theirs, turns, overlapping = _fill(inputs, params, embedder, theirs, turns, list(sp["overlapping_turns"]), others, APP)
    mine = [{**w, "speaker": owner, "sure": True} for w in mine]
    own = owner_turns(mine, owner)
    turns = sorted(turns + own, key=lambda t: t[1])
    overlapping = sorted(overlapping + [{"speaker": a, "start": s, "end": e} for a, s, e in own], key=lambda t: t["start"])
    lines = utterances(sorted(mine + theirs, key=lambda w: w["start"]))
    talk = {n: round(sum(e - s for spk, s, e in turns if spk == n), 1) for n in sp["speakers"]}
    return {"model": sp["model"], "clustering_threshold": sp["clustering_threshold"], "owner": owner,
            "speakers": sorted(sp["speakers"]), "talk_s": talk,
            "turns": [{"speaker": a, "start": s, "end": e} for a, s, e in turns],
            "overlapping_turns": overlapping, "lines": lines, "echo_dropped": len(echo)}


# ---- numbers: neutral counts, no judgement ---------------------------------------------------

def numbers(inputs: dict, params: dict) -> dict:
    return count(inputs["upstream"][0], params)


# Hesitation words and verbal cushions. "like" and "I mean" are left out (too often they carry
# meaning), and so is "uh huh" (a backchannel, not a hesitation).
FILLERS = re.compile(r"\b(?:um+|uh+(?![\s,-]*huh)|ah+|er+|hmm+|you know|kind of|sort of)\b", re.I)


def count(dz: dict, params: dict) -> dict:
    """Talk time, turns, questions, interruptions and filler words per speaker, from the diarize output.

    Deterministic and neutral (plan §3.7): no good/bad. Two are approximate and say so:
    - questions come from Whisper's punctuation, which is patchy on casual speech;
    - interruptions: X starts while Y is talking (overlapping by ≥ min_overlap_s) and Y stops before
      X does. If Y keeps going it's a backchannel ("mm", "okay") or talking over, not a takeover.
      Turn edges are ±300 ms, so short overlaps are ignored.
    - fillers ("um", "ah", "you know", "kind of"…): only what the transcription kept, and it may drop
      some. The same model transcribes everyone, so comparing speakers is fairer than the raw count.
    """
    speakers = dz["speakers"]
    # Consecutive exclusive turns by one speaker = one time holding the floor: [speaker, speech seconds]
    # (speech only, so a long turn's pauses don't count as talking).
    floors: list[list] = []
    for t in sorted(dz["turns"], key=lambda t: t["start"]):
        if floors and floors[-1][0] == t["speaker"]:
            floors[-1][1] += t["end"] - t["start"]
        else:
            floors.append([t["speaker"], t["end"] - t["start"]])
    ov = dz.get("overlapping_turns") or []
    made = {s: 0 for s in speakers}
    received = {s: 0 for s in speakers}
    for x in ov:
        for y in ov:
            if x["speaker"] == y["speaker"]:
                continue
            if (y["start"] < x["start"] and y["end"] - x["start"] >= params["min_overlap_s"]
                    and y["end"] < x["end"]):
                made[x["speaker"]] += 1
                received[y["speaker"]] += 1
                break  # one interruption per turn, however many people were talking
    total = sum(dz["talk_s"].values()) or 1.0
    out = {}
    for spk in speakers:
        mine = [f for f in floors if f[0] == spk]
        text = " ".join(line["text"] for line in dz["lines"] if line["speaker"] == spk)
        words = len(text.split())
        fillers = len(FILLERS.findall(text))
        out[spk] = {
            "talk_s": dz["talk_s"].get(spk, 0.0),
            "share": round(dz["talk_s"].get(spk, 0.0) / total, 3),
            "turns": len(mine),
            "longest_turn_s": round(max((f[1] for f in mine), default=0.0), 1),
            "words": words,
            "questions": text.count("?"),
            "fillers": fillers,
            "fillers_per_100_words": round(100 * fillers / words, 1) if words else 0.0,
            "interrupted_others": made[spk],
            "was_interrupted": received[spk],
        }
    return {"speakers": out, "speaker_changes": max(0, len(floors) - 1),
            "overlap_counted": bool(ov), "approx": ["questions", "interruptions", "fillers"]}


# ---- identify: who each speaker is ----------------------------------------------------------


def identify(inputs: dict, params: dict) -> dict:
    """Match each diarized speaker against stored voiceprints. Embeddings of unmatched speakers
    exist only in this process and are never written anywhere (D1)."""
    from .voice import Embedder, jay_presence, match, similarities, split_by_voice

    dz = inputs["upstream"][0]
    prints = inputs["voiceprints"]
    embedder = Embedder() if prints else None
    owner = dz.get("owner")  # a call (B19): the owner's voice on the mic, the others' on the app's channel
    heard = {ch: A.read(inputs["path"], inputs["start_s"], inputs["end_s"], ch) for ch in {channel(dz, s) for s in dz["speakers"]}} \
        if prints else {}
    samples_of = lambda spk: heard[channel(dz, spk)]
    turns_of = {spk: [(t["start"], t["end"]) for t in dz["turns"] if t["speaker"] == spk] for spk in dz["speakers"]}
    speakers = []
    for spk in dz["speakers"]:
        m, self_sim = None, None
        if embedder is not None:
            got = embedder.embed_turns(samples_of(spk), turns_of[spk])
            if got:
                m = match(got[0], prints)
                self_sim = next((r["similarity"] for r in similarities(got[0], prints) if r["is_self"]), None)
        speakers.append({"speaker": spk, "speech_s": round(sum(e - s for s, e in turns_of[spk]), 1),
                         "match": m, "self_similarity": self_sim, **({"owner_channel": True} if spk == owner else {})})

    # One known person heard as two or more speakers (people plan 4.3): merged into the one with most
    # talk. On real conversations the owner had confirmed, 6 such merges, all the same person, none wrong (13 Oct).
    merges, by_person = [], {}
    for sp in speakers:
        if sp["match"] and not sp["match"]["ambiguous"]:
            by_person.setdefault(sp["match"]["person_id"], []).append(sp)
    for pid, group in by_person.items():
        into = max(group, key=lambda x: x["speech_s"])["speaker"]
        merges += [{"from": x["speaker"], "into": into, "person_id": pid} for x in group if x["speaker"] != into]
    merged_into = {mg["from"]: mg["into"] for mg in merges}

    # Two or more known people heard as one speaker (people plan 4.2), after those merges: each part
    # named by its own voice.
    if embedder is not None:
        for sp in speakers:
            if sp["speaker"] in merged_into or sp["speaker"] == owner:  # the mic is one person
                continue
            turns = sorted(t for x in [sp["speaker"], *[f for f, i in merged_into.items() if i == sp["speaker"]]]
                           for t in turns_of[x])
            split = split_by_voice(embedder, samples_of(sp["speaker"]), turns, prints)
            for part in split or []:
                part_turns = [(max(a, s), min(b, e)) for a, b in part["spans"] for s, e in turns if min(b, e) > max(a, s)]
                got = embedder.embed_turns(samples_of(sp["speaker"]), part_turns)
                part["match"] = match(got[0], prints) if got else None
            if split:
                sp["split"] = split

    # D10, with a deliberately low bar for "present" and a high one for "absent".
    sims = [s["self_similarity"] for s in speakers if s["self_similarity"] is not None]
    me = jay_presence(sims, any(p["is_self"] for p in prints))
    if owner and next(s["speech_s"] for s in speakers if s["speaker"] == owner) > 0:
        me = True  # a call: the owner is the one on the mic
    return {"speakers": speakers, "merges": merges, "diarize_run": inputs["upstream_ids"][0], "jay_present": me,
            "jay_best_similarity": max(sims) if sims else None}


def readmitted(rejected: list[dict], turns: list[tuple[str, float, float]], at_least: float) -> list[dict]:
    """Words of segments the silence filter dropped (transcribe) that a speaker's turn covers by at
    least `at_least` of their length: the speech detector missed them, pyannote heard someone
    (review 4 Oct, M17: the first 108 s of 2ae17b6f). Given back with a low p, so their line shows
    ⚠ and the review doesn't lean on it. Segments stored before 5 Oct have no words: nothing back."""
    out = []
    for r in rejected:
        a, b = r.get("start"), r.get("end")
        if not r.get("words") or a is None or b is None or b <= a:
            continue
        covered = sum(max(0.0, min(b, e) - max(a, s)) for _, s, e in turns)
        if covered >= at_least * (b - a):
            out += [{**w, "p": min(w.get("p", 1.0), 0.2)} for w in r["words"]]
    return out


def dropped(tr: dict, turns: list[dict], at_least: float = 0.3) -> list[dict]:
    """What the page lists as dropped as silence: every rejected segment not given back."""
    t = [(x["speaker"], x["start"], x["end"]) for x in turns]
    back = {id(r) for r in tr.get("rejected_as_silence") or [] if readmitted([r], t, at_least)}
    return [{"start": r["start"], "text": r["text"]} for r in tr.get("rejected_as_silence") or [] if id(r) not in back]


def remap_speakers(old_turns: list[dict], new_turns: list[dict]) -> dict[str, str]:
    """Old speaker letter → new letter by greatest shared time. Used to carry the owner's confirmed
    labels across a diarize re-run -- by timing, never by voice (architecture review)."""
    shared: dict[tuple[str, str], float] = {}
    for o in old_turns:
        for n in new_turns:
            ov = min(o["end"], n["end"]) - max(o["start"], n["start"])
            if ov > 0:
                shared[(o["speaker"], n["speaker"])] = shared.get((o["speaker"], n["speaker"]), 0.0) + ov
    # A letter carries only where most of its talk in the new run's span went to that one new
    # letter; a thin overlap is a guess, and a wrong carried name is worse than asking (M32).
    lo, hi = min((n["start"] for n in new_turns), default=0.0), max((n["end"] for n in new_turns), default=0.0)
    talk: dict[str, float] = {}
    for o in old_turns:
        talk[o["speaker"]] = talk.get(o["speaker"], 0.0) + max(0.0, min(o["end"], hi) - max(o["start"], lo))
    out, used = {}, set()
    for (o, n), t in sorted(shared.items(), key=lambda kv: -kv[1]):
        if o not in out and n not in used and t >= 0.5 * talk.get(o, 0.0):
            out[o] = n
            used.add(n)
    return out


# ---- registry -----------------------------------------------------------------------------


def insight(inputs: dict, params: dict) -> dict:
    from . import insight as I
    names = {s["speaker"]: s["name"] for s in params["speakers"]}
    me = {s["speaker"] for s in params["speakers"] if s["self"]}
    params = {**params, "started_at": inputs.get("started_at"), "owner": params.get("owner") or inputs.get("owner") or {}}
    from .rubric import text as rubric_text  # the owner's file (octo/rubric.py): the owner's pipeline/rubric.md as before
    progress = inputs.get("progress", lambda m: print(m, flush=True))
    if params.get("reader"):  # on one of the owner's connections (octo/review_any.py, product plan 4.1)
        from . import review_any as RA
        return RA.run(inputs["upstream"][0], names, me, params, rubric_text(), on_progress=progress,
                      memo=inputs.get("memo"))
    return I.run(inputs["upstream"][0], names, me, params, rubric_text=rubric_text(), on_progress=progress,
                 memo=inputs.get("memo"))


def commitments(inputs: dict, params: dict) -> dict:
    from . import commitments as CM
    return CM.run(inputs["upstream"][0], params, started_at=inputs.get("started_at"), memo=inputs.get("memo"),
                  on_progress=inputs.get("progress", lambda m: print(m, flush=True)))


@dataclass(frozen=True)
class Stage:
    name: str
    version: int
    level: str  # "recording" or "session"
    # The stage(s) whose current output this builds on, in the order the stage function receives
    # them as inputs["upstream"].
    after: str | tuple[str, ...] | None
    params: dict
    fn: Callable[[dict, dict], dict]
    # Extra inputs read from the database at planning time, folded into the input hash
    # (e.g. the voiceprint set: enrolling someone re-runs identify everywhere). (conn, target).
    dynamic: Callable[[Any, str], dict] | None = None
    # Whether the stage may run yet for this target (conn, target); e.g. insights wait for the owner to
    # confirm who's who, so nothing is said about the wrong person.
    ready: Callable[[Any, str], bool] | None = None

    @property
    def upstreams(self) -> tuple[str, ...]:
        return () if self.after is None else (self.after,) if isinstance(self.after, str) else tuple(self.after)


STAGES: dict[str, Stage] = {s.name: s for s in [
    # threshold 0.1 on normalised audio: found 6.1 of ~7 s of the owner's speech over a fan (0.5 found
    # 0.7 s) with zero false detections on fan-only and room noise (2026-09-30). Missing a real
    # conversation is worse than transcribing a little noise, which Whisper then flags.
    Stage("find_conversations", 4, "recording", None,
          {"highpass_hz": 80.0, "threshold": 0.1, "normalize_dbfs": -20.0, "max_gain_db": 40.0,
           "gap_s": 180.0, "min_speech_s": 2.0, "pad_s": 1.0}, find_conversations, dynamic=P.splits_input),
    # languages: the ones spoken around the owner -- never Norwegian. Kannada and Gujarati are weak in Whisper.
    Stage("transcribe", 4, "session", "find_conversations",
          {"model": "Qwen/Qwen3-ASR-1.7B", "language_model": "mlx-community/whisper-large-v3-mlx",
           "language": None, "languages": ["en", "hi", "kn", "gu"],
           "highpass_hz": 80.0, "normalize_dbfs": -20.0, "max_gain_db": 40.0, "min_speech_overlap": 0.3,
           "segment_gap_s": 1.0},
          transcribe, dynamic=P.hindi_input),
    # Split out of diarize on 2 Oct: pyannote needs only the audio.
    Stage("speakers", 1, "session", "find_conversations",
          {"model": "pyannote/speaker-diarization-3.1", "clustering_threshold": 0.705}, speakers),
    # v5 (2 Oct): zero-length words inside a turn get its speaker; stretches pyannote missed are
    # given by voice (fill_by_voice). v6: speaker turns come from `speakers`.
    Stage("diarize", 6, "session", ("transcribe", "speakers"),
          {"fill_min_s": 1.0, "fill_pause_s": 1.0, "fill_min_similarity": 0.5, "fill_min_margin": 0.10}, diarize),
    Stage("identify", 3, "session", "diarize", {}, identify, dynamic=P.voiceprints_input),
    # v3 (2 Oct): filler words per speaker.
    Stage("numbers", 3, "session", "diarize", {"min_overlap_s": 0.3}, numbers, dynamic=P.corrections_input),
    # Claude Opus 5.5 through Claude Code since 2 Oct: the local models timed out on every
    # conversation over ~25 min and couldn't read a conversation the way he wants. qwen3:8b and
    # gemma3:12b stay as local choices on the Pipeline page (octo/settings.py).
    # v3 (6 Oct): the review gives "the one thing" and held / partly / missed per principle (the owner, E2).
    # Reviews the owner has answered, rated or sent a step from are pinned and stay; the rest are made
    # again with the new prompt (E5, docs/coach-plan.md).
    Stage("insight", 3, "session", "diarize",
          {"model": "claude-opus-5-5", "effort": "high", "min_jay_speech_s": 20.0, "max_ctx": 32768}, insight,
          dynamic=P.insight_input, ready=P.insight_ready),
    # B14 (19 Oct): commitments both ways, a step of its own after the review so the review stays exactly as it
    # was. Built on diarize, not on the review: a review made again doesn't look for them again.
    Stage("commitments", 1, "session", "diarize", {"model": "claude-opus-5-5", "effort": "high"}, commitments,
          dynamic=P.commitments_input, ready=P.commitments_ready),
]}

# Pipeline order is the registry's order (dicts keep insertion order): one source of truth.
# Every stage comes after the stages it builds on.


def downstream(name: str) -> list[str]:
    """Every stage that builds on `name`, directly or not, in pipeline order."""
    out: list[str] = []
    for other, st in STAGES.items():
        if any(u == name or u in out for u in st.upstreams):
            out.append(other)
    return out


def params_for(stage: Stage, overrides: dict[str, Any] | None = None) -> dict:
    return {**stage.params, **(overrides or {})}
