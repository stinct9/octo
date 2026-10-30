"""Fixes to who-said-what: merge two speakers, or move one line to someone else (the owner's); split one
speaker that is really two or more known people (the lab's, from identify: people plan 4.2).

Stored as a list against the diarize run they were made on and applied, in order, wherever
speakers are used after that: the conversation view, numbers and insights (which re-run when
the list changes). The diarize output itself is never edited. When diarize re-runs (a new
transcription model, say), they're carried to the new run by timing, like confirmed names:
letters by the most shared speaking time, lines by overlap. A fix whose speaker or line has no
counterpart in the new run is dropped. (Before 2 Oct they were dropped on every re-run.)

These fixes are also the ground truth the plan asks for: a conversation the owner has corrected is a
labelled example of what speaker separation got wrong.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time

from .db import load_json, new_id, row, rows


def for_run(conn, session_id: str, diarize_run: str) -> list[dict]:
    return [{**json.loads(r["data"]), "id": r["id"], "kind": r["kind"]} for r in rows(
        conn, "SELECT * FROM corrections WHERE session_id=? AND diarize_run=? ORDER BY created_at, rowid",
        session_id, diarize_run)]


def add(conn, session_id: str, diarize_run: str, kind: str, data: dict) -> None:
    conn.execute("INSERT INTO corrections (id, session_id, diarize_run, kind, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (new_id("fix"), session_id, diarize_run, kind, json.dumps(data), time.time()))


SAME_VOICE = 0.60  # two letters' voiceprints at least this alike: the same person, merged without asking


def merge_same_person(conn, session_id: str) -> list[tuple[str, str]]:
    """Two speakers the owner has confirmed as the same person in one conversation are one person:
    pyannote split them (3.1 splits rather than merges; 2 Oct, the owner twice in two conversations).
    Each extra speaker is merged into the one with the most talk time, as a fix like the owner's own
    Merge (undo takes it back). Returns the (from, into) merges added.

    Reconciled on every label change (review 4 Oct, M22), not only added to: a merge this made is
    removed once its two letters no longer share a confirmed person (a wrong "Yes" fixed, a rename,
    a reject). It merges by itself only for the owner, or when the two letters' voiceprints agree; two
    different people who happen to share a name are left for the owner to decide (`same_person_asks`)."""
    dz = row(conn, "SELECT id, output FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1 "
                   "AND status='done'", session_id)
    if not dz:
        return []
    who = {r["speaker"]: r for r in rows(
        conn, "SELECT ss.speaker, ss.person_id, p.is_self FROM session_speakers ss JOIN people p ON p.id = ss.person_id "
              "WHERE ss.session_id=? AND ss.diarize_run=? AND ss.state IN ('confirmed', 'auto')", session_id, dz["id"])}
    for f in for_run(conn, session_id, dz["id"]):
        if f["kind"] == "merge" and f.get("why") == "same person":
            a, b = who.get(f["from"]), who.get(f["into"])
            if not a or not b or a["person_id"] != b["person_id"]:
                conn.execute("DELETE FROM corrections WHERE id=?", (f["id"],))
    out = apply(load_json(dz["output"]), for_run(conn, session_id, dz["id"]))
    letters: dict[str, list[str]] = {}
    for spk in sorted(who):
        if spk in out["speakers"]:
            letters.setdefault(who[spk]["person_id"], []).append(spk)
    added = []
    for person, same in letters.items():
        into = max(same, key=lambda s: out["talk_s"].get(s, 0.0))
        for s in same:
            if s != into and (who[s]["is_self"] or _same_voice(conn, session_id, s, into)):
                add(conn, session_id, dz["id"], "merge", {"from": s, "into": into, "why": "same person"})
                added.append((s, into))
    return added


def same_person_asks(conn, session_id: str) -> list[dict]:
    """Letters confirmed as the same person but not merged: their voices don't clearly agree, so the
    page asks whether it's one person (Merge) or two who share a name (rename one)."""
    dz = row(conn, "SELECT id, output FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1 "
                   "AND status='done'", session_id)
    if not dz:
        return []
    out = apply(load_json(dz["output"]), for_run(conn, session_id, dz["id"]))
    by: dict[str, list[str]] = {}
    names = {}
    for r in rows(conn, "SELECT ss.speaker, ss.person_id, p.name FROM session_speakers ss JOIN people p ON p.id = ss.person_id "
                        "WHERE ss.session_id=? AND ss.diarize_run=? AND ss.state IN ('confirmed', 'auto')", session_id, dz["id"]):
        if r["speaker"] in out["speakers"]:
            by.setdefault(r["person_id"], []).append(r["speaker"])
            names[r["person_id"]] = r["name"]
    return [{"name": names[p], "letters": sorted(ls)} for p, ls in by.items() if len(ls) > 1]


