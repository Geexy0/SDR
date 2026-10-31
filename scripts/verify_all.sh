#!/usr/bin/env bash
# Master verification harness for pp_dashboard (offline + SPA + smoke).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "error: .venv missing — create with python -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

echo "==> Layer 1–2 + unit: pytest with coverage (≥85%, HIL deselected)"
COV_FAIL_UNDER="${COV_FAIL_UNDER:-85}"
.venv/bin/python -m pytest tests/ \
  --cov=pp_dashboard \
  --cov-report=term-missing:skip-covered \
  -q \
  --cov-fail-under="$COV_FAIL_UNDER" \
  -m "not hil"

echo "==> Layer 3: frontend typecheck + vitest + production build"
cd frontend
if [[ ! -d node_modules/vitest ]]; then
  npm install
fi
npx tsc --noEmit
npm test -- --run
npm run build
test -f dist/index.html
cd "$ROOT"

echo "==> Layer 5: full-stack smoke (PP_MOCK_HARDWARE=1)"
PORT="$(.venv/bin/python - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"
export PP_MOCK_HARDWARE=1
export PP_HOST=127.0.0.1
export PP_PORT="$PORT"
# Prefer uvicorn module path used by entrypoint
.venv/bin/python - <<PY &
import os, uvicorn
port = int(os.environ["PP_PORT"])
uvicorn.run("pp_dashboard.main:app", host="127.0.0.1", port=port, log_level="warning")
PY
SERVER_PID=$!
cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for healthz
for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null; then
    break
  fi
  sleep 0.1
done

.venv/bin/python - <<PY
import json, urllib.request
base = "http://127.0.0.1:${PORT}"
hz = json.load(urllib.request.urlopen(base + "/healthz", timeout=5))
assert hz.get("status") == "ok", hz
for path in ("/", "/api/hardware/status", "/api/recon/status", "/api/tpms/status", "/api/geoloc/status"):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        assert r.status == 200, path
print("smoke ok", hz)
PY

echo "==> verify_all OK (coverage table above; SPA dist + smoke green)"
