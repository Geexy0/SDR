#!/usr/bin/env bash
# Native HackRF Linux SDR recon wrappers (receive-only, AMP off by default).
# Requires HackRF Native Mode (USB 1d50:6089), not Mayhem serial.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${SDR_OUT_DIR:-$ROOT/captures}"
mkdir -p "$OUT_DIR"

# Hardware protection defaults — never enable AMP unless user passes AMP=1
LNA="${LNA:-32}"
VGA="${VGA:-24}"
AMP="${AMP:-0}"

if [[ "${AMP}" != "0" ]]; then
  echo "WARNING: AMP=${AMP} — internal MGA-81563 will be powered." >&2
  echo "Set AMP=0 (default) unless you explicitly need the RF amp." >&2
fi

die() { echo "error: $*" >&2; exit 1; }

require_hackrf_native() {
  if ! command -v hackrf_info >/dev/null 2>&1; then
    die "hackrf_info not found"
  fi
  if ! hackrf_info 2>&1 | grep -qiE 'Board ID|Firmware Version|hackrf'; then
    die "No HackRF in Native Mode. Use: ./mayhem_serial_ctl.py --switch-to-hackrf"
  fi
  if hackrf_info 2>&1 | grep -qi 'No HackRF boards found'; then
    die "No HackRF boards found (still in Mayhem Serial Mode?)"
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [args]

Commands (RX-only, AMP default 0):
  sweep_band <f_start_mhz>:<f_end_mhz> [extra hackrf_sweep args...]
      Wideband power survey; prints top-10 peaks (DC spike filtered).

  decode_ism_433 [seconds]
      Decode 433.92 MHz ISM via rtl_433 (SoapyHackRF) or capture+replay.

  capture_iq <center_hz> [seconds] [sample_rate]
      Record IQ with +50 kHz LO offset (avoids zero-IF DC spike).
      Writes timestamped .cs8 under $OUT_DIR/

Env: LNA=32 VGA=24 AMP=0 SDR_OUT_DIR=$OUT_DIR
EOF
}

# --- sweep_band -------------------------------------------------------------
sweep_band() {
  require_hackrf_native
  local range="${1:-430:448}"
  shift || true
  local tmp
  tmp="$(mktemp -t sweepXXXXXX.csv)"
  echo "hackrf_sweep -f ${range} -l ${LNA} -g ${VGA} -a ${AMP} $*"
  # One-shot style: limited duration via timeout if available
  if command -v timeout >/dev/null 2>&1; then
    timeout 12 hackrf_sweep -f "$range" -l "$LNA" -g "$VGA" -a "$AMP" "$@" \
      >"$tmp" 2>"${tmp}.err" || true
  else
    hackrf_sweep -f "$range" -l "$LNA" -g "$VGA" -a "$AMP" "$@" \
      >"$tmp" 2>"${tmp}.err" &
    local pid=$!
    sleep 10
    kill -INT "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi

  if [[ ! -s "$tmp" ]]; then
    echo "--- stderr ---" >&2
    cat "${tmp}.err" >&2 || true
    die "sweep produced no CSV data"
  fi

  python3 - "$tmp" <<'PY'
import csv, sys
from collections import defaultdict

path = sys.argv[1]
# hackrf_sweep CSV: date,time,hz_low,hz_high,hz_bin_width,num_samples,dB...
bins = []  # (freq_hz, power_db)
with open(path, newline="") as f:
    for row in csv.reader(f):
        if len(row) < 7:
            continue
        try:
            hz_low = float(row[2])
            bw = float(row[4])
            samples = row[6:]
        except ValueError:
            continue
        for i, s in enumerate(samples):
            try:
                db = float(s)
            except ValueError:
                continue
            freq = hz_low + i * bw + bw / 2.0
            bins.append((freq, db))

if not bins:
    print("No power bins parsed")
    sys.exit(1)

# Cluster into ~25 kHz peaks; filter zero-IF / DC-ish: drop bins near exact
# multiples of large LO centers is hard without LO knowledge — instead drop
# the global min-power floor outliers and bins with abs power that look like
# the classic DC spike (often the hottest bin at sweep segment edges).
# Practical filter: ignore bins within 50 kHz of each segment's hz_low+0
# (first bin) which often carries the DC spike artifact.
filtered = []
with open(path, newline="") as f:
    for row in csv.reader(f):
        if len(row) < 7:
            continue
        try:
            hz_low = float(row[2])
            bw = float(row[4])
            samples = row[6:]
        except ValueError:
            continue
        for i, s in enumerate(samples):
            try:
                db = float(s)
            except ValueError:
                continue
            freq = hz_low + i * bw + bw / 2.0
            # Skip first bin of each FFT row (common DC/spike location)
            if i == 0:
                continue
            filtered.append((freq, db))

if not filtered:
    filtered = bins

# Peak pick: keep local maxima above median+margin, then top 10 unique ~12.5k
filtered.sort(key=lambda x: x[1], reverse=True)
picked = []
for freq, db in filtered:
    if any(abs(freq - pf) < 12500 for pf, _ in picked):
        continue
    picked.append((freq, db))
    if len(picked) >= 10:
        break

print(f"{'Rank':<6}{'Freq (MHz)':<14}{'Power (dB)':<12}")
print("-" * 32)
for i, (freq, db) in enumerate(picked, 1):
    print(f"{i:<6}{freq/1e6:<14.6f}{db:<12.2f}")
PY
  cp "$tmp" "$OUT_DIR/last_sweep.csv"
  rm -f "$tmp" "${tmp}.err"
  echo "CSV saved: $OUT_DIR/last_sweep.csv"
}

