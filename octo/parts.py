"""One discussion in parts (B35, 30 Oct): neighbouring conversations read as one at the review, never in the audio.

find_conversations cuts at 3 minutes of silence, `cap_length` cuts anything over an hour, and the recorder ends a
recording after three silent minutes, so one meeting can come out as two or three conversations. Each stays a
conversation of its own (its recording, audio, transcript, speakers, numbers, marks, fillers, commitments): nothing is
transcribed again and no audio is stitched. A link in `parts` says two neighbours are one discussion; a chain of links
is a group, its first part the head. One review reads the parts' transcripts in order with the gap marked (`stitch`)
and lives on the head; the list and Progress show the group once, its page lists the parts.

The lab groups (`auto`, after identify): an hour-cap cut always, unless the people clearly change (split.auto's own
test); across a pause in one recording, or across recordings up to 10 minutes apart, when at least half of each side's
talk around the boundary (the owner left out) is by people major on the other side, the lab's guesses counted. Each
boundary is the lab's to decide once: never where a link is or was (the owner's "Not one discussion" keeps the row,
undone) or where a split is or was. The owner groups and ungroups by hand.

Answered parts are grouped too, and their answers stay where they are. A whole review is made by itself only while no
part is answered; otherwise only when the owner asks (Redo the review), so grouping never spends a call on its own
over something the owner has answered.
"""

from __future__ import annotations

import time

from .db import row, rows
from .split import AUTO_MAX_OVERLAP, WINDOW_S, _carry, _talk

GAP_S = 178.0           # find_conversations' 3 minutes, less its pad: nearer than this in one recording is the hour cap's cut
AUTO_MAX_GAP_S = 600.0  # the lab groups across a pause or recordings at most 10 minutes apart
AUTO_MIN_CARRY = 0.5    # ... when at least half of each side's talk is by people major on the other (30 Oct)
MIN_TALK_S = 30.0       # others' talk on each side, or there's nothing to compare
HAND_MAX_GAP_S = 1800.0  # by hand, the conversation right before or after, up to half an hour apart
NEAR_S = 2.0            # a split this near the boundary is the boundary


def _active(conn) -> list[dict]:
    """Every active conversation by when it began (wall clock), with `at` and `end_at`."""
    return rows(conn, "SELECT s.*, coalesce(r.started_at, r.created_at) + s.start_s AS at, "
                      "coalesce(r.started_at, r.created_at) + s.end_s AS end_at FROM sessions s "
                      "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active' ORDER BY at, s.id")


def groups(conn) -> list[list[dict]]:
    """Every group of two or more parts, each in order. A link counts while both are active and still neighbours: a
    split or a new conversation between them leaves it dormant (the lab may then group the new neighbours)."""
    order = _active(conn)
    nxt = {a["id"]: b for a, b in zip(order, order[1:])}
    linked = {r["first_id"]: r["second_id"] for r in rows(conn, "SELECT first_id, second_id FROM parts WHERE undone_at IS NULL")}
    seconds = {b for a, b in linked.items() if nxt.get(a, {}).get("id") == b}
    out = []
    for s in order:
        if s["id"] in seconds or linked.get(s["id"]) not in seconds or nxt[s["id"]]["id"] != linked[s["id"]]:
            continue
        g = [s]
        while (b := linked.get(g[-1]["id"])) in seconds and nxt[g[-1]["id"]]["id"] == b and len(g) < 50:
            g.append(nxt[g[-1]["id"]])
        out.append(g)
    return out


def heads(conn) -> dict[str, str]:
    """Each part of a group → its head (the head too)."""
    return {s["id"]: g[0]["id"] for g in groups(conn) for s in g}


def group(conn, sid: str) -> list[dict]:
    """The parts this conversation is one of, in order; [] when it stands alone."""
    return next((g for g in groups(conn) if any(s["id"] == sid for s in g)), [])


def whole(conn, sid: str) -> list[dict]:
    """The parts the review on this conversation reads: its group when it's the head, else []."""
    g = group(conn, sid)
    return g if g and g[0]["id"] == sid else []


def answered(conn, sid: str) -> bool:
    from .planning import pinned_review
    return bool(row(conn, "SELECT 1 FROM sessions WHERE id=? AND reviewed_at IS NOT NULL", sid) or pinned_review(conn, sid))


def is_whole(run: dict | None) -> bool:
    """A review run that read a whole group."""
    import json
    return bool(run and run.get("params") and (json.loads(run["params"]) or {}).get("parts"))


