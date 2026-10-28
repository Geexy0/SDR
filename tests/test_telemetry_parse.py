import pytest

from pp_dashboard.hardware.telemetry_parse import parse_hackrf_line


def test_parse_mib_rate() -> None:
    line = "3.814697 MiB / 1.000000 sec = 3.814697 MiB/second"
    metrics = parse_hackrf_line(line)
    assert metrics is not None
    assert metrics["mib_per_sec"] == pytest.approx(3.814697)
    assert "mbps" in metrics


def test_parse_power_and_clips() -> None:
    metrics = parse_hackrf_line("avg power: -32.5 dBFS clips=12")
    assert metrics is not None
    assert metrics["avg_power_dbfs"] == pytest.approx(-32.5)
    assert metrics["clip_count"] == 12


def test_parse_empty() -> None:
    assert parse_hackrf_line("   ") is None
