from pathlib import Path
import yaml


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Execution outputs inherit the sealed source provenance instead of being
# permanently hard-coded as non-promotable.
replace_once(
    "execution_realism.py",
    '        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,\n'
    '        "promotion_evidence_allowed": False,\n',
    '        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,\n'
    '        "promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,\n'
    '        "strategy_fingerprint": provenance.get("strategy_fingerprint", ""),\n',
)

# Add execution provenance sealing and policy enforcement.
replace_once(
    "evidence_provenance.py",
    'BACKFILL_ORIGIN = "HISTORICAL_CURRENT_UNIVERSE_BACKFILL"\nALLOWED_LIVE_SOURCE',
    'BACKFILL_ORIGIN = "HISTORICAL_CURRENT_UNIVERSE_BACKFILL"\n'
    'EXECUTION_ORIGIN = "LIVE_FORWARD_NEXT_OPEN_EXECUTION"\n'
    'REQUIRED_EXECUTION_MODEL = "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"\n'
    'ALLOWED_LIVE_SOURCE',
)

execution_function = r'''

def _finite_number(value: Any) -> bool:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return pd.notna(converted) and float(converted) >= 0


def seal_execution_evidence(
    source_provenance_path: str,
    execution_manifest_path: str,
    provenance_path: str,
) -> dict[str, Any]:
    source = load_json(source_provenance_path)
    execution = load_json(execution_manifest_path)
    current = current_strategy_fingerprint()
    source_fingerprint = str(source.get("strategy_fingerprint", ""))
    execution_fingerprint = str(execution.get("strategy_fingerprint", source_fingerprint))
    execution_model = str(execution.get("entry_model", ""))
    same_day_allowed = execution.get("same_day_close_entry_allowed")
    outcome_count = int(pd.to_numeric(pd.Series([execution.get("outcome_count")]), errors="coerce").fillna(0).iloc[0])
    cost_fields = {
        "entry_slippage_bps": execution.get("default_entry_slippage_bps"),
        "exit_slippage_bps": execution.get("default_exit_slippage_bps"),
        "fees_bps": execution.get("default_fees_bps"),
    }
    controls_valid = bool(
        execution_model == REQUIRED_EXECUTION_MODEL
        and same_day_allowed is False
        and all(_finite_number(value) for value in cost_fields.values())
        and outcome_count > 0
    )
    promotion_allowed = bool(
        source.get("promotion_evidence_allowed") is True
        and execution.get("promotion_evidence_allowed") is True
        and source_fingerprint == current
        and execution_fingerprint == current
        and controls_valid
    )
    payload = {
        "provenance_version": PROVENANCE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_origin": source.get("evidence_origin", ""),
        "execution_origin": EXECUTION_ORIGIN,
        "execution_evidence": True,
        "promotion_evidence_allowed": promotion_allowed,
        "strategy_fingerprint": source_fingerprint,
        "source_path": source.get("source_path", ""),
        "source_provenance_path": source_provenance_path,
        "source_provenance_sha256": sha256_file(source_provenance_path),
        "execution_manifest_path": execution_manifest_path,
        "execution_manifest_sha256": sha256_file(execution_manifest_path),
        "execution_model": execution_model,
        "same_day_close_entry_allowed": same_day_allowed,
        "entry_slippage_bps": cost_fields["entry_slippage_bps"],
        "exit_slippage_bps": cost_fields["exit_slippage_bps"],
        "fees_bps": cost_fields["fees_bps"],
        "execution_outcome_count": outcome_count,
        "execution_controls_valid": controls_valid,
        "bias_flags": source.get("bias_flags", []),
        "research_only": True,
    }
    atomic_write_json(payload, provenance_path)
    return payload


'''
replace_once(
    "evidence_provenance.py",
    "def provenance_valid(provenance: dict[str, Any], registry: dict[str, Any]) -> tuple[bool, str]:\n",
    execution_function + "def provenance_valid(provenance: dict[str, Any], registry: dict[str, Any]) -> tuple[bool, str]:\n",
)

