"""MULTIPLEX_RX API model + rtl_433 FIFO argv."""

from __future__ import annotations

from unittest.mock import MagicMock

from pp_dashboard.models.rf import MultiplexRxRequest
from pp_dashboard.subghz.sniffer import Rtl433Sniffer


def test_multiplex_request_defaults() -> None:
    req = MultiplexRxRequest()
    assert req.center_hz == 435_000_000
    assert req.sample_rate == 10_000_000
    assert req.amp == 0


def test_rtl433_fifo_argv(monkeypatch) -> None:
    sniffer = Rtl433Sniffer(arbiter=MagicMock(), store=MagicMock())
    monkeypatch.setattr(sniffer, "resolve_binary", lambda: "/usr/bin/rtl_433")
    argv = sniffer.build_fifo_argv(sample_rate=2_000_000)
    assert argv[0] == "/usr/bin/rtl_433"
    assert "cs8:/tmp/pp_subghz.cs8" in argv
    assert "-s" in argv
    assert "2000000" in argv