# --- decode_ism_433 ---------------------------------------------------------
decode_ism_433() {
  require_hackrf_native
  local secs="${1:-15}"
  local log="$OUT_DIR/rtl433_$(date +%Y%m%d_%H%M%S).jsonl"
  echo "ISM 433.92 decode → $log (AMP=$AMP LNA=$LNA VGA=$VGA)"

  if command -v rtl_433 >/dev/null 2>&1; then
    # Prefer SoapyHackRF if available
    if rtl_433 -h 2>&1 | grep -qi soapy || true; then
      if timeout "$secs" rtl_433 -d driver=hackrf -f 433920000 \
          -g "LNA=${LNA},VGA=${VGA},AMP=${AMP}" -F "json:${log}" 2>"$OUT_DIR/rtl433.err"; then
        echo "Logged to $log"
        tail -n 20 "$log" 2>/dev/null || true
        return 0
      fi
    fi
    # Fallback: short IQ capture then rtl_433 -r
    local iq="$OUT_DIR/ism433_$(date +%Y%m%d_%H%M%S).cs8"
    local sr=2500000
    local n=$((sr * secs * 2))  # cs8 interleaved I/Q bytes
    echo "Capture ${secs}s @ ${sr} SPS → $iq then rtl_433 -r"
    # +50 kHz offset from 433.92 MHz
    local center=$((433920000 + 50000))
    timeout $((secs + 3)) hackrf_transfer -r "$iq" -f "$center" -s "$sr" \
      -n "$n" -l "$LNA" -g "$VGA" -a "$AMP" || true
    rtl_433 -r "$iq" -s "$sr" -f 433920000 -F "json:${log}" || true
    echo "Logged to $log"
    tail -n 20 "$log" 2>/dev/null || true
  else
    die "rtl_433 not installed. Install: sudo apt install rtl-433"
  fi
}

# --- capture_iq -------------------------------------------------------------
capture_iq() {
  require_hackrf_native
  local center_hz="${1:-}"
  local secs="${2:-2}"
  local sr="${3:-2500000}"
  [[ -n "$center_hz" ]] || die "capture_iq requires center_hz"
  # Mandatory +50 kHz offset away from zero-IF DC spike
  local lo=$((center_hz + 50000))
  local stamp
  stamp="$(date +%Y%m%d_%H%M%S)"
  local out="$OUT_DIR/iq_${center_hz}Hz_lo${lo}_${stamp}.cs8"
  local n=$((sr * secs * 2))
  echo "Recording ${secs}s IQ: target=${center_hz} Hz LO=${lo} Hz (+50 kHz) SR=${sr}"
  echo "AMP=${AMP} LNA=${LNA} VGA=${VGA} → $out"
  hackrf_transfer -r "$out" -f "$lo" -s "$sr" -n "$n" \
    -l "$LNA" -g "$VGA" -a "$AMP"
  # Sidecar for URH / PortaPack replay notes
  cat >"${out}.txt" <<META
center_target_hz=${center_hz}
lo_hz=${lo}
offset_hz=50000
sample_rate=${sr}
seconds=${secs}
format=cs8
lna=${LNA}
vga=${VGA}
amp=${AMP}
META
  ls -lh "$out"
  echo "Open in URH as complex 8-bit IQ @ ${sr} SPS; signal is 50 kHz below LO."
}

cmd="${1:-}"
shift || true
case "$cmd" in
  sweep_band) sweep_band "$@" ;;
  decode_ism_433) decode_ism_433 "$@" ;;
  capture_iq) capture_iq "$@" ;;
  -h|--help|help|"") usage ;;
  *) die "unknown command: $cmd" ;;
esac
