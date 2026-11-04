"""The pipeline page: every step, the model it uses and why, and the owner's choice where there is one.

What can be chosen is defined here, in code, and nowhere else: each choice is a set of stage
parameters that was tested on the owner's own audio, with the evidence. A choice is stored per step in
`settings` and merged into the stage's parameters when the planner queues work, so a switch is just
a new input hash: the step re-runs for every conversation and the steps after it follow. Each
step's first choice is the stage's own parameters, so with nothing chosen nothing changes.
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import providers as PV
from .db import row, rows
from .stages import STAGES, downstream, params_for


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    params: dict
    why: str
    hf: tuple[str, ...] = ()        # Hugging Face repos that must be in the local cache (the lab runs offline)
    ollama: str | None = None       # an Ollama model that must be installed
    claude: bool = False            # runs through Claude Code: the `claude` command must be installed
    provider: str | None = None     # runs on one of the owner's connections (octo/providers.py, 7 Oct)


@dataclass(frozen=True)
class Step:
    stage: str
    name: str
    does: str
    model: str                      # what runs, for steps without choices (and the fixed part of those with)
    why: str
    choices: tuple[Choice, ...] = ()
    fixed: str | None = None        # why it can't be switched here
    tried: tuple[str, ...] = field(default_factory=tuple)


# The two ways to Claude (31 Oct), told apart wherever they're shown.
CLAUDE_CODE_LABEL = "Claude Opus 5.5 · Claude Code on this Mac: your Claude Pro or Max plan"
CLAUDE_CODE_WHY = ("Runs claude -p, logged in with your Claude account: the plan's limits, no bill per use, shared with "
                   "your own Claude use, at most 60 calls a day. The plan's terms are yours. The transcript goes to Anthropic.")

QWEN_ASR = ("Qwen/Qwen3-ASR-1.7B", "Qwen/Qwen3-ForcedAligner-0.6B")
WHISPER = ("mlx-community/whisper-large-v3-mlx",)
PYANNOTE_31 = ("pyannote/speaker-diarization-3.1", "pyannote/segmentation-3.0", "pyannote/wespeaker-voxceleb-resnet34-LM")

STEPS: tuple[Step, ...] = (
    Step("find_conversations", "Find conversations",
         "Finds speech in each recording; a conversation ends after 3 minutes of silence.",
         "Silero VAD",
         "Sensitive on purpose (threshold 0.1 on louder audio): over a fan it found 6.1 of about 7 s of "
         "speech with no false detections (30 Sep). Missing a conversation is worse than a little noise.",
         fixed="Nothing else has been tested."),
    Step("transcribe", "Transcribe",
         "Decides the language, then writes down the words with their timings.",
         "Language: Whisper large-v3, among English, Hindi, Kannada and Gujarati",
         "English: the choice below. Hindi and mixed (4 Oct): Whisper and Qwen each write a Hindi draft and "
         "the review's model merges the two from their text, never the audio, once you're confirmed in the "
         "conversation. Tested with Claude Opus 5.5 on real conversations: preferred 6 of 9 times in a blind test, "
         "with no words that weren't said; the pipeline's per-segment form isn't blind-tested yet. Kannada and "
         "Gujarati get no transcript yet.",
         choices=(
             Choice("qwen3-asr-1.7b", "Qwen3-ASR 1.7B", {"model": "Qwen/Qwen3-ASR-1.7B"},
                    "Won a blind test on real meetings 7–3 (2 Oct). Where it won, Whisper had dropped "
                    "about a tenth of the words. No word confidence: ⚠ marks only unsure speakers.",
                    hf=QWEN_ASR + WHISPER),
             Choice("whisper-large-v3", "Whisper large-v3", {"model": "mlx-community/whisper-large-v3-mlx"},
                    "Used until 2 Oct. Lost 3–7 to Qwen on English. Gives word confidence, so ⚠ also marks "
                    "unsure words.",
                    hf=WHISPER),
         ),
         tried=("On a real Hindi conversation, Whisper, Qwen3-ASR and Whisper Hinglish (Trelis): none usable on 9 of 9 stretches (2 Oct)",
                "Same 9 stretches, 4 Oct: Opus merge 6, Qwen told Hindi 3, Whisper told Hindi 0",
                "Whisper small: about 35% of words wrong at the bench (30 Sep)",
                "Cloud services (Zoom, Azure, ElevenLabs, AssemblyAI…): ruled out, the audio would leave the Mac")),
    Step("speakers", "Separate speakers",
         "Works out who spoke when, as Speaker A, B, C…, from the audio alone.",
         "",
         "Scored on 7 real conversations with confirmed speakers (2 Oct).",
         choices=(
             Choice("pyannote-3.1", "pyannote 3.1",
                    {"model": "pyannote/speaker-diarization-3.1", "clustering_threshold": 0.705},
                    "In testing, never put two people confirmed as different on one speaker. It splits instead, and a "
                    "split is one Merge click to fix.",
                    hf=PYANNOTE_31),
             Choice("pyannote-community-1", "pyannote community-1",
                    {"model": "pyannote/speaker-diarization-community-1", "clustering_threshold": None},
                    "Merged two people into one speaker in a family conversation, and lost one person in a short clip (2 Oct). "
                    "Otherwise agreed with 3.1 on 99% of confirmed speech.",
                    hf=("pyannote/speaker-diarization-community-1",)),
         ),
         tried=("Nemotron 3 Diarization (NVIDIA): merged two colleagues, at most 8 speakers, needs its own environment (2 Oct)",
                "pyannoteAI Precision-2 and -3: cloud only, ruled out")),
    Step("diarize", "Who said what",
         "Gives each word to the speaker heard at that moment, then joins words into lines. Words no "
         "speaker covers go to the one whose voice they clearly match.",
         "No model of its own; ResNet34 voice match for the gaps",
         "pyannote left whole stretches of clear speech with no one: 37 lines of up to 13 s in one 32-min "
         "conversation. Matching their voice agreed with pyannote on all ~550 held-out lines it took (2 Oct).",
         fixed="Nothing to choose: it follows the two steps before it."),
    Step("identify", "Recognise voices",
         "Matches each speaker against the voiceprints of people you've named.",
         "WeSpeaker ResNet293 voiceprints",
         "Chosen on 2 Oct after comparing four voice models on confirmed speakers: ResNet293 separated people "
         "best, by a small margin. Names a speaker at similarity 0.60 or more with a clear margin; in testing "
         "that named 15 of 16 with no wrong names.",
         fixed="Switching rebuilds every voiceprint from your labels (`octo reprint` in a terminal).",
         tried=("ResNet34 (WeSpeaker): used until 2 Oct; close behind (EER 0.8% vs 0.0% on 60 s of speech)",
                "CAM++ (3D-Speaker): close behind ResNet34; TitaNet-Large (NVIDIA): last",
                "Score normalisation against the other people you've labelled: worse on every model, too few people")),
    Step("numbers", "Count",
         "Talk time, turns, questions and interruptions per speaker.",
         "No model",
         "Plain counting, no judgement. Questions come from the transcript's punctuation.",
         fixed="Nothing to choose."),
    Step("insight", "Review the conversation",
         "Reads the whole conversation: how the others acted, what went unsaid, your conduct against your "
         "principles, and what to do next. Every quote is checked against the transcript.",
         "",
         "Transcript text may leave the Mac, nothing before it: the model you choose gets the transcript text, "
         "names and numbers, never the audio or voiceprints. Transcribe also sends Hindi drafts' text to the same "
         "model to merge, and the coach your reviews.",
         choices=(
             Choice("claude-opus-5-5", CLAUDE_CODE_LABEL, {"model": "claude-opus-5-5", "effort": "high"},
                    "Wrote the 20:31 analysis chosen as the bar (2 Oct). One review per conversation. " + CLAUDE_CODE_WHY,
                    claude=True),
             Choice("qwen3-8b", "qwen3:8b (local)", {"model": "qwen3:8b"},
                    "In use 1–2 Oct. Stays on the Mac, one principle at a time. Timed out on every conversation "
                    "over about 25 minutes (2 Oct).",
                    ollama="qwen3:8b"),
             Choice("gemma3-12b", "gemma3:12b (local)", {"model": "gemma3:12b"},
                    "Not compared on real conversations. Bigger (8 GB): slower than qwen3:8b, and tight "
                    "next to the other models in 16 GB.",
                    ollama="gemma3:12b"),
         ),
         tried=("Local models couldn't do the whole-conversation review in testing: at 16 GB the largest is about "
                "12–14B, and they missed what a reference review found or timed out (2 Oct)",)),
    Step("commitments", "Commitments",
         "After the review: what you promised and what others promised you, each with its line, to whom, and a date if "
         "one was said; and which earlier open ones came up. Every quote is checked against the transcript.",
         "",
         "A step of its own (B14, 19 Oct), so the review stays as it was. One call per conversation, from conversations "
         "of the 14 days before it came and every one since. The model you choose gets the same transcript text as the "
         "review. Not yet checked by eye: the first ten go in learnings.",
         choices=(
             Choice("claude-opus-5-5", CLAUDE_CODE_LABEL, {"model": "claude-opus-5-5", "effort": "high"},
                    "The review's model. " + CLAUDE_CODE_WHY, claude=True),
         )),
)
BY_STAGE = {s.stage: s for s in STEPS}
LATER = {name: downstream(name) for name in STAGES}


# ---- choosing --------------------------------------------------------------------------------

def provider_choices() -> tuple[Choice, ...]:
    """The review on the owner's own connections (octo/providers.py): each Claude model on an
    Anthropic key, and each model the owner named on an OpenAI-compatible service. The params carry
    which connection and model, never the key."""
    out = []
    for c in PV.load()["connections"]:
        if c["kind"] == "anthropic":
            for model, spec in PV.CLAUDE_MODELS.items():
                reader = {"kind": "anthropic", "connection": c["id"], "model": model, "effort": "high"}
                out.append(Choice(f"{c['id']}:{model}", f"{spec['label']} · Anthropic API key ({c['label']}): billed per use",
                                  {"model": model, "effort": "high", "reader": reader},
                                  spec["why"] + " On your API key, paid per use: the transcript goes to Anthropic.",
                                  provider=c["id"]))
        else:
            host = urllib.parse.urlparse(c["base_url"]).hostname
            for model in c["models"]:
                reader = {"kind": "openai", "connection": c["id"], "model": model, "base_url": c["base_url"]}
                out.append(Choice(f"{c['id']}:{model}", f"{model} · {c['label']}", {"model": model, "reader": reader},
                                  f"Not tested with Octo yet. The transcript goes to {c['label']} ({host}).",
                                  provider=c["id"]))
    return tuple(out)


def choices(conn, step: Step) -> tuple[Choice, ...]:
    """What can be chosen for a step here. The review and commitments add the owner's connections; Claude Code is
    offered in every lab (P5, 31 Oct)."""
    if step.stage not in ("insight", "commitments"):
        return step.choices
    return step.choices + provider_choices()


def default(step: Step) -> Choice:
    """The choice that is the stage's own parameters (what runs when the owner hasn't chosen)."""
    own = STAGES[step.stage].params
    return next(c for c in step.choices if all(own.get(k) == v for k, v in c.params.items()))


def selected(conn, step: Step) -> Choice | None:
    """The step's choice; for the review, None in a new lab until its owner picks a model."""
    if not step.choices:
        return None
    here = choices(conn, step)
    r = row(conn, "SELECT choice FROM settings WHERE stage=?", step.stage)
    got = next((c for c in here if r and c.id == r["choice"]), None)
    if got:
        return got
    if step.stage == "commitments" and not r:  # B14: unchosen, the review's own model where it has one here
        review = selected(conn, BY_STAGE["insight"])
        if review and review.id in {c.id for c in here}:
            return next(c for c in here if c.id == review.id)
    fallback = default(step)
    if fallback.claude and not PV.claude_code_default(conn):  # a new lab: its owner picks Claude Code, nothing else does
        return None
    return fallback if fallback in here else None


