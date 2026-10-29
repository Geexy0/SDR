"""Local-origin gate for WebSocket upgrades (CSWSH hardening)."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import WebSocket

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def verify_ws_origin(websocket: WebSocket) -> bool:
    """Permit missing Origin (non-browser) or local dashboard origins only."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host in _LOCAL_HOSTS:
        return True
    # Same-host as the bound server (e.g. http://127.0.0.1:8765).
    server_host = (websocket.url.hostname or "").lower()
    if host and server_host and host == server_host:
        return True
    return False


async def accept_local_ws(websocket: WebSocket) -> bool:
    """Accept local WS; close with 1008 Policy Violation for foreign origins."""
    if not verify_ws_origin(websocket):
        await websocket.close(code=1008)
        return False
    await websocket.accept()
    return True
