# pp_dashboard — current state

**Version:** `0.7.0` (`src/pp_dashboard/__init__.py`, `pyproject.toml`)  
**Updated:** 2026-10-29  
**Git:** `master` ahead of `origin/master` (commits through `d233e84` security remediations). **Module 1–3 + geoloc still uncommitted** on the working tree.

## What it is

PortaPack H4M Pro + HackRF Pro **Cyber-Deck**: FastAPI control plane + React SPA (`frontend/dist`). Dual-mode USB mutex (`MAYHEM_SERIAL` ↔ `HACKRF_NATIVE`). Receive-first; TX under `SCOPE.md` + `CONFIRM.md` engagement **PP-LAB-RF-2026**.

## Run

```bash
cd ~/Projects/SDR && source .venv/bin/activate
pp-dashboard          # http://127.0.0.1:8765
# optional SPA rebuild: cd frontend && npm run build
```

Offline tests: `.venv/bin/python -m pytest` (153+ passing as of this snapshot).

## Shipped capabilities (by lane)

### Hardware / security
- `HardwareArbiter` — mode switch, busy registry, stop callbacks, SIGINT→2s SIGKILL
- `tx_gate` + SCOPE/CONFIRM; AMP off on TX unless SCOPE amends
- Telemetry WS `/ws/telemetry` (incl. `type=mission`)

### RF / Mayhem
- Capture / replay / CW / sweep / multiplex RX
- Mayhem serial bridge, screen WS, FREQMAN bookmark + SD sync
- Gated TX: jammer, rolljam, synth (GPS L1 / pager / TouchTunes / FM RDS)

### Sub-GHz
- `rtl_433` sniff, pulse analyze, OOK synth / replay / bruteforce (TX gated)
- **Packet Workbench** (`subghz/workbench.py`) — baud estimate, MM slicer, sync/CRC, UI tab

### Recon
- Waterfall (`hackrf_sweep -B`), ADS-B / AIS / POCSAG, Auto Voice Recon (WebAudio/CTCSS/VOX)
- TPMS fleet listen + sensor/vehicle mapping
- **Mission Scheduler** (`recon/scheduler.py`) — templates + `POST /api/recon/mission`
  - Steps: `STEP_SWEEP_RANK` → `BURST_CAPTURE` → `AUTO_ANALYZE` → `FREQMAN_EXPORT`
  - Arbiter busy/stop wired (hardware stop aborts mission)

### Geoloc / location intel (`/api/geoloc/*`)
- Operator GPS (browser watch or map-center) → stamps mission / voice / TPMS
- Breadcrumb heatmap, LOB DF + intersection, range rings, track CPA on map tooltips
- Wi‑Fi/cell accuracy circle (browser geolocation uncertainty)
- GNSS L1 RX interference sample (`POST /api/geoloc/gnss/l1-sample`)

## Frontend (Cyber-Deck tabs)
Sub-GHz (incl. Packet Workbench) · Waterfall · Recon (Mission Control + Location Intel + map) · TPMS · Mayhem · TX tools (gated)

## Remit reminder
- RX / recon / geoloc stamp: always allowed per `PRODUCT_REMIT.md`
- TX / jam / spoof / replay: only with active SCOPE + matching CONFIRM
- Hard/broadband jamming remains forbidden

## Next / blocked
- Commit Module 1 (workbench) + Module 3 (mission) + geoloc as Conventional Commits (not pushed)
- Optional: live dry-run of `subghz_sensor_hunt` + GPS breadcrumb walk on hardware
