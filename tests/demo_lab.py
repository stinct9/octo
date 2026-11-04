"""A made-up conversation lab, to look at the pages without any of the owner's data.

    .venv/bin/python -m tests.demo_lab [--port 8799]

Builds `data/demo/` (a database, audio and clips: gitignored, and never `data/octo.db`) from scripted
people and conversations, with reviews written for the demo and passed through the real checks
(`review_any.check`), then serves the UI built in `ui/dist` on 127.0.0.1. Nothing is processed:
every step is already "done", and the background processor does nothing. Everyone here is invented.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from octo import ROOT, RATE
from octo import audio as A
from octo import review as R
from octo import review_any as RA
from octo.db import connect
from octo.insight import RUBRIC, principles, rubric_version
from octo.pipeline import wanted
from octo.stages import STAGES, count

DEMO = ROOT / "data" / "demo"
DB = DEMO / "octo.db"

PEOPLE = {"Sam": True, "Asha": False, "Ravi": False, "Dev": False, "Meera": False, "Kabir": False}
PITCH = {"A": 150.0, "B": 220.0, "C": 280.0, "D": 340.0}  # a quiet tone per speaker, so playback is audible

# ---- the scripts (A is the owner in every one) -----------------------------------------------------

ASHA = [
    ("B", "So where are we on the payments retry work? Leadership asked me for a date yesterday."),
    ("A", "Yeah, so, um, basically the retry service is done, but you know, the ledger team changed their API last week, so we're kind of blocked on them."),
    ("B", "Okay. When did you find out about the API change?"),
    ("A", "Um, Tuesday, I think. I mean, we were already halfway through the integration, so it was, you know, kind of a surprise for everyone."),
    ("B", "Tuesday. And this is the first I'm hearing of it."),
    ("A", "Yeah, I, uh, I wanted to have a plan before I came to you with it."),
    ("B", "I understand that, but I'd rather know early. What's the actual risk to the date?"),
    ("A", "So basically, if they ship the new endpoint by the fifteenth, we're fine. If they don't, um, we slip by about two weeks."),
    ("B", "Two weeks. That's the number I need for leadership. Is it two weeks or could it be more?"),
    ("A", "It could be more, honestly. Their lead is on leave, so, you know, I don't have a firm answer."),
    ("B", "Okay. Who on their side can commit?"),
    ("A", "Probably Farhan. I can, um, I can set up a call with him tomorrow."),
    ("B", "Please do. And copy me on it."),
    ("A", "Sure. Also, you know, Dev did a really good job on the retry logic, I want to make sure that's visible."),
    ("B", "Good, put it in the update. What do you need from me?"),
    ("A", "Um, honestly, I think we're okay. Maybe just, you know, air cover if the date moves."),
    ("B", "You'll have it, as long as I hear about it before leadership does."),
    ("A", "That's fair. I'll send you a written update by Thursday with both dates."),
    ("B", "Thursday works. Anything else?"),
    ("A", "No, I think that's it. Thanks, Asha."),
]

TEAM = [
    ("A", "Okay, let's start. Ravi, can you walk us through what's left in the sprint?"),
    ("B", "Three stories are left, and the reconciliation one is the big one. I don't think it fits."),
    ("A", "Hmm. I mean, we committed to it, so I'd like to try. Dev, what do you think?"),
    ("C", "I think it could fit if we drop the dashboard polish, but the test data isn't ready yet, and"),
    ("A", "Right, so let's drop the polish then. Ravi, are you okay with that?"),
    ("B", "I'm okay dropping polish, but my worry is the test data. Without it we're guessing."),
    ("A", "Okay, fair. Who owns the test data?"),
    ("D", "That's with the platform team. I can chase them today."),
    ("A", "Great, thanks. So, um, polish out, test data by Wednesday, and we keep reconciliation in."),
    ("B", "If the test data isn't there by Wednesday, can we agree to move reconciliation out?"),
    ("A", "Let's, you know, let's see where we are on Wednesday."),
    ("B", "I'd rather agree now, so nobody's surprised on Friday."),
    ("A", "Okay. If it's not there Wednesday, reconciliation moves to next sprint. Agreed."),
    ("C", "Can I add one thing? The retry logic needs a second reviewer before it ships."),
    ("A", "Good catch. Ravi, can you review it this week?"),
    ("B", "Yes, Thursday."),
]

DINNER = [
    ("B", "Are you coming on Saturday? Mum's asking if we should book the table for six."),
    ("A", "Saturday, um, yes. I might have a call in the morning but the evening is fine."),
    ("B", "You said that last time and then you were on the phone all through lunch."),
    ("A", "I know, I know. This time I'll keep the phone in the car."),
    ("B", "Okay. And can you call the restaurant? I did it last time."),
    ("A", "Yes, I'll call them tomorrow morning."),
    ("B", "And Kavya wants to show you her science project before we go."),
    ("A", "Oh, the volcano? Tell her I want the full demo, with the eruption."),
]

KABIR = [
    ("B", "Hi Sam, it's Kabir from the vendor side. Do you have a minute about the contract renewal?"),
    ("A", "Sure, go ahead. Is this about the pricing change?"),
    ("B", "Partly. We'd like to move to annual billing, and there's a new support tier."),
    ("A", "Okay. Send me the details in writing and I'll take it to procurement."),
    ("B", "Will do. Can we get a decision by month end?"),
    ("A", "I can't promise month end, but I'll tell you by Friday where it stands."),
]

SHORT = [
    ("B", "Did you see the message from the school about Friday?"),
    ("A", "Not yet, I'll look tonight."),
    ("B", "Okay, it's about the pickup time."),
]

# The television on in the evening: voices, but the words were all dropped (B1, 13 Oct). The script only
# times the turns; no text is shown.
TV = [("B", "x " * 9), ("C", "x " * 6), ("B", "x " * 12), ("D", "x " * 5), ("C", "x " * 8), ("B", "x " * 10)]
# Music on while cooking: one voice singing along, no words kept (B5, "Sort them").
MUSIC = [("B", "x " * 20), ("B", "x " * 26), ("B", "x " * 18)]

# ---- reviews, as Claude would answer (then checked in code, like a real one) ----------------

ASHA_REVIEW = {
    "summary": "Asha asked for a date for the payments retry work. You explained the ledger team's API change, which she heard about for the first time, three days after you knew. She pressed twice for the real risk; you said two weeks, then that it could be more. You agreed to call Farhan and to send her a written update by Thursday.",
    "one_thing": {"headline": "Tell Asha when a date moves, before you have the plan",
                  "why": "She said twice that she'd rather know early. The slip reached her three days late, and her standing with leadership rests on hearing it first.",
                  "quotes": [{"line": 5, "text": "I wanted to have a plan before I came to you with it"},
                             {"line": 6, "text": "I'd rather know early"}], "next": 0},
    "reading": [{"person": "Asha", "headline": "Protects her standing by hearing things first",
                 "observation": "Asha came back to timing three times: when you found out, the real risk, and hearing before leadership does. She offered help once you'd given her the facts. Her words read as wanting to be useful to you, on condition that she isn't surprised.",
                 "quotes": [{"line": 6, "text": "I'd rather know early"},
                            {"line": 16, "text": "as long as I hear about it before leadership does"}]}],
    "between": [{"headline": "The real date is still open",
                 "observation": "You gave two weeks, then said it could be more. Asha needs one number for leadership and left without it; Thursday's update is where it lands.",
                 "quotes": [{"line": 8, "text": "Is it two weeks or could it be more?"},
                            {"line": 9, "text": "It could be more, honestly."}]},
                {"headline": "Her offer of help went unused",
                 "observation": "Asha asked what you need from her; you said you were okay and asked only for air cover later. A specific ask (a word with the ledger team's lead) might have moved the date.",
                 "quotes": [{"line": 14, "text": "What do you need from me?"},
                            {"line": 15, "text": "I think we're okay"}]}],
    "conduct": [{"principle": 3, "held": "partly",
                 "observation": "When she asked, you were plain about the uncertainty. But you held the news for three days, which kept one thing inside while things looked fine outside.",
                 "quotes": [{"line": 9, "text": "It could be more, honestly."},
                            {"line": 5, "text": "I wanted to have a plan before I came to you with it"}]},
                {"principle": 7, "held": "held",
                 "observation": "When Asha pointed out she was hearing it first, you didn't defend or argue; you accepted it and moved to what you'd do.",
                 "quotes": [{"line": 17, "text": "That's fair."}]},
                {"principle": 12, "held": "held",
                 "observation": "You made sure Dev's work on the retry logic would be seen, rather than your own.",
                 "quotes": [{"line": 13, "text": "Dev did a really good job on the retry logic"}]}],
    "no_moment": [],
    "next": [{"title": "Tell Asha the day a date moves", "detail": "Next time a dependency slips, message Asha that day with what you know, even before there's a plan.",
              "why": "She asked for it twice.", "due": ""},
             {"title": "Call Farhan for a firm endpoint date", "detail": "Ask whether the new endpoint ships by the fifteenth, and who decides if it doesn't. Copy Asha.",
              "why": "Asha needs one number for leadership.", "due": "+1"},
             {"title": "Send Asha the written update", "detail": "Both dates, what each depends on, and Dev's work on the retry logic.",
              "why": "You promised it for Thursday.", "due": "+3"}],
    "limits": "Text only: tone is inferred from the words. How Asha took the three-day delay is read from what she said, not how she said it.",
}

# B35: the same meeting after Ravi's call dropped, recorded apart four minutes on; the lab read them as one discussion.
TEAM_AFTER = [
    ("B", "Sorry, my call dropped. Where were we, the retry logic?"),
    ("A", "Dev wants a second reviewer on it before it ships. Ravi, can you take that?"),
    ("B", "I can, but not before Thursday. Is that too late?"),
    ("C", "Thursday works if it ships Friday. I'll keep the change small so it's quick to read."),
    ("A", "Okay. So, Ravi reviews Thursday, Dev keeps it small, and it ships Friday."),
    ("B", "And if the test data slips, reconciliation moves. We agreed that, right?"),
    ("A", "We did. I'll write it in the channel so it's in one place."),
]

TEAM_REVIEW = {
    "summary": "Sprint planning with Ravi, Dev and a fourth voice. Ravi doubted the reconciliation story would fit; you kept it in by dropping the dashboard polish. Ravi asked twice for a rule if the test data slipped, and you agreed one only after he pressed.",
    "one_thing": {"headline": "Agree the fallback when someone asks for it",
                  "why": "Ravi asked for a rule in advance and you deferred it once before agreeing. Settling it the first time saves the second ask and shows him his worry was heard.",
                  "quotes": [{"line": 10, "text": "let's see where we are on Wednesday"},
                             {"line": 11, "text": "I'd rather agree now, so nobody's surprised on Friday."}], "next": 0},
    "reading": [{"person": "Ravi", "headline": "Wants risks settled before they bite",
                 "observation": "Ravi raised the fit early, then the test data, then asked for an agreement in advance. Each time he named a consequence rather than an opinion.",
                 "quotes": [{"line": 1, "text": "I don't think it fits."},
                            {"line": 11, "text": "I'd rather agree now, so nobody's surprised on Friday."}]}],
    "between": [{"headline": "Dev's point about the test data was cut short",
                 "observation": "Dev was explaining the test data when you moved to dropping the polish. Ravi raised the same worry a moment later.",
                 "quotes": [{"line": 3, "text": "but the test data isn't ready yet, and"},
                            {"line": 4, "text": "Right, so let's drop the polish then."}]},
                {"headline": "Commitment came before capacity",
                 "observation": "You kept reconciliation because it was committed, before hearing whether it fit. The meeting then worked backwards from that.",
                 "quotes": [{"line": 2, "text": "we committed to it, so I'd like to try"}]}],
    "conduct": [{"principle": 7, "held": "held", "observation": "Ravi pushed back twice; you stayed even and agreed the rule.",
                 "quotes": [{"line": 12, "text": "Okay. If it's not there Wednesday, reconciliation moves to next sprint. Agreed."}]},
                {"principle": 11, "held": "partly", "observation": "Ravi was the colleague telling you what you'd rather not hear. You came round to it, after one deferral.",
                 "quotes": [{"line": 10, "text": "let's see where we are on Wednesday"}]}],
    "no_moment": [],
    "next": [{"title": "Ask Dev to finish the test-data point", "detail": "At stand-up, ask Dev what he was about to say about the test data.",
              "why": "His point was cut short.", "due": "+1"},
             {"title": "Write down Wednesday's reconciliation rule", "detail": "Post the agreed rule in the sprint channel so it's not re-argued on Friday.",
              "why": "Ravi asked for it to be settled.", "due": "+1"}],
    "limits": "One voice isn't named. Tone is inferred from words.",
}

DINNER_REVIEW = {  # made before 6 Oct: no "one thing", no held / partly / missed
    "summary": "Meera asked about Saturday and reminded you of last time's phone calls. You agreed to keep the phone away and to call the restaurant.",
    "reading": [{"person": "Meera", "headline": "Wants you present, not just there",
                 "observation": "Meera's questions were practical, but the reminder about lunch was the point: she wants the day without work in it.",
                 "quotes": [{"line": 2, "text": "you were on the phone all through lunch"},
                            {"line": 4, "text": "can you call the restaurant? I did it last time."}]}],
    "between": [],
    "conduct": [{"principle": 1, "observation": "You named the morning call, then put the evening first.",
                 "quotes": [{"line": 1, "text": "I might have a call in the morning but the evening is fine"}]}],
    "no_moment": [],
    "next": [{"title": "Call the restaurant before 10", "detail": "Book the table for six on Saturday evening.", "why": "You said tomorrow morning.", "due": "+1"}],
    "limits": "Text only.",
}

# Two weeks of everyday conversations for the fillers trend: the owner's fillers fall over the fortnight,
# and run higher with Asha (his manager) than with the team or at home. Made up, like everything here.
JAY_SAYS = [
    "the retry service is nearly done but the ledger team changed their API last week",
    "we should get a firm date from the platform team before we commit to anything",
    "I think the reconciliation story fits if we drop the dashboard polish",
    "let me check with Farhan and come back to you by Thursday",
    "the test data is the real risk here and nobody owns it yet",
    "I want Dev's work on the retry logic to be visible in the update",
    "we can keep the scope if the endpoint ships by the fifteenth",
    "I'll write down what we agreed so nobody is surprised on Friday",
    "the weekend works for me as long as I keep the phone in the car",
    "I'll call the restaurant tomorrow morning before ten",
]
THEY_SAY = [
    "Where are we on that, and what do you need from me?",
    "Okay, and what's the risk if it slips again?",
    "I'd rather agree on it now than argue on Friday.",
    "Can you send me something in writing by Thursday?",
    "That makes sense. Who owns the next step?",
    "Are you coming on Saturday, or is there a call again?",
]
FILLERS_SAID = ["um", "you know", "kind of", "basically", "uh", "sort of"]
PEOPLE_BY_DAY = ["Asha", "Ravi", "Meera", "Asha", "Dev", "Ravi", "Asha", "Meera", "Dev", "Ravi", "Asha", "Ravi",
                 "Dev", "Asha", "Meera", "Ravi", "Asha", "Dev"]  # one a day, the oldest 23 days ago


def _with_fillers(rng, sentence: str, rate: float) -> str:
    out = []
    for i, w in enumerate(sentence.split()):
        if rng.random() < rate / 100:
            out.append(rng.choice(FILLERS_SAID) + ",")
        if i and rng.random() < rate / 400:  # a restart: "we, we should" (octo/delivery.py)
            out.append(w + ",")
        out.append(w)
    text = " ".join(out)
    return ("So " + text if rng.random() < 0.4 else text[0].upper() + text[1:]) + "."


def _everyday(i: int, other: str) -> list[tuple[str, str]]:
    import random
    rng = random.Random(i)
    days_in = i / (len(PEOPLE_BY_DAY) - 1)  # 0 → 1 across the fortnight
    rate = (7.5 - 4.0 * days_in) * (1.4 if other == "Asha" else 1.0)
    script = []
    for k in range(12):
        if k % 2 == 0:
            script.append(("B", rng.choice(THEY_SAY)))
        else:
            script.append(("A", " ".join(_with_fillers(rng, rng.choice(JAY_SAYS), rate) for _ in range(4))))
    return script


EVERYDAY_REVIEW = {
    "summary": "An everyday conversation from the demo.", "one_thing": None, "reading": [],
    "between": [{"headline": "A point left open", "observation": "Made up for the demo.", "quotes": [{"line": 0, "text": "?"}]}],
    "conduct": [], "no_moment": [], "next": [], "limits": "Made up.",
}

# The coach's notes (octo/coach.py), as Claude might answer, then checked like a real one. The ids are
# the prompt's: c1 is the newest conversation with a review (Asha, today), c2 sprint planning, c3 dinner.
EARLIER_NOTE = {
    "work_on": {"theme": "When someone asks you to settle something, you let it wait",
                "why": "Ravi asked for the fallback rule up front and got \"let's see on Wednesday\" before you agreed. Meera had to ask "
                       "you to call the restaurant because she did it last time. Each time the other person carried the open question "
                       "for longer than they needed to.",
                "evidence": ["c2.o", "c2.p11", "c3.r0"],
                "try": {"when": "When someone asks you to decide or do something small now",
                        "instead": "Settle it in the conversation, or say exactly when you will"},
                "know": {"measure": "questions", "how": "More of your questions land before a decision is made, not after it."}},
    "keep": {"theme": "", "why": "", "evidence": []},
    "also": [],
    "changed": "",
    "limits": "Two conversations with reviews this week; the everyday ones say little. Tone is inferred from words.",
}
LATEST_NOTE = {
    "work_on": {"theme": "You hold news and decisions until you have a plan",
                "why": "With Asha you waited three days to tell her a date had moved, because you wanted a plan first; with Ravi you "
                       "put off agreeing the fallback until he asked twice. Both times the other person needed the answer before you "
                       "were ready to give it, and said so. Your third principle, not keeping one thing inside and another outside, "
                       "speaks to it directly.",
                "evidence": ["c1.o", "c1.p3", "c2.o", "c2.p11", "c1.l0"],
                "try": {"when": "When someone needs a date, a decision or a rule from you",
                        "instead": "Say what you know today and when you'll know the rest, before you go away to plan"},
                "know": {"measure": "focus",
                         "how": "After each conversation: did someone need an answer from you, and did you give what you had then?"}},
    "keep": {"theme": "You stay even when someone pushes back",
             "why": "Ravi pushed back twice and Asha told you she heard the news last; both times you accepted it and moved to "
                    "what you'd do, without defending yourself.",
             "evidence": ["c1.p7", "c2.p7"]},
    "also": [{"theme": "Offers and points from others go unused when you move fast",
              "why": "Dev's point about the test data and Asha's offer of help both dropped as you moved to the next decision.",
              "evidence": ["c1.l1", "c2.l0"]},
             {"theme": "Home plans get squeezed by work calls", "why": "Meera reminded you of last time's phone calls.",
              "evidence": ["c3.p1"]}],
    "changed": "Since the last note, the same pattern showed with Asha today in a new form: news held back rather than a rule. "
               "Your focus, settling the fallback the first time, hasn't been answered on a conversation yet, so there's "
               "nothing to say about whether practising it is changing anything.",
    "limits": "Two reviews this week carry nearly all of this; the everyday ones say little, and one voice in sprint planning "
              "isn't named. Tone is inferred from words, not heard. Treat it as a pattern to test, not a finding.",
}
COACH_USAGE = {"cost_usd_equivalent": 0.62, "duration_ms": 71000, "input_tokens": 23000, "output_tokens": 900}


def _coach(conn, now: float) -> None:
    """The two notes: an earlier one, marked and made the focus, and the latest, not opened yet."""
    from octo import coach as CO
    ids = []
    for raw, at in ((EARLIER_NOTE, now - 1.5 * 86400), (LATEST_NOTE, now - 600)):
        nid = f"coach_demo_{len(ids)}"
        conn.execute("INSERT INTO coach_notes (id, created_at, status, model) VALUES (?, ?, 'running', ?)", (nid, at, CO.MODEL))
        CO.run(conn, nid, ask=lambda *a, raw=raw: (json.loads(json.dumps(raw)), COACH_USAGE), now=at)
        conn.execute("UPDATE coach_notes SET created_at=?, finished_at=? WHERE id=?", (at, at + 71, nid))
        ids.append(nid)
    CO.rate(conn, ids[0], "work_on", "partly", "new")
    CO.seen(conn, ids[0])
    conn.execute("UPDATE focus SET coach_note=? WHERE id='foc_demo'", (ids[0],))


# key, days ago, hour, source, script, speakers (letter: name, state), review, extra
CONVERSATIONS = [
    ("asha", 0, 11.0, "device", ASHA, {"A": ("Sam", "auto"), "B": ("Asha", "confirmed")}, ASHA_REVIEW, {}),
    ("kabir", 1, 16.5, "phone", KABIR, {"A": ("Sam", "auto"), "B": ("Kabir", "suggested")}, None, {}),
    ("team", 2, 10.0, "device", TEAM, {"A": ("Sam", "confirmed"), "B": ("Ravi", "confirmed"), "C": ("Dev", "confirmed"),
                                       "D": (None, "unknown")}, TEAM_REVIEW, {"answer": ["o", "l0"], "acted": ["n1"], "let_go": ["n0"]}),
    ("team_after", 2, 10.1, "device", TEAM_AFTER, {"A": ("Sam", "confirmed"), "B": ("Ravi", "confirmed"),
                                                   "C": ("Dev", "confirmed")}, None, {"part_of": "team"}),
    ("dinner", 3, 20.5, "device", DINNER, {"A": ("Sam", "confirmed"), "B": ("Meera", "confirmed")}, DINNER_REVIEW,
     {"old": True}),
    ("short", 4, 19.0, "phone", SHORT, {"A": ("Sam", "auto"), "B": ("Meera", "confirmed")}, "short", {}),
    ("tv", 1, 21.5, "device", TV, {"B": (None, "unknown"), "C": (None, "unknown"), "D": (None, "unknown")}, None,
     {"no_words": True}),
    ("music", 2, 19.5, "device", MUSIC, {"B": (None, "unknown")}, None, {"no_words": True}),
    # Just in: being transcribed, speakers next (B3: when it'll be ready). Nothing runs in the demo, so it
    # reads "taking longer than usual" after a few minutes.
    ("fresh", 0, 0.0, "device", SHORT, {"A": ("Sam", "auto"), "B": ("Meera", "confirmed")}, None, {"processing": True}),
] + [
    (f"day{i}", 23 - i, 9.5 + (i % 5) * 2.2, "device", _everyday(i, other), {"A": ("Sam", "auto"), "B": (other, "confirmed")},
     "everyday", {"nothing_new": True})
    for i, other in enumerate(PEOPLE_BY_DAY)
]


# What the commitments step (B14) would have found: (by, to, what, line, due), due as days after the conversation.
COMMITMENTS = {
    "asha": [("A", "B", "Set up a call with Farhan and copy Asha", 11, "+1"),
             ("B", "A", "Give Sam air cover if the date moves", 16, ""),
             ("A", "B", "Send Asha a written update with both dates", 17, "+2")],
    "team": [("D", "A", "Chase the platform team for the test data", 7, "+0"),
             ("B", "A", "Review the retry logic", 15, "+2")],
    "dinner": [("A", "B", "Keep the phone in the car on Saturday", 3, ""),
               ("A", "B", "Call the restaurant", 5, "+1")],
}


def _commitments(conn, key: str, sid: str, lines: list[dict], speakers: dict, pid: dict, day: datetime) -> None:
    from octo import commitments as CM
    names = {k: v[0] or f"Speaker {k}" for k, v in speakers.items()}
    raw = {"commitments": [{"by": names[b], "to": names[t], "what": w, "quote": {"line": n, "text": lines[n]["text"]},
                            "due": _due(d, day) if d else ""} for b, t, w, n, d in COMMITMENTS[key]], "came_up": []}
    out = {"kind": "commitments", "model": "claude-opus-5-5", "engine": "claude-code", "skipped": None, "seconds": 20.0,
           "reused": 0, "usage": {"duration_ms": 20000}, "memo": {}, **CM.check(raw, {ln["id"]: ln for ln in lines}, names, {"A"}, [])}
    _run(conn, f"cm_{sid}", "commitments", sid, out, [f"dz_{sid}"])
    people = [{"speaker": k, "name": names[k], "self": k == "A", "person": pid.get(v[0])} for k, v in speakers.items()]
    conn.execute("UPDATE stage_runs SET params=? WHERE id=?", (json.dumps({"speakers": people, "open": []}), f"cm_{sid}"))
    CM.apply(conn, {"id": f"cm_{sid}", "target": sid, "output": json.dumps(out), "params": json.dumps({"speakers": people})})
    if key == "dinner":  # one already done
        conn.execute("UPDATE commitments SET state='done', state_at=? WHERE session_id=? AND what='Call the restaurant'",
                     (day.timestamp() + 86400, sid))


def _lines(script: list[tuple[str, str]], lead_s: float = 2.0) -> list[dict]:
    t, out = lead_s, []
    for i, (spk, text) in enumerate(script):
        dur = max(1.2, len(text.split()) / 2.6)
        out.append({"id": i, "speaker": spk, "start": round(t, 2), "end": round(t + dur, 2), "text": text, "uncertain": False})
        t += dur + 0.7
    return out


def _timed(lines: list[dict], seed: int) -> list[dict]:
    """The transcript's words with times, spread over each line; about half of the owner's longer lines have a
    1.3 s pause mid-sentence (octo/delivery.py reads these)."""
    import random
    rng, words = random.Random(seed), []
    for ln in lines:
        ws = ln["text"].split()
        gap_at = rng.randrange(3, len(ws) - 3) if ln["speaker"] == "A" and len(ws) > 12 and rng.random() < 0.5 else None
        if gap_at is not None and ws[gap_at].endswith((".", "?", "!")):
            gap_at = None
        gap = 1.3 if gap_at is not None else 0.0
        step, t = (ln["end"] - ln["start"] - gap) / len(ws), ln["start"]
        for i, w in enumerate(ws):
            words.append({"w": w, "start": round(t, 2), "end": round(t + step * 0.85, 2)})
            t += step + (gap if i == gap_at else 0.0)
    return [{"id": 0, "start": lines[0]["start"], "end": lines[-1]["end"], "text": "", "words": words}] if words else []


def _diarize(lines: list[dict]) -> dict:
    turns = [{"speaker": ln["speaker"], "start": ln["start"], "end": ln["end"]} for ln in lines]
    talk: dict[str, float] = {}
    for tr in turns:
        talk[tr["speaker"]] = round(talk.get(tr["speaker"], 0.0) + tr["end"] - tr["start"], 1)
    return {"model": "pyannote/speaker-diarization-3.1", "clustering_threshold": 0.705, "speakers": sorted(talk),
            "talk_s": talk, "turns": turns, "overlapping_turns": turns, "lines": lines}


def _audio(path: Path, lines: list[dict], length_s: float, n: int = 0) -> None:
    """A tone per speaker; the owner's (A) glides over a few semitones a line and is a dB or two off the others,
    by conversation, so the voice rows (B27) have something to compare."""
    x = np.zeros(int(length_s * RATE), dtype=np.float32)
    for ln in lines:
        a, b = int(ln["start"] * RATE), int(ln["end"] * RATE)
        t = np.arange(b - a) / RATE
        me = ln["speaker"] == "A"
        st = (3 + (n * 5) % 7) * (t / max(t[-1], 1e-3) - 0.5) if me and len(t) else 0.0
        f = PITCH.get(ln["speaker"], 200.0) * 2 ** (st / 12)
        amp = 0.04 * 10 ** (((n * 3) % 5 - 2) / 20) if me else 0.04
        x[a:b] = amp * np.sin(2 * np.pi * np.cumsum(f * np.ones_like(t)) / RATE) * np.minimum(1, np.minimum(t, t[::-1]) * 20)
    A.write(path, x)


def _due(v: str, day: datetime) -> str:
    return (day + timedelta(days=int(v[1:]))).date().isoformat() if v.startswith("+") else v


def _run(conn, rid: str, stage: str, target: str, output: dict, upstream: list[str] = ()) -> None:
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, output, "
                 "queued_at, started_at, finished_at) VALUES (?, ?, ?, 'demo', 'done', 1, ?, '{}', ?, 0, 0, 0)",
                 (rid, stage, target, json.dumps(list(upstream)), json.dumps(output)))


def build(now: float | None = None, new_lab: bool = False) -> Path:
    """A fresh demo database under data/demo/. Never touches data/octo.db. `new_lab`: as a buyer's lab
    is before its owner chooses a model (Claude Code or a key: nothing is chosen for them); otherwise like the owner's."""
    now = now or time.time()
    DEMO.mkdir(parents=True, exist_ok=True)
    for f in [*DEMO.glob("octo.db*"), DEMO / "providers.json", DEMO / "rubric.md"]:
        f.unlink(missing_ok=True)
    conn = connect(DB)
    # Which lab the demo is (octo/labconfig.py): a buyer's new one, or one like the owner's.
    conn.execute("INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES ('lab_origin', ?, 0)",
                 ('"product"' if new_lab else '"first"',))
    conn.execute("DELETE FROM app_config WHERE key IN ('claude_code', 'voiceprints_need_consent')")
    rubric = RUBRIC.read_text()
    items = principles(rubric)
    pid = {}
    for name, me in PEOPLE.items():
        pid[name] = f"per_{name.lower()}"
        conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES (?, ?, ?, 0)", (pid[name], name, int(me)))
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    ids: dict[str, str] = {}
    for n, (key, ago, hour, source, script, speakers, review, extra) in enumerate(CONVERSATIONS):
        day = today - timedelta(days=ago)
        start = (day + timedelta(hours=hour)).timestamp() if ago else now - 2 * 3600  # today's: two hours ago
        stamp = day.strftime("%Y%m%d")
        sid, rec = f"ses_{stamp}_{n:04x}de30", f"rec_{stamp}_{n:04x}de30"  # n: unique across the demo
        lines = _lines(script)
        length = lines[-1]["end"] + 3.0
        path = DEMO / "audio" / f"{rec}.flac"
        _audio(path, lines, length, n)
        conn.execute("INSERT INTO recordings (id, source, path, started_at, duration_s, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (rec, source, str(path.relative_to(ROOT)), start, length, start))
        speech = sum(ln["end"] - ln["start"] for ln in lines)
        ids[key] = sid
        if extra.get("part_of"):  # B35: grouped by the lab as the second part of one discussion
            conn.execute("INSERT INTO parts (first_id, second_id, by, kind, created_at) VALUES (?, ?, 'lab', 'recordings', ?)",
                         (ids[extra["part_of"]], sid, start + 60))
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at, in_gate) "
                     "VALUES (?, ?, 0, ?, ?, 'active', ?, 1)", (sid, rec, length, round(speech, 1), start))
        dz = _diarize(lines)
        if extra.get("no_words"):
            dz["lines"] = []
        _run(conn, f"fc_{rec}", "find_conversations", rec, {"conversations": [{"start": 0.0, "end": length, "speech": []}]})
        _run(conn, f"tr_{sid}", "transcribe", sid, {"language": "en", "language_votes": {"en": 0.93, "hi": 0.04},
                                                   "model": "Qwen/Qwen3-ASR-1.7B", "segments": _timed(dz["lines"], n),
                                                   "rejected_as_silence": []})
        _run(conn, f"sp_{sid}", "speakers", sid, {"model": "pyannote/speaker-diarization-3.1"})
        _run(conn, f"dz_{sid}", "diarize", sid, dz, [f"tr_{sid}", f"sp_{sid}"])
        _run(conn, f"id_{sid}", "identify", sid, {"jay_present": True, "speakers": [
            {"speaker": k, "match": ({"person_id": pid[v[0]], "name": v[0], "similarity": 0.82, "ambiguous": False,
                                      "alternatives": []} if v[0] else None), "speech_s": dz["talk_s"][k]}
            for k, v in speakers.items()]}, [f"dz_{sid}"])
        _run(conn, f"nu_{sid}", "numbers", sid, count(dz, {"min_overlap_s": 0.3}), [f"dz_{sid}"])
        for letter, (name, state) in speakers.items():
            conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, similarity, diarize_run) "
                         "VALUES (?, ?, ?, ?, 0.82, ?)", (sid, letter, pid[name] if name else None, state, f"dz_{sid}"))
        if extra.get("processing"):  # only transcribe has started; the rest is still to come
            conn.execute("DELETE FROM stage_runs WHERE target=? AND stage != 'transcribe'", (sid,))
            conn.execute("DELETE FROM session_speakers WHERE session_id=?", (sid,))
            conn.execute("UPDATE stage_runs SET status='running', is_current=0, output=NULL, started_at=? WHERE id=?",
                         (time.time() - 20, f"tr_{sid}"))
            conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, queued_at) "
                         "VALUES (?, 'speakers', ?, 'demo', 'queued', 0, '[]', '{}', ?)", (f"sp_{sid}", sid, time.time() - 20))
            continue
        if review is None:
            continue  # speakers to confirm: the review waits
        names = {k: v[0] for k, v in speakers.items() if v[0]}
        out = {"kind": "review", "model": "claude-opus-5-5", "engine": "claude-code", "effort": "high",
               "rubric": rubric_version(rubric), "skipped": None, "summary": "", "one_thing": None, "reading": [],
               "between": [], "conduct": [], "no_moment": [], "dropped": [], "beneath": [], "next": [], "limits": "",
               "seconds": 74.0, "reused": 0, "usage": {"duration_ms": 74000}, "memo": {}}
        if review == "short":
            out["skipped"] = "too short: you spoke for 3 s (insights need 20 s)"
        else:
            raw = json.loads(json.dumps(EVERYDAY_REVIEW if review == "everyday" else review))
            if review == "everyday":  # its quote: the first thing the other person said
                raw["between"][0]["quotes"][0]["text"] = lines[0]["text"]
            for step in raw["next"]:
                step["due"] = _due(step["due"], day)
            out.update(RA.check(raw, items, {ln["id"]: ln for ln in lines}, names, {"A"}))
            if extra.get("old"):  # a review from before 6 Oct
                out.pop("one_thing", None)
                for f in out["conduct"]:
                    f.pop("held", None)
        if key == "asha":  # B15: the intention it took, before the hash below, and what the review said of it
            text = "Tell her about a risk the day I see it"
            conn.execute("INSERT INTO intentions (id, person_id, text, created_at, session_id, taken_at) VALUES ('int_demo', ?, ?, ?, ?, ?)",
                         (pid["Asha"], text, start - 3 * 86400, sid, start + 60))
            out["intention"], _ = RA.check_intentions({"intention": [{"person": "Asha", "happened": "no", "observation":
                "You held the ledger change from Tuesday until she asked, waiting to have a plan first.",
                "quotes": [{"line": 5, "text": lines[5]["text"]}]}]}, [{"person": "Asha", "text": text}], {ln["id"]: ln for ln in lines}, {"A"})
            # B16: a double tap on the recorder just after it, before the hash below (the review read it)
            conn.execute("INSERT INTO marks (id, at, source, boot, at_ms, created_at) VALUES ('mark_demo', ?, 'recorder', 1, 1, ?)",
                         (start + lines[5]["end"] + 2, start + 600))
        run = f"in_{sid}"
        _run(conn, run, "insight", sid, out, [f"dz_{sid}"])
        conn.execute("UPDATE stage_runs SET finished_at=? WHERE id=?", (start + 300, run))  # reviewed five minutes after
        # The hash the planner would want now, so the review doesn't read as made before later changes.
        conn.execute("UPDATE stage_runs SET input_hash=? WHERE id=?", (wanted(conn, STAGES["insight"], sid, [f"dz_{sid}"])[1], run))
        if key in COMMITMENTS:
            _commitments(conn, key, sid, lines, speakers, pid, day)
        if key == "team":  # B15: a reflection, and an intention waiting for the next conversation with Asha
            conn.execute("INSERT INTO reflections (session_id, text, updated_at) VALUES (?, ?, ?)",
                         (sid, "Felt rushed. I kept going back to the dates and didn't really hear Dev's worry.", start + 3000))
            conn.execute("INSERT INTO intentions (id, person_id, text, created_at) VALUES ('int_demo2', ?, ?, ?)",
                         (pid["Asha"], "Thank her for the air cover before anything else", now - 600))
        if extra.get("answer"):
            R.answer(conn, sid, run, extra["answer"], False, now=start + 3600)
        if extra.get("nothing_new"):
            R.answer(conn, sid, run, [], True, now=start + 3600)
        for item in extra.get("acted", []):
            conn.execute("INSERT OR IGNORE INTO feedback (insight_run, item, session_id, updated_at) VALUES (?, ?, ?, 0)", (run, item, sid))
            conn.execute("UPDATE feedback SET acted=1 WHERE insight_run=? AND item=?", (run, item))
        for item in extra.get("let_go", []):
            conn.execute("INSERT OR IGNORE INTO feedback (insight_run, item, session_id, updated_at) VALUES (?, ?, ?, 0)", (run, item, sid))
            conn.execute("UPDATE feedback SET let_go_at=? WHERE insight_run=? AND item=?", (start + 7200, run, item))
        if extra.get("old"):  # rated item by item, the rule before 6 Oct
            for it in R.items(out):
                conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, novelty, updated_at) "
                             "VALUES (?, ?, ?, 'right', ?, 0)", (run, it["id"], sid, "new" if it["id"] == "r0" else "knew"))
            R.mark_reviewed_if_done(conn, sid, run)
        if key == "team":  # the focus, from this review, active since
            conn.execute("INSERT INTO focus (id, text, session_id, insight_run, item, started_at) VALUES "
                         "('foc_demo', 'Settle the fallback the first time someone asks for it', ?, ?, 'o', ?)",
                         (sid, run, start + 4000))
    _drills(conn, now)
    _rehearsal(conn, now, pid["Asha"], items)
    _events(conn, today, now)
    _coach(conn, now)
    conn.close()
    return DB


