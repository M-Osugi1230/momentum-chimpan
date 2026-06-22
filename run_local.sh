#!/usr/bin/env bash
set -euo pipefail

MAX_SYMBOLS="${1:-${MOMENTUM_MAX_SYMBOLS:-3}}"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Error: python3 command was not found. Install Python 3.11+ first." >&2
  exit 127
fi

echo "Using ${PYTHON_BIN}: $(${PYTHON_BIN} --version)"
echo "Installing dependencies..."
"${PYTHON_BIN}" -m pip install -r requirements.txt

echo "Running Momentum Chimpan with MOMENTUM_MAX_SYMBOLS=${MAX_SYMBOLS}..."
MOMENTUM_MAX_SYMBOLS="${MAX_SYMBOLS}" "${PYTHON_BIN}" main.py

echo "Done. Check output/daily_report.xlsx and data/momentum_history.csv"
