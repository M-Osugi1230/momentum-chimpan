from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:140]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


daily_path = Path(".github/workflows/daily.yml")
daily = daily_path.read_text(encoding="utf-8")
recovery_step = '''      - name: Seal recoverable state snapshot
        id: recovery
        if: >-
          steps.strategy.outcome == 'success' &&
          steps.report.outcome == 'success' &&
          steps.heartbeat.outcome == 'success' &&
          steps.evidence.outcome == 'success'
        continue-on-error: true
        run: |
          python state_recovery.py seal \\
            --report output/daily_report.xlsx \\
            --fingerprint data/strategy_fingerprint.json \\
            --snapshot-root data/state_snapshots \\
            --audit output/recovery_snapshot_audit.json

'''
anchor = "      - name: Validate state and apply bounded retention\n"
if "id: recovery\n" not in daily:
    if anchor not in daily:
        raise RuntimeError("daily maintenance anchor not found")
    daily = daily.replace(anchor, recovery_step + anchor, 1)

# Add the recovery gate to maintenance and persistence conditions.
daily = daily.replace(
    "          steps.evidence.outcome == 'success'\n        continue-on-error: true\n        run: |\n          python operations.py maintain",
    "          steps.evidence.outcome == 'success' &&\n          steps.recovery.outcome == 'success'\n        continue-on-error: true\n        run: |\n          python operations.py maintain",
    1,
)
daily = daily.replace(
    "          steps.evidence.outcome == 'success' &&\n          steps.maintenance.outcome == 'success' &&",
    "          steps.evidence.outcome == 'success' &&\n          steps.recovery.outcome == 'success' &&\n          steps.maintenance.outcome == 'success' &&",
    1,
)

# Failure notification and final enforcement include recovery sealing.
daily = daily.replace(
    "           steps.evidence.outcome == 'failure' ||\n           steps.maintenance.outcome == 'failure' ||",
    "           steps.evidence.outcome == 'failure' ||\n           steps.recovery.outcome == 'failure' ||\n           steps.maintenance.outcome == 'failure' ||",
    1,
)
daily = daily.replace(
    "          EVIDENCE_OUTCOME: ${{ steps.evidence.outcome }}\n          MAINTENANCE_OUTCOME:",
    "          EVIDENCE_OUTCOME: ${{ steps.evidence.outcome }}\n          RECOVERY_OUTCOME: ${{ steps.recovery.outcome }}\n          MAINTENANCE_OUTCOME:",
    1,
)
daily = daily.replace(
    "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" != \"failure\" ] && [ \"${HEARTBEAT_OUTCOME}\" != \"failure\" ] && [ \"${EVIDENCE_OUTCOME}\" != \"failure\" ] && [ \"${MAINTENANCE_OUTCOME}\" = \"failure\" ]; then stage=\"state-maintenance\"; fi\n",
    "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" != \"failure\" ] && [ \"${HEARTBEAT_OUTCOME}\" != \"failure\" ] && [ \"${EVIDENCE_OUTCOME}\" != \"failure\" ] && [ \"${RECOVERY_OUTCOME}\" = \"failure\" ]; then stage=\"recovery-snapshot-seal\"; fi\n"
    "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" != \"failure\" ] && [ \"${HEARTBEAT_OUTCOME}\" != \"failure\" ] && [ \"${EVIDENCE_OUTCOME}\" != \"failure\" ] && [ \"${RECOVERY_OUTCOME}\" != \"failure\" ] && [ \"${MAINTENANCE_OUTCOME}\" = \"failure\" ]; then stage=\"state-maintenance\"; fi\n",
    1,
)
daily = daily.replace(
    "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" != \"failure\" ] && [ \"${HEARTBEAT_OUTCOME}\" != \"failure\" ] && [ \"${EVIDENCE_OUTCOME}\" != \"failure\" ] && [ \"${MAINTENANCE_OUTCOME}\" != \"failure\" ] && [ \"${PERSIST_OUTCOME}\" = \"failure\" ]; then stage=\"state-persistence\"; fi\n",
    "          if [ \"${STRATEGY_OUTCOME}\" != \"failure\" ] && [ \"${REPORT_OUTCOME}\" != \"failure\" ] && [ \"${HEARTBEAT_OUTCOME}\" != \"failure\" ] && [ \"${EVIDENCE_OUTCOME}\" != \"failure\" ] && [ \"${RECOVERY_OUTCOME}\" != \"failure\" ] && [ \"${MAINTENANCE_OUTCOME}\" != \"failure\" ] && [ \"${PERSIST_OUTCOME}\" = \"failure\" ]; then stage=\"state-persistence\"; fi\n",
    1,
)
daily = daily.replace(
    "            output/evidence_stamp_audit.json\n",
    "            output/evidence_stamp_audit.json\n            output/recovery_snapshot_audit.json\n",
    1,
)
daily = daily.replace(
    "           steps.evidence.outcome == 'failure' ||\n           steps.maintenance.outcome == 'failure' ||",
    "           steps.evidence.outcome == 'failure' ||\n           steps.recovery.outcome == 'failure' ||\n           steps.maintenance.outcome == 'failure' ||",
    1,
)
daily_path.write_text(daily, encoding="utf-8")

