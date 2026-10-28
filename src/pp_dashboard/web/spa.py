"""Serve the Vite Cyber-Deck build from frontend/dist when present."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# src/pp_dashboard/web/spa.py → repo root is parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


def frontend_dist_ready() -> bool:
    return (FRONTEND_DIST / "index.html").is_file()


def mount_spa(app: FastAPI) -> bool:
    """Mount /assets + SPA fallback. Returns True if mounted."""
    if not frontend_dist_ready():
        return False

    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/deck")
    @app.get("/deck/")
    async def deck_alias() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")

    icons = FRONTEND_DIST / "icons.svg"
    if icons.is_file():

        @app.get("/icons.svg")
        async def icons_svg() -> FileResponse:
            return FileResponse(icons)

    fav = FRONTEND_DIST / "favicon.svg"
    if fav.is_file():

        @app.get("/app-favicon.svg")
        async def app_favicon() -> FileResponse:
            return FileResponse(fav)

    return True


def spa_index() -> FileResponse:
    if not frontend_dist_ready():
        raise HTTPException(status_code=503, detail="frontend/dist missing — run npm run build")
    return FileResponse(FRONTEND_DIST / "index.html")


def spa_static_file(rel: str) -> FileResponse | None:
    if not rel or ".." in rel:
        return None
    candidate = (FRONTEND_DIST / rel).resolve()
    try:
        candidate.relative_to(FRONTEND_DIST.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return FileResponse(candidate)
    return None
