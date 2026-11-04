"""Hindi with English mixed in: two local drafts, merged by Claude Opus from their text.

    python -m bench.hindimerge qwen SESSION        # Qwen3-ASR told "Hindi", on the louder copy
    python -m bench.hindimerge merge SRC           # Opus merges the drafts on SRC's stretches
    python -m bench.hindimerge merge3 SRC GEMMA.json   # the same with a third draft, Gemma 4 (bench/gemma_asr.py)
    python -m bench.asrcompare retest SRC data/bench/asrcompare/hindi-merge/{whisper-hi,qwen-hi,opus-merge}.json

Claude can't hear audio; it only reads the two transcripts (Whisper large-v3 and Qwen3-ASR, both
told the language is Hindi), with Whisper's unsure words marked, and writes what it thinks was said,
"[unclear]" where the drafts don't support a reading. Only transcript text and names leave the Mac
(2 Oct). The worry to measure is invention: fluent Hindi nobody said.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from octo import DATA, DB_PATH, HOME
from octo import audio as A
from octo.db import connect, row, rows

OUT = DATA / "bench" / "asrcompare" / "hindi-merge"  # swept at 60 days
CONTEXT_S = 90.0  # drafts this far either side of a stretch, so Opus can follow the thread
UNSURE_P = 0.4    # Whisper words below this probability are marked for Opus
MODEL, EFFORT = "claude-opus-5-5", "high"


def whisper_hi(conn, sid: str) -> list[dict]:
    """The last Whisper run that heard this conversation as Hindi (from before Hindi was paused)."""
    r = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='transcribe' AND status='done' "
                  "AND json_extract(params, '$.model') LIKE '%whisper%' AND json_extract(output, '$.language')='hi' "
                  "ORDER BY finished_at DESC LIMIT 1", sid)
    if not r:
        raise SystemExit(f"{sid}: no Whisper transcript in Hindi")
    return json.loads(r["output"])["segments"]


def qwen(sid: str) -> None:
    """Qwen3-ASR as the pipeline would run it if Hindi weren't paused: same louder copy, language
    Hindi. (The 2 Oct run let Qwen guess, and it guessed Chinese.)"""
    from octo.asr import louder, qwen_words

    conn = connect(DB_PATH)
    s = row(conn, "SELECT s.*, r.path FROM sessions s JOIN recordings r ON r.id=s.recording_id WHERE s.id=?", sid)
    tr = row(conn, "SELECT params, upstream FROM stage_runs WHERE target=? AND stage='transcribe' AND is_current=1", sid)
    params = json.loads(tr["params"])
    find = row(conn, "SELECT output FROM stage_runs WHERE id=?", json.loads(tr["upstream"])[0])
    conv = next(c for c in json.loads(find["output"])["conversations"] if abs(c["start"] - s["start_s"]) < 0.01)
    spans = [[a - s["start_s"], b - s["start_s"]] for a, b in conv.get("speech", [])]
    samples = louder(A.read(HOME / s["path"], s["start_s"], s["end_s"]), spans, params)
    segments, rejected = qwen_words(samples, spans, "hi", params)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"qwen-hi_{sid}.json").write_text(json.dumps({"segments": segments, "rejected_as_silence": rejected},
                                                        ensure_ascii=False))
    print(f"{sid}: {len(segments)} segments, {sum(len(x['words']) for x in segments)} words")


def lines(segments: list[dict], a: float, b: float, unsure: bool) -> list[str]:
    out = []
    for seg in segments:
        ws = [w for w in seg["words"] if a <= (w["start"] + w["end"]) / 2 < b]
        if ws:
            text = " ".join(f"{w['w']}(?)" if unsure and w.get("p", 1) < UNSURE_P else w["w"] for w in ws)
            out.append(f"[{ws[0]['start']:.1f}] {text}")
    return out


SYSTEM = """You turn two automatic transcripts of the same recording into the best reading of what was said.

The recording is a family or work conversation in India, mostly Hindi with English words and
sentences mixed in. It was recorded across a room, so both transcripts are poor: words misheard as
similar-sounding words, words dropped, and nonsense in noisy places. You cannot hear the audio; the
two transcripts are all you have.

- Draft W is Whisper large-v3. Words it was unsure of are marked (?).
- Draft Q is Qwen3-ASR. It is often better on mixed Hindi-English, but not always.
- Each line starts with its time in seconds, so you can line the drafts up.