def _same_voice(conn, session_id: str, a: str, b: str) -> bool:
    import numpy as np
    prints = {r["source"].rsplit(":", 1)[1]: np.frombuffer(r["embedding"], dtype=np.float32) for r in rows(
        conn, "SELECT source, embedding FROM voiceprints WHERE source IN (?, ?)",
        f"label:{session_id}:{a}", f"label:{session_id}:{b}")}
    if a not in prints or b not in prints:
        return False
    x, y = prints[a], prints[b]
    return float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-9)) >= SAME_VOICE


def undo_last(conn, session_id: str, diarize_run: str) -> bool:
    """Take back the last fix to who said what. Not a "leave out of insights": that has its own
    switch, and Undo quietly bringing someone's words back into the review would be a leak."""
    last = rows(conn, "SELECT * FROM corrections WHERE session_id=? AND diarize_run=? AND kind NOT IN ('exclude', 'declined') "
                      "ORDER BY created_at DESC, rowid DESC LIMIT 1", session_id, diarize_run)
    if last:
        conn.execute("DELETE FROM corrections WHERE id=?", (last[0]["id"],))
        data = json.loads(last[0]["data"])
        if data.get("by") == "lab":  # the lab's merge or split, undone: remembered, so identify doesn't redo it
            add(conn, session_id, diarize_run, "declined", {"of": last[0]["kind"], "letter": data.get("letter", data.get("from")),
                                                            "into": data.get("into") if last[0]["kind"] == "merge" else None})
    return bool(last)


def _line_map(old: list[dict], new: list[dict]) -> dict[int, int]:
    """Old line id → the new line that is still the same stretch: each covers at least 80% of the
    other. A line that was split or joined has no counterpart (moving half of it would be a guess)."""
    out = {}
    for o in old:
        for n in new:
            shared = min(o["end"], n["end"]) - max(o["start"], n["start"])
            if shared >= 0.8 * max(0.01, o["end"] - o["start"]) and shared >= 0.8 * max(0.01, n["end"] - n["start"]):
                out[o["id"]] = n["id"]
                break
    return out


def carry(conn, session_id: str, old_run: str, new_run: str) -> int:
    """Copy the owner's fixes on old_run's speakers and lines onto new_run's. Returns how many carried."""
    from .stages import remap_speakers
    fixes = for_run(conn, session_id, old_run)
    if not fixes or for_run(conn, session_id, new_run):
        return 0
    old = load_json((row(conn, "SELECT output FROM stage_runs WHERE id=?", old_run) or {}).get("output"))
    new = load_json((row(conn, "SELECT output FROM stage_runs WHERE id=?", new_run) or {}).get("output"))
    if not old or not new:
        return 0
    letters = remap_speakers(old["turns"], new["turns"])
    lines = _line_map(old["lines"], new["lines"])
    carried = 0
    for f in fixes:
        data = {k: v for k, v in f.items() if k not in ("id", "kind")}
        if f["kind"] == "merge" and f["from"] in letters and f["into"] in letters:
            data.update({"from": letters[f["from"]], "into": letters[f["into"]]})
        elif f["kind"] == "exclude" and f["letter"] in letters:
            data["letter"] = letters[f["letter"]]
        elif f["kind"] == "line" and f["line"] in lines and f["to"] in letters:
            data.update({"line": lines[f["line"]], "to": letters[f["to"]]})
        elif f["kind"] == "declined" and f["letter"] in letters:
            data.update({"letter": letters[f["letter"]], "into": letters.get(f["into"]) if f.get("into") else None})
        elif f["kind"] == "split" and f["letter"] in letters:  # times stay; the new letter is one free in the new run
            taken = set(new["speakers"]) | {x["into"] for x in for_run(conn, session_id, new_run) if x["kind"] == "split"}
            into = next_letter(taken)
            letters[f["into"]] = into  # later fixes naming it follow
            data.update({"letter": letters[f["letter"]], "into": into})
        else:
            continue
        add(conn, session_id, new_run, f["kind"], data)
        carried += 1
    return carried


