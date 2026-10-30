"""The owner's filler words over time (docs/coach-plan.md §4.2), counted at read time.

Not a pipeline stage, and not `stages.count`: the review builds its numbers table with that function,
so its list stays as it was. Here the list is the owner's own (`filler_words`, starting from the same eight),
counted over the lines already stored: changing the list recounts every conversation, so the trend
stays one measuring stick. Nothing re-runs.

What counts toward the trend (the measuring stick): the owner's confirmed lines, in conversations
transcribed in English by Qwen (the words model since 2 Oct), with at least MIN_WORDS of their words,
recorded since the lab began. Whisper and the Hindi merge keep fillers differently, so those are off
the line, each with the reason. And only what the transcription kept is counted at all: the filler
check by ear (`filler_checks`) says how much that is.

Numbers stay neutral (coaching plan A4): rates against the owner's own earlier rates, never a target.
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import date, datetime
from functools import lru_cache

from .db import config, load_json, new_id, row, rows

DEFAULTS = ("um", "uh", "ah", "er", "hmm", "you know", "kind of", "sort of")  # stages.FILLERS, as of 6 Oct
# Hesitations count however long they're held: umm, uhhh, hmmm.
HESITATIONS = {"um", "uh", "ah", "er", "erm", "hmm", "mm", "eh"}
MIN_WORDS = 100            # of the owner's words, for a conversation to be on the line
WINDOW_WORDS = 2000        # the line pools about this many of his words
MIN_POOL_WORDS = 500       # a "first fortnight" or "usually" needs at least this many
BASELINE_DAYS = 14
MIN_GROUP_WORDS = 300      # a person or setting needs this many before it's shown
LAB_START = date(2026, 10, 1)
# Discourse markers worth asking about (Indian English and Hindi-English): suggested only when the owner
# uses one far more than the others in the same conversations. He decides; nothing is added for him.
LEAN_ON = ("basically", "actually", "literally", "honestly", "obviously", "definitely", "so", "like", "just",
           "really", "right", "okay", "i mean", "you see", "i think", "i guess", "kind of", "sort of", "you know",
           "na", "matlab", "achha", "accha", "yaani", "arre", "um", "uh", "ah", "er", "hmm")
# Words that open a turn as a lead-in rather than as content ("So…", "Basically…", "Well…").
LEAD_INS = {w for w in LEAN_ON if " " not in w} | {"well", "yeah", "look", "see", "and", "but", "no", "ok", "anyway", "listen"}
PHRASE = re.compile(r"^[a-z][a-z' ]{0,38}[a-z]$|^[a-z]$")


class FillerError(ValueError):
    pass


# ---- the list ----------------------------------------------------------------------------------

def phrases(conn) -> list[str]:
    """The owner's filler words, oldest first. The eight defaults are added the first time; a phrase they
    removes stays as a removed row, so the defaults never come back on their own."""
    if not row(conn, "SELECT 1 FROM filler_words LIMIT 1"):
        now = time.time() - 1.0  # before anything added after them, in their own order
        conn.executemany("INSERT OR IGNORE INTO filler_words (phrase, source, added_at) VALUES (?, 'default', ?)",
                         [(p, now + i * 1e-3) for i, p in enumerate(DEFAULTS)])
    return [r["phrase"] for r in rows(conn, "SELECT phrase FROM filler_words WHERE removed_at IS NULL ORDER BY added_at, phrase")]


def live(conn) -> dict:
    """B21: what the phone and the Octo app's live cue need: the owner's list, and whether calls on this Mac show it
    (app_config `live_cue_mac`, off until the owner turns it on). The phone keeps its own switch."""
    return {"phrases": phrases(conn), "mac": bool(config(conn, "live_cue_mac", False))}


def set_live_mac(conn, on: bool) -> dict:
    conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('live_cue_mac', ?, strftime('%s','now')) "
                 "ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (json.dumps(on),))
    return live(conn)


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z' ]", " ", (text or "").lower().replace("’", "'")).split())


def set_phrase(conn, text: str, on: bool, source: str = "typed") -> list[str]:
    phrases(conn)  # the defaults first, if they've never been added
    p = normalize(text)
    if not PHRASE.match(p) or len(p.split()) > 4:
        raise FillerError("a word or a short phrase (up to four words), letters only")
    now = time.time()
    if on:
        conn.execute("INSERT INTO filler_words (phrase, source, added_at) VALUES (?, ?, ?) "
                     "ON CONFLICT (phrase) DO UPDATE SET removed_at=NULL, added_at=excluded.added_at", (p, source, now))
    else:
        conn.execute("UPDATE filler_words SET removed_at=? WHERE phrase=?", (now, p))
    return phrases(conn)


# ---- counting ----------------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _order(phr: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(phr, key=lambda x: (-len(x), x)))  # longest first: "kind of" before "of"


def matcher(phr: list[str]) -> re.Pattern | None:
    """One pattern for the whole list: longest phrases first, each its own group, whole words only."""
    if not phr:
        return None
    parts = []
    for i, p in enumerate(_order(tuple(phr))):
        if p in HESITATIONS:
            body = "".join(f"{re.escape(c)}+" for c in dict.fromkeys(p))  # um → u+m+, hmm → h+m+
        else:
            body = r"[\s,]+".join(re.escape(w) for w in p.split())
        parts.append(f"(?P<p{i}>{body})")
    return re.compile(r"(?<![\w'])(?:" + "|".join(parts) + r")(?![\w'])", re.I)


def _key(m: re.Match, phr: list[str]) -> str:
    return _order(tuple(phr))[int(m.lastgroup[1:])]


def find(text: str, pat: re.Pattern | None, phr: list[str]) -> list[tuple[int, int, str]]:
    """Each filler in a line: (start, end, the phrase it counts as). "uh huh" and "mm hmm" are
    backchannels (listening), not hesitations, so their parts don't count."""
    if not pat or not text:
        return []
    out = []
    for m in pat.finditer(text):
        key = _key(m, phr)
        after, before = text[m.end():m.end() + 8].lower(), text[max(0, m.start() - 6):m.start()].lower()
        if key in ("uh", "mm", "hmm") and re.match(r"[\s,-]*(huh|hmm)\b", after):
            continue
        if key in ("hmm", "mm") and re.search(r"\b(uh|mm)[\s,-]*$", before):
            continue
        out.append((m.start(), m.end(), key))
    return out