def held(conn, sid: str) -> bool:
    """Its own review isn't made now: it's a later part (the head's review reads it), or the head of a group with
    another part answered, until the owner asks for the whole (Redo the review: `redo_review`)."""
    g = group(conn, sid)
    if not g:
        return False
    if g[0]["id"] != sid:
        return True
    from .pipeline import current
    cur = current(conn, sid, "insight")
    asked = row(conn, "SELECT redo_review FROM sessions WHERE id=?", sid)["redo_review"]
    if is_whole(cur) or (asked and asked in ("whole", cur and cur["id"])):
        return False
    return any(answered(conn, s["id"]) for s in g[1:])


def around(conn, sid: str) -> tuple[dict | None, dict | None, dict | None]:
    """(the active conversation right before this one in any recording, this one, the one right after), each with
    `at` and `end_at`; this one None when it isn't active."""
    order = _active(conn)
    i = next((k for k, s in enumerate(order) if s["id"] == sid), None)
    if i is None:
        return None, None, None
    return order[i - 1] if i else None, order[i], order[i + 1] if i + 1 < len(order) else None


def kind(conn, a: dict, b: dict) -> str | None:
    """Why `a` and the next conversation `b` are apart, for the lab's rule: hour | pause | recordings, or None when it
    isn't the lab's to group (a split there, now or undone; too far apart; overlapping recordings)."""
    gap = b["at"] - a["end_at"]
    if a["recording_id"] == b["recording_id"]:
        if row(conn, "SELECT 1 FROM splits WHERE recording_id=? AND at_s BETWEEN ? AND ?", a["recording_id"],
               a["end_s"] - NEAR_S, b["start_s"] + NEAR_S):
            return None
        return "hour" if gap < GAP_S else "pause" if gap <= AUTO_MAX_GAP_S else None
    return "recordings" if -NEAR_S <= gap <= AUTO_MAX_GAP_S else None


def _people(conn, sid: str) -> tuple[dict | None, dict[str, str], set[str]]:
    """(its lines and turns, speaker letter → who, the owner's letters). The lab's guesses count as who; an unnamed
    speaker keeps a name of their own, never shared."""
    from . import sessions as SV
    _, out, _ = SV.diarized(conn, sid)
    sp = SV.speakers(conn, sid)
    who = {s["letter"]: s["person_id"] if s["person_id"] and s["state"] in ("confirmed", "auto", "suggested")
           else f"{sid}:{s['letter']}" for s in sp}
    return out, who, {s["letter"] for s in sp if s["self"]}


def measure(conn, a: dict, b: dict) -> dict | None:
    """How much the same people carry on from `a` into the next conversation `b`, the owner left out: around the
    boundary (a's last 10 minutes, b's first 10) "ab" is the share of b's talk by people major in a, "ba" the reverse;
    "whole" the more shared of the same over both parts whole. "talk" is False when either side has under 30 s of
    others' talk around it. None until identify has run on both."""
    sides = []
    for s, near_end in ((a, True), (b, False)):
        out, who, me = _people(conn, s["id"])
        if not out or not row(conn, "SELECT 1 FROM stage_runs WHERE target=? AND stage='identify' AND is_current=1 "
                                    "AND status='done'", s["id"]):
            return None
        n = s["end_s"] - s["start_s"]

        def by_who(t0, t1):  # noqa: E306
            talk: dict[str, float] = {}
            for letter, secs in _talk(out["turns"], t0, t1, me).items():
                k = who.get(letter, f"{s['id']}:{letter}")
                talk[k] = talk.get(k, 0.0) + secs
            return talk
        sides.append((by_who(*((n - WINDOW_S, n) if near_end else (0.0, WINDOW_S))), by_who(0.0, n)))
    (x, wx), (y, wy) = sides
    c = lambda f, t: round(_carry(f, t), 2) if sum(f.values()) and sum(t.values()) else 0.0  # noqa: E731
    named = lambda d: {k: v for k, v in d.items() if ":" not in k}  # noqa: E731
    nx, ny = named(x), named(y)
    return {"ab": c(x, y), "ba": c(y, x), "whole": max(c(wx, wy), c(wy, wx)),
            "talk": sum(x.values()) >= MIN_TALK_S and sum(y.values()) >= MIN_TALK_S,
            # Among named people only (guesses included), for the hour cut's "clearly change": an unnamed speaker can't
            # be shown to be someone new.
            "named": max(c(nx, ny), c(ny, nx), c(named(wx), named(wy)), c(named(wy), named(wx))),
            "named_talk": sum(nx.values()) >= MIN_TALK_S and sum(ny.values()) >= MIN_TALK_S}


