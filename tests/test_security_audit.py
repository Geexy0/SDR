"""Regression tests for v0.5.0 Bugbot + AppSec audit fixes."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app
from pp_dashboard.models.rf import ContinuousTxRequest, ReplayRequest
from pp_dashboard.security import paths, tx_gate
from pp_dashboard.security.tx_gate import force_amp_off, tx_argv_amp


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


def test_resolve_safe_path_blocks_traversal(tmp_path: Path) -> None:
    base = tmp_path / "captures"
    base.mkdir(exist_ok=True)
    (base / "ok.cs8").write_bytes(b"\x00\x01")
    ok = paths.resolve_safe_path(base, "ok.cs8")
    assert ok == (base / "ok.cs8").resolve()
    with pytest.raises(ValueError, match="under"):
        paths.resolve_safe_path(base, "../etc/passwd")
    with pytest.raises(ValueError, match="under"):
        paths.resolve_safe_path(base, "/etc/passwd")


def test_resolve_under_any_blocks_shm_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shm = tmp_path / "shm"
    shm.mkdir()
    evil = tmp_path / "outside.cs8"
    evil.write_bytes(b"x")
    # Simulated /dev/shm/../outside via resolve containment
    with pytest.raises(ValueError):
        paths.resolve_under_any([shm], evil)


def test_tx_gate_fail_closed_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", tmp_path / "missing_scope.md")
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", tmp_path / "missing_confirm.md")
    with pytest.raises(tx_gate.TxGateError, match="missing"):
        tx_gate.assert_tx_allowed()


def test_tx_gate_fail_closed_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside_scope.md"
    outside.write_text("**Status:** `ACTIVE`\n", encoding="utf-8")
    link = tmp_path / "SCOPE.md"
    link.symlink_to(outside)
    # Point REPO_ROOT so symlink resolve is outside it
    monkeypatch.setattr(tx_gate, "REPO_ROOT", tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", link)
    confirm = tmp_path / "CONFIRM.md"
    confirm.write_text("**Status:** `CONFIRMED`\n\nENGAGEMENT: X\n", encoding="utf-8")
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    with pytest.raises(tx_gate.TxGateError, match="symlink escapes"):
        tx_gate.assert_tx_allowed()


def test_tx_argv_amp_hard_forces_zero() -> None:
    assert tx_argv_amp(0) == "0"
    with pytest.raises(tx_gate.TxGateError, match="AMP"):
        tx_argv_amp(1)
    assert force_amp_off(0) == 0


def test_hardware_tx_models_reject_amp_one(tmp_path: Path) -> None:
    iq = tmp_path / "t.cs8"
    iq.write_bytes(bytes([0, 0] * 16))
    with pytest.raises(ValidationError):
        ReplayRequest(
            filepath=iq,
            frequency_hz=433_920_000,
            sample_rate=2_000_000,
            tx_vga=10,
            amp=1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ContinuousTxRequest(
            frequency_hz=433_920_000,
            sample_rate=2_000_000,
            amplitude=64,
            tx_vga=10,
            amp=1,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_mayhem_tx_app_blocked_when_gate_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path, confirm_status="NOT CONFIRMED")
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/mayhem/app", json={"app_id": "jammer"})
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_hardware_replay_amp_one_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path)
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    HardwareArbiter.reset_instance_for_tests()
    iq = tmp_path / "r.cs8"
    iq.write_bytes(bytes([1, 0] * 64))
    # Point captures so ReplayRequest path validates
    from pp_dashboard import config as cfg

    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/hardware/replay",
            json={
                "filepath": str(iq),
                "frequency_hz": 433_920_000,
                "sample_rate": 2_000_000,
                "tx_vga": 10,
                "amp": 1,
                "loop": False,
                "max_duration_sec": 1,
            },
        )
        assert resp.status_code in {403, 422}


def test_sqlite_wal_pragma(tmp_path: Path) -> None:
    from pp_dashboard.subghz.db import PacketStore
    from pp_dashboard.tpms.store import TpmsFleetStore

    store = PacketStore(tmp_path / "packets.sqlite3")
    with store._connect() as conn:  # noqa: SLF001
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
    fleet = TpmsFleetStore(tmp_path / "fleet.sqlite3")
    with fleet._connect() as conn:  # noqa: SLF001
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"


def test_estimate_rms_dbfs_int8_and_epsilon(tmp_path: Path) -> None:
    from pp_dashboard.rf.chirp import estimate_rms_dbfs

    n = 8192
    amp = 64
    iq = np.zeros(n * 2, dtype=np.int8)
    iq[0::2] = amp
    path = tmp_path / "tone.cs8"
    path.write_bytes(iq.tobytes())
    dbfs = estimate_rms_dbfs(path)
    assert abs(dbfs - (-6.02)) < 0.25

    # Near-zero power must not yield NaN
    zpath = tmp_path / "zero.cs8"
    zpath.write_bytes(np.zeros(256, dtype=np.int8).tobytes())
    z = estimate_rms_dbfs(zpath)
    assert z == float("-inf") or np.isfinite(z)


@pytest.mark.asyncio
async def test_subprocess_runner_killpg_on_hung(isolate_hardware) -> None:
    """SIGINT → grace → SIGKILL via os.killpg (process group), offline fake pool."""
    import signal

    from pp_dashboard.config import Settings
    from pp_dashboard.hardware.state import HardwareState
    from pp_dashboard.hardware.subprocess_runner import SubprocessRunner

    isolate_hardware.hung_transfer = True
    runner = SubprocessRunner(cfg=Settings(sigkill_grace_s=0.1))
    await runner.start(
        ["hackrf_transfer", "-r", "-", "-f", "100000000", "-s", "2000000"],
        busy_state=HardwareState.BUSY_RX,
    )
    proc = isolate_hardware.processes[-1]
    assert proc.ignore_sigint
    code = await runner.stop(reason="audit-killpg")
    assert signal.SIGINT in proc.signals
    assert signal.SIGKILL in proc.signals
    assert code is not None
    assert not runner.running


@pytest.mark.asyncio
async def test_stop_does_not_require_ws_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP /api/hardware/stop remains the kill-switch (WS stop disabled)."""
    scope, confirm = _write_gate(tmp_path)
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/hardware/stop")
        assert resp.status_code == 200


