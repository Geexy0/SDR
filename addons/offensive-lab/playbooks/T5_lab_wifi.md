# T5 — Isolated lab Wi‑Fi

## Goal
Characterize / stress **lab SSID only** (AP + clients you own).

## PortaPack
1. Looking Glass 2.4 / 5.8 using `LAB_WIFI_ISM.TXT` centers.  
2. Capture if studying proprietary AV dongles (T2 overlap).  

## Light jam (lab only)
```bash
# HackRF Native Mode — 2s narrow burst on lab channel center, AMP=0
./addons/offensive-lab/scripts/lab_light_jam.sh 2437000000 2
```

## Forbidden
Neighbor SSIDs, café Wi‑Fi, hard/broadband continuous jamming.