replace_once(
    "evidence_provenance.py",
    '    if origin == LIVE_ORIGIN and str(provenance.get("source_path", "")) != ALLOWED_LIVE_SOURCE:\n'
    '        return False, "live evidence source path is not the governed ranking history"\n'
    '    return True, "promotion provenance is valid"\n',
    '    if origin == LIVE_ORIGIN and str(provenance.get("source_path", "")) != ALLOWED_LIVE_SOURCE:\n'
    '        return False, "live evidence source path is not the governed ranking history"\n'
    '    required_execution_model = str(policy.get("required_promotion_execution_model", "")).strip()\n'
    '    if required_execution_model:\n'
    '        if provenance.get("execution_evidence") is not True:\n'
    '            return False, "promotion evidence is not sealed execution evidence"\n'
    '        if str(provenance.get("execution_model", "")) != required_execution_model:\n'
    '            return False, "execution model does not satisfy promotion policy"\n'
    '        if provenance.get("same_day_close_entry_allowed") is not False:\n'
    '            return False, "same-day close entry is not allowed for promotion"\n'
    '        for field in ("entry_slippage_bps", "exit_slippage_bps", "fees_bps"):\n'
    '            if not _finite_number(provenance.get(field)):\n'
    '                return False, f"execution cost control is missing or invalid: {field}"\n'
    '        if int(pd.to_numeric(pd.Series([provenance.get("execution_outcome_count")]), errors="coerce").fillna(0).iloc[0]) <= 0:\n'
    '            return False, "execution evidence has no outcomes"\n'
    '    return True, "promotion provenance is valid"\n',
)

replace_once(
    "evidence_provenance.py",
    '    derived = sub.add_parser("seal-derived")\n'
    '    derived.add_argument("--source-manifest", required=True)\n'
    '    derived.add_argument("--provenance", required=True)\n\n'
    '    audit = sub.add_parser("governance-audit")\n',
    '    derived = sub.add_parser("seal-derived")\n'
    '    derived.add_argument("--source-manifest", required=True)\n'
    '    derived.add_argument("--provenance", required=True)\n\n'
    '    execution = sub.add_parser("seal-execution")\n'
    '    execution.add_argument("--source-provenance", required=True)\n'
    '    execution.add_argument("--execution-manifest", required=True)\n'
    '    execution.add_argument("--provenance", required=True)\n\n'
    '    audit = sub.add_parser("governance-audit")\n',
)

replace_once(
    "evidence_provenance.py",
    '    elif args.command == "seal-derived":\n'
    '        result = seal_derived_backfill(args.source_manifest, args.provenance)\n'
    '    else:\n',
    '    elif args.command == "seal-derived":\n'
    '        result = seal_derived_backfill(args.source_manifest, args.provenance)\n'
    '    elif args.command == "seal-execution":\n'
    '        result = seal_execution_evidence(\n'
    '            args.source_provenance, args.execution_manifest, args.provenance\n'
    '        )\n'
    '    else:\n',
)

# Promotion policy now requires executable next-session evidence.
registry_path = Path("research/experiment_registry.yaml")
registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
registry.setdefault("policy", {})["required_promotion_execution_model"] = "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"
registry_path.write_text(yaml.safe_dump(registry, sort_keys=False, allow_unicode=True), encoding="utf-8")

