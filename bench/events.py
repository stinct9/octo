"""The event stream. Every change in a run is one of these; UI state is a fold over them.

Types:
  run.started    {steps, env}               run.state   {state: running|waiting_for_user|cancelling}
  run.finished   {status, reason?}          step.started {title}
  step.log       {line, stream: device|tool}
  step.progress  {phase, pct?, detail?}     e.g. compile, upload %, model load, transfer
  step.metric    {key, label, value, unit?, verdict?, previous?}
  step.level     {dbfs}                     live mic meter, ~10 Hz, not persisted
  step.prompt    {phase, message?}          ready | silent | speak | done | transfer
  step.artifact  {kind, ...}                recording | levels | transcript
  step.finished  {status, summary, hint?, duration_s, metrics}
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "run.started", "run.state", "run.finished",
    "step.started", "step.log", "step.progress", "step.metric", "step.level",
    "step.prompt", "step.artifact", "step.finished",
]

# High-rate or transient events that aren't worth keeping in the run log.
EPHEMERAL = {"step.level"}


class Event(BaseModel):
    seq: int
    run_id: str
    t: float = Field(default_factory=time.time)
    type: EventType
    step: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
