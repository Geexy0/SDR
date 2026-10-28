"""Flipper Zero / Mayhem .sub ↔ .cs8 / .C16 transcoder."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

from pp_dashboard.subghz.ook_synth import DEFAULT_DELTA_F, envelope_to_cs8

SubKind = Literal["raw", "key"]


@dataclass(slots=True)
class SubFile:
    filetype: str
    version: int = 1
    frequency: int = 433_920_000
    preset: str = "FuriHalSubGhzPresetOok650Async"
    protocol: str | None = None
    bit: int | None = None
    key: str | None = None
    te: int | None = None
    raw_data: list[int] = field(default_factory=list)  # µs signed pulses
    kind: SubKind = "raw"


_KV = re.compile(r"^([A-Za-z0-9_]+):\s*(.*)$")


def parse_sub(text: str) -> SubFile:
    meta: dict[str, str] = {}
    raw_chunks: list[int] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _KV.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "RAW_Data":
            for tok in val.split():
                try:
                    raw_chunks.append(int(tok))
                except ValueError:
                    continue
        else:
            meta[key] = val

    filetype = meta.get("Filetype", "Flipper SubGhz RAW File")
    kind: SubKind = "key" if "Key File" in filetype else "raw"
    return SubFile(
        filetype=filetype,
        version=int(meta.get("Version", "1")),
        frequency=int(meta.get("Frequency", "433920000")),
        preset=meta.get("Preset", "FuriHalSubGhzPresetOok650Async"),
        protocol=meta.get("Protocol"),
        bit=int(meta["Bit"]) if "Bit" in meta else None,
        key=meta.get("Key"),
        te=int(meta["TE"]) if "TE" in meta else None,
        raw_data=raw_chunks,
        kind=kind,
    )


def parse_sub_file(path: Path) -> SubFile:
    return parse_sub(path.read_text(encoding="utf-8", errors="replace"))


def render_sub(sub: SubFile) -> str:
    lines = [
        f"Filetype: {sub.filetype}",
        f"Version: {sub.version}",
        f"Frequency: {sub.frequency}",
        f"Preset: {sub.preset}",
    ]
    if sub.protocol:
        lines.append(f"Protocol: {sub.protocol}")
    if sub.bit is not None:
        lines.append(f"Bit: {sub.bit}")
    if sub.key:
        lines.append(f"Key: {sub.key}")
    if sub.te is not None:
        lines.append(f"TE: {sub.te}")
    if sub.raw_data:
        # Flipper wraps long RAW_Data; keep single line chunks of ~512 tokens.
        chunk = 256
        for i in range(0, len(sub.raw_data), chunk):
            part = " ".join(str(x) for x in sub.raw_data[i : i + chunk])
            lines.append(f"RAW_Data: {part}")
    return "\n".join(lines) + "\n"


def pulses_to_envelope(pulses_us: list[int], sample_rate: int) -> np.ndarray:
    segs: list[tuple[bool, int]] = []
    for p in pulses_us:
        if p == 0:
            continue
        segs.append((p > 0, abs(int(p))))
    if not segs:
        return np.zeros(0, dtype=np.float32)
    counts = np.array(
        [max(1, int(round(us * sample_rate / 1_000_000.0))) for _, us in segs],
        dtype=np.int64,
    )
    levels = np.array([1.0 if hi else 0.0 for hi, _ in segs], dtype=np.float32)
    total = int(counts.sum())
    env = np.empty(total, dtype=np.float32)
    pos = 0
    for level, n in zip(levels, counts, strict=True):
        env[pos : pos + n] = level
        pos += n
    return env


def sub_to_cs8(
    sub: SubFile,
    *,
    sample_rate: int,
    out_path: Path,
    amplitude: float = 100.0,
    delta_f_hz: float = DEFAULT_DELTA_F,
) -> Path:
    if not sub.raw_data:
        raise ValueError("SUB file has no RAW_Data to convert")
    env = pulses_to_envelope(sub.raw_data, sample_rate)
    iq = envelope_to_cs8(env, sample_rate=sample_rate, amplitude=amplitude, delta_f_hz=delta_f_hz)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    iq.tofile(out_path)
    return out_path


def cs8_pulses_to_sub(
    pulses_us: list[int],
    *,
    frequency_hz: int,
    out_path: Path,
) -> Path:
    sub = SubFile(
        filetype="Flipper SubGhz RAW File",
        frequency=frequency_hz,
        raw_data=list(pulses_us),
        kind="raw",
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_sub(sub), encoding="utf-8")
    return out_path


def sub_to_c16(
    sub: SubFile,
    *,
    sample_rate: int,
    out_path: Path,
    amplitude: int = 2000,
) -> Path:
    """Mayhem-style interleaved int16 IQ (.C16)."""
    if not sub.raw_data:
        raise ValueError("SUB file has no RAW_Data")
    env = pulses_to_envelope(sub.raw_data, sample_rate)
    n = env.size
    t = np.arange(n, dtype=np.float32) / np.float32(sample_rate)
    phase = (2.0 * np.pi * np.float32(DEFAULT_DELTA_F)) * t
    i = (amplitude * env * np.cos(phase)).astype(np.float32)
    q = (amplitude * env * np.sin(phase)).astype(np.float32)
    out = np.empty(n * 2, dtype=np.int16)
    np.clip(np.rint(i), -32768, 32767, out=i)
    np.clip(np.rint(q), -32768, 32767, out=q)
    out[0::2] = i.astype(np.int16, copy=False)
    out[1::2] = q.astype(np.int16, copy=False)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.tofile(out_path)
    return out_path


def sub_to_mayhem_txt(sub: SubFile, out_path: Path) -> Path:
    """Simple Mayhem FREQMAN-ish note + pulse dump for SD."""
    lines = [
        f"# converted from Flipper SUB",
        f"# Frequency: {sub.frequency}",
        f"# Preset: {sub.preset}",
        f"f={sub.frequency}",
    ]
    if sub.raw_data:
        lines.append("pulses_us=" + ",".join(str(x) for x in sub.raw_data[:5000]))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