def left_out(dz: dict, fixes: list[dict]) -> set[str]:
    """Speakers the owner left out of insights, as letters of the corrected output. A merge with a
    left-out speaker, either way round, leaves the merged speaker out: before 4 Oct merging a
    left-out speaker into someone else brought their words back into the review."""
    label = {s: s for s in dz.get("speakers", [])}
    for f in fixes:
        if f["kind"] == "merge":
            for k, v in label.items():
                if v == f["from"]:
                    label[k] = f["into"]
    return {label.get(f["letter"], f["letter"]) for f in fixes if f["kind"] == "exclude"}


def without(dz: dict, letters: set[str]) -> dict:
    """The corrected diarize output with these speakers taken out: their lines, their turns and
    their talk time, so neither their words nor their numbers reach the review."""
    if not letters:
        return dz
    keep = lambda x: x.get("speaker") not in letters
    return {**dz, "speakers": [s for s in dz["speakers"] if s not in letters],
            "talk_s": {k: v for k, v in dz.get("talk_s", {}).items() if k not in letters},
            "turns": [t for t in dz["turns"] if keep(t)],
            "overlapping_turns": [t for t in dz.get("overlapping_turns", []) if keep(t)],
            "lines": [ln for ln in dz["lines"] if keep(ln)]}


def left_out_spans(dz: dict, fixes: list[dict]) -> list[list[list[float]]]:
    """Each left-out speaker's turns, so the choice can outlive the diarize run (privacy.delete_range)."""
    out = apply(dz, fixes)
    return [[[t["start"], t["end"]] for t in out["turns"] if t["speaker"] == s] for s in sorted(left_out(dz, fixes))]


def adopt_left_out(conn, session_id: str, diarize_run: str) -> int:
    """After "delete a part", the conversation is worked out again from scratch, and the speaker
    letters change. Speakers the owner had left out are found again by time (the speaker who talks most
    in each left-out speaker's old turns) and left out of the new run too. Returns how many."""
    sess = row(conn, "SELECT left_out_spans FROM sessions WHERE id=?", session_id)
    groups = load_json(sess["left_out_spans"]) if sess and sess["left_out_spans"] else None
    dz = row(conn, "SELECT output FROM stage_runs WHERE id=?", diarize_run)
    if not groups or not dz or not dz["output"]:
        return 0
    turns = load_json(dz["output"])["turns"]
    have = {f["letter"] for f in for_run(conn, session_id, diarize_run) if f["kind"] == "exclude"}
    added = 0
    for spans in groups:
        talk: dict[str, float] = {}
        for t in turns:
            for a, b in spans:
                talk[t["speaker"]] = talk.get(t["speaker"], 0.0) + max(0.0, min(b, t["end"]) - max(a, t["start"]))
        best = max(talk, key=talk.get, default=None)
        if best and talk[best] > 0 and best not in have:
            add(conn, session_id, diarize_run, "exclude", {"letter": best})
            have.add(best)
            added += 1
    conn.execute("UPDATE sessions SET left_out_spans=NULL WHERE id=?", (session_id,))
    return added


def version(fixes: list[dict]) -> str:
    return hashlib.sha256(json.dumps([{k: v for k, v in f.items() if k != "id"} for f in fixes],
                                     sort_keys=True).encode()).hexdigest()[:12] if fixes else "none"


