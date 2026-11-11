# PortaPack Cyber-Deck — current state

**Product:** `pp_dashboard` (PortaPack H4M Pro + HackRF Pro control plane + React SPA)  
**Version:** `0.9.0` — `src/pp_dashboard/__init__.py`, `pyproject.toml`  
**Updated:** 2026-11-11

**TX gate:** Local `SCOPE.md` + `CONFIRM.md` (gitignored). Copy from `SCOPE.example.md` / `CONFIRM.example.md`. Without both, all TX routes return **403**.

---

## What it is

Linux Cyber-Deck for **HackRF Pro + PortaPack H4M (Mayhem)**:

| Layer | Role |
|-------|------|
| Backend | FastAPI (`pp_dashboard`) — hardware arbiter, engines, REST + WebSockets |
| Frontend | Vite + React 19 + Zustand + Leaflet SPA → served from `frontend/dist` |
| Hardware | Dual-mode USB mutex: `MAYHEM_SERIAL` (CDC `/dev/ttyACM*`) ↔ `HACKRF_NATIVE` (`1d50:6089` / `libhackrf`) |
| Policy | Receive-first per `PRODUCT_REMIT.md`; TX only under SCOPE+CONFIRM |

Default bind: **`http://127.0.0.1:8765`**. Entry: `pp-dashboard` / `uvicorn pp_dashboard.main:app`.

---

## How to run

```bash
source .venv/bin/activate
pp-dashboard                          # API + SPA on :8765
# SPA rebuild after UI changes:
cd frontend && npm install && npm run build
```

**Master verification (Definition of Done):**

```bash
./scripts/verify_all.sh
```

Optional physical RX-only: `.venv/bin/python -m pytest --run-hil -m hil` (requires HackRF USB; **no TX**).

---

## Architecture snapshot

```
Browser SPA ──REST/WS──► FastAPI create_app()
                           ├─ HardwareArbiter (mutex, runner, mux, stop callbacks)
                           ├─ SubGhzEngine / TpmsEngine / ReconEngine / GeolocService
                           ├─ MissionScheduler (wired into arbiter busy/stop)
                           ├─ LiveGeoHuntFusion + RF fingerprint store
                           └─ Mayhem bridge (serial screen + apps + FREQMAN)
```

**Hard constraints:** never run Mayhem serial and `hackrf_*` simultaneously; LNA 8 dB / RX VGA 2 dB / TX VGA 1 dB / AMP prefer 0; long SDR jobs SIGINT then 2 s SIGKILL.

---

## v0.9.0 highlights

- **RF fingerprint:** CFO / spectral features, Mahalanobis distance, clone badge on Sub-GHz packets + workbench
- **Live geo-hunt:** fusion of LOB + RSSI + Doppler CPA; hunt start/stop/ingest/bearing routes
- **UI:** fingerprint badge, geo-hunt toggle on LocationIntelDeck
- Coverage gate: offline pytest `fail_under=82` on the gated surface

---

## Remit lanes (summary)

- **Always allowed:** sweeps, IQ capture, voice recon, ADS-B/AIS/POCSAG, Sub-GHz sniff + TPMS mapping, geoloc math, Mayhem serial helpers
- **TX (SCOPE+CONFIRM):** replay, CW/AWGN, light lab jam, Sub-GHz bruteforce/replay, Mayhem TX apps, synth packs
- **Forbidden even when CONFIRMED:** hard/broadband/long jam; emergency-dispatch disruption aids; AMP-on unless SCOPE amends

---

## Repo layout

```
src/pp_dashboard/     backend package
frontend/             Cyber-Deck SPA
tests/                offline + HIL suites
scripts/verify_all.sh master DoD script
SCOPE.example.md      TX gate template → copy to SCOPE.md
CONFIRM.example.md    TX confirm template → copy to CONFIRM.md
PRODUCT_REMIT.md      remit lanes
LICENSE / SECURITY.md public release hygiene
```
