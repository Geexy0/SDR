# PortaPack Cyber-Deck — current state

**Product:** `pp_dashboard` (PortaPack H4M Pro + HackRF Pro control plane + React SPA)  
**Version:** `0.8.0` — `src/pp_dashboard/__init__.py`, `pyproject.toml`  
**Updated:** 2026-10-31  
**Git:** `master` **ahead of `origin/master` by 13 commits**. Latest on branch tip: analytical geoloc suite (`b3202e3`). Large uncommitted set: v0.8 geoloc UI (Upgrade 2–3), 5-layer verify harness, Vitest, `CURRENT_STATE` / remit tweaks. **Do not push** unless Vivien asks.

**Engagement gate:** `SCOPE.md` = `ACTIVE` · `CONFIRM.md` = `CONFIRMED` · name **`PP-LAB-RF-2026`** (2026-10-27 → 2027-10-27). TX allow-list T1–T7 lab DUTs only; AMP off by default; hard/broadband jam forbidden.

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
cd ~/Projects/SDR && source .venv/bin/activate
pp-dashboard                          # API + SPA on :8765
# SPA rebuild after UI changes:
cd frontend && npm install && npm run build
```

**Master verification (Definition of Done):**

```bash
./scripts/verify_all.sh
```

That script runs: pytest (HIL deselected) + ≥85% line coverage on the gated surface → `frontend` `tsc` / Vitest / production build → ephemeral mock-hardware smoke (`/healthz`, `/`, hardware/recon/tpms/geoloc status).

Optional physical RX-only: `.venv/bin/python -m pytest --run-hil -m hil` (requires HackRF USB ID or live `hackrf_info`; **no TX**).

---

## Architecture snapshot

```
Browser SPA ──REST/WS──► FastAPI create_app()
                           ├─ HardwareArbiter (mutex, runner, mux, stop callbacks)
                           ├─ SubGhzEngine / TpmsEngine / ReconEngine / GeolocService
                           ├─ MissionScheduler (wired into arbiter busy/stop)
                           └─ Mayhem bridge (serial screen + apps + FREQMAN)
