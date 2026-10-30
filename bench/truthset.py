"""A yardstick that can see a merge (people plan 4.1).

    python -m bench.truthset build                 # clips + synthetic conversations, from what the owner confirmed
    python -m bench.truthset run NAME [--threshold 0.705]    # pyannote, saved raw
    python -m bench.truthset post NAME RUN METHOD [k=v ...]  # a fix applied to RUN's raw output, scored
    python -m bench.truthset report

`bench.diarcompare` scores against the owner's confirmations, and where pyannote heard two people as one and
the owner confirmed the blend (8 Oct), the reference is wrong the same way as the model. Here the answer is
known by construction: conversations are spliced from stretches where one confirmed person speaks
alone, each checked by voice against that person's prints from *other* conversations, with the
closest-sounding pairs on purpose.

Stored under data/bench/truthset/ (swept at 60 days): pointers into the lab's own recordings (which
recording, from when to when, whose voice) and scores. No audio, no embeddings, no words.

Each person's clips are split in two: "heard before" (their voiceprints for the test, one per
conversation the clips came from, as the lab keeps one per labelled conversation) and "test" (what the
conversations are made of). For people heard in one conversation only, both halves come from it, which
flatters naming them: the lab's prints always come from another conversation.

Caveat: spliced speech has no crosstalk and no change of room mid-turn, which flatters every method
about equally. The ranking is what to trust, then the owner's ear on real conversations.
"""

from __future__ import annotations

import json
import random
import sys
import time
from itertools import combinations

import numpy as np

from octo import DATA, DB_PATH as DB, HOME
from octo import audio as A
from octo.db import connect, load_json, row, rows
from octo.voice import MATCH_MARGIN, MATCH_MIN, Embedder, cosine, match, similarities

OUT = DATA / "bench" / "truthset"
CLIP_MIN_S, CLIP_MAX_S = 3.0, 8.0
PER_SPEAKER = 12      # clips from one speaker in one conversation, longest first
PER_PERSON = 40       # clips per person, spread over their conversations
MIN_CLIPS = 10        # people with fewer aren't used (half are "heard before", half make the test)
TURNS = 30            # per synthetic conversation (about 3-4 minutes)


def prints_now(conn) -> list[dict]:
    model = (row(conn, "SELECT model FROM voiceprints ORDER BY created_at DESC LIMIT 1") or {}).get("model")
    return [{**r, "embedding": np.frombuffer(r["embedding"], dtype=np.float32)} for r in rows(
        conn, "SELECT v.person_id, p.name, p.is_self, v.source, v.embedding FROM voiceprints v "
              "JOIN people p ON p.id = v.person_id WHERE v.model IS ?", model)]


def _session_of(source: str) -> str | None:
    return source.split(":")[1] if source.startswith("label:") else None


def _alone(turn: dict, others: list[dict]) -> bool:
    return not any(o["start"] < turn["end"] and turn["start"] < o["end"] for o in others)


