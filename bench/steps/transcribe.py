"""Step: transcribe the recording locally with faster-whisper, forced English vs auto-detect."""

from __future__ import annotations


from .. import AUDIO, audio
from ..runner import Metric, Outcome, Step, StepContext

DEFAULT_MODEL = "large-v3"
LOW_CONFIDENCE = -0.8  # mean avg_logprob below this is usually wrong


def evaluate(en: dict, auto: dict, model: str) -> Outcome:
    lp = en.get("mean_logprob")
    metrics = [
        Metric("text", "Transcript", en["text"] or "(nothing)"),
        Metric("mean_logprob", "Confidence (log-prob)", lp, verdict=None if lp is None else
               "pass" if lp >= LOW_CONFIDENCE else "warn"),
        Metric("auto_language", "Auto-detected language", f"{auto['language']} (p={auto['language_p']})",
               verdict="pass" if auto["language"] == en["language"] else "warn"),
        Metric("seconds_en", "Transcription time", en["seconds"], "s"),
        Metric("model", "Model", model),
    ]
    if not en["text"]:
        return Outcome("fail", "nothing transcribed", "Was there speech in the recording?", metrics)
    if lp is not None and lp < LOW_CONFIDENCE:
        return Outcome("warn", f"“{en['text']}”", "Low confidence: likely word errors.", metrics)
    return Outcome("pass", f"“{en['text']}”", None, metrics)


class TranscribeStep(Step):
    id = "transcribe"
    title = "Transcription"

    def run(self, ctx: StepContext) -> Outcome:
        path = ctx.state.get("recording")
        if path is None:
            recordings = sorted(AUDIO.glob("*.wav"), key=lambda p: p.stat().st_mtime)
            if not recordings:
                return Outcome("skipped", "no recording yet: run the mic step first")
            path = recordings[-1]
            ctx.log(f"no recording in this run; using the latest: {path.name}", "tool")

        name = ctx.options.get("model", DEFAULT_MODEL)
        last = [0.0]

        def waiting(elapsed: float) -> None:
            if elapsed - last[0] >= 2 or last[0] == 0:
                last[0] = elapsed or 0.001
                ctx.progress("model", detail=ctx.models.notes.get(name) or f"loading {name} ({elapsed:.0f} s)")

        model = ctx.models.get(name, ctx.check, waiting)
        ctx.progress("transcribe", detail="forced English")
        en = audio.transcribe(model, path, "en", ctx.check)
        ctx.progress("transcribe", detail="auto-detect")
        auto = audio.transcribe(model, path, None, ctx.check)
        ctx.emit("step.artifact", kind="transcript", file=path.name, results=[en, auto])
        return evaluate(en, auto, name)
