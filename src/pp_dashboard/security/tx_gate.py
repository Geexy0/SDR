"""Enforce PRODUCT_REMIT TX lane via SCOPE.md + CONFIRM.md."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCOPE_PATH = REPO_ROOT / "SCOPE.md"
CONFIRM_PATH = REPO_ROOT / "CONFIRM.md"

_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*`(?P<status>[^`]+)`", re.MULTILINE)
_ENGAGEMENT_RE = re.compile(r"ENGAGEMENT:\s*(?P<name>\S+)")
_WINDOW_RE = re.compile(
    r"(?:Start\s*/\s*end dates|Engagement window):\s*"
    r"`?(?P<start>\d{4}-\d{2}-\d{2})\s*[→\-–]+\s*(?P<end>\d{4}-\d{2}-\d{2})`?",
    re.IGNORECASE,
)
_ALLOWED_TARGETS = frozenset({f"T{i}" for i in range(1, 8)})


class TxGateError(PermissionError):
    pass


def _status_from(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _STATUS_RE.search(text)
    return match.group("status").strip() if match else None


def _engagement_from_confirm(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _ENGAGEMENT_RE.search(text)
    return match.group("name").strip() if match else None


def _engagement_from_scope(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if "Engagement name:" in line or "**Engagement name:**" in line:
            tick = re.search(r"`([^`]+)`", line)
            if tick:
                return tick.group(1).strip()
    return None


def _window_from(path: Path) -> tuple[date, date] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _WINDOW_RE.search(text)
    if not match:
        return None
    start = datetime.strptime(match.group("start"), "%Y-%m-%d").date()
    end = datetime.strptime(match.group("end"), "%Y-%m-%d").date()
    return start, end


def assert_tx_allowed(
    *,
    require_loop_ok: bool = False,
    target_id: str | None = None,
    amp: int = 0,
    today: date | None = None,
) -> None:
    """Raise TxGateError unless SCOPE is ACTIVE and CONFIRM is CONFIRMED for same engagement."""
    _ = require_loop_ok  # continuous loop still requires gate; hard jam forbidden by SCOPE text
    scope_status = _status_from(SCOPE_PATH)
    confirm_status = _status_from(CONFIRM_PATH)
    if scope_status != "ACTIVE":
        raise TxGateError("TX denied: SCOPE.md is not ACTIVE")
    if confirm_status != "CONFIRMED":
        raise TxGateError("TX denied: CONFIRM.md is not CONFIRMED")
    scope_eng = _engagement_from_scope(SCOPE_PATH)
    confirm_eng = _engagement_from_confirm(CONFIRM_PATH)
    if not scope_eng or not confirm_eng or scope_eng != confirm_eng:
        raise TxGateError(
            f"TX denied: engagement mismatch scope={scope_eng!r} confirm={confirm_eng!r}"
        )

    window = _window_from(SCOPE_PATH) or _window_from(CONFIRM_PATH)
    if window is not None:
        start, end = window
        day = today or date.today()
        if day < start or day > end:
            raise TxGateError(f"TX denied: engagement window expired ({start} → {end})")

    if target_id is not None:
        tid = target_id.strip().upper()
        if tid not in _ALLOWED_TARGETS:
            raise TxGateError(
                f"TX denied: target {target_id!r} outside allow-list T1–T7"
            )

    # PP-LAB-RF-2026: AMP off hard rule unless SCOPE amends (currently forbids AMP on).
    if int(amp) != 0:
        raise TxGateError("TX denied: AMP must be off (-a 0) under PP-LAB-RF-2026")


def force_amp_off(amp: int) -> int:
    """Policy helper — always return 0 for lab TX."""
    if int(amp) != 0:
        raise TxGateError("TX denied: AMP must be off (-a 0) under PP-LAB-RF-2026")
    return 0