DRILL = ("The answer is yes, we can ship the retry service on Friday. The reason is the ledger team fixed their API "
         "yesterday and our tests pass against it. The one risk left is the load test, which runs tomorrow morning, "
         "and if it fails we move the date by two days, not more. What I need from you is a yes on the two-day buffer today, "
         "so I can tell finance the date with the buffer in it instead of a date we might miss.")


def _drills(conn, now: float) -> None:
    """B18: six practice takes on C7's prompt, one a day, fillers thinning out; the newest is today's."""
    import random
    from octo import drills as DR
    for i in range(6):
        at = now - (5 - i) * 86400 - 3600
        text = _with_fillers(random.Random(i), DRILL, 9 - i)
        lines = _lines([("A", text)], lead_s=1.0)
        did = f"drl_demo{i}"
        path = DEMO / "drills" / f"{did}.flac"
        _audio(path, lines, lines[-1]["end"] + 1.0)
        conn.execute("INSERT INTO drills (id, prompt, path, duration_s, created_at, status, transcript) VALUES (?, ?, ?, ?, ?, 'done', ?)",
                     (did, DR.PROMPTS_DEFAULT[0], str(path.relative_to(ROOT)), lines[-1]["end"] + 1.0, at,
                      json.dumps([{**seg, "text": text} for seg in _timed(lines, 100 + i)])))


