"""The time-to-point pilot (coaching plan C5, backlog B18, 21 Oct) on the owner's two pitch memos (J6).

    python -m bench.ttp run       # one Claude call per memo, quotes checked, the page built
    python -m bench.ttp serve     # the page on 127.0.0.1, saving the owner's answers beside it
    python -m bench.ttp tally     # match / partly / no

The memos are the conversations kept out of the gate (in_gate=0). Both are the owner alone, so each is a single
turn of minutes: Claude splits it into its parts (consecutive lines of at least MIN_PART_S where the owner explains
or answers one thing) and gives, for every part and for the memo as a whole, the sentence that carries the point
(an exact quote with its line), whether building up first was the right call, and the part said in at most 20 words.
Code checks each part is real and each quote is in its line and inside its part (insight.verify_quote), drops the
rest, and measures the quote's start from the transcript's word times: "the point came 41 s into a 55 s turn".

The lab's database is opened read only; only transcript text goes to Claude, as for reviews (octo/claude_code.py:
tools off, an empty folder). The page, its audio and the answers go under data/bench/ttp/ (swept at 60 days).
"""

from __future__ import annotations

import hashlib
import html
import json
import sqlite3
import sys
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from octo import DATA, DB_PATH, HOME
from octo import audio as A
from octo import claude_code
from octo import fillers as FL
from octo.insight import clock, quote_start, transcript, verify_quote

OUT = DATA / "bench" / "ttp"  # swept at 60 days (privacy.BENCH)
MODEL, EFFORT = "claude-opus-5-5", "high"
MIN_PART_S = 20.0
PORT = 8771

POINT = {"type": "object", "properties": {
    "point_line": {"type": "integer"}, "point_quote": {"type": "string"},
    "build_up_right": {"type": "boolean"}, "build_up_why": {"type": "string"}, "short": {"type": "string"}},
    "required": ["point_line", "point_quote", "build_up_right", "build_up_why", "short"]}
SCHEMA = {"type": "object", "properties": {
    "whole": POINT,
    "parts": {"type": "array", "items": {"type": "object", "properties": {
        "first_line": {"type": "integer"}, "last_line": {"type": "integer"}, **POINT["properties"]},
        "required": ["first_line", "last_line", *POINT["required"]]}}},
    "required": ["whole", "parts"]}

SYSTEM = """You read a transcript of {name} speaking alone: a recorded work pitch. {name} wants to get to the point
sooner. Find where the point is.

- `parts`: split the transcript into its parts, in order, each a run of consecutive lines (first_line..last_line) of
  at least 20 seconds where {name} explains or answers one thing. Leave out stretches that aren't explaining or
  answering (a greeting, a false start).
- For each part and for the whole pitch (`whole`):
  - point_quote: the one sentence that carries the point, copied exactly from line point_line (a part's own line).
    Copy the words as they are, fillers included; "..." may stand for words left out.
  - build_up_right: whether building up before the point was the right call here (context the listener needed
    first), or whether the point should have come first. build_up_why: one sentence on why.
  - short: the part said in at most 20 words, as {name} could have said it.
Quote only from the transcript. Line numbers are in square brackets."""


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)  # never writes the live lab
    conn.row_factory = sqlite3.Row
    return conn


def memos(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT s.id, s.start_s, s.end_s, r.path, r.started_at FROM sessions s JOIN recordings r ON r.id=s.recording_id "
        "WHERE s.in_gate=0 AND s.status='active' ORDER BY r.started_at")]


def check_point(raw: dict, by_id: dict, words: list[dict], ids: set[int], start: float, end: float) -> dict:
    """One part's (or the whole memo's) answer, its quote checked against the line and the part."""
    q = verify_quote({"line": raw.get("point_line"), "text": raw.get("point_quote", "")}, by_id) \
        if raw.get("point_line") in ids else None
    out = {"start": start, "end": end, "dur": round(end - start, 1), "short": (raw.get("short") or "").strip(),
           "short_words": len((raw.get("short") or "").split()), "build_up_right": bool(raw.get("build_up_right")),
           "build_up_why": (raw.get("build_up_why") or "").strip(), "quote": None}
    if q:
        at, measured = quote_start(q, by_id[q["line"]], words)
        out["quote"] = {**q, "at": round(at, 1), "offset": round(at - start, 1), "measured": measured}
    else:
        out["dropped"] = "the quote isn't in that line, or the line isn't in this part"
    return out


