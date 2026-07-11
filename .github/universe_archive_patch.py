from pathlib import Path


# Daily capture occurs only after a successful full report. Heartbeat and all
# later persistence gates require the archive step to succeed.
daily_path = Path(".github/workflows/daily.yml")
daily = daily_path.read_text(encoding="utf-8")
if "id: universe" not in daily:
    anchor = "      - name: Write operational heartbeat\n"
    step = '''      - name: Capture immutable point-in-time universe
        id: universe
        if: steps.report.outcome == 'success'
        continue-on-error: true
        run: |
          python universe_archive.py capture \\
            --report output/daily_report.xlsx \\
            --cache data/jpx_list_cache.csv \\
            --config config.yaml \\
            --snapshot-root data/universe_snapshots \\
            --catalog data/universe_snapshot_catalog.csv \\
            --audit output/universe_archive_audit.json

'''
    if anchor not in daily:
        raise RuntimeError("daily heartbeat anchor not found")
    daily = daily.replace(anchor, step + anchor, 1)

daily = daily.replace(
    "        if: steps.report.outcome == 'success'\n        continue-on-error: true\n        env:\n          RUN_URL:",
    "        if: steps.report.outcome == 'success' && steps.universe.outcome == 'success'\n        continue-on-error: true\n        env:\n          RUN_URL:",
    1,
)

# Persist/archive the immutable universe catalog.
if "data/universe_snapshot_catalog.csv" not in daily:
    daily = daily.replace(
        "            data/jpx_list_cache.csv\n",
        "            data/jpx_list_cache.csv \\\n            data/universe_snapshot_catalog.csv \\\n            data/universe_snapshots\n",
        1,
    )
    daily = daily.replace(
        "            output/evidence_stamp_audit.json\n",
        "            output/evidence_stamp_audit.json\n            output/universe_archive_audit.json\n            data/universe_snapshot_catalog.csv\n            data/universe_snapshots/**\n",
        1,
    )

# Add universe capture to notification and final enforcement.
daily = daily.replace(
    "           steps.heartbeat.outcome == 'failure' ||",
    "           steps.universe.outcome == 'failure' ||\n           steps.heartbeat.outcome == 'failure' ||",
    2,
)
daily = daily.replace(
    "          HEARTBEAT_OUTCOME: ${{ steps.heartbeat.outcome }}\n",
    "          UNIVERSE_OUTCOME: ${{ steps.universe.outcome }}\n          HEARTBEAT_OUTCOME: ${{ steps.heartbeat.outcome }}\n",
    1,
)
if "stage=\"universe-archive\"" not in daily:
    daily = daily.replace(
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${RUNTIME_OUTCOME}\" != \"failure\" ] && [ \"${CANARY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n",
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${RUNTIME_OUTCOME}\" != \"failure\" ] && [ \"${CANARY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n"
        "          if [ \"${REPORT_OUTCOME}\" != \"failure\" ] && [ \"${UNIVERSE_OUTCOME}\" = \"failure\" ]; then stage=\"universe-archive\"; fi\n",
        1,
    )
    daily = daily.replace(
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${CANARY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n",
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${CANARY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n"
        "          if [ \"${REPORT_OUTCOME}\" != \"failure\" ] && [ \"${UNIVERSE_OUTCOME}\" = \"failure\" ]; then stage=\"universe-archive\"; fi\n",
        1,
    )
    daily = daily.replace(
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n",
        "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n"
        "          if [ \"${REPORT_OUTCOME}\" != \"failure\" ] && [ \"${UNIVERSE_OUTCOME}\" = \"failure\" ]; then stage=\"universe-archive\"; fi\n",
        1,
    )
daily_path.write_text(daily, encoding="utf-8")

# Weekly immutable archive audit is read-only.
Path(".github/workflows/universe-archive-audit.yml").write_text('''name: Point In Time Universe Archive Audit

on:
  schedule:
    # Sunday 11:00 JST.
    - cron: '0 2 * * 0'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: momentum-universe-archive-audit
  cancel-in-progress: true

jobs:
  audit:
    runs-on: ubuntu-latest
    timeout-minutes: 20
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

      - name: Validate immutable universe history
        run: |
          mkdir -p output/universe-archive
          python universe_archive.py validate \\
            --strict \\
            --catalog data/universe_snapshot_catalog.csv \\
            --output output/universe-archive/archive_validation.csv

      - name: Upload archive audit
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: universe-archive-audit-${{ github.run_id }}
          path: output/universe-archive/
          if-no-files-found: error
          retention-days: 90
''', encoding="utf-8")

# CI validates archive logic.
ci_path = Path(".github/workflows/ci.yml")
ci = ci_path.read_text(encoding="utf-8")
if "universe_archive.py" not in ci:
    ci = ci.replace(
        "          historical_backfill.py historical_price_panel.py",
        "          historical_backfill.py historical_price_panel.py universe_archive.py",
        1,
    )
if ".github/test_universe_archive.py" not in ci:
    ci = ci.replace(
        "          .github/test_historical_backfill.py",
        "          .github/test_historical_backfill.py .github/test_universe_archive.py",
        1,
    )
    step = '''      - name: Run point-in-time universe archive validation
        run: |
          set -o pipefail
          python .github/test_universe_archive.py 2>&1 | tee /tmp/universe-archive-test.log

'''
    anchor = "      - name: Upload validation failure logs\n"
    if anchor not in ci:
        raise RuntimeError("CI failure upload anchor not found")
    ci = ci.replace(anchor, step + anchor, 1)
    ci = ci.replace(
        "            /tmp/historical-backfill-test.log\n",
        "            /tmp/historical-backfill-test.log\n            /tmp/universe-archive-test.log\n",
        1,
    )
ci_path.write_text(ci, encoding="utf-8")

# Workflow policy enforces capture and read-only archive audit.
validator_path = Path(".github/validate_workflows.py")
validator = validator_path.read_text(encoding="utf-8")
if '"universe_archive.py capture"' not in validator:
    validator = validator.replace(
        '        "operations.py heartbeat",\n',
        '        "universe_archive.py capture",\n'
        '        "data/universe_snapshot_catalog.csv",\n'
        '        "data/universe_snapshots",\n'
        '        "steps.universe.outcome == \'success\'",\n'
        '        "operations.py heartbeat",\n',
        1,
    )
archive_validation = '''
    universe_audit = load_workflow("universe-archive-audit.yml")
    require("universe-archive-audit.yml", universe_audit, [
        "universe_archive.py validate",
        "--strict",
        "data/universe_snapshot_catalog.csv",
        "contents: read",
        "retention-days: 90",
    ])
    forbid("universe-archive-audit.yml", universe_audit, [
        "git push",
        "contents: write",
        "EMAIL_APP_PASSWORD",
    ])

'''
anchor = '    main_source = (ROOT / "main.py").read_text(encoding="utf-8")\n'
if "load_workflow(\"universe-archive-audit.yml\")" not in validator:
    if anchor not in validator:
        raise RuntimeError("validator main source anchor not found")
    validator = validator.replace(anchor, archive_validation + anchor, 1)
validator_path.write_text(validator, encoding="utf-8")

print("immutable universe archive integration applied")
