"""CLI-only step: capacity spot-check, then erase and reformat the SD card.

Destructive. The CLI asks for WIPE on the terminal and passes it as options["confirm"];
anything else makes the sketch abort with the card untouched.
"""

from __future__ import annotations

from ..runner import Metric, Outcome, Step, StepContext

HINTS = {
    "no_card_or_unreadable": "Reseat the card, contacts facing the board.",
    "fake_capacity": "The card lies about its size. Don't use it for recordings.",
    "write_rejected": "The card rejects writes past this point: capacity is likely fake.",
    "format_failed": "Formatting failed. Try the card in a computer with the SD Association's formatter.",
}


def evaluate(r: dict) -> Outcome:
    metrics = [Metric("claimed_gb", "Claimed size", round(r.get("claimed_sectors", 0) * 512 / 1e9, 1), "GB")]
    if "samples" in r:
        metrics.append(Metric("bad_samples", "Bad capacity samples", f"{r['bad_samples']} of {r['samples']}",
                              verdict="pass" if r["bad_samples"] == 0 else "fail"))
    if r.get("error") == "not_confirmed":
        return Outcome("skipped", "not confirmed; card untouched", None, metrics)
    if "error" in r:
        return Outcome("fail", f"{r.get('phase')}: {r['error'].replace('_', ' ')}", HINTS.get(r["error"]), metrics)
    return Outcome("pass", "capacity spot-check passed; wiped and reformatted FAT32", None, metrics)


class SdWipeStep(Step):
    id = "sd_wipe"
    title = "SD wipe (destructive)"
    sketch = "01b_sd_wipe"

    def run(self, ctx: StepContext) -> Outcome:
        ctx.flash()
        board = ctx.connect()
        answer = b"WIPE\n" if ctx.options.get("confirm") == "WIPE" else b"NO\n"

        def on_data(d: dict) -> None:
            if d.get("confirm") == "WIPE":
                board.send(answer)

        with ctx.protected("the SD wipe"):
            result = board.run(b"g", timeout=900, on_line=ctx.log, on_data=on_data)
        return evaluate(result)
