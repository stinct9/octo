"""Blind comparison of two transcription models on the owner's own conversations.

    python -m bench.asrcompare run Qwen/Qwen3-ASR-1.7B SESSION...   # transcribe whole conversations
    python -m bench.asrcompare page SESSION... [--n 10]              # build the blind A/B page
    python -m bench.asrcompare retest OLDPAGE whisper Qwen/... X.json  # same stretches, other models
    python -m bench.asrcompare tally PICKS.json                      # reveal which model won

The current transcript (Whisper, from the pipeline) is compared against the other model's, both
run over the whole conversation, on short stretches where people are talking. The page and its
clips go under data/bench/asrcompare/ (gitignored, swept at 60 days, on the Mac only); which side is which is kept in
key.json beside it, not in the page.
"""

from __future__ import annotations

import html
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from octo import DATA, DB_PATH, HOME, ROOT
from octo import audio as A
from octo.db import connect, row

OUT = DATA / "bench" / "asrcompare"  # swept at 60 days (privacy.BENCH)
STRETCH_S = 30.0


def slug(model: str) -> str:
    return model.replace("/", "__")


def session(conn, sid: str) -> dict:
    return row(conn, "SELECT s.*, r.path FROM sessions s JOIN recordings r ON r.id=s.recording_id WHERE s.id=?", sid)


def run(model: str, sids: list[str]) -> None:
    conn = connect(DB_PATH)
    d = OUT / slug(model)
    d.mkdir(parents=True, exist_ok=True)
    for sid in sids:
        s = session(conn, sid)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "in.wav"
            A.write(wav, A.read(HOME / s["path"], s["start_s"], s["end_s"]))
            env = {**os.environ, "HF_HUB_OFFLINE": "0"}  # first use downloads the model
            proc = subprocess.run([str(ROOT / ".venv-qwen" / "bin" / "python"), str(ROOT / "bench" / "qwen_asr.py"),
                                   str(wav), "--model", model], capture_output=True, text=True, env=env)
        if proc.returncode:
            raise RuntimeError(proc.stderr[-2000:])
        out = json.loads(proc.stdout)
        (d / f"{sid}.json").write_text(json.dumps({"model": model, **out}, ensure_ascii=False))
        print(f"{sid}  {(s['end_s'] - s['start_s']) / 60:5.1f} min  {out['seconds']:6.1f} s  {out['language']}  "
              f"{len(out['words'])} words", flush=True)


def whisper_words(conn, sid: str) -> list[dict]:
    t = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='transcribe' AND is_current=1", sid)
    return [{"w": w["w"].strip(), "start": w["start"], "end": w["end"]}
            for seg in json.loads(t["output"])["segments"] for w in seg["words"] if w["w"].strip()]


def stretches(words: list[dict], length: float, n: int) -> list[tuple[float, float]]:
    """n stretches of about `length` seconds, one per equal part of the conversation, each the
    busiest in its part, starting and ending in a pause between words."""
    if not words:
        return []
    starts = [w["start"] for i, w in enumerate(words) if i == 0 or w["start"] - words[i - 1]["end"] >= 0.4]
    first, last = words[0]["start"], words[-1]["end"]
    part = (last - first) / n
    out = []
    for k in range(n):
        lo, hi = first + k * part, first + (k + 1) * part
        best = None
        for a in (x for x in starts if lo <= x <= hi - length / 2):
            ends = [w["end"] for i, w in enumerate(words[:-1]) if a + 0.8 * length <= w["end"] <= a + 1.3 * length
                    and words[i + 1]["start"] - w["end"] >= 0.4]
            if not ends:
                continue
            b = min(ends, key=lambda e: abs(e - a - length))
            busy = sum(1 for w in words if a <= w["start"] < b)
            if not best or busy > best[0]:
                best = (busy, a, b)
        if best:
            out.append((round(best[1] - 0.3, 2), round(best[2] + 0.3, 2)))
    return out


def text_in(words: list[dict], a: float, b: float) -> str:
    """Lower case, no punctuation: the aligner's words carry none and Whisper's do, which would
    give the models away. The owner judges the words."""
    import unicodedata
    said = " ".join(w["w"] for w in words if a <= (w["start"] + w["end"]) / 2 < b)
    said = said.replace("[unclear]", "\uE000")  # a merged transcript's honest gaps survive (bench/hindimerge)
    # By Unicode category, not \w: Devanagari vowel signs are marks, and \w would cut words at them.
    kept = "".join(" " if unicodedata.category(c).startswith("P") and c != "'" else c for c in said)
    return " ".join(kept.lower().split()).replace("\uE000", "[unclear]")


