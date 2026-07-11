from pathlib import Path
import re


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:160]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Main application reads only the compact signed status. It does not consume
# raw replay outcomes or alter any strategy rule.
main_path = Path("main.py")
main_text = main_path.read_text(encoding="utf-8")
if "import json\n" not in main_text:
    if "import logging\n" in main_text:
        main_text = main_text.replace("import logging\n", "import json\nimport logging\n", 1)
    else:
        main_text = "import json\n" + main_text

status_helper = r'''
RESEARCH_EVIDENCE_STATUS_PATH = "data/research_evidence_status.json"
RESEARCH_EVIDENCE_MAX_AGE_DAYS = 10


def load_research_evidence_status(
    path: str = RESEARCH_EVIDENCE_STATUS_PATH,
    current_fingerprint: str | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    target = Path(path)
    default = {
        "readiness": "MISSING",
        "manual_review_eligible": False,
        "outcome_count": 0,
        "robustness_status": "MISSING",
        "provenance_valid": False,
        "strategy_fingerprint": "",
        "fingerprint_matches": False,
        "status_fresh": False,
        "status_age_days": None,
        "status_path": str(target),
    }
    if not target.exists():
        return default
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read research evidence status %s: %s", path, exc)
        return {**default, "readiness": "UNREADABLE"}
    if not isinstance(payload, dict):
        return {**default, "readiness": "UNREADABLE"}
    fingerprint = str(
        current_fingerprint
        if current_fingerprint is not None
        else os.environ.get("MOMENTUM_STRATEGY_FINGERPRINT", "")
    ).strip()
    status_fingerprint = str(payload.get("strategy_fingerprint", "")).strip()
    fingerprint_matches = bool(fingerprint and status_fingerprint == fingerprint)
    generated = pd.to_datetime(payload.get("generated_at_utc"), utc=True, errors="coerce")
    now_value = now_utc or datetime.now(timezone.utc)
    age_days = None if pd.isna(generated) else max((pd.Timestamp(now_value) - generated).total_seconds() / 86400, 0.0)
    status_fresh = age_days is not None and age_days <= RESEARCH_EVIDENCE_MAX_AGE_DAYS
    execution_mode_valid = payload.get("execution_mode") == EXECUTION_MODE
    source_ready = payload.get("manual_review_eligible") is True
    effective_ready = bool(
        source_ready
        and fingerprint_matches
        and status_fresh
        and execution_mode_valid
        and payload.get("provenance_valid") is True
        and payload.get("execution_model") == "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"
        and payload.get("same_day_close_entry_allowed") is False
    )
    return {
        **default,
        **payload,
        "manual_review_eligible": effective_ready,
        "source_manual_review_eligible": source_ready,
        "fingerprint_matches": fingerprint_matches,
        "status_fresh": status_fresh,
        "status_age_days": age_days,
        "execution_mode_valid": execution_mode_valid,
        "status_path": str(target),
    }


'''
if "def load_research_evidence_status(" not in main_text:
    anchor = "def build_release_readiness("
    if anchor not in main_text:
        raise RuntimeError("build_release_readiness anchor not found")
    main_text = main_text.replace(anchor, status_helper + anchor, 1)

# Add an optional evidence status parameter, preserving compatibility with old tests.
main_text = main_text.replace(
    "    paper_trade_history: pd.DataFrame,\n    paper_risk_budget: pd.DataFrame,\n) -> pd.DataFrame:\n",
    "    paper_trade_history: pd.DataFrame,\n    paper_risk_budget: pd.DataFrame,\n    research_evidence_status: dict[str, Any] | None = None,\n) -> pd.DataFrame:\n",
    1,
)

