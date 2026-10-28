"""Hardware bus arbiter, detection, and SDR subprocess management."""

from pp_dashboard.hardware.state import HardwareState

__all__ = ["HardwareState", "HardwareArbiter", "get_arbiter"]


def __getattr__(name: str):
    if name in {"HardwareArbiter", "get_arbiter"}:
        from pp_dashboard.hardware.arbiter import HardwareArbiter, get_arbiter

        return HardwareArbiter if name == "HardwareArbiter" else get_arbiter
    raise AttributeError(name)