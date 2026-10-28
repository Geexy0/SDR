"""Bi-directional FREQMAN + field-capture sync over Mayhem serial."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from pp_dashboard.config import settings
from pp_dashboard.mayhem.bridge import MayhemBridgeError, MayhemSerialBridge
from pp_dashboard.mayhem.freqman import FreqmanStore, normalize_category

log = logging.getLogger(__name__)

_CAPT_NAME = re.compile(r"(?i)([\w./-]+\.(?:C16|SUB|c16|sub))\b")
_TXT_NAME = re.compile(r"(?i)([\w./-]+\.TXT)\b")


def freqman_store() -> FreqmanStore:
    return FreqmanStore(settings.captures_dir / "freqman")


async def sync_freqman_to_sd(
    bridge: MayhemSerialBridge,
    *,
    categories: list[str] | None = None,
    pull_captures: bool = True,
) -> dict[str, Any]:
    """Upload local NORMANDY/RECON (etc.) TXT → ``/FREQMAN/``, pull new .C16/.SUB."""
    store = freqman_store()
    cats = categories or ["RECON", "NORMANDY"]
    paths = store.sync_files(cats)
    uploaded: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file():
            continue
        data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        remote = f"/FREQMAN/{path.name.upper()}"
        try:
            await bridge.upload_bytes(remote, data)
            uploaded.append({"local": str(path), "remote": remote, "bytes": str(len(data))})
        except MayhemBridgeError as exc:
            log.warning("FREQMAN upload %s failed: %s", remote, exc)
            uploaded.append({"local": str(path), "remote": remote, "error": str(exc)})

    listing = ""
    try:
        listing = await bridge.command("ls /FREQMAN", wait=5.0)
    except MayhemBridgeError as exc:
        listing = f"(ls failed: {exc})"

    pulled: list[dict[str, str]] = []
    if pull_captures:
        pulled = await pull_field_captures(bridge)

    return {
        "ok": True,
        "uploaded": uploaded,
        "freqman_listing": listing,
        "pulled": pulled,
        "categories": [normalize_category(c) for c in cats],
    }


async def pull_field_captures(bridge: MayhemSerialBridge) -> list[dict[str, str]]:
    """Scan common SD dirs for .C16 / .SUB and copy new ones into captures/."""
    dest_root = settings.captures_dir
    dest_root.mkdir(parents=True, exist_ok=True)
    dirs = ("/", "/CAPTURES", "/captures", "/SUBGHZ", "/subghz", "/REC", "/recordings")
    found: list[str] = []
    for d in dirs:
        try:
            listing = await bridge.command(f"ls {d}", wait=5.0)
        except MayhemBridgeError:
            continue
        for m in _CAPT_NAME.finditer(listing):
            name = m.group(1)
            if "/" not in name and d not in (".", "/"):
                remote = f"{d.rstrip('/')}/{name}"
            elif name.startswith("/"):
                remote = name
            else:
                remote = f"/{name}" if d == "/" else f"{d.rstrip('/')}/{name}"
            if remote not in found:
                found.append(remote)

    pulled: list[dict[str, str]] = []
    for remote in found[:40]:
        local_name = Path(remote).name
        local = dest_root / local_name
        if local.is_file() and local.stat().st_size > 0:
            continue
        try:
            data = await bridge.download_bytes(remote, max_bytes=4_000_000)
        except MayhemBridgeError as exc:
            pulled.append({"remote": remote, "error": str(exc)})
            continue
        if not data:
            continue
        local.write_bytes(data)
        pulled.append({"remote": remote, "local": str(local), "bytes": str(len(data))})
        log.info("Pulled SD capture %s → %s (%s B)", remote, local, len(data))
    return pulled
