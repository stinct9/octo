"""How the owner showed up in one conversation, against their usual (B10, docs/backlog.md, 15 Oct).

The owner: "I want to speak clearly without hesitation like a CEO." Six delivery measures of their confirmed lines,
counted when the page is read from what's stored (no stage, no model call, nothing re-runs):

- fillers per 100 words: their own list (octo/fillers.py);
- hesitation pauses per 100 words: a silence of PAUSE_S or more between two of their words, with nobody else's
  words in it, where the word before doesn't end a sentence (. ? !). Lines break at pauses of over a second
  (stages.utterances), so this reads the transcript's words and their times, not the lines;
- restarts per 100 words: a word or two said again straight away ("I, I think", "we should, we should"), or a
  word broken off with a dash ("we sh- we need"). Repeats that are ordinary speech (very very, no no) and
  fillers (um um) don't count;
- hedges per 100 words: their own list (`hedge_words` in app_config), counted like fillers;
- pace: their words per minute while they're speaking (stretches between pauses of PAUSE_S or more);
- longest stretch: the longest they spoke without a pause of PAUSE_S, in seconds.

Pauses depend on the aligner's word times and restarts on Qwen keeping them: neither is checked by ear yet.

Voice (B27, 21 Oct), from the stored audio, a group under Listening: pitch range, loudness, and both when pushed back
(octo/tone.py: measured once per conversation and kept, so the usual outlives the audio). Not checked by ear yet.

Listening (B13, 19 Oct), from the same timed words, a group of its own under the six:

- patience: the gap from someone else's last word to the owner's first, each time the owner takes the turn; the
  conversation's median. Starting ZERO_S or more before they finish is talking over (the numbers stage counts it),
  so it isn't patience; a gap under ZERO_S either way reads as zero (speaker edges move in crosstalk);
- turns per minute: how often the talk changes hands, over the conversation's span of words;
- their longest stretch: the longest anyone else held the floor before someone took it, per person.
A floor is one speaker's words until someone else's take over; BACKCHANNEL_WORDS or fewer ("mm", "yes, okay")
after which the same person carries on don't take it. Patience shares B10's open check of the aligner's timing.

The measuring stick is the fillers': their confirmed lines in English Qwen transcripts since the lab began,
with at least fillers.MIN_WORDS of their words. Their usual is every conversation on it before this one (15 Oct: overall, not by kind), pooled; "about usual" is the middle half of those conversations' own values,
since one conversation varies more than a Poisson band allows (8 of 15 fell outside it). Numbers stay
neutral (coaching plan A4): fewer / about usual / more, never better or worse.
"""

from __future__ import annotations

import bisect
import json
import statistics

from . import fillers as FL
from . import tone as T
from .db import config

MEASURES = ("fillers", "pauses", "restarts", "hedges", "pace", "longest")
LISTENING = ("patience", "turns", "their_longest")
VOICE = T.VOICE
PER_100 = ("fillers", "pauses", "restarts", "hedges")
HEDGE_DEFAULTS = ("i think", "maybe", "probably", "just", "a bit")  # B10's list; "sort of" is a filler already
PAUSE_S = 1.0
ZERO_S = 0.2               # a gap this close to nothing reads as zero, either way
BACKCHANNEL_WORDS = 2      # an interjection this short, with the same person carrying on, doesn't take the floor
MIN_TELL_WORDS = 300       # of his words here, before a state is given
MIN_HISTORY = 5            # conversations on the line before this one, for a usual and its spread
SENTENCE_END = (".", "?", "!")
DASHES = ("-", "—", "–")
# Said twice on purpose, not a restart.
REPEATS_OK = {"very", "no", "yes", "yeah", "bye", "okay", "ok", "really", "so", "ha", "hey", "please", "right", "go",
              "come", "na", "haan", "hello", "that", "had", "is", "more", "again", "slowly", "far", "long", "many", "much"}


# ---- the hedge list ------------------------------------------------------------------------------

def hedges(conn) -> list[str]:
    return config(conn, "hedge_words", list(HEDGE_DEFAULTS))


def set_hedge(conn, text: str, on: bool) -> list[str]:
    p = FL.normalize(text)
    if not FL.PHRASE.match(p) or len(p.split()) > 4:
        raise FL.FillerError("a word or a short phrase (up to four words), letters only")
    got = [h for h in hedges(conn) if h != p] + ([p] if on else [])
    conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('hedge_words', ?, strftime('%s','now')) "
                 "ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (json.dumps(got),))
    return got


# ---- counting ----------------------------------------------------------------------------------

