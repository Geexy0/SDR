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
_LOOP_MAX_DURATION_SEC = 60.0


class TxGateError(PermissionError):
    pass


def _read_gate_file(path: Path) -> str:
    """Fail-closed read: missing, unreadable, or symlink escaping the repo → deny TX."""
    if path.is_symlink():
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise TxGateError(f"TX denied: {path.name} symlink is broken") from exc
        try:
            target.relative_to(REPO_ROOT.resolve())
        except ValueError as exc:
            raise TxGateError(
                f"TX denied: {path.name} symlink escapes repository"
            ) from exc
    if not path.is_file():
        raise TxGateError(f"TX denied: {path.name} missing or unreadable")
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except OSError as exc:
        raise TxGateError(f"TX denied: cannot read {path.name}") from exc


def _status_from_text(text: str) -> str | None:
    match = _STATUS_RE.search(text)
    return match.group("status").strip() if match else None


def _engagement_from_confirm_text(text: str) -> str | None:
    match = _ENGAGEMENT_RE.search(text)
    return match.group("name").strip() if match else None


def _engagement_from_scope_text(text: str) -> str | None:
    for line in text.splitlines():
        if "Engagement name:" in line or "**Engagement name:**" in line:
            tick = re.search(r"`([^`]+)`", line)
            if tick:
                return tick.group(1).strip()
    return None


def _window_from_text(text: str) -> tuple[date, date] | None:
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
    max_duration_sec: float | None = None,
) -> None:
    """Raise TxGateError unless SCOPE is ACTIVE and CONFIRM is CONFIRMED for same engagement.

    Re-reads SCOPE.md and CONFIRM.md on every call (fail-closed snapshot).
    When ``require_loop_ok`` is True (looped ``-R`` TX), ``max_duration_sec`` must be
    set and ``<= 60`` so the arbiter watchdog is always armed.
    """
    scope_text = _read_gate_file(SCOPE_PATH)
    confirm_text = _read_gate_file(CONFIRM_PATH)

    scope_status = _status_from_text(scope_text)
    confirm_status = _status_from_text(confirm_text)
    if scope_status != "ACTIVE":
        raise TxGateError("TX denied: SCOPE.md is not ACTIVE")
    if confirm_status != "CONFIRMED":
        raise TxGateError("TX denied: CONFIRM.md is not CONFIRMED")
    scope_eng = _engagement_from_scope_text(scope_text)
    confirm_eng = _engagement_from_confirm_text(confirm_text)
    if not scope_eng or not confirm_eng or scope_eng != confirm_eng:
        raise TxGateError(
            f"TX denied: engagement mismatch scope={scope_eng!r} confirm={confirm_eng!r}"
        )

    window = _window_from_text(scope_text) or _window_from_text(confirm_text)
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

    # AMP off hard rule unless SCOPE amends (default forbids AMP on).
    if int(amp) != 0:
        raise TxGateError("TX denied: AMP must be off (-a 0) under active SCOPE")

    if require_loop_ok:
        if max_duration_sec is None or float(max_duration_sec) <= 0:
            raise TxGateError(
                "TX denied: looped TX requires max_duration_sec watchdog (<= 60s)"
            )
        if float(max_duration_sec) > _LOOP_MAX_DURATION_SEC:
            raise TxGateError(
                f"TX denied: looped TX max_duration_sec must be <= {_LOOP_MAX_DURATION_SEC}"
            )


def force_amp_off(amp: int) -> int:
    """Policy helper — always return 0 for lab TX; reject non-zero."""
    if int(amp) != 0:
        raise TxGateError("TX denied: AMP must be off (-a 0) under active SCOPE")
    return 0


def tx_argv_amp(amp: int) -> str:
    """Hard-force ``-a 0`` for every TX argv assembly site."""
    return str(force_amp_off(amp))
