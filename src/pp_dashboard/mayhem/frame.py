"""Decode Mayhem ``screenframeshort`` ASCII rows → RGBA8888 for HTML5 Canvas."""

from __future__ import annotations

from dataclasses import dataclass

# PortaPack classic ILI9341 panel (Mayhem ui::screen_width / height defaults).
DEFAULT_WIDTH = 240
DEFAULT_HEIGHT = 320


@dataclass(slots=True, frozen=True)
class ScreenFrame:
    width: int
    height: int
    rgba: bytes  # length = width * height * 4

    @property
    def pixel_count(self) -> int:
        return self.width * self.height


def _expand_2bit(v: int) -> int:
    """Map 2-bit channel 0..3 → 0..255."""
    return (v & 0x3) * 85


def decode_short_pixel(ch: int) -> tuple[int, int, int, int]:
    """Invert firmware getChrFromRgb: printable = pack(00RRGGBB) + 32."""
    packed = (ch - 32) & 0x3F
    r = _expand_2bit((packed >> 4) & 0x3)
    g = _expand_2bit((packed >> 2) & 0x3)
    b = _expand_2bit(packed & 0x3)
    return r, g, b, 255


def decode_screenframeshort(
    text: str,
    *,
    expect_width: int = DEFAULT_WIDTH,
) -> ScreenFrame:
    """Parse serial dump of ``screenframeshort`` into an RGBA frame.

    Each non-meta line is one row of printable ASCII pixels (typically 240 chars).
    """
    rows: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        # Do NOT str.strip() — spaces are valid black pixels in screenframeshort.
        line = raw.rstrip("\r\n")
        if line == "":
            continue
        low = line.strip().lower()
        if low in {"screenframeshort", "ok", "ch>", "ok."}:
            continue
        s = line
        if s.rstrip().endswith("ch>"):
            s = s.rstrip()[: -3]
            if s.endswith(" "):
                # rare prompt glued after pixels; keep pixel spaces
                pass
        if s == "":
            continue
        # Row of printable short pixels (space..underscore range mostly).
        if len(s) >= max(1, expect_width // 2) and all(32 <= ord(c) <= 126 for c in s):
            rows.append(s)

    if not rows:
        raise ValueError("No screenframeshort rows decoded")

    width = max(len(r) for r in rows)
    # Prefer canonical width when rows are consistent / padded.
    if all(len(r) == expect_width for r in rows):
        width = expect_width
    height = len(rows)
    out = bytearray(width * height * 4)
    i = 0
    for row in rows:
        if len(row) < width:
            row = row + (" " * (width - len(row)))
        elif len(row) > width:
            row = row[:width]
        for ch in row:
            r, g, b, a = decode_short_pixel(ord(ch))
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            out[i + 3] = a
            i += 4
    return ScreenFrame(width=width, height=height, rgba=bytes(out))
