"""Insight stage: rubric parsing, quote verification, and the run loop with a fake model."""

import pytest

from octo import insight as I

RUBRIC = """# Rubric

## How the review applies it

1. Judge the conversation against each conduct principle.

## Conduct principles

_Written by Sam._

1. Speak gently, even when you are pushed hard. From my notebook, p. 12.

2. Ignore insults. They pass on their own. Notebook p. 31.

## Doing well at work, within the principles

1.
"""

LINES = [
    {"id": 0, "speaker": "A", "start": 238.0, "text": "But what's the budget, roughly?", "uncertain": False},
    {"id": 1, "speaker": "B", "start": 252.0, "text": "I already said we'll decide on Sunday.", "uncertain": False},
    {"id": 2, "speaker": "A", "start": 260.0, "text": "You never plan anything properly.", "uncertain": False},
    {"id": 3, "speaker": "B", "start": 271.0, "text": "Can we just, let me finish.", "uncertain": True},
]
BY_ID = {ln["id"]: ln for ln in LINES}
P8 = {"n": 8, "title": "Speak gently, even when you are pushed hard.", "text": "..."}


def test_principles_are_read_from_the_conduct_section_only():
    ps = I.principles(RUBRIC)
    assert [p["n"] for p in ps] == [1, 2]  # "1." under the work section is empty and not a principle
    assert ps[0]["title"] == "Speak gently, even when you are pushed hard."
    assert ps[1]["title"] == "Ignore insults. They pass on their own."  # too-short first sentence extended
    assert "notebook, p. 12" in ps[0]["text"]  # The owner's whole paragraph goes to the model


def test_the_real_rubric_has_the_owners_seven_principles():
    """14 written 1 Oct, one removed 2 Oct, 7 kept 29 Oct. A public copy carries the empty template instead."""
    ps = I.principles(I.RUBRIC.read_text())
    if not ps:
        pytest.skip("the empty template (a public copy)")
    assert [p["n"] for p in ps] == list(range(1, 8)) and all(p["title"] for p in ps)


def test_quotes_must_really_be_in_the_cited_line():
    ok = I.verify_quote({"line": 1, "text": "I already said we’ll decide on Sunday"}, BY_ID)  # curly ', no full stop
    assert ok and ok["speaker"] == "B" and ok["start"] == 252.0
    assert I.verify_quote({"line": 1, "text": "I already said… on Sunday."}, BY_ID)  # ellipsis, in order
    assert I.verify_quote({"line": 1, "text": "on Sunday… I already said"}, BY_ID) is None  # out of order
    assert I.verify_quote({"line": 0, "text": "I already said we'll decide on Sunday."}, BY_ID) is None  # wrong line
    assert I.verify_quote({"line": 1, "text": "I told you to stop asking."}, BY_ID) is None  # invented
    assert I.verify_quote({"line": 99, "text": "anything"}, BY_ID) is None  # no such line
    assert I.verify_quote({"line": 1, "text": "..."}, BY_ID) is None  # nothing to check


def test_a_finding_needs_a_verified_quote_of_the_owners_own_words():
    raw = {"moment": True, "observation": "You cut her off.", "try_next_time": "",
           "quotes": [{"line": 0, "text": "But what's the budget, roughly?"}]}  # only Nisha's words
    kind, item = I.check_conduct(raw, P8, BY_ID, {"B"})
    assert kind == "dropped" and "your own words" in item["reason"]

    raw["quotes"].append({"line": 3, "text": "let me finish"})
    raw["quotes"].append({"line": 1, "text": "words nobody said"})
    kind, item = I.check_conduct(raw, P8, BY_ID, {"B"})
    assert kind == "finding"
    assert [q["line"] for q in item["quotes"]] == [0, 3]
    assert item["unverified_quotes"] == 1
    assert item["flags"] == ["speaker or words unsure at 4:31"]
    assert item["try_next_time"] is None


def test_no_moment_is_a_normal_answer():
    kind, item = I.check_conduct({"moment": False, "observation": "", "quotes": [], "try_next_time": ""},
                                 P8, BY_ID, {"B"})
    assert kind == "no_moment"


def test_beneath_the_surface_keeps_only_grounded_questions():
    raw = {"questions": [{"question": "Was something else on your mind?", "quotes": [{"line": 1, "text": "decide on Sunday"}]},
                         {"question": "Ungrounded?", "quotes": [{"line": 1, "text": "not there"}]},
                         {"question": "A third one", "quotes": [{"line": 2, "text": "plan anything"}]}]}
    out = I.check_beneath(raw, BY_ID)
    assert [b["question"] for b in out] == ["Was something else on your mind?"]  # at most two considered


def test_cloud_models_are_refused():
    with pytest.raises(ValueError):
        I.check_model("gpt-oss:120b-cloud")
    I.check_model("qwen3:8b")


def test_context_grows_with_the_transcript_and_has_a_cap():
    assert I.context_size("x" * 1000, 32768) == 4096
    assert I.context_size("x" * 40000, 32768) == 16384
    with pytest.raises(ValueError):
        I.context_size("x" * 200000, 32768)


def _diarize(jay_seconds):
    return {"turns": [{"speaker": "A", "start": 0, "end": 30}, {"speaker": "B", "start": 30, "end": 30 + jay_seconds}],
            "lines": LINES}


