# Security Policy

## Scope

This repository is a **receive-first** HackRF / PortaPack control plane. Transmit helpers are gated by local `SCOPE.md` + `CONFIRM.md` (see `PRODUCT_REMIT.md`). Shipping the code does **not** authorize TX in your jurisdiction.

## Reporting a vulnerability

Please open a **private** security advisory on GitHub (Security → Advisories → New draft advisory), or contact the maintainer via their GitHub profile.

Include:

- Affected component (API route, TX gate, subprocess runner, frontend, etc.)
- Reproduction steps that do **not** require live TX against third-party spectrum
- Impact (auth bypass, path traversal, TX-gate bypass, RCE, etc.)

Do **not** file public issues that include working exploit PoCs against the TX gate until a fix is available.

## Non-goals for reports

- “I can transmit if I fill SCOPE+CONFIRM” — intentional lab policy, not a vulnerability
- Spectrum-law questions — operators own legal compliance locally
- Issues that require physical access to the operator’s USB HackRF with an already-open lab gate

## Hardening expectations

- Default bind is loopback (`127.0.0.1:8765`); do not expose the API to the public internet without auth and TLS in front
- TX routes must return **403** when SCOPE/CONFIRM are unset or mismatched
- Subprocess runners must never leave `hackrf_*` hanging (SIGINT → 2 s SIGKILL)