REHEARSAL = [("me", "Asha, before anything else: thank you for the air cover last week."),
             ("them", "Of course. But I'd still rather have heard about the ledger change on Tuesday."),
             ("me", "You're right. Next time I'll tell you the day I see a risk, even before I have a plan."),
             ("them", "Good. What's the date now?")]


def _demo_reply(prompt: str) -> str:
    """The demo's other person: a reply that fits a rehearsal well enough, no model."""
    n = prompt.count("\n[")
    return ("I hear you. What do you need from me?", "Hm. Why am I only hearing this now?",
            "Okay. And when will I know for sure?", "That works. Put it in writing for me.")[n % 4]


def _demo_review(prompt: str) -> dict:
    """The demo's review of a rehearsal, quoting the owner's first line so it passes the real check."""
    import re
    first = re.search(r"^\[(\d+)\] Sam: (.+)$", prompt, re.M)
    q = [{"line": int(first[1]), "text": " ".join(first[2].split()[:6])}] if first else []
    return {"summary": "You opened plainly and stayed with what she needed.", "no_moment": [],
            "one_thing": {"headline": "Say the risk before the reasons", "why": "She asks for the date each time.", "quotes": q, "next": 0},
            "conduct": [{"principle": 1, "held": "partly", "observation": "You owned it, then explained for a while.", "quotes": q}],
            "next": [{"title": "Open with the new date", "detail": "Say the date first, then the one reason it moved.", "why": "It's what she asks."}],
            "limits": "Typed, so tone and timing aren't here; the real Asha may answer differently."}


