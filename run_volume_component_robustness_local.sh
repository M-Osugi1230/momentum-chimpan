#!/usr/bin/env bash
set -euo pipefail

FOLD_COUNT="${1:-3}"
SYMBOLS_PER_FOLD="${2:-48}"
LOOKBACK_DAYS="${3:-420}"
SAMPLE_EVERY="${4:-5}"
BATCH_SIZE="${5:-48}"
OUTPUT_ROOT="${MOMENTUM_VOLUME_OUTPUT_ROOT:-output/volume_component_local}"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Error: Python was not found. Install Python 3.12 first." >&2
  exit 127
fi

for required in \
  data/jpx_list_cache.csv \
  config.yaml \
  requirements.txt \
  research/volume_component_robustness.yaml \
  volume_component_robustness.py \
  volume_component_aggregate_guard.py \
  historical_backfill.py \
  historical_price_panel.py \
  evidence_provenance.py; do
  if [[ ! -f "${required}" ]]; then
    echo "Error: required file is missing: ${required}" >&2
    exit 2
  fi
done

echo "Using ${PYTHON_BIN}: $(${PYTHON_BIN} --version)"
echo "Output root: ${OUTPUT_ROOT}"
echo "Folds: ${FOLD_COUNT} x ${SYMBOLS_PER_FOLD} symbols"
echo "Lookback: ${LOOKBACK_DAYS} calendar days / snapshot every ${SAMPLE_EVERY} sessions"

if [[ "${MOMENTUM_SKIP_INSTALL:-0}" != "1" ]]; then
  echo "Installing locked dependencies..."
  if [[ -f requirements.lock ]]; then
    "${PYTHON_BIN}" -m pip install -r requirements.txt -c requirements.lock
  else
    "${PYTHON_BIN}" -m pip install -r requirements.txt
  fi
fi

echo "Running compile and synthetic guards..."
"${PYTHON_BIN}" -m py_compile \
  volume_component_robustness.py \
  volume_component_aggregate_guard.py \
  score_component_ablation.py \
  historical_backfill.py \
  historical_price_panel.py \
  replay.py \
  portfolio_research.py \
  portfolio_exit_lab.py \
  portfolio_regime_attribution.py \
  main.py \
  .github/test_volume_component_robustness.py \
  .github/test_volume_component_aggregate_guard.py
"${PYTHON_BIN}" .github/test_volume_component_robustness.py
"${PYTHON_BIN}" .github/test_volume_component_aggregate_guard.py

rm -rf "${OUTPUT_ROOT}"
mkdir -p "${OUTPUT_ROOT}/folds" "${OUTPUT_ROOT}/report"
printf 'run_mode=local_manual\n' > "${OUTPUT_ROOT}/run-mode.txt"

echo "Preparing disjoint sector-stratified folds..."
"${PYTHON_BIN}" volume_component_robustness.py prepare-folds \
  --cache data/jpx_list_cache.csv \
  --config config.yaml \
  --output-dir "${OUTPUT_ROOT}/folds" \
  --fold-count "${FOLD_COUNT}" \
  --symbols-per-fold "${SYMBOLS_PER_FOLD}"

processed_folds=0
for fold_dir in "${OUTPUT_ROOT}"/folds/fold_*; do
  [[ -d "${fold_dir}" ]] || continue
  processed_folds=$((processed_folds + 1))
  fold_id="$(basename "${fold_dir}")"
  mkdir -p "${fold_dir}/backfill/replay" "${fold_dir}/analysis"
  echo "Analyzing ${fold_id}..."

  "${PYTHON_BIN}" historical_backfill.py \
    --strict \
    --cache "${fold_dir}/jpx_fold_cache.csv" \
    --max-symbols 0 \
    --lookback-calendar-days "${LOOKBACK_DAYS}" \
    --sample-every "${SAMPLE_EVERY}" \
    --batch-size "${BATCH_SIZE}" \
    --output-dir "${fold_dir}/backfill" \
    2>&1 | tee "${fold_dir}/backfill/backfill.log"

  "${PYTHON_BIN}" evidence_provenance.py seal-derived \
    --source-manifest "${fold_dir}/backfill/backfill_manifest.json" \
    --provenance "${fold_dir}/backfill/replay/evidence_provenance.json"

  "${PYTHON_BIN}" historical_price_panel.py \
    --strict \
    --history "${fold_dir}/backfill/historical_ranking.csv" \
    --backfill-manifest "${fold_dir}/backfill/backfill_manifest.json" \
    --output "${fold_dir}/backfill/historical_price_panel.csv" \
    --manifest "${fold_dir}/backfill/historical_price_panel_manifest.json" \
    --batch-size "${BATCH_SIZE}" \
    2>&1 | tee "${fold_dir}/backfill/price-panel.log"

  "${PYTHON_BIN}" volume_component_robustness.py analyze-fold \
    --strict \
    --history "${fold_dir}/backfill/historical_ranking.csv" \
    --prices "${fold_dir}/backfill/historical_price_panel.csv" \
    --provenance "${fold_dir}/backfill/replay/evidence_provenance.json" \
    --fold-manifest "${fold_dir}/fold_manifest.json" \
    --registry research/volume_component_robustness.yaml \
    --output-dir "${fold_dir}/analysis" \
    --top-limit 100 \
    2>&1 | tee "${fold_dir}/analysis/analysis.log"
done

if [[ "${processed_folds}" -ne "${FOLD_COUNT}" ]]; then
  echo "Error: processed ${processed_folds} folds; expected ${FOLD_COUNT}." >&2
  exit 3
fi

echo "Aggregating complete-case cross-fold evidence..."
"${PYTHON_BIN}" volume_component_aggregate_guard.py \
  --strict \
  --fold-root "${OUTPUT_ROOT}/folds" \
  --registry research/volume_component_robustness.yaml \
  --output-dir "${OUTPUT_ROOT}/report" \
  2>&1 | tee "${OUTPUT_ROOT}/report/aggregate.log"

echo
echo "Cross-fold summary:"
"${PYTHON_BIN}" - <<PY
from pathlib import Path
import pandas as pd

root = Path("${OUTPUT_ROOT}")
summary = pd.read_csv(root / "report" / "volume_component_robustness_summary.csv")
folds = pd.read_csv(root / "report" / "volume_cross_fold_summary.csv")
print(folds[[
    "fold_id",
    "baseline_full_trades",
    "tested_full_trades",
    "delta_excess_return",
    "delta_max_drawdown",
    "early_delta_excess",
    "late_delta_excess",
    "fold_status",
]].to_string(index=False))
print()
print(summary.to_string(index=False))
PY

echo
echo "Completed without production-state mutation."
echo "Excel: ${OUTPUT_ROOT}/report/volume_component_robustness.xlsx"
echo "Manifest: ${OUTPUT_ROOT}/report/volume_component_robustness_manifest.json"
