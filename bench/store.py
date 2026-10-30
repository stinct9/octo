"""Run logs: one JSONL file of events per run in runs/, appended as events happen."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from . import RUNS
from .events import EPHEMERAL, Event


class RunStore:
    def __init__(self, root: Path = RUNS):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def new_run_id(self) -> str:
        base = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id, n = base, 1
        while (self.root / f"{run_id}.jsonl").exists():
            n += 1
            run_id = f"{base}-{n}"
        return run_id

    def path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.jsonl"

    def append(self, event: Event) -> None:
        if event.type in EPHEMERAL:
            return
        with self._lock, open(self.path(event.run_id), "a") as f:
            f.write(event.model_dump_json() + "\n")

    def read(self, run_id: str) -> list[dict]:
        p = self.path(run_id)
        if not p.exists():
            return []
        with open(p) as f:
            return [json.loads(line) for line in f if line.strip()]

    def run_ids(self) -> list[str]:
        """Newest first."""
        return sorted((p.stem for p in self.root.glob("*.jsonl")), reverse=True)

    def recover_aborted(self, exclude: str | None = None) -> list[str]:
        """Close out runs that never finished (process killed mid-run). Returns their IDs.

        Only safe while holding the board lock -- use runner.recover_if_idle().
        """
        aborted = []
        for run_id in self.run_ids():
            if run_id == exclude:
                continue
            events = self.read(run_id)
            if events and not any(e["type"] == "run.finished" for e in events):
                last = events[-1]
                self.append(Event(seq=last["seq"] + 1, run_id=run_id, type="run.finished",
                                  data={"status": "aborted", "reason": "server stopped mid-run"}))
                aborted.append(run_id)
        return aborted

    def summary(self, run_id: str) -> dict:
        events = self.read(run_id)
        started = next((e for e in events if e["type"] == "run.started"), None)
        finished = next((e for e in reversed(events) if e["type"] == "run.finished"), None)
        steps = {e["step"]: e["data"]["status"] for e in events if e["type"] == "step.finished"}
        return {
            "run_id": run_id,
            "started_at": started["t"] if started else None,
            "status": finished["data"]["status"] if finished else "running",
            "steps": steps,
        }

    def previous_metrics(self, before: str | None = None) -> dict[str, dict[str, object]]:
        """Most recent recorded value of every metric, per step, from runs older than `before`.

        Skipped/cancelled steps don't count -- "last time" means the last time it was measured.
        """
        out: dict[str, dict[str, object]] = {}
        for run_id in self.run_ids():
            if before and run_id >= before:
                continue
            for e in self.read(run_id):
                if e["type"] != "step.finished" or e["data"].get("status") not in ("pass", "warn", "fail"):
                    continue
                step_prev = out.setdefault(e["step"], {})
                for key, value in (e["data"].get("metrics") or {}).items():
                    step_prev.setdefault(key, value)
        return out