# Weekly workflow: keep close-based diagnostics, but govern promotion using
# next-session execution outcomes and sealed execution provenance only.
Path(".github/workflows/replay.yml").write_text(r'''name: Walk Forward Replay

on:
  schedule:
    # Saturday 09:30 JST, after the Friday market data is persisted.
    - cron: '30 0 * * 6'
  workflow_dispatch:
    inputs:
      max_dates:
        description: 'Replay the latest N eligible report dates (blank = all)'
        required: false
        type: string

permissions:
  contents: read

concurrency:
  group: momentum-walk-forward-replay
  cancel-in-progress: true

jobs:
  replay:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt

      - name: Prepare current-strategy live evidence history
        id: provenance
        run: |
          mkdir -p output/replay output/replay/execution
          python strategy_governance.py snapshot \
            --output output/replay/current_strategy_fingerprint.json
          python evidence_provenance.py prepare-live \
            --ranking data/momentum_daily_ranking.csv \
            --output output/replay/live_strategy_history.csv \
            --provenance output/replay/evidence_provenance.json \
            --fingerprint output/replay/current_strategy_fingerprint.json | tee output/replay/provenance.log
          eligible_dates=$(python - <<'PY'
          import json
          from pathlib import Path
          payload = json.loads(Path('output/replay/evidence_provenance.json').read_text(encoding='utf-8'))
          print(int(payload.get('eligible_date_count', 0)))
          PY
          )
          echo "eligible_dates=${eligible_dates}" >> "${GITHUB_OUTPUT}"

      - name: Record insufficient live history
        if: fromJSON(steps.provenance.outputs.eligible_dates) < 2
        run: |
          cat > output/replay/insufficient-live-evidence.txt <<EOF
          Current strategy has fewer than two fingerprint-stamped report dates.
          Replay and execution evidence are intentionally deferred.
          Old-strategy and unstamped rows are not reused.
          EOF

      - name: Run no-lookahead close-based diagnostic replay
        if: fromJSON(steps.provenance.outputs.eligible_dates) >= 2
        env:
          REPLAY_MAX_DATES: ${{ inputs.max_dates }}
        run: |
          args=(--strict --history output/replay/live_strategy_history.csv)
          if [ -n "${REPLAY_MAX_DATES}" ]; then
            args+=(--max-dates "${REPLAY_MAX_DATES}")
          fi
          python replay.py "${args[@]}" | tee output/replay/run.log

      - name: Inspect replay signals
        id: replay_state
        run: |
          signal_count=$(python - <<'PY'
          from pathlib import Path
          import pandas as pd
          path = Path('output/replay/replay_signals.csv')
          print(len(pd.read_csv(path)) if path.exists() else 0)
          PY
          )
          echo "signal_count=${signal_count}" >> "${GITHUB_OUTPUT}"

      - name: Build close-based diagnostic evidence
        if: fromJSON(steps.replay_state.outputs.signal_count) > 0
        run: |
          set -o pipefail
          python research_scorecard.py \
            --strict \
            --outcomes output/replay/replay_outcomes.csv \
            --history output/replay/live_strategy_history.csv \
            --output-dir output/replay | tee output/replay/close-evidence.log
          python robustness_analysis.py \
            --strict \
            --input output/replay/replay_benchmarked_outcomes.csv \
            --output-dir output/replay | tee output/replay/close-robustness.log

      - name: Download isolated live execution prices
        if: fromJSON(steps.replay_state.outputs.signal_count) > 0
        run: |
          set -o pipefail
          python live_execution_panel.py \
            --strict \
            --signals output/replay/replay_signals.csv \
            --provenance output/replay/evidence_provenance.json \
            --output output/replay/live_execution_price_panel.csv \
            --manifest output/replay/live_execution_price_panel_manifest.json | tee output/replay/execution-panel.log

      - name: Simulate next-session executable evidence
        if: fromJSON(steps.replay_state.outputs.signal_count) > 0
        run: |
          set -o pipefail
          python execution_realism.py \
            --signals output/replay/replay_signals.csv \
            --prices output/replay/live_execution_price_panel.csv \
            --ranking output/replay/live_strategy_history.csv \
            --provenance output/replay/evidence_provenance.json \
            --output-dir output/replay/execution | tee output/replay/execution/execution.log

      - name: Inspect execution outcomes
        id: execution_state
        run: |
          outcome_count=$(python - <<'PY'
          import json
          from pathlib import Path
          path = Path('output/replay/execution/execution_realism_manifest.json')
          payload = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
          print(int(payload.get('outcome_count', 0)))
          PY
          )
          echo "outcome_count=${outcome_count}" >> "${GITHUB_OUTPUT}"

      - name: Stress-test already-net execution evidence
        if: fromJSON(steps.execution_state.outputs.outcome_count) > 0
        run: |
          set -o pipefail
          python robustness_analysis.py \
            --strict \
            --base-cost-bps 0 \
            --input output/replay/execution/execution_benchmarked_outcomes.csv \
            --output-dir output/replay/execution | tee output/replay/execution/robustness.log

      - name: Seal execution evidence provenance
        run: |
          if [ -f output/replay/execution/execution_realism_manifest.json ]; then
            python evidence_provenance.py seal-execution \
              --source-provenance output/replay/evidence_provenance.json \
              --execution-manifest output/replay/execution/execution_realism_manifest.json \
              --provenance output/replay/execution_evidence_provenance.json | tee output/replay/execution-provenance.log
          else
            cp output/replay/evidence_provenance.json output/replay/execution_evidence_provenance.json
          fi

      - name: Record insufficient executable evidence
        if: fromJSON(steps.execution_state.outputs.outcome_count) < 100
        run: |
          cat > output/replay/insufficient-execution-evidence.txt <<EOF
          Current strategy has fewer than 100 next-session executable outcomes.
          Close-based diagnostics are not eligible for strategy promotion.
          Promotion remains blocked until execution evidence meets policy.
          EOF

      - name: Audit strategy experiments using execution evidence only
        run: |
          robustness_path=output/replay/execution/replay_robustness_summary.csv
          if [ ! -f "${robustness_path}" ]; then
            robustness_path=output/replay/execution/no_robustness_available.csv
          fi
          python evidence_provenance.py governance-audit \
            --strict \
            --provenance output/replay/execution_evidence_provenance.json \
            --robustness "${robustness_path}" \
            --output-dir output/replay/execution | tee output/replay/execution/strategy-governance.log

      - name: Upload replay research artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: walk-forward-replay-${{ github.run_number }}
          path: output/replay/
          if-no-files-found: error
          retention-days: 90
''', encoding="utf-8")

