"""Step: the PDM mic works, then a cued 10 s recording at conversational distance.

Sequence: 2 s self-test -> live level meter while waiting for Ready -> recording with cues
(silent 3 s, speak 7 s) -> analysis, including a check that the Glass cue landed where it
should, which proves the recording's timeline and catches a muted Mac.
"""

from __future__ import annotations

from .. import audio
from ..protocol import parse
from ..runner import Metric, Outcome, Step, StepContext

READY_MESSAGE = ("Sit about 1 m away. Press Ready, stay silent until the Glass sound (3 s), "
                 "then talk normally until the Hero sound.")


def evaluate_selftest(r: dict) -> Outcome | None:
    """None if the mic looks alive; a failing Outcome otherwise."""
    if "error" in r:
        return Outcome("fail", f"mic error: {r['error'].replace('_', ' ')}",
                       "Is the Sense board seated on the XIAO? The mic is on the Sense board.")
    if r.get("samples", 0) == 0 or r.get("empty_blocks", 0) > 0:
        return Outcome("fail", "no samples from the mic", "The mic isn't clocking. Reseat the Sense board.")
    if r.get("longest_run", 0) > 800:  # half of a 100 ms block identical: a stuck data line
        return Outcome("fail", "mic data stuck at one value", "Reseat the Sense board.")
    if r.get("dbfs_max", -120) < -95:
        return Outcome("fail", "mic is silent", "Reseat the Sense board.")
    return None


def evaluate_recording(a: dict, record_ms: int | None, seconds: int = audio.RECORD_SECONDS) -> Outcome:
    if a.get("empty"):
        return Outcome("fail", "empty recording")
    snr, cue = a.get("snr_db"), a.get("cue_at")
    lo, hi = audio.CUE_WINDOW
    cue_ok = cue is not None and lo - 0.2 <= cue <= hi
    metrics = [
        Metric("snr_db", "Speech above background", snr, "dB",
               None if snr is None else "pass" if snr >= 10 else "warn"),
        Metric("speech_db", "Speech level", a.get("speech_db"), "dBFS"),
        Metric("quiet_db", "Background level", a.get("quiet_db"), "dBFS"),
        Metric("peak_dbfs", "Peak", a.get("peak_dbfs"), "dBFS"),
        Metric("clipped", "Clipped samples", a.get("clipped"), verdict="pass" if not a.get("clipped") else "warn"),
        Metric("longest_flat_ms", "Longest dropout", a.get("longest_flat_ms"), "ms",
               "pass" if a.get("longest_flat_ms", 0) < 5 else "fail"),
        Metric("cue_at", "Speak cue heard at", cue, "s", "pass" if cue_ok else "warn"),
    ]
    if record_ms is not None:
        metrics.append(Metric("record_ms", "Recording duration", record_ms, "ms",
                              "pass" if abs(record_ms - seconds * 1000) <= 100 else "warn"))

    if a.get("seconds", 0) < seconds - 0.1:
        return Outcome("fail", f"recording only {a.get('seconds')} s", None, metrics)
    if a.get("longest_flat_ms", 0) >= 5:
        return Outcome("fail", "dropouts in the recording", "Audio went flat -- check the Sense board.", metrics)
    summary = f"SNR {snr} dB" if snr is not None else "recorded"
    if not cue_ok:
        return Outcome("warn", summary + ", speak cue not detected",
                       "Is the Mac muted or playing to headphones? Without the cue the SNR windows may be wrong.",
                       metrics)
    if a.get("clipped"):
        return Outcome("warn", summary + ", clipping", "Too loud or too close to the mic.", metrics)
    if snr is not None and snr < 10:
        return Outcome("warn", summary, "Marginal for transcription. A noisy room? Check the transcript.", metrics)
    return Outcome("pass", summary, None, metrics)


class MicStep(Step):
    id = "mic"
    title = "Microphone"
    sketch = "02_mic"
    needs_user = True
    seconds = audio.RECORD_SECONDS
    ready_message = READY_MESSAGE

    def output_path(self, ctx: StepContext):
        return ctx.audio_dir / f"bench_{ctx.run_id}.wav"

    def run(self, ctx: StepContext) -> Outcome:
        ctx.flash()
        board = ctx.connect()

        self_test = board.run(b"g", timeout=10, on_line=ctx.log, check=ctx.check)
        bad = evaluate_selftest(self_test)
        if bad:
            return bad
        ctx.log(f"self-test ok: {self_test['dbfs_min']}..{self_test['dbfs_max']} dBFS", "tool")

        # Pre-roll: stream levels so the meter moves while the owner gets into position.
        board.send(b"m")
        try:
            def pump() -> None:
                line = board.readline(0.2)
                data = parse(line) if line else None
                if data and "level" in data:
                    ctx.emit("step.level", dbfs=data["level"]["dbfs"])
            ctx.wait_for_user(self.ready_message, pump)
        finally:
            board.send(b"m")  # stop the stream even on cancel, so the next upload isn't flooded
            board.drain(quiet=0.3, limit=3)

        path = self.output_path(ctx)
        with ctx.protected("the recording"):
            result = audio.capture(board, path, lambda phase, t: ctx.emit("step.prompt", phase=phase, t=round(t, 2)),
                                   cues=ctx.options.get("cues", True), seconds=self.seconds)
        ctx.state["recording"] = path
        analysis = audio.analyse(path)
        ctx.emit("step.artifact", kind="recording", file=path.name)
        ctx.emit("step.artifact", kind="levels", **{k: analysis.get(k) for k in (
            "levels", "window_s", "quiet_window", "speech_window", "cue_at", "seconds")})
        return evaluate_recording(analysis, result.get("record_ms"), self.seconds)
