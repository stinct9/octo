"""Two discussions in one conversation: suggesting where to split, and splitting.

find_conversations only splits at 3 minutes of silence. Back-to-back discussions with different
people (one meeting ending and another starting a minute later) end up as one conversation. The
signal is that the people change: around a pause, almost nobody who was talking before is talking
after. The owner themselves is left out of the comparison: they're in every conversation.

A split is stored per recording (`splits`) and fed to find_conversations as an input, so it
survives any re-run. The owner splits by hand; since 9 Oct the lab also splits by itself when the change is
clear (`auto`, people plan 4.5), and either can be joined back (`join_back`, one cut at a time). The owner can cut at
several points at once (`split_at`, 22 Oct). The first part keeps the session (and its labels); each later one
becomes a new conversation and goes through the pipeline on its own -- which also separates its
speakers on their own, the better for it.
"""

from __future__ import annotations

import re
import time

from .stages import apply_splits as apply  # noqa: F401  (a stage's, since 5 Nov: risk 8)

# A split goes in a pause, never mid-speech. 20 s until 17 Oct: the owner's 16 Oct 20:16 was three calls back to
# back, 12.7 s and 8.6 s apart with nobody in common, and wasn't offered. At 8 s, across all 36 conversations,
# those two are the only new suggestions (at 5 s, three more, two of them with shared talk).
MIN_PAUSE_S = 8.0
WINDOW_S = 600.0      # who talks in the 10 minutes before and after
MIN_PART_S = 180.0    # both parts at least 3 minutes
MAX_OVERLAP = 0.2     # at most a fifth of the talk on either side by people from the other side
MAJOR_SHARE = 0.1     # "was in the discussion": at least a tenth of that side's talk
# The lab splits by itself only when the people change this clearly (in the 10 minutes around the pause and
# over the whole conversation) and the first meeting visibly ended: the others silent for a minute (the owner
# talking in between doesn't count), or a goodbye just before and a hello just after. 9 Oct: the owner said 8 Oct
# 15:14 (85 s pause, 0% shared) and 20:32 (72 s, 1%) were two conversations each and 1 Oct 19:58 (28 s, 3%)
# was one. 17 Oct: 16 Oct 20:16 was three calls back to back, 12.7 s apart (with a goodbye and a hello)
# and 8.6 s apart (the others silent 77 s); a pause of at least 60 s, the bar until then, caught neither.
AUTO_MIN_GAP_S = 60.0
AUTO_MAX_OVERLAP = 0.05
EDGE_S = 90.0  # where a goodbye or hello is looked for, before and after
# A suggestion the page puts first (31 Oct, the owner chose "suggest, don't cut"): nobody shared and the owner barely
# talking on one side. Replayed over 78 conversations: 30 Oct 21:29 (0.00 shared, the owner 0% before and 60% after,
# an 11 s pause) was two conversations and the lab didn't cut it; 1 Oct 19:58 (0.03) and 29 Oct 20:43 (0.02, the
# owner on both sides) were one; 22 Oct 21:31 (0.00, the owner 29% then 0%) the owner wasn't sure.
STRONG_MAX_OVERLAP = 0.01
STRONG_OWN_SHARE = 0.05  # until 11 Nov; kept for the record, no longer part of `strong`
# A second tier (pipeline review 11 Nov, Jay's call): a change this complete is cut without the minute of silence or
# the goodbye. Replayed over every split so far (`python -m bench.splitreplay`): the owner's 12 measurable hand cuts were
# at 0.00 shared (9) or 0.03 (3); the others silent 11-58 s at the 10 the first tier missed; the two places the owner
# called one conversation were at 0.02 and 0.03. Only recordings made from CLEAR_FROM on and before any review exists,
# so no conversation of before re-runs and no review is made again.
CLEAR_MAX_OVERLAP = 0.01
CLEAR_FROM = 1794421800  # 12 Nov 2026, 00:00 +05:30
# Neither tier cuts where the owner talked through the change: at least half of the others' silence filled by the
# owner's own talk is the owner carrying on with someone new, as likely one conversation as two, so it's only
# suggested (first on the page). 13 Nov: the lab cut 12 Nov 21:17 (one person before, another after, the others silent
# 63 s, the owner talking for 0.67 of it) and the owner said it was one. Replayed over every split
# (`python -m bench.splitreplay`, from the parts as heard again apart): 11 of the 12 hand cuts are at 0.00-0.26, one
# at 0.58; of the lab's 7, 12 Nov 21:17 is at 0.63, 12 Nov 21:03 at 0.55 (0.12 as the lab saw it then: still cut) and
# 16 Oct's second call at 0.73, a real change the lab cut that is now suggested instead. Shared words across the cut
# didn't tell them apart (learnings, Splitting, 13 Nov).
THROUGH_SHARE = 0.5
# shortcut: English only, from 16 Oct's calls; add Hindi words once a Hindi meeting switch is seen.
GOODBYE = re.compile(r"\b(bye|goodbye|see you|talk (to you )?later|thanks everyone|thank you (all|everyone|so much)"
                     r"|have a good|take care|cheers)\b")