def overrides(conn, stage: str) -> dict:
    """The stage parameters the owner's choice sets (empty when the step has no choices). A review on one of
    the owner's connections also carries the owner's profile, so editing it makes the review again."""
    step = BY_STAGE.get(stage)
    choice = selected(conn, step) if step else None
    if not choice:
        return {}
    params = dict(choice.params)
    if choice.provider or (choice.claude and not PV.claude_code_default(conn)):  # the first lab's Claude Code: as before
        params["owner"] = owner(conn)
    return params


def owner(conn) -> dict:
    """The owner's profile for the review's prompt (octo/owner.py)."""
    from .owner import load
    return load(conn)


def reader(conn) -> dict | None:
    """What reads text for the Hindi merge and the coach. The review's own choice when it's one of the
    owner's connections or Claude Code; otherwise Claude Code in the first lab (as before 7 Oct); otherwise nothing yet."""
    choice = selected(conn, BY_STAGE["insight"])
    if choice and choice.params.get("reader"):
        return dict(choice.params["reader"])
    if choice and choice.claude:
        return {"kind": "claude-code", "model": choice.params["model"], "effort": choice.params.get("effort", "high")}
    if PV.claude_code_default(conn):
        return {"kind": "claude-code", "model": "claude-opus-5-5", "effort": "high"}
    return None