def words(text: str) -> int:
    return len((text or "").split())


# ---- one conversation ----------------------------------------------------------------------------

def _diarized(conn, session_id: str) -> tuple[dict | None, set[str], dict[str, str]]:
    """(diarize output with the owner's corrections, the owner's letters, letter → confirmed name of the others)."""
    from .sessions import diarized, speakers
    _, out, _ = diarized(conn, session_id)
    if not out:
        return None, set(), {}
    sp = speakers(conn, session_id)
    mine = {s["letter"] for s in sp if s["self"]}
    names = {s["letter"]: s["name"] for s in sp if not s["self"] and s["name"] and s["state"] in ("confirmed", "auto")}
    return out, mine, names


def _off_line(conn, sess: dict, at: float | None, tr: dict | None, mine: set[str], n_words: int) -> str | None:
    """Why this conversation isn't on the line, or None if it is."""
    from .asr import language_paused
    if not tr:
        return "not transcribed yet"
    if tr.get("merged"):
        return "Hindi or mixed, merged from two drafts: they keep fillers differently"
    if language_paused(tr) or tr.get("language") != "en":
        return "not in English"
    if "whisper" in (tr.get("model") or "").lower():
        return "transcribed by Whisper (before 2 Oct): it keeps fillers differently"
    if not mine:
        return "you're not confirmed in it"
    if at is None or datetime.fromtimestamp(at).date() < LAB_START:
        return "recorded before the lab began"
    if n_words < MIN_WORDS:
        return f"under {MIN_WORDS} of your words"
    return None