def timed_words(tr: dict | None, lines: list[dict], mine: set[str]) -> list[dict] | None:
    """Every transcribed word with its time and whether it's the owner's (by the line it falls in, so their
    corrections count); None if the transcript has no word times."""
    if not tr:
        return None
    ws = [w for seg in (tr.get("segments") or []) for w in seg.get("words") or []]
    ws += [w for seg in (tr.get("rejected_as_silence") or []) for w in seg.get("words") or []]
    if not ws:
        return None
    ls = sorted(lines, key=lambda ln: ln["start"])
    starts = [ln["start"] for ln in ls]
    out = []
    for w in sorted(ws, key=lambda w: w["start"]):
        i = bisect.bisect_right(starts, w["start"] + 0.01) - 1
        spk = ls[i]["speaker"] if i >= 0 and w["start"] <= ls[i]["end"] + 0.01 else None
        out.append({"w": w["w"], "start": w["start"], "end": w["end"], "me": spk in mine, "spk": spk})
    return out


def runs(words: list[dict]) -> list[list[dict]]:
    """His words in unbroken runs: anyone else's word (or one in no line) ends a run."""
    out: list[list[dict]] = [[]]
    for w in words:
        if w["me"]:
            out[-1].append(w)
        elif out[-1]:
            out.append([])
    return [r for r in out if r]


def restarts(run: list[dict], skip: set[str]) -> list[dict]:
    """Restarts in one run of his words: a word or two said again at once, or a word broken off."""
    toks = [FL.normalize(w["w"]) for w in run]
    out, i = [], 0
    while i < len(run):
        if run[i]["w"].strip().endswith(DASHES):
            out.append({"start": run[i]["start"], "end": run[min(i + 1, len(run) - 1)]["end"]})
            i += 1
            continue
        for k in (2, 1):
            a = toks[i:i + k]
            if (len(toks[i + k:i + 2 * k]) == k and toks[i + k:i + 2 * k] == a and all(a)
                    and " ".join(a) not in skip and not (k == 1 and a[0] in REPEATS_OK)):
                j = i + k
                while toks[j + k:j + 2 * k] == a:  # "I, I, I think" is one restart
                    j += k
                out.append({"start": run[i]["start"], "end": run[j + k - 1]["end"]})
                i = j + k
                break
        else:
            i += 1
    return out


def timing(words: list[dict], skip: set[str]) -> dict:
    """Pauses, restarts, speaking time and the longest stretch, from his timed words."""
    pauses, stretches, rs = [], [], []
    for run in runs(words):
        rs += restarts(run, skip)
        first = 0
        for i, a in enumerate(run):
            b = run[i + 1] if i + 1 < len(run) else None
            if b is not None and b["start"] - a["end"] < PAUSE_S:
                continue
            stretches.append({"start": run[first]["start"], "end": a["end"], "words": i + 1 - first})
            if b is not None:
                if not a["w"].rstrip().endswith(SENTENCE_END):
                    pauses.append({"start": a["end"], "end": b["start"]})
                first = i + 1
    speak = sum(s["end"] - s["start"] for s in stretches)
    longest = max(stretches, key=lambda s: s["end"] - s["start"], default=None)
    return {"pauses": pauses, "restarts": rs, "speak_s": speak, "timed_words": sum(s["words"] for s in stretches),
            "longest": longest}


def floors(words: list[dict]) -> list[dict]:
    """Who held the floor, in order. The owner's letters are one speaker, "me"; words in nobody's line are left out."""
    out: list[dict] = []
    for w in words:
        if w["spk"] is None:
            continue
        k = "me" if w["me"] else w["spk"]
        if out and out[-1]["spk"] == k:
            out[-1].update(end=max(out[-1]["end"], w["end"]), words=out[-1]["words"] + 1)
            continue
        if len(out) >= 2 and out[-2]["spk"] == k and out[-1]["words"] <= BACKCHANNEL_WORDS:
            out.pop()  # a backchannel: they carry on
            out[-1].update(end=max(out[-1]["end"], w["end"]), words=out[-1]["words"] + 1)
            continue
        out.append({"spk": k, "start": w["start"], "end": w["end"], "words": 1})
    return out


def listening(words: list[dict]) -> dict:
    """Patience, changes of hands and the others' longest stretches, from everyone's timed words."""
    fs = floors(words)
    gaps, over, longest = [], 0, {}
    for prev, f in zip(fs, fs[1:]):
        if f["spk"] != "me" or prev["spk"] == "me":
            continue
        g = f["start"] - prev["end"]
        if g <= -ZERO_S:
            over += 1
        else:
            gaps.append({"spk": prev["spk"], "gap": 0.0 if g < ZERO_S else round(g, 2), "start": prev["end"], "end": f["start"]})
    for f in fs:
        if f["spk"] != "me" and f["end"] - f["start"] > longest.get(f["spk"], {}).get("s", -1):
            longest[f["spk"]] = {"s": round(f["end"] - f["start"], 1), "start": f["start"], "end": f["end"], "words": f["words"]}
    return {"gaps": gaps, "over": over, "changes": max(0, len(fs) - 1),
            "span_s": fs[-1]["end"] - fs[0]["start"] if fs else 0.0, "longest": longest}


