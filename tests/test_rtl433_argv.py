"""HackRF rtl_433 argv constraints (sample rate + TPMS hop/gain)."""

from __future__ import annotations

from unittest.mock import MagicMock

from pp_dashboard.subghz.sniffer import (
    DEFAULT_HACKRF_SAMPLE_RATE,
    Rtl433Sniffer,
    snap_hackrf_sample_rate,
)
from pp_dashboard.tpms.engine import resolve_tpms_frequencies


def test_snap_rejects_invalid_2_5_msps() -> None:
    # 2.5 Msps is equidistant; snap prefers the lower valid rate (2e6).
    assert snap_hackrf_sample_rate(2_500_000) == 2_000_000
    assert snap_hackrf_sample_rate(2_000_000) == 2_000_000
    assert DEFAULT_HACKRF_SAMPLE_RATE == 2_000_000


def test_build_argv_hackrf_gains_autolevel_and_hop(tmp_path, monkeypatch) -> None:
    sniffer = Rtl433Sniffer(MagicMock(), MagicMock())
    monkeypatch.setattr(sniffer, "resolve_binary", lambda: "/bin/rtl_433")
    argv = sniffer.build_argv(
        frequency_hz=433_920_000,
        sample_rate=2_500_000,  # invalid → snapped
        hop_frequencies_hz=[433_920_000, 315_000_000],
        hop_interval_sec=15,
        gain="LNA=32,VGA=40,AMP=0",
        autolevel=True,
        protocol_ids=[60, 89],
    )
    assert argv[0] == "/bin/rtl_433"
    assert "-s" in argv
    assert argv[argv.index("-s") + 1] == "2000000"
    assert argv.count("-f") == 2
    assert "433920000" in argv and "315000000" in argv
    assert "-H" in argv
    assert "-g" in argv and argv[argv.index("-g") + 1] == "LNA=32,VGA=40,AMP=0"
    assert argv[argv.index("-Y") + 1] == "autolevel"
    assert argv.count("-F") >= 2  # json + log
    assert "noise:5" in argv
    assert argv.count("-R") == 2


def test_resolve_tpms_band_helpers() -> None:
    primary, hops = resolve_tpms_frequencies(
        band="hop_both", frequency_hz=433_920_000, hop_frequencies_hz=[]
    )
    assert primary == 433_920_000
    assert hops == [433_920_000, 315_000_000]
    primary, hops = resolve_tpms_frequencies(
        band="us315", frequency_hz=433_920_000, hop_frequencies_hz=[]
    )
    assert primary == 315_000_000
    assert hops == []
