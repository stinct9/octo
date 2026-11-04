"""Objective checks for insight models, on scripted transcripts with known answers.

  octo evalinsight [--models qwen3:8b,gemma3:12b]

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
    return {"lines": lines, "turns": turns}


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
