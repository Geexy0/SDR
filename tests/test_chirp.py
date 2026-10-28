from pathlib import Path

import numpy as np
import pytest

from pp_dashboard.rf.chirp import estimate_rms_dbfs, synthesize_chirp_cs8


def test_synthesize_chirp_cs8(tmp_path: Path) -> None:
    out = tmp_path / "chirp.cs8"
    synthesize_chirp_cs8(
        sample_rate=2_000_000,
        bandwidth_hz=1_000_000,
        sweep_period_s=0.001,
        amplitude=100,
        path=out,
    )
    raw = np.fromfile(out, dtype=np.int8)
    assert raw.size == 2_000_000 * 0.001 * 2
    assert raw.dtype == np.int8
    rms = estimate_rms_dbfs(out)
    assert rms > -20  # strong chirp near full-ish scale


def test_chirp_rejects_bandwidth_gt_fs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceed sample_rate"):
        synthesize_chirp_cs8(
            sample_rate=2_000_000,
            bandwidth_hz=5_000_000,
            sweep_period_s=0.001,
            path=tmp_path / "x.cs8",
        )