def check(raw: dict, lines: list[dict], words: list[dict]) -> dict:
    by_id = {ln["id"]: ln for ln in lines}
    order = [ln["id"] for ln in lines]
    parts, dropped = [], []
    for p in raw.get("parts") or []:
        a, b = p.get("first_line"), p.get("last_line")
        if a not in by_id or b not in by_id or order.index(a) > order.index(b):
            dropped.append({"why": "not a run of lines", "raw": p})
            continue
        span = order[order.index(a):order.index(b) + 1]
        start, end = by_id[a]["start"], by_id[b]["end"]
        if end - start < MIN_PART_S:
            dropped.append({"why": f"under {MIN_PART_S:.0f} s", "raw": p})
            continue
        if parts and start < parts[-1]["end"] - 0.01:
            dropped.append({"why": "overlaps the part before", "raw": p})
            continue
        parts.append({"lines": [a, b], **check_point(p, by_id, words, set(span), start, end)})
    whole = check_point(raw.get("whole") or {}, by_id, words, set(order), lines[0]["start"], lines[-1]["end"])
    return {"whole": whole, "parts": parts, "dropped": dropped}


def run() -> None:
    conn = connect_ro()
    found = memos(conn)
    if len(found) != 2:
        sys.exit(f"expected the two pitch memos (in_gate=0), found {len(found)}: check with the owner")
    OUT.mkdir(parents=True, exist_ok=True)
    for m in found:
        out, mine, _, tr = FL.load(conn, m["id"])
        lines = [ln for ln in out["lines"] if ln["speaker"] in mine]
        words = [w for seg in tr["segments"] for w in seg.get("words") or []]
        name = "You"
        f = OUT / f"{m['id']}.json"
        cached = json.loads(f.read_text()) if f.exists() else None
        prompt = transcript(lines, {k: name for k in mine})
        if cached and cached["prompt_sha"] == hash_of(prompt):
            raw, usage = cached["raw"], cached["usage"]  # a re-run costs no call
        else:
            raw, usage = claude_code.ask(MODEL, EFFORT, SYSTEM.format(name=name), prompt, SCHEMA)
        res = {"id": m["id"], "recorded": m["started_at"], "model": MODEL, "effort": EFFORT, "raw": raw, "usage": usage,
               "prompt_sha": hash_of(prompt), "lines": {ln["id"]: ln["text"] for ln in lines}, **check(raw, lines, words)}
        f.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        clip = OUT / f"{m['id']}.wav"
        if not clip.exists():
            A.write(clip, A.read(HOME / m["path"], m["start_s"], m["end_s"]))
        print(f"{m['id']}: {len(res['parts'])} parts, {sum(1 for p in res['parts'] if p['quote'])} quotes kept, "
              f"{len(res['dropped'])} parts dropped, whole quote {'kept' if res['whole']['quote'] else 'dropped'}", flush=True)
    print(build())


def hash_of(s: str) -> str:
    return hashlib.sha256(f"{MODEL}{EFFORT}{SYSTEM}{json.dumps(SCHEMA)}{s}".encode()).hexdigest()[:16]


# ---- the page ----------------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s or "")


