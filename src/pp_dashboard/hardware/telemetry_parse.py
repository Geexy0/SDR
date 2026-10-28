"""Parse hackrf_transfer / hackrf_sweep stderr/stdout into telemetry metrics."""

from __future__ import annotations

import re
from typing import Any

# hackrf_transfer progress: "3.814697 MiB / 1.000000 sec = 3.814697 MiB/second"
MIB_RATE_RE = re.compile(
    r"(?P<mib>[\d.]+)\s*MiB\s*/\s*(?P<sec>[\d.]+)\s*sec\s*=\s*(?P<rate>[\d.]+)\s*MiB",
    re.IGNORECASE,
)
# Average power style lines (when present in tooling wrappers / future builds)
POWER_RE = re.compile(
    r"(?:avg(?:erage)?\s*power|power)\s*[:=]\s*(?P<dbfs>[-+]?\d+(?:\.\d+)?)\s*dB(?:FS)?",
    re.IGNORECASE,
)
CLIP_RE = re.compile(r"clip(?:s|ping|ped)?\s*[:=]?\s*(?P<clips>\d+)", re.IGNORECASE)
# hackrf_sweep CSV-ish power columns often end with dB values; capture last float as proxy.
SWEEP_DB_RE = re.compile(r",\s*(-?\d+(?:\.\d+)?)\s*$")


def parse_hackrf_line(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None

    metrics: dict[str, Any] = {}

    mib = MIB_RATE_RE.search(text)
    if mib:
        metrics["mib"] = float(mib.group("mib"))
        metrics["seconds"] = float(mib.group("sec"))
        metrics["mib_per_sec"] = float(mib.group("rate"))
        metrics["mbps"] = float(mib.group("rate")) * 8.0  # mebibyte/s → rough Mibit/s scale note

    power = POWER_RE.search(text)
    if power:
        metrics["avg_power_dbfs"] = float(power.group("dbfs"))

    clips = CLIP_RE.search(text)
    if clips:
        metrics["clip_count"] = int(clips.group("clips"))

    if "hackrf_sweep" in text.lower() or text.count(",") >= 5:
        sweep = SWEEP_DB_RE.search(text)
        if sweep:
            metrics["sweep_bin_db"] = float(sweep.group(1))

    return metrics or None
