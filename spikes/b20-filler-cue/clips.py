"""SPIKE (B20, 20 Oct): is the lab's "you know" good enough to train the recorder's filler cue on?

Cuts a sample of the transcript's "you know"s from the lab's audio for a check by ear, serves a page to mark
them (heard it? who said it?), and reports. Reads the lab; writes only under HOME/data/bench/b20-fillers/,
swept with raw audio at 60 days. Nothing leaves the Mac.

    OCTO_HOME=~/Library/Application\\ Support/octo/lab.noindex .venv/bin/python spikes/b20-filler-cue/clips.py cut
    ... clips.py serve        # http://127.0.0.1:8772
    ... clips.py report [cues]
    ... clips.py cut-cues     # after train.py: the model's cues the transcript doesn't explain, at /cues/
"""
import json
import os
import random
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from octo import HOME, db
from octo import audio as A
from octo import delivery, fillers
from octo.db import row

OUT = HOME / "data" / "bench" / "b20-fillers"
PAGE = Path(__file__).with_name("page.html")
BEFORE, AFTER = 0.8, 0.6   # context around the phrase, s
PER_CONV = 5               # at most this many from one conversation, for spread
PORT = 8772


def norm(w: str) -> str:
    return re.sub(r"[^a-z']", "", w.lower())


def conversations(conn, on_line_only: bool = True):
    """(session with its recording's path and source, its timed words) for each conversation on the fillers' line
    whose audio is still kept."""
    for c in fillers.all_conversations(conn):
        if on_line_only and not c["on_line"]:
            continue
        sess = row(conn, "SELECT s.*, r.path, r.source, r.started_at, r.audio_deleted_at FROM sessions s "
                         "JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", c["session_id"])
        if sess["audio_deleted_at"] or not (HOME / sess["path"]).exists():
            continue
        out, mine, _, tr = fillers.load(conn, sess["id"])
        yield sess, delivery.timed_words(tr, out["lines"], mine) or []


def occurrences(conn) -> list[dict]:
    """Every "you know" in the conversations on the fillers' line: two consecutive words, one speaker."""
    got = []
    for sess, ws in conversations(conn):
        for a, b in zip(ws, ws[1:]):
            if norm(a["w"]) == "you" and norm(b["w"]) == "know" and a["spk"] and a["spk"] == b["spk"]:
                got.append({"session_id": sess["id"], "path": sess["path"], "base": sess["start_s"],
                            "length": sess["end_s"] - sess["start_s"], "start": a["start"], "end": b["end"],
                            "app_says": "me" if a["me"] else "other"})
    return got


def sample(occ: list[dict], n: int, rng: random.Random) -> list[dict]:
    rng.shuffle(occ)
    per: dict[str, int] = {}
    out = []
    for o in occ:
        if per.get(o["session_id"], 0) < PER_CONV:
            per[o["session_id"]] = per.get(o["session_id"], 0) + 1
            out.append(o)
        if len(out) == n:
            break
    return out


def write_set(folder: Path, picked: list[dict], word: str = "you know") -> None:
    """Each picked moment as a clip, made louder to listen to, with the page and its list."""
    (folder / "clips").mkdir(parents=True, exist_ok=True)
    for i, o in enumerate(picked, 1):
        o["id"] = f"c{i:02d}"
        # Made louder over ±5 s around it (for_listening reads speech level over the stretch), then cut.
        lo = max(0.0, o["start"] - 5)
        hi = min(o["length"], o["end"] + 5)
        wide = A.for_listening(A.read(HOME / o["path"], o["base"] + lo, o["base"] + hi))
        a = round((max(0.0, o["start"] - BEFORE) - lo) * A.RATE)
        b = round((min(o["length"], o["end"] + AFTER) - lo) * A.RATE)
        A.write(folder / "clips" / f"{o['id']}.wav", wide[a:b])
    (folder / "sample.json").write_text(json.dumps(picked, indent=1))
    (folder / "index.html").write_text(PAGE.read_text().replace("you know", word))


def cut(n_mine: int = 40, n_other: int = 20) -> None:
    conn = db.connect()
    occ = occurrences(conn)
    rng = random.Random(20)
    picked = sample([o for o in occ if o["app_says"] == "me"], n_mine, rng) + \
        sample([o for o in occ if o["app_says"] == "other"], n_other, rng)
    rng.shuffle(picked)  # mixed, so the page doesn't give away whose
    write_set(OUT, picked)
    print(f"{len(occ)} on the line ({sum(o['app_says'] == 'me' for o in occ)} the owner's); "
          f"cut {len(picked)} into {OUT}")


def cut_cues(n: int = 40) -> None:
    """The model's cues with no "you know" in the transcript near them (train.py, threshold 0.9): its mistake, or
    the transcript's?"""
    conn = db.connect()
    wrong = json.loads((OUT / "step2.json").read_text())["wrong"]
    picked = []
    for sid, t in random.Random(20).sample(wrong, min(n, len(wrong))):
        sess = row(conn, "SELECT s.*, r.path FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", sid)
        picked.append({"session_id": sid, "path": sess["path"], "base": sess["start_s"],
                       "length": sess["end_s"] - sess["start_s"], "start": t - 0.3, "end": t + 0.3, "app_says": "cue"})
    write_set(OUT / "cues", picked)
    print(f"{len(wrong)} cues with no transcript \"you know\"; cut {len(picked)} into {OUT / 'cues'}")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(OUT), **k)

    def do_POST(self):
        # A set's page posts to its own folder: /labels, /cues/labels, /essentially/labels
        sub = self.path.removesuffix("labels").strip("/")
        folder = OUT / sub if re.fullmatch(r"[a-z]*", sub) else None
        if not self.path.endswith("/labels") or not folder or not (folder / "sample.json").exists():
            return self.send_error(404)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tmp = folder / "labels.json.tmp"
        tmp.write_text(json.dumps(body, indent=1))
        os.replace(tmp, folder / "labels.json")
        self.send_response(204)
        self.end_headers()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def report(folder: Path = OUT) -> None:
    picked = {o["id"]: o for o in json.loads((folder / "sample.json").read_text())}
    labels = json.loads((folder / "labels.json").read_text())
    done = [(picked[k], v) for k, v in labels.items() if k in picked and v.get("heard")]
    print(f"{len(done)} of {len(picked)} marked")
    for side in ("me", "other", "cue"):
        got = [v for o, v in done if o["app_says"] == side]
        if not got:
            continue
        heard = [v for v in got if v["heard"] == "yes"]
        print(f"app says {side}: {len(got)} marked; heard {len(heard)}, cut off "
              f"{sum(v['heard'] == 'cut' for v in got)}, not there {sum(v['heard'] == 'no' for v in got)}; "
              f"of those heard, who: " + ", ".join(f"{w} {sum(v.get('who') == w for v in heard)}"
                                                   for w in ("me", "other", "unsure")))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "cut":
        cut()
    elif cmd == "serve":
        print(f"http://127.0.0.1:{PORT}")
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    elif cmd == "cut-cues":
        cut_cues()
    elif cmd == "report":
        report(OUT / sys.argv[2] if len(sys.argv) > 2 else OUT)
    else:
        sys.exit(__doc__)
