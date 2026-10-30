"""Step: Wi-Fi connects and the signal is good enough for uploads."""

from __future__ import annotations

from .. import SKETCHES
from ..runner import Metric, Outcome, Step, StepContext

SECRETS = SKETCHES / "03_wifi" / "secrets.h"


def rssi_verdict(rssi: float) -> str:
    return "pass" if rssi >= -70 else "warn" if rssi >= -80 else "fail"


def evaluate(r: dict) -> Outcome:
    metrics = [Metric("networks_visible", "Networks visible", r.get("networks_visible"))]
    if not r.get("own_visible"):
        return Outcome("fail", "your network isn't visible to the board",
                       "The ESP32 only sees 2.4 GHz. Check the name in secrets.h matches exactly, "
                       "and that the U.FL antenna is fitted.", metrics)
    metrics += [
        Metric("channel", "Channel", r["channel"]),
        Metric("others_on_channel", "Other networks on channel", r["others_on_channel"]),
    ]
    if not r.get("connected"):
        return Outcome("fail", "visible but couldn't connect",
                       "Check the password in secrets.h, and that the network is 2.4 GHz.", metrics)
    mean = r["rssi_mean"]
    verdict = rssi_verdict(mean)
    metrics += [
        Metric("rssi_mean", "Signal", round(mean), "dBm", verdict),
        Metric("rssi_range", "Signal range", f"{r['rssi_min']} to {r['rssi_max']}", "dBm"),
        Metric("connect_ms", "Connect time", r["connect_ms"], "ms"),
    ]
    summary = f"connected, {mean:.0f} dBm"
    if verdict == "fail":
        return Outcome("fail", summary, "Too weak for uploads. Is the antenna fitted? Move closer to the router.", metrics)
    if verdict == "warn":
        return Outcome("warn", summary, "Uploads will be slow or flaky at this strength.", metrics)
    return Outcome("pass", summary, None, metrics)


class WifiStep(Step):
    id = "wifi"
    title = "Wi-Fi"
    sketch = "03_wifi"

    def skip_reason(self) -> str | None:
        if not SECRETS.exists():
            return "no secrets.h: copy firmware/bench/03_wifi/secrets.h.example to secrets.h and fill it in"
        text = SECRETS.read_text()
        if "your-network" in text or "your-password" in text:
            return "secrets.h still has the example values"
        return None

    def run(self, ctx: StepContext) -> Outcome:
        ctx.flash()
        board = ctx.connect()
        result = board.run(b"g", timeout=60, on_line=ctx.log, check=ctx.check)
        return evaluate(result)