def test_replay_loop_defaults_watchdog_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pp_dashboard import config as cfg

    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
    iq = tmp_path / "loop.cs8"
    iq.write_bytes(bytes([1, 0] * 32))
    req = ReplayRequest(
        filepath=iq,
        frequency_hz=433_920_000,
        sample_rate=2_000_000,
        tx_vga=10,
        amp=0,
        loop=True,
        max_duration_sec=None,
    )
    assert req.max_duration_sec is not None
    assert req.max_duration_sec <= 60.0
    req2 = ReplayRequest(
        filepath=iq,
        frequency_hz=433_920_000,
        sample_rate=2_000_000,
        tx_vga=10,
        amp=0,
        loop=True,
        max_duration_sec=120.0,
    )
    assert req2.max_duration_sec == 60.0


def test_require_loop_ok_enforces_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path)
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    with pytest.raises(tx_gate.TxGateError, match="max_duration"):
        tx_gate.assert_tx_allowed(require_loop_ok=True, max_duration_sec=None)
    with pytest.raises(tx_gate.TxGateError, match="<= 60"):
        tx_gate.assert_tx_allowed(require_loop_ok=True, max_duration_sec=90.0)
    tx_gate.assert_tx_allowed(require_loop_ok=True, max_duration_sec=30.0)


def test_bruteforce_shm_traversal_rejected() -> None:
    from pp_dashboard.models.subghz import BruteforceRequest

    with pytest.raises(ValidationError):
        BruteforceRequest(
            mode="framed",
            frequency_hz=433_920_000,
            sample_rate=2_000_000,
            tx_vga=10,
            amp=0,
            family="PT2262",
            nbits=8,
            key_start=0,
            key_end=2,
            out_path=Path("/dev/shm/../tmp/evil.cs8"),
        )


@pytest.mark.asyncio
async def test_stop_invokes_registered_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, confirm = _write_gate(tmp_path)
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    HardwareArbiter.reset_instance_for_tests()
    arbiter = await HardwareArbiter.get_instance()
    calls: list[str] = []

    async def _cb() -> None:
        calls.append("waterfall")

    arbiter.register_stop_callback("waterfall", _cb)
    arbiter.register_busy_check("waterfall", lambda: False)
    await arbiter.stop_subprocess(reason="audit")
    assert "waterfall" in calls


def test_verify_ws_origin_local_only() -> None:
    from pp_dashboard.api.ws_origin import verify_ws_origin

    class _FakeURL:
        hostname = "127.0.0.1"

    class _FakeWS:
        def __init__(self, origin: str | None) -> None:
            self.headers = {"origin": origin} if origin else {}
            self.url = _FakeURL()

    assert verify_ws_origin(_FakeWS(None)) is True  # type: ignore[arg-type]
    assert verify_ws_origin(_FakeWS("http://127.0.0.1:8765")) is True  # type: ignore[arg-type]
    assert verify_ws_origin(_FakeWS("http://localhost:8765")) is True  # type: ignore[arg-type]
    assert verify_ws_origin(_FakeWS("https://evil.example")) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_waterfall_attach_idempotent_subscribe() -> None:
    """Repeated attach must not stack duplicate handlers (subscribe dedup)."""
    from pp_dashboard.recon.waterfall import WaterfallSession

    class _Arb:
        pass

    session = WaterfallSession(
        arbiter=_Arb(),  # type: ignore[arg-type]
        start_mhz=400,
        stop_mhz=450,
        bin_width_hz=100_000,
        lna=16,
        vga=20,
        amp=0,
    )
    hits: list[int] = []

    async def handler(_payload: dict) -> None:
        hits.append(1)

    session.subscribe(handler)
    session.subscribe(handler)
    session.subscribe(handler)
    assert session._handlers.count(handler) == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_voice_path_outside_captures_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pp_dashboard import config as cfg
    from pp_dashboard.recon.engine import ReconEngine
    from pp_dashboard.security.paths import resolve_safe_path

    voice = tmp_path / "captures" / "voice"
    voice.mkdir(parents=True)
    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path / "captures")
    evil = tmp_path / "etc_passwd.wav"
    evil.write_bytes(b"RIFF")
    with pytest.raises(ValueError):
        resolve_safe_path(voice, evil)
