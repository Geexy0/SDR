"""Hardware-isolation fixtures — pytest must never touch real USB / RF."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.fakes import FakeMayhemSerial, FakeSubprocessPool


@pytest.fixture
def synthetic_cs8_factory(tmp_path: Path):
    """Deterministic CS8 IQ file builders under tmp_path."""

    class Factory:
        def __init__(self, root: Path) -> None:
            self.root = root
            self.root.mkdir(parents=True, exist_ok=True)

        def make_cw_tone(
            self,
            *,
            amplitude: int = 64,
            freq_offset_hz: float = 50_000.0,
            sample_rate: int = 2_000_000,
            num_samples: int = 20_000,
            name: str = "cw_tone.cs8",
        ) -> Path:
            n = np.arange(num_samples, dtype=np.float64)
            phase = 2.0 * math.pi * (freq_offset_hz / float(sample_rate)) * n
            i = np.clip(np.rint(amplitude * np.cos(phase)), -128, 127).astype(np.int8)
            q = np.clip(np.rint(amplitude * np.sin(phase)), -128, 127).astype(np.int8)
            out = np.empty(num_samples * 2, dtype=np.int8)
            out[0::2] = i
            out[1::2] = q
            path = self.root / name
            path.write_bytes(out.tobytes())
            return path

        def make_ook_pt2262(
            self,
            *,
            short_us: int = 350,
            long_us: int = 1050,
            sync_us: int = 10850,
            bits: str = "101001011100",
            sample_rate: int = 2_000_000,
            amplitude: int = 80,
            freq_offset_hz: float = 50_000.0,
            name: str = "ook_pt2262.cs8",
            repeats: int = 3,
        ) -> Path:
            """PWM OOK: bit1=short-hi/long-lo, bit0=long-hi/short-lo, then sync gap."""

            def us_to_n(us: int) -> int:
                return max(1, int(round(us * sample_rate / 1e6)))

            env_parts: list[np.ndarray] = []
            for _ in range(repeats):
                for b in bits:
                    if b == "1":
                        env_parts.append(np.ones(us_to_n(short_us), dtype=np.float32))
                        env_parts.append(np.zeros(us_to_n(long_us), dtype=np.float32))
                    else:
                        env_parts.append(np.ones(us_to_n(long_us), dtype=np.float32))
                        env_parts.append(np.zeros(us_to_n(short_us), dtype=np.float32))
                env_parts.append(np.zeros(us_to_n(sync_us), dtype=np.float32))
            env = np.concatenate(env_parts)
            n = env.size
            t = np.arange(n, dtype=np.float64) / sample_rate
            carrier = np.exp(2j * math.pi * freq_offset_hz * t).astype(np.complex64)
            z = (amplitude / 127.0) * env * carrier
            i = np.clip(np.rint(z.real * 127.0), -128, 127).astype(np.int8)
            q = np.clip(np.rint(z.imag * 127.0), -128, 127).astype(np.int8)
            out = np.empty(n * 2, dtype=np.int8)
            out[0::2] = i
            out[1::2] = q
            path = self.root / name
            path.write_bytes(out.tobytes())
            return path

    return Factory(tmp_path / "iq")


@pytest.fixture(autouse=True)
def isolate_hardware(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeSubprocessPool:
    """Redirect captures/DBs and mock serial + SDR subprocesses for every test."""
    FakeMayhemSerial.reset()
    pool = FakeSubprocessPool()

    # --- paths ---
    captures = tmp_path / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    from pp_dashboard.config import settings

    monkeypatch.setattr(settings, "captures_dir", captures)
    monkeypatch.setattr(settings, "subghz_db_path", captures / "subghz_packets.sqlite3")

    # --- serial ---
    import serial

    monkeypatch.setattr(serial, "Serial", FakeMayhemSerial)
    try:
        import serial.serialutil as serialutil

        monkeypatch.setattr(serialutil, "Serial", FakeMayhemSerial)
    except Exception:
        pass

    # --- subprocess pool ---
    pool.install(monkeypatch)

    # detect_hardware should never call real lsusb/hackrf_info
    async def _fake_detect(cfg: Any = None):
        from pp_dashboard.hardware.detect import DetectionResult

        return DetectionResult(
            mayhem_serial=True,
            hackrf_native=False,
            serial_port="/dev/ttyACM0",
            lsusb_ids=(),
            hackrf_info_ok=False,
        )

    monkeypatch.setattr(
        "pp_dashboard.hardware.detect.detect_hardware",
        _fake_detect,
    )
    monkeypatch.setattr(
        "pp_dashboard.hardware.arbiter.detect_hardware",
        _fake_detect,
    )

    # Resolve serial port without touching filesystem by-id links
    monkeypatch.setattr(
        "pp_dashboard.hardware.detect.resolve_mayhem_serial_port",
        lambda cfg=None: "/dev/ttyACM0",
    )
    monkeypatch.setattr(
        "pp_dashboard.hardware.arbiter.resolve_mayhem_serial_port",
        lambda cfg=None: "/dev/ttyACM0",
    )

    return pool
