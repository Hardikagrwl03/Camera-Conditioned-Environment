#!/usr/bin/env bash
# One-shot Linux pipeline: fit calibration -> simulate -> validate, for every sweep that has rawframes/.
# Usage: bash scripts/run_all.sh [DEVICE_NAME]   (default device name: first folder in calibration/)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
DEV="${1:-}"
if [ -z "$DEV" ] && [ -d calibration ]; then DEV="$(ls calibration | head -n1 || true)"; fi

if [ -n "$DEV" ] && [ -d "calibration/$DEV/raw_captures" ]; then
  echo "=== fit_calibration ($DEV) ==="
  "$PY" scripts/fit_calibration.py --device-name "$DEV" --include-scene-pairs
else
  echo "=== no calibration/<device>/raw_captures: skipping fit (degraded mode: NoiseProfile + scene-pair tone curve) ==="
fi

shopt -s nullglob
n=0
for raw in rawframes/*/; do
  s="$(basename "$raw")"
  if [ ! -d "framesweeps/$s" ]; then echo "skip $s: no framesweeps/$s"; continue; fi
  echo "=== simulate_sweep $s ==="
  "$PY" scripts/simulate_sweep.py --sweep "$s"
  echo "=== validate $s ==="
  "$PY" scripts/validate.py --sweep "$s"
  echo "report: outputs/$s/report/report.html"
  n=$((n+1))
done
[ "$n" -gt 0 ] || { echo "no sweeps with rawframes/ found"; exit 1; }
