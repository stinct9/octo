"""Step: the board is alive and is the hardware we paid for."""

from __future__ import annotations

from ..runner import Metric, Outcome, Step, StepContext

MB = 1024 * 1024


def evaluate(r: dict) -> Outcome:
    chip, flash, psram = r.get("chip"), r.get("flash_bytes", 0), r.get("psram_bytes", 0)
    metrics = [
        Metric("chip", "Chip", f"{chip} rev {r.get('rev')}", verdict="pass" if chip == "ESP32-S3" else "fail"),
        Metric("cpu_mhz", "CPU", r.get("cpu_mhz"), "MHz"),
        Metric("flash_mb", "Flash", round(flash / MB), "MB", "pass" if flash == 8 * MB else "fail"),
        Metric("psram_mb", "PSRAM", round(psram / MB), "MB", "pass" if psram >= 8 * MB else "fail"),
        Metric("mac", "MAC", r.get("mac")),
    ]
    if chip != "ESP32-S3":
        return Outcome("fail", f"unexpected chip {chip}", "Is this the XIAO ESP32S3 Sense?", metrics)
    if psram < 8 * MB:
        return Outcome("fail", f"PSRAM {psram // MB} MB, expected 8",
                       "The firmware must be built with PSRAM=opi (bench.firmware.FQBN).", metrics)
    if flash != 8 * MB:
        return Outcome("fail", f"flash {flash // MB} MB, expected 8", None, metrics)
    return Outcome("pass", f"ESP32-S3, {flash // MB} MB flash, {psram // MB} MB PSRAM", None, metrics)


class BoardStep(Step):
    id = "board"
    title = "Board, memory, USB"
    sketch = "00_hello"

    def run(self, ctx: StepContext) -> Outcome:
        ctx.flash()
        board = ctx.connect()
        result = board.run(b"g", timeout=10, on_line=ctx.log, check=ctx.check)
        return evaluate(result)
