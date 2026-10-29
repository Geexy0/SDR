"""Universal Digital Packet Workbench — offline DSP regression tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app
from pp_dashboard.subghz import workbench as wb


def _write_ook_nrz_cs8(
    path: Path,
    *,
    bits: str,
    baud: float = 2400.0,
    sample_rate: int = 200_000,
    amplitude: float = 80.0,
    freq_offset_hz: float = 25_000.0,
) -> Path:
    """Synthesize clean OOK NRZ (1=carrier on, 0=off) at known baud."""
    spb = int(round(sample_rate / baud))
    n = len(bits) * spb
    t = np.arange(n, dtype=np.float64) / sample_rate
    env = np.zeros(n, dtype=np.float64)
    for i, b in enumerate(bits):
        if b == "1":
            env[i * spb : (i + 1) * spb] = 1.0
    carrier = np.exp(2j * np.pi * freq_offset_hz * t)
    z = (amplitude / 127.0) * env * carrier
    out = np.empty(n * 2, dtype=np.int8)
    out[0::2] = np.clip(np.rint(z.real * 127.0), -128, 127).astype(np.int8)
    out[1::2] = np.clip(np.rint(z.imag * 127.0), -128, 127).astype(np.int8)
    path.write_bytes(out.tobytes())
    return path


def _packet_bits(*, sync: str = "0010110111010100", payload: str = "1100110010100101") -> str:
    # Alternating preamble + sync (0x2DD4) + 16-bit payload + CRC-8 placeholder bits
    preamble = "10" * 16
    return preamble + sync + payload


def test_estimate_baud_from_ook_transitions(tmp_path: Path) -> None:
    bits = _packet_bits()
    path = _write_ook_nrz_cs8(tmp_path / "ook.cs8", bits=bits, baud=2400.0)
    iq = wb.load_cs8(path)
    env = wb.am_envelope(iq)
    baud = wb.estimate_baud_fft(env, sample_rate=200_000)
    assert abs(baud - 2400.0) / 2400.0 < 0.15


def test_mm_timing_recovery_slices_nrz(tmp_path: Path) -> None:
    bits = "1010101010101010" + "0010110111010100" + "11001100"
    path = _write_ook_nrz_cs8(tmp_path / "nrz.cs8", bits=bits, baud=2000.0)
    iq = wb.load_cs8(path)
    result = wb.demodulate(
        iq,
        sample_rate=200_000,
        modulation="OOK",
        line_code="NRZ",
        baud_override=2000.0,
    )
    # Recovered bit string should contain the sync word
    assert "0010110111010100" in result.bits or "0010110111010100" in result.bits_inv


def test_manchester_ieee_roundtrip() -> None:
    data = "11001010"
    # IEEE 802.3: 0 → 01, 1 → 10
    man = "".join("10" if b == "1" else "01" for b in data)
    decoded = wb.decode_manchester(man, convention="ieee")
    assert decoded == data
    thomas = "".join("01" if b == "1" else "10" for b in data)
    assert wb.decode_manchester(thomas, convention="thomas") == data


def test_preamble_and_sync_extraction() -> None:
    sync = "0010110111010100"  # 0x2DD4
    payload = "1010010111001100"
    bits = ("10" * 20) + sync + payload + ("10" * 8)
    hits = wb.find_sync_packets(bits, sync_candidates=[sync], hamming_tol=1)
    assert len(hits) >= 1
    assert hits[0].sync_hex.upper() in {"0X2DD4", "0x2DD4".upper()}
    assert hits[0].payload_bits.startswith(payload[:8])


def test_column_entropy_classification() -> None:
    # Three aligned packets: static addr, incrementing counter, random CRC-ish
    rows = [
        bytes([0xAB, 0x01, 0x11]),
        bytes([0xAB, 0x02, 0x9C]),
        bytes([0xAB, 0x03, 0x55]),
        bytes([0xAB, 0x04, 0xE2]),
    ]
    classes = wb.classify_columns(rows)
    assert classes[0] == "static"
    assert classes[1] == "incrementing"
    assert classes[2] == "high_entropy"


def test_crc_reveng_crc8_match() -> None:
    payload = bytes([0x12, 0x34, 0x56])
    crc = wb.crc8(payload)
    frame = payload + bytes([crc])
    matches = wb.crc_reveng(frame, payload_len=3)
    names = {m["name"] for m in matches if m.get("ok")}
    assert "CRC-8" in names


@pytest.mark.asyncio
async def test_workbench_demod_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pp_dashboard import config as cfg

    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
    bits = _packet_bits()
    path = _write_ook_nrz_cs8(tmp_path / "cap.cs8", bits=bits, baud=2400.0)
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/subghz/workbench/demod",
            json={
                "filepath": str(path),
                "sample_rate": 200_000,
                "modulation": "OOK",
                "line_code": "NRZ",
                "baud_override": 2400.0,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "baud_est" in body
        assert "packets" in body
        assert "column_classes" in body
        assert abs(body["baud_est"] - 2400.0) / 2400.0 < 0.2
