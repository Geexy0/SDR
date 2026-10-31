"""Discover FastAPI HTTP + WebSocket routes (incl. FastAPI `_IncludedRouter`)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute


def collect_route_keys(app: FastAPI) -> set[str]:
    """Return ``{"GET /api/...", "WS /ws/..."}`` covering every registered route."""
    out: set[str] = set()
    for r in app.routes:
        _collect_one(r, out)
    return out


def _collect_one(route: object, out: set[str]) -> None:
    if isinstance(route, APIRoute):
        for method in route.methods or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.add(f"{method} {route.path}")
        return
    if isinstance(route, APIWebSocketRoute):
        out.add(f"WS {route.path}")
        return
    # FastAPI ≥0.115 packs included routers as `_IncludedRouter`
    original = getattr(route, "original_router", None)
    if original is not None:
        for child in getattr(original, "routes", []) or []:
            _collect_one(child, out)
        return
    nested = getattr(route, "routes", None)
    if nested:
        for child in nested:
            _collect_one(child, out)