release_anchor = '''    risk_failures = int((paper_risk_budget.get("status", pd.Series(dtype=str)) == "FAIL").sum()) if paper_risk_budget is not None and not paper_risk_budget.empty else 0

    criteria = [
'''
release_insert = '''    risk_failures = int((paper_risk_budget.get("status", pd.Series(dtype=str)) == "FAIL").sum()) if paper_risk_budget is not None and not paper_risk_budget.empty else 0
    evidence = research_evidence_status or {}
    evidence_ready = evidence.get("manual_review_eligible") is True
    evidence_count = int(pd.to_numeric(pd.Series([evidence.get("outcome_count")]), errors="coerce").fillna(0).iloc[0])
    evidence_robustness = optional_text(evidence.get("robustness_status")) or "MISSING"
    evidence_readiness = optional_text(evidence.get("readiness")) or "MISSING"
    evidence_actual = f"{evidence_count}件 / {evidence_robustness} / {evidence_readiness}"

    criteria = [
'''
if release_anchor not in main_text:
    raise RuntimeError("release readiness variable anchor not found")
main_text = main_text.replace(release_anchor, release_insert, 1)

criteria_anchor = '''        ("リスク予算超過", risk_failures, "0件", risk_failures == 0, True, "銘柄・業種・総投資比率の上限遵守"),
    ]
'''
criteria_new = '''        ("リスク予算超過", risk_failures, "0件", risk_failures == 0, True, "銘柄・業種・総投資比率の上限遵守"),
        ("ライブ実行証拠", evidence_actual, "100件以上・ROBUST・現行指紋・翌営業日寄付", evidence_ready, False, "署名済み翌営業日execution証拠。close-based診断は昇格判定に使用しない"),
    ]
'''
if criteria_anchor not in main_text:
    raise RuntimeError("release readiness criteria anchor not found")
main_text = main_text.replace(criteria_anchor, criteria_new, 1)

# Load the compact status before release readiness and pass it into the gate.
call_anchor = '''    release_readiness = build_release_readiness(run_health, signal_governance, sector_leader_performance, paper_performance, paper_trade_history, paper_risk_budget)
'''
call_new = '''    research_evidence_status = load_research_evidence_status()
    release_readiness = build_release_readiness(
        run_health,
        signal_governance,
        sector_leader_performance,
        paper_performance,
        paper_trade_history,
        paper_risk_budget,
        research_evidence_status,
    )
'''
if call_anchor not in main_text:
    raise RuntimeError("release readiness call anchor not found")
main_text = main_text.replace(call_anchor, call_new, 1)

summary_anchor = '''        "リリース判定": release_status_value(release_readiness),
        "実行モード": EXECUTION_MODE,
'''
summary_new = '''        "リリース判定": release_status_value(release_readiness),
        "ライブ実行証拠状態": research_evidence_status.get("readiness", "MISSING"),
        "ライブ実行証拠件数": int(research_evidence_status.get("outcome_count", 0) or 0),
        "ライブ実行頑健性": research_evidence_status.get("robustness_status", "MISSING"),
        "ライブ実行証拠現行指紋": bool(research_evidence_status.get("fingerprint_matches", False)),
        "ライブ実行証拠鮮度OK": bool(research_evidence_status.get("status_fresh", False)),
        "ライブ実行手動レビュー可": bool(research_evidence_status.get("manual_review_eligible", False)),
        "実行モード": EXECUTION_MODE,
'''
if summary_anchor not in main_text:
    raise RuntimeError("summary release anchor not found")
main_text = main_text.replace(summary_anchor, summary_new, 1)
main_path.write_text(main_text, encoding="utf-8")

# Weekly replay persists only the compact signed status; raw outcomes remain artifacts.
replay_path = Path(".github/workflows/replay.yml")
replay = replay_path.read_text(encoding="utf-8")
replay = replay.replace("permissions:\n  contents: read\n", "permissions:\n  contents: write\n", 1)
checkout_anchor = "      - uses: actions/checkout@v4\n\n"
checkout_new = '''      - uses: actions/checkout@v4
        with:
          ref: main
          fetch-depth: 0
          token: ${{ secrets.GITHUB_TOKEN }}

'''
if checkout_anchor not in replay:
    raise RuntimeError("weekly checkout anchor not found")
replay = replay.replace(checkout_anchor, checkout_new, 1)

