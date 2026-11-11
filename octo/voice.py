"""Voiceprints: speaker embeddings, matching, and the people they belong to.

D1 (2026-09-30): a voiceprint is stored for every speaker the owner has labelled, so they're
auto-tagged later. Unlabelled voices are compared in memory and never stored. Tags are
suggestions until the owner confirms. "Forget" deletes a person's voiceprints and labels.

Measured 2026-09-30 (synthetic voices): same voice ~0.90 cosine similarity; a similar male voice
0.52–0.63 clean, and up to 0.77 when diarization mixed one of their lines into another speaker;
male vs female ~0.1.

Two different questions, deliberately with different bars:
- **Naming** someone (a suggestion the owner sees): ≥ 0.60 *and* ≥ 0.15 ahead of the runner-up. Was
  0.75, set on synthetic voices; real voiceprints on 2026-10-01 (recorder, phone, desk) put the same
  person at 0.50–0.77 across recordings and different people at ≤ 0.45, so 0.75 named almost
  nobody (two people at 0.69 and 0.72 went unnamed). Close calls are shown as "Asha or Ravi?", never silently as one of them.
- **Is the owner here?** (D10 discards sessions without them): generous, because wrongly discarding a
  real conversation is far worse than keeping one. Present at ≥ 0.6; absent only if *every*
  speaker is < 0.45; in between, kept and flagged. The first version used the naming rule for
  this and discarded a conversation the owner was in (their match 0.90 vs a similar voice 0.77).
"""

from __future__ import annotations

import hashlib
import json
import time

import numpy as np

from . import RATE
from .db import new_id, row, rows

# A voiceprint is only comparable with prints from the same model, so each one records its model and
# only the current model's prints are used. After a switch, prints rebuild from the owner's enrolment
# recording, conversations whose audio is still kept, and new confirmations (`octo reprint`).
#
# ResNet293 since 2 Oct, from `python -m bench.voicecompare` on the owner's confirmed speakers: ahead of
# ResNet34, CAM++ and TitaNet on every measure (EER 0.0% vs 0.8% on up to 60 s, 1.2% vs 2.5% on 10 s),
# by a small margin on four repeat people. Its raw scores sit on ResNet34's scale (different people
# at most 0.47 vs 0.49), so the bars below held: at 0.60 / 0.15 no wrong names at either length,
# 15 of 16 named vs 14. Diarization keeps ResNet34 (DIARIZE_EMBEDDING): pyannote 3.1 clusters with it.
EMBEDDING_MODEL = "Wespeaker/wespeaker-voxceleb-resnet293-LM"
DIARIZE_EMBEDDING = "pyannote/wespeaker-voxceleb-resnet34-LM"
# Models run as WeSpeaker's own ONNX export (onnxruntime) rather than through pyannote: repo -> file.
# Same features as pyannote's WeSpeaker models; ResNet34 this way matched pyannote's to cosine 1.0000.
ONNX_FILES = {"Wespeaker/wespeaker-voxceleb-resnet293-LM": "voxceleb_resnet293_LM.onnx"}
MATCH_MIN = 0.60
MATCH_MARGIN = 0.15
# Auto-accept (coaching plan C4, 5 Oct): the owner's own voice, when the match is this sure, is settled
# without asking -- state "auto", no voiceprint added until he confirms (K2). Measured 5 Oct on every
# suggestion made before its conversation had a print of its own: 10 of 10 for the owner right (lowest
# 0.61), 9 of 9 for others right; the bar sits well above the lowest right one (learnings).
AUTO_SELF_MIN = 0.70
AUTO_SELF_MARGIN = 0.25
# The same for other people (people plan 4.4, 13 Oct), once their voice is known from at least two
# conversations, so one mixed print can't settle a name. Measured: every suggestion the owner answered after it
# was made, 16 of 16 right (11 at this bar); on the spliced set after merging and splitting by voice,
# 66 clear matches, none wrong at any bar from 0.6 to 0.8 (learnings).
AUTO_OTHER_MIN = 0.70
AUTO_OTHER_MARGIN = 0.25
AUTO_OTHER_CONVERSATIONS = 2
JAY_PRESENT = 0.60
JAY_ABSENT = 0.45
MIN_TURN_S = 0.5  # shorter turns give unreliable embeddings
# A person has several voiceprints (phone, recorder across a room, loud, quiet); each voice is matched
# against all of them and the best wins. Prints from under MIN_PRINT_S of speech are too unreliable
# to match against (the owner's enrolment: 10 s of them scored ~0.78 against 30 s, 5 s 0.64-0.76), so they're
# not stored, and older ones are ignored. At most MAX_PRINTS per person: the most redundant goes.
MIN_PRINT_S = 8.0
MAX_PRINTS = 12
MAX_SPEECH_S = 60.0  # enough to characterise a voice


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def similarities(embedding: np.ndarray, prints: list[dict]) -> list[dict]:
    """Best similarity per person, highest first. prints: [{"person_id", "name", "embedding"}]."""
    best: dict[str, dict] = {}
    for p in prints:
        s = cosine(embedding, p["embedding"])
        if p["person_id"] not in best or s > best[p["person_id"]]["similarity"]:
            best[p["person_id"]] = {"person_id": p["person_id"], "name": p["name"], "similarity": round(s, 3),
                                    "is_self": bool(p.get("is_self"))}
    return sorted(best.values(), key=lambda b: -b["similarity"])