HELLO = re.compile(r"\b(hi|hello|hey|good (morning|afternoon|evening)|can you hear|can you see my screen|how are you)\b")


def _talk(turns: list[dict], a: float, b: float, ignore: set[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in turns:
        if t["speaker"] in ignore:
            continue
        o = min(t["end"], b) - max(t["start"], a)
        if o > 0:
            out[t["speaker"]] = out.get(t["speaker"], 0.0) + o
    return out


def _own_share(turns: list[dict], a: float, b: float, me: set[str]) -> float:
    talk = _talk(turns, a, b, set())
    return sum(v for s, v in talk.items() if s in me) / (sum(talk.values()) or 1)


def _carry(frm: dict[str, float], to: dict[str, float]) -> float:
    """Share of `to`'s talk by people who were really in `frm`."""
    total_from, total_to = sum(frm.values()), sum(to.values())
    major = {s for s, v in frm.items() if v >= MAJOR_SHARE * total_from}
    return sum(v for s, v in to.items() if s in major) / total_to


def _candidates(turns: list[dict], ignore: set[str]) -> list[dict]:
    """Every pause where the people change: [{"at", "pause_s", "gap_s", "through", "before", "after", "overlap",
    "strong"}]. `gap_s` is how long the others were silent around it (the owner left out), `through` the share of
    that silence the owner talked through (THROUGH_SHARE); `overlap` the more shared of
    the 10 minutes on each side and the whole conversation on each side; `strong` see STRONG_*."""
    turns = sorted(turns, key=lambda t: t["start"])
    if len(turns) < 2:
        return []
    first, last = turns[0]["start"], max(t["end"] for t in turns)
    found = []
    end_so_far = turns[0]["end"]
    for t in turns[1:]:
        pause = t["start"] - end_so_far
        if pause >= MIN_PAUSE_S:
            at = round((end_so_far + t["start"]) / 2, 1)
            if at - first >= MIN_PART_S and last - at >= MIN_PART_S:
                before = _talk(turns, at - WINDOW_S, at, ignore)
                after = _talk(turns, at, at + WINDOW_S, ignore)
                if sum(before.values()) >= 30 and sum(after.values()) >= 30:
                    whole_b, whole_a = _talk(turns, first, at, ignore), _talk(turns, at, last, ignore)
                    overlap = max(_carry(before, after), _carry(after, before),
                                  _carry(whole_b, whole_a), _carry(whole_a, whole_b))
                    if overlap <= MAX_OVERLAP:
                        others = [x for x in turns if x["speaker"] not in ignore]
                        g0 = max(x["end"] for x in others if x["end"] <= at)
                        g1 = min(x["start"] for x in others if x["start"] >= at)
                        gap = g1 - g0
                        own = sum(max(0.0, min(x["end"], g1) - max(x["start"], g0)) for x in turns if x["speaker"] in ignore)
                        # Since 11 Nov whatever the owner's share: the owner's share missed both hand cuts made
                        # after 31 Oct (2 Nov, 4 Nov), each at 0.00 shared.
                        strong = bool(ignore) and overlap <= STRONG_MAX_OVERLAP
                        found.append({"at": at, "pause_s": round(pause, 1), "gap_s": round(gap, 1),
                                      "overlap": round(overlap, 2), "strong": strong,
                                      "through": round(own / gap, 2) if gap > 0 else 0.0,
                                      "before": sorted(before, key=lambda s: -before[s]),
                                      "after": sorted(after, key=lambda s: -after[s])})
        end_so_far = max(end_so_far, t["end"])
    return found


def suggest(turns: list[dict], ignore: set[str] = frozenset()) -> list[dict]:
    """Places where the people change, best first (see `_candidates`). `at` is seconds into the
    conversation; `before`/`after` are the speakers on each side."""
    found = sorted(_candidates(turns, set(ignore)), key=lambda f: (f["overlap"], -f["pause_s"]))
    # One suggestion per change: the clearest within each stretch of nearby candidates.
    kept: list[dict] = []
    for f in found:
        if all(abs(f["at"] - k["at"]) >= MIN_PART_S for k in kept):
            kept.append(f)
    return kept


def suggestions(conn, sess: dict, turns: list[dict], me: set[str]) -> list[dict]:
    """For the page: `suggest`, less any near a place the owner said is one conversation (It's one, or Join back),
    the same rule `auto` keeps to."""
    undone = [r[0] - sess["start_s"] for r in conn.execute(
        "SELECT at_s FROM splits WHERE recording_id=? AND undone_at IS NOT NULL", (sess["recording_id"],))]
    return [f for f in suggest(turns, me) if all(abs(f["at"] - u) >= MIN_PART_S for u in undone)]


def keep_whole(conn, sess: dict, at: float) -> None:
    """It's one conversation (`at`, seconds in): kept as a split already undone, so the page stops suggesting it
    and the lab never cuts near it. Splitting there by hand later still works (`add`)."""
    now = time.time()
    conn.execute("INSERT INTO splits (recording_id, at_s, created_at, by, undone_at) VALUES (?, ?, ?, 'owner', ?) "
                 "ON CONFLICT (recording_id, at_s) DO NOTHING", (sess["recording_id"], round(sess["start_s"] + at, 2), now, now))


def ended(lines: list[dict], at: float) -> bool:
    """A goodbye in the 90 s before `at` and a hello in the 90 s after: one meeting ending, the next beginning."""
    def said(a: float, b: float) -> str:
        return " ".join(ln.get("text", "") for ln in lines if a <= ln["start"] < b).lower()
    return bool(GOODBYE.search(said(at - EDGE_S, at)) and HELLO.search(said(at, at + EDGE_S)))


def split_now(conn, session: dict, at_s: float, by: str = "owner") -> str:
    """One cut (the lab's, and the owner's single split): see `split_at`. Returns the second part's id."""
    return split_at(conn, session, [at_s], by)[0]


def split_at(conn, session: dict, cuts: list[float], by: str = "owner") -> list[str]:
    """Record the splits (recording time, inside the session) and apply them to the sessions at once, rather
    than when find_conversations next runs: the owner sees the new conversations straight away. N cuts make
    N+1 parts: the first keeps the session (and its labels), each later one is a new session. Returns the
    later parts' ids, in order. The re-run that the new splits trigger produces the same boundaries
    (sessions are keyed on their start)."""
    import json

    from .db import new_id, row

    for at in cuts:
        add(conn, session["recording_id"], at, by)
    fc = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='find_conversations' AND is_current=1",
             session["recording_id"])
    conv = next((c for c in json.loads(fc["output"])["conversations"] if abs(c["start"] - session["start_s"]) < 0.01),
                None) if fc else None
    conv = conv or {"start": session["start_s"], "end": session["end_s"], "speech_s": session["speech_s"], "speech": []}
    first, *later = apply([{**conv, "end": session["end_s"]}], cuts, 0.0)
    conn.execute("UPDATE sessions SET end_s=?, speech_s=? WHERE id=?", (first["end"], first["speech_s"], session["id"]))
    ids = []
    for part in later:
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT (recording_id, start_s) DO UPDATE SET end_s=excluded.end_s, speech_s=excluded.speech_s, "
                     "status='active'", (new_id("ses"), session["recording_id"], part["start"], part["end"], part["speech_s"],
                                         time.time()))
        ids.append(row(conn, "SELECT id FROM sessions WHERE recording_id=? AND start_s=?", session["recording_id"],
                       part["start"])["id"])
    return ids