def _cut(start: float, end: float, spans: list[list[float]]) -> list[tuple[float, float, bool]]:
    """[start, end) in pieces, each marked whether it lies in one of `spans`."""
    edges = sorted({start, end, *(x for s, e in spans for x in (s, e) if start < x < end)})
    return [(a, b, any(s <= a and b <= e for s, e in spans)) for a, b in zip(edges, edges[1:]) if b - a > 1e-6]


def next_letter(taken: set[str]) -> str:
    """A speaker letter not in use: A..Z, then AA, AB, …"""
    for n in range(26 * 27):
        letter = chr(65 + n) if n < 26 else chr(64 + n // 26) + chr(65 + n % 26)
        if letter not in taken:
            return letter
    raise ValueError("no letters left")


def _relabel_span(turns: list[dict], start: float, end: float, to: str, only: str | None = None) -> list[dict]:
    """Give [start, end) to `to`, cutting turns that straddle its edges. `only`: just that speaker's turns."""
    out = []
    for t in turns:
        if (only and t["speaker"] != only) or t["end"] <= start or t["start"] >= end:
            out.append(t)
            continue
        if t["start"] < start:
            out.append({**t, "end": start})
        out.append({**t, "start": max(t["start"], start), "end": min(t["end"], end), "speaker": to})
        if t["end"] > end:
            out.append({**t, "start": end})
    return out


def apply(dz: dict, fixes: list[dict]) -> dict:
    """The diarize output as the owner has corrected it."""
    if not fixes:
        return dz
    out = copy.deepcopy(dz)
    for f in fixes:
        if f["kind"] == "merge":
            src, dst = f["from"], f["into"]
            for coll in ("turns", "overlapping_turns", "lines"):
                for x in out.get(coll, []):
                    if x.get("speaker") == src:
                        x["speaker"] = dst
        elif f["kind"] == "split":  # the lab: these stretches of `letter` are someone else, `into`
            spans = f["spans"]
            inside = lambda a, b: sum(max(0.0, min(b, e) - max(a, s)) for s, e in spans)
            for coll in ("turns", "overlapping_turns"):
                moved = []
                for t in out.get(coll, []):
                    if t["speaker"] != f["letter"]:
                        moved.append(t)
                        continue
                    for a, b, who in _cut(t["start"], t["end"], spans):
                        moved.append({**t, "start": a, "end": b, "speaker": f["into"] if who else f["letter"]})
                out[coll] = moved
            for ln in out["lines"]:
                if ln["speaker"] == f["letter"] and inside(ln["start"], ln["end"]) > 0.5 * (ln["end"] - ln["start"]):
                    ln["speaker"] = f["into"]
        elif f["kind"] == "line":
            line = next((ln for ln in out["lines"] if ln["id"] == f["line"]), None)
            if not line:
                continue
            old = line["speaker"]
            line["speaker"] = f["to"]
            line["uncertain"] = False  # The owner said who it is
            out["turns"] = _relabel_span(out["turns"], line["start"], line["end"], f["to"])
            if "overlapping_turns" in out:
                out["overlapping_turns"] = _relabel_span(out["overlapping_turns"], line["start"], line["end"], f["to"], only=old)
    # Merge neighbouring turns of one speaker; drop speakers left with no speech.
    turns = []
    for t in sorted(out["turns"], key=lambda t: t["start"]):
        if turns and turns[-1]["speaker"] == t["speaker"] and t["start"] - turns[-1]["end"] < 0.05:
            turns[-1]["end"] = max(turns[-1]["end"], t["end"])
        elif t["end"] - t["start"] > 0.01:
            turns.append(dict(t))
    out["turns"] = turns
    present = {t["speaker"] for t in turns} | {ln["speaker"] for ln in out["lines"] if ln["speaker"]}
    order = list(dz["speakers"])
    for f in fixes:  # a split's new letter comes after the one it came from
        if f["kind"] == "split" and f["into"] not in order:
            order.insert(order.index(f["letter"]) + 1 if f["letter"] in order else len(order), f["into"])
    out["speakers"] = [s for s in order if s in present]
    out["talk_s"] = {s: round(sum(t["end"] - t["start"] for t in turns if t["speaker"] == s), 1) for s in out["speakers"]}
    return out