def _rehearsal(conn, now: float, asha: str, items: list[dict]) -> None:
    """B26: a rehearsal with Asha yesterday, reviewed."""
    turns = [{"by": b, "text": t, "at": now - 86400 + 30 * i, **({"reader": {"kind": "claude-code"}} if b == "them" else {})}
             for i, (b, t) in enumerate(REHEARSAL)]
    lines = [{"id": i, "speaker": t["by"], "start": 0.0, "text": t["text"]} for i, t in enumerate(turns)]
    prompt = "\n".join(f"[{i}] {'Sam' if t['by'] == 'me' else 'Asha'}: {t['text']}" for i, t in enumerate(turns))
    review = RA.check(_demo_review(prompt), items, {ln["id"]: ln for ln in lines}, {"me": "Sam", "them": "Asha"}, {"me"})
    conn.execute("INSERT INTO rehearsals (id, person_id, hard, want, turns, created_at, updated_at, review, review_at) "
                 "VALUES ('reh_demo', ?, ?, ?, ?, ?, ?, ?, ?)",
                 (asha, "She'll ask why she heard about the slip late", "Thank her for the air cover before anything else",
                  json.dumps(turns), now - 86400, now - 86400 + 200, json.dumps(review), now - 86400 + 200))


def _events(conn, today: datetime, now: float) -> None:
    """B23: the team call's calendar event (its fourth speaker is offered as Nisha) and a 1:1 with Asha tomorrow; B34's
    agenda also has today's catch-up with Asha (the conversation two hours ago) and a design review later."""
    from octo import events as EV
    team = (today - timedelta(days=2) + timedelta(hours=10)).timestamp()
    EV.store(conn, "mac", [
        {"uid": "demo-team", "title": "Retry service sync", "start": team - 120, "end": team + 1800,
         "invitees": [{"name": "Ravi", "email": "ravi@example.com"}, {"name": "Dev Malhotra", "email": "dev@example.com"},
                      {"name": "Nisha Rao", "email": "nisha@example.com"}]},
        {"uid": "demo-asha", "title": "1:1 with Asha", "start": (today + timedelta(days=1, hours=11)).timestamp(),
         "end": (today + timedelta(days=1, hours=11, minutes=30)).timestamp(), "invitees": [{"name": "Asha", "email": ""}]},
        {"uid": "demo-catchup", "title": "Catch-up", "start": now - 2 * 3600 - 300, "end": now - 2 * 3600 + 1500,
         "invitees": [{"name": "Asha", "email": ""}]},
        {"uid": "demo-design", "title": "Design review", "start": now + 3 * 3600, "end": now + 4 * 3600,
         "invitees": [{"name": "Dev Malhotra", "email": "dev@example.com"}, {"name": "Lena Ortiz", "email": "lena@example.com"}]},
    ], now - 7 * 86400, now + 7 * 86400, now)


