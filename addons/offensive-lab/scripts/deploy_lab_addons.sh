#!/usr/bin/env bash
# Copy lab FREQMAN + BLESPAM profiles onto mounted Mayhem SD
set -euo pipefail
MP="${1:-}"
[[ -n "$MP" && -d "$MP" ]] || {
  echo "usage: $0 /media/\$USER/MAYHEM" >&2
  exit 1
}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$MP/FREQMAN" "$MP/BLESPAM" "$MP/SAMPLES/LAB"
cp -f "$ROOT"/freqman/LAB_*.TXT "$MP/FREQMAN/"
cp -f "$ROOT"/BLESPAM/*.txt "$MP/BLESPAM/" 2>/dev/null || true
# Strip any accidental CR
python3 - <<PY
from pathlib import Path
for p in Path("$MP/FREQMAN").glob("LAB_*.TXT"):
    b = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    lines = [ln for ln in b.decode().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    p.write_text("\n".join(lines) + "\n")
    print("deployed", p.name, p.stat().st_size)
PY
sync
echo "Lab addons copied to $MP — eject SD, insert in PortaPack, load LAB_* in Freq Manager."
echo "BLESpam: use names from BLESPAM/ on SD (lab T4 only)."