def page(sids: list[str], n: int, other: str) -> Path:
    conn = connect(DB_PATH)
    d = OUT / "page"
    (d / "clips").mkdir(parents=True, exist_ok=True)
    for f in (d / "clips").glob("*.wav"):
        f.unlink()
    rng = random.Random()
    items, key = [], []
    per = max(1, round(n / len(sids)))
    for sid in sids:
        s = session(conn, sid)
        mine = whisper_words(conn, sid)
        theirs = json.loads((OUT / slug(other) / f"{sid}.json").read_text())["words"]
        for a, b in stretches(mine, STRETCH_S, per):
            i = len(items)
            A.write(d / "clips" / f"{i:02d}.wav", A.read(HOME / s["path"], s["start_s"] + a, s["start_s"] + b))
            pair = [("whisper", text_in(mine, a, b)), (other, text_in(theirs, a, b))]
            rng.shuffle(pair)
            items.append({"clip": f"clips/{i:02d}.wav", "at": f"{int(a // 60)}:{int(a % 60):02d}", "sides": [t for _, t in pair]})
            key.append({"session": sid, "start": a, "end": b, **{LETTERS[j]: m for j, (m, _) in enumerate(pair)}})
    (d / "key.json").write_text(json.dumps(key, indent=1))
    (d / "index.html").write_text(render(items))
    return d / "index.html"


LETTERS = "ABCD"


def render(items: list[dict]) -> str:
    import time
    build = int(time.time())
    many = max(len(it["sides"]) for it in items) > 2
    def card(i: int, it: dict) -> str:
        sides = "".join(f'<div class="side"><h3>{LETTERS[j]}</h3><p>{html.escape(t) or "<em>(nothing)</em>"}</p></div>'
                        for j, t in enumerate(it["sides"]))
        best = "best" if many else "better"
        buttons = "".join(f'<button data-v="{LETTERS[j]}">{LETTERS[j]} is {best}</button>' for j in range(len(it["sides"])))
        buttons += '<button data-v="same">About the same</button>'
        if many:
            buttons += '<button data-v="none">None is usable</button>'
        made_up = "".join(f'<button data-x="{LETTERS[j]}">{LETTERS[j]}</button>' for j in range(len(it["sides"])))
        made_up = f'<div class="pick made-up"><span>Words that weren\'t said in:</span>{made_up}</div>' if many else ""
        return f"""
<section class="card" data-i="{i}">
  <h2>Stretch {i + 1} <span>at {it['at']}</span></h2>
  <audio controls preload="none" src="{it['clip']}"></audio>
  <div class="pair" style="grid-template-columns: repeat({len(it['sides'])}, 1fr)">{sides}</div>
  <div class="pick">{buttons}</div>
  {made_up}
</section>"""
    cards = "\n".join(card(i, it) for i, it in enumerate(items))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transcript Blind Test</title>
