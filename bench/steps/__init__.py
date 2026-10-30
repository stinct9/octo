"""The bench steps, in run-all order.

The mic step comes first so the only part that needs the owner is over in the first minute.
"""

from __future__ import annotations

from ..runner import Step
from .board import BoardStep
from .mic import MicStep
from .record import RecordStep
from .sd import SdStep
from .sd_wipe import SdWipeStep
from .transcribe import TranscribeStep
from .wifi import WifiStep

STEPS: dict[str, Step] = {s.id: s for s in (BoardStep(), MicStep(), SdStep(), WifiStep(), TranscribeStep())}
RUN_ALL = ["board", "mic", "sd", "wifi", "transcribe"]

# Not part of run-all, and not exposed in the UI: destructive or rare, CLI only.
CLI_ONLY: dict[str, Step] = {"sd_wipe": SdWipeStep(), "record": RecordStep()}
