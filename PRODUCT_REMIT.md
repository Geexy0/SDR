# PRODUCT_REMIT — PortaPack / Mayhem SDR toolkit

## What this project is
Linux + Mayhem tooling for **HackRF Pro / PortaPack H4M** (serial control, FREQMAN, RX recon wrappers, lab TX under gate).

## Remit lanes

### Always in remit (no extra SCOPE) — receive / recon
Open, receive-first work. No lab allow-list required:

- Spectrum RX: sweeps, waterfall, IQ capture, voice-band auto-recon, decoder pipelines (ADS-B/AIS/POCSAG, etc.)
- ISM / Sub-GHz RX: `rtl_433` sniff, pulse analyze, Packet Workbench (baud/mod tag), TPMS sensor ID mapping & fleet tagging UI
- Autonomous RX Mission Scheduler (sweep → burst capture → analyze → FREQMAN export)
- Operator GPS / RF breadcrumbs / LOB DF / range rings / track CPA / GNSS L1 RX interference sample (receive-only geoloc)
- FREQMAN / Looking Glass style lists (aviation, marine, amateur, PMR/LPD, ISM)
- Mayhem serial helpers, SD deploy, official Mayhem SD content
- Defensive / awareness documentation for public RF formats

**Note:** “Open source / public RF formats” means tooling may decode over-the-air signals that use documented protocols. It does **not** waive spectrum law, privacy law, or operator responsibility in the local jurisdiction.

### Offensive / TX lane (requires SCOPE + CONFIRM)
Lab-gated **transmit** and disruption only. Authorized when all of:

1. `SCOPE.md` filled with concrete **TX** targets and RF limits  
2. `CONFIRM.md` signed off for the same engagement  
3. Work stays inside those documents  

In this lane (examples): controlled replay to named lab DUTs, Mayhem TX apps against allow-listed fixtures, **light/local lab jamming**.

Out of remit even with CONFIRM: hard/broadband/long-duration jamming of shared spectrum; AMP on unless SCOPE amends; police/emergency **dispatch disruption aids**.

## Relationship to Blazar
This repo is **not** Blazar. Authorization here does not inherit Blazar campaigns.

## Hardware safety (always)
- Default **receive-first**
- Internal RF **AMP off** (`-a 0`) unless SCOPE explicitly allows otherwise for TX work
- No TX helpers without SCOPE + CONFIRM

## Legal
Operators are responsible for spectrum law and privacy law in their jurisdiction (e.g. France). Remit text is not a license.
