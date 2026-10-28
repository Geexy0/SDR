"""Explicit hardware bus states for the dual-mode USB mutex."""

from __future__ import annotations

from enum import Enum


class HardwareState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    MAYHEM_SERIAL = "MAYHEM_SERIAL"
    HACKRF_NATIVE = "HACKRF_NATIVE"
    BUSY_TX = "BUSY_TX"
    BUSY_RX = "BUSY_RX"
    MULTIPLEX_RX = "MULTIPLEX_RX"
    SWITCHING = "SWITCHING"

    @property
    def is_idle_mode(self) -> bool:
        return self in {HardwareState.MAYHEM_SERIAL, HardwareState.HACKRF_NATIVE}

    @property
    def is_busy(self) -> bool:
        return self in {
            HardwareState.BUSY_TX,
            HardwareState.BUSY_RX,
            HardwareState.MULTIPLEX_RX,
            HardwareState.SWITCHING,
        }
