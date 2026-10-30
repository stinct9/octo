"""Step: the Sense's microSD slot mounts, writes, verifies, and keeps up with audio."""

from __future__ import annotations

from ..runner import Metric, Outcome, Step, StepContext

AUDIO_BYTES_PER_S = 16000 * 2  # 16 kHz x 16-bit mono
MIN_HEADROOM = 10  # write speed as a multiple of the audio rate

HINTS = {
    "no_card_or_unreadable": "Reseat the card: push until it stops, contacts facing the board. "
                             "It must be FAT32 and 32 GB or smaller. A half-seated card looks inserted.",
    "short_write": "Card full or failing. Try `bench wipe`, or another card.",
    "open_failed": "Filesystem problem. Try `bench wipe`.",
    "short_read": "Card failing, or the filesystem is damaged. Try `bench wipe`.",
    "readback_mismatch": "Data came back wrong: suspect a fake-capacity card. Check it with f3write/f3read.",
}


def evaluate(r: dict) -> Outcome:
    metrics = []
    attempts = r.get("mount_attempts")
    if attempts is not None:
        metrics.append(Metric("mount_attempts", "Mount attempts", attempts,
                              verdict="pass" if attempts == 1 else "warn"))
    if "error" in r:
        return Outcome("fail", f"{r.get('phase')}: {r['error'].replace('_', ' ')}", HINTS.get(r["error"]), metrics)

    write_bps = r["test_bytes"] / (r["write_ms"] / 1000)
    read_bps = r["test_bytes"] / (r["read_ms"] / 1000)
    headroom = write_bps / AUDIO_BYTES_PER_S
    # A chunk holds this much audio; a stall longer than that needs a bigger RAM buffer.
    budget_ms = r["chunk_bytes"] / AUDIO_BYTES_PER_S * 1000
    worst = r["worst_chunk_ms"]
    metrics += [
        Metric("write_kbs", "Write speed", round(write_bps / 1024), "KB/s"),
        Metric("read_kbs", "Read speed", round(read_bps / 1024), "KB/s"),
        Metric("headroom", "Headroom vs audio", round(headroom, 1), "x",
               "pass" if headroom >= MIN_HEADROOM else "warn"),
        Metric("worst_chunk_ms", f"Worst stall (budget {budget_ms:.0f} ms)", worst, "ms",
               "pass" if worst < budget_ms / 2 else "warn"),
        Metric("card_gb", "Card size", round(r["card_bytes"] / 1e9, 1), "GB"),
        Metric("fs_used_mb", "Used", round(r["fs_used_bytes"] / 2**20), "MB"),
        Metric("verified", "Read-back verified", r.get("verified"), verdict="pass" if r.get("verified") else "fail"),
    ]
    if r.get("leftover_test_file"):
        metrics.append(Metric("leftover_test_file", "Leftover test file", True, verdict="warn"))

    summary = f"{write_bps / 1024:.0f} KB/s write, {headroom:.0f}x audio rate, verified"
    if headroom < MIN_HEADROOM:
        return Outcome("warn", summary, "Slow card: under 10x the audio rate leaves little margin for stalls.", metrics)
    if attempts and attempts > 1:
        return Outcome("warn", summary + f", mounted on attempt {attempts}",
                       "Intermittent mount (known open issue) -- the recorder must retry mounts.", metrics)
    if r.get("leftover_test_file"):
        return Outcome("warn", summary, "An earlier test was interrupted mid-write; the file was cleaned up.", metrics)
    return Outcome("pass", summary, None, metrics)


class SdStep(Step):
    id = "sd"
    title = "SD card"
    sketch = "01_sd"

    def run(self, ctx: StepContext) -> Outcome:
        ctx.flash()
        board = ctx.connect()
        ctx.check()
        # Once g is sent the card is being written; stopping mid-write corrupted it before.
        with ctx.protected("the SD write/verify test"):
            result = board.run(b"g", timeout=60, on_line=ctx.log)
        return evaluate(result)
