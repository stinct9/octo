"""Where the owner's principles live, and editing them from the page (product plan 4.2, 7 Oct).

The review reads two sections of the rubric: "## Conduct principles" (list lines, numbered or not, in the
owner's words; headings between them are fine, kept below the section's level) and "## How the review applies it" (how they want the review
done, in their words; may be empty). The rest of the file reaches no review.

Which file:
- `data/rubric.md`: a buyer's own, created the first time they save. It sits with their other data,
  so an update to the software never touches it.
- `pipeline/rubric.md`: the first lab's, in the repo, as before (`insight.RUBRIC`). A lab without data/rubric.md
  reads it; its first save copies it, with the edit, to data/rubric.md, and the repo's file is never written (Jay,
  30 Oct: a save there blocked `octo deploy`). The landing still takes its titles from the repo's file.
- `pipeline/rubric-template.md`: the empty sections a new lab starts from.

The editor saves the owner's text as they typed it. Nothing here writes, suggests or completes a
principle (CLAUDE.md, the rubric decision).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import DATA, HOME, ROOT
from . import insight as I

OWN = DATA / "rubric.md"
TEMPLATE = ROOT / "pipeline" / "rubric-template.md"
PRINCIPLES, RULES = "Conduct principles", "How the review applies it"


def path() -> Path:
    if OWN.exists():
        return OWN
    return I.RUBRIC if I.RUBRIC.exists() else TEMPLATE


def text() -> str:
    p = path()
    return p.read_text() if p.exists() else ""


def sections(t: str | None = None) -> dict:
    """The two sections as the page edits them: raw Markdown, and the principles as the review reads them."""
    t = text() if t is None else t
    return {"principles": I._section(t, PRINCIPLES).strip("\n"), "rules": I._section(t, RULES).strip("\n"),
            "parsed": [{"n": p["n"], "title": p["title"]} for p in I.principles(t)], "file": _shown(path())}


def _shown(p: Path) -> str:
    for base in (HOME, ROOT):  # an owner's data/rubric.md, or the first lab's pipeline/rubric.md in the code
        try:
            return str(p.relative_to(base))
        except ValueError:
            pass
    return str(p)


def _replace(t: str, heading: str, body: str) -> str:
    body = body.strip("\n")
    block = f"## {heading}\n\n{body}\n\n" if body else f"## {heading}\n\n"
    pattern = re.compile(rf"^## {re.escape(heading)}\s*$.*?(?=^## |\Z)", re.M | re.S)
    if pattern.search(t):
        return pattern.sub(lambda m: block, t, count=1)
    return t.rstrip("\n") + "\n\n" + block


def _within(body: str) -> str:
    """A heading typed in a box stays inside its section: "## At work" would end it, so it's kept as "### At work"."""
    return re.sub(r"^#{1,2}(?=\s)", "###", body, flags=re.M)


def preview(principles_md: str) -> list[dict]:
    """The principles as the review would read this text once saved: the page's preview, so the two can't disagree."""
    return [{"n": p["n"], "text": p["text"]} for p in I.principles(f"## {PRINCIPLES}\n\n{_within(principles_md)}\n")]


def save(principles_md: str, rules_md: str) -> dict:
    """Write both sections where they belong (see the module's note); everything else in the file stays.
    Returns the sections as now read."""
    current, target = path(), OWN  # never the code's folder: git tracks pipeline/rubric.md
    if current == OWN or (current == I.RUBRIC and I.principles(current.read_text())):
        base = current.read_text()
    else:  # a new lab: from the template
        base = TEMPLATE.read_text() if TEMPLATE.exists() else "# Rubric\n\n"
    out = _replace(_replace(base, RULES, _within(rules_md)), PRINCIPLES, _within(principles_md))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(out.rstrip("\n") + "\n")
    os.replace(tmp, target)
    return sections()
