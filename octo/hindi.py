"""Hindi and mixed Hindi-English conversations: two local drafts, merged by Claude from their text.

No local model was usable on Hindi alone (2 Oct, 9 of 9 stretches "none usable"). On 4 Oct, on the
same 9 stretches, Claude Opus 5.5 merging Whisper large-v3 and Qwen3-ASR (both told "Hindi") won
the owner's blind test 6 of 9 (Qwen-hi 3, Whisper-hi 0), with no stretch where they heard words that weren't
said (docs/learnings.md, Transcription). Claude can't hear: it reads only the two transcripts. Audio
stays on the Mac; transcript text leaving it for Claude is the owner's rule of 2 Oct.

Qwen's draft is the base because it has word timings (its aligner), which `diarize` needs. Claude
rewrites each Qwen segment, and its words are put back on Qwen's timings (`align`).
"""

from __future__ import annotations

import difflib
import unicodedata
from typing import Callable

# What the planner folds into the transcribe input hash for a Hindi or mixed conversation
# (octo/planning.py, hindi_input). Changing it re-runs those conversations only.
HINDI = {"base_model": "Qwen/Qwen3-ASR-1.7B", "merge_model": "claude-opus-5-5", "merge_effort": "high", "context_s": 90.0, "chunk_s": 40.0,
         "unsure_p": 0.4}
UNCLEAR = "[unclear]"

SYSTEM = """You turn two automatic transcripts of the same recording into the best reading of what was said.

The recording is a family or work conversation in India, mostly Hindi with English words and
sentences mixed in, or English with Hindi mixed in. It was often recorded across a room, so both
transcripts are poor: words misheard as similar-sounding words, words dropped, and nonsense in noisy
places. You cannot hear the audio; the two transcripts are all you have.

- Draft W is Whisper large-v3. Words it was unsure of are marked (?).
- Draft Q is Qwen3-ASR. It is often better on mixed Hindi-English, but not always.
- Each line starts with its time in seconds, so you can line the drafts up.

Rewrite each numbered TARGET segment of draft Q; the lines around them are context.

Rules:
- Hindi in Devanagari, English words in Latin script, as Indian speakers who mix Hindi and English would write it.
- Prefer a reading at least one draft supports. You may correct a word to a similar-sounding one
  when the drafts and the context make it clear.
- Never add content neither draft has. Where the drafts disagree and neither makes sense, or both
  are nonsense, write [unclear] for that part. A shorter honest transcript is better than a fluent
  invented one: this transcript is used to judge how the speakers treated each other.
- Punctuate as spoken: full stops, commas, and a question mark for every question.
- Return every target segment by its number, in order. If a segment has no real speech, return
  an empty text for it."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["segments"],
          "properties": {"segments": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["id", "text"],
              "properties": {"id": {"type": "integer"}, "text": {"type": "string"}}}}}}

Ask = Callable[[str, str, str, str, dict], dict]  # (model, effort, system, prompt, schema) -> answer


def _lines(segments: list[dict], a: float, b: float, unsure_p: float | None) -> list[str]:
    out = []
    for seg in segments:
        ws = [w for w in seg["words"] if a <= (w["start"] + w["end"]) / 2 < b]
        if ws:
            text = " ".join(f"{w['w']}(?)" if unsure_p is not None and w.get("p", 1.0) < unsure_p else w["w"]
                            for w in ws)
            out.append(f"[{ws[0]['start']:.1f}] {text}")
    return out


def chunks(segments: list[dict], max_s: float) -> list[list[dict]]:
    """Consecutive segments, about max_s seconds at most per chunk (a longer segment stands alone)."""
    out: list[list[dict]] = []
    for seg in segments:
        if out and seg["end"] - out[-1][0]["start"] <= max_s:
            out[-1].append(seg)
        else:
            out.append([seg])
    return out


def prompt(target: list[dict], qwen: list[dict], whisper: list[dict], params: dict) -> str:
    a, b = target[0]["start"], target[-1]["end"]
    ctx_a, ctx_b = a - params["context_s"], b + params["context_s"]
    tq = [f"#{seg['id']} [{seg['start']:.1f}] {seg['text']}" for seg in target]
    tw = _lines(whisper, a - 0.5, b + 0.5, params["unsure_p"])
    return ("== Draft W, with context ==\n" + "\n".join(_lines(whisper, ctx_a, ctx_b, params["unsure_p"]))
            + "\n\n== Draft Q, with context ==\n" + "\n".join(_lines(qwen, ctx_a, ctx_b, None))
            + "\n\n== TARGET, draft W ==\n" + ("\n".join(tw) or "(nothing)")
            + "\n\n== TARGET, draft Q (rewrite these, by number) ==\n" + "\n".join(tq)
            + "\n\nRewrite each TARGET segment of draft Q.")


def _key(token: str) -> str:
    """A word without its punctuation, lower case. By Unicode category, not \\w: Devanagari vowel
    signs are marks, and \\w would cut words at them."""
    return "".join(c for c in token if not unicodedata.category(c).startswith("P")).lower()


def align(words: list[dict], text: str) -> list[dict]:
    """The merged text as words with timings: a word Claude kept takes the Qwen word's timing; the
    words between two kept ones share that time evenly. "[unclear]" gets p 0, so its line is marked
    unsure and insights don't lean on it (stages.utterances)."""
    tokens = text.split()
    if not tokens:
        return []
    timed: list[dict | None] = [None] * len(tokens)
    m = difflib.SequenceMatcher(a=[_key(w["w"]) for w in words], b=[_key(t) for t in tokens], autojunk=False)
    for blk in m.get_matching_blocks():
        for k in range(blk.size):
            w = words[blk.a + k]
            timed[blk.b + k] = {"w": tokens[blk.b + k], "start": w["start"], "end": w["end"]}
    lo, hi = words[0]["start"], words[-1]["end"]
    out: list[dict] = []
    i = 0
    while i < len(tokens):
        if timed[i]:
            out.append(timed[i])
            i += 1
            continue
        j = i
        while j < len(tokens) and not timed[j]:
            j += 1
        a = out[-1]["end"] if out else lo
        b = timed[j]["start"] if j < len(tokens) else hi
        b = max(a, b)
        step = (b - a) / (j - i)
        for k in range(i, j):
            t0 = a + step * (k - i)
            out.append({"w": tokens[k], "start": round(t0, 2), "end": round(t0 + step, 2)})
        i = j
    for w in out:
        if UNCLEAR in w["w"]:  # "[unclear]", "[unclear]।", "[unclear]," (the danda wasn't caught: M24)
            w["p"] = 0.0
    return out


