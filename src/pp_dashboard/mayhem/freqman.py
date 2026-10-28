"""Mayhem FREQMAN .TXT formatting, local bookmark store, SD sync helpers."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

MAX_DESC = 22
MAX_ENTRIES = 140
VALID_CATEGORY = re.compile(r"^[A-Z0-9_]{1,24}$")

EntryKind = Literal["freq", "range"]


def trunc_desc(description: str, max_len: int = MAX_DESC) -> str:
    return description.strip().replace(",", " ")[:max_len]


def format_freq_entry(freq_hz: int, description: str = "", **extra: str) -> str:
    """Mayhem single-freq line: ``f=<hz>,d=<desc>`` (+ optional m/bw)."""
    parts = [f"f={int(freq_hz)}"]
    mod = extra.get("modulation") or extra.get("m")
    bw = extra.get("bandwidth") or extra.get("bw")
    if mod:
        parts.append(f"m={mod}")
        if bw:
            parts.append(f"bw={bw}")
    if description:
        parts.append(f"d={trunc_desc(description)}")
    return ",".join(parts)


def format_range_entry(
    start_hz: int,
    end_hz: int,
    description: str = "",
    *,
    modulation: str | None = None,
    bandwidth: str | None = None,
    step: str | None = None,
) -> str:
    """Mayhem range line: ``a=<hz>,b=<hz>,d=<desc>`` (+ optional m/bw/s)."""
    parts = [f"a={int(start_hz)}", f"b={int(end_hz)}"]
    if modulation:
        parts.append(f"m={modulation}")
    if bandwidth:
        parts.append(f"bw={bandwidth}")
    if step:
        parts.append(f"s={step}")
    if description:
        parts.append(f"d={trunc_desc(description)}")
    return ",".join(parts)


def parse_freqman_line(line: str) -> dict[str, Any] | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    fields: dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        fields[k.strip().lower()] = v.strip()
    if "f" in fields:
        try:
            freq = int(fields["f"])
        except ValueError:
            return None
        return {
            "kind": "freq",
            "freq_hz": freq,
            "description": fields.get("d", ""),
            "modulation": fields.get("m"),
            "bandwidth": fields.get("bw"),
            "raw": raw,
        }
    if "a" in fields and "b" in fields:
        try:
            a, b = int(fields["a"]), int(fields["b"])
        except ValueError:
            return None
        return {
            "kind": "range",
            "start_hz": a,
            "end_hz": b,
            "description": fields.get("d", ""),
            "modulation": fields.get("m"),
            "bandwidth": fields.get("bw"),
            "step": fields.get("s"),
            "raw": raw,
        }
    return None


def normalize_category(name: str) -> str:
    cat = name.strip().upper().replace(" ", "_").replace(".TXT", "")
    if not VALID_CATEGORY.match(cat):
        raise ValueError(f"invalid FREQMAN category: {name!r}")
    return cat


class FreqmanStore:
    """Local bookmarks under ``captures/freqman/`` + rendered ``*.TXT``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._db = self.root / "bookmarks.json"

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if not self._db.is_file():
            return {}
        try:
            data = json.loads(self._db.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, list[dict[str, Any]]] = {}
        for k, v in data.items():
            if isinstance(v, list):
                out[str(k)] = [x for x in v if isinstance(x, dict)]
        return out

    def _save(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self._db.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def category_path(self, category: str) -> Path:
        cat = normalize_category(category)
        return self.root / f"{cat}.TXT"

    def bookmark(
        self,
        freq_hz: int,
        description: str,
        *,
        category: str = "RECON",
        modulation: str | None = None,
        bandwidth: str | None = None,
        source: str = "dashboard",
    ) -> dict[str, Any]:
        cat = normalize_category(category)
        if not (1_000_000 <= int(freq_hz) <= 6_000_000_000):
            raise ValueError("freq_hz out of HackRF range")
        entry = {
            "kind": "freq",
            "freq_hz": int(freq_hz),
            "description": trunc_desc(description or f"{freq_hz / 1e6:.4f} MHz"),
            "modulation": modulation,
            "bandwidth": bandwidth,
            "source": source,
            "ts": time.time(),
        }
        data = self._load()
        rows = list(data.get(cat, []))
        # Replace same frequency
        rows = [r for r in rows if int(r.get("freq_hz") or 0) != entry["freq_hz"]]
        rows.insert(0, entry)
        data[cat] = rows[:MAX_ENTRIES]
        self._save(data)
        text = self.render_category(cat)
        path = self.category_path(cat)
        path.write_text(text + ("\n" if text and not text.endswith("\n") else ""), encoding="utf-8")
        return {"category": cat, "entry": entry, "path": str(path), "count": len(data[cat])}

    def list_bookmarks(self, category: str | None = None) -> dict[str, list[dict[str, Any]]]:
        data = self._load()
        if category:
            cat = normalize_category(category)
            return {cat: list(data.get(cat, []))}
        return {k: list(v) for k, v in data.items()}

    def render_category(self, category: str) -> str:
        cat = normalize_category(category)
        rows = self._load().get(cat, [])
        # Also merge any pre-seeded repo freqman file if local empty
        lines: list[str] = []
        for r in rows:
            if r.get("kind") == "range":
                lines.append(
                    format_range_entry(
                        int(r["start_hz"]),
                        int(r["end_hz"]),
                        str(r.get("description") or ""),
                        modulation=r.get("modulation"),
                        bandwidth=r.get("bandwidth"),
                        step=r.get("step"),
                    )
                )
            else:
                extra: dict[str, str] = {}
                if r.get("modulation"):
                    extra["m"] = str(r["modulation"])
                if r.get("bandwidth"):
                    extra["bw"] = str(r["bandwidth"])
                lines.append(
                    format_freq_entry(
                        int(r["freq_hz"]),
                        str(r.get("description") or ""),
                        **extra,
                    )
                )
        return "\n".join(lines)

    def sync_files(self, categories: list[str] | None = None) -> list[Path]:
        """Ensure ``CATEGORY.TXT`` exists for each requested / known category."""
        data = self._load()
        cats = [normalize_category(c) for c in (categories or list(data.keys()) or ["RECON"])]
        # Always include NORMANDY/RECON if seeded from repo
        paths: list[Path] = []
        for cat in cats:
            text = self.render_category(cat)
            path = self.category_path(cat)
            if not text:
                # Seed from repo freqman/ if present
                repo = Path(__file__).resolve().parents[3] / "freqman" / f"{cat}.TXT"
                if repo.is_file():
                    text = repo.read_text(encoding="utf-8", errors="replace")
                    path.write_text(text, encoding="utf-8")
                else:
                    path.write_text("", encoding="utf-8")
            else:
                path.write_text(text + "\n", encoding="utf-8")
            paths.append(path)
        # Always try to ship NORMANDY.TXT from repo when requested or default sync
        if categories is None or any(normalize_category(c) == "NORMANDY" for c in categories):
            repo_n = Path(__file__).resolve().parents[3] / "freqman" / "NORMANDY.TXT"
            dest = self.category_path("NORMANDY")
            if repo_n.is_file() and (not dest.is_file() or dest.stat().st_size == 0):
                dest.write_text(repo_n.read_text(encoding="utf-8"), encoding="utf-8")
            if dest.is_file() and dest not in paths:
                paths.append(dest)
        if categories is None:
            repo_r = Path(__file__).resolve().parents[3] / "freqman" / "RECON.TXT"
            dest = self.category_path("RECON")
            if repo_r.is_file() and dest not in paths:
                if not dest.is_file() or dest.stat().st_size == 0:
                    # Prefer bookmarks; else seed
                    if not self.render_category("RECON"):
                        dest.write_text(repo_r.read_text(encoding="utf-8"), encoding="utf-8")
                if dest.is_file() and dest not in paths:
                    paths.append(dest)
        return paths
