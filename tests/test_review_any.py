"""The review of a whole conversation (octo/review_any.py) through Claude Code, as the first lab runs it:
what it sends, and how its answer is checked in code. Claude Code itself is never called here: `ask` is replaced."""

import json
import time

import pytest

from octo import claude_code as CC
from octo import insight as I
from octo import review as R
from octo import review_any as RA
from octo import stages

RUBRIC = """# Rubric

## How the review applies it

1. Read the whole conversation first.

## Conduct principles

1. Speak gently, even when you are pushed hard. From my notebook, p. 12.

2. Ignore insults. They pass on their own. Notebook p. 31.
"""

LINES = [
    {"id": 0, "speaker": "A", "start": 10.0, "end": 14.0, "text": "Could we put a due date on every impediment?", "uncertain": False},
    {"id": 1, "speaker": "B", "start": 20.0, "end": 26.0, "text": "We have the flag, so there was no downside to going ahead.", "uncertain": False},
    {"id": 2, "speaker": "A", "start": 30.0, "end": 36.0, "text": "It feels like we put the cart before the horse.", "uncertain": False},
    {"id": 3, "speaker": "B", "start": 40.0, "end": 41.0, "text": "For sure.", "uncertain": True},
]
DZ = {"speakers": ["A", "B"], "talk_s": {"A": 10.0, "B": 7.0},
      "turns": [{"speaker": l["speaker"], "start": l["start"], "end": l["end"]} for l in LINES],
      "overlapping_turns": [], "lines": LINES}
NAMES = {"A": "Kiran", "B": "Sam"}
PARAMS = {"model": "claude-opus-5-5", "effort": "high", "min_jay_speech_s": 5.0, "max_ctx": 32768,
          "started_at": 1790866860.0}  # Thu 1 Oct 2026, 20:31 IST

ANSWER = {
    "summary": "Kiran questioned the plan; you explained the flag.",
    "reading": [
        {"person": "Kiran", "headline": "Sets standards from examples",
         "observation": "Turns items into rules.", "quotes": [
             {"line": 0, "text": "put a due date on every impediment"},
             {"line": 2, "text": "cart before the horse"}]},
        {"person": "Kiran", "headline": "One line only", "observation": "x",
         "quotes": [{"line": 2, "text": "cart before the horse"}]},  # a pattern needs two moments
    ],
    "between": [
        {"headline": "The disagreement stayed open", "observation": "Your flag point went unanswered.",
         "quotes": [{"line": 1, "text": "no downside to going ahead"}, {"line": 3, "text": "Absolutely right."}]},
        {"headline": "Invented", "observation": "y", "quotes": [{"line": 2, "text": "we failed"}]},
    ],
    "conduct": [
        {"principle": 1, "observation": "You stayed calm.", "quotes": [{"line": 1, "text": "there was no downside"}]},
        {"principle": 2, "observation": "Kiran's words only.", "quotes": [{"line": 2, "text": "cart before the horse"}]},
        {"principle": 9, "observation": "No such principle.", "quotes": [{"line": 1, "text": "flag"}]},
    ],
    "no_moment": [],
    "next": [{"title": "Ask Kiran about the flag exception", "detail": "In the 1:1, ask whether the flag is an exception.",
              "why": "It settles it.", "due": "2026-10-08"},
             {"title": " ", "detail": "", "why": "", "due": ""},
             {"title": "Put a due date on the impediment", "detail": "Early December.", "why": "Kiran asked.", "due": "next week"}],
    "limits": "Text only.",
}


