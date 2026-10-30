"""`bench` command: run the hardware bench from the terminal (same runner as the web UI).

  bench run [all | board mic sd wifi transcribe ...] [--model NAME] [--no-cues] [--verbose]
  bench wipe                      erase + reformat the SD card (asks for WIPE)
  bench record [OUT.wav]          35 s board-mic recording (voice enrolment: then octo enroll)
  bench runs                      recent runs
  bench analyse FILE.wav          level / SNR / cue report for any recording
  bench transcribe FILE.wav       Whisper, forced English vs auto-detect
  bench steps                     list the steps
  bench serve [--port 8765]       web UI on http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .events import Event
from .runner import Busy, Runner
from .steps import CLI_ONLY, RUN_ALL, STEPS
from .store import RunStore

ICON = {"pass": "✓", "warn": "!", "fail": "✗", "skipped": "–", "cancelled": "⨯", "aborted": "⨯"}
COLOR = {"pass": "32", "warn": "33", "fail": "31", "skipped": "90", "cancelled": "90", "aborted": "90"}
TTY = sys.stdout.isatty()


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


class Printer:
    """Turns events into terminal output."""

    def __init__(self, runner: Runner, verbose: bool):
        self.runner = runner
        self.verbose = verbose
        self._inline = False  # a \r-updated line (meter/progress) is on screen

    def _end_inline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    def _inline_line(self, text: str) -> None:
        if TTY:
            print("\r\033[K" + text, end="", flush=True)
            self._inline = True

    def __call__(self, e: Event) -> None:
        d = e.data
        if e.type == "step.level":
            db = d["dbfs"]
            bar = "█" * max(0, min(40, int((db + 80) / 2)))
            return self._inline_line(f"    mic {db:6.1f} dBFS {bar}")
        if e.type == "step.progress":
            if d["phase"] == "upload" and d.get("pct") is not None:
                return self._inline_line(f"    uploading {d['pct']:5.1f}%")
            if d["phase"] == "compile":
                return self._inline_line(f"    compiling {e.step}")
            self._end_inline()
            print(c("90", f"    {d['phase']}{': ' + d['detail'] if d.get('detail') else ''}"))
            return
        self._end_inline()
        if e.type == "run.started":
            print(c("1", f"Run {e.run_id}: {', '.join(s['id'] for s in d['steps'])}"))
        elif e.type == "run.state" and d["state"] == "waiting_for_user":
            pass  # the prompt event carries the message
        elif e.type == "run.state" and d["state"] == "cancelling":
            print(c("33", "  Cancelling at the next safe point…"))
        elif e.type == "step.started":
            print(c("1", f"\n▶ {d['title']}"))
        elif e.type == "step.log":
            if self.verbose or e.data.get("stream") == "device":
                print(c("90", f"    │ {d['line']}"))
        elif e.type == "step.prompt":
            if d["phase"] == "ready":
                print(c("1;36", f"\n  {d['message']}\n  Press Enter when ready (Ctrl-C to cancel)."))
                threading.Thread(target=self._wait_enter, daemon=True).start()
            else:
                text = {"silent": "● Recording — stay silent", "speak": "● SPEAK NOW",
                        "done": "● Done", "transfer": "  transferring…"}.get(d["phase"], d["phase"])
                print(c("1;35", f"  {text}"))
        elif e.type == "step.metric":
            v = d.get("verdict")
            prev = d.get("previous")
            mark = c(COLOR.get(v, "0"), ICON.get(v, "•")) if v else "•"
            value = f"{d['value']}{' ' + d['unit'] if d.get('unit') else ''}"
            delta = f"  (last {prev})" if prev is not None and prev != d["value"] else ""
            print(f"    {mark} {d['label']}: {value}{c('90', delta)}")
        elif e.type == "step.artifact" and d["kind"] == "transcript":
            for r in d["results"]:
                print(c("90", f"    [{r['mode']}] {r['language']} p={r['language_p']}: {r['text']}"))
        elif e.type == "step.finished":
            s = d["status"]
            name = f"{e.step}: " if d["summary"] == "not started" else ""
            print(c(COLOR[s], f"  {ICON[s]} {s.upper()}  {name}{d['summary']}") + c("90", f"  ({d['duration_s']} s)"))
            if d.get("hint"):
                print(c("33", f"    → {d['hint']}"))
        elif e.type == "run.finished":
            s = d["status"]
            print(c(f"1;{COLOR.get(s, '0')}", f"\nRun {s.upper()}") + (f" — {d['reason']}" if d.get("reason") else ""))

    def _wait_enter(self) -> None:
        try:
            sys.stdin.readline()
        except Exception:
            return
        self.runner.ready()


def _run(step_ids: list[str], options: dict, verbose: bool, steps_table=STEPS) -> int:
    runner = Runner()
    runner.listeners.append(Printer(runner, verbose))
    try:
        runner.start([steps_table[s] for s in step_ids], options)
    except Busy as e:
        print(e, file=sys.stderr)
        return 1
    try:
        while runner.running:
            runner.join(0.2)
    except KeyboardInterrupt:
        runner.cancel()
        try:
            while runner.running:
                runner.join(0.2)
        except KeyboardInterrupt:
            print("\nForced exit -- the board may be mid-operation.", file=sys.stderr)
            return 130
    last = runner.store.read(runner.run_id)[-1] if runner.run_id else None
    return 0 if last and last["data"].get("status") in ("pass", "warn") else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="run steps (default: all)")
    p.add_argument("steps", nargs="*", default=["all"])
    p.add_argument("--model", default="large-v3")
    p.add_argument("--no-cues", action="store_true", help="don't play the Mac sound cues")
    p.add_argument("-v", "--verbose", action="store_true", help="show compiler/uploader output")

    sub.add_parser("wipe", help="erase and reformat the SD card")
    p = sub.add_parser("record", help="35 s recording on the board mic, for voice enrolment")
    p.add_argument("out", type=Path, nargs="?", help="where to save the WAV (default audio/record_<run>.wav)")
    p.add_argument("--no-cues", action="store_true")
    sub.add_parser("runs", help="recent runs")
    sub.add_parser("steps", help="list steps")
    p = sub.add_parser("analyse", help="level/SNR/cue report for a WAV")
    p.add_argument("file", type=Path)
    p = sub.add_parser("transcribe", help="transcribe a WAV")
    p.add_argument("file", type=Path)
    p.add_argument("--model", default="large-v3")
    p = sub.add_parser("serve", help="web UI on localhost")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "steps":
        for sid in RUN_ALL:
            s = STEPS[sid]
            print(f"{sid:11} {s.title}{'  (needs you)' if s.needs_user else ''}")
        for sid, s in CLI_ONLY.items():
            print(f"{sid:11} {s.title}  (bench {'wipe' if sid == 'sd_wipe' else sid})")
        return 0

    if args.cmd == "run":
        ids = RUN_ALL if args.steps == ["all"] else args.steps
        unknown = [s for s in ids if s not in STEPS]
        if unknown:
            ap.error(f"unknown step(s): {', '.join(unknown)}. Known: {', '.join(RUN_ALL)}")
        return _run(ids, {"model": args.model, "cues": not args.no_cues}, args.verbose)

    if args.cmd == "wipe":
        print(c("1;31", "This ERASES the SD card in the board. Type WIPE to continue:"), end=" ", flush=True)
        answer = sys.stdin.readline().strip()
        if answer != "WIPE":
            print("Not confirmed; nothing done.")
            return 1
        return _run(["sd_wipe"], {"confirm": "WIPE"}, verbose=False, steps_table=CLI_ONLY)

    if args.cmd == "record":
        out = args.out.resolve() if args.out else None
        code = _run(["record"], {"cues": not args.no_cues, "out": str(out) if out else None}, verbose=False,
                    steps_table=CLI_ONLY)
        if code == 0 and out:
            print(f"\nsaved {out}\nnext: octo enroll NAME {out} [--self]")
        return code

    if args.cmd == "runs":
        store = RunStore()
        for rid in store.run_ids()[:15]:
            s = store.summary(rid)
            when = datetime.fromtimestamp(s["started_at"]).strftime("%d %b %H:%M") if s["started_at"] else "?"
            steps = " ".join(f"{k}{ICON.get(v, '?')}" for k, v in s["steps"].items())
            print(f"{rid}  {when}  {c(COLOR.get(s['status'], '0'), s['status']):>8}  {steps}")
        return 0

    if args.cmd == "analyse":
        from .audio import analyse
        a = analyse(args.file)
        levels = a.pop("levels", [])
        print(json.dumps(a, indent=2))
        for i, d in enumerate(levels):
            print(f"  {i * 0.25:5.2f}s {d:6.1f} {'#' * max(0, int(d + 70))}")
        return 0

    if args.cmd == "transcribe":
        from .audio import ModelCache, transcribe
        models = ModelCache()
        t0 = time.time()
        model = models.get(args.model, lambda: None, lambda s: None)
        print(f"model {args.model} ready in {time.time() - t0:.1f} s")
        for lang in ("en", None):
            r = transcribe(model, args.file, lang, lambda: None)
            print(f"[{r['mode']}] {r['language']} p={r['language_p']} logp={r['mean_logprob']} "
                  f"({r['seconds']} s): {r['text']}")
        return 0

    if args.cmd == "serve":
        return _serve(args.port, open_browser=not args.no_browser)
    return 0


def _src_hash(src: Path) -> str:
    """Same as ui/scripts/stamp.mjs: sha256 over sorted relative paths + contents. Content-based,
    because git checkouts rewrite timestamps (a timestamp check once reported identical sources stale)."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(src.rglob("*"), key=lambda p: p.relative_to(src).as_posix()):
        if p.is_file():
            h.update(p.relative_to(src).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _serve(port: int, open_browser: bool) -> int:
    import webbrowser

    import uvicorn

    from .api import UI_DIST, create_app

    src = UI_DIST.parent / "src"
    if UI_DIST.exists() and src.exists():
        stamp = UI_DIST / ".src-hash"
        if not stamp.exists() or stamp.read_text().strip() != _src_hash(src):
            print("The UI build doesn't match ui/src -- rebuild it: (cd ui && npm run build)", file=sys.stderr)
            return 1
    url = f"http://127.0.0.1:{port}"
    print(f"bench UI on {url}  (Ctrl-C to stop)")
    if open_browser:  # the link that lets this browser in (octo/mackey.py); never printed
        from octo import mackey as MK
        threading.Timer(1.0, webbrowser.open, args=(MK.let_in_url(url),)).start()
    # 127.0.0.1 only; never --reload with hardware attached (a reload kills the run mid-flash).
    # timeout_graceful_shutdown: an open browser tab holds a never-ending event stream, which
    # otherwise makes Ctrl-C wait forever. The runner cancels any live run on shutdown.
    uvicorn.run(create_app(background_processing=True), host="127.0.0.1", port=port, log_level="warning",
                timeout_graceful_shutdown=3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
