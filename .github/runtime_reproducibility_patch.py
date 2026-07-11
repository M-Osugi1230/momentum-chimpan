from pathlib import Path
import re


# Use the evidence provenance wrapper that is already governed and tested.
runtime_path = Path("runtime_provenance.py")
runtime = runtime_path.read_text(encoding="utf-8")
runtime = runtime.replace("import strategy_governance\n", "import evidence_provenance\n", 1)
runtime = runtime.replace(
    "strategy_fingerprint = strategy_governance.current_strategy_fingerprint()",
    "strategy_fingerprint = evidence_provenance.current_strategy_fingerprint()",
    1,
)
runtime_path.write_text(runtime, encoding="utf-8")

# Every maintained workflow that installs project requirements must use the
# committed constraints lock. Temporary completion workflows are removed before
# merge, so changing them is harmless for the current in-flight run.
workflow_root = Path(".github/workflows")
for path in workflow_root.glob("*.yml"):
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r"python -m pip install -r requirements\.txt(?! -c requirements\.lock)",
        "python -m pip install -r requirements.txt -c requirements.lock",
        text,
    )
    text = re.sub(
        r"(?m)^([ \t]*)pip install -r requirements\.txt$",
        r"\1python -m pip install -r requirements.txt -c requirements.lock",
        text,
    )
    path.write_text(text, encoding="utf-8")

# Daily runtime provenance is created before the report and becomes a persisted
# operational state file. Drift is recorded but does not alter strategy rules.
daily_path = Path(".github/workflows/daily.yml")
daily = daily_path.read_text(encoding="utf-8")
if "id: runtime" not in daily:
    anchor = "      - name: Run report\n"
    step = '''      - name: Capture locked runtime provenance
        id: runtime
        if: steps.strategy.outcome == 'success'
        continue-on-error: true
        run: |
          python runtime_provenance.py \
            --strict \
            --previous data/runtime_provenance.json \
            --output data/runtime_provenance.json \
            --freeze-output output/runtime_pip_freeze.txt

'''
    if anchor not in daily:
        raise RuntimeError("daily report step anchor not found")
    daily = daily.replace(anchor, step + anchor, 1)

daily = daily.replace(
    "        if: steps.strategy.outcome == 'success'\n        continue-on-error: true\n        env:\n",
    "        if: steps.strategy.outcome == 'success' && steps.runtime.outcome == 'success'\n        continue-on-error: true\n        env:\n",
    1,
)
# Persist and artifact the provenance file.
if "data/runtime_provenance.json" not in daily:
    daily = daily.replace(
        "            data/strategy_fingerprint.json \\\n",
        "            data/strategy_fingerprint.json \\\n            data/runtime_provenance.json \\\n",
        1,
    )
    daily = daily.replace(
        "            data/strategy_fingerprint.json\n",
        "            data/strategy_fingerprint.json\n            data/runtime_provenance.json\n            output/runtime_pip_freeze.txt\n",
        1,
    )
# Include runtime failure in notification and final gate.
daily = daily.replace(
    "          (steps.strategy.outcome == 'failure' ||\n           steps.report.outcome == 'failure' ||",
    "          (steps.strategy.outcome == 'failure' ||\n           steps.runtime.outcome == 'failure' ||\n           steps.report.outcome == 'failure' ||",
    1,
)
daily = daily.replace(
    "          STRATEGY_OUTCOME: ${{ steps.strategy.outcome }}\n          REPORT_OUTCOME:",
    "          STRATEGY_OUTCOME: ${{ steps.strategy.outcome }}\n          RUNTIME_OUTCOME: ${{ steps.runtime.outcome }}\n          REPORT_OUTCOME:",
    1,
)
daily = daily.replace(
    "          stage=\"strategy-fingerprint\"\n          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n",
    "          stage=\"strategy-fingerprint\"\n          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${RUNTIME_OUTCOME}\" = \"failure\" ]; then stage=\"runtime-provenance\"; fi\n          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${RUNTIME_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" = \"failure\" ]; then stage=\"report\"; fi\n",
    1,
)
daily = daily.replace(
    "          (steps.strategy.outcome == 'failure' ||\n           steps.report.outcome == 'failure' ||",
    "          (steps.strategy.outcome == 'failure' ||\n           steps.runtime.outcome == 'failure' ||\n           steps.report.outcome == 'failure' ||",
    1,
)
daily_path.write_text(daily, encoding="utf-8")