<style>
:root {{ --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --card:#fff; --line:#e7e5e4; --accent:#2563eb; --on:#dbeafe; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --card:#292524; --line:#44403c; --accent:#60a5fa; --on:#1e3a5f; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.55 -apple-system, system-ui, sans-serif; }}
main {{ max-width: 980px; margin: 0 auto; padding: 24px 16px 80px; }}
header p {{ color: var(--muted); margin: 4px 0 0; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin:16px 0; }}
.card h2 {{ font-size:17px; margin:0 0 10px; }} .card h2 span {{ color:var(--muted); font-weight:400; font-size:14px; }}
audio {{ width:100%; }}
.pair {{ display:grid; gap:12px; margin:12px 0; }}
@media (max-width: 640px) {{ .pair {{ grid-template-columns:1fr !important; }} }}
.side {{ border:1px solid var(--line); border-radius:8px; padding:10px 12px; }}
.side h3 {{ margin:0 0 4px; font-size:14px; color:var(--muted); }} .side p {{ margin:0; }}
.pick {{ display:flex; gap:8px; flex-wrap:wrap; }}
.pick button {{ font:inherit; padding:6px 14px; border-radius:8px; border:1px solid var(--line); background:transparent; color:var(--fg); cursor:pointer; }}
.pick button.on {{ background:var(--on); border-color:var(--accent); }}
.made-up {{ margin-top:8px; align-items:center; }} .made-up span {{ color:var(--muted); font-size:14px; }}
#progress {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); font-weight:600; }}
</style></head><body><main>
<header><h1>Which transcript is closer?</h1>
<p>Play each stretch and pick the transcript with fewer mistakes: wrong words, missing words, words that weren't said.
The sides are shuffled on every stretch; capitals and punctuation are removed from both so they can't give a model away. Nothing leaves this Mac; tell Claude when you're done.</p></header>
<div id="progress"></div>
{cards}
</main>
<script>
const KEY = "asrcompare-picks-{build}";  // a new page starts with no picks
let picks = {{}};
try {{ picks = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{}}
const total = document.querySelectorAll(".card").length;
function show() {{
  document.querySelectorAll(".card").forEach(c => c.querySelectorAll("button").forEach(b =>
    b.classList.toggle("on", b.dataset.v ? picks[c.dataset.i] === b.dataset.v
                                         : (picks["x" + c.dataset.i] || []).includes(b.dataset.x))));
  const done = Object.keys(picks).filter(k => !k.startsWith("x")).length;
  document.getElementById("progress").textContent = done + " of " + total + " picked" + (done === total ? " — done, tell Claude" : "");
}}
document.querySelectorAll(".card").forEach(c => c.querySelectorAll("button").forEach(b => b.onclick = () => {{
  if (b.dataset.x) {{  // "words that weren't said": any number of sides, click again to undo
    const k = "x" + c.dataset.i, on = new Set(picks[k] || []);
    on.has(b.dataset.x) ? on.delete(b.dataset.x) : on.add(b.dataset.x);
    picks[k] = [...on];
  }} else picks[c.dataset.i] = b.dataset.v;
  try {{ localStorage.setItem(KEY, JSON.stringify(picks)); }} catch (e) {{}}
  show();
}}));
window.asrPicks = () => picks;
show();
</script></body></html>"""


def tally(picks: dict) -> None:
    key = json.loads((OUT / "page" / "key.json").read_text())
    score: dict[str, int] = {}
    for i, k in enumerate(key):
        v = picks.get(str(i))
        winner = v if v in ("same", "none") else k[v] if v in k else "unpicked"
        score[winner] = score.get(winner, 0) + 1
        made_up = [k[x] for x in picks.get(f"x{i}", []) if x in k]
        print(f"{i + 1:2}  {k['session'][-8:]}  {winner}" + (f"   made up: {', '.join(made_up)}" if made_up else ""))
    print(score)
    invented: dict[str, int] = {}
    for i, k in enumerate(key):
        for x in picks.get(f"x{i}", []):
            invented[k[x]] = invented.get(k[x], 0) + 1
    if invented:
        print("stretches with words that weren't said:", invented)


def retest(src: str, sources: list[str]) -> Path:
    """A new page over an earlier page's stretches and clips (data/bench/asrcompare/SRC), with other
    candidates: "whisper" (the pipeline's transcript), a model run with `run` (by its name), or a
    JSON file from bench/clip_asr.py (texts by clip)."""
    import shutil

    conn = connect(DB_PATH)
    old = OUT / src if (OUT / src).exists() else DATA / "asrcompare" / src  # before 5 Oct (kept, J5)
    key = json.loads((old / "key.json").read_text())
    d = OUT / "page"
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(old / "clips", d / "clips")
    rng = random.Random()
    texts: dict[str, list[str]] = {}
    for name in sources:
        if name == "whisper":
            cache: dict[str, list[dict]] = {}
            texts[name] = [text_in(cache.setdefault(k["session"], whisper_words(conn, k["session"])), k["start"], k["end"])
                           for k in key]
        elif name.endswith(".json"):
            data = json.loads(Path(name).read_text())
            by_clip = {Path(c).name: t for c, t in data["texts"].items()}
            texts[data["model"]] = [text_in([{"w": by_clip[f"{i:02d}.wav"], "start": 0, "end": 0}], 0, 1)
                                    for i in range(len(key))]
        else:
            runs = {}
            texts[name] = [text_in(runs.setdefault(k["session"], json.loads(
                (OUT / slug(name) / f"{k['session']}.json").read_text())["words"]), k["start"], k["end"]) for k in key]
    items, new_key = [], []
    for i, k in enumerate(key):
        sides = [(m, t[i]) for m, t in texts.items()]
        rng.shuffle(sides)
        items.append({"clip": f"clips/{i:02d}.wav", "at": f"{int(k['start'] // 60)}:{int(k['start'] % 60):02d}",
                      "sides": [t for _, t in sides]})
        new_key.append({"session": k["session"], "start": k["start"], "end": k["end"],
                        **{LETTERS[j]: m for j, (m, _) in enumerate(sides)}})
    (d / "key.json").write_text(json.dumps(new_key, indent=1))
    (d / "index.html").write_text(render(items))
    return d / "index.html"


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "run":
        run(sys.argv[2], sys.argv[3:])
    elif cmd == "page":
        args = sys.argv[2:]
        n = int(args[args.index("--n") + 1]) if "--n" in args else 10
        other = args[args.index("--model") + 1] if "--model" in args else "Qwen/Qwen3-ASR-1.7B"
        sids = [a for a in args if a.startswith("ses_")]
        print(page(sids, n, other))
    elif cmd == "retest":
        print(retest(sys.argv[2], sys.argv[3:]))
    else:
        tally(json.loads(Path(sys.argv[2]).read_text()))
