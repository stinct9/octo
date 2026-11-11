"""Objective checks for insight models, on scripted transcripts with known answers.

  octo evalinsight [--models qwen3:8b,gemma3:12b]
  octo evalinsight --review   (the review itself, review_any: which verdict each planted moment gets)

Complements the owner's blind A/B (octo compare), which judges usefulness on real conversations.
This measures what can be scored without them: does a model find the planted moments, does it
stay quiet where nothing bears on a principle (stretching was the first failure seen), are its
quotes real, and does it talk to the owner as "you". Only the principles each transcript scores are
asked, to keep a run to minutes.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import ROOT
from . import insight as I

FIXTURE = ROOT / "tests" / "fixtures" / "insight_eval.json"


def diarize_like(t: dict) -> dict:
    lines, turns, clock = [], [], 0.0
    for i, (spk, text) in enumerate(t["lines"]):
        dur = max(1.5, len(text.split()) * 0.35)
        lines.append({"id": i, "speaker": spk, "start": round(clock, 2), "end": round(clock + dur, 2), "text": text, "uncertain": False})
        turns.append({"speaker": spk, "start": clock, "end": clock + dur})
        clock += dur + 0.4
    talk = {s: round(sum(x["end"] - x["start"] for x in turns if x["speaker"] == s), 1) for s in sorted(t["speakers"])}
    return {"lines": lines, "turns": turns, "speakers": sorted(t["speakers"]), "talk_s": talk}  # the last two: the numbers table


def evaluate(model: str, fixture: Path = FIXTURE, rubric_text: str | None = None, on_progress=print,
             verify: bool = True) -> dict:
    data = json.loads(fixture.read_text())
    from .rubric import text as owners_rubric
    rubric_text = rubric_text if rubric_text is not None else owners_rubric()
    principles = {p["n"]: p for p in I.principles(rubric_text)}
    rules = I.review_rules(rubric_text)
    results, t0 = [], time.time()
    try:
        for t in data["transcripts"]:
            dz = diarize_like(t)
            by_id = {ln["id"]: ln for ln in dz["lines"]}
            text = I.transcript(dz["lines"], t["speakers"])
            me = t["speakers"][t["me"]]
            for n_str, expected in t["expect"].items():
                p = principles[int(n_str)]
                on_progress(f"{model} · {t['name']} · principle {p['n']}")
                raw = I.chat(model, I.conduct_messages(p, rules, me, text), I.CONDUCT_SCHEMA, I.context_size(text, 32768))
                kind, item = I.check_conduct(raw, p, by_id, {t["me"]})
                if kind == "finding" and verify and not I.verified(model, p, me, item):
                    kind = "dropped"
                results.append({
                    "transcript": t["name"], "principle": p["n"], "expected": expected,
                    "claimed": bool(raw.get("moment")), "found": kind == "finding", "kind": kind,
                    "quotes": len(raw.get("quotes", [])),
                    "verified": len(item.get("quotes", [])) if kind == "finding" else 0,
                    "second_look_dropped": raw.get("moment", False) and kind == "dropped" and bool(item.get("quotes")),
                    "third_person": bool(re.search(rf"\b{re.escape(me)}\b", raw.get("observation", ""))),
                    "observation": raw.get("observation", ""), "try_next_time": raw.get("try_next_time", ""),
                })
    finally:
        I.unload(model)
    scored = results
    tp = sum(r["found"] and r["expected"] for r in scored)
    fp = sum(r["found"] and not r["expected"] for r in scored)
    fn = sum(not r["found"] and r["expected"] for r in scored)
    tn = sum(not r["found"] and not r["expected"] for r in scored)
    quotes = sum(r["quotes"] for r in scored if r["claimed"])
    return {
        "model": model, "seconds": round(time.time() - t0, 1), "results": results,
        "summary": {
            "accuracy": round((tp + tn) / len(scored), 2), "found": f"{tp}/{tp + fn}",
            "stretched": f"{fp}/{fp + tn}",
            "quotes_real": f"{sum(r['verified'] for r in scored if r['found'])}/{sum(r['quotes'] for r in scored if r['found'])}",
            "dropped_for_evidence": sum(r["kind"] == "dropped" and not r["second_look_dropped"] for r in scored),
            "dropped_on_second_look": sum(r["second_look_dropped"] for r in scored),
            "third_person": sum(r["third_person"] for r in scored if r["found"]),
            "claims_quotes": quotes,
        },
    }


def report(evals: list[dict]) -> str:
    out = ["# Insight model check", "", "| Model | Accuracy | Planted moments found | Stretched (false alarms) | "
           "Quotes real | Dropped (no evidence) | Wrote their name, not 'you' | Time |", "|---|---|---|---|---|---|---|---|"]
    for e in evals:
        s = e["summary"]
        out.append(f"| {e['model']} | {s['accuracy']:.0%} | {s['found']} | {s['stretched']} | {s['quotes_real']} | "
                   f"{s['dropped_for_evidence']} | {s['third_person']} | {e['seconds']:.0f} s |")
    for e in evals:
        out += ["", f"## {e['model']}", ""]
        for r in e["results"]:
            mark = "✓" if r["found"] == r["expected"] else "✗"
            out.append(f"- {mark} {r['transcript']} · principle {r['principle']} · expected "
                       f"{'moment' if r['expected'] else 'none'}, got {r['kind']}"
                       + (f": {r['observation']}" if r["observation"] else ""))
    return "\n".join(out) + "\n"


# ---- the review itself (--review, 11 Nov) ----------------------------------------------------
# Does the review's model, with the review's own prompt and checks (review_any, untouched), find the planted
# moments, and which verdict does each get? Answers whether "missed" ever comes back. Nothing is stored.

def review_eval(reader: dict, owner: dict | None = None, fixture: Path = FIXTURE, rubric_text: str | None = None,
                ask=None, on_progress=print) -> dict:
    from . import providers as P
    from . import review_any as RA
    from .rubric import text as owners_rubric
    data = json.loads(fixture.read_text())
    rubric_text = rubric_text if rubric_text is not None else owners_rubric()
    now = {p["id"]: p["n"] for p in I.principles(rubric_text)}
    results, unscored, t0 = [], [], time.time()
    for t in data["transcripts"]:
        dz = diarize_like(t)
        on_progress(f"{reader['model']} · {t['name']}")
        # The 20 s length gate is the stage's, not the prompt's; a script's made-up timing shouldn't skip it.
        params = {"reader": reader, "owner": {**(owner or {}), "name": t["speakers"][t["me"]]}, "min_jay_speech_s": 0}
        out = RA.run(dz, t["speakers"], {t["me"]}, params, rubric_text, ask=ask or P.ask)
        found = {f["principle_id"]: f for f in out["conduct"]}
        # "report": shown beside the scored ones (expected None), not scored; the owner hasn't said they count
        asked = {**t["expect"], **{n: None for n in t.get("report", [])}}
        scored = {data["principle_ids"][n] for n in asked}
        unscored += [(t["name"], f["principle"], f["held"]) for f in out["conduct"] if f["principle_id"] not in scored]
        for n_str, expected in asked.items():
            pid = data["principle_ids"][n_str]
            f = found.get(pid)
            results.append({"transcript": t["name"], "was": int(n_str), "principle": now.get(pid), "expected": expected,
                            "want": t.get("verdict", {}).get(n_str),  # the verdict the owner confirmed, where there is one
                            "verdict": "gone" if pid not in now else (f["held"] or "no verdict") if f else "not found",
                            "lines": sorted({q["line"] for q in f["quotes"]}) if f else []})
    planted = [r for r in results if r["expected"] and r["principle"]]
    quiet = [r for r in results if r["expected"] is False and r["principle"]]
    wanted = [r for r in planted if r["want"]]
    count = lambda rs: {v: sum(r["verdict"] == v for r in rs) for v in (*RA.HELD, "no verdict", "not found")}  # noqa: E731
    return {"model": reader["model"], "effort": reader.get("effort"), "seconds": round(time.time() - t0, 1),
            "results": results, "unscored": unscored, "planted": count(planted), "stretched": f"{sum(r['verdict'] != 'not found' for r in quiet)}/{len(quiet)}",
            "gone": sum(r["principle"] is None for r in results),
            "expected_missed": sum(r["want"] == "missed" for r in wanted),
            "got_missed": sum(r["want"] == r["verdict"] == "missed" for r in wanted)}


def review_report(e: dict) -> str:
    out = [f"# Review check: {e['model']}, effort {e['effort']}", "",
           "| Transcript | Principle (fixture's number) | Moment planted | Expected | Verdict | Lines cited |",
           "|---|---|---|---|---|---|"]
    for r in e["results"]:
        n = r["principle"] if r["principle"] else "no longer in the rubric"
        planted = {True: "yes", False: "no", None: "shown, not scored"}[r["expected"]]
        out.append(f"| {r['transcript']} | {n} ({r['was']}) | {planted} | {r['want'] or '-'} | {r['verdict']} | "
                   f"{', '.join(map(str, r['lines'])) or '-'} |")
    p = e["planted"]
    out += ["", f"Planted moments ({sum(p.values())}): " + ", ".join(f"{v} {k}" for k, v in p.items()),
            f"Expected missed: {e['expected_missed']}, got missed: {e['got_missed']}",
            f"Findings where nothing was planted: {e['stretched']}",
            "Findings on principles the fixture doesn't score: "
            + ("; ".join(f"{t} · {n}: {v}" for t, n, v in e["unscored"]) or "none"),
            f"Left out, principle no longer in the rubric: {e['gone']}", f"Time: {e['seconds']:.0f} s"]
    return "\n".join(out) + "\n"