# A read-only weekly clean-install audit verifies lock solvability and captures
# the actual installed environment as an artifact.
Path(".github/workflows/dependency-audit.yml").write_text('''name: Dependency Lock Audit

on:
  schedule:
    # Monday 06:30 JST.
    - cron: '30 21 * * 0'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: momentum-dependency-lock-audit
  cancel-in-progress: true

jobs:
  audit:
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

      - name: Install exactly constrained dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt -c requirements.lock
          python -m pip check

      - name: Capture runtime provenance
        run: |
          mkdir -p output/dependency-audit
          python runtime_provenance.py \
            --strict \
            --output output/dependency-audit/runtime_provenance.json \
            --freeze-output output/dependency-audit/pip_freeze.txt

      - name: Confirm installed versions satisfy the lock
        run: |
          python - <<'PY'
          from pathlib import Path
          import subprocess
          import sys

          lock = {
              line.strip().lower()
              for line in Path('requirements.lock').read_text(encoding='utf-8').splitlines()
              if line.strip() and not line.lstrip().startswith('#')
          }
          freeze = {
              line.strip().lower()
              for line in subprocess.check_output(
                  [sys.executable, '-m', 'pip', 'freeze'], text=True
              ).splitlines()
              if line.strip()
          }
          missing = sorted(lock - freeze)
          assert not missing, f'locked packages not installed exactly: {missing}'
          PY

      - name: Upload dependency audit
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: dependency-lock-audit-${{ github.run_id }}
          path: output/dependency-audit/
          if-no-files-found: error
          retention-days: 90
''', encoding="utf-8")

# Permanent CI includes runtime tests, pip check, and enforces constraints use.
ci_path = Path(".github/workflows/ci.yml")
ci = ci_path.read_text(encoding="utf-8")
if "runtime_provenance.py" not in ci:
    ci = ci.replace(
        "          strategy_governance.py evidence_provenance.py",
        "          strategy_governance.py evidence_provenance.py runtime_provenance.py",
        1,
    )
if ".github/test_runtime_provenance.py" not in ci:
    ci = ci.replace(
        "          .github/test_capacity_analysis.py",
        "          .github/test_capacity_analysis.py .github/test_runtime_provenance.py",
        1,
    )
    step = '''      - name: Run runtime reproducibility validation
        run: |
          set -o pipefail
          python .github/test_runtime_provenance.py 2>&1 | tee /tmp/runtime-provenance-test.log
          python -m pip check

'''
    anchor = "      - name: Upload validation failure logs\n"
    if anchor not in ci:
        raise RuntimeError("CI failure upload anchor not found")
    ci = ci.replace(anchor, step + anchor, 1)
    ci = ci.replace(
        "            /tmp/capacity-analysis-test.log\n",
        "            /tmp/capacity-analysis-test.log\n            /tmp/runtime-provenance-test.log\n",
        1,
    )
ci_path.write_text(ci, encoding="utf-8")

# Workflow policy checks lock usage across every maintained workflow.
validator_path = Path(".github/validate_workflows.py")
validator = validator_path.read_text(encoding="utf-8")
if "Dependency workflow does not use requirements.lock" not in validator:
    insertion = '''
    for workflow_path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        workflow_text = workflow_path.read_text(encoding="utf-8")
        if "requirements.txt" in workflow_text and "-c requirements.lock" not in workflow_text:
            raise AssertionError(
                f"Dependency workflow does not use requirements.lock: {workflow_path.name}"
            )

    dependency = load_workflow("dependency-audit.yml")
    require("dependency-audit.yml", dependency, [
        "requirements.txt -c requirements.lock",
        "python -m pip check",
        "runtime_provenance.py",
        "contents: read",
        "retention-days: 90",
    ])
    forbid("dependency-audit.yml", dependency, [
        "git push",
        "contents: write",
        "EMAIL_APP_PASSWORD",
    ])

'''
    anchor = '    main_source = (ROOT / "main.py").read_text(encoding="utf-8")\n'
    if anchor not in validator:
        raise RuntimeError("validator main source anchor not found")
    validator = validator.replace(anchor, insertion + anchor, 1)
validator_path.write_text(validator, encoding="utf-8")

print("runtime reproducibility integration applied")