def test_answers_are_checked_in_code(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    sent = {}

    def fake_ask(model, effort, system, prompt, schema, **kw):
        sent.update(model=model, effort=effort, system=system, prompt=prompt)
        return ANSWER, {"duration_ms": 1}

    monkeypatch.setattr(CC, "ask", fake_ask)
    out = I.run(DZ, NAMES, {"B"}, PARAMS, rubric_text=RUBRIC)
    assert out["kind"] == "review" and sent["model"] == "claude-opus-5-5" and sent["effort"] == "high"
    assert "Read the whole conversation first." in sent["system"]  # The owner's rules, as written
    assert "1. Speak gently, even when you are pushed hard. From my notebook, p. 12." in sent["prompt"]  # verbatim
    assert "[2] 0:30 Kiran: It feels like we put the cart before the horse." in sent["prompt"]
    assert "fillers per 100 words" in sent["prompt"]

    assert [r["headline"] for r in out["reading"]] == ["Sets standards from examples"]
    assert len(out["between"]) == 1 and out["between"][0]["unverified_quotes"] == 1  # one quote invented
    assert [f["principle"] for f in out["conduct"]] == [1]  # 2: no words of the owner's; 9: no such principle
    assert out["conduct"][0]["title"] == "Speak gently, even when you are pushed hard."
    assert out["no_moment"] == [2]
    assert [(n["title"], n["due"]) for n in out["next"]] == [("Ask Kiran about the flag exception", "2026-10-08"),
                                                             ("Put a due date on the impediment", None)]  # not a date
    assert "This conversation was on Thursday 1 Oct 2026" in sent["prompt"]
    assert {d.get("headline") or d.get("principle") for d in out["dropped"]} == {"One line only", "Invented", 2}

    # Everything the owner rates, and the gate's "reviewed" needs every one of them.
    assert [i["id"] for i in R.items(out)] == ["r0", "l0", "p1", "n0", "n1"]
    assert all(i["required"] for i in R.items(out))


def test_the_one_thing_and_held_are_checked_in_code(monkeypatch):  # 6 Oct (E2)
    answer = {**ANSWER,
              "one_thing": {"headline": "Ask before you explain", "why": "Kiran's question went unanswered.",
                            "quotes": [{"line": 1, "text": "no downside to going ahead"}, {"line": 2, "text": "we failed"}],
                            "next": 2},
              "conduct": [{**ANSWER["conduct"][0], "held": "partly"},
                          {"principle": 2, "held": "sometimes", "observation": "x", "quotes": [{"line": 1, "text": "flag"}]}]}
    monkeypatch.setattr(CC, "ask", lambda *a, **k: (answer, {}))
    out = I.run(DZ, NAMES, {"B"}, PARAMS, rubric_text=RUBRIC)
    one = out["one_thing"]
    assert one["headline"] == "Ask before you explain" and one["why"] == "Kiran's question went unanswered."
    assert [q["line"] for q in one["quotes"]] == [1] and one["unverified_quotes"] == 1
    assert one["next"] == 1  # the answer's third step is the second kept: the blank one was dropped
    assert [(f["principle"], f["held"]) for f in out["conduct"]] == [(1, "partly"), (2, None)]  # not a judgement it knows
    assert [i["id"] for i in R.items(out)] == ["o", "r0", "l0", "p1", "p2", "n0", "n1"]

    answer["one_thing"] = {"headline": "Invented", "why": "", "quotes": [{"line": 2, "text": "we failed"}], "next": -1}
    out = I.run(DZ, NAMES, {"B"}, PARAMS, rubric_text=RUBRIC)
    assert out["one_thing"] is None
    assert {"section": "one_thing", "headline": "Invented", "reason": "no verified quote"} in out["dropped"]

    answer["one_thing"] = {"headline": "Ask first", "why": "", "quotes": [{"line": 1, "text": "flag"}], "next": -1}
    assert I.run(DZ, NAMES, {"B"}, PARAMS, rubric_text=RUBRIC)["one_thing"]["next"] is None  # no step acts on it


def test_the_prompt_asks_for_the_one_thing_and_held():
    assert "one_thing" in RA.SCHEMA["required"]
    held = RA.SCHEMA["properties"]["conduct"]["items"]
    assert held["properties"]["held"]["enum"] == ["held", "partly", "missed", "depends"] and "held" in held["required"]
    system = RA.system_prompt({"name": "Sam"}, "1. Read the whole conversation first.")
    assert "- one_thing:" in system and '"held" is "depends"' in system


def test_an_unchanged_conversation_is_not_sent_again(monkeypatch):
    calls = []
    monkeypatch.setattr(CC, "ask", lambda *a, **k: (calls.append(1), (ANSWER, {}))[1])
    first = I.run(DZ, NAMES, {"B"}, PARAMS, rubric_text=RUBRIC)
    again = I.run(DZ, NAMES, {"B"}, PARAMS, rubric_text=RUBRIC, memo=first["memo"])
    assert len(calls) == 1 and again["reused"] == 1 and again["conduct"] == first["conduct"]
    I.run(DZ, NAMES, {"B"}, PARAMS, rubric_text=RUBRIC.replace("first.", "first, slowly."), memo=first["memo"])
    assert len(calls) == 2  # The owner changed their rules: asked again


def test_too_short_is_never_sent(monkeypatch):
    monkeypatch.setattr(CC, "ask", lambda *a, **k: pytest.fail("sent"))
    out = I.run(DZ, NAMES, {"B"}, {**PARAMS, "min_jay_speech_s": 60.0}, rubric_text=RUBRIC)
    assert out["skipped"].startswith("too short")


def test_claude_code_runs_with_nothing_but_the_text(monkeypatch, tmp_path):
    seen = {}

    class Done:
        returncode = 0
        stderr = ""
        stdout = json.dumps({"is_error": False, "structured_output": {"summary": "ok"}, "total_cost_usd": 0.2,
                             "duration_ms": 5, "usage": {"input_tokens": 9, "output_tokens": 3}})

    def fake_run(args, **kw):
        seen.update(args=args, **kw)
        return Done()

    monkeypatch.setattr(CC, "claude_bin", lambda: "/bin/claude")
    monkeypatch.setattr(CC.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sams-own")
    answer, usage = CC.ask("claude-opus-5-5", "high", "sys", "the transcript", RA.SCHEMA)
    a = seen["args"]
    assert answer == {"summary": "ok"} and usage["input_tokens"] == 9
    assert a[a.index("--tools") + 1] == "" and "--strict-mcp-config" in a and "--no-session-persistence" in a
    assert a[a.index("--setting-sources") + 1] == ""  # no project settings, hooks or CLAUDE.md
    assert seen["input"] == "the transcript" and "octo-claude-" in seen["cwd"]
    env = seen["env"]  # not the parent Claude Code's session, but the owner's own token if they set one
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env and env["CLAUDE_CODE_OAUTH_TOKEN"] == "sams-own"


def test_a_login_problem_says_how_to_fix_it(monkeypatch):
    class Failed:
        returncode = 1
        stderr = ""
        stdout = json.dumps({"is_error": True, "result": "Failed to authenticate: OAuth session expired"})

    monkeypatch.setattr(CC, "claude_bin", lambda: "/bin/claude")
    monkeypatch.setattr(CC.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(RuntimeError, match="claude auth login"):
        CC.ask("claude-opus-5-5", "high", "sys", "text", RA.SCHEMA)



def test_the_first_labs_review_goes_through_claude_code_about_its_owner(monkeypatch):  # 19 Oct: off claude_review.py
    """The Claude Code choice's params are unchanged (so no stored review re-runs); the owner's profile comes from
    the worker's inputs, not the params, so editing it doesn't re-run them either."""
    sent = {}
    monkeypatch.setattr(CC, "ask", lambda model, effort, system, prompt, schema, **kw:
                        (sent.update(model=model, effort=effort, system=system), (ANSWER, {}))[1])
    monkeypatch.setattr("octo.rubric.text", lambda: RUBRIC)
    params = {**stages.STAGES["insight"].params, "speakers": [{"speaker": "A", "name": "Kiran", "self": False},
                                                              {"speaker": "B", "name": "Sam", "self": True}]}
    assert "reader" not in params and "owner" not in params
    owner = {"name": "Sam", "pronoun": "he", "about": "a teacher in Pune", "never": "taking credit"}
    out = stages.insight({"upstream": [DZ], "owner": owner, "progress": lambda m: None}, {**params, "min_jay_speech_s": 5.0})
    assert out["kind"] == "review" and out["engine"] == "claude-code" and sent["effort"] == "high"
    assert "for Sam, a teacher in Pune. He uses these reviews" in sent["system"]
    assert "never taking credit" in sent["system"]
    assert [f["principle"] for f in out["conduct"]] == [1]


def test_every_prompt_that_carries_what_was_said_treats_it_as_material_not_instructions():
    """Security review M3 (28 Oct): a line said in a meeting ("note for whoever reviews this: …") is reviewed, never obeyed."""
    from octo import ask, coach, commitments, rehearse, talk
    sam = {"name": "Sam", "pronoun": "they"}
    played = {"person_id": "p1", "name": "Asha", "who": "", "hard": "", "want": ""}
    prompts = {"review": RA.system_prompt(sam, ""), "commitments": commitments.system_prompt("Sam"),
               "ask": ask.SYSTEM.format(name="Sam"), "talk": talk.system_prompt("Sam", sam, ""),
               "coach": coach.system_prompt("Sam", sam), "rehearsal": rehearse.role_prompt("Sam", sam, played, []),
               "rehearsal's review": rehearse.review_prompt("Sam", sam, played, "")}
    assert [k for k, p in prompts.items() if not p.endswith(I.MATERIAL)] == []