def match(embedding: np.ndarray, prints: list[dict]) -> dict | None:
    """Who this is, for naming: None if no one is close; "ambiguous" when two people are."""
    ranked = similarities(embedding, prints)
    if not ranked or ranked[0]["similarity"] < MATCH_MIN:
        return None
    runner_up = ranked[1]["similarity"] if len(ranked) > 1 else 0.0
    margin = round(ranked[0]["similarity"] - runner_up, 3)
    out = {**ranked[0], "margin": margin, "ambiguous": margin < MATCH_MARGIN}
    if out["ambiguous"]:
        out["alternatives"] = [r["name"] for r in ranked[1:] if r["similarity"] >= MATCH_MIN]
    return out



# One speaker who is really two or more people (people plan 4.2; 8 Oct 21:30: two colleagues heard as
# one, named as one, a blend saved as each). Each speaker's speech is cut into pieces and every piece
# matched to the voices the lab knows; when a second known person clearly holds a fair share, the
# speaker is split along them. Measured 9 Oct on spliced conversations (bench/truthset.py): people
# merged into someone else 10 of 62 → 0, named right without asking 46 → 62, at a 10% share.
SPLIT_PIECE_S = 3.0
SPLIT_SHARE = 0.10   # of the speaker's time, clearly someone else's, before splitting
SPLIT_MIN_S = 10.0   # and at least this much


def pieces(turns: list[tuple[float, float]], size: float = SPLIT_PIECE_S, least: float = 1.5) -> list[tuple[float, float]]:
    """Each turn cut into pieces of about `size` seconds; a turn shorter than `least` is one piece."""
    out = []
    for start, end in turns:
        a = start
        while a < end:
            b = end if end - a < size + least else a + size
            out.append((a, b))
            a = b
    return out


def split_by_voice(embedder: "Embedder", samples: np.ndarray, turns: list[tuple[float, float]],
                   prints: list[dict]) -> list[dict] | None:
    """The known people one speaker's turns are, if more than one: [{"person_id", "name", "spans"}],
    most talk first, spans covering every turn. A piece no one is sure of goes to whichever of them it
    sounds closest to; one too short to tell, to the person of the nearest sure piece. None if the
    speaker is one person (or no one the lab knows)."""
    ps = pieces(turns)
    tags: list[tuple[str | None, dict[str, float]]] = []
    for a, b in ps:
        if b - a < 1.0:
            tags.append((None, {}))
            continue
        ranked = similarities(embedder.embed(samples[int(a * RATE):int(b * RATE)]), prints)
        sure = ranked and ranked[0]["similarity"] >= MATCH_MIN and \
            ranked[0]["similarity"] - (ranked[1]["similarity"] if len(ranked) > 1 else 0.0) >= MATCH_MARGIN
        tags.append((ranked[0]["person_id"] if sure else None, {r["person_id"]: r["similarity"] for r in ranked}))
    total = sum(b - a for a, b in ps)
    held: dict[str, float] = {}
    for (a, b), (who, _) in zip(ps, tags):
        if who:
            held[who] = held.get(who, 0.0) + b - a
    people = sorted((p for p, v in held.items() if v >= SPLIT_SHARE * total and v >= SPLIT_MIN_S), key=lambda p: -held[p])
    if len(people) < 2:
        return None
    sure = [(a, who) for (a, _), (who, _) in zip(ps, tags) if who in people]
    spans: dict[str, list[list[float]]] = {p: [] for p in people}
    for (a, b), (who, sims) in zip(ps, tags):
        if who not in people:
            who = max(people, key=lambda p: sims.get(p, -1.0)) if sims else min(sure, key=lambda x: abs(x[0] - a))[1]
        if spans[who] and abs(spans[who][-1][1] - a) < 1e-6:
            spans[who][-1][1] = round(b, 2)
        else:
            spans[who].append([round(a, 2), round(b, 2)])
    names = {p["person_id"]: p["name"] for p in prints}
    return [{"person_id": p, "name": names.get(p), "spans": spans[p]} for p in people]


