from pathlib import Path

import numpy as np

from pp_dashboard.subghz.ook_synth import (
    de_bruijn,
    envelope_to_cs8,
    synthesize_de_bruijn_stream,
    synthesize_framed_bruteforce,
)
from pp_dashboard.subghz.subfile import parse_sub, render_sub, sub_to_cs8
from pp_dashboard.subghz.analyze import analyze_cs8


def test_de_bruijn_length() -> None:
    seq = de_bruijn(2, 8)
    assert seq.size == 2**8
    # every 8-bit window unique cyclic — spot check cardinality of windows
    extended = np.concatenate([seq, seq[:7]])
    windows = {tuple(extended[i : i + 8].tolist()) for i in range(256)}
    assert len(windows) == 256


def test_framed_synth_fast(tmp_path: Path) -> None:
    out = tmp_path / "ook.cs8"
    result = synthesize_framed_bruteforce(
        family="PT2262",
        nbits=8,
        key_start=0,
        key_end=31,
        repeats=2,
        sample_rate=2_000_000,
        path=out,
    )
    assert out.exists()
    assert result.bytes_written == out.stat().st_size
    assert result.elapsed_ms < 2000


def test_100mb_cs8_under_500ms(tmp_path: Path) -> None:
    """Generate ~100 MiB interleaved IQ via tiled envelope (vectorized path)."""
    import time

    target_bytes = 100 * 1024 * 1024
    samples = target_bytes // 2
    # Build a short pattern then tile — exercises envelope_to_cs8 at scale.
    base = np.zeros(20_000, dtype=np.float32)
    base[:5_000] = 1.0
    reps = int(np.ceil(samples / base.size))
    env = np.tile(base, reps)[:samples]
    t0 = time.perf_counter()
    iq = envelope_to_cs8(env, sample_rate=2_000_000, amplitude=100.0)
    path = tmp_path / "big.cs8"
    iq.tofile(path)
    ms = (time.perf_counter() - t0) * 1000.0
    assert path.stat().st_size >= target_bytes
    assert ms < 500, f"took {ms:.1f} ms"


def test_sub_roundtrip(tmp_path: Path) -> None:
    text = """Filetype: Flipper SubGhz RAW File
Version: 1
Frequency: 433920000
Preset: FuriHalSubGhzPresetOok650Async
RAW_Data: 400 -400 800 -800 400 -1200
"""
    sub = parse_sub(text)
    assert sub.frequency == 433_920_000
    assert len(sub.raw_data) == 6
    rendered = render_sub(sub)
    assert "RAW_Data:" in rendered
    cs8 = tmp_path / "x.cs8"
    sub_to_cs8(sub, sample_rate=2_000_000, out_path=cs8)
    assert cs8.stat().st_size > 0
    analyzed = analyze_cs8(cs8, sample_rate=2_000_000)
    assert analyzed.pulses.pulses_us


def test_de_bruijn_stream_file(tmp_path: Path) -> None:
    out = tmp_path / "db.cs8"
    result = synthesize_de_bruijn_stream(
        k=2, n=10, sample_rate=2_000_000, path=out
    )
    assert out.exists()
    assert result.num_samples > 0