# Historical backfill also seals its execution result, which remains
# non-promotable because the source provenance is biased backfill evidence.
historical = Path(".github/workflows/historical-backfill.yml")
historical_text = historical.read_text(encoding="utf-8")
anchor = '''      - name: Stress-test already-net execution evidence
        run: |
'''
insert = '''      - name: Seal historical execution provenance
        run: |
          python evidence_provenance.py seal-execution \\
            --source-provenance output/backfill/replay/evidence_provenance.json \\
            --execution-manifest output/backfill/execution/execution_realism_manifest.json \\
            --provenance output/backfill/execution/execution_evidence_provenance.json | tee output/backfill/execution/execution-provenance.log

'''
if anchor not in historical_text:
    raise RuntimeError("historical execution stress anchor not found")
historical_text = historical_text.replace(anchor, insert + anchor, 1)
historical_text = historical_text.replace(
    '--provenance output/backfill/replay/evidence_provenance.json \\
            --robustness output/backfill/execution/replay_robustness_summary.csv',
    '--provenance output/backfill/execution/execution_evidence_provenance.json \\
            --robustness output/backfill/execution/replay_robustness_summary.csv',
    1,
)
historical_text = historical_text.replace(
    "          execution = json.loads(Path('output/backfill/execution/execution_realism_manifest.json').read_text(encoding='utf-8'))\n",
    "          execution = json.loads(Path('output/backfill/execution/execution_realism_manifest.json').read_text(encoding='utf-8'))\n"
    "          execution_provenance = json.loads(Path('output/backfill/execution/execution_evidence_provenance.json').read_text(encoding='utf-8'))\n",
    1,
)
historical_text = historical_text.replace(
    "          assert execution['production_state_mutations'] == []\n",
    "          assert execution['production_state_mutations'] == []\n"
    "          assert execution_provenance['execution_evidence'] is True\n"
    "          assert execution_provenance['promotion_evidence_allowed'] is False\n"
    "          assert execution_provenance['execution_model'] == 'NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN'\n",
    1,
)
historical.write_text(historical_text, encoding="utf-8")

