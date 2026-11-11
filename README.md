# PortaPack Cyber-Deck (`pp_dashboard`)

Linux command-and-control dashboard for **HackRF One / HackRF Pro + PortaPack H4M (Mayhem)**: FastAPI backend, React SPA, dual-mode USB mutex, receive-first recon, and lab-gated TX.

**Version:** `0.9.0`

## What you get

| Area | Capabilities |
|------|----------------|
| Hardware | Mayhem serial ↔ native `hackrf_*` mode switch, arbiter lock, capture / sweep / replay lifecycle |
| Sub-GHz | Sniff, analyze, Packet Workbench, TPMS mapping, RF fingerprint (CFO / Mahalanobis clone check) |
| Recon | Voice recon, ADS-B / AIS / POCSAG pipelines, mission scheduler |
| Geoloc | Operator GPS, LOB WLS, RSSI grid ML, Doppler CPA, live geo-hunt fusion |
| Policy | Receive-first (`PRODUCT_REMIT.md`); TX only with local `SCOPE.md` + `CONFIRM.md` |

## Quick start

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd frontend && npm install && npm run build && cd ..
pp-dashboard   # http://127.0.0.1:8765
```

**Definition of Done / CI harness:**

```bash
./scripts/verify_all.sh
```

## TX gate (lab only)

Transmit helpers stay **denied** until you copy the templates and fill them for a closed lab:

```bash
cp SCOPE.example.md SCOPE.md
cp CONFIRM.example.md CONFIRM.md
# edit both — Status ACTIVE + CONFIRMED, matching engagement name
```

`SCOPE.md` and `CONFIRM.md` are **gitignored**. Never commit signed engagement docs.

Hard / broadband / long-duration jamming remains forbidden even when CONFIRM is active. Prefer RF **AMP off**.

## Hardware constraints (HackRF)

- Frequency 1 MHz … 6 GHz · sample rate 2–20 MSPS · IQ `.cs8` signed 8-bit
- RX LNA: 0…40 dB in **8 dB** steps · RX VGA: 0…62 in **2 dB** · TX VGA: 0…47 in **1 dB** · AMP `0|1`
- Never run Mayhem CDC-ACM and native `hackrf_*` at the same time (USB mutex)

## Legal

Operators are responsible for spectrum and privacy law in their jurisdiction. This software is a lab / research tool, not a license to transmit.

## License

MIT — see [LICENSE](LICENSE).