def privacy(conn) -> dict:
    """What Conversations' privacy line says leaves the Mac (22 Oct): the review's model as chosen here (None
    until one is), whether it runs on the Mac, and what merges Hindi drafts (None where nothing does)."""
    def name(c: Choice) -> str:
        return "Claude Opus 5.5 through Claude Code" if c.claude else c.label
    review = selected(conn, BY_STAGE["insight"])
    who = reader(conn)
    hindi = None if who is None else name(review) if review and review.params.get("reader") else \
        "Claude Opus 5.5 through Claude Code" if who["kind"] == "claude-code" else who["model"]
    return {"review": name(review) if review else None, "local": bool(review and review.ollama), "hindi": hindi}


def review_blocked(conn) -> str | None:
    """Why reviews can't start in this lab yet, or None."""
    if selected(conn, BY_STAGE["insight"]) is None:
        return "No model chosen for reviews yet"
    return None


def choose(conn, stage: str, choice_id: str) -> Choice:
    step = BY_STAGE.get(stage)
    if not step or not step.choices:
        raise KeyError(stage)
    choice = next((c for c in choices(conn, step) if c.id == choice_id), None)
    if not choice:
        raise ValueError(f"no choice {choice_id!r} for {stage}")
    missing = unavailable(choice)
    if missing:
        raise ValueError(missing)
    conn.execute("INSERT INTO settings (stage, choice, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT (stage) DO UPDATE SET choice=excluded.choice, updated_at=excluded.updated_at",
                 (stage, choice.id, time.time()))
    return choice


