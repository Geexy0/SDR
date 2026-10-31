"""Layer 1 — dynamic FastAPI route discovery + TX gate / AMP matrix."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app
from pp_dashboard.security import tx_gate
from tests.fakes import FakeSubprocessPool
from tests.route_catalog import collect_route_keys

# ---------------------------------------------------------------------------
# Explicit classification — every app route MUST appear in exactly one set.
# Adding a new route without updating these sets fails the suite.
# ---------------------------------------------------------------------------

EXPECTED_TX_GATED_ROUTES: frozenset[str] = frozenset(
    {
        "POST /api/hardware/replay",
        "POST /api/hardware/tx/continuous",
        "POST /api/tx/jammer",
        "POST /api/tx/synthesize",
        "POST /api/rf/replay",
        "POST /api/rf/rolljam",
        "POST /api/subghz/bruteforce",
        "POST /api/subghz/replay",
        # Mayhem TX-capable apps are gated inside the handler; route is TX-gated
        # when body.app_id is a TX pack (tested via jammer/replay-class apps).
        "POST /api/mayhem/app",
    }
)

EXPECTED_RX_OR_CONTROL_ROUTES: frozenset[str] = frozenset(
    {
        # Meta / SPA / legacy
        "GET /",
        "GET /legacy",
        "GET /healthz",
        "GET /favicon.svg",
        "GET /favicon.ico",
        "GET /icons.svg",
        "GET /app-favicon.svg",
        "GET /deck",
        "GET /deck/",
        # Hardware control / RX
        "GET /api/hardware/status",
        "POST /api/hardware/detect",
        "POST /api/hardware/mode",
        "POST /api/hardware/stop",
        "POST /api/hardware/capture",
        "POST /api/hardware/sweep",
        "POST /api/hardware/multiplex",
        "POST /api/hardware/multiplex/stop",
        # RF RX capture
        "POST /api/rf/capture",
        # Mayhem RX / UI
        "GET /api/mayhem/apps",
        "POST /api/mayhem/input",
        "GET /api/mayhem/freqman/bookmarks",
        "POST /api/mayhem/freqman/bookmark",
        "POST /api/mayhem/freqman/sync",
        # Sub-GHz RX / analyze
        "POST /api/subghz/sniff",
        "POST /api/subghz/sniff/stop",
        "GET /api/subghz/packets",
        "POST /api/subghz/analyze",
        "POST /api/subghz/workbench/demod",
        "POST /api/subghz/bruteforce/stop",
        "POST /api/subghz/sub-file",
        # TPMS
        "GET /api/tpms/status",
        "POST /api/tpms/listen",
        "POST /api/tpms/listen/stop",
        "GET /api/tpms/sensors",
        "GET /api/tpms/passages",
        "GET /api/tpms/vehicles",
        "POST /api/tpms/vehicles",
        "POST /api/tpms/link",
        "POST /api/tpms/clear",
        # Recon RX (sweep trigger_action=tx_tone is gated inside handler)
        "GET /api/recon/status",
        "POST /api/recon/sweep",
        "POST /api/recon/sweep/stop",
        "POST /api/recon/decoder",
        "POST /api/recon/decoder/stop",
        "POST /api/recon/voice",
        "POST /api/recon/voice/stop",
        "GET /api/recon/recordings",
        "GET /api/recon/recordings/{recording_id}",
        "GET /api/recon/recordings/{recording_id}/audio",
        "GET /api/recon/mission/templates",
        "GET /api/recon/mission",
        "POST /api/recon/mission",
        "POST /api/recon/mission/stop",
        "GET /api/recon/radiosonde/tracks",
        "POST /api/recon/radiosonde/inject",
        # Geoloc (RX / analysis)
        "GET /api/geoloc/status",
        "POST /api/geoloc/operator",
        "POST /api/geoloc/breadcrumb",
        "GET /api/geoloc/breadcrumbs",
        "GET /api/geoloc/heatmap",
        "POST /api/geoloc/lob",
        "GET /api/geoloc/lobs",
        "DELETE /api/geoloc/lobs",
        "POST /api/geoloc/lob/intersect",
        "POST /api/geoloc/lob/solve",
        "POST /api/geoloc/rssi-grid/solve",
        "POST /api/geoloc/solve",
        "POST /api/geoloc/cpa",
        "GET /api/geoloc/range-rings",
        "POST /api/geoloc/foxhunt",
        "POST /api/geoloc/polar/peak",
        "POST /api/geoloc/gnss/l1-sample",
        "POST /api/geoloc/gnss/l1-analyze",
        "GET /api/geoloc/gnss/samples",
        "POST /api/geoloc/look-angle",
        # WebSockets
        "WS /ws/telemetry",
        "WS /ws/waterfall",
        "WS /ws/subghz",
        "WS /ws/audio",
        "WS /ws/mayhem-screen",
    }
)

def _tx_bodies(iq_path: Path) -> dict[str, dict]:
    """Minimal JSON bodies that pass Pydantic so the TX gate is reached."""
    fp = str(iq_path)
    return {
        "POST /api/hardware/replay": {
            "filepath": fp,
            "frequency_hz": 433_920_000,
            "sample_rate": 2_000_000,
            "tx_vga": 20,
            "amp": 0,
            "loop": False,
            "max_duration_sec": 5,
        },
        "POST /api/hardware/tx/continuous": {
            "frequency_hz": 433_920_000,
            "sample_rate": 2_000_000,
            "tx_vga": 10,
            "amp": 0,
            "amplitude": 64,
            "max_duration_sec": 2,
        },
        "POST /api/tx/jammer": {
            "frequency_hz": 433_920_000,
            "sample_rate": 2_000_000,
            "tx_vga": 10,
            "amp": 0,
            "mode": "CW_AWGN",
            "max_duration_sec": 2,
        },
        "POST /api/tx/synthesize": {
            "kind": "lrs_pager",
            "frequency_hz": 466_075_000,
            "tx_vga": 10,
            "amp": 0,
            "max_duration_sec": 2,
            "pager_id": 7,
            "pin": "1234",
        },
        "POST /api/rf/replay": {
            "filepath": fp,
            "frequency_hz": 433_920_000,
            "sample_rate": 2_000_000,
            "tx_vga": 10,
            "amp": 0,
            "loop": False,
            "max_duration_sec": 5,
        },
        "POST /api/rf/rolljam": {
            "phase": "CAPTURE_THEN_REPLAY",
            "frequency_hz": 433_920_000,
            "sample_rate": 2_000_000,
            "lna": 24,
            "vga": 20,
            "num_samples": 1000,
            "tx_vga": 10,
            "amp": 0,
            "replay_max_duration_sec": 2,
        },
        "POST /api/subghz/bruteforce": {
            "family": "PT2262",
            "frequency_hz": 433_920_000,
            "sample_rate": 2_000_000,
            "tx_vga": 10,
            "amp": 0,
            "mode": "framed",
            "max_duration_sec": 2,
        },
        "POST /api/subghz/replay": {
            "packet_id": 1,
            "tx_vga": 10,
            "amp": 0,
            "max_duration_sec": 2,
        },
        "POST /api/mayhem/app": {"app_id": "jammer"},
    }


def _write_gate(
    tmp_path: Path,
    *,
    confirm_status: str = "NOT CONFIRMED",
    scope_status: str = "ACTIVE",
) -> tuple[Path, Path]:
    scope = tmp_path / "SCOPE.md"
    confirm = tmp_path / "CONFIRM.md"
    scope.write_text(
        f"**Status:** `{scope_status}`\n\n"
        "**Engagement name:** `PP-LAB-RF-2026`\n\n"
        "**Start / end dates:** `2026-10-27 → 2027-10-27`\n",
        encoding="utf-8",
    )
    confirm.write_text(
        f"**Status:** `{confirm_status}`\n\nENGAGEMENT: PP-LAB-RF-2026\n"
        "Engagement window: 2026-10-27 → 2027-10-27\n",
        encoding="utf-8",
    )
    return scope, confirm


class TestRouteClassification:
    def test_union_covers_every_registered_route(self) -> None:
        app = create_app()
        discovered = collect_route_keys(app)
        classified = EXPECTED_TX_GATED_ROUTES | EXPECTED_RX_OR_CONTROL_ROUTES
        missing = discovered - classified
        extra = classified - discovered
        assert not missing, (
            "Unclassified route(s) — add to EXPECTED_TX_GATED_ROUTES or "
            f"EXPECTED_RX_OR_CONTROL_ROUTES: {sorted(missing)}"
        )
        assert not extra, (
            "Stale classified route(s) no longer registered on app: "
            f"{sorted(extra)}"
        )
        overlap = EXPECTED_TX_GATED_ROUTES & EXPECTED_RX_OR_CONTROL_ROUTES
        assert not overlap, f"Route in both TX and RX sets: {sorted(overlap)}"


@pytest.mark.asyncio
async def test_tx_gate_lock_sweep_returns_403(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolate_hardware: FakeSubprocessPool,
) -> None:
    scope, confirm = _write_gate(tmp_path, confirm_status="NOT CONFIRMED")
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    from pp_dashboard.config import settings

    iq = settings.captures_dir / "gate_probe.cs8"
    iq.write_bytes(b"\x00\x00" * 64)
    bodies = _tx_bodies(iq)
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    assert isolate_hardware is not None
    n_before = len(isolate_hardware.started)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for key in sorted(EXPECTED_TX_GATED_ROUTES):
            method, path = key.split(" ", 1)
            assert method == "POST"
            body = bodies[key]
            res = await client.post(path, json=body)
            assert res.status_code == 403, (
                f"{key} expected 403 under NOT CONFIRMED, got {res.status_code}: "
                f"{res.text[:200]}"
            )
    assert len(isolate_hardware.started) == n_before, (
        "TX-gated routes must not spawn subprocesses when CONFIRM is locked "
        f"(spawned={isolate_hardware.started[n_before:]})"
    )


@pytest.mark.asyncio
async def test_amp_one_sweep_rejected_or_forced_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolate_hardware: FakeSubprocessPool,
) -> None:
    scope, confirm = _write_gate(tmp_path, confirm_status="CONFIRMED")
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)
    from pp_dashboard.config import settings

    iq = settings.captures_dir / "amp_probe.cs8"
    iq.write_bytes(b"\x00\x00" * 64)
    bodies = _tx_bodies(iq)
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    amp_routes = [
        k
        for k in EXPECTED_TX_GATED_ROUTES
        if k != "POST /api/mayhem/app"  # no amp field; gated amp=0 internally
    ]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for key in amp_routes:
            method, path = key.split(" ", 1)
            body = dict(bodies[key])
            body["amp"] = 1
            n_before = len(isolate_hardware.started)
            res = await client.post(path, json=body)
            assert res.status_code in {403, 422}, (
                f"{key} with amp=1 must be rejected (403/422), got {res.status_code}: "
                f"{res.text[:200]}"
            )
            # No new TX process may have been launched with -a 1
            for argv in isolate_hardware.started[n_before:]:
                if "-a" in argv:
                    idx = argv.index("-a")
                    assert argv[idx + 1] == "0", f"AMP forced off failed: {argv}"
