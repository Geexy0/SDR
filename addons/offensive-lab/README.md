# Offensive lab addons

**Gate:** local `SCOPE.md` status `ACTIVE` + matching `CONFIRM.md` `CONFIRMED` (copy from the repo-root `*.example.md` templates).  
**Boundary:** closed lab, lab-owned DUTs T1–T7 only. AMP off by default. Hard/broadband jamming forbidden.

## Map targets → Mayhem apps (already on nightly SD)

| ID | DUT class | Mayhem / host workflow |
|----|-----------|------------------------|
| T1 | Garage / gate remotes | Capture → Replay; SubGhzD; OOK Editor |
| T2 | Wireless AV / screens | Looking Glass + Capture; characterize then Replay if analog/OOK |
| T3 | ISM sensors / TPMS / weather | `rtl_433` / Weather / TPMS RX; controlled replay only to lab fixture |
| T4 | BLE lab peripherals | BLE Rx / BLE Tx / BLESpam **lab profiles only** |
| T5 | Lab Wi‑Fi SSID | Looking Glass 2.4/5.8; host tools vs **isolated lab AP only** |
| T6 | Lab PMR/LPD HTs | Audio / Scanner; HT↔HT only |
| T7 | Toys / LED / fan remotes | Same as T1; stock `SAMPLES/LED` as reference |

## Repo layout

```
addons/offensive-lab/
  playbooks/     operator notes per DUT class
  freqman/       optional lab FREQMAN lists
  captures/      gitignored IQ / .C16 drops
```

Playbooks assume you already filled SCOPE/CONFIRM for **your** engagement ID. Replace any placeholder `YOUR-ENGAGEMENT-ID` / `PP_LAB_CONFIRM` export with that name.