# ---- availability ----------------------------------------------------------------------------

def _hf_cached(repo: str) -> bool:
    from huggingface_hub.constants import HF_HUB_CACHE
    snaps = Path(HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "snapshots"
    return snaps.is_dir() and any(snaps.iterdir())


def _ollama_models() -> set[str] | None:
    from .insight import OLLAMA
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=1.5) as r:
            return {m["name"] for m in json.load(r).get("models", [])}
    except Exception:  # Ollama not running
        return None


def unavailable(choice: Choice, ollama: Callable[[], set[str] | None] | None = None) -> str | None:
    """Why this choice can't run on this Mac right now, or None."""
    ollama = ollama or _ollama_models
    if choice.provider:
        c = PV.connection(choice.provider)
        if not c:
            return "that service isn't connected any more"
        if c["kind"] == "anthropic" and not c.get("api_key"):
            return "no API key"
        return None
    missing = [r for r in choice.hf if not _hf_cached(r)]
    if missing:
        return "not downloaded: " + ", ".join(missing)
    if choice.claude:
        from .claude_code import INSTALL, claude_bin
        if not claude_bin():  # its version and login: Settings → Models and keys, and `octo doctor`
            return f"Claude Code isn't installed on this Mac: {INSTALL}, then claude auth login"
    if choice.ollama:
        have = ollama()
        if have is None:
            return "Ollama isn't running"
        if choice.ollama not in have:
            return f"not installed in Ollama (ollama pull {choice.ollama})"
    return None


# ---- what the page shows ---------------------------------------------------------------------

