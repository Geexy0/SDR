"""FastAPI entrypoint for the PortaPack / HackRF control plane."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from fastapi import FastAPI

from pp_dashboard import __version__
from pp_dashboard.api.routes_hardware import router as hardware_router
from pp_dashboard.api.routes_mayhem import router as mayhem_router
from pp_dashboard.api.routes_recon import router as recon_router
from pp_dashboard.api.routes_rf import router as rf_router
from pp_dashboard.api.routes_subghz import router as subghz_router
from pp_dashboard.api.routes_tpms import router as tpms_router
from pp_dashboard.api.routes_tx import router as tx_router
from pp_dashboard.api.routes_tx_synth import router as tx_synth_router
from pp_dashboard.api.ws_mayhem_screen import router as mayhem_screen_router
from pp_dashboard.api.ws_subghz import router as subghz_ws_router
from pp_dashboard.api.ws_telemetry import router as telemetry_router
from pp_dashboard.api.ws_audio import router as audio_router
from pp_dashboard.api.ws_waterfall import router as waterfall_router
from pp_dashboard.config import settings
from pp_dashboard.hardware.arbiter import get_arbiter
from pp_dashboard.mayhem.bridge import close_mayhem_bridge
from pp_dashboard.web.home import router as web_router
from pp_dashboard.web.spa import mount_spa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("pp_dashboard")


async def _wire_sdr_busy_registry(arbiter: Any) -> None:
    """Register recon / Sub-GHz busy checks + stop callbacks on the arbiter."""
    from pp_dashboard.recon.engine import get_recon_engine
    from pp_dashboard.recon.scheduler import MissionScheduler
    from pp_dashboard.subghz.engine import get_subghz_engine

    recon = await get_recon_engine()
    subghz = await get_subghz_engine()
    mission = await MissionScheduler.get_instance(arbiter=arbiter)
    mission.wire_arbiter(arbiter)

    arbiter.register_busy_check(
        "waterfall",
        lambda: bool(recon.waterfall is not None and recon.waterfall.running),
    )
    arbiter.register_busy_check(
        "decoder",
        lambda: bool(recon.decoder is not None and recon.decoder.running),
    )
    arbiter.register_busy_check(
        "voice_recon",
        lambda: bool(recon.voice_recon is not None and recon.voice_recon.running),
    )
    arbiter.register_busy_check(
        "subghz",
        lambda: bool(getattr(subghz.sniffer, "running", False)),
    )

    async def _stop_waterfall() -> None:
        if recon.waterfall is not None and recon.waterfall.running:
            await recon.stop_sweep()

    async def _stop_decoder() -> None:
        if recon.decoder is not None and recon.decoder.running:
            await recon.stop_decoder()

    async def _stop_voice() -> None:
        if recon.voice_recon is not None and recon.voice_recon.running:
            await recon.stop_voice_recon()

    async def _stop_sniff() -> None:
        if getattr(subghz.sniffer, "running", False):
            await subghz.stop_sniff()

    arbiter.register_stop_callback("waterfall", _stop_waterfall)
    arbiter.register_stop_callback("decoder", _stop_decoder)
    arbiter.register_stop_callback("voice_recon", _stop_voice)
    arbiter.register_stop_callback("subghz", _stop_sniff)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    arbiter = await get_arbiter()
    await arbiter.refresh_detection()
    try:
        await _wire_sdr_busy_registry(arbiter)
    except Exception:
        log.exception("SDR busy registry wiring failed")
    log.info(
        "Hardware arbiter ready state=%s serial=%s",
        arbiter.state.value,
        arbiter.serial_port,
    )
    yield
    try:
        from pp_dashboard.recon.engine import ReconEngine

        if ReconEngine._instance is not None:  # noqa: SLF001
            eng = ReconEngine._instance
            if eng.waterfall and eng.waterfall.running:
                await eng.waterfall.stop(reason="shutdown")
            if eng.decoder and eng.decoder.running:
                await eng.decoder.stop()
            if eng.voice_recon and eng.voice_recon.running:
                await eng.voice_recon.stop()
    except Exception:
        log.exception("recon shutdown cleanup failed")
    try:
        from pp_dashboard.tpms.engine import TpmsEngine

        if TpmsEngine._instance is not None and TpmsEngine._instance.running:  # noqa: SLF001
            await TpmsEngine._instance.stop()  # noqa: SLF001
    except Exception:
        log.exception("tpms shutdown cleanup failed")
    await close_mayhem_bridge()
    await arbiter.stop_subprocess(reason="shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="PortaPack H4M / HackRF Pro Dashboard",
        version=__version__,
        description="Phase 1–5 + Voice WebAudio/CTCSS/VOX — Arbiter, RF, Mayhem, Sub-GHz, Recon",
        lifespan=lifespan,
    )
    app.include_router(hardware_router)
    app.include_router(tx_router)
    app.include_router(tx_synth_router)
    app.include_router(rf_router)
    app.include_router(mayhem_router)
    app.include_router(subghz_router)
    app.include_router(tpms_router)
    app.include_router(recon_router)
    app.include_router(telemetry_router)
    app.include_router(mayhem_screen_router)
    app.include_router(subghz_ws_router)
    app.include_router(waterfall_router)
    app.include_router(audio_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # Static Cyber-Deck assets before the HTML home route.
    spa_ok = mount_spa(app)
    app.include_router(web_router)
    if spa_ok:
        log.info("Serving Cyber-Deck SPA from frontend/dist at /")
    else:
        log.warning("frontend/dist missing — / serves legacy HTML only")

    return app


app = create_app()


def run() -> None:
    uvicorn.run(
        "pp_dashboard.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    run()
