from pathlib import Path

import numpy as np

from pp_dashboard.rf.metadata import read_sidecar, write_capture_sidecar


def test_sidecar_roundtrip(tmp_path: Path) -> None:
    cs8 = tmp_path / "x.cs8"
    # Constant tone-ish IQ at half scale
    n = 1000
    i = np.full(n, 64, dtype=np.int8)
    q = np.zeros(n, dtype=np.int8)
    inter = np.empty(n * 2, dtype=np.int8)
    inter[0::2] = i
    inter[1::2] = q
    cs8.write_bytes(inter.tobytes())

    side = write_capture_sidecar(
        cs8,
        frequency_hz=433_920_000,
        sample_rate=10_000_000,
        lna=16,
        vga=20,
        amp=0,
        num_samples=n,
    )
    assert side.exists()
    meta = read_sidecar(cs8)
    assert meta is not None
    assert meta["frequency_hz"] == 433_920_000
    assert meta["rms_power_dbfs"] is not None
    assert meta["rms_power_dbfs"] < 0
