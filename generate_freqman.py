#!/usr/bin/env python3
"""Generate Mayhem FREQMAN .TXT scan lists (Mayhem v2.x string syntax)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Mayhem v2.4+ uses named modulation/bandwidth (not legacy m=0/1/2 integers).
MOD_AM = "AM"
MOD_NFM = "NFM"
MOD_WFM = "WFM"
# NFM index 0 historically mapped to first NFM BW; PMR446 needs 12.5 kHz.
BW_NFM_12K5 = "12k5"
MAX_DESC = 22


def trunc_desc(d: str, max_len: int = MAX_DESC) -> str:
    d = d.strip().replace(",", " ")
    return d[:max_len]


def entry(
    freq_hz: int,
    modulation: str | None = None,
    bandwidth: str | None = None,
    description: str = "",
) -> str:
    parts = [f"f={int(freq_hz)}"]
    if modulation:
        parts.append(f"m={modulation}")
        if bandwidth:
            parts.append(f"bw={bandwidth}")
    if description:
        parts.append(f"d={trunc_desc(description)}")
    return ",".join(parts)


def pmr446_channels() -> list[str]:
    """16 analog PMR446 channels: 446.00625 + (n-1)*12.5 kHz."""
    start = 446_006_250
    step = 12_500
    lines = []
    for i in range(16):
        freq = start + i * step
        lines.append(
            entry(freq, MOD_NFM, BW_NFM_12K5, f"PMR446 Ch {i + 1:02d}")
        )
    return lines


def build_normandy() -> str:
    lines: list[str] = [
        "# NORMANDY - local aviation / maritime / PMR / ham calling",
        "# Generated for PortaPack Mayhem FREQMAN (LF endings)",
        "",
        "# --- Local Aviation (AM) ---",
        entry(120_325_000, MOD_AM, None, "Caen ATIS Weather"),
        entry(118_625_000, MOD_AM, None, "Caen Tower TWR"),
        entry(134_525_000, MOD_AM, None, "Normandy Approach"),
        entry(121_500_000, MOD_AM, None, "Air Guard 121.5"),
        "",
        "# --- Maritime Safety (NFM) ---",
        entry(156_800_000, MOD_NFM, BW_NFM_12K5, "Marine VHF Ch16"),
        entry(156_300_000, MOD_NFM, BW_NFM_12K5, "Marine Search Ch06"),
        "",
        "# --- Analog PMR446 (16 ch, 12.5 kHz) ---",
    ]
    lines.extend(pmr446_channels())
    lines.extend(
        [
            "",
            "# --- Amateur 2m / 70cm calling ---",
            entry(145_500_000, MOD_NFM, BW_NFM_12K5, "2m FM Calling"),
            entry(433_500_000, MOD_NFM, BW_NFM_12K5, "70cm FM Calling"),
            "",
        ]
    )
    return "\n".join(lines)


def write_freqman(path: Path, content: str) -> None:
    # Uppercase basename convention; LF only
    path = path.with_name(path.name.upper())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.replace("\r\n", "\n").replace("\r", "\n")
    if not data.endswith("\n"):
        data += "\n"
    path.write_bytes(data.encode("ascii", errors="replace"))
    print(f"Wrote {path} ({path.stat().st_size} bytes, LF)")


def deploy_copy(local: Path, mount: Path) -> None:
    dest_dir = mount / "FREQMAN"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / local.name.upper()
    shutil.copy2(local, dest)
    print(f"Copied -> {dest}")


def deploy_serial(local: Path, remote: str, port: str) -> None:
    script = Path(__file__).resolve().parent / "mayhem_serial_ctl.py"
    cmd = [
        sys.executable,
        str(script),
        "--port",
        port,
        "--upload",
        str(local),
        "--remote",
        remote,
    ]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> int:
    p = argparse.ArgumentParser(description="Build Mayhem FREQMAN lists")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("freqman/NORMANDY.TXT"),
        help="Output path (basename forced uppercase)",
    )
    p.add_argument(
        "--deploy-mount",
        type=Path,
        help="Copy into <mount>/FREQMAN/ (USB SD Card mode)",
    )
    p.add_argument(
        "--deploy-serial",
        action="store_true",
        help="Upload via Mayhem serial fopen/fwrite",
    )
    p.add_argument(
        "--remote",
        default="/FREQMAN/NORMANDY.TXT",
        help="Remote path for --deploy-serial",
    )
    p.add_argument("--port", default="/dev/ttyACM0")
    args = p.parse_args()

    content = build_normandy()
    write_freqman(args.output, content)
    # Always keep a canonical copy under freqman/
    canonical = Path(__file__).resolve().parent / "freqman" / "NORMANDY.TXT"
    if args.output.resolve() != canonical.resolve():
        write_freqman(canonical, content)

    if args.deploy_mount:
        deploy_copy(args.output if args.output.exists() else canonical, args.deploy_mount)
    if args.deploy_serial:
        deploy_serial(
            args.output if args.output.exists() else canonical,
            args.remote,
            args.port,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