def clips(conn, embedder: Embedder, log=print) -> list[dict]:
    """Stretches of one confirmed person speaking alone, kept only if their voice matches that person's
    prints from other conversations (≥ MATCH_MIN, clear of everyone else by MATCH_MARGIN). A person whose
    only print is from the same conversation is checked against it, and marked so."""
    from octo.sessions import diarized

    prints = prints_now(conn)
    found: list[dict] = []
    for s in rows(conn, "SELECT s.*, r.path, r.audio_deleted_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                        "WHERE s.status='active' AND r.audio_deleted_at IS NULL ORDER BY s.start_s"):
        dz, out, _ = diarized(conn, s["id"])
        if not dz:
            continue
        who = {r["speaker"]: r for r in rows(
            conn, "SELECT ss.speaker, p.id, p.name, p.is_self FROM session_speakers ss JOIN people p ON p.id = ss.person_id "
                  "WHERE ss.session_id=? AND ss.state='confirmed' AND ss.diarize_run=?", s["id"], dz["id"])}
        if not who:
            continue
        overlapping = out.get("overlapping_turns") or out["turns"]
        samples = None
        for letter, person in who.items():
            others = [t for t in overlapping if t["speaker"] != letter]
            cand = [t for t in out["turns"] if t["speaker"] == letter and t["end"] - t["start"] >= CLIP_MIN_S and _alone(t, others)]
            cand = sorted(cand, key=lambda t: t["start"] - t["end"])[:PER_SPEAKER]
            if not cand:
                continue
            if samples is None:
                samples = A.read(HOME / s["path"], s["start_s"], s["end_s"])
            elsewhere = [p for p in prints if _session_of(p["source"]) != s["id"]]
            here_only = not any(p["person_id"] == person["id"] for p in elsewhere)
            against = [p for p in prints if p["person_id"] != person["id"] or _session_of(p["source"]) == s["id"]] \
                if here_only else elsewhere
            for t in cand:
                a, b = t["start"], min(t["end"], t["start"] + CLIP_MAX_S)
                ranked = similarities(embedder.embed(samples[int(a * A.RATE):int(b * A.RATE)]), against)
                own = next((r["similarity"] for r in ranked if r["person_id"] == person["id"]), 0.0)
                other = next((r["similarity"] for r in ranked if r["person_id"] != person["id"]), 0.0)
                if own >= MATCH_MIN and own - other >= MATCH_MARGIN:
                    found.append({"person": person["name"], "person_id": person["id"], "is_self": bool(person["is_self"]),
                                  "session": s["id"], "path": s["path"], "start": round(s["start_s"] + a, 2),
                                  "end": round(s["start_s"] + b, 2), "own": own, "other": other, "checked": "this conversation only" if here_only else "elsewhere"})
        log(f"{s['id']}: {sum(1 for c in found if c['session'] == s['id'])} clips")
    by: dict[str, list[dict]] = {}
    for c in found:
        by.setdefault(c["person"], []).append(c)
    kept = []
    for person, cs in by.items():  # spread over conversations: round-robin by session
        per: dict[str, list[dict]] = {}
        for c in cs:
            per.setdefault(c["session"], []).append(c)
        queues = list(per.values())
        while queues and sum(1 for c in kept if c["person"] == person) < PER_PERSON:
            queues = [q for q in queues if q]
            for q in queues:
                if q and sum(1 for c in kept if c["person"] == person) < PER_PERSON:
                    kept.append(q.pop(0))
    return kept


def pair_similarity(prints: list[dict]) -> dict[tuple[str, str], float]:
    by: dict[str, list[np.ndarray]] = {}
    for p in prints:
        by.setdefault(p["name"], []).append(p["embedding"])
    return {(a, b): float(np.mean([cosine(x, y) for x in by[a] for y in by[b]]))
            for a, b in combinations(sorted(by), 2)}


def conversations(clipset: list[dict], prints: list[dict], seed: int = 7) -> list[dict]:
    """Deterministic: the 8 closest-sounding pairs, 6 random pairs, 6 threes holding a close pair, 4 fours."""
    rng = random.Random(seed)
    by: dict[str, list[int]] = {}
    for i, c in enumerate(clipset):
        if c.get("half", "test") == "test":
            by.setdefault(c["person"], []).append(i)
    people = sorted(p for p, cs in by.items() if len(cs) >= MIN_CLIPS // 2)
    sim = {k: v for k, v in pair_similarity(prints).items() if k[0] in people and k[1] in people}
    close = [k for k, _ in sorted(sim.items(), key=lambda kv: -kv[1])[:8]]
    sets: list[tuple[str, ...]] = [*close]
    sets += [tuple(sorted(rng.sample(people, 2))) for _ in range(6)]
    for pair in close[:6]:
        third = rng.choice([p for p in people if p not in pair])
        sets.append(tuple(sorted((*pair, third))))
    sets += [tuple(sorted(rng.sample(people, 4))) for _ in range(4)]
    out = []
    for n, group in enumerate(sets):
        cursor = {p: rng.randrange(len(by[p])) for p in group}
        t, last, turns = 0.0, None, []
        for _ in range(TURNS):
            who = rng.choice([p for p in group if p != last])
            clip = by[who][cursor[who] % len(by[who])]
            cursor[who] += 1
            length = clipset[clip]["end"] - clipset[clip]["start"]
            turns.append({"speaker": who, "clip": clip, "start": round(t, 2), "end": round(t + length, 2)})
            t += length + rng.uniform(0.2, 1.2)
            last = who
        out.append({"id": f"syn{n:02d}", "people": list(group), "close": group in close or any(set(c) <= set(group) for c in close[:6]),
                    "similarity": round(max(sim.get(tuple(sorted(p)), 0.0) for p in combinations(group, 2)), 3), "turns": turns})
    return out


def audio_of(conv: dict, clipset: list[dict]) -> np.ndarray:
    end = conv["turns"][-1]["end"]
    out = np.zeros(int((end + 1.0) * A.RATE), dtype=np.float32)
    for t in conv["turns"]:
        c = clipset[t["clip"]]
        piece = A.read(HOME / c["path"], c["start"], c["end"])
        a = int(t["start"] * A.RATE)
        out[a:a + len(piece)] = piece[:len(out) - a]
    return out


def build() -> None:
    conn = connect(DB)
    t0 = time.time()
    clipset = clips(conn, Embedder())
    seen: dict[str, int] = {}
    for c in clipset:  # alternate within each person's (round-robin) order: both halves span their conversations
        c["half"] = "enrol" if seen.get(c["person"], 0) % 2 == 0 else "test"
        seen[c["person"]] = seen.get(c["person"], 0) + 1
    convs = conversations(clipset, prints_now(conn))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "clips.json").write_text(json.dumps(clipset))
    (OUT / "conversations.json").write_text(json.dumps(convs))
    people = sorted({c["person"] for c in clipset})
    print(f"\n{len(clipset)} clips of {len(people)} people in {time.time() - t0:.0f} s; {len(convs)} conversations")
    for p in people:
        cs = [c for c in clipset if c["person"] == p]
        print(f"  {p:14} {len(cs):3} clips from {len({c['session'] for c in cs})} conversations"
              f"{' (checked against this conversation only)' if all(c['checked'] != 'elsewhere' for c in cs) else ''}")


def enrol_prints(clipset: list[dict], embedder: Embedder) -> list[dict]:
    """One print per person per conversation, from their "heard before" clips (≤ 60 s, as the lab's)."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for c in clipset:
        if c.get("half") == "enrol":
            groups.setdefault((c["person"], c["session"]), []).append(c)
    out = []
    for (person, session), cs in groups.items():
        pieces, total = [], 0.0
        for c in cs:
            if total >= 60:
                break
            pieces.append(A.read(HOME / c["path"], c["start"], c["end"]))
            total += c["end"] - c["start"]
        if total >= 8:  # MIN_PRINT_S
            out.append({"person_id": cs[0]["person_id"], "name": person, "is_self": bool(cs[0].get("is_self")),
                        "source": f"enrol:{session}", "embedding": embedder.embed(np.concatenate(pieces))})
    return out


def run(name: str, threshold: float | None) -> None:
    """Separate speakers with pyannote 3.1 (the lab's threshold unless given) and keep the raw turns, so
    each fix (`post`) is tried on the same output. Scored as it is, with identify's naming."""
    from bench.diarcompare import diarize_pyannote

    clipset = json.loads((OUT / "clips.json").read_text())
    convs = json.loads((OUT / "conversations.json").read_text())
    model = "pyannote/speaker-diarization-3.1" + (f"@{threshold}" if threshold is not None else "@0.705")
    raw = []
    for conv in convs:
        t0 = time.time()
        raw.append({"id": conv["id"], "hyp": diarize_pyannote(model, audio_of(conv, clipset)), "seconds": round(time.time() - t0, 1)})
        print(f"{conv['id']}: {len({t['speaker'] for t in raw[-1]['hyp']})} speakers", flush=True)
    (OUT / "raw").mkdir(exist_ok=True)
    (OUT / "raw" / f"{name}.json").write_text(json.dumps({"name": name, "model": model, "convs": raw}))
    post(name, name, "none", {})


def fix_none(hyp, samples, known, embedder, **_):
    return hyp


def windows(hyp: list[dict], size: float = 3.0, least: float = 1.5) -> list[dict]:
    """Each turn cut into pieces of about `size` seconds; a turn shorter than `least` is one piece."""
    out = []
    for t in hyp:
        a = t["start"]
        while a < t["end"]:
            b = t["end"] if t["end"] - a < size + least else a + size
            out.append({"speaker": t["speaker"], "start": a, "end": b})
            a = b
    return out


def fix_known(hyp, samples, known, embedder, share: float = 0.2, least_s: float = 10.0, size: float = 3.0, **_):
    """Phase 2: a speaker whose pieces sound clearly like two or more known people is split along them.
    A piece no one is sure of goes to whichever of them it sounds closest to; one too short to tell, to
    the person of the nearest sure piece."""
    out = []
    for spk in sorted({t["speaker"] for t in hyp}):
        ws = windows([t for t in hyp if t["speaker"] == spk], size)
        tags = []
        for w in ws:
            if w["end"] - w["start"] < 1.0:
                tags.append((None, {}))
                continue
            ranked = similarities(embedder.embed(samples[int(w["start"] * A.RATE):int(w["end"] * A.RATE)]), known)
            sure = ranked and ranked[0]["similarity"] >= MATCH_MIN and \
                ranked[0]["similarity"] - (ranked[1]["similarity"] if len(ranked) > 1 else 0) >= MATCH_MARGIN
            tags.append((ranked[0]["name"] if sure else None, {r["name"]: r["similarity"] for r in ranked}))
        total = sum(w["end"] - w["start"] for w in ws)
        held: dict[str, float] = {}
        for w, (who, _) in zip(ws, tags):
            if who:
                held[who] = held.get(who, 0.0) + w["end"] - w["start"]
        people = [p for p, v in held.items() if v >= share * total and v >= least_s]
        if len(people) < 2:
            out += [t for t in hyp if t["speaker"] == spk]
            continue
        sure = [(w["start"], who) for w, (who, _) in zip(ws, tags) if who in people]
        for w, (who, sims) in zip(ws, tags):
            if who not in people:
                who = max(people, key=lambda p: sims[p]) if sims else min(sure, key=lambda x: abs(x[0] - w["start"]))[1]
            out.append({"speaker": f"{spk}/{who}", "start": w["start"], "end": w["end"]})
    return sorted(out, key=lambda t: t["start"])


def fix_merge(hyp, samples, known, embedder, same: float = 0.75, **_):
    """Phase 3: speakers named as the same known person are one; two unknown speakers whose voices are
    this close are one."""
    spk = sorted({t["speaker"] for t in hyp})
    emb, name = {}, {}
    for s_ in spk:
        got = embedder.embed_turns(samples, [(t["start"], t["end"]) for t in hyp if t["speaker"] == s_])
        if got:
            emb[s_] = got[0]
            m = match(got[0], known)
            name[s_] = m["name"] if m and not m["ambiguous"] else None
    into = {s_: s_ for s_ in spk}
    def root(x):
        while into[x] != x:
            x = into[x]
        return x
    for a, b in combinations([s_ for s_ in spk if s_ in emb], 2):
        if (name[a] and name[a] == name[b]) or (not name[a] and not name[b] and cosine(emb[a], emb[b]) >= same):
            into[root(b)] = root(a)
    return [{**t, "speaker": root(t["speaker"])} for t in hyp]


FIXES = {"none": fix_none, "known": fix_known, "merge": fix_merge,
         "merge+known": lambda hyp, *a, **k: fix_known(fix_merge(hyp, *a, **k), *a, **k)}


def post(name: str, raw_name: str, method: str, params: dict) -> None:
    from bench.diarcompare import score

    clipset = json.loads((OUT / "clips.json").read_text())
    convs = {c["id"]: c for c in json.loads((OUT / "conversations.json").read_text())}
    raw = json.loads((OUT / "raw" / f"{raw_name}.json").read_text())
    embedder = Embedder()
    known = enrol_prints(clipset, embedder)
    results = []
    for r in raw["convs"]:
        conv = convs[r["id"]]
        samples = audio_of(conv, clipset)
        hyp = FIXES[method](r["hyp"], samples, known, embedder, **params)
        ref = [{"speaker": t["speaker"], "start": t["start"], "end": t["end"]} for t in conv["turns"]]
        sc = score(ref, {p: p for p in conv["people"]}, hyp)
        named, matches = {}, {}
        for spk in sorted({t["speaker"] for t in hyp}):
            got = embedder.embed_turns(samples, [(t["start"], t["end"]) for t in hyp if t["speaker"] == spk])
            m = match(got[0], known) if got else None
            named[spk] = None if not m else ("?" if m["ambiguous"] else m["name"])
            # whose speech this speaker mostly is, to judge naming it without asking (people plan 4.4)
            held_by: dict[str, float] = {}
            for x in ref:
                for h in hyp:
                    if h["speaker"] == spk:
                        o = min(x["end"], h["end"]) - max(x["start"], h["start"])
                        if o > 0:
                            held_by[x["speaker"]] = held_by.get(x["speaker"], 0.0) + o
            total = sum(held_by.values()) or 1.0
            mostly = max(held_by, key=held_by.get) if held_by else None
            matches[spk] = {"name": m["name"] if m else None, "similarity": m["similarity"] if m else None,
                            "margin": m.get("margin") if m else None, "ambiguous": bool(m and m["ambiguous"]),
                            "mostly": mostly, "share": round(held_by.get(mostly, 0.0) / total, 2) if mostly else None,
                            "speech_s": round(total, 1)}
        held = {}
        for p in conv["people"]:  # the speaker holding most of their talk, and what it was called
            share: dict[str, float] = {}
            for x in ref:
                if x["speaker"] != p:
                    continue
                for h in hyp:
                    o = min(x["end"], h["end"]) - max(x["start"], h["start"])
                    if o > 0:
                        share[h["speaker"]] = share.get(h["speaker"], 0.0) + o
            spk = max(share, key=share.get) if share else None
            called = named.get(spk) if spk else None
            held[p] = {"speaker": spk, "named": called, "known": True,
                       "result": "lost" if p in sc["lost"] else "right" if called == p else "asked" if called == "?"
                       else "unknown" if called is None else "wrong"}
        results.append({"id": conv["id"], "people": conv["people"], "close": conv["close"], "similarity": conv["similarity"],
                        **sc, "named": named, "matches": matches, "held": held})
        print(f"{conv['id']} {'+'.join(conv['people']):40} {sc['speakers']} spk  {100 * sc['right_s'] / sc['talk_s']:5.1f}%  "
              f"lost {sc['lost'] or '-'}  " + " ".join(f"{p}:{h['result']}" for p, h in held.items()), flush=True)
    (OUT / "results").mkdir(exist_ok=True)
    (OUT / "results" / f"{name}.json").write_text(json.dumps({"name": name, "model": raw["model"], "method": method,
                                                               "params": params, "results": results}))


def bars(name: str) -> None:
    """Naming without asking (people plan 4.4): at each bar, how many speakers would be named, and how
    many of those wrongly (the name isn't whose speech the speaker mostly is)."""
    rs = json.loads((OUT / "results" / f"{name}.json").read_text())["results"]
    ms = [m for r in rs for m in r["matches"].values() if m["name"] and not m["ambiguous"]]
    print(f"{name}: {len(ms)} speakers with a clear match")
    for sim, margin in [(0.6, 0.15), (0.65, 0.2), (0.7, 0.25), (0.75, 0.25), (0.8, 0.3)]:
        take = [m for m in ms if m["similarity"] >= sim and m["margin"] >= margin]
        wrong = [m for m in take if m["name"] != m["mostly"]]
        said = ", ".join(f"{m['name']} for {m['mostly']}" for m in wrong)
        print(f"  ≥ {sim}, margin ≥ {margin}: {len(take)} named without asking, {len(wrong)} wrong"
              + (f" ({said})" if wrong else ""))


def report() -> None:
    for f in sorted((OUT / "results").glob("*.json")):
        data = json.loads(f.read_text())
        rs = data["results"]
        for label, sub in (("all", rs), ("close pairs", [r for r in rs if r["close"]])):
            right = sum(r["right_s"] for r in sub) / max(1e-9, sum(r["talk_s"] for r in sub))
            lost = sum(len(r["lost"]) for r in sub)
            people = sum(len(r["people"]) for r in sub)
            extra = sum(max(0, r["speakers"] - len(r["people"])) for r in sub)
            res: dict[str, int] = {}
            for r in sub:
                for h in r["held"].values():
                    res[h["result"]] = res.get(h["result"], 0) + 1
            print(f"{data['name']:24} {label:12} {len(sub):2} convs  {100 * right:5.1f}% right  lost {lost}/{people}  "
                  f"extra speakers {extra}  people: "
                  + " ".join(f"{k} {v}" for k, v in sorted(res.items())))
        print()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "build":
        build()
    elif cmd == "run":
        thr = float(sys.argv[sys.argv.index("--threshold") + 1]) if "--threshold" in sys.argv else None
        run(sys.argv[2], thr)
    elif cmd == "post":
        post(sys.argv[2], sys.argv[3], sys.argv[4], {k: float(v) for k, v in (a.split("=") for a in sys.argv[5:])})
    elif cmd == "bars":
        bars(sys.argv[2])
    else:
        report()