def jay_presence(self_similarities: list[float], self_enrolled: bool) -> bool | None:
    """D10: True = the owner is here, False = clearly not, None = can't tell (keep, to be safe)."""
    if not self_enrolled or not self_similarities:
        return None
    if max(self_similarities) >= JAY_PRESENT:
        return True
    if max(self_similarities) < JAY_ABSENT:
        return False
    return None


def wespeaker_fbank(samples: np.ndarray) -> np.ndarray:
    """WeSpeaker's input, as pyannote computes it (compute_fbank): 80 log-mel bins, 25/10 ms Hamming
    frames over int16-scaled samples, centred over the clip. Shape (1, frames, 80)."""
    import torch
    from torchaudio.compliance import kaldi

    x = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)).unsqueeze(0)
    f = kaldi.fbank(x * (1 << 15), num_mel_bins=80, frame_length=25, frame_shift=10, dither=0.0,
                    sample_frequency=RATE, window_type="hamming", use_energy=False)
    return (f - f.mean(0, keepdim=True)).numpy()[None]


class Embedder:
    """Loads an embedding model once per process (a worker, or an enrol/label command). Voiceprints
    use EMBEDDING_MODEL; diarize passes DIARIZE_EMBEDDING."""

    def __init__(self, model: str = EMBEDDING_MODEL) -> None:
        self.model = model
        if model in ONNX_FILES:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            # CoreML (Apple GPU/Neural Engine) with CPU for what it can't run: 2-10x faster than CPU
            # alone, embeddings 0.99999 cosine to it (2 Oct).
            self._file = hf_hub_download(model, ONNX_FILES[model])
            self._onnx = ort.InferenceSession(self._file, providers=["CoreMLExecutionProvider", "CPUExecutionProvider"])
            self._cpu = None
            self._input = self._onnx.get_inputs()[0].name
            return
        import torch
        from pyannote.audio import Inference, Model

        self._onnx = None
        self._torch = torch
        device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        self._inf = Inference(Model.from_pretrained(model), window="whole", device=device)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        from .audio import highpass

        samples = highpass(samples)
        if self._onnx is not None:
            feats = {self._input: wespeaker_fbank(samples)}
            try:
                out = self._onnx.run(None, feats)
            except Exception as e:
                # CoreML refuses some lengths ("Error in dynamically resizing for sequence length"): one
                # conversation's identify failed six times on 8-9 Oct. The CPU gives the same embedding.
                if "CoreML" not in str(e):
                    raise
                if self._cpu is None:
                    import onnxruntime as ort
                    self._cpu = ort.InferenceSession(self._file, providers=["CPUExecutionProvider"])
                out = self._cpu.run(None, feats)
            return out[0].reshape(-1).astype(np.float32)
        x = self._torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)).unsqueeze(0)
        return np.asarray(self._inf({"waveform": x, "sample_rate": RATE})).reshape(-1).astype(np.float32)

    def embed_turns(self, samples: np.ndarray, turns: list[tuple[float, float]]) -> tuple[np.ndarray, float] | None:
        """One embedding from a speaker's turns (seconds, relative to samples), or None if too little."""
        pieces, total = [], 0.0
        for s, e in sorted(turns, key=lambda t: t[1] - t[0], reverse=True):  # longest turns first
            if e - s < MIN_TURN_S or total >= MAX_SPEECH_S:
                continue
            # At most MAX_SPEECH_S in all, cutting the turn that crosses it (13 Oct, B3): one long turn used to go
            # in whole, up to 223 s, which CoreML refuses (over ~164 s) and the CPU took 22 s to embed.
            e = min(e, s + MAX_SPEECH_S - total)
            pieces.append(samples[int(s * RATE):int(e * RATE)])
            total += e - s
        if total < 1.0:
            return None
        return self.embed(np.concatenate(pieces)), round(total, 1)


# ---- people & voiceprints in the database -----------------------------------------------------

def person_by_name(conn, name: str) -> dict | None:
    return row(conn, "SELECT * FROM people WHERE lower(name)=lower(?)", name)