def _demo_recorder() -> None:
    """A made-up recorder for Settings → Recorder: paired, its filler cue on, a better cue waiting (B22). Nothing reaches
    a real recorder or the lab's data/: the loop's files are beside the demo, and Install only pretends."""
    from octo import device as D
    from octo import filler_loop as FLP
    st = {"state": "stopped", "fw": "rec-27", "filler_model": True, "filler_on": True, "filler_threshold": 0.96,
          "filler_ready": True, "fillers": 0, "filler_models": True, "filler_name": "you know", "filler_before": ""}
    D.load_config = lambda: {"key": "demo"}
    D.Device.from_config = classmethod(lambda cls: None)
    D.networks_wifi = lambda dev, op: ["Home", "Office"]
    D.last_status = lambda: {"status": st, "seen_at": time.time()}
    D.save_status = lambda new, error=None: None
    D.set_filler = lambda dev, on, threshold=None: st.update(filler_on=on, **({"filler_threshold": threshold} if threshold else {})) or st
    d = DEMO / "filler"
    real = FLP.FILLER
    for name in ("MODEL", "MODEL_META", "MODEL_BEFORE", "CANDIDATE", "CUE_LOG", "TRAIN_STATE", "INSTALL_STATE", "JOB"):
        setattr(FLP, name, d / getattr(FLP, name).relative_to(real))
    FLP.FILLER = d
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    (d / "candidate").mkdir(parents=True)
    (d / "octo_filler_model.h").write_text("// the demo's model\n")
    (d / "candidate" / "octo_filler_model.h").write_text("// the demo's better model\n")
    week = time.time() - 2 * 86400
    bench = {"conversations": 31, "before_conversations": 22, "his": 448, "before_mean": 0.62, "now_mean": 0.71,
             "better": True, "threshold": 0.95}
    (d / "candidate" / "candidate.json").write_text(json.dumps({"made_at": week, "threshold": 0.95, "conversations": [],
                                                                "bench": bench}))
    (d / "train.json").write_text(json.dumps({"started_at": week, "finished_at": week + 3600, "running": False,
                                              "bench": bench, "candidate": True}))
    FLP.live = lambda conn, days=7, now=None: {"days": 7, "conversations": 9, "cues": 41, "right": 33, "his": 96,
                                               "caught": 52, "waiting": 1}
    FLP._flash = lambda log: time.sleep(6)  # the demo's "update"
    FLP.start_job = _demo_training  # B29: Train only pretends, a minute from start to a result
    FLP._set_threshold = lambda t: st.update(filler_threshold=t)
    # rec-27 (B29): a model as data takes seconds; the recorder keeps the one before for Undo
    FLP.pack = lambda header, name: name.encode()

    def send(blob, log):
        time.sleep(3)
        st.update(filler_before=st["filler_name"], filler_name=blob.decode())

    def send_undo(log):
        time.sleep(3)
        st.update(filler_before=st["filler_name"], filler_name=st["filler_before"])
        return st
    FLP._send, FLP._send_undo = send, send_undo