def card(key: str, title: str, p: dict, clip: str, lines: dict, ids: list[int] | None) -> str:
    q = p["quote"]
    point = (f'<p class="quote">“{esc(q["text"])}”</p><p class="muted">The point came <b>{q["offset"]:.0f} s</b> into a '
             f'{p["dur"]:.0f} s {"memo" if ids is None else "part"}, at {clock(q["at"])}'
             f'{"" if q["measured"] else " (the line’s start: the words’ times weren’t found)"}. '
             f'<button class="hear" data-clip="{clip}" data-at="{q["at"]}" data-end="{p["end"]}">▶ From the point</button></p>'
             if q else f'<p class="muted">Claude’s quote didn’t check out and was dropped ({esc(p.get("dropped"))}).</p>')
    build = ("Building up first was the right call" if p["build_up_right"] else "The point should have come first")
    text = ""
    if ids:
        keys = [int(k) for k in lines]
        span = keys[keys.index(ids[0]):keys.index(ids[1]) + 1]
        text = "<details><summary>Read the part</summary>" + "".join(
            f'<p><span class="muted">{k}</span> {esc(lines[str(k)])}</p>' for k in span) + "</details>"
    return f"""
<section class="card" data-k="{key}">
  <h2>{esc(title)} <span>{clock(p['start'])}–{clock(p['end'])} · {p['dur']:.0f} s</span>
    <button class="hear" data-clip="{clip}" data-at="{p['start']}" data-end="{p['end']}">▶ Play</button></h2>
  {point}
  <p><b>In {p['short_words']} words:</b> {esc(p['short'])}</p>
  <p class="muted">{build}: {esc(p['build_up_why'])}</p>
  {text}
  <div class="pick"><span>Is that the point, as you see it?</span>
    <button data-v="match">Match</button><button data-v="partly">Partly</button><button data-v="no">No</button></div>
  <textarea placeholder="A note (optional): what the point really was, or what’s off"></textarea>
</section>"""