# Permanent CI compile and execute the new live execution controls.
ci_path = Path(".github/workflows/ci.yml")
ci = ci_path.read_text(encoding="utf-8")
ci = ci.replace(
    "          smoke_validate.py historical_backfill.py historical_price_panel.py\n          execution_realism.py capacity_analysis.py .github/validate_workflows.py\n",
    "          smoke_validate.py historical_backfill.py historical_price_panel.py\n          live_execution_panel.py execution_realism.py capacity_analysis.py .github/validate_workflows.py\n",
    1,
)
ci = ci.replace(
    "          .github/test_historical_backfill.py .github/test_execution_realism.py\n          .github/test_capacity_analysis.py\n",
    "          .github/test_historical_backfill.py .github/test_execution_realism.py\n          .github/test_live_execution_panel.py .github/test_execution_provenance.py\n          .github/test_capacity_analysis.py\n",
    1,
)
new_steps = '''      - name: Run live execution panel validation
        run: |
          set -o pipefail
          python .github/test_live_execution_panel.py 2>&1 | tee /tmp/live-execution-panel-test.log

      - name: Run live execution provenance validation
        run: |
          set -o pipefail
          python .github/test_execution_provenance.py 2>&1 | tee /tmp/execution-provenance-test.log

'''
anchor_step = "      - name: Run operational controls validation\n"
if anchor_step not in ci:
    raise RuntimeError("CI operational step anchor not found")
ci = ci.replace(anchor_step, new_steps + anchor_step, 1)
ci = ci.replace(
    "            /tmp/evidence-provenance-test.log\n",
    "            /tmp/evidence-provenance-test.log\n"
    "            /tmp/live-execution-panel-test.log\n"
    "            /tmp/execution-provenance-test.log\n",
    1,
)
ci_path.write_text(ci, encoding="utf-8")

# Workflow policy checks now require the live execution promotion path.
validator = Path(".github/validate_workflows.py")
validator_text = validator.read_text(encoding="utf-8")
validator_text = validator_text.replace(
    '        "evidence_provenance.py governance-audit",\n        "evidence_provenance.json",\n        "insufficient-live-evidence.txt",\n',
    '        "python live_execution_panel.py",\n'
    '        "python execution_realism.py",\n'
    '        "--base-cost-bps 0",\n'
    '        "evidence_provenance.py seal-execution",\n'
    '        "execution_evidence_provenance.json",\n'
    '        "evidence_provenance.py governance-audit",\n'
    '        "evidence_provenance.json",\n'
    '        "insufficient-live-evidence.txt",\n'
    '        "insufficient-execution-evidence.txt",\n',
    1,
)
validator_text = validator_text.replace(
    '        "python capacity_analysis.py",\n',
    '        "evidence_provenance.py seal-execution",\n'
    '        "execution_evidence_provenance.json",\n'
    '        "python capacity_analysis.py",\n',
    1,
)
validator_text = validator_text.replace(
    '    assert policy["allowed_promotion_evidence_origins"] == ["LIVE_FORWARD_RANKING_HISTORY"]\n',
    '    assert policy["allowed_promotion_evidence_origins"] == ["LIVE_FORWARD_RANKING_HISTORY"]\n'
    '    assert policy["required_promotion_execution_model"] == "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"\n',
    1,
)
validator.write_text(validator_text, encoding="utf-8")

print("live execution evidence patch applied")
