#!/usr/bin/env bash
# Light / local lab jam — requires PP_LAB_CONFIRM=<engagement-id> matching CONFIRM.md.
# Narrow, short, AMP=0, attended. Not a broadband jammer.
set -euo pipefail

FREQ_HZ="${1:-}"
SECONDS_TX="${2:-2}"
MAX_SECONDS=15
LNA="${LNA:-16}"
VGA="${VGA:-20}"
AMP="${AMP:-0}"
SR="${SR:-2000000}"

die() { echo "error: $*" >&2; exit 1; }

[[ -n "${PP_LAB_CONFIRM:-}" ]] || die \
  "Set PP_LAB_CONFIRM=<engagement-id> (must match local CONFIRM.md)"

[[ -n "$FREQ_HZ" ]] || die "usage: $0 <freq_hz> [seconds<=15]"
[[ "$SECONDS_TX" =~ ^[0-9]+$ ]] || die "seconds must be integer"
(( SECONDS_TX >= 1 && SECONDS_TX <= MAX_SECONDS )) || die "seconds must be 1..$MAX_SECONDS"
[[ "$AMP" == "0" ]] || die "AMP must be 0 for light jam (SCOPE default)"

command -v hackrf_transfer >/dev/null || die "hackrf_transfer missing (HackRF Native Mode?)"
if hackrf_info 2>&1 | grep -qi 'No HackRF boards found'; then
  die "No HackRF Native Mode device"
fi

# Generate brief white-ish noise as cs8 via python (no huge files on disk)
N=$((SR * SECONDS_TX * 2))
TMP=$(mktemp -t labjamXXXXXX.cs8)
python3 - "$TMP" "$N" <<'PY'
import os, sys, numpy as np
path, n = sys.argv[1], int(sys.argv[2])
rng = np.random.default_rng(0x1A8F2026)
# 8-bit IQ noise, modest amplitude (~25% FS) — "light"
i = rng.integers(-32, 33, size=n // 2, dtype=np.int8)
q = rng.integers(-32, 33, size=n // 2, dtype=np.int8)
iq = np.empty(n, dtype=np.int8)
iq[0::2] = i
iq[1::2] = q
iq.tofile(path)
print(f"noise_bytes={n}", flush=True)
PY

echo "LIGHT JAM lab-only: f=${FREQ_HZ} Hz ${SECONDS_TX}s LNA=${LNA} VGA=${VGA} AMP=0"
echo "Ctrl+C to abort before TX..."
sleep 1
# -t transmit file; -x TX VGA 0..47 — keep modest
TXVGA="${TXVGA:-20}"
hackrf_transfer -t "$TMP" -f "$FREQ_HZ" -s "$SR" -n "$N" \
  -a 0 -x "$TXVGA" || true
rm -f "$TMP"
echo "done (attended light burst)."
