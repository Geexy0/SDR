# SCOPE — PortaPack lab **TX / jamming** lane (template)

**Status:** `UNSET`

> Copy to `SCOPE.md` (gitignored) and fill in before any TX work.  
> Receive-only recon does **not** require this file — see `PRODUCT_REMIT.md`.

## Engagement identity
- **Engagement name:** `YOUR-ENGAGEMENT-ID`
- **Operator:** `YOUR NAME`
- **Environment:** Closed RF lab (indoor controlled space for **TX energy**)
- **Start / end dates:** `YYYY-MM-DD` → `YYYY-MM-DD`
- **Jurisdiction:** your local spectrum authority — keep **TX** energy inside the lab; prefer shielded box / attenuator / dummy load

## Authorized TX targets (allow-list)
Only **lab-owned** devices physically inside the closed lab:

| ID | Target class (lab-owned DUT) | Owner / permission | Allowed **TX** actions |
|----|------------------------------|--------------------|------------------------|
| T1 | Garage-door / gate remotes (OOK/FSK ISM) | Lab property | Controlled replay **to lab DUT only** |
| T2 | Wireless displays / AV dongles | Lab property | Mayhem TX apps vs lab DUT only |
| T3 | ISM sensors / weather / TPMS **lab fixtures** | Lab property | Controlled replay to lab fixture only |
| T4 | BLE peripherals / beacons | Lab property | Mayhem BLE TX tools vs lab DUT only |
| T5 | Wi‑Fi APs / clients (isolated SSID) | Lab property | Lab-local TX tools vs lab SSID only |
| T6 | PMR446 / LPD handhelds | Lab property | TX between lab radios only |
| T7 | Other lab ISM remotes (LEDs, fans, toys) | Lab property | Replay to matching lab DUT |

## Explicitly out of this TX scope
- TX / jam / replay against devices **not** on T1–T7
- Taking lab **TX** outdoors or past the lab RF boundary
- Persistent or broadband **hard jamming** of shared spectrum
- Police / emergency **dispatch disruption** aids
- AMP on — only if later amended here; default **AMP off**

## Allowed TX techniques
- [ ] Controlled replay / Mayhem TX apps aimed at **matching lab-owned DUT only**
- [ ] BLE / Sub-GHz / Wi‑Fi **lab TX tools** against **T1–T7 only**
- [ ] **Light / local jamming** — short-duration, narrow, low-duty, **inside lab only**
- [ ] Hard / broadband / long-duration jamming — **FORBIDDEN**
- [ ] AMP on — only if later amended here

## RF / hardware constraints (TX)
- **AMP:** `off` by default (`-a 0`)
- **Duty cycle (light jam):** short bursts, operator-attended
- **Geographic limit (TX):** closed lab perimeter only

## Approval
Valid only with `CONFIRM.md` status `CONFIRMED` for the same engagement name.
