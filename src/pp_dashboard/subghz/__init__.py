"""Sub-GHz sniffer, pulse analyzer, OOK bruteforce, .sub transcoder."""

from __future__ import annotations

__all__ = ["SubGhzEngine", "get_subghz_engine"]


def __getattr__(name: str):
    if name in {"SubGhzEngine", "get_subghz_engine"}:
        from pp_dashboard.subghz.engine import SubGhzEngine, get_subghz_engine

        return SubGhzEngine if name == "SubGhzEngine" else get_subghz_engine
    raise AttributeError(name)
