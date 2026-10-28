#!/usr/bin/env python3
"""Generate Mayhem FREQMAN .TXT scan lists (Mayhem v2.x string syntax).

Receive-only recon lists. No jammer / attack / public-safety dispatch freqs.
Mayhem truncates files over ~150 entries — keep each list under that.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

MOD_AM = "AM"
MOD_NFM = "NFM"
MOD_WFM = "WFM"
BW_NFM_12K5 = "12k5"
BW_WFM_200K = "200k"
MAX_DESC = 22
MAX_ENTRIES = 140  # leave headroom under Mayhem ~150 cap


def trunc_desc(d: str, max_len: int = MAX_DESC) -> str:
    return d.strip().replace(",", " ")[:max_len]


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


def range_entry(
    start_hz: int,
    end_hz: int,
    modulation: str,
    bandwidth: str,
    step: str,
    description: str,
) -> str:
    return (
        f"a={int(start_hz)},b={int(end_hz)},m={modulation},"
        f"bw={bandwidth},s={step},d={trunc_desc(description)}"
    )


def pmr446_channels() -> list[str]:
    start, step = 446_006_250, 12_500
    return [
        entry(start + i * step, MOD_NFM, BW_NFM_12K5, f"PMR446 Ch {i + 1:02d}")
        for i in range(16)
    ]


def lpd433_channels(limit: int = 20) -> list[str]:
    """EU LPD433: 433.075 + (n-1)*25 kHz, ch 1..69 — keep a useful subset."""
    start, step = 433_075_000, 25_000
    # Common mid-band channels used by remotes / sensors
    picks = [1, 2, 3, 4, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 69]
    lines = []
    for n in picks[:limit]:
        freq = start + (n - 1) * step
        lines.append(entry(freq, MOD_NFM, BW_NFM_12K5, f"LPD433 Ch {n:02d}"))
    return lines


def marine_vhf() -> list[str]:
    """ITU marine VHF (simplex) — safety + common working channels."""
    # ch: MHz
    chans = [
        (16, 156_800_000, "Marine Distress 16"),
        (6, 156_300_000, "Marine SAR Ch06"),
        (8, 156_400_000, "Marine Ch08"),
        (9, 156_450_000, "Marine Pilot Ch09"),
        (10, 156_500_000, "Marine Ch10"),
        (11, 156_550_000, "Marine Port Ch11"),
        (12, 156_600_000, "Marine Port Ch12"),
        (13, 156_650_000, "Marine Bridge Ch13"),
        (14, 156_700_000, "Marine Port Ch14"),
        (67, 156_375_000, "Marine Ch67"),
        (68, 156_425_000, "Marine Ch68"),
        (69, 156_475_000, "Marine Ch69"),
        (71, 156_575_000, "Marine Ch71"),
        (72, 156_625_000, "Marine Ch72"),
        (73, 156_675_000, "Marine Ch73"),
        (77, 156_875_000, "Marine Ch77"),
        (15, 156_750_000, "Marine Ch15"),
        (17, 156_850_000, "Marine Ch17"),
    ]
    return [entry(hz, MOD_NFM, BW_NFM_12K5, desc) for _, hz, desc in chans]


def aviation_normandy() -> list[str]:
    """Public AIP / known civil ATS (RX only). Prefer current SIA where known."""
    return [
        # Guard + emergency
        entry(121_500_000, MOD_AM, None, "Air Guard 121.5"),
        entry(123_100_000, MOD_AM, None, "SAR 123.1"),
        # Caen Carpiquet LFRK (SIA AMDT 2026)
        entry(123_080_000, MOD_AM, None, "Caen ATIS 123.08"),
        entry(134_530_000, MOD_AM, None, "Caen TWR 134.53"),
        entry(121_790_000, MOD_AM, None, "Caen GND 121.79"),
        # Deauville LFRG
        entry(119_180_000, MOD_AM, None, "Deauv ATIS 119.18"),
        entry(118_305_000, MOD_AM, None, "Deauv TWR 118.30"),
        # Rennes APP covering Normandy coast
        entry(120_350_000, MOD_AM, None, "Rennes APP 120.35"),
        entry(134_200_000, MOD_AM, None, "Rennes APP 134.2"),
        entry(126_950_000, MOD_AM, None, "Rennes INFO 126.95"),
        # Regional (commonly published; verify AIP if critical)
        entry(119_625_000, MOD_AM, None, "Cherbourg INFO"),
        entry(135_205_000, MOD_AM, None, "Le Havre INFO"),
        entry(120_200_000, MOD_AM, None, "Rouen TWR 120.2"),
        entry(118_575_000, MOD_AM, None, "Rouen APP 118.57"),
        entry(125_700_000, MOD_AM, None, "Paris INFO 125.7"),
        # Legacy labels from older lists (still sometimes heard)
        entry(120_325_000, MOD_AM, None, "Legacy Caen ATIS"),
        entry(118_625_000, MOD_AM, None, "Legacy Caen TWR"),
        entry(134_525_000, MOD_AM, None, "Legacy NDY APP"),
    ]


def ham_fr() -> list[str]:
    """FR amateur calling + common simplex / plan repeater outputs (RX)."""
    lines = [
        entry(144_800_000, MOD_NFM, BW_NFM_12K5, "2m APRS 144.8"),
        entry(145_225_000, MOD_NFM, BW_NFM_12K5, "2m Simplex 225"),
        entry(145_500_000, MOD_NFM, BW_NFM_12K5, "2m FM Calling"),
        entry(145_550_000, MOD_NFM, BW_NFM_12K5, "2m Simplex 550"),
        entry(145_575_000, MOD_NFM, BW_NFM_12K5, "2m Simplex 575"),
        # Standard EU/FR 2m repeater outputs 145.600–145.7875 / 12.5 kHz
    ]
    for i, hz in enumerate(range(145_600_000, 145_800_000, 12_500)):
        if i >= 16:
            break
        lines.append(entry(hz, MOD_NFM, BW_NFM_12K5, f"2m RPT out {i+1:02d}"))
    lines.extend(
        [
            entry(430_025_000, MOD_NFM, BW_NFM_12K5, "70cm RPT low"),
            entry(433_400_000, MOD_NFM, BW_NFM_12K5, "70cm Simplex"),
            entry(433_500_000, MOD_NFM, BW_NFM_12K5, "70cm FM Calling"),
            entry(433_550_000, MOD_NFM, BW_NFM_12K5, "70cm Simplex 550"),
            entry(433_575_000, MOD_NFM, BW_NFM_12K5, "70cm Simplex 575"),
            entry(439_000_000, MOD_NFM, BW_NFM_12K5, "70cm RPT region"),
            entry(439_100_000, MOD_NFM, BW_NFM_12K5, "70cm RPT region"),
            entry(439_200_000, MOD_NFM, BW_NFM_12K5, "70cm RPT region"),
            entry(439_300_000, MOD_NFM, BW_NFM_12K5, "70cm RPT region"),
            entry(439_400_000, MOD_NFM, BW_NFM_12K5, "70cm RPT region"),
            entry(439_500_000, MOD_NFM, BW_NFM_12K5, "70cm RPT region"),
        ]
    )
    return lines


def ism_sensors() -> list[str]:
    return [
        entry(433_920_000, MOD_NFM, BW_NFM_12K5, "ISM 433.92 OOK"),
        entry(868_300_000, MOD_NFM, BW_NFM_12K5, "ISM 868.3 EU"),
        entry(868_950_000, MOD_NFM, BW_NFM_12K5, "ISM 868.95"),
        entry(869_525_000, MOD_NFM, BW_NFM_12K5, "ISM 869.525"),
        entry(915_000_000, MOD_NFM, BW_NFM_12K5, "ISM 915 US-ish"),
        entry(315_000_000, MOD_NFM, BW_NFM_12K5, "Keyfob 315"),
        entry(318_000_000, MOD_NFM, BW_NFM_12K5, "Keyfob 318"),
        entry(390_000_000, MOD_NFM, BW_NFM_12K5, "Keyfob 390"),
        entry(433_420_000, MOD_NFM, BW_NFM_12K5, "Keyfob 433.42"),
        entry(434_420_000, MOD_NFM, BW_NFM_12K5, "Keyfob 434.42"),
    ]


def broadcast_ndy() -> list[str]:
    """Broadcast FM — search range + a few known service centers (WFM)."""
    return [
        range_entry(
            87_500_000,
            108_000_000,
            MOD_WFM,
            BW_WFM_200K,
            "100kHz",
            "FM Broadcast FR",
        ),
        entry(87_800_000, MOD_WFM, BW_WFM_200K, "FM low edge"),
        entry(95_000_000, MOD_WFM, BW_WFM_200K, "FM mid band"),
        entry(100_700_000, MOD_WFM, BW_WFM_200K, "FM mid-high"),
        entry(105_500_000, MOD_WFM, BW_WFM_200K, "FM high"),
        # Longwave / time (AM)
        entry(162_000, MOD_AM, None, "ALS162 time FR"),
        entry(198_000, MOD_AM, None, "LW BBC4-ish"),
    ]


def build_file(title: str, sections: list[tuple[str, list[str]]]) -> str:
    lines: list[str] = [
        f"# {title}",
        "# RX-only recon — PortaPack Mayhem FREQMAN",
        "# Not for TX / jam / unauthorized intercept of restricted services",
        "",
    ]
    count = 0
    for heading, entries in sections:
        lines.append(f"# --- {heading} ---")
        for e in entries:
            if count >= MAX_ENTRIES:
                lines.append("# TRUNCATED at Mayhem entry limit")
                break
            lines.append(e)
            count += 1
        lines.append("")
        if count >= MAX_ENTRIES:
            break
    return "\n".join(lines) + "\n"


def build_normandy_hot() -> str:
    """Short default list for SCANNER / NORMANDY (~40 entries)."""
    return build_file(
        "NORMANDY hot list (Scanner default)",
        [
            ("Aviation Normandy", aviation_normandy()[:12]),
            ("Marine safety / ports", marine_vhf()[:8]),
            ("PMR446", pmr446_channels()),
            ("Ham calling", [
                entry(145_500_000, MOD_NFM, BW_NFM_12K5, "2m FM Calling"),
                entry(433_500_000, MOD_NFM, BW_NFM_12K5, "70cm FM Calling"),
                entry(144_800_000, MOD_NFM, BW_NFM_12K5, "2m APRS 144.8"),
            ]),
            ("ISM peek", ism_sensors()[:4]),
        ],
    )


def pack_files() -> dict[str, str]:
    return {
        "NORMANDY.TXT": build_normandy_hot(),
        "SCANNER.TXT": build_normandy_hot(),
        "NX_AIR.TXT": build_file(
            "Normandy / regional aviation (AM)",
            [("ATS / ATIS / TWR / APP", aviation_normandy())],
        ),
        "NX_MARINE.TXT": build_file(
            "Marine VHF ITU working + safety (NFM)",
            [("Marine VHF", marine_vhf())],
        ),
        "NX_HAM.TXT": build_file(
            "FR amateur 2m/70cm calling + plan RPT outs",
            [("Amateur", ham_fr())],
        ),
        "NX_PMR_LPD.TXT": build_file(
            "PMR446 + LPD433 subset",
            [("PMR446", pmr446_channels()), ("LPD433", lpd433_channels())],
        ),
        "NX_ISM.TXT": build_file(
            "ISM / keyfob RX centers",
            [("ISM", ism_sensors())],
        ),
        "NX_BCST.TXT": build_file(
            "Broadcast FM + FR time",
            [("Broadcast / time", broadcast_ndy())],
        ),
        "RECON.TXT": build_file(
            "Recon aggregator (air+marine+ham calling+ISM)",
            [
                ("Air", aviation_normandy()[:10]),
                ("Marine", marine_vhf()[:10]),
                ("Ham", ham_fr()[:8]),
                ("PMR", pmr446_channels()[:8]),
                ("ISM", ism_sensors()[:6]),
            ],
        ),
    }


def write_freqman(path: Path, content: str) -> None:
    path = path.with_name(path.name.upper())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.replace("\r\n", "\n").replace("\r", "\n")
    if not data.endswith("\n"):
        data += "\n"
    # Strip comments for device-friendly copies when --device-clean
    path.write_bytes(data.encode("ascii", errors="replace"))
    entries = sum(1 for ln in data.splitlines() if ln.startswith("f=") or ln.startswith("a="))
    print(f"Wrote {path} ({path.stat().st_size} B, {entries} entries)")


def device_clean(content: str) -> str:
    """Drop comment/blank lines for picky loaders."""
    out = []
    for ln in content.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Build Mayhem FREQMAN recon lists")
    p.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "freqman",
    )
    p.add_argument(
        "--deploy-mount",
        type=Path,
        help="Copy pack into <mount>/FREQMAN/",
    )
    p.add_argument(
        "--device-clean",
        action="store_true",
        help="Write comment-free files (better for Scanner)",
    )
    p.add_argument("--deploy-serial", action="store_true")
    p.add_argument("--remote", default="/FREQMAN/NORMANDY.TXT")
    p.add_argument("--port", default="/dev/ttyACM0")
    args = p.parse_args()

    files = pack_files()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        body = device_clean(content) if args.device_clean else content
        write_freqman(args.output_dir / name, body)

    if args.deploy_mount:
        dest = args.deploy_mount / "FREQMAN"
        dest.mkdir(parents=True, exist_ok=True)
        for name in files:
            src = args.output_dir / name
            shutil.copy2(src, dest / name)
            print(f"Copied -> {dest / name}")
        # Also keep a commented archive on disk only
        (args.output_dir / "README_RECON.txt").write_text(
            "RX-only Normandy recon pack.\n"
            "SCANNER.TXT / NORMANDY.TXT = hot list.\n"
            "NX_*.TXT = topic lists. Load via Freq Manager / Scanner / Recon.\n"
            "No police / jammer / attack lists included.\n"
        )

    if args.deploy_serial:
        script = Path(__file__).resolve().parent / "mayhem_serial_ctl.py"
        local = args.output_dir / "NORMANDY.TXT"
        subprocess.check_call(
            [
                sys.executable,
                str(script),
                "--port",
                args.port,
                "--upload",
                str(local),
                "--remote",
                args.remote,
            ]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