def _demo_training(target: str) -> None:
    """The demo's training job: a sleeping process holds the job slot (so Cancel works as it does in a lab) while a
    thread walks its state through the steps to a made-up result. Nothing is trained."""
    import subprocess
    import sys
    import threading
    from octo import filler_loop as FLP
    if FLP.job():
        raise FLP.JobRefused(f"“{FLP.job()['target']}” is training: one at a time")
    pr = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"], start_new_session=True)
    t0 = time.time()
    FLP._write(FLP.JOB, {"target": target, "pid": pr.pid, "started_at": t0})
    path, cand = FLP.paths(target)

    def walk() -> None:
        st = {"target": target, "started_at": t0, "running": True, "step": "gathering"}
        FLP._write(path, st)
        time.sleep(4)
        st.update(step="testing", done=0, trainings=9, conversations=31, examples=453)
        for k in range(1, 10):
            if not FLP.job():
                return  # cancelled
            st["done"] = k
            FLP._write(path, st)
            time.sleep(6)
        st.update(step="small")
        FLP._write(path, st)
        time.sleep(4)
        cand.mkdir(parents=True, exist_ok=True)
        (cand / "octo_filler_model.h").write_text("// the demo's model\n")
        FLP._write(cand / "candidate.json", {"made_at": time.time(), "target": target, "threshold": 0.95, "conversations": [],
                                             "bench": {"conversations": 31, "before_mean": 0.0, "now_mean": 0.61}})
        FLP._write(path, {**st, "running": False, "step": "done", "finished_at": time.time(), "candidate": True,
                          "bench": {"conversations": 31, "before_conversations": 0, "before_mean": 0.0, "now_mean": 0.61}})
        pr.kill()
        FLP.JOB.unlink(missing_ok=True)
    threading.Thread(target=walk, daemon=True).start()