```

**Hard constraints (see `.cursor/rules/hackrf-hardware-constraints.mdc`):**

- Never run Mayhem serial and `hackrf_*` simultaneously.
- Mode switch: serial `hackrf\r\n` → native; native → `hackrf_spiflash -R` → wait ACM.
- Gains: LNA 8 dB steps · RX VGA 2 dB · TX VGA 1 dB · AMP `0|1` (prefer 0).
- Long SDR jobs: async subprocess, SIGINT then **2 s** SIGKILL.
- Every hardware action acquires the arbiter lock.

**API surface:** **89** discovered routes (HTTP + WS) — must stay classified in `tests/test_route_introspection.py` (`EXPECTED_TX_GATED_ROUTES` ∪ `EXPECTED_RX_OR_CONTROL_ROUTES`).

---

## Remit / security lanes

### Always allowed (no SCOPE) — RX / recon
Sweeps, waterfall, IQ capture, voice recon, ADS-B/AIS/POCSAG/radiosonde RX, Sub-GHz sniff + analyze + workbench, TPMS listen + fleet mapping, mission scheduler (RX steps), operator GPS / LOB / geoloc math / GNSS L1 **sample+classify**, Mayhem serial helpers, FREQMAN lists.

### TX lane (SCOPE + CONFIRM required)
Replay, continuous CW/AWGN, jammer, rolljam, Sub-GHz bruteforce/replay, Mayhem TX apps, synth packs (GPS L1 / pager / TouchTunes / FM RDS). Validators force **`amp=0`** unless SCOPE amends. Route lock sweep asserts **HTTP 403** + zero subprocesses when CONFIRM is `NOT CONFIRMED`.

### Forbidden even when CONFIRMED
Hard / broadband / long-duration jamming; police/emergency dispatch disruption aids; TX against non–allow-list targets; AMP-on unless SCOPE text changes.

---

## Backend capabilities (detail)

### Hardware / control plane
| Piece | Path / endpoint | Notes |
|-------|-----------------|-------|
| Arbiter | `hardware/arbiter.py` | Mode toggle, busy registry, stop callbacks, watchdog |
| Runner | `hardware/subprocess_runner.py` | `hackrf_transfer` / `hackrf_sweep` / `rtl_433` lifecycle |
| Detect | `hardware/detect.py` | `lsusb` + `hackrf_info` + by-id ACM resolve |
| IQ mux | `hardware/iq_multiplexer.py` | 10 MSPS stdout → FFT + DDC FIFOs |
| Status / stop | `GET/POST /api/hardware/*` | detect, mode, stop, capture, sweep, multiplex, replay, CW |
| Telemetry WS | `/ws/telemetry` | Single fan-out socket (status / metric / mission) |

### TX / RF synth (gated)
| Endpoint | Purpose |
|----------|---------|
| `POST /api/hardware/replay` | Loop/once `.cs8` TX |
| `POST /api/hardware/tx/continuous` | CW / amplitude tone |
| `POST /api/tx/jammer` | CW_AWGN / chirp barrage (light lab) |
| `POST /api/tx/synthesize` | GPS L1 / LRS pager / TouchTunes / FM RDS |
| `POST /api/rf/replay`, `/api/rf/rolljam` | Capture-then-replay helpers |
| `POST /api/subghz/bruteforce`, `/replay` | OOK framed / De Bruijn |
| `POST /api/mayhem/app` | TX packs gated by app id inside handler |

### Sub-GHz
- Live sniff: `rtl_433` via SoapyHackRF (sample rates snapped to integer MHz).
- Pulse analyzer: `subghz/analyze.py` → SQLite (`subghz/db.py`) → `/ws/subghz`.
- Packet Workbench: `subghz/workbench.py` — blind baud, M&M slicer, line codes, sync/CRC; `POST /api/subghz/workbench/demod`.
- OOK synth / `.sub` helpers: `ook_synth.py`, `subfile.py`.
- Sniffer exposes `running` from live fifo / `rtl_433` runner argv (for arbiter busy/stop).

### TPMS
- Listen + hop bands, 12 s passage clustering (`tpms/cluster.py`), vehicle link CRUD.
- OEM hints from rtl_433 model strings (`tpms/oem_hints.py`).
- APIs: `/api/tpms/status|listen|sensors|passages|vehicles|link|clear`.

### Recon
| Engine | Role |
|--------|------|
| Waterfall | `hackrf_sweep -B` binary rows → `/ws/waterfall` (WF32 frames) |
| Decoders | POCSAG, AIS, Hybrid ADS-B (`hybrid_adsb.py` / `modes_s.py`), Radiosonde |
| Voice | Band dwell, CTCSS Goertzel / DCS Golay, `/ws/audio` PCM, VOX `.wav` store |
| Radiosonde | 400.15–406 MHz; RS41 sync `0x10B6CA11`; prefers `rs41mod`/`rs_detect`; soft inject API |
| MissionScheduler | Templates + DAG: `STEP_SWEEP_RANK` → `STEP_BURST_CAPTURE` → `STEP_AUTO_ANALYZE` → `STEP_FREQMAN_EXPORT`; hardware stop aborts |

### Geoloc / location intel (`/api/geoloc/*`)
| Module | Capability |
|--------|------------|
| `projection.py` | WGS84 ↔ ENU local tangent |
| `solver.py` | Stansfield 2-pass WLS LOB + CEP₉₅ (χ²≈5.991); RSSI path-loss grid ML + HDR |
| `foxhunt.py` | 1D Kalman `[rssi, rate]` × GPS motion → warmer/colder |
| `polar.py` | Circular-centroid lobe peak → Commit Peak LOB |
| `gnss_monitor.py` | L1 sample + classify: NOMINAL / CW_TONE_INGRESS / CHIRP_SWEEP / WIDEBAND_NOISE |
| `horizon.py` | 4/3-Earth horizon, slant range, Az/El, FSPL |
| Store / service | Operator fix, breadcrumbs, heatmap, LOBs, range rings, CPA |

Key POSTs: `operator`, `breadcrumb`, `lob`, `lob/intersect`, `lob/solve`, `rssi-grid/solve`, `foxhunt`, `polar/peak`, `gnss/l1-sample`, `gnss/l1-analyze`, `look-angle`, `cpa`, `solve`.

### Mayhem
- Serial bridge + ASCII/screen frame decode → `/ws/mayhem-screen`.
- App list / start / button input; FREQMAN bookmark + SD sync.
- TX-capable apps share the TX gate with CONFIRM.

---

## Frontend (Cyber-Deck SPA)

**Stack:** React 19, Zustand (`useHardwareStore`), Leaflet / react-leaflet, Tailwind 4, Lucide, Vitest.

**Workspace tabs** (`WorkspaceTabs.tsx`):

1. **Sub-GHz OOK** — `SubGhzDeck` + pulse canvas  
2. **Packet Workbench** — offline demod UI  
3. **TPMS Spy** — `TpmsFleetDeck`  
4. **Active TX / Rolljam** — `ActiveTxDeck` (gated)  
5. **Recon / Spoof** — `ReconSpoofDeck` (mission drawer, waterfall, location intel, spoof pin)

**Chrome / shared:** `HardwareHeader` (status + single `/ws/telemetry`), `MayhemBridgeDock`, `SpectrumWaterfall` (uses `lib/binaryFrames.ts` WF32 parser), `WebAudioPlayer`, `MissionControlDrawer`.

**v0.8 location UI (present in tree; largely uncommitted):**

- `LocationIntelDeck.tsx` / `LocationIntelPanel.tsx` — Leaflet overlays  
- `PolarBearingRose.tsx` — polar peak + commit LOB  
- `LookAngleWidget.tsx` — Az/El / horizon for radiosonde tracks  
- `lib/geigerVariometer.ts` — WebAudio pitch/pulse vs RSSI  
- `lib/binaryFrames.ts` — WF32 + PCM queue (empty / NaN safe)

**Frontend tests:** `src/__tests__/hardwareStore.test.ts`, `binaryFrames.test.ts` · config `vitest.config.ts`.

---

## Verification harness (5 layers)

| Layer | Artifact | Role |
|-------|----------|------|
| 1 | `tests/test_route_introspection.py` + `route_catalog.py` | Every route classified; TX 403 when NOT CONFIRMED; `amp=1` → 403/422 |
| 2 | `tests/test_all_engines_e2e.py` | Parametrized start → `POST /api/hardware/stop`; no orphan FakeProcess |
| 3 | `frontend/` | `tsc --noEmit`, Vitest `--run`, `npm run build` → `dist/index.html` |
| 4 | `tests/test_hil_hardware.py` | `@pytest.mark.hil`; RX-only; needs `--run-hil` + real HackRF |
| 5 | `scripts/verify_all.sh` | Master gate (DoD) |

**Fakes:** `tests/fakes.py` — `FakeMayhemSerial`, `FakeSubprocessPool` / `FakeProcess` (`communicate`, killpg prefers live PIDs). Autouse `isolate_hardware` skipped for `@hil`.

**Coverage:** `pyproject.toml` `fail_under = 85` on line coverage with stream/engine pumps omitted (documented in omit list). Full-package no-omit remains ~61% — shrinking omits is follow-up work. Last green gate: **214 passed**, **85.02%** gated coverage.

**Cursor DoD rule:** `.cursor/rules/verify-all-definition-of-done.mdc` — never claim done without a fresh green `./scripts/verify_all.sh`.

---

## Route inventory (summary)

- **Meta / SPA:** `/`, `/legacy`, `/deck`, `/healthz`, favicons, icons  
- **Hardware RX/control:** status, detect, mode, stop, capture, sweep, multiplex  
- **TX-gated (9):** hardware replay/CW, `/api/tx/*`, `/api/rf/replay|rolljam`, subghz bruteforce/replay, mayhem app  
- **Sub-GHz / TPMS / Recon / Geoloc:** as listed in architecture section  
- **WebSockets (5):** telemetry, waterfall, subghz, audio, mayhem-screen  

Adding a route without updating `EXPECTED_*` sets **fails CI/local harness**.

---

## Repo layout (high-signal)

```
src/pp_dashboard/     backend package
frontend/             Cyber-Deck SPA
tests/                offline + HIL suites
scripts/verify_all.sh master DoD script
SCOPE.md / CONFIRM.md TX gate documents
PRODUCT_REMIT.md      remit lanes
.cursor/rules/        hardware + offensive + verify-all DoD
```

---

## Known gaps / honesty notes

1. **Coverage omit list is aggressive** — arbiter, detect, engines, WS pumps, bridge omitted from the 85% gate; Layer 2 soft-starts and Layer 4 HIL exercise them, but line coverage is not full-package 85%.  
2. **Layer 2** accepts 409/503 on start (tool missing / busy); kill-clean still asserted.  
3. **Frontend v0.8 location widgets** exist on disk but are not fully committed.  
4. **HIL** is opt-in and environment-sensitive (noise-floor band, mode bounce).

---

## Next / blocked

| Priority | Item |
|----------|------|
| Commit | Harness + v0.8 UI + CURRENT_STATE as Conventional Commits when Vivien asks (still **ahead 13**, dirty tree) |
| Coverage | Shrink omit list; raise real arbiter/engine line coverage |
| Hardware | Optional `pytest --run-hil` dry-run on PortaPack/HackRF |
| Product | Live fox-hunt / polar / radiosonde (`rs41mod`) on air when convenient |

**Blocked on:** none for offline development; TX features already gated by active SCOPE+CONFIRM.
