"""CLI-only step: a 35 s recording for voice enrolment (`bench record`).

Same as the mic step -- self-test, level meter until Ready, cues, analysis -- but 35 s: a 3 s
silent lead-in (so the background level is measured), then ~30 s of reading. The WAV goes
where the CLI says, ready for `octo enroll`.
"""

from __future__ import annotations

from pathlib import Path

from ..runner import StepContext
from .mic import MicStep

ENROL_MESSAGE = ("Sit about 1 m away, where the board will usually be. Press Ready, stay silent until the "
                 "Glass sound (3 s), then read the passage aloud in your normal voice until the Hero sound (35 s).")


class RecordStep(MicStep):
    id = "record"
    title = "Record 35 s"
    needs_user = True
    seconds = 35
    ready_message = ENROL_MESSAGE

    def output_path(self, ctx: StepContext) -> Path:
        out = ctx.options.get("out")
        return Path(out) if out else ctx.audio_dir / f"record_{ctx.run_id}.wav"
