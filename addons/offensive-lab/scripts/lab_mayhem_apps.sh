#!/usr/bin/env bash
# Print / optionally start Mayhem apps via serial for lab workflows
set -euo pipefail
PORT="${MAYHEM_PORT:-/dev/ttyACM0}"
[[ -e "$PORT" ]] || PORT=/dev/ttyACM1
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
CTL="$ROOT/mayhem_serial_ctl.py"

usage() {
  cat <<EOF
Lab Mayhem app helper (serial). Requires PP_LAB_CONFIRM for TX-capable apps.
Usage:
  $0 list
  $0 start <shortname>     # e.g. capture replay lookingglass blespam ook_editor subghzd
EOF
}
cmd="${1:-}"
case "$cmd" in
  list)
    python3 "$CTL" --port "$PORT" --cmd "applist" --wait 8
    ;;
  start)
    app="${2:-}"
    [[ -n "$app" ]] || { usage; exit 1; }
    [[ -n "${PP_LAB_CONFIRM:-}" ]] || {
      echo "Set PP_LAB_CONFIRM=<engagement-id> before starting TX-capable apps" >&2
      exit 1
    }
    python3 "$CTL" --port "$PORT" --cmd "appstart $app" --wait 5
    ;;
  *) usage; exit 1 ;;
esac
