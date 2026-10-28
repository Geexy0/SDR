"""Mayhem serial input + app launcher HTTP API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from pp_dashboard.mayhem.apps import FEATURED_APPS, resolve_featured_short_name
from pp_dashboard.mayhem.bridge import (
    BUTTON_MAP,
    MayhemBridgeError,
    MayhemSerialBridge,
    get_mayhem_bridge,
)
from pp_dashboard.mayhem.freqman_sync import freqman_store, sync_freqman_to_sd
from pp_dashboard.models.mayhem import (
    FreqmanBookmarkRequest,
    FreqmanSyncRequest,
    MayhemAppInfo,
    MayhemAppListResponse,
    MayhemAppStartRequest,
    MayhemAppStartResponse,
    MayhemInputKind,
    MayhemInputRequest,
    MayhemInputResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mayhem", tags=["mayhem"])


async def bridge_dep() -> MayhemSerialBridge:
    return await get_mayhem_bridge()


@router.post("/input", response_model=MayhemInputResponse)
async def mayhem_input(
    body: MayhemInputRequest,
    bridge: MayhemSerialBridge = Depends(bridge_dep),
) -> MayhemInputResponse:
    try:
        if body.kind is MayhemInputKind.BUTTON:
            assert body.button is not None
            resp = await bridge.button(body.button)
            return MayhemInputResponse(
                kind=body.kind,
                response=resp,
                button_label=BUTTON_MAP[body.button],
            )
        if body.kind is MayhemInputKind.TOUCH:
            assert body.x is not None and body.y is not None
            resp = await bridge.touch(body.x, body.y)
            return MayhemInputResponse(kind=body.kind, response=resp)
        if body.hex:
            resp = await bridge.keyboard_hex(body.hex)
        else:
            assert body.text is not None
            resp = await bridge.keyboard_text(body.text)
        return MayhemInputResponse(kind=body.kind, response=resp)
    except MayhemBridgeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception:
        log.exception("Mayhem input failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Mayhem input failed",
        ) from None


@router.get("/apps", response_model=MayhemAppListResponse)
async def list_apps(bridge: MayhemSerialBridge = Depends(bridge_dep)) -> MayhemAppListResponse:
    try:
        apps = await bridge.applist()
    except MayhemBridgeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("applist failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="applist failed",
        ) from None

    featured_names = {f.short_name.lower() for f in FEATURED_APPS}
    for f in FEATURED_APPS:
        featured_names.update(a.lower() for a in f.aliases)
    out = [
        MayhemAppInfo(
            short_name=a.short_name,
            full_name=a.full_name,
            category=a.category,
            featured=a.short_name.lower() in featured_names
            or any(a.short_name.lower() == al.lower() for feat in FEATURED_APPS for al in feat.aliases),
        )
        for a in apps
    ]
    # Ensure featured stubs appear even if firmware omitted them from this boot's list.
    present = {a.short_name.lower() for a in out}
    for feat in FEATURED_APPS:
        if feat.short_name.lower() not in present and not any(
            al.lower() in present for al in feat.aliases
        ):
            out.append(
                MayhemAppInfo(
                    short_name=feat.short_name,
                    full_name=feat.label,
                    category=feat.category,
                    featured=True,
                )
            )
    return MayhemAppListResponse(
        apps=out,
        featured=[f.short_name for f in FEATURED_APPS],
    )


@router.post("/app", response_model=MayhemAppStartResponse)
async def start_app(
    body: MayhemAppStartRequest,
    bridge: MayhemSerialBridge = Depends(bridge_dep),
) -> MayhemAppStartResponse:
    try:
        apps = await bridge.applist()
        shorts = {a.short_name.lower() for a in apps}
        resolved = resolve_featured_short_name(body.app_id, shorts)
        resp = await bridge.appstart(resolved)
        return MayhemAppStartResponse(app_id=body.app_id, resolved_id=resolved, response=resp)
    except MayhemBridgeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception:
        log.exception("appstart failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="appstart failed",
        ) from None


@router.post("/freqman/bookmark")
async def freqman_bookmark(body: FreqmanBookmarkRequest) -> dict:
    try:
        store = freqman_store()
        result = store.bookmark(
            body.frequency_hz,
            body.description,
            category=body.category,
            modulation=body.modulation,
            bandwidth=body.bandwidth,
            source=body.source,
        )
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/freqman/bookmarks")
async def freqman_bookmarks(category: str | None = None) -> dict:
    store = freqman_store()
    return {"bookmarks": store.list_bookmarks(category)}


@router.post("/freqman/sync")
async def freqman_sync(
    body: FreqmanSyncRequest = FreqmanSyncRequest(),
    bridge: MayhemSerialBridge = Depends(bridge_dep),
) -> dict:
    try:
        return await sync_freqman_to_sd(
            bridge,
            categories=body.categories,
            pull_captures=body.pull_captures,
        )
    except MayhemBridgeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("FREQMAN sync failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="FREQMAN sync failed",
        ) from None