def load(conn, session_id: str) -> tuple[dict | None, set[str], dict[str, str], dict | None]:
    """(diarize output with the owner's corrections, their letters, the others' names, the transcript), read once."""
    out, mine, names = _diarized(conn, session_id)
    tr_run = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='transcribe' AND is_current=1 AND status='done'",
                 session_id) if out else None
    return out, mine, names, load_json(tr_run["output"]) if tr_run else None


def conversation(conn, sess: dict, phr: list[str], pat: re.Pattern | None = None, detail: bool = False,
                 loaded: tuple | None = None) -> dict | None:
    """The owner's fillers in one conversation, with their list; None if it has no diarized lines. `loaded`:
    what `load` read, when the caller needs it too (octo/delivery.py)."""
    pat = pat if pat is not None else matcher(phr)
    out, mine, names, tr = loaded or load(conn, sess["id"])
    if not out:
        return None
    at = sess["started_at"] + sess["start_s"] if sess.get("started_at") else None
    by_phrase: dict[str, int] = {}
    spans: dict[int, list[list[int]]] = {}
    n_words = n_fill = 0
    for ln in out["lines"]:
        if ln["speaker"] not in mine:
            continue
        found = find(ln["text"], pat, phr)
        n_words += words(ln["text"])
        n_fill += len(found)
        for a, b, k in found:
            by_phrase[k] = by_phrase.get(k, 0) + 1
        if found:
            spans[ln["id"]] = [[a, b] for a, b, _ in found]
    # Who else: anyone named; how many: voices with at least 5 s of talk (not a stray fragment).
    talkers = [k for k, v in out["talk_s"].items() if v >= 5.0 and k not in mine]
    got = {
        "session_id": sess["id"], "at": at, "words": n_words, "fillers": n_fill,
        "rate": round(100 * n_fill / n_words, 2) if n_words else None,
        "by_phrase": dict(sorted(by_phrase.items(), key=lambda kv: -kv[1])),
        "with": sorted(set(names[k] for k in out["talk_s"] if k in names)),
        "size": "solo" if not talkers else "one" if len(talkers) == 1 else "group",
        "reason": _off_line(conn, sess, at, tr, mine, n_words),
    }
    got["on_line"] = got["reason"] is None
    if detail:
        got["spans"] = spans
        got["heaviest"] = heaviest(out["lines"], mine, pat, phr)
    return got


def heaviest(lines: list[dict], mine: set[str], pat: re.Pattern | None, phr: list[str], n: int = 3,
             min_words: int = 15) -> list[dict]:
    """The owner's turns (their consecutive lines) with the most fillers for their length: to hear."""
    turns: list[dict] = []
    for ln in lines:
        if ln["speaker"] not in mine:
            turns.append({})
            continue
        if not turns or not turns[-1]:
            turns.append({"start": ln["start"], "end": ln["end"], "words": 0, "fillers": 0, "lines": []})
        t = turns[-1]
        t["end"] = ln["end"]
        t["words"] += words(ln["text"])
        t["fillers"] += len(find(ln["text"], pat, phr))
        t["lines"].append(ln["id"])
    full = [t for t in turns if t and t["words"] >= min_words and t["fillers"]]
    full.sort(key=lambda t: (-t["fillers"] / t["words"], -t["fillers"]))
    return [{**t, "rate": round(100 * t["fillers"] / t["words"], 1)} for t in full[:n]]


# ---- across conversations ---------------------------------------------------------------------

def made_from(conn, sess: dict, *more) -> str:
    """What a conversation's record is made from, read cheaply: its row, its current diarize and transcribe runs, the
    owner's corrections, who its speakers are, the recording's cuts (sessions.trimmed), and `more` (the lists). Any change to these makes the record again."""
    r = row(conn, "SELECT (SELECT group_concat(stage || id || status || coalesce(finished_at, ''), ',') FROM stage_runs "
                  "WHERE target=?1 AND stage IN ('diarize', 'transcribe') AND is_current=1) AS runs, "
                  "(SELECT group_concat(id, ',') FROM corrections WHERE session_id=?1) AS fixes, "
                  "(SELECT group_concat(ss.speaker || '|' || coalesce(ss.person_id, '') || '|' || ss.state || '|' || "
                  "coalesce(ss.diarize_run, '') || '|' || coalesce(p.name, '') || '|' || coalesce(p.is_self, ''), ',') "
                  "FROM session_speakers ss LEFT JOIN people p ON p.id = ss.person_id WHERE ss.session_id=?1) AS who, "
                  "(SELECT group_concat(at_s, ',') FROM splits WHERE recording_id=?2 AND undone_at IS NULL) AS cuts",
            sess["id"], sess["recording_id"])
    return json.dumps([sess, r, str(LAB_START), *more], sort_keys=True, default=str)


