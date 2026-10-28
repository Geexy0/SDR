# Offensive lab addons — `PP-LAB-RF-2026`

**Gate:** `SCOPE.md` ACTIVE + `CONFIRM.md` CONFIRMED.  
**Boundary:** closed lab, lab-owned DUTs T1–T7 only. AMP off by default. Hard/broadband jamming forbidden.

## Map targets → Mayhem apps (already on nightly SD)

| ID | DUT class | Mayhem / host workflow |
|----|-----------|------------------------|
| T1 | Garage / gate remotes | Capture → Replay; SubGhzD; OOK Editor; `LAB_OOK_ISM.TXT` |
| T2 | Wireless AV / screens | Looking Glass + Capture; characterize then Replay if analog/OOK |
| T3 | ISM sensors / TPMS / weather | `rtl_433` / Weather / TPMS RX; controlled replay only to lab fixture |
| T4 | BLE lab peripherals | BLE Rx / BLE Tx / BLESpam **lab profiles only** (`BLESPAM/`) |
| T5 | Lab Wi‑Fi SSID | Looking Glass 2.4/5.8; host tools vs **isolated lab AP only** |
| T6 | Lab PMR/LPD HTs | Audio / Scanner + `NX_PMR_LPD.TXT`; HT↔HT only |
| T7 | Toys / LED / fan remotes | Same as T1; stock `SAMPLES/LED` as reference |

## Repo layout

```
addons/offensive-lab/
  README.md                 # this file
  freqman/LAB_*.TXT         # lab FREQMAN lists
  scripts/                  # gated helpers (AMP=0, lab flags required)
  playbooks/                # step-by-step per target class
  captures/                 # put lab IQ / .C16 here (gitignored samples OK)
  BLESPAM/                  # lab-only name/payload text for Mayhem BLESpam
```

## Deploy FREQMAN + BLESPAM to SD

```bash
# After mounting MAYHEM SD:
./addons/offensive-lab/scripts/deploy_lab_addons.sh /media/$USER/MAYHEM
```

## Safety checklist (every TX session)

1. DUT on the table is **lab-owned** (T1–T7).  
2. Prefer attenuator / short range / shielded box.  
3. `AMP=0` unless SCOPE amended.  
4. Light jam: `lab_light_jam.sh` only — short, narrow, attended.  
5. Stop if energy leaks outside the lab.  
6. No street / neighbor / public SSID testing.
