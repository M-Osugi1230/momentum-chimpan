from pathlib import Path


# Read-only weekly/manual review packet generation.
Path(".github/workflows/release-review.yml").write_text('''name: Manual Strategy Release Review Packet

on:
  schedule:
    # Sunday 12:00 JST, after replay, recovery, and archive audits.
    - cron: '0 3 * * 0'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: momentum-manual-release-review
  cancel-in-progress: true

jobs:
  packet:
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

      - name: Validate human approval registry
        run: |
          mkdir -p output/release-review
          python release_review.py validate-approvals \\
            --approvals research/strategy_approvals.yaml \\
            --output output/release-review/approval_validation.csv

      - name: Build review packet
        run: |
          python release_review.py packet \\
            --output-dir output/release-review \\
            --evidence-status data/research_evidence_status.json \\
            --runtime-provenance data/runtime_provenance.json \\
            --heartbeat data/operations_heartbeat.json \\
            --fingerprint data/strategy_fingerprint.json \\
            --paper-equity data/paper_equity_history.csv \\
            --paper-trades data/paper_trade_history.csv

      - name: Add packet status to job summary
        run: |
          cat output/release-review/release_review_packet.md >> "${GITHUB_STEP_SUMMARY}"

      - name: Upload manual review packet
        uses: actions/upload-artifact@v4
        with:
          name: manual-release-review-${{ github.run_id }}
          path: output/release-review/
          if-no-files-found: error
          retention-days: 180
''', encoding="utf-8")

# Permanent CI validates packet generation and immutable approval policy.
ci_path = Path(".github/workflows/ci.yml")
ci = ci_path.read_text(encoding="utf-8")
if "release_review.py" not in ci:
    ci = ci.replace(
        "          strategy_governance.py evidence_provenance.py",
        "          strategy_governance.py evidence_provenance.py release_review.py",
        1,
    )
if ".github/test_release_review.py" not in ci:
    ci = ci.replace(
        "          .github/test_strategy_governance.py",
        "          .github/test_strategy_governance.py .github/test_release_review.py",
        1,
    )
    step = '''      - name: Run manual release review validation
        run: |
          set -o pipefail
          python .github/test_release_review.py 2>&1 | tee /tmp/release-review-test.log
          python release_review.py validate-approvals \\
            --approvals research/strategy_approvals.yaml \\
            --output /tmp/approval-validation.csv

'''
    anchor = "      - name: Upload validation failure logs\n"
    if anchor not in ci:
        raise RuntimeError("CI failure upload anchor not found")
    ci = ci.replace(anchor, step + anchor, 1)
    ci = ci.replace(
        "            /tmp/strategy-governance-test.log\n",
        "            /tmp/strategy-governance-test.log\n            /tmp/release-review-test.log\n",
        1,
    )
ci_path.write_text(ci, encoding="utf-8")

# Workflow policy enforces read-only manual review and exact approval scope.
validator_path = Path(".github/validate_workflows.py")
validator = validator_path.read_text(encoding="utf-8")
review_validation = '''
    review = load_workflow("release-review.yml")
    require("release-review.yml", review, [
        "release_review.py validate-approvals",
        "release_review.py packet",
        "research/strategy_approvals.yaml",
        "contents: read",
        "retention-days: 180",
    ])
    forbid("release-review.yml", review, [
        "git push",
        "contents: write",
        "EMAIL_APP_PASSWORD",
        "automatic_activation: true",
    ])

'''
anchor = '    main_source = (ROOT / "main.py").read_text(encoding="utf-8")\n'
if "load_workflow(\"release-review.yml\")" not in validator:
    if anchor not in validator:
        raise RuntimeError("validator main source anchor not found")
    validator = validator.replace(anchor, review_validation + anchor, 1)
validator_path.write_text(validator, encoding="utf-8")

print("manual release review workflow integration applied")