# Permanent CI compiles and tests recovery.
ci_path = Path(".github/workflows/ci.yml")
ci = ci_path.read_text(encoding="utf-8")
if "state_recovery.py" not in ci:
    ci = ci.replace(
        "          execution_realism.py capacity_analysis.py .github/validate_workflows.py\n",
        "          execution_realism.py capacity_analysis.py state_recovery.py .github/validate_workflows.py\n",
        1,
    )
if ".github/test_state_recovery.py" not in ci:
    ci = ci.replace(
        "          .github/test_capacity_analysis.py",
        "          .github/test_capacity_analysis.py .github/test_state_recovery.py",
        1,
    )
    step = '''      - name: Run sealed state recovery validation
        run: |
          set -o pipefail
          python .github/test_state_recovery.py 2>&1 | tee /tmp/state-recovery-test.log

'''
    anchor = "      - name: Run operational controls validation\n"
    if anchor not in ci:
        raise RuntimeError("CI operational controls anchor not found")
    ci = ci.replace(anchor, step + anchor, 1)
    ci = ci.replace(
        "            /tmp/operations-test.log\n",
        "            /tmp/state-recovery-test.log\n            /tmp/operations-test.log\n",
        1,
    )
ci_path.write_text(ci, encoding="utf-8")

# Workflow policy validates daily sealing and read-only recovery drill.
validator_path = Path(".github/validate_workflows.py")
validator = validator_path.read_text(encoding="utf-8")
validator = validator.replace(
    '        "operations.py maintain",\n',
    '        "state_recovery.py seal",\n'
    '        "output/recovery_snapshot_audit.json",\n'
    '        "steps.recovery.outcome == \'success\'",\n'
    '        "operations.py maintain",\n',
    1,
)
recovery_validation = '''
    recovery = load_workflow("recovery-drill.yml")
    require("recovery-drill.yml", recovery, [
        "python state_recovery.py drill",
        "--strict",
        "data/state_snapshots",
        "output/recovery",
        "contents: read",
        "retention-days: 90",
        "production state",
    ])
    forbid("recovery-drill.yml", recovery, [
        "git push",
        "contents: write",
        "EMAIL_APP_PASSWORD",
    ])

'''
anchor = '    main_source = (ROOT / "main.py").read_text(encoding="utf-8")\n'
if "load_workflow(\"recovery-drill.yml\")" not in validator:
    if anchor not in validator:
        raise RuntimeError("validator main source anchor not found")
    validator = validator.replace(anchor, recovery_validation + anchor, 1)
validator_path.write_text(validator, encoding="utf-8")

print("state recovery patch applied")
