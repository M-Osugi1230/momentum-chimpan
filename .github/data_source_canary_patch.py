from pathlib import Path


# Correct the deterministic sector order expectation in the synthetic test.
test_path = Path(".github/test_data_source_canary.py")
test = test_path.read_text(encoding="utf-8")
test = test.replace(
    'assert [member.code for member in sample] == ["2001", "1001", "3001"]',
    'assert [member.code for member in sample] == ["3001", "2001", "1001"]',
    1,
)
test_path.write_text(test, encoding="utf-8")

# Daily canary runs after strategy/runtime preflight and before the full report.
daily_path = Path(".github/workflows/daily.yml")
daily = daily_path.read_text(encoding="utf-8")
if "id: canary" not in daily:
    anchor = "      - name: Run report\n"
    step = '''      - name: Preflight external market data source
        id: canary
        if: >-
          steps.strategy.outcome == 'success' &&
          steps.runtime.outcome != 'failure'
        continue-on-error: true
        run: |
          python data_source_canary.py \\
            --strict \\
            --sample-size 8 \\
            --compare-count 2 \\
            --batch-size 8 \\
            --lookback-days 120 \\
            --output-dir output/data-source-canary

'''
    if anchor not in daily:
        raise RuntimeError("daily report anchor not found")
    daily = daily.replace(anchor, step + anchor, 1)

# Add canary success to the report gate for both runtime-enabled and older daily workflows.
daily = daily.replace(
    "steps.runtime.outcome == 'success'\n        continue-on-error: true\n        env:",
    "steps.runtime.outcome == 'success' &&\n          steps.canary.outcome == 'success'\n        continue-on-error: true\n        env:",
    1,
)
daily = daily.replace(
    "if: steps.strategy.outcome == 'success'\n        continue-on-error: true\n        env:",
    "if: steps.strategy.outcome == 'success' && steps.canary.outcome == 'success'\n        continue-on-error: true\n        env:",
    1,
)

# Include canary failure in operational notification and final enforcement.
daily = daily.replace(
    "           steps.report.outcome == 'failure' ||",
    "           steps.canary.outcome == 'failure' ||\n           steps.report.outcome == 'failure' ||",
    2,
)
daily = daily.replace(
    "          REPORT_OUTCOME: ${{ steps.report.outcome }}\n",
    "          CANARY_OUTCOME: ${{ steps.canary.outcome }}\n          REPORT_OUTCOME: ${{ steps.report.outcome }}\n",
    1,
)
if "stage=\"data-source-canary\"" not in daily:
    daily = daily.replace(
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${RUNTIME_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n",
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${RUNTIME_OUTCOME}\" != \"failure\" ] && [ \"${CANARY_OUTCOME}\" = \"failure\" ]; then stage=\"data-source-canary\"; fi\n"
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${RUNTIME_OUTCOME}\" != \"failure\" ] && [ \"${CANARY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n",
        1,
    )
    daily = daily.replace(
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n",
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${CANARY_OUTCOME}\" = \"failure\" ]; then stage=\"data-source-canary\"; fi\n"
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${CANARY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n",
        1,
    )

# Add diagnostics to the existing always-uploaded artifact.
if "output/data-source-canary/**" not in daily:
    daily = daily.replace(
        "            output/daily_report.xlsx\n",
        "            output/daily_report.xlsx\n            output/data-source-canary/**\n",
        1,
    )
daily_path.write_text(daily, encoding="utf-8")

# Independent read-only canary catches provider changes outside daily execution.
Path(".github/workflows/data-source-canary.yml").write_text('''name: External Data Source Canary

on:
  schedule:
    # 16:20 JST, before the full daily report.
    - cron: '20 7 * * 1-5'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: momentum-external-data-source-canary
  cancel-in-progress: true

jobs:
  canary:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          ref: main

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements.lock ]; then
            python -m pip install -r requirements.txt -c requirements.lock
          else
            python -m pip install -r requirements.txt
          fi

      - name: Run live external data checks
        id: canary
        continue-on-error: true
        run: |
          mkdir -p output/data-source-canary
          set -o pipefail
          python data_source_canary.py \\
            --strict \\
            --sample-size 12 \\
            --compare-count 3 \\
            --batch-size 12 \\
            --lookback-days 120 \\
            --output-dir output/data-source-canary | tee output/data-source-canary/canary.log

      - name: Upload data-source diagnostics
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: external-data-source-canary-${{ github.run_id }}
          path: output/data-source-canary/
          if-no-files-found: error
          retention-days: 90

      - name: Enforce canary success
        if: steps.canary.outcome == 'failure'
        run: |
          echo "External market data canary failed. Review the artifact before relying on the daily report." >&2
          exit 1
''', encoding="utf-8")

# Permanent CI compiles/tests the canary.
ci_path = Path(".github/workflows/ci.yml")
ci = ci_path.read_text(encoding="utf-8")
if "data_source_canary.py" not in ci:
    ci = ci.replace(
        "          historical_backfill.py historical_price_panel.py",
        "          historical_backfill.py historical_price_panel.py data_source_canary.py",
        1,
    )
if ".github/test_data_source_canary.py" not in ci:
    ci = ci.replace(
        "          .github/test_historical_backfill.py",
        "          .github/test_historical_backfill.py .github/test_data_source_canary.py",
        1,
    )
    step = '''      - name: Run external data canary validation
        run: |
          set -o pipefail
          python .github/test_data_source_canary.py 2>&1 | tee /tmp/data-source-canary-test.log

'''
    anchor = "      - name: Upload validation failure logs\n"
    if anchor not in ci:
        raise RuntimeError("CI failure upload anchor not found")
    ci = ci.replace(anchor, step + anchor, 1)
    ci = ci.replace(
        "            /tmp/historical-backfill-test.log\n",
        "            /tmp/historical-backfill-test.log\n            /tmp/data-source-canary-test.log\n",
        1,
    )
ci_path.write_text(ci, encoding="utf-8")

# Workflow policy enforces both preflight and standalone read-only canary.
validator_path = Path(".github/validate_workflows.py")
validator = validator_path.read_text(encoding="utf-8")
if '"data_source_canary.py"' not in validator:
    validator = validator.replace(
        '        "strategy_governance.py snapshot",\n',
        '        "strategy_governance.py snapshot",\n'
        '        "data_source_canary.py",\n'
        '        "steps.canary.outcome == \'success\'",\n'
        '        "output/data-source-canary/**",\n',
        1,
    )
canary_validation = '''
    data_canary = load_workflow("data-source-canary.yml")
    require("data-source-canary.yml", data_canary, [
        "python data_source_canary.py",
        "--sample-size 12",
        "--compare-count 3",
        "contents: read",
        "retention-days: 90",
    ])
    forbid("data-source-canary.yml", data_canary, [
        "git push",
        "contents: write",
        "EMAIL_APP_PASSWORD",
    ])

'''
anchor = '    main_source = (ROOT / "main.py").read_text(encoding="utf-8")\n'
if "load_workflow(\"data-source-canary.yml\")" not in validator:
    if anchor not in validator:
        raise RuntimeError("validator main source anchor not found")
    validator = validator.replace(anchor, canary_validation + anchor, 1)
validator_path.write_text(validator, encoding="utf-8")

print("external data source canary integration applied")
