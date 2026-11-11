"""The review check (octo evalinsight --review): planted moments matched by principle id through a renumbered
rubric, scored by the verdict review_any keeps, on a canned answer (no model call)."""

import json

from octo import insight as I
from octo import insight_eval as E
from tests.test_insight import RUBRIC

P1, P2 = (p["id"] for p in I.principles(RUBRIC))


def test_review_check_scores_verdicts_by_principle_id(tmp_path):
    fixture = tmp_path / "eval.json"
    fixture.write_text(json.dumps({
        "principle_ids": {"5": P2, "8": P1, "9": "deadbeef"},  # numbered by an older rubric; 9 is gone from it
        "transcripts": [{"name": "t", "speakers": {"A": "Nisha", "B": "Sam"}, "me": "B",
                         "lines": [["A", "You never plan anything."], ["B", "Fine, whatever you want."],
                                   ["B", "Sorry, that came out wrong."]],
                         "expect": {"8": True, "5": True, "9": True}}]}))
    asked = []

    def ask(reader, system, prompt, schema):
        asked.append(prompt)
        return {"summary": "", "one_thing": {}, "reading": [], "between": [], "next": [], "limits": "", "no_moment": [],
                "conduct": [{"principle": 1, "held": "missed", "observation": "", "quotes": [{"line": 1, "text": "whatever you want"}]},
                            # only the other person's words: review_any drops it, so it reads as not found
                            {"principle": 2, "held": "held", "observation": "", "quotes": [{"line": 0, "text": "You never plan"}]}]}, None

    e = E.review_eval({"kind": "claude-code", "model": "m", "effort": "high"}, {"pronoun": "he"}, fixture, RUBRIC, ask=ask,
                      on_progress=lambda m: None)
    got = {r["was"]: (r["principle"], r["verdict"], r["lines"]) for r in e["results"]}
    assert got == {8: (1, "missed", [1]), 5: (2, "not found", []), 9: (None, "gone", [])}
    assert e["planted"] == {"held": 0, "partly": 0, "missed": 1, "depends": 0, "no verdict": 0, "not found": 1}
    assert e["gone"] == 1 and len(asked) == 1 and "Sam's principles" in asked[0]
    assert "no longer in the rubric" in E.review_report(e)


def test_review_check_scores_expected_verdicts_and_shows_reported_ones(tmp_path):
    fixture = tmp_path / "eval.json"
    script = lambda name: {"name": name, "speakers": {"A": "Arjun", "B": "Sam"}, "me": "B",  # noqa: E731
                           "lines": [["A", "That was amateur work."], ["B", "Remember this next month."]]}
    fixture.write_text(json.dumps({"principle_ids": {"3": P1, "7": P2}, "transcripts": [
        {**script("a"), "expect": {"3": True}, "verdict": {"3": "missed"}, "report": ["7"]},
        {**script("b"), "expect": {"3": True}, "verdict": {"3": "missed"}}]}))
    said = iter([[{"principle": 1, "held": "missed"}], [{"principle": 1, "held": "partly"}, {"principle": 2, "held": "depends"}]])

    def ask(reader, system, prompt, schema):
        conduct = [{**c, "observation": "", "quotes": [{"line": 1, "text": "Remember this"}]} for c in next(said)]
        return {"summary": "", "one_thing": {}, "reading": [], "between": [], "next": [], "limits": "", "no_moment": [],
                "conduct": conduct}, None

    e = E.review_eval({"kind": "claude-code", "model": "m", "effort": "high"}, {}, fixture, RUBRIC, ask=ask,
                      on_progress=lambda m: None)
    got = [(r["transcript"], r["principle"], r["expected"], r["want"], r["verdict"]) for r in e["results"]]
    assert got == [("a", 1, True, "missed", "missed"), ("a", 2, None, None, "not found"), ("b", 1, True, "missed", "partly")]
    assert (e["expected_missed"], e["got_missed"]) == (2, 1)
    assert e["planted"]["missed"] == 1 and e["stretched"] == "0/0"  # the reported one is neither planted nor quiet
    assert e["unscored"] == [("b", 2, "depends")]  # b doesn't list 2 under "report": the free list, as before
    report = E.review_report(e)
    assert "Expected missed: 2, got missed: 1" in report and "shown, not scored" in report