def memoized(conn, memo: dict, make, *more) -> list[dict]:
    """Every active conversation's record (make(sess)), oldest first, each made again only when what it's made from
    changed (made_from): every page with a usual read every conversation in full, ~0.7 s with 70 (28 Oct). `memo` is
    the caller's own; shallow copies go out, so a caller adding to a record doesn't change the kept one."""
    out, seen = [], set()
    more = (row(conn, "PRAGMA database_list")["file"], *more)  # another lab (a test's, the demo's) is another record
    for s in rows(conn, "SELECT s.*, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                        "WHERE s.status='active'"):
        seen.add(s["id"])
        key = made_from(conn, s, *more)
        hit = memo.get(s["id"])
        if not hit or hit[0] != key:
            hit = memo[s["id"]] = (key, make(s))
        if hit[1]:
            out.append(dict(hit[1]))
    for k in set(memo) - seen:  # deleted or set aside
        memo.pop(k, None)
    out.sort(key=lambda c: c["at"] or 0)
    return out


MEMOS: list[dict] = []  # every memo kept with memoized, so Delete and Forget can drop what they hold


def memo() -> dict[str, tuple[str, dict | None]]:
    MEMOS.append(m := {})
    return m


def forget(session_id: str | None = None) -> None:
    """Drop the kept records of one conversation (deleted, or part of it), or of every one (Forget): they hold its
    words. Read again, they're made again."""
    for m in MEMOS:
        m.clear() if session_id is None else m.pop(session_id, None)


_RECORDS = memo()


def all_conversations(conn, phr: list[str] | None = None) -> list[dict]:
    phr = phrases(conn) if phr is None else phr
    pat = matcher(phr)
    return memoized(conn, _RECORDS, lambda s: conversation(conn, s, phr, pat), phr)


def pooled(cs: list[dict]) -> dict | None:
    """The rate over these conversations' words together, and a rough 95% band for ordinary variation
    (Poisson, normal approximation): a change inside it isn't evidence of anything."""
    w = sum(c["words"] for c in cs)
    f = sum(c["fillers"] for c in cs)
    if not w:
        return None
    lo, hi = max(0.0, f - 1.96 * math.sqrt(f)), f + 1.96 * math.sqrt(f + 1)
    return {"words": w, "fillers": f, "conversations": len(cs), "rate": round(100 * f / w, 2),
            "lo": round(100 * lo / w, 2), "hi": round(100 * hi / w, 2)}


def _last_words(cs: list[dict], n: int = WINDOW_WORDS) -> list[dict]:
    """The latest conversations that together hold about n of the owner's words (at least one)."""
    got, total = [], 0
    for c in reversed(cs):
        got.append(c)
        total += c["words"]
        if total >= n:
            break
    return got[::-1]


def trend(on: list[dict]) -> dict:
    """The dots (one per conversation), the pooled line, the first fortnight, and now: the latest
    ~WINDOW_WORDS words after the fortnight, so the two never share a conversation."""
    line = []
    for i, c in enumerate(on):
        p = pooled(_last_words(on[:i + 1]))
        line.append({"at": c["at"], **p})
    end = on[0]["at"] + BASELINE_DAYS * 86400 if on else 0.0
    first = [c for c in on if c["at"] < end]
    after = [c for c in on if c["at"] >= end]
    base = pooled(first) if sum(c["words"] for c in first) >= MIN_POOL_WORDS else None
    now = pooled(_last_words(after)) if sum(c["words"] for c in after) >= MIN_POOL_WORDS else None
    change = None
    if now and base:
        change = "lower" if now["hi"] < base["lo"] else "higher" if now["lo"] > base["hi"] else "within"
    return {"points": [{k: c[k] for k in ("session_id", "at", "words", "fillers", "rate", "with")} for c in on],
            "line": line, "baseline": base, "now": now, "change": change,
            "baseline_until": end if on else None, "words_after": sum(c["words"] for c in after)}


def usual_before(on: list[dict], at: float | None, exclude: str) -> dict | None:
    """The owner's rate over about WINDOW_WORDS of their words before this conversation (not counting it)."""
    if at is None:
        return None
    before = [c for c in on if c["session_id"] != exclude and c["at"] is not None and c["at"] < at]
    p = pooled(_last_words(before)) if before else None
    return p if p and p["words"] >= MIN_POOL_WORDS else None


def groups(on: list[dict]) -> dict:
    """Where they happen: pooled by person and by how many were talking, only with enough words."""
    people: dict[str, list[dict]] = {}
    for c in on:
        for name in c["with"]:
            people.setdefault(name, []).append(c)
    by_person = sorted(({"name": k, **pooled(v)} for k, v in people.items()), key=lambda g: -g["words"])
    sizes = {"solo": "just you", "one": "with one other", "group": "with two or more"}
    by_size = [{"size": k, "label": label, **pooled([c for c in on if c["size"] == k])}
               for k, label in sizes.items() if any(c["size"] == k for c in on)]
    return {"people": [g for g in by_person if g["words"] >= MIN_GROUP_WORDS],
            "sizes": [g for g in by_size if g["words"] >= MIN_GROUP_WORDS],
            "min_words": MIN_GROUP_WORDS}


def _tallies(conn, session_id: str, cands: list[str]) -> dict:
    """One conversation's counts for suggestions: the candidates' words and the words that start turns, the owner's
    and the others'."""
    pats = {c: matcher([c]) for c in cands}
    t = {"mine_n": {c: 0 for c in cands}, "theirs_n": {c: 0 for c in cands}, "mine_w": 0, "theirs_w": 0,
         "starts_me": {}, "starts_them": {}, "turns_me": 0, "turns_them": 0}
    out, mine, _ = _diarized(conn, session_id)
    prev = None
    for ln in out["lines"]:
        me = ln["speaker"] in mine
        who = "me" if me else ln["speaker"]  # The owner's two letters, if they have two, are one speaker
        t["mine_w" if me else "theirs_w"] += words(ln["text"])
        for cand, p in pats.items():
            t["mine_n" if me else "theirs_n"][cand] += len(find(ln["text"], p, [cand]))
        if who != prev:  # a new turn
            first = [w for w in normalize(ln["text"]).split()[:1] if w in LEAD_INS]
            if first:
                bucket = t["starts_me" if me else "starts_them"]
                bucket[first[0]] = bucket.get(first[0], 0) + 1
            t["turns_me" if me else "turns_them"] += 1
        prev = who
    return t


_TALLIES = memo()


def suggestions(conn, on: list[dict], phr: list[str]) -> dict:
    """Words the owner leans on more than the others in the same conversations, and how their turns start. Each
    conversation's counts are kept under its record's key (all_conversations: the list is part of it)."""
    cands = [c for c in LEAN_ON if c not in phr]
    mine_n = {c: 0 for c in cands}
    theirs_n = {c: 0 for c in cands}
    mine_w = theirs_w = 0
    starts_me: dict[str, int] = {}
    starts_them: dict[str, int] = {}
    turns_me = turns_them = 0
    for c in on:
        sid, rec = c["session_id"], _RECORDS.get(c["session_id"])
        key = rec and rec[0] + json.dumps(cands)
        hit = _TALLIES.get(sid)
        if not key or not hit or hit[0] != key:  # no record kept: counted, not kept
            hit = (key, _tallies(conn, sid, cands))
            if key:
                _TALLIES[sid] = hit
        t = hit[1]
        for cand in cands:
            mine_n[cand] += t["mine_n"][cand]
            theirs_n[cand] += t["theirs_n"][cand]
        mine_w += t["mine_w"]
        theirs_w += t["theirs_w"]
        for mine, theirs in ((starts_me, t["starts_me"]), (starts_them, t["starts_them"])):
            for w, n in theirs.items():
                mine[w] = mine.get(w, 0) + n
        turns_me += t["turns_me"]
        turns_them += t["turns_them"]
    lean = []
    for cand in cands:
        rm = 100 * mine_n[cand] / mine_w if mine_w else 0.0
        rt = 100 * theirs_n[cand] / theirs_w if theirs_w else 0.0
        if mine_n[cand] >= 3 and rm >= 0.4 and rm >= 1.5 * rt:
            lean.append({"phrase": cand, "count": mine_n[cand], "rate": round(rm, 2), "others": round(rt, 2)})
    lean.sort(key=lambda x: -x["rate"])
    begins = []
    for w, n in sorted(starts_me.items(), key=lambda kv: -kv[1]):
        share = n / turns_me if turns_me else 0.0
        theirs = starts_them.get(w, 0) / turns_them if turns_them else 0.0
        if n >= 5 and share >= 0.15 and share >= 1.5 * theirs:
            begins.append({"word": w, "turns": n, "share": round(share, 2), "others": round(theirs, 2)})
    return {"lean_on": lean[:8], "turn_starts": begins[:3]}


def summary(conn) -> dict:
    """Everything the Progress page's fillers card shows."""
    phr = phrases(conn)
    cs = all_conversations(conn, phr)
    on = [c for c in cs if c["on_line"]]
    common: dict[str, int] = {}
    for c in on:
        for k, n in c["by_phrase"].items():
            common[k] = common.get(k, 0) + n
    return {"phrases": phr, "trend": trend(on), "groups": groups(on), "suggestions": suggestions(conn, on, phr),
            "common": [{"phrase": k, "count": n} for k, n in sorted(common.items(), key=lambda kv: -kv[1])[:5]],
            "off_line": [{"session_id": c["session_id"], "at": c["at"], "reason": c["reason"]}
                         for c in cs if not c["on_line"] and c["reason"] not in ("not transcribed yet",)],
            "checks": checks_summary(conn), "window_words": WINDOW_WORDS, "min_words": MIN_WORDS}


# ---- the filler check by ear (S0) ------------------------------------------------------------

CHECK_S = 180.0


def check_window(conn, session_id: str, length: float = CHECK_S) -> dict | None:
    """The stretch of about `length` seconds with the most of the owner's speech in it."""
    out, mine, _ = _diarized(conn, session_id)
    if not out:
        return None
    mine_lines = [ln for ln in out["lines"] if ln["speaker"] in mine]
    if not mine_lines:
        return None
    best = None
    for ln in mine_lines:
        a, b = ln["start"], ln["start"] + length
        talk = sum(min(b, x["end"]) - x["start"] for x in mine_lines if a <= x["start"] < b)
        if best is None or talk > best[2]:
            best = (a, b, talk)
    a, b, talk = best
    end = max(x["end"] for x in out["lines"] if x["start"] < b)
    return {"start": round(max(0.0, a - 0.5), 2), "end": round(end + 0.5, 2), "jay_s": round(talk, 1)}


def check_candidates(conn) -> list[dict]:
    """Conversations to check by ear: on the line, audio still kept, newest first."""
    out = []
    phr = phrases(conn)
    for c in reversed(all_conversations(conn, phr)):
        if not c["on_line"]:
            continue
        s = row(conn, "SELECT r.audio_deleted_at FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?",
                c["session_id"])
        if s and not s["audio_deleted_at"]:
            out.append({k: c[k] for k in ("session_id", "at", "words", "with")})
        if len(out) >= 10:
            break
    return out


def app_count(conn, session_id: str, start: float, end: float) -> dict:
    """The app's count in the owner's lines that start in the stretch, with their list now, and the lines."""
    phr = phrases(conn)
    pat = matcher(phr)
    out, mine, _ = _diarized(conn, session_id)
    lines = [ln for ln in (out or {}).get("lines", []) if ln["speaker"] in mine and start <= ln["start"] < end]
    found = {ln["id"]: find(ln["text"], pat, phr) for ln in lines}
    return {"fillers": sum(len(v) for v in found.values()), "words": sum(words(ln["text"]) for ln in lines),
            "lines": [{**ln, "spans": [[a, b] for a, b, _ in found[ln["id"]]]} for ln in lines], "phrases": phr}


def save_check(conn, session_id: str, start: float, end: float, by_ear: int) -> dict:
    if not (0 <= start < end) or by_ear < 0 or by_ear > 2000:
        raise FillerError("a stretch and a count")
    got = app_count(conn, session_id, start, end)
    cid = new_id("fck")
    conn.execute("INSERT INTO filler_checks (id, session_id, start_s, end_s, by_ear, by_app, words, phrases, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (cid, session_id, start, end, by_ear, got["fillers"], got["words"],
                                                        json.dumps(got["phrases"]), time.time()))
    return {"id": cid, "by_ear": by_ear, "by_app": got["fillers"], "words": got["words"], "lines": got["lines"],
            "summary": checks_summary(conn)}


