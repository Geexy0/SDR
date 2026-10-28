import struct

import numpy as np

from pp_dashboard.recon.sweep_parser import SweepBinaryParser, assemble_row, powers_to_uint8
from pp_dashboard.recon.synth_fm_rds import synthesize_fm_rds
from pp_dashboard.recon.synth_pager import synthesize_lrs_pager, synthesize_touchtunes_pin


def _record(lo: int, hi: int, powers: list[float]) -> bytes:
    arr = np.asarray(powers, dtype="<f4")
    rec_len = 16 + arr.nbytes
    return struct.pack("<IQQ", rec_len, lo, hi) + arr.tobytes()


def test_parser_one_record() -> None:
    parser = SweepBinaryParser()
    blob = _record(100_000_000, 105_000_000, [-50.0, -60.0, -55.0])
    recs = parser.feed(blob)
    assert len(recs) == 1
    assert recs[0].lo_hz == 100_000_000
    assert recs[0].power_db.tolist() == [-50.0, -60.0, -55.0]


def test_parser_incremental() -> None:
    parser = SweepBinaryParser()
    blob = _record(200_000_000, 205_000_000, [-40.0] * 10)
    assert parser.feed(blob[:7]) == []
    recs = parser.feed(blob[7:])
    assert len(recs) == 1


def test_assemble_and_compress() -> None:
    parser = SweepBinaryParser()
    recs = parser.feed(_record(433_000_000, 433_100_000, [-30.0] * 5))
    row, n = assemble_row(recs, start_hz=433_000_000, stop_hz=434_000_000, bin_hz=100_000)
    assert n == 10
    u8 = powers_to_uint8(row)
    assert len(u8) == n


def test_pager_and_fm(tmp_path) -> None:
    p = synthesize_lrs_pager(42, path=tmp_path / "p.cs8")
    assert p.path.exists()
    t = synthesize_touchtunes_pin("99", path=tmp_path / "t.cs8")
    assert t.path.exists()
    fm = synthesize_fm_rds(path=tmp_path / "fm.cs8", duration_s=0.2, sample_rate=500_000)
    assert fm.path.stat().st_size > 1000