def serve(port: int, new_lab: bool = False, phone_port: int | None = None, mac_key: bool = False,
          tailscale: str = "on") -> None:
    import uvicorn
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    import threading

    from octo import api as OA
    from octo import claude_code as CC
    from octo import coach as CO
    from octo import fillers as FL
    from octo import privacy as PV
    from octo import providers as PR
    PV.CLIPS = DEMO / "clips"  # playback slices beside the demo, never in audio/lab
    from octo import drills as DR
    DR.FOLDER = DEMO / "drills"  # practice takes recorded in the demo stay beside it (B18)
    from octo import tone as TO
    TO.CACHE = DEMO / "tone.json"  # voice numbers (B27) beside the demo, never in data/
    PR.CONFIG = DEMO / "providers.json"  # keys typed into the demo stay beside it, never in data/providers.json
    from octo import insight as IN
    from octo import rubric as RB
    RB.OWN = DEMO / "rubric.md"  # principles saved in the demo stay beside it, never in pipeline/rubric.md
    if new_lab:
        IN.RUBRIC = RB.TEMPLATE  # a buyer's lab starts with no principles
    else:
        RB.OWN.write_text(IN.RUBRIC.read_text())
    _demo_recorder()
    OA.Processor._loop = lambda self: None  # nothing is processed in the demo
    CC.claude_bin = lambda: "demo"  # the coach can be asked; its answer is the demo's own, after a pause
    CC.status = lambda: {"path": "demo", "version": "2.1.287", "version_ok": True, "logged_in": True, "auth": "claude.ai",
                         "plan": "pro", "fix": None}  # Settings' Claude Code check: a made-up Mac that's ready
    from octo import rehearse as RH

    def rehearse(who, effort, system, prompt, schema):  # B26: the other person and the review are the demo's own
        time.sleep(2)
        return ({"reply": _demo_reply(prompt)} if "reply" in schema["properties"] else _demo_review(prompt)), {}
    RH._ask = rehearse
    from octo import talk as TK

    def talk(who, effort, system, prompt, schema):  # B31: a pretend reply, citing the transcript's first two lines
        time.sleep(4)
        got = re.findall(r"^\[(\d+)\] \S+ [^:]+: (.+)$", prompt, re.M)[:2]
        return {"reply": "A pretend reply: the demo lab calls no model.\n\nIn the real lab the review's model reads the whole "
                         "conversation, its review and your principles, and answers what you asked from its lines.",
                "moments": [{"line": int(n), "text": " ".join(t.split()[:6])} for n, t in got]}, {}
    TK._ask = talk
    from octo import prepare as PP

    def prepare(who, effort, system, prompt, schema):  # B36: a pretend plan, quoting the first line of each conversation sent
        time.sleep(4)
        firsts, key = [], None
        for ln in prompt.splitlines():
            if m := re.match(r"(c\d+) · ", ln):
                key = m[1]
            elif key and (m := re.match(r"\[(\d+)\] \S+ [^:]+: (.+)", ln)):
                firsts.append({"conversation": key, "line": int(m[1]), "text": " ".join(m[2].split()[:6])})
                key = None
        return {"aim": "Listen first, and understand what matters most to them right now.",
                "why": "A pretend plan: the demo lab calls no model.",
                "thinking": [{"headline": "What the lines show (pretend)", "observation": "In the real lab the review's model reads "
                              "these lines and says how they seem to think.", "quotes": firsts[:2]}],
                "open": [{"headline": "Something unresolved (pretend)", "observation": "What's still open around them.", "quotes": firsts[2:3]}],
                "structure": [{"minutes": 5, "part": "Hello", "detail": "Who you are, and why you asked to meet."},
                              {"minutes": 15, "part": "Their view", "detail": "Mostly listen."},
                              {"minutes": 5, "part": "Close", "detail": "Say back what you heard; name one next step you'll own."}],
                "questions": [{"purpose": "Their view", "items": ["What does good look like for you a year from now?"]}],
                "careful": [], "limits": "A pretend plan."}, COACH_USAGE
    PP._ask = prepare

    def coach(note_id: str) -> None:
        def go() -> None:
            time.sleep(6)
            CO.run(connect(DB), note_id, ask=lambda *a: (json.loads(json.dumps(LATEST_NOTE)), COACH_USAGE))
        threading.Thread(target=go, daemon=True).start()

    from octo import ask as AK

    def asker(ask_id: str) -> None:  # B24: no model in the demo; it quotes the first line sent of up to three conversations
        def fake(model, effort, system, prompt, schema):
            claims, key = [], None
            for ln in prompt.splitlines():
                if m := re.match(r"(c\d+) · ", ln):
                    key = m[1]
                elif key and (m := re.match(r"\[(\d+)\] \S+ ([^:]+): (.+)", ln)) and len(claims) < 3:
                    claims.append({"text": f"{m[2]} said this (the demo's pretend answer: it quotes, it doesn't read).",
                                   "quotes": [{"conversation": key, "line": int(m[1]), "text": " ".join(m[3].split()[:8])}]})
                    key = None
            return {"claims": claims, "not_found": "", "limits": "A pretend answer: the demo lab calls no model."}, COACH_USAGE
        threading.Thread(target=lambda: (time.sleep(4), AK.run(connect(DB), ask_id, ask=fake)), daemon=True).start()

    server = None

    @asynccontextmanager
    async def lifespan(_app):
        if server:
            await server.start()
        yield
        if server:
            await server.stop()

    app = FastAPI(title="octo demo lab", lifespan=lifespan)
    app.include_router(OA.make_router(lambda: connect(DB), coach=coach, asker=asker))
    from octo.phone_api import security_headers
    security_headers(app)  # as the real lab, so a page that breaks under it breaks here first
    if mac_key:
        # As the real lab: /api only with the Mac's key. The demo's own key, beside it; let in with
        # http://127.0.0.1:PORT/let-in?k=<data/demo/mac.key>
        from octo import mackey as MK
        from octo import phone_api as PA
        MK.FILE = DEMO / "mac.key"
        PA.require_mac_key(app)
    if phone_port:
        # The phone listener on its own port (not the real lab's 8766), its certificates beside the demo: the
        # Octo app can pair with this made-up lab and show it, at home or through Tailscale.
        from octo import phone as PH
        from octo.phone_api import install
        PH.DIR = DEMO / "phone"
        PH.PORT = phone_port
        # Made-up Tailscale (the Phone page's "Away from home"), never this Mac's own: its peers are real devices.
        seen = time.time() - 3 * 86400
        PH.tailscale_status = lambda: {
            "none": {"installed": False, "running": False, "state": None, "ips": [], "name": None, "account": None,
                     "phones": [], "asked": False},
            "stopped": {"installed": True, "running": True, "state": "Stopped", "ips": [], "name": "Sam's MacBook Air",
                        "account": "sam@example.com", "phones": [{"name": "Sam's iPhone", "online": False, "last_seen": seen}],
                        "asked": True},
            "on": {"installed": True, "running": True, "state": "Running", "ips": ["100.101.102.103"],
                   "name": "Sam's MacBook Air", "account": "sam@example.com",
                   "phones": [{"name": "Sam's iPhone", "online": True, "last_seen": None}], "asked": True},
        }[tailscale]
        server = install(app, lambda: connect(DB))
        PH.set_enabled(True)
    dist = ROOT / "ui" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    print(f"demo lab on http://127.0.0.1:{port}  (made-up data in {DEMO.relative_to(ROOT)})"
          + (f"; phones on port {phone_port} (#/phone shows the code)" if phone_port else ""))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--new-lab", action="store_true", help="as a buyer's lab before a model is chosen")
    ap.add_argument("--phone", type=int, metavar="PORT", help="also serve phones (the Octo app) on this port, e.g. 8796")
    ap.add_argument("--tailscale", choices=("none", "stopped", "on"), default="on",
                    help="with --phone: the made-up Tailscale the Phone page sees")
    ap.add_argument("--mac-key", action="store_true", help="ask for the Mac's key as the real lab does (its own, in data/demo)")
    a = ap.parse_args()
    from octo import fillers as FL
    FL.LAB_START = (datetime.now() - timedelta(days=30)).date()  # the demo's month is "since the lab began"
    build(new_lab=a.new_lab)
    if not a.build_only:
        serve(a.port, new_lab=a.new_lab, phone_port=a.phone, mac_key=a.mac_key, tailscale=a.tailscale)


if __name__ == "__main__":
    main()
