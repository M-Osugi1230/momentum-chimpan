#!/usr/bin/env bash
set -euo pipefail

if ! grep -q '^def seal_execution_evidence' evidence_provenance.py; then
  test -f .github/live_execution_evidence_patch.py
  python - <<'PY'
from pathlib import Path

path = Path('.github/live_execution_evidence_patch.py')
lines = path.read_text(encoding='utf-8').splitlines()
fixed = []
repairs = 0
index = 0
backslash = chr(92)
while index < len(lines):
    line = lines[index]
    stripped = line.strip()
    if (
        stripped.startswith("'--provenance output/backfill/replay/evidence_provenance.json")
        and index + 1 < len(lines)
        and '--robustness output/backfill/execution/replay_robustness_summary.csv' in lines[index + 1]
    ):
        indent = line[: len(line) - len(line.lstrip())]
        fixed.append(
            indent
            + "'--provenance output/backfill/replay/evidence_provenance.json "
            + backslash * 3
            + "n            --robustness output/backfill/execution/replay_robustness_summary.csv',"
        )
        index += 2
        repairs += 1
        continue
    if (
        stripped.startswith("'--provenance output/backfill/execution/execution_evidence_provenance.json")
        and index + 1 < len(lines)
        and '--robustness output/backfill/execution/replay_robustness_summary.csv' in lines[index + 1]
    ):
        indent = line[: len(line) - len(line.lstrip())]
        fixed.append(
            indent
            + "'--provenance output/backfill/execution/execution_evidence_provenance.json "
            + backslash * 3
            + "n            --robustness output/backfill/execution/replay_robustness_summary.csv',"
        )
        index += 2
        repairs += 1
        continue
    fixed.append(line)
    index += 1
if repairs != 2:
    raise SystemExit(f'expected two quoting repairs, found {repairs}')
path.write_text('\n'.join(fixed) + '\n', encoding='utf-8')
PY
  python -m py_compile .github/live_execution_evidence_patch.py
  python .github/live_execution_evidence_patch.py
  python - <<'PY'
from pathlib import Path

path = Path('.github/workflows/replay.yml')
lines = path.read_text(encoding='utf-8').splitlines()
fixed = []
repairs = 0
index = 0
backslash = chr(92)
while index < len(lines):
    line = lines[index]
    if (
        line.strip() == 'python robustness_analysis.py ' + backslash
        and index + 1 < len(lines)
        and lines[index + 1].strip() == '--strict ' + backslash
    ):
        indent = line[: len(line) - len(line.lstrip())]
        fixed.append(indent + 'python robustness_analysis.py --strict ' + backslash)
        index += 2
        repairs += 1
        continue
    fixed.append(line)
    index += 1
if repairs != 2:
    raise SystemExit(f'expected two robustness control repairs, found {repairs}')
path.write_text('\n'.join(fixed) + '\n', encoding='utf-8')
PY
fi

python - <<'PY'
from pathlib import Path

path = Path('.github/test_execution_provenance.py')
text = path.read_text(encoding='utf-8')
old = (
    '        "horizon_days": 5,\n'
    '        "forward_return": 0.03,\n'
    '        "excess_vs_universe": 0.01,\n'
    '        "entry_slippage_bps": 5.0,\n'
)
new = (
    '        "horizon_days": 5,\n'
    '        "entry_gap_return": 0.01,\n'
    '        "execution_status": "NORMAL",\n'
    '        "close_based_forward_return": 0.035,\n'
    '        "next_open_gross_return": 0.033,\n'
    '        "implementation_shortfall": -0.002,\n'
    '        "forward_return": 0.03,\n'
    '        "excess_vs_universe": 0.01,\n'
    '        "beat_universe": True,\n'
    '        "entry_slippage_bps": 5.0,\n'
)
if old in text:
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
elif '"implementation_shortfall": -0.002' not in text:
    raise SystemExit('execution fixture is neither original nor completed')
PY

python -m py_compile \
  main.py replay.py research_scorecard.py robustness_analysis.py \
  strategy_governance.py evidence_provenance.py live_execution_panel.py \
  execution_realism.py operations.py .github/validate_workflows.py \
  .github/test_walk_forward_replay.py .github/test_research_scorecard.py \
  .github/test_robustness_analysis.py .github/test_strategy_governance.py \
  .github/test_evidence_provenance.py .github/test_live_execution_panel.py \
  .github/test_execution_provenance.py .github/test_execution_realism.py \
  .github/test_operations.py
python .github/validate_workflows.py
python .github/test_walk_forward_replay.py
python .github/test_research_scorecard.py
python .github/test_robustness_analysis.py
python .github/test_strategy_governance.py
python .github/test_evidence_provenance.py
python .github/test_live_execution_panel.py
python .github/test_execution_provenance.py
python .github/test_execution_realism.py
python .github/test_operations.py

rm -f .github/live_execution_evidence_patch.py
rm -f .github/live-execution-finalize-trigger.txt
rm -f .github/finalize_live_execution_evidence.sh
rm -f .github/workflows/build-live-execution-evidence.yml
rm -f .github/workflows/fix-live-execution-test.yml
rm -f .github/workflows/finalize-live-execution-evidence.yml
rm -f .github/workflows/finalize-live-execution-evidence-v2.yml
rm -f .github/workflows/complete-live-execution-evidence.yml

git diff --check
tar --exclude=.git --exclude=output --exclude='*.pyc' --exclude=__pycache__ -czf /tmp/live-execution-final-tree.tar.gz .
