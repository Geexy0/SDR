"""SCOPE/CONFIRM TX gate — offline, fixture-driven."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app
from pp_dashboard.security import tx_gate


def _write_gate(
    tmp_path: Path,
    *,
    scope_status: str = "ACTIVE",
    confirm_status: str = "CONFIRMED",
    engagement: str = "PP-LAB-RF-2026",
    start: str = "2026-10-27",
    end: str = "2027-10-27",
) -> tuple[Path, Path]:
    scope = tmp_path / "SCOPE.md"
    confirm = tmp_path / "CONFIRM.md"
    scope.write_text(
        f"**Status:** `{scope_status}`\n\n"
        f"**Engagement name:** `{engagement}`\n\n"
        f"**Start / end dates:** `{start} → {end}`\n",
        encoding="utf-8",
    )
    confirm.write_text(
        f"**Status:** `{confirm_status}`\n\nENGAGEMENT: {engagement}\n"
        f"Engagement window: {start} → {end}\n",
        encoding="utf-8",
    )
    return scope, confirm


def test_tx_gate_requires_matching_engagement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path, engagement="ENG-A")
    confirm.write_text("**Status:** `CONFIRMED`\n\nENGAGEMENT: ENG-B\n", encoding="utf-8")
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    with pytest.raises(tx_gate.TxGateError, match="mismatch"):
        tx_gate.assert_tx_allowed()


def test_tx_gate_allows_pp_lab_when_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path)
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    tx_gate.assert_tx_allowed(target_id="T3", amp=0)


def test_tx_gate_blocks_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path, confirm_status="NOT CONFIRMED")
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    with pytest.raises(tx_gate.TxGateError, match="CONFIRMED"):
        tx_gate.assert_tx_allowed()


def test_tx_gate_blocks_expired_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path, start="2020-01-01", end="2020-12-31")
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    with pytest.raises(tx_gate.TxGateError, match="expired"):
        tx_gate.assert_tx_allowed(today=date(2026, 10, 28))


def test_tx_gate_blocks_target_outside_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path)
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    with pytest.raises(tx_gate.TxGateError, match="allow-list"):
        tx_gate.assert_tx_allowed(target_id="T99")


def test_tx_gate_rejects_amp_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path)
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    with pytest.raises(tx_gate.TxGateError, match="AMP"):
        tx_gate.assert_tx_allowed(amp=1)
    assert tx_gate.force_amp_off(0) == 0
    with pytest.raises(tx_gate.TxGateError, match="AMP"):
        tx_gate.force_amp_off(1)


@pytest.mark.asyncio
async def test_tx_endpoints_403_when_gate_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path, confirm_status="NOT CONFIRMED")
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path, body in (
            (
                "/api/tx/jammer",
                {
                    "frequency_hz": 433_920_000,
                    "sample_rate": 10_000_000,
                    "tx_vga": 10,
                    "amp": 0,
                    "mode": "CW_AWGN",
                    "max_duration_sec": 1,
                },
            ),
            (
                "/api/tx/synthesize",
                {
                    "kind": "lrs_pager",
                    "pager_id": 7,
                    "tx_vga": 10,
                    "amp": 0,
                    "max_duration_sec": 1,
                },
            ),
            (
                "/api/subghz/bruteforce",
                {
                    "mode": "framed",
                    "frequency_hz": 433_920_000,
                    "sample_rate": 2_000_000,
                    "tx_vga": 10,
                    "amp": 0,
                    "family": "PT2262",
                    "nbits": 8,
                    "key_start": 0,
                    "key_end": 3,
                    "max_duration_sec": 1,
                },
            ),
        ):
            resp = await client.post(path, json=body)
            assert resp.status_code == 403, path


@pytest.mark.asyncio
async def test_rx_endpoints_allowed_when_tx_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path, confirm_status="NOT CONFIRMED")
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        st = await client.get("/api/recon/status")
        assert st.status_code == 200
        tpms = await client.get("/api/tpms/status")
        assert tpms.status_code == 200
        # sniff may 409/503 without hardware path but must not be 403 TX-gate
        sniff = await client.post(
            "/api/subghz/sniff",
            json={
                "frequency_hz": 433_920_000,
                "sample_rate": 2_000_000,
                "max_duration_sec": 1,
            },
        )
        assert sniff.status_code != 403
