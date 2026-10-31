"""High-signal unit coverage for detect + SPA static serving (offline)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.config import Settings
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.detect import (
    DetectionResult,
    detect_hardware,
    hackrf_info_ready,
    list_ttyacm_ports,
    lsusb_ids,
    resolve_mayhem_serial_port,
)
from pp_dashboard.main import create_app
from pp_dashboard.web.spa import frontend_dist_ready, mount_spa, spa_index, spa_static_file


@pytest.mark.asyncio
async def test_lsusb_ids_parses_hackrf(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _cap(cmd, timeout=2.0):
        return 0, "Bus 001 Device 003: ID 1d50:6089 Great Scott Gadgets HackRF One\n", ""

    monkeypatch.setattr("pp_dashboard.hardware.detect._run_capture", _cap)
    ids = await lsusb_ids()
    assert "1d50:6089" in ids


@pytest.mark.asyncio
async def test_lsusb_ids_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _cap(cmd, timeout=2.0):
        return 1, "", "boom"

    monkeypatch.setattr("pp_dashboard.hardware.detect._run_capture", _cap)
    assert await lsusb_ids() == ()


@pytest.mark.asyncio
async def test_hackrf_info_ready_true(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _cap(cmd, timeout=3.0):
        return 0, "hackrf_info version: fake\n", ""

    monkeypatch.setattr("pp_dashboard.hardware.detect._run_capture", _cap)
    assert await hackrf_info_ready() is True


@pytest.mark.asyncio
async def test_detect_hardware_native_via_usb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pp_dashboard.hardware.detect.resolve_mayhem_serial_port",
        lambda cfg=None: None,
    )
    monkeypatch.setattr(
        "pp_dashboard.hardware.detect.lsusb_ids",
        AsyncMock(return_value=("1d50:6089",)),
    )
    monkeypatch.setattr(
        "pp_dashboard.hardware.detect.hackrf_info_ready",
        AsyncMock(return_value=True),
    )
    det = await detect_hardware()
    assert isinstance(det, DetectionResult)
    assert det.hackrf_native is True
    assert det.mayhem_serial is False


def test_resolve_mayhem_serial_from_ttyacm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "ttyACM9"
    fake.write_text("")
    monkeypatch.setattr(
        "pp_dashboard.hardware.detect.list_ttyacm_ports",
        lambda: [str(fake)],
    )
    original_is_dir = Path.is_dir

    def _is_dir(self: Path) -> bool:
        if str(self) == "/dev/serial/by-id":
            return False
        return original_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", _is_dir)
    assert resolve_mayhem_serial_port(Settings()) == str(fake)


def test_list_ttyacm_ports_filters_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pp_dashboard.hardware.detect.glob.glob",
        lambda pattern: ["/dev/ttyACM0", "/dev/ttyACM99_missing"],
    )
    monkeypatch.setattr(
        "pp_dashboard.hardware.detect.os.path.exists",
        lambda p: p.endswith("ACM0"),
    )
    assert list_ttyacm_ports() == ["/dev/ttyACM0"]


def test_spa_helpers_and_path_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>deck</html>", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "icons.svg").write_text("<svg/>", encoding="utf-8")
    monkeypatch.setattr("pp_dashboard.web.spa.FRONTEND_DIST", dist)
    assert frontend_dist_ready() is True
    from fastapi import FastAPI

    app = FastAPI()
    assert mount_spa(app) is True
    idx = spa_index()
    assert str(idx.path).endswith("index.html")
    assert spa_static_file("../etc/passwd") is None
    assert spa_static_file("icons.svg") is not None


@pytest.mark.asyncio
async def test_fastapi_serves_root_html() -> None:
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
        hz = await client.get("/healthz")
        assert hz.status_code == 200
        assert hz.json()["status"] == "ok"