def for_recording(conn, recording_id: str) -> list[float]:
    return [r[0] for r in conn.execute("SELECT at_s FROM splits WHERE recording_id=? AND undone_at IS NULL "
                                       "ORDER BY at_s", (recording_id,))]


def add(conn, recording_id: str, at_s: float, by: str = "owner") -> None:
    """A split there, by the owner or the lab. The owner splitting where one was joined back splits it again."""
    conn.execute("INSERT INTO splits (recording_id, at_s, created_at, by) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (recording_id, at_s) DO UPDATE SET undone_at=NULL, by=excluded.by",
                 (recording_id, round(at_s, 2), time.time(), by))


def auto(conn, session_id: str) -> str | None:
    """Split by the lab, once identify has found the owner (they're in both parts and are left out of the
    comparison), where the people change clearly. Before speakers are asked about and before the review,
    so nothing is spent on a blend. Not once the owner has answered or rated its review, nor where a split was
    joined back. The second tier (CLEAR_*) only on recordings from CLEAR_FROM and before any review is made. Never where the owner
    talked through the change (THROUGH_SHARE): that's suggested. Returns the second part's session id, or None."""
    from . import sessions as SV
    from .db import row
    from .runs import pinned_review

    sess = row(conn, "SELECT * FROM sessions WHERE id=?", session_id)
    if not sess or sess["status"] != "active" or sess["reviewed_at"] or pinned_review(conn, session_id):
        return None
    _, out, _ = SV.diarized(conn, session_id)
    me = {sp["letter"] for sp in SV.speakers(conn, session_id) if sp["self"]}
    if not out or not me:
        return None
    undone = [r[0] for r in conn.execute("SELECT at_s FROM splits WHERE recording_id=? AND undone_at IS NOT NULL",
                                         (sess["recording_id"],))]
    clear_tier = (conn.execute("SELECT created_at FROM recordings WHERE id=?", (sess["recording_id"],)).fetchone()[0] >= CLEAR_FROM
                  and not conn.execute("SELECT 1 FROM stage_runs WHERE stage='insight' AND target=? AND status='done' LIMIT 1",
                                       (session_id,)).fetchone())
    # Every candidate, clearest first: a nearby clearer one that doesn't qualify mustn't hide one that does.
    for f in sorted(_candidates(out["turns"], me), key=lambda f: f["overlap"]):
        at = sess["start_s"] + f["at"]
        first = f["overlap"] <= AUTO_MAX_OVERLAP and (f["gap_s"] >= AUTO_MIN_GAP_S or ended(out["lines"], f["at"]))
        if (first or (clear_tier and f["overlap"] <= CLEAR_MAX_OVERLAP)) and f["through"] < THROUGH_SHARE and all(abs(at - u) >= MIN_PART_S for u in undone):
            return split_now(conn, sess, at, by="lab")
    return None