def one(m: dict, why: str) -> bool:
    """The lab's rule. An hour cut is one discussion unless the people clearly change: split.auto's bar, at most 5% of
    either side's talk by the other's people, around the cut and over both parts, among people named (guesses too) with
    enough talk to tell; nobody named on a side, it can't be told. (Not its "the meeting visibly ended"
    too: that picks which pause inside a conversation to cut at, and the hour cap already chose; on 30 Oct an hour cut
    sharing nobody, the others silent 52 s, would have been grouped.) A pause or other recordings: at least half of
    each side's talk by the other side's people, with enough talk to tell."""
    if why == "hour":
        return not (m["named_talk"] and m["named"] <= AUTO_MAX_OVERLAP)
    return m["talk"] and m["ab"] >= AUTO_MIN_CARRY and m["ba"] >= AUTO_MIN_CARRY


def decided(conn, a: dict, b: dict) -> bool:
    return bool(row(conn, "SELECT 1 FROM parts WHERE first_id=? AND second_id=?", a["id"], b["id"]))


def auto(conn, sid: str) -> list[str]:
    """Group by the lab, once identify has run on both, with the conversation before and after when the rule says one
    discussion. Never where the lab or the owner decided before. Returns the conversations it grouped this one with."""
    before, me, after = around(conn, sid)
    out = []
    for a, b in ((before, me), (me, after)):
        if a and b and not decided(conn, a, b) and (why := kind(conn, a, b)) and (m := measure(conn, a, b)) \
                and one(m, why):
            add(conn, a["id"], b["id"], by="lab", why=why)
            out.append((a if b is me else b)["id"])
    return out


def sweep(conn, apply: bool = False) -> list[dict]:
    """The lab's rule over every pair of neighbours at once, for conversations identified before parts existed (`auto`
    runs only as identify finishes). Each pair it would group: {"a", "b", "why", "m"}. Writes only with `apply`."""
    order = _active(conn)
    out = []
    for a, b in zip(order, order[1:]):
        if not decided(conn, a, b) and (why := kind(conn, a, b)) and (m := measure(conn, a, b)) and one(m, why):
            out.append({"a": a, "b": b, "why": why, "m": m})
            if apply:
                add(conn, a["id"], b["id"], by="lab", why=why)
    return out


def add(conn, first: str, second: str, by: str = "owner", why: str | None = None) -> None:
    conn.execute("INSERT INTO parts (first_id, second_id, by, kind, created_at) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT (first_id, second_id) DO UPDATE SET by=excluded.by, kind=excluded.kind, undone_at=NULL, "
                 "created_at=excluded.created_at", (first, second, by, why, time.time()))


def by_hand(a: dict, b: dict) -> bool:
    """`b` starts after `a` ends (overlapping recordings are two takes of one time, never parts), within half an hour."""
    return -NEAR_S <= b["at"] - a["end_at"] <= HAND_MAX_GAP_S


def group_with(conn, sid: str, side: str) -> str:
    """By hand: this conversation and the one right before or after it are one discussion. Returns the head's id.
    ValueError when there's none within half an hour."""
    before, me, after = around(conn, sid)
    a, b = (before, me) if side == "before" else (me, after)
    if not a or not b or not by_hand(a, b):
        raise ValueError("only the conversation right before or after, up to half an hour apart and not overlapping")
    add(conn, a["id"], b["id"], by="owner")
    return group(conn, sid)[0]["id"]


def ungroup(conn, first: str, second: str) -> None:
    """"Not one discussion" between these two: the link stays, undone, so the lab never groups them again.
    KeyError when they aren't linked."""
    if not conn.execute("UPDATE parts SET undone_at=? WHERE first_id=? AND second_id=? AND undone_at IS NULL",
                        (time.time(), first, second)).rowcount:
        raise KeyError(first)


def delete_for(conn, sid: str) -> None:
    """A conversation deleted: no link names it."""
    conn.execute("DELETE FROM parts WHERE first_id=? OR second_id=?", (sid, sid))


def drop_reading(conn, sid: str) -> None:
    """Its words gone (deleted, or a stretch of it): every review of a whole discussion that read it, current or not,
    goes too, with its answers, and the notes, answers, threads and plans that read that review (as for its own)."""
    from . import ask, prepare, talk
    from .coach import drop_for
    for r in rows(conn, "SELECT id, target FROM stage_runs WHERE stage='insight' AND target<>? AND params LIKE ?",
                  sid, f'%"{sid}"%'):
        for t in ("feedback", "review_answers", "ticktick_tasks"):
            conn.execute(f"DELETE FROM {t} WHERE insight_run=?", (r["id"],))
        conn.execute("DELETE FROM stage_runs WHERE id=?", (r["id"],))
        for drop in (drop_for, ask.drop_for, talk.drop_for, prepare.drop_for):
            drop(conn, [r["target"]])