Write what was said in the TARGET stretch only; the lines around it are context.

Rules:
- Hindi in Devanagari, English words in Latin script, as Indian speakers who mix Hindi and English would write it.
- Prefer a reading at least one draft supports. You may correct a word to a similar-sounding one
  when the drafts and the context make it clear.
- Never add content neither draft has. Where the drafts disagree and neither makes sense, or both
  are nonsense, write [unclear] for that part. A shorter honest transcript is better than a fluent
  invented one: this transcript is used to judge how the speakers treated each other."""


GEMMA_NOTE = """
- Draft G is Gemma 4 (Google), a third, independent model. It covers the TARGET stretch only, with
  no timings, and sometimes writes Hindi in Latin letters: read it for what was said, not its script."""


def prompt(w: list[str], q: list[str], tw: list[str], tq: list[str], names: list[str], g: str | None = None) -> str:
    who = f"People in this conversation (names may appear): {', '.join(names)}\n\n" if names else ""
    return (f"{who}== Draft W, with context ==\n" + "\n".join(w) + "\n\n== Draft Q, with context ==\n" + "\n".join(q)
            + "\n\n== TARGET, draft W ==\n" + ("\n".join(tw) or "(nothing)")
            + "\n\n== TARGET, draft Q ==\n" + ("\n".join(tq) or "(nothing)")
            + (f"\n\n== TARGET, draft G ==\n{g or '(nothing)'}" if g is not None else "")
            + "\n\nWrite what was said in the TARGET stretch.")


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["text"],
          "properties": {"text": {"type": "string", "description": "What was said in the target stretch"}}}


def merge(src: str, gemma: str | None = None) -> None:
    from octo.claude_code import ask

    g_texts = {Path(c).name: t for c, t in json.loads(Path(gemma).read_text())["texts"].items()} if gemma else None
    name = "opus-merge3" if gemma else "opus-merge"

    conn = connect(DB_PATH)
    # Blind tests from before 5 Oct are still in data/asrcompare/ (the owner kept them, J5); newer ones in data/bench/.
    key_file = next(p for p in (OUT.parent / src / "key.json", DATA / "asrcompare" / src / "key.json") if p.exists())
    key = json.loads(key_file.read_text())
    texts: dict[str, dict[str, str]] = {"whisper-hi": {}, "qwen-hi": {}, name: {}}
    log = []
    for i, k in enumerate(key):
        sid, a, b = k["session"], k["start"], k["end"]
        ws, qs = whisper_hi(conn, sid), json.loads((OUT / f"qwen-hi_{sid}.json").read_text())["segments"]
        names = [r["name"] for r in rows(conn, "SELECT DISTINCT p.name FROM session_speakers ss JOIN people p "
                                               "ON p.id=ss.person_id WHERE ss.session_id=? AND ss.state='confirmed'", sid)]
        tw, tq = lines(ws, a, b, False), lines(qs, a, b, False)
        clip = f"{i:02d}.wav"
        texts["whisper-hi"][clip] = " ".join(x.split("] ", 1)[1] for x in tw)
        texts["qwen-hi"][clip] = " ".join(x.split("] ", 1)[1] for x in tq)
        p = prompt(lines(ws, a - CONTEXT_S, b + CONTEXT_S, True), lines(qs, a - CONTEXT_S, b + CONTEXT_S, False),
                   lines(ws, a, b, True), tq, names, g_texts[clip] if g_texts else None)
        system = SYSTEM.replace("but not always.", "but not always." + GEMMA_NOTE) if g_texts else SYSTEM
        answer, usage = ask(MODEL, EFFORT, system, p, schema=SCHEMA, timeout=600)
        texts[name][clip] = answer["text"].strip()
        log.append({"clip": clip, "usage": usage})
        print(f"{clip}  {usage.get('duration_ms', 0) / 1000:5.0f} s  {answer['text'].count('[unclear]')} unclear", flush=True)
    for model, t in texts.items():
        (OUT / f"{model}.json").write_text(json.dumps({"model": model, "texts": t}, ensure_ascii=False, indent=1))
    (OUT / f"usage-{name}.json").write_text(json.dumps(log, indent=1))


if __name__ == "__main__":
    {"qwen": lambda: qwen(sys.argv[2]), "merge": lambda: merge(sys.argv[2]),
     "merge3": lambda: merge(sys.argv[2], sys.argv[3])}[sys.argv[1]]()
