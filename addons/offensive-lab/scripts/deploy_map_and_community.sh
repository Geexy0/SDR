#!/usr/bin/env bash
set -euo pipefail
ST=/home/geexy/Projects/SDR/firmware-cache/sd-stage
CUST=/home/geexy/Projects/SDR
MP="${1:-}"
[[ -d "$MP" ]] || { echo "usage: $0 /media/\$USER/MAYHEM"; exit 1; }
mkdir -p "$MP/ADSB" "$MP/FREQMAN" "$MP/LOOKINGGLASS" "$MP/APPS" "$MP/BLESPAM"
echo "Copying ADSB map+dbs (~2.1 GiB world_map.bin — be patient)..."
rsync -a --info=progress2 "$ST/ADSB/" "$MP/ADSB/"
echo "Copying FR FREQMAN + Looking Glass..."
cp -f "$ST/FREQMAN/"* "$MP/FREQMAN/"
cp -f "$ST/LOOKINGGLASS/"* "$MP/LOOKINGGLASS/" 2>/dev/null || true
echo "Copying standalone APPS (.ppmp)..."
cp -f "$ST/APPS/"*.ppmp "$MP/APPS/" 2>/dev/null || true
echo "Re-asserting custom Normandy/lab lists..."
cp -f "$CUST/freqman/"NORMANDY.TXT "$CUST/freqman/"SCANNER.TXT "$CUST/freqman/"RECON.TXT "$MP/FREQMAN/" 2>/dev/null || true
cp -f "$CUST/freqman/"NX_*.TXT "$MP/FREQMAN/" 2>/dev/null || true
cp -f "$CUST/addons/offensive-lab/freqman/"LAB_*.TXT "$MP/FREQMAN/" 2>/dev/null || true
cp -f "$CUST/addons/offensive-lab/BLESPAM/"* "$MP/BLESPAM/" 2>/dev/null || true
sync
echo "=== ADSB maps ==="
ls -lh "$MP/ADSB"/world_map*.bin
echo "=== sample FREQMAN ==="
ls "$MP/FREQMAN"/AIRPORT_FR_NORD.TXT "$MP/FREQMAN"/NORMANDY.TXT "$MP/FREQMAN"/LAB_OOK_ISM.TXT 2>/dev/null
echo "=== ppmp count ==="
ls "$MP/APPS"/*.ppmp 2>/dev/null | wc -l
df -h "$MP"
echo DONE