def with_groups(conn, ids: list[str]) -> list[str]:
    """These conversations and every part of the discussions they're in: a review of the whole read them all."""
    out = list(ids)
    for g in groups(conn):
        if any(s["id"] in ids for s in g):
            out += [s["id"] for s in g if s["id"] not in out]
    return out


# ---- the review of the whole ---------------------------------------------------------------------------------


def letters(conn, g: list[dict]) -> tuple[list[dict], list[dict]]:
    """For the review: (its speakers [{speaker, name, self}], per part {id, offset_s, letters: its letter → the
    review's}). The head keeps its letters; a later part's speaker settled as someone already heard takes their letter,
    anyone else gets one of their own ("C2": speaker C of part 2). ([], []) until every part is settled (no suggestion
    waiting) and the owner is confirmed in at least one."""
    from .planning import settled_speakers
    speakers: list[dict] = []
    by_person: dict[str, str] = {}
    meta = []
    for n, s in enumerate(g, 1):
        got = settled_speakers(conn, s["id"], need_self=False, with_person=True)
        if got is None:
            return [], []
        mine = {}
        for sp in got:
            key = by_person.get(sp["person"]) if sp["person"] else None
            if key is None:
                key = sp["speaker"] if n == 1 else f"{sp['speaker']}{n}"
                name = sp["name"] if sp["person"] or n == 1 else f"Speaker {key}"
                speakers.append({"speaker": key, "name": name, "self": sp["self"]})
                if sp["person"]:
                    by_person[sp["person"]] = key
            mine[sp["speaker"]] = key
        meta.append({"id": s["id"], "offset_s": round(s["at"] - g[0]["at"], 2), "letters": mine})
    if not any(sp["self"] for sp in speakers):
        return [], []
    return speakers, meta


PART_LINES = 10_000  # a later part's line ids start at n × this: a part is at most an hour (~800 lines)


def stitch(outs: list[dict], meta: list[dict]) -> dict:
    """The parts' diarize outputs as one: times from the head's start, the review's letters, part n's line ids from
    (n - 1) × PART_LINES (the head's as they are), and on each later part's first line how long the gap before it was
    (`gap_before`), for the transcript to say."""
    out: dict = {"speakers": [], "talk_s": {}, "turns": [], "overlapping_turns": [], "lines": [], "parts": []}
    end = None
    for k, (dz, m) in enumerate(zip(outs, meta)):
        key = lambda s: m["letters"].get(s, s if k == 0 else f"{s}{k + 1}") if s else s  # noqa: E731
        off, base = m["offset_s"], k * PART_LINES
        for t in ("turns", "overlapping_turns"):
            out[t] += [{**x, "speaker": key(x["speaker"]), "start": round(x["start"] + off, 2),
                        "end": round(x["end"] + off, 2)} for x in dz.get(t) or []]
        for s, v in dz["talk_s"].items():
            out["talk_s"][key(s)] = round(out["talk_s"].get(key(s), 0.0) + v, 2)
        out["speakers"] += [key(s) for s in dz["speakers"] if key(s) not in out["speakers"]]
        assert all(ln["id"] < PART_LINES for ln in dz["lines"])
        lines = [{**ln, "id": base + ln["id"], "speaker": key(ln["speaker"]), "start": round(ln["start"] + off, 2),
                  "end": round(ln["end"] + off, 2)} for ln in dz["lines"]]
        if lines and end is not None:
            lines[0]["gap_before"] = round(max(0.0, lines[0]["start"] - end))
        out["parts"].append({"id": m["id"], "offset_s": off})
        out["lines"] += lines
        end = max((ln["end"] for ln in lines), default=end)
    return out


def gap_text(seconds: float) -> str:
    return f"{round(seconds)} s later" if seconds < 90 else f"{round(seconds / 60)} min later"


def whole_lines(conn, head: str) -> tuple[list[dict], dict[str, dict]] | None:
    """The whole discussion's lines as its review read them (times from the head's start) and who each of the review's
    letters is ({letter, name, state, self, person_id}), when the review shown on `head` is a whole one; else None."""
    from . import sessions as SV
    from .corrections import left_out, without
    from .db import load_json
    ins = SV.shown_review(conn, head)
    if not is_whole(ins):
        return None
    import json
    params = json.loads(ins["params"])
    outs = []
    for m in params["parts"]:
        sess = row(conn, "SELECT * FROM sessions WHERE id=?", m["id"])
        dz, out, fixes = SV.diarized(conn, m["id"])
        if not sess or not dz:
            return None
        outs.append(without(SV.trimmed(conn, sess, out), left_out(load_json(dz["output"]), fixes)))
    st = stitch(outs, params["parts"])
    people = {}
    for m in params["parts"]:
        for sp in SV.speakers(conn, m["id"]):
            key = m["letters"].get(sp["letter"])
            if key and key not in people:
                people[key] = {**sp, "letter": key}
    return st["lines"], people


