"""Make a public copy of this repository: its tracked files minus what's personal, with the public
README, and a check that fails if anything personal is left (product plan 4.7).

    python scripts/public_copy.py OUT_DIR [--test]

--test also runs the test suite inside the copy, so a page that needs a file left out fails here first.

It only writes a folder. Publishing it (a new repository, its first commit, a licence) is a person's step.
What's left out, swapped in and searched for is in public-copy.json, which is never copied itself:
  exclude  globs of tracked files to leave out
  replace  {path in the copy: tracked file to put there}
  block    words that must not appear anywhere in the copy, in a file or in a file's path: exit 1
  warn     words that are reported, file by file, for a person to decide
Words match whole, in any case. Every file that reads as text is searched, whatever its name (review 8 Oct,
finding 16: .pbxproj, .xcconfig, .storyboard and .gitignore were copied unread, and "Jay" missed "JAY").
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "public-copy.json"


def tracked() -> list[str]:
    return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")[:-1]


def excluded(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, g) or (g.endswith("/**") and path.startswith(g[:-2])) for g in globs)


def build(out: Path, cfg: dict) -> list[str]:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} isn't empty")
    out.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in tracked():
        if excluded(path, cfg["exclude"]) or path in cfg["replace"]:
            continue
        dest = out / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, dest)
        copied.append(path)
    for path, source in cfg["replace"].items():
        dest = out / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, dest)
        copied.append(path)
    return sorted(copied)


def text_of(f: Path) -> str | None:
    """The file as text, or None for a binary one (it doesn't decode, or holds NUL bytes)."""
    try:
        data = f.read_bytes()
        return None if b"\0" in data else data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def scan(out: Path, words: list[str]) -> dict[str, list[str]]:
    """{word: ["file:line", ...]} for whole-word matches, in any case, in every text file of the copy and in every
    file's path ("file:path")."""
    found: dict[str, list[str]] = {}
    pats = {w: re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.I) for w in words}
    for f in sorted(out.rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(out))
        for w, pat in pats.items():
            if pat.search(rel.replace("/", " ").replace("_", " ").replace("-", " ").replace(".", " ")):
                found.setdefault(w, []).append(f"{rel}:path")
        text = text_of(f)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for w, pat in pats.items():
                if pat.search(line):
                    found.setdefault(w, []).append(f"{rel}:{i}")
    return found


def run_tests(out: Path) -> int:
    """The suite, inside the copy, with this checkout's Python: a page or test that needs a file left out fails."""
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=out).returncode


def main(argv: list[str]) -> int:
    test = "--test" in argv
    argv = [a for a in argv if a != "--test"]
    if len(argv) != 2:
        print(__doc__)
        return 2
    cfg = json.loads(CONFIG.read_text())
    out = Path(argv[1]).resolve()
    files = build(out, cfg)
    blocked, warned = scan(out, cfg["block"]), scan(out, cfg["warn"])
    print(f"{len(files)} files in {out}")
    for w, hits in warned.items():
        by_file = sorted({h.rsplit(':', 1)[0] for h in hits})
        print(f"  ! {w}: {len(hits)} lines in {len(by_file)} files (for a person to decide): {', '.join(by_file[:8])}"
              + (" …" if len(by_file) > 8 else ""))
    for w, hits in blocked.items():
        print(f"  ✗ {w}: {', '.join(hits[:10])}" + (" …" if len(hits) > 10 else ""))
    if blocked:
        print(f"\nNot ready to publish: {sum(map(len, blocked.values()))} lines with words that must not be public.")
        return 1
    if test and run_tests(out) != 0:
        print("\nNot ready to publish: the tests fail in the copy (something it needs was left out).")
        return 1
    print("\nNothing personal found from the block list. Publishing is still a person's step (and the licence).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