def build() -> Path:
    sections = []
    for f in sorted(OUT.glob("ses_*.json")):
        r = json.loads(f.read_text())
        year = datetime.fromtimestamp(r["recorded"]).strftime("%-d %b %Y")
        clip = f"{r['id']}.wav"
        cards = [card(f"{r['id']}:whole", f"The {year} pitch as a whole", r["whole"], clip, r["lines"], None)]
        cards += [card(f"{r['id']}:{p['lines'][0]}", f"Part {i + 1}", p, clip, r["lines"], p["lines"])
                  for i, p in enumerate(r["parts"])]
        sections.append(f'<h2 class="memo">The {year} pitch</h2><audio data-clip="{clip}" controls></audio>'
                        + "".join(cards))
    page = OUT / "index.html"
    page.write_text(PAGE.replace("{{sections}}", "\n".join(sections)))
    return page


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Time to Point</title>
<style>
:root { --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --card:#fff; --line:#e7e5e4; --accent:#2563eb; --on:#dbeafe; }
@media (prefers-color-scheme: dark) { :root { --bg:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --card:#292524; --line:#44403c; --accent:#60a5fa; --on:#1e3a5f; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.55 -apple-system, system-ui, sans-serif; }
main { max-width: 820px; margin: 0 auto; padding: 24px 16px 80px; }
.muted, header p { color: var(--muted); }
h2.memo { margin-top: 36px; } audio { width: 100%; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin:16px 0; }
.card h2 { font-size:17px; margin:0 0 10px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.card h2 span { color:var(--muted); font-weight:400; font-size:14px; }
.quote { font-size:17px; margin:0 0 4px; }
button { font:inherit; padding:5px 12px; border-radius:8px; border:1px solid var(--line); background:transparent; color:var(--fg); cursor:pointer; }
.pick { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:10px; } .pick span { color:var(--muted); }
.pick button.on { background:var(--on); border-color:var(--accent); }
textarea { width:100%; margin-top:8px; font:inherit; padding:8px; border-radius:8px; border:1px solid var(--line); background:transparent; color:var(--fg); min-height:2.6em; }
details { margin-top: 8px; } details p { margin: 4px 0; }
#progress { position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); font-weight:600; z-index:1; }
</style></head><body><main>
<header><h1>Is that the point?</h1>
<p>Claude read each pitch memo's transcript and picked, for each part and for the memo as a whole, the sentence that
carries the point. The time it came was measured from the words' times. Play a part, then say whether Claude's point is
yours: <b>Match</b>, <b>Partly</b> or <b>No</b>, with a note if you like. Your answers are saved on this Mac as you go
(answers.json beside this page); nothing leaves it. If you disagree on one of the two memos, time to point isn't tracked in drills; the short version still is.</p></header>
<div id="progress"></div>
{{sections}}
</main>
<script>
let answers = {}, stop = null, saved = true;
const cards = [...document.querySelectorAll(".card")];
function show() {
  cards.forEach(c => c.querySelectorAll(".pick button").forEach(b => b.classList.toggle("on", (answers[c.dataset.k] || {}).v === b.dataset.v)));
  const done = cards.filter(c => (answers[c.dataset.k] || {}).v).length;
  document.getElementById("progress").textContent = done + " of " + cards.length + " answered" +
    (saved ? "" : " · not saved: open this page through `python -m bench.ttp serve`") + (done === cards.length ? " · done, tell Claude" : "");
}
async function save() {
  try { saved = (await fetch("answers", {method: "POST", body: JSON.stringify(answers)})).ok; } catch (e) { saved = false; }
  show();
}
cards.forEach(c => {
  const a = () => (answers[c.dataset.k] ||= {});
  c.querySelectorAll(".pick button").forEach(b => b.onclick = () => { a().v = b.dataset.v; save(); });
  const t = c.querySelector("textarea");
  t.onchange = () => { a().note = t.value; save(); };
});
document.querySelectorAll("button.hear").forEach(b => b.onclick = () => {
  const au = document.querySelector('audio[data-clip="' + b.dataset.clip + '"]');
  au.currentTime = +b.dataset.at; au.play(); stop = +b.dataset.end;
});
// SimpleHTTPRequestHandler ignores Range, so a src it serves can't seek; a blob can.
document.querySelectorAll("audio").forEach(au => fetch(au.dataset.clip).then(r => r.blob()).then(b => au.src = URL.createObjectURL(b)));
document.querySelectorAll("audio").forEach(au => au.ontimeupdate = () => { if (stop !== null && au.currentTime >= stop) { au.pause(); stop = null; } });
fetch("answers.json").then(r => r.ok ? r.json() : {}).then(j => { answers = j || {}; cards.forEach(c => {
  c.querySelector("textarea").value = (answers[c.dataset.k] || {}).note || ""; }); show(); }).catch(() => { saved = false; show(); });
</script></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(OUT), **k)

    def do_POST(self):
        if self.path != "/answers":
            return self.send_error(404)
        body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1_000_000))
        try:
            answers = json.loads(body)
            assert isinstance(answers, dict)
        except Exception:
            return self.send_error(400)
        tmp = OUT / "answers.json.tmp"
        tmp.write_text(json.dumps(answers, ensure_ascii=False, indent=1))
        tmp.replace(OUT / "answers.json")  # never a half-written file
        self.send_response(204)
        self.end_headers()


def serve() -> None:
    print(f"http://127.0.0.1:{PORT}/  (answers in {OUT / 'answers.json'})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


def tally() -> None:
    answers = json.loads((OUT / "answers.json").read_text())
    for which in ("whole", "parts"):
        ks = [k for k in answers if (k.endswith(":whole")) == (which == "whole")]
        n = {v: sum(1 for k in ks if answers[k].get("v") == v) for v in ("match", "partly", "no")}
        print(f"{which}: {n}  ({len(ks)} answered)")


def _selfcheck() -> None:
    lines = [{"id": 1, "speaker": "A", "start": 0.0, "end": 12.0, "text": "So the thing is, we build it first.", "uncertain": False},
             {"id": 2, "speaker": "A", "start": 13.0, "end": 30.0, "text": "Um, the point: we save ten hours a week.", "uncertain": False}]
    words = [{"w": w, "start": 13.0 + i, "end": 13.5 + i} for i, w in enumerate("Um, the point: we save ten hours a week.".split())]
    pt = {"point_quote": "we save ten hours", "build_up_right": False, "build_up_why": "x", "short": "We save ten hours."}
    r = check({"whole": {"point_line": 2, **pt}, "parts": [{"first_line": 1, "last_line": 2, "point_line": 2, **pt},
                                                           {"first_line": 2, "last_line": 2, "point_line": 2, **pt}]}, lines, words)
    assert r["whole"]["quote"]["offset"] == 16.0 and r["whole"]["quote"]["measured"], r["whole"]
    assert len(r["parts"]) == 1 and r["dropped"][0]["why"].startswith("under"), r
    bad = check({"whole": {"point_line": 1, **pt}, "parts": []}, lines, words)  # not in line 1
    assert bad["whole"]["quote"] is None


if __name__ == "__main__":
    _selfcheck()
    {"run": run, "serve": serve, "tally": tally, "page": lambda: print(build())}[sys.argv[1] if len(sys.argv) > 1 else "run"]()
