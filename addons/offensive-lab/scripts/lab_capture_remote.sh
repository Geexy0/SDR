#!/usr/bin/env bash
# Capture lab remote / ISM DUT IQ (RX only, AMP=0) — PP-LAB-RF-2026
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
FREQ="${1:-433920000}"
SECS="${2:-3}"
TAG="${3:-lab_remote}"
export LNA="${LNA:-32}" VGA="${VGA:-24}" AMP=0
export SDR_OUT_DIR="${SDR_OUT_DIR:-$ROOT/addons/offensive-lab/captures}"
mkdir -p "$SDR_OUT_DIR"
[[ "${PP_LAB_CONFIRM:-}" == "PP-LAB-RF-2026" ]] || {
  echo "Set PP_LAB_CONFIRM=PP-LAB-RF-2026" >&2
  exit 1
}
echo "Capturing ${SECS}s @ ${FREQ} (+50 kHz LO) tag=${TAG}"
"$ROOT/sdr_recon_suite.sh" capture_iq "$FREQ" "$SECS"
echo "Press lab DUT remote during capture; replay only to matching lab receiver."