def checks_summary(conn) -> dict:
    got = rows(conn, "SELECT by_ear, by_app, words FROM filler_checks ORDER BY created_at")
    ear, app = sum(r["by_ear"] for r in got), sum(r["by_app"] for r in got)
    return {"checks": len(got), "by_ear": ear, "by_app": app, "words": sum(r["words"] for r in got),
            "share": round(app / ear, 2) if ear else None}


def focus_numbers(conn, started_at: float, ended_at: float | None = None) -> dict:
    """For a focus that carries the fillers number: the owner's rate since it started, and before."""
    on = [c for c in all_conversations(conn) if c["on_line"] and c["at"] is not None]
    since = [c for c in on if c["at"] >= started_at and (ended_at is None or c["at"] < ended_at)]
    before = _last_words([c for c in on if c["at"] < started_at])
    return {"since": pooled(since), "before": pooled(before) if before else None}


# ---- the recorder's own cues (B20, rec-24) -------------------------------------------------------------------------

# A cue's time minus its phrase's middle on the lab's timeline: -0.3 s (median of 11 cues near a transcript "you know",
# 21-22 Oct, learnings, Speech, 22 Oct). It was +0.4 from the through-the-air test, timed by the Mac's clock.
CUE_LAG_S = -0.3


def recorder_cues(conn, sess: dict, lines: list[dict]) -> list[dict] | None:
    """The "you know"s the recorder flashed during this conversation, read from its boots' manifests (each cue a line
    with the boot's milliseconds, placed by the boot's clock as its chunks are), each with the transcript line it most
    likely followed: the line under the moment just before it, else the last one to start before it. None when it
    isn't a recorder conversation or the recorder flashed none in it."""
    from .device import wall_clock
    if sess.get("source") != "device" or not sess.get("started_at"):
        return None
    a, b = sess["started_at"] + sess["start_s"], sess["started_at"] + sess["end_s"]
    got = []
    for r in rows(conn, "SELECT manifest FROM device_boots WHERE manifest LIKE '%\"filler\"%'"):
        clock, found = None, []
        for text in r["manifest"].splitlines():
            try:
                d = json.loads(text)
            except ValueError:
                continue
            if "clock_ms" in d:
                clock = (d["clock_ms"], d["at_ms"])  # the latest one is the best, as for chunks
            elif "filler" in d:
                found.append(d)
        for d in found:
            t = wall_clock(clock, d.get("at_ms"))
            if t is not None and a <= t <= b:
                got.append({"at": round(t - a, 2), "score": d.get("score"), "filler": d.get("filler"), "line": None})
    if not got:
        return None
    for c in sorted(got, key=lambda c: c["at"]):
        t = c["at"] - CUE_LAG_S
        under = [ln for ln in lines if ln["start"] <= t <= ln["end"] + 0.5]
        before = [ln for ln in lines if ln["start"] <= t]
        pick = under[-1] if under else before[-1] if before else None
        c["line"] = pick["id"] if pick else None
    return sorted(got, key=lambda c: c["at"])