def conversation(conn, sess: dict, lists: dict, detail: bool = False) -> dict | None:
    """One conversation's record: the fillers' (octo/fillers.py) with the other five measures' counts."""
    loaded = FL.load(conn, sess["id"])
    out, mine, names, tr = loaded
    c = FL.conversation(conn, sess, lists["fillers"], lists["fillers_pat"], detail=detail, loaded=loaded)
    if not c:
        return None
    hedged: dict[str, int] = {}
    for ln in out["lines"]:
        if ln["speaker"] in mine:
            for _, _, k in FL.find(ln["text"], lists["hedges_pat"], lists["hedges"]):
                hedged[k] = hedged.get(k, 0) + 1
    tw = timed_words(tr, out["lines"], mine)
    t = timing(tw, lists["skip"]) if tw and any(w["me"] for w in tw) else None
    c.update({"hedges": sum(hedged.values()), "hedges_by_phrase": dict(sorted(hedged.items(), key=lambda kv: -kv[1])),
              "pauses": len(t["pauses"]) if t else None, "restarts": len(t["restarts"]) if t else None,
              "speak_s": t["speak_s"] if t else None, "timed_words": t["timed_words"] if t else None,
              "longest_s": round(t["longest"]["end"] - t["longest"]["start"], 1) if t and t["longest"] else None})
    ls = listening(tw) if t and c["size"] != "solo" else None  # nobody else here: nothing to listen to
    c.update({"gaps": [[g["spk"], g["gap"]] for g in ls["gaps"]] if ls else None, "over": ls["over"] if ls else None,
              "changes": ls["changes"] if ls else None, "span_s": ls["span_s"] if ls else None,
              "their_longest": {k: v["s"] for k, v in ls["longest"].items()} if ls else None})
    c["voice_src"] = T.spans(tw) if t else None  # tone.fill reads the audio, where it's shown
    if detail and t:
        c["moments"] = {"pauses": sorted(t["pauses"], key=lambda p: p["start"] - p["end"])[:3],
                        "restarts": t["restarts"][:3], "longest": t["longest"],
                        "waits": sorted(ls["gaps"], key=lambda g: -g["gap"])[:3] if ls else [],
                        "their_longest": sorted(({**v, "letter": k, "name": names.get(k)} for k, v in ls["longest"].items()),
                                                key=lambda v: -v["s"]) if ls else []}
    return c


def value(c: dict, m: str) -> float | None:
    if m in PER_100:
        return 100 * c[m] / c["words"] if c[m] is not None and c["words"] else None
    if m == "pace":
        return 60 * c["timed_words"] / c["speak_s"] if c["speak_s"] else None
    if m == "patience":
        return statistics.median(g for _, g in c["gaps"]) if c["gaps"] else None
    if m == "turns":
        return 60 * c["changes"] / c["span_s"] if c["span_s"] else None
    if m == "their_longest":
        return max(c["their_longest"].values()) if c["their_longest"] else None
    if m in VOICE:
        return c.get(m)
    return c["longest_s"]


def pooled(cs: list[dict], m: str) -> float | None:
    """One number over these conversations together; the longest stretch, a maximum, takes their median."""
    cs = [c for c in cs if value(c, m) is not None]
    if not cs:
        return None
    if m in PER_100:
        return 100 * sum(c[m] for c in cs) / sum(c["words"] for c in cs)
    if m == "pace":
        return 60 * sum(c["timed_words"] for c in cs) / sum(c["speak_s"] for c in cs)
    if m == "patience":  # every wait together; a median, so one long silence doesn't carry it
        return statistics.median(g for c in cs for _, g in c["gaps"])
    if m == "turns":
        return 60 * sum(c["changes"] for c in cs) / sum(c["span_s"] for c in cs)
    return statistics.median(value(c, m) for c in cs)


def lists(conn) -> dict:
    fil, hed = FL.phrases(conn), hedges(conn)
    return {"fillers": fil, "fillers_pat": FL.matcher(fil), "hedges": hed, "hedges_pat": FL.matcher(hed),
            "skip": set(fil) | FL.HESITATIONS}


def all_conversations(conn, ls: dict | None = None) -> list[dict]:
    """Every active conversation's record, oldest first (fillers.all_conversations, with the rest)."""
    ls = ls or lists(conn)
    return FL.memoized(conn, _RECORDS, lambda s: conversation(conn, s, ls), ls["fillers"], ls["hedges"])


_RECORDS = FL.memo()


