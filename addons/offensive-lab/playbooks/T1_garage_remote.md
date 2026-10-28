# T1 — Lab garage / gate remote

## Goal
Capture and replay **lab-owned** remotes against the matching lab receiver only.

## PortaPack steps
1. Looking Glass / Level around 433.42 / 433.92 / 868.x while pressing the lab remote.  
2. **Capture** a short `.C16` while pressing the button (AMP off, moderate LNA/VGA).  
3. Save under `addons/offensive-lab/captures/` or SD `SAMPLES/LAB/`.  
4. **Replay** aimed at the lab door/gate board on the bench — confirm physical ownership.  
5. Optional: OOK Editor / SubGhzD to inspect bit patterns.

## Host assist
```bash
# Native HackRF mode — capture near 433.92 with +50 kHz LO offset
./sdr_recon_suite.sh capture_iq 433920000 3
```

## Out of scope
Street cars, neighbor gates, rolling-code attacks against non-lab property.