PARAMS = {"model": "qwen3:8b", "min_jay_speech_s": 20.0, "max_ctx": 32768}


def test_run_asks_once_per_principle_then_beneath(monkeypatch):
    asked = []

    def fake_chat(model, messages, schema, num_ctx, keep_alive="5m"):
        asked.append(messages[1]["content"].splitlines()[0] + (" (second look)" if schema is I.VERIFY_SCHEMA else ""))
        if schema is I.VERIFY_SCHEMA:
            return {"why": "it is", "clearly_about_it": True}
        if schema is I.BENEATH_SCHEMA:
            return {"questions": []}
        if "Principle 1" in messages[1]["content"]:
            return {"moment": True, "observation": "You answered shortly.", "try_next_time": "Ask what's behind it.",
                    "quotes": [{"line": 1, "text": "I already said we'll decide on Sunday."}]}
        return {"moment": False, "observation": "", "quotes": [], "try_next_time": ""}

    monkeypatch.setattr(I, "chat", fake_chat)
    monkeypatch.setattr(I, "unload", lambda model: None)
    out = I.run(_diarize(40), {"A": "Nisha", "B": "Sam"}, {"B"}, PARAMS, rubric_text=RUBRIC)
    assert asked == ["Principle 1, in Sam's words:", "Principle 1, in Sam's words: (second look)",
                     "Principle 2, in Sam's words:", "Transcript:"]
    assert [f["principle"] for f in out["conduct"]] == [1]
    assert out["conduct"][0]["try_next_time"] == "Ask what's behind it."
    assert out["no_moment"] == [2] and out["skipped"] is None


def test_run_skips_when_jay_barely_spoke(monkeypatch):
    monkeypatch.setattr(I, "chat", lambda *a, **k: pytest.fail("no model call for a short conversation"))
    out = I.run(_diarize(9), {"A": "Nisha", "B": "Sam"}, {"B"}, PARAMS, rubric_text=RUBRIC)
    assert out["skipped"].startswith("too short")


def test_a_finding_that_fails_the_second_look_is_dropped(monkeypatch):
    def fake_chat(model, messages, schema, num_ctx, keep_alive="5m"):
        if schema is I.VERIFY_SCHEMA:
            return {"why": "that's ordinary helpfulness, not this principle", "clearly_about_it": False}
        if schema is I.BENEATH_SCHEMA:
            return {"questions": []}
        return {"principle_is_about": "x", "moment": True, "observation": "You helped.", "try_next_time": "",
                "quotes": [{"line": 1, "text": "I already said we'll decide on Sunday."}]}

    monkeypatch.setattr(I, "chat", fake_chat)
    monkeypatch.setattr(I, "unload", lambda model: None)
    out = I.run(_diarize(40), {"A": "Nisha", "B": "Sam"}, {"B"}, PARAMS, rubric_text=RUBRIC)
    assert out["conduct"] == [] and [d["principle"] for d in out["dropped"]] == [1, 2]


def test_a_rerun_reuses_each_principle_whose_words_and_transcript_are_unchanged(monkeypatch):
    asked = []

    def fake_chat(model, messages, schema, num_ctx, keep_alive="5m"):
        asked.append(schema)
        if schema is I.VERIFY_SCHEMA:
            return {"why": "it is", "clearly_about_it": True}
        if schema is I.BENEATH_SCHEMA:
            return {"questions": []}
        if "Ignore insults" in messages[1]["content"]:
            return {"moment": True, "observation": "You let it pass.", "try_next_time": "",
                    "quotes": [{"line": 1, "text": "I already said we'll decide on Sunday."}]}
        return {"moment": False, "observation": "", "quotes": [], "try_next_time": ""}

    monkeypatch.setattr(I, "chat", fake_chat)
    monkeypatch.setattr(I, "unload", lambda model: pytest.fail("nothing was asked, nothing to unload") if not asked else None)
    first = I.run(_diarize(40), {"A": "Nisha", "B": "Sam"}, {"B"}, PARAMS, rubric_text=RUBRIC)
    assert [f["principle"] for f in first["conduct"]] == [2] and len(asked) == 4

    # The owner removes principle 1: "Ignore insults" is now 1, and nothing needs asking again.
    asked.clear()
    shorter = RUBRIC.replace("1. Speak gently, even when you are pushed hard. From my notebook, p. 12.\n\n2. Ignore", "1. Ignore")
    again = I.run(_diarize(40), {"A": "Nisha", "B": "Sam"}, {"B"}, PARAMS, rubric_text=shorter, memo=first["memo"])
    assert asked == [] and again["reused"] == 2
    assert [(f["principle"], f["principle_id"]) for f in again["conduct"]] == [(1, first["conduct"][0]["principle_id"])]

    # He rewords one principle: only that one is asked again.
    reworded = RUBRIC.replace("Speak gently, even when you are pushed hard.", "Speak gently, even when you are provoked.")
    I.run(_diarize(40), {"A": "Nisha", "B": "Sam"}, {"B"}, PARAMS, rubric_text=reworded, memo=first["memo"])
    assert asked == [I.CONDUCT_SCHEMA]

    # A different transcript (or names) asks everything again.
    asked.clear()
    I.run(_diarize(40), {"A": "Amma", "B": "Sam"}, {"B"}, PARAMS, rubric_text=RUBRIC, memo=first["memo"])
    assert len(asked) == 4
