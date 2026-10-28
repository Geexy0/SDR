"""Mayhem FREQMAN entry formatting + bookmark store."""

from __future__ import annotations

from pathlib import Path

from pp_dashboard.mayhem.freqman import (
    FreqmanStore,
    format_freq_entry,
    format_range_entry,
    parse_freqman_line,
)


def test_format_freq_entry_strict() -> None:
    line = format_freq_entry(446_006_250, "PMR446 Ch01")
    assert line == "f=446006250,d=PMR446 Ch01"
    assert "," not in line.split("d=")[1]


def test_format_range_entry() -> None:
    line = format_range_entry(433_000_000, 435_000_000, "ISM 433 band")
    assert line.startswith("a=433000000,b=435000000,")
    assert "d=ISM 433 band" in line


def test_parse_roundtrip() -> None:
    line = format_freq_entry(121_500_000, "Air Guard")
    parsed = parse_freqman_line(line)
    assert parsed is not None
    assert parsed["kind"] == "freq"
    assert parsed["freq_hz"] == 121_500_000
    assert parsed["description"] == "Air Guard"


def test_bookmark_store_writes_category(tmp_path: Path) -> None:
    store = FreqmanStore(tmp_path)
    store.bookmark(433_920_000, "SubGHz click", category="RECON")
    store.bookmark(446_081_250, "Voice dwell", category="RECON")
    text = store.render_category("RECON")
    assert "f=433920000,d=SubGHz click" in text
    assert "f=446081250,d=Voice dwell" in text
    path = store.category_path("RECON")
    assert path.is_file()
    assert "f=433920000" in path.read_text(encoding="utf-8")