def ensure_person(conn, name: str, is_self: bool = False) -> dict:
    p = person_by_name(conn, name)
    if p:
        if is_self and not p["is_self"]:
            conn.execute("UPDATE people SET is_self=0")
            conn.execute("UPDATE people SET is_self=1 WHERE id=?", (p["id"],))
        return person_by_name(conn, name)
    if is_self:
        conn.execute("UPDATE people SET is_self=0")
    pid = new_id("per")
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES (?, ?, ?, ?)",
                 (pid, name, int(is_self), time.time()))
    return person_by_name(conn, name)


def needs_consent(conn) -> bool:
    """Whether this lab keeps someone's voiceprint only once their agreement is recorded (product plan 4.6)."""
    from . import labconfig
    return labconfig.flag(conn, "voiceprints_need_consent")


def may_keep_print(conn, person_id: str) -> bool:
    """The owner's own voice, always; anyone else's, in a lab that needs it, only with their agreement
    recorded. Without it their name is still given, per conversation, by hand."""
    p = row(conn, "SELECT is_self, consent FROM people WHERE id=?", person_id)
    return bool(p) and (bool(p["is_self"]) or not needs_consent(conn) or bool(p["consent"]))


def record_consent(conn, person_id: str, how: str, note: str = "") -> dict:
    c = {"at": time.time(), "how": how.strip() or "asked in person", "note": note.strip()}
    conn.execute("UPDATE people SET consent=? WHERE id=?", (json.dumps(c), person_id))
    return c


def withdraw_consent(conn, person_id: str) -> int:
    """Their agreement withdrawn: every voiceprint of theirs goes, overwritten in the database file (secure_delete,
    octo/db.py); the daily backups let go of them within 14 days. Their name stays on the conversations."""
    conn.execute("UPDATE people SET consent=NULL WHERE id=?", (person_id,))
    return conn.execute("DELETE FROM voiceprints WHERE person_id=?", (person_id,)).rowcount


