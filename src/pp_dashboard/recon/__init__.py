"""Phase 5 — wideband waterfall, multi-protocol recon, active spoofing."""

from __future__ import annotations

__all__ = ["ReconEngine", "get_recon_engine"]


def __getattr__(name: str):
    if name in {"ReconEngine", "get_recon_engine"}:
        from pp_dashboard.recon.engine import ReconEngine, get_recon_engine

        return ReconEngine if name == "ReconEngine" else get_recon_engine
    raise AttributeError(name)
