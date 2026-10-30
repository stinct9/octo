"""A review of a made-up conversation, from the setup page (open-source plan §4, step 6): a new owner sees what a
review of them looks like, against their own principles and on their own model, before they've recorded anyone.

Text only: the transcript below goes straight to the review, as `octo evalinsight` does with its scripts. An audio
sample would have to go through "Is this you?", and a synthetic voice confirmed as the owner would become their
voiceprint. Nothing here touches the database: the result is one file, replaced each time, and counts toward
nothing (reviewed, the counts, the beta, the coach).
"""

from __future__ import annotations

import json
import time

from . import DATA
from . import files as FL

RESULT = DATA / "sample-review.json"
OTHER = "Meera"

# Invented, like the landing page's example: a 1:1 about a launch date, where a question goes unanswered and the
# owner leans on the date. "A" is the owner.
LINES = [
    ("A", "Thanks for making time. I wanted to talk about the launch date before Thursday's steering meeting."),
    ("B", "Sure. Honestly, I'm worried. QA hasn't signed off on the payments flow, and I don't think the twelfth is realistic."),
    ("A", "Leadership has already announced the twelfth, so moving it is going to be really hard."),
    ("B", "I know. But what does QA actually need from us? Nobody has asked them."),
    ("A", "Let's not go down that path yet. Can your team just work the weekend and close the open bugs?"),
    ("B", "We did that last month. Two people are already burned out, and one of them asked me about moving teams."),
    ("A", "Okay, I hear you. I didn't know about that."),
    ("B", "If we ship with the retry bug, support gets flooded, and it's my team that takes those calls."),
    ("A", "Right. So what would it take to get QA's sign off by the twelfth?"),
    ("B", "Two more test cycles, and someone from platform to fix the retry logic. I asked last week and nobody answered."),
    ("A", "Let me find someone from platform today. If we can't, I'll raise moving the date at the steering meeting myself."),
    ("B", "That would help. Can you also tell them it was my team that flagged it? It keeps getting lost."),
    ("A", "Yes, I'll make sure they know it came from your team. I'll send you a note tonight with what we agreed."),
]


def diarize() -> dict:
    """The script as the review reads a real conversation (what `diarize` gives it): lines with times, about 0.35 s a
    word, and each speaker's talk."""
    lines, turns, clock = [], [], 0.0
    for i, (spk, text) in enumerate(LINES):
        dur = max(1.5, len(text.split()) * 0.35)
        lines.append({"id": i, "speaker": spk, "start": round(clock, 2), "end": round(clock + dur, 2), "text": text,
                      "uncertain": False})
        turns.append({"speaker": spk, "start": clock, "end": clock + dur})
        clock += dur + 0.4
    talk = {k: round(sum(t["end"] - t["start"] for t in turns if t["speaker"] == k), 1) for k in ("A", "B")}
    return {"speakers": ["A", "B"], "lines": lines, "turns": turns, "overlapping_turns": [], "talk_s": talk}


def run(conn, review=None) -> dict:
    """Review the sample with the owner's review model and principles; keep and return the result."""
    from . import review_any as RA
    from . import settings as ST
    from .rubric import text as rubric_text
    reader = ST.reader(conn)
    if not reader:
        raise ValueError("choose a model for reviews first")
    owner = ST.owner(conn)
    me = owner.get("name") or "You"
    params = {"reader": reader, "owner": owner, "min_jay_speech_s": 20.0, "started_at": time.time()}
    out = (review or RA.run)(diarize(), {"A": me, "B": OTHER}, {"A"}, params, rubric_text())
    out.pop("memo", None)
    result = {"at": time.time(), "names": {"A": me, "B": OTHER}, "lines": LINES, "review": out}
    FL.write_private(RESULT, json.dumps(result), 0o600)
    return result


def last() -> dict | None:
    try:
        return json.loads(RESULT.read_text())
    except (OSError, ValueError):
        return None