def row_for(c: dict, before: list[dict], m: str, min_words: int = MIN_TELL_WORDS) -> dict:
    """One row of the glance: this conversation's number, his usual, and where it sits in his spread."""
    v = value(c, m)
    hist = [x for x in before if value(x, m) is not None]
    usual = pooled(hist, m) if len(hist) >= MIN_HISTORY else None
    lo = hi = None
    if usual is not None:
        lo, _, hi = statistics.quantiles([value(x, m) for x in hist], n=4)
    if v is None and c.get("voice_gone"):
        state = "no_audio"  # deleted at 60 days before its voice was measured
    elif v is None:
        state = "unmeasured"  # no word times in this transcript
    elif not c["on_line"]:
        state = "off"
    elif c["words"] < min_words:
        state = "short"
    elif usual is None:
        state = "no_usual"
    else:
        state = "low" if v < lo else "high" if v > hi else "about"
        if abs(v - usual) < T.NEAR.get(m, 0):
            state = "about"
    groups, ones = pooled([x for x in hist if x["size"] == "group"], m), pooled([x for x in hist if x["size"] == "one"], m)
    words = lambda size: sum(x["words"] for x in hist if x["size"] == size)
    r2 = lambda x: None if x is None else round(x, 2)
    return {"measure": m, "value": r2(v), "usual": r2(usual), "lo": r2(lo), "hi": r2(hi), "state": state,
            "conversations": len(hist),
            "groups_higher": bool(c["size"] == "group" and groups is not None and ones is not None
                                  and words("group") >= FL.MIN_GROUP_WORDS and words("one") >= FL.MIN_GROUP_WORDS
                                  and groups > ones)}


def for_session(conn, sess: dict) -> dict | None:
    """The glance for one conversation, with what each row opens to; None without his words."""
    ls = lists(conn)
    c = conversation(conn, sess, ls, detail=True)
    if not c or not c["words"]:
        return None
    before = [x for x in all_conversations(conn, ls) if x["on_line"] and x["session_id"] != c["session_id"]
              and x["at"] is not None and c["at"] is not None and x["at"] < c["at"]]
    T.fill(conn, [c, *before[::-1]], T.PAGE_BUDGET_S)  # this one first, then the nearest before it
    return {"rows": [row_for(c, before, m) for m in MEASURES], "words": c["words"], "on_line": c["on_line"],
            "listening": [row_for(c, before, m) for m in LISTENING] if c["size"] != "solo" else [],
            "taken": len(c["gaps"] or []), "over": c["over"], "changes": c["changes"], "span_s": c["span_s"],
            "voice": [row_for(c, before, m) for m in VOICE], "voice_s": c["voice_s"], "pushed_s": c["pushed_s"],
            "pushed_turns": c["pushed_turns"],
            "reason": c["reason"], "size": c["size"], "min_words": MIN_TELL_WORDS,
            "fillers": {k: c[k] for k in ("words", "fillers", "rate", "by_phrase", "on_line", "reason", "spans", "heaviest")}
                       | {"phrases": ls["fillers"]},
            "hedges": {"phrases": ls["hedges"], "by_phrase": c["hedges_by_phrase"], "count": c["hedges"]},
            "speak_s": c["speak_s"], "timed_words": c["timed_words"],
            "moments": c.get("moments") or {"pauses": [], "restarts": [], "longest": None, "waits": [], "their_longest": []}}


def measure_voice(conn) -> None:
    """Every conversation's voice numbers measured and kept (`octo process`, each pass): new ones, and any whose audio
    goes at 60 days in this pass."""
    T.fill(conn, all_conversations(conn))


# ---- a focus that carries one of these (octo/measures.py) ------------------------------------

def focus_numbers(conn, m: str, started_at: float, ended_at: float | None = None) -> dict:
    """Since a focus started, against about fillers.WINDOW_WORDS of his words before it."""
    cs = all_conversations(conn)
    if m in VOICE:
        T.fill(conn, cs)
    on = [c for c in cs if c["on_line"] and c["at"] is not None and value(c, m) is not None]
    since = [c for c in on if c["at"] >= started_at and (ended_at is None or c["at"] < ended_at)]
    before = FL._last_words([c for c in on if c["at"] < started_at])
    as_value = lambda cs: cs and {"value": round(pooled(cs, m), 2), "lo": None, "hi": None, "conversations": len(cs),
                                  "words": sum(c["words"] for c in cs)} or None
    return {"since": as_value(since), "before": as_value(before)}


def number(conn, m: str, session_id: str) -> float | None:
    """This conversation's own number, for the focus check on its page."""
    from .db import row
    s = row(conn, "SELECT s.*, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", session_id)
    c = conversation(conn, s, lists(conn)) if s else None
    if c and m in VOICE:
        T.fill(conn, [c])
    v = value(c, m) if c and c["on_line"] else None
    return None if v is None else round(v, 2)
