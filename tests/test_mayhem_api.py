from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app
from pp_dashboard.mayhem.bridge import AppInfo, MayhemSerialBridge
from pp_dashboard.mayhem.frame import ScreenFrame


@pytest.mark.asyncio
async def test_mayhem_input_and_app_routes() -> None:
    HardwareArbiter.reset_instance_for_tests()
    MayhemSerialBridge.reset_instance_for_tests()
    app = create_app()
    bridge = MayhemSerialBridge()
    bridge.button = AsyncMock(return_value="ok")  # type: ignore[method-assign]
    bridge.touch = AsyncMock(return_value="ok")  # type: ignore[method-assign]
    bridge.keyboard_text = AsyncMock(return_value="ok")  # type: ignore[method-assign]
    bridge.applist = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            AppInfo("jammer", "Jammer", "Transmit"),
            AppInfo("replay", "Replay", "Transmit"),
            AppInfo("lookingglass", "Looking Glass", "Receive"),
        ]
    )
    bridge.appstart = AsyncMock(return_value="ok")  # type: ignore[method-assign]

    async def _dep() -> MayhemSerialBridge:
        return bridge

    from pp_dashboard.api.routes_mayhem import bridge_dep

    app.dependency_overrides[bridge_dep] = _dep
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/mayhem/input", json={"kind": "button", "button": 5})
        assert r1.status_code == 200
        assert r1.json()["button_label"] == "Select"

        r2 = await client.post("/api/mayhem/input", json={"kind": "touch", "x": 10, "y": 20})
        assert r2.status_code == 200

        r3 = await client.post(
            "/api/mayhem/input", json={"kind": "keyboard", "text": "Hi"}
        )
        assert r3.status_code == 200
        bridge.keyboard_text.assert_awaited()

        apps = await client.get("/api/mayhem/apps")
        assert apps.status_code == 200
        body = apps.json()
        assert "jammer" in body["featured"]

        start = await client.post("/api/mayhem/app", json={"app_id": "looking_glass"})
        assert start.status_code == 200
        assert start.json()["resolved_id"] == "lookingglass"


@pytest.mark.asyncio
async def test_home_mentions_mayhem_panel() -> None:
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        home = await client.get("/")
        assert home.status_code == 200
        # SPA shell (React Cyber-Deck) or legacy HTML console
        text = home.text
        assert ("id=\"root\"" in text) or ("mayhem-canvas" in text)
        assert ("/ws/mayhem-screen" in text) or ("text/html" in home.headers["content-type"])