def add_voiceprint(conn, person_id: str, embedding: np.ndarray, seconds: float, source: str) -> None:
    conn.execute("INSERT INTO voiceprints (id, person_id, embedding, seconds, source, created_at, model) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (new_id("vp"), person_id, embedding.astype(np.float32).tobytes(), seconds, source, time.time(), EMBEDDING_MODEL))


# A print far from its own person's others is a blend (a label on a speaker who was two people) and teaches the wrong
# voice: left out of naming (pipeline review 11 Nov, Jay; open question F2). bench.printaudit's FAR_FROM_OWN; on the
# lab's 191 prints the same person's mean ran 0.74 (median), the two blends 0.03 and 0.10. Only with >= 2 others, so a
# person's second print is never judged by their first alone.
FAR_FROM_OWN = 0.30
FAR_MIN_OTHERS = 2


def _usable(prints: list[dict]) -> list[dict]:
    """`prints` (each with an `embedding`) less those far from their own person's other prints."""
    by: dict[str, list[dict]] = {}
    for p in prints:
        by.setdefault(p["person_id"], []).append(p)

    def far(p: dict) -> bool:
        others = [q for q in by[p["person_id"]] if q is not p]
        return len(others) >= FAR_MIN_OTHERS and float(np.mean([cosine(p["embedding"], q["embedding"]) for q in others])) < FAR_FROM_OWN
    return [p for p in prints if not far(p)]


def _current_prints(conn) -> list[dict]:
    return [{**r, "is_self": bool(r["is_self"]), "embedding": np.frombuffer(r["embedding"], dtype=np.float32)}
            for r in rows(conn, "SELECT v.id, v.person_id, v.embedding, p.name, p.is_self FROM voiceprints v "
                                "JOIN people p ON p.id = v.person_id WHERE v.model=? AND v.seconds >= ? ORDER BY v.id",
                                EMBEDDING_MODEL, MIN_PRINT_S)]


def all_voiceprints(conn) -> list[dict]:
    return [{k: p[k] for k in ("person_id", "name", "is_self", "embedding")} for p in _usable(_current_prints(conn))]


def voiceprints_version(conn) -> str:
    """Changes whenever voiceprints are added or removed, or the embedding model changes, so identify re-runs."""
    ids = [p["id"] for p in _usable(_current_prints(conn))]
    # hashlib, not hash(): Python's hash() is randomised per process, which would re-run identify every time.
    return hashlib.sha256(",".join([EMBEDDING_MODEL, *ids]).encode()).hexdigest()[:12] if ids else "none"


def prune(conn, person_id: str, keep: int = MAX_PRINTS) -> int:
    """Keep a person's voiceprints varied and bounded: while there are too many, drop the one most
    similar to another of theirs (it adds least). Enrolment recordings are never dropped."""
    dropped = 0
    while True:
        ps = [{**r, "e": np.frombuffer(r["embedding"], dtype=np.float32)} for r in rows(
            conn, "SELECT id, embedding, source FROM voiceprints WHERE person_id=? AND model=? AND seconds >= ?",
            person_id, EMBEDDING_MODEL, MIN_PRINT_S)]
        if len(ps) <= keep:
            return dropped
        candidates = [p for p in ps if not p["source"].startswith("enrol:")]
        if not candidates:
            return dropped
        worst = max(candidates, key=lambda p: max(cosine(p["e"], q["e"]) for q in ps if q["id"] != p["id"]))
        conn.execute("DELETE FROM voiceprints WHERE id=?", (worst["id"],))
        dropped += 1


def drop_label(conn, session_id: str, speaker: str) -> int:
    """Delete the voiceprint saved when this speaker was labelled. A wrong label (a mother
    tagged as her daughter) would otherwise keep teaching the wrong voice after the owner corrects it."""
    return conn.execute("DELETE FROM voiceprints WHERE source=?", (f"label:{session_id}:{speaker}",)).rowcount


def rename(conn, name: str, to: str) -> tuple[str, dict]:
    """"Asha's Mom" → her name, everywhere. If `to` is already someone else, they're the same
    person: their voiceprints and labels are combined under `to` (under the owner, if one of them is them).
    Returns ("renamed" | "merged", the person who's left)."""
    p, other = person_by_name(conn, name), person_by_name(conn, to)
    if not p:
        raise KeyError(name)
    if not other or other["id"] == p["id"]:
        conn.execute("UPDATE people SET name=? WHERE id=?", (to, p["id"]))  # also fixes capitalisation
        return "renamed", {**p, "name": to}
    if p["is_self"] and not other["is_self"]:
        p, other = other, p  # keep the "you" person
    if p["consent"] and not other["consent"]:  # their agreement goes with their voiceprints (review 8 Oct, 14)
        conn.execute("UPDATE people SET consent=? WHERE id=?", (p["consent"], other["id"]))
        other = {**other, "consent": p["consent"]}
    conn.execute("UPDATE voiceprints SET person_id=? WHERE person_id=?", (other["id"], p["id"]))
    conn.execute("UPDATE session_speakers SET person_id=? WHERE person_id=?", (other["id"], p["id"]))
    for side in ("by_person", "to_person"):  # B14: the same person's promises
        conn.execute(f"UPDATE commitments SET {side}=? WHERE {side}=?", (other["id"], p["id"]))
    from .intention import merged
    merged(conn, p["id"], other["id"])  # B15
    conn.execute("UPDATE rehearsals SET person_id=? WHERE person_id=?", (other["id"], p["id"]))  # B26
    conn.execute("DELETE FROM preparations WHERE person_id=?", (p["id"],))  # B36: picked by the old name; prepare again
    conn.execute("UPDATE invitee_matches SET person_id=? WHERE person_id=?", (other["id"], p["id"]))  # B23
    conn.execute("DELETE FROM people WHERE id=?", (p["id"],))
    return "merged", other


def reprint(conn, root, embedder=None) -> dict[str, list[str]]:
    """After a change of EMBEDDING_MODEL: rebuild each older voiceprint with the current model from
    the audio it came from, where that audio is still kept (enrolment recordings in audio/; labelled
    speakers in conversations inside the audio retention). The old prints stay, unused, in case of a
    switch back. Returns {"rebuilt": [...sources], "missing": [...sources]}."""
    import json

    from . import audio as A

    have = {r["source"] for r in rows(conn, "SELECT source FROM voiceprints WHERE model=?", EMBEDDING_MODEL)}
    old = rows(conn, "SELECT * FROM voiceprints WHERE model IS NOT ? ORDER BY created_at", EMBEDDING_MODEL)
    out: dict[str, list[str]] = {"rebuilt": [], "missing": []}
    emb = embedder
    for vp in old:
        src = vp["source"]
        if src in have:
            continue
        got = None
        kind, _, rest = src.partition(":")
        if kind == "enrol":
            path = next((p for p in (root / "audio").rglob(rest) if p.is_file()), None)
            if path:
                speech = speech_only(A.read(path))
                if len(speech) >= RATE:
                    emb = emb or Embedder()
                    got = (emb.embed(speech), round(len(speech) / RATE, 1))
        elif kind == "label":
            sid, _, spk = rest.rpartition(":")
            sess = row(conn, "SELECT s.*, r.path, r.audio_deleted_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                             "WHERE s.id=?", sid)
            dz = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", sid)
            if sess and dz and not sess["audio_deleted_at"] and (root / sess["path"]).exists():
                from .calls import channel
                out_dz = json.loads(dz["output"])
                turns = [(t["start"], t["end"]) for t in out_dz["turns"] if t["speaker"] == spk]
                emb = emb or Embedder()
                got = emb.embed_turns(A.read(root / sess["path"], sess["start_s"], sess["end_s"], channel(out_dz, spk)),
                                      turns) if turns else None
        if got:
            if may_keep_print(conn, vp["person_id"]):  # (4.6) never rebuilt for someone who withdrew
                add_voiceprint(conn, vp["person_id"], got[0], got[1], src)
            out["rebuilt"].append(src)
        else:
            out["missing"].append(src)
    return out


def forget(conn, name: str) -> bool:
    p = person_by_name(conn, name)
    if not p:
        return False
    # B29: a lab that keeps its recordings keeps their voice too, so Forget silences their lines. First, while they
    # can still be found (audit 28 Oct): if it fails, nothing below has run and Forget can be asked again.
    from .labconfig import flag
    from .privacy import person_spans, silence_person
    silence_person(conn, person_spans(conn, p["id"]) if flag(conn, "keep_recordings") else {})
    from . import ask, prepare, talk
    from .coach import drop_for
    from .db import tx
    with tx(conn):  # all of it or none: a half-forgotten person can't be found to finish the job
        from .parts import with_groups  # B35: a review of a whole discussion read every part of it
        theirs = with_groups(conn, [r["session_id"] for r in conn.execute(
            "SELECT DISTINCT session_id FROM session_speakers WHERE person_id=?", (p["id"],))])
        drop_for(conn, theirs)  # the coach's notes that read a conversation they were in name or quote them (6 Oct)
        ask.drop_for(conn, theirs)  # B24: answers that read them quote them
        talk.drop_for(conn, theirs)  # B31: the threads on those conversations name and quote them
        prepare.drop_for(conn, theirs)  # B36: plans for others that read a conversation they were in
        conn.execute("DELETE FROM preparations WHERE person_id=?", (p["id"],))  # B36: theirs, mentions of them included
        prepare.forget_name(conn, p["name"])  # B36: others' plans that name them, from conversations they weren't in
        from .privacy import forget_words
        forget_words(conn, p["id"])  # L9: their lines read "(forgotten)"; the reviews that read them wait for a redo
        conn.execute("DELETE FROM voiceprints WHERE person_id=?", (p["id"],))
        conn.execute("DELETE FROM commitments WHERE by_person=? OR to_person=?", (p["id"], p["id"]))  # B14: both ways
        conn.execute("DELETE FROM intentions WHERE person_id=?", (p["id"],))  # B15: open and taken
        conn.execute("DELETE FROM rehearsals WHERE person_id=?", (p["id"],))  # B26: the model's version of them, and the owner's lines to it
        conn.execute("DELETE FROM invitee_matches WHERE person_id=?", (p["id"],))  # B23: which invitee they are
        conn.execute("UPDATE session_speakers SET person_id=NULL, state='unknown', similarity=NULL WHERE person_id=?", (p["id"],))
        conn.execute("DELETE FROM people WHERE id=?", (p["id"],))
    from .fillers import forget as drop_kept
    drop_kept()  # what the read-time pages keep in memory names them
    return True


def speech_only(samples: np.ndarray) -> np.ndarray:
    """Keep just the speech (for enrolment from a file that may have pauses or a lead-in)."""
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    from .audio import highpass

    filtered = highpass(samples)  # the mic's slow drift would otherwise set the gain
    rms = float(np.sqrt(np.mean(filtered ** 2))) + 1e-9
    norm = np.clip(filtered * min(100.0, 0.1 / rms), -1, 1).astype(np.float32)
    ts = get_speech_timestamps(torch.from_numpy(norm), load_silero_vad(), sampling_rate=RATE, threshold=0.1)
    return np.concatenate([samples[t["start"]:t["end"]] for t in ts]) if ts else samples[:0]