def merge(qwen: list[dict], whisper: list[dict], params: dict, ask: Ask,
          progress: Callable[[str], None] = lambda m: None) -> tuple[list[dict], dict]:
    """Qwen's segments with Claude's text and timings mapped back; and what it took."""
    out: list[dict] = []
    calls, unclear = 0, 0
    kept: list[int] = []  # segments Claude didn't return: Qwen's draft as it was
    groups = chunks(qwen, params["chunk_s"])
    for n, group in enumerate(groups):
        progress(f"Merging the Hindi drafts: part {n + 1} of {len(groups)}")
        answer = ask(params["merge_model"], params["merge_effort"], SYSTEM, prompt(group, qwen, whisper, params), SCHEMA)
        calls += 1
        by_id = {s["id"]: s.get("text", "") for s in answer.get("segments", [])}
        for seg in group:
            # A segment Claude didn't return keeps Qwen's words: missing is not the same as "no speech".
            # It's counted and its line shows ⚠: an unmerged Hindi draft (review 4 Oct, M24).
            text = by_id.get(seg["id"], seg["text"]).strip()
            words = align(seg["words"], text)
            if not words:
                continue
            if seg["id"] not in by_id:
                kept.append(seg["id"])
                for w in words:
                    w.setdefault("p", 0.2)
            unclear += sum(1 for w in words if w.get("p") == 0.0)
            out.append({"id": seg["id"], "start": words[0]["start"], "end": words[-1]["end"],
                        "text": " ".join(w["w"] for w in words), "words": words})
    return out, {"model": params["merge_model"], "effort": params["merge_effort"], "calls": calls, "unclear": unclear,
                 "kept_draft": kept}