def _neighbours(conn, sess: dict) -> tuple[dict | None, dict | None]:
    """The split at this conversation's start and at its end, if any (rows of `splits`)."""
    from .db import rows

    out: list[dict | None] = [None, None]
    for r in rows(conn, "SELECT * FROM splits WHERE recording_id=? AND undone_at IS NULL", sess["recording_id"]):
        if abs(r["at_s"] - sess["start_s"]) < 0.01:
            out[0] = r
        elif abs(r["at_s"] - sess["end_s"]) < 0.01:
            out[1] = r
    return out[0], out[1]


def here(conn, sess: dict) -> dict | None:
    """For the page: this conversation was split from the one before or after it, by whom, and that
    one's id (for Join back). The split at its start counts first: that's how this one began."""
    from .db import row

    start, end = _neighbours(conn, sess)
    for r, side in ((start, "before"), (end, "after")):
        if not r:
            continue
        other = row(conn, "SELECT id FROM sessions WHERE recording_id=? AND status='active' AND " +
                    ("abs(end_s - ?) < 0.01" if side == "before" else "abs(start_s - ?) < 0.01"),
                    sess["recording_id"], r["at_s"])
        return {"by": r["by"], "side": side, "other": other["id"] if other else None,
                "at": round(r["at_s"] - sess["start_s"], 1)}
    return None


def join_back(conn, session_id: str) -> str:
    """Undo the split at this conversation's start (else its end): the two become one again, kept in the
    first's session. The split row stays, marked undone, so the lab doesn't split there again. Answers
    given in the second part go with it (it's superseded, kept); the whole is worked out again.
    Returns the joined session's id. KeyError if there's no split to undo."""
    from .db import row

    sess = row(conn, "SELECT * FROM sessions WHERE id=? AND status='active'", session_id)
    if not sess:
        raise KeyError(session_id)
    start, end = _neighbours(conn, sess)
    r = start or end
    if not r:
        raise KeyError(session_id)
    first = row(conn, "SELECT * FROM sessions WHERE recording_id=? AND status='active' AND abs(end_s - ?) < 0.01",
                sess["recording_id"], r["at_s"]) if r is start else sess
    second = sess if r is start else row(conn, "SELECT * FROM sessions WHERE recording_id=? AND status='active' "
                                               "AND abs(start_s - ?) < 0.01", sess["recording_id"], r["at_s"])
    conn.execute("UPDATE splits SET undone_at=? WHERE recording_id=? AND at_s=?", (time.time(), r["recording_id"], r["at_s"]))
    if not first or not second:  # the other part isn't a conversation (too little speech): finding redoes it
        return (first or second)["id"]
    # At once, as split_now does, rather than when find_conversations next runs; that re-run agrees.
    conn.execute("UPDATE sessions SET end_s=?, speech_s=? WHERE id=?",
                 (second["end_s"], round(first["speech_s"] + second["speech_s"], 1), first["id"]))
    conn.execute("UPDATE sessions SET status='superseded' WHERE id=?", (second["id"],))
    return first["id"]
