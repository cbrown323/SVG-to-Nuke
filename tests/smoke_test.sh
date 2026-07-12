#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -x .venv-test/bin/python ]]; then
  PYTHON=.venv-test/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  echo "No python3 or .venv-test/bin/python found" >&2
  exit 1
fi
"$PYTHON" tests/run_smoke.py && echo "SMOKE OK"
