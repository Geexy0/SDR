"""IQ capture metadata sidecars (.cs8.json)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pp_dashboard.rf.chirp import estimate_rms_dbfs


def sidecar_path(cs8: Path) -> Path:
    return cs8.with_suffix(cs8.suffix + ".json")


def write_capture_sidecar(
    cs8: Path,
    *,
    frequency_hz: int,
    sample_rate: int,
    lna: int,
    vga: int,
    amp: int,
    num_samples: int,
    extra: dict[str, Any] | None = None,
) -> Path:
    rms = estimate_rms_dbfs(cs8) if cs8.exists() else float("-inf")
    payload: dict[str, Any] = {
        "filepath": str(cs8),
        "frequency_hz": frequency_hz,
        "sample_rate": sample_rate,
        "lna": lna,
        "vga": vga,
        "amp": amp,
        "num_samples": num_samples,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "rms_power_dbfs": rms if rms != float("-inf") else None,
        "format": "cs8",
    }
    if extra:
        payload.update(extra)
    out = sidecar_path(cs8)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def read_sidecar(cs8: Path) -> dict[str, Any] | None:
    path = sidecar_path(cs8)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