status_steps = '''      - name: Build signed research evidence status
        run: |
          robustness_path=output/replay/execution/replay_robustness_summary.csv
          execution_manifest=output/replay/execution/execution_realism_manifest.json
          if [ ! -f "${robustness_path}" ]; then robustness_path=output/replay/execution/no_robustness_available.csv; fi
          if [ ! -f "${execution_manifest}" ]; then execution_manifest=output/replay/execution/no_execution_manifest.json; fi
          python research_evidence_status.py \\
            --provenance output/replay/execution_evidence_provenance.json \\
            --robustness "${robustness_path}" \\
            --governance-issues output/replay/execution/strategy_governance_issues.csv \\
            --execution-manifest "${execution_manifest}" \\
            --registry research/experiment_registry.yaml \\
            --output data/research_evidence_status.json \\
            --artifact-dir output/replay/evidence-status

      - name: Persist compact research evidence status
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/research_evidence_status.json
          if git diff --cached --quiet; then
            echo "Research evidence status is unchanged"
            exit 0
          fi
          git commit -m "Update governed research evidence status"
          git fetch origin main
          git rebase origin/main
          for attempt in 1 2 3; do
            if git push origin HEAD:main; then exit 0; fi
            if [ "${attempt}" -lt 3 ]; then
              sleep $((attempt * 10))
              git fetch origin main
              git rebase origin/main
            fi
          done
          echo "Failed to persist research evidence status" >&2
          exit 1

'''
upload_anchor = "      - name: Upload replay research artifact\n"
if "research_evidence_status.py" not in replay:
    if upload_anchor not in replay:
        raise RuntimeError("weekly upload anchor not found")
    replay = replay.replace(upload_anchor, status_steps + upload_anchor, 1)
replay_path.write_text(replay, encoding="utf-8")

# Permanent CI validates status generation and release gating.
ci_path = Path(".github/workflows/ci.yml")
ci = ci_path.read_text(encoding="utf-8")
if "research_evidence_status.py" not in ci:
    ci = ci.replace(
        "          strategy_governance.py evidence_provenance.py operations.py\n",
        "          strategy_governance.py evidence_provenance.py research_evidence_status.py operations.py\n",
        1,
    )
if ".github/test_research_evidence_status.py" not in ci:
    ci = ci.replace(
        "          .github/test_capacity_analysis.py",
        "          .github/test_capacity_analysis.py .github/test_research_evidence_status.py .github/test_release_evidence_gate.py",
        1,
    )
    steps = '''      - name: Run signed research evidence status validation
        run: |
          set -o pipefail
          python .github/test_research_evidence_status.py 2>&1 | tee /tmp/research-evidence-status-test.log

      - name: Run release evidence gate validation
        run: |
          set -o pipefail
          python .github/test_release_evidence_gate.py 2>&1 | tee /tmp/release-evidence-gate-test.log

'''
    anchor = "      - name: Run operational controls validation\n"
    if anchor not in ci:
        raise RuntimeError("CI operational controls anchor not found")
    ci = ci.replace(anchor, steps + anchor, 1)
    ci = ci.replace(
        "            /tmp/operations-test.log\n",
        "            /tmp/research-evidence-status-test.log\n            /tmp/release-evidence-gate-test.log\n            /tmp/operations-test.log\n",
        1,
    )
ci_path.write_text(ci, encoding="utf-8")

# Workflow policy limits weekly write access to the compact status file.
validator_path = Path(".github/validate_workflows.py")
validator = validator_path.read_text(encoding="utf-8")
validator = validator.replace(
    '        "retention-days: 90",\n        "contents: read",\n',
    '        "research_evidence_status.py",\n'
    '        "data/research_evidence_status.json",\n'
    '        "git add data/research_evidence_status.json",\n'
    '        "retention-days: 90",\n'
    '        "contents: write",\n',
    1,
)
validator = validator.replace(
    '    assert "strategy_governance.py audit" not in replay_text\n',
    '    assert "strategy_governance.py audit" not in replay_text\n'
    '    assert "git add main.py" not in replay_text\n'
    '    assert "git add config.yaml" not in replay_text\n'
    '    assert "git add research/experiment_registry.yaml" not in replay_text\n',
    1,
)
validator_path.write_text(validator, encoding="utf-8")

print("research evidence status integration applied")