def _targets(conn, stage: str) -> list[dict]:
    if STAGES[stage].level == "recording":
        return rows(conn, "SELECT id, duration_s AS audio_s FROM recordings WHERE audio_deleted_at IS NULL")
    return rows(conn, "SELECT id, end_s - start_s AS audio_s FROM sessions WHERE status='active'")


def counts(conn, stage: str) -> dict:
    from .sessions import paused
    out = {"done": 0, "queued": 0, "running": 0, "failed": 0, "paused": 0}
    for t in _targets(conn, stage):
        live = row(conn, "SELECT status FROM stage_runs WHERE target=? AND stage=? AND status IN ('queued','running') "
                         "LIMIT 1", t["id"], stage)
        if live:
            out[live["status"]] += 1
            continue
        last = row(conn, "SELECT status FROM stage_runs WHERE target=? AND stage=? ORDER BY queued_at DESC LIMIT 1",
                   t["id"], stage)
        cur = row(conn, "SELECT id, status, coalesce(output, '') != '' AS output, stale FROM stage_runs "
                  "WHERE target=? AND stage=? AND is_current=1",
                  t["id"], stage)
        if last and last["status"] == "failed":
            out["failed"] += 1
        elif cur and not cur["stale"]:
            if stage == "transcribe" and paused(cur, conn):
                out["paused"] += 1
            else:
                out["done"] += 1
    return out


def _seconds_per_audio_second(conn, stage: str) -> float | None:
    """Median work time per second of audio over the stage's recent runs."""
    level = STAGES[stage].level
    audio = "s.end_s - s.start_s" if level == "session" else "r.duration_s"
    join = "JOIN sessions s ON s.id = sr.target" if level == "session" else "JOIN recordings r ON r.id = sr.target"
    got = [x["t"] / x["a"] for x in rows(
        conn, f"SELECT sr.finished_at - sr.started_at AS t, {audio} AS a FROM stage_runs sr {join} "
              f"WHERE sr.stage=? AND sr.status='done' AND sr.started_at IS NOT NULL ORDER BY sr.finished_at DESC LIMIT 20",
        stage) if x["a"] and x["t"] and x["a"] > 1]
    return statistics.median(got) if got else None


def impact(conn, stage: str) -> dict:
    """What a switch at this step redoes: conversations, later steps, and a rough time."""
    targets = [t for t in _targets(conn, stage)
               if row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage=? AND (is_current=1 OR status IN "
                            "('queued','running'))", t["id"], stage)]
    audio = sum(t["audio_s"] or 0 for t in targets)
    seconds, known = 0.0, True
    for name in [stage, *LATER[stage]]:
        rate = _seconds_per_audio_second(conn, name)
        if rate is None:
            known = False
            continue
        seconds += rate * audio
    return {"conversations": len(targets), "audio_s": round(audio), "later": [BY_STAGE[n].name for n in LATER[stage]],
            "estimate_s": round(seconds) if seconds else None, "estimate_partial": not known,
            "keeps": "Names you confirmed, speaker merges and line moves carry over by timing (a line move only "
                     "while that stretch is still one line)."
                     if stage in ("transcribe", "speakers", "diarize") else "Your ratings stay with the insights they were given on."}


def describe(conn) -> list[dict]:
    have = _ollama_models()  # one call for the page
    out = []
    for i, step in enumerate(STEPS, start=1):
        sel = selected(conn, step)
        here = choices(conn, step)
        stage = STAGES[step.stage]
        out.append({
            "stage": step.stage, "n": i, "name": step.name, "does": step.does, "why": step.why,
            "model": step.model, "fixed": step.fixed, "tried": list(step.tried), "version": stage.version,
            "params": params_for(stage, overrides(conn, step.stage)),
            "selected": sel.id if sel else None,
            "choices": [{"id": c.id, "label": c.label, "why": c.why, "default": c is default(step),
                         "provider": c.provider, "unavailable": unavailable(c, lambda: have)} for c in here],
            "blocked": review_blocked(conn) if step.stage == "insight" else
                       "No model chosen for commitments yet" if step.stage == "commitments" and sel is None else None,
            "counts": counts(conn, step.stage),
        })
    return out