def _page_meta(conn, g: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """For the page while no whole review is shown: each part's letters as the review would take them (the lab's
    guesses at who's who included), and who each is."""
    from . import sessions as SV
    meta, people, by_person = [], {}, {}
    for n, s in enumerate(g, 1):
        mine = {}
        for sp in SV.speakers(conn, s["id"]):
            key = by_person.get(sp["person_id"]) if sp["person_id"] else None
            if key is None:
                key = sp["letter"] if n == 1 else f"{sp['letter']}{n}"
                people[key] = {**sp, "letter": key}
                if sp["person_id"]:
                    by_person[sp["person_id"]] = key
            mine[sp["letter"]] = key
        meta.append({"id": s["id"], "offset_s": round(s["at"] - g[0]["at"], 2), "letters": mine})
    return meta, people


def page(conn, sess: dict) -> dict | None:
    """For the conversation page: the parts it's one of (each with its time, length, the gap before it, who grouped it
    and whether it was answered), whether the review shown reads the whole, and on the head's page the later parts'
    lines as the whole review numbers and times them (from the head's start; the page plays each from its own part's
    audio, at `start` less that part's offset), with who each letter is.
    Or, for a conversation alone, the neighbours it could be grouped with. None when there's neither."""
    import json

    from . import sessions as SV
    g = group(conn, sess["id"])
    if not g:
        if sess["status"] != "active":
            return None
        before, me, after = around(conn, sess["id"])
        offers = []
        for side, a, b in (("before", before, me), ("after", me, after)):
            other = a if side == "before" else b
            if other and by_hand(a, b):
                offers.append({"side": side, "other": other["id"], "gap_s": round(b["at"] - a["end_at"]),
                               "minutes": round((other["end_s"] - other["start_s"]) / 60, 1),
                               "who": [SV.display(sp) for sp in SV.speakers(conn, other["id"]) if not sp["self"]],
                               "ungrouped": decided(conn, a, b)})
        return {"offers": offers} if offers else None
    head = g[0]
    links = {(r["first_id"], r["second_id"]): r for r in rows(conn, "SELECT * FROM parts WHERE undone_at IS NULL")}
    listed = []
    for n, s in enumerate(g, 1):
        link = links.get((g[n - 2]["id"], s["id"])) if n > 1 else None
        listed.append({"id": s["id"], "n": n, "at": s["at"], "duration_s": round(s["end_s"] - s["start_s"], 1),
                       "gap_s": round(s["at"] - g[n - 2]["end_at"]) if n > 1 else None,
                       "by": link and link["by"], "kind": link and link["kind"], "first": g[n - 2]["id"] if n > 1 else None,
                       "answered": answered(conn, s["id"])})
    ins = SV.shown_review(conn, head["id"])
    out = {"head": head["id"], "list": listed, "whole": is_whole(ins), "held": held(conn, head["id"]),
           "lines": [], "people": {}}
    if sess["id"] != head["id"]:
        return out
    if out["whole"]:
        params = json.loads(ins["params"])
        meta, people = params["parts"], {}
        for m in meta:
            for sp in SV.speakers(conn, m["id"]):
                key = m["letters"].get(sp["letter"])
                if key and key not in people:
                    people[key] = {**sp, "letter": key}
    else:
        meta, people = _page_meta(conn, g)
    outs = []
    for m in meta:
        part = row(conn, "SELECT * FROM sessions WHERE id=?", m["id"])
        _, dz, _ = SV.diarized(conn, m["id"])
        dz = SV.trimmed(conn, part, dz) if dz else {"speakers": [], "talk_s": {}, "lines": []}
        outs.append({**dz, "lines": []} if SV.paused(SV.current(conn, m["id"], "transcribe"), conn) else dz)
    out["lines"] = [{**ln, "part": meta[ln["id"] // PART_LINES]["id"]} for ln in stitch(outs, meta)["lines"]
                    if ln["id"] >= PART_LINES]
    out["people"] = {k: {"name": SV.display(v), "self": v["self"]} for k, v in people.items()}
    return out
