"""FastAPI dependencies."""

from __future__ import annotations

from pp_dashboard.hardware.arbiter import HardwareArbiter, get_arbiter


async def arbiter_dep() -> HardwareArbiter:
    return await get_arbiter()
