from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_workflow(name: str) -> str:
    path = ROOT / ".github" / "workflows" / name
    text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict):
        raise AssertionError(f"{name}: YAML root must be a mapping")
    return text


def require(name: str, text: str, fragments: list[str]) -> None:
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise AssertionError(f"{name}: missing required controls: {missing}")


def forbid(name: str, text: str, fragments: list[str]) -> None:
    found = [fragment for fragment in fragments if fragment in text]
    if found:
        raise AssertionError(f"{name}: forbidden controls found: {found}")


def main() -> int:
    daily = load_workflow("daily.yml")
    require("daily.yml", daily, [
        "python-version: '3.12'",
        "daily-momentum-report-main",
        "retention-days: 90",
        "data/momentum_daily_ranking.csv",
        "data/market_temperature.csv",
        "data/sector_leader_signal_history.csv",
        "data/paper_portfolio.csv",
        "data/paper_trade_history.csv",
        "data/paper_equity_history.csv",
        "data/execution_audit.csv",
        "data/operations_heartbeat.json",
        "data/strategy_fingerprint.json",
        "data/state_snapshots",
        "operations.py heartbeat",
        "Snapshot governed strategy fingerprint before report",
        "MOMENTUM_STRATEGY_FINGERPRINT",
        "MOMENTUM_STRATEGY_STAMP_SOURCE",
        "strategy_governance.py snapshot",
        "evidence_provenance.py stamp-live",
        "output/evidence_stamp_audit.json",
        "--snapshot-root data/state_snapshots",
        "steps.evidence.outcome == 'success'",
        "state_recovery.py seal",
        "output/recovery_snapshot_audit.json",
        "steps.recovery.outcome == 'success'",
        "operations.py maintain",
        "operations.py notify",
        "git rebase origin/main",
    ])

    replay = load_workflow("replay.yml")
    require("replay.yml", replay, [
        "evidence_provenance.py prepare-live",
        "live_strategy_history.csv",
        "eligible_dates",
        "python replay.py",
        "research_scorecard.py",
        "robustness_analysis.py --strict",
        "python live_execution_panel.py",
        "python execution_realism.py",
        "--base-cost-bps 0",
        "evidence_provenance.py seal-execution",
        "execution_evidence_provenance.json",
        "evidence_provenance.py governance-audit",
        "evidence_provenance.json",
        "insufficient-live-evidence.txt",
        "insufficient-execution-evidence.txt",
        "retention-days: 90",
        "contents: read",
    ])
    forbid("replay.yml", replay, ["strategy_governance.py audit"])

    forward = load_workflow("volume-component-forward-evidence.yml")
    require("volume-component-forward-evidence.yml", forward, [
        "volume_component_forward_evidence.py",
        "research/volume_component_forward_evidence.yaml",
        "live_session_eligibility.py validate",
        "forward_eligible_history.py",
        "research/evidence/live_session_eligibility.csv",
        "eligibility_enforced",
        "live_strategy_history.csv",
        "eligible_signal_date_from",
        "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "promotion_evidence_allowed",
        "automatic_weight_change",
        "production_state_mutations",
        "contents: read",
        "retention-days: 180",
    ])
    forbid("volume-component-forward-evidence.yml", forward, [
        "evidence_provenance.py prepare-live",
        "git push",
        "contents: write",
        "EMAIL_APP_PASSWORD",
    ])

    eligibility = load_workflow("live-session-eligibility-ledger.yml")
    require("live-session-eligibility-ledger.yml", eligibility, [
        "Daily Momentum Report",
        "actions/download-artifact@v4",
        "run-id: ${{ steps.source.outputs.run_id }}",
        "source_run_id:",
        "gh api",
        "live_session_readiness_with_recovery.py",
        "live_session_eligibility_with_recovery.py",
        "live_session_eligibility_with_recovery.py update",
        "research/evidence/live_session_eligibility.csv",
        "research/evidence/live_session_eligibility_status.json",
        "git add --",
        "contents: read",
        "retention-days: 90",
    ])
    forbid("live-session-eligibility-ledger.yml", eligibility, [
        "python live_session_eligibility.py update",
        "EMAIL_APP_PASSWORD",
        "data/momentum_daily_ranking.csv \\",
        "config.yaml \\",
        "main.py \\",
    ])

    reconciliation = load_workflow("reconcile-research-ledgers.yml")
    require("reconcile-research-ledgers.yml", reconciliation, [
        "Reconcile Research Ledgers",
        "workflow_dispatch:",
        "gh api --paginate --slurp",
        "actions/workflows/daily.yml/runs?status=completed&per_page=100",
        "momentum-operations-${run_id}",
        "operations_audit.py update",
        "live_session_eligibility_with_recovery.py update",
        "live_session_readiness_with_recovery.py build",
        "eligible_for_priority_outcome_ingestion",
        "priority_outcomes.py update",
        "Mature all available 5, 10, and 20-session outcomes",
        "missing_successful_eligibility_run_ids",
        "research/operations/daily_production_audit.csv",
        "research/evidence/live_session_eligibility.csv",
        "research/priority_outcomes/daily_research_decisions.csv",
        "research/priority_outcomes/daily_research_outcomes.csv",
        "git add --",
        "contents: write",
        "retention-days: 90",
    ])
    forbid("reconcile-research-ledgers.yml", reconciliation, [
        "EMAIL_APP_PASSWORD",
        "data/momentum_daily_ranking.csv \\",
        "data/paper_portfolio.csv \\",
        "config.yaml \\",
        "main.py \\",
    ])
    assert reconciliation.count("git push origin HEAD:main") == 1
    assert "research/priority_outcomes/**" not in reconciliation.split("push:", 1)[1].split("permissions:", 1)[0]

    priority = load_workflow("daily-priority-outcomes.yml")
    require("daily-priority-outcomes.yml", priority, [
        "live_session_readiness_with_recovery.py build",
        "exact recovery PASS is required",
        "production_state_unchanged",
        "eligible_for_priority_outcome_ingestion",
        "research/priority_outcomes/daily_research_decisions.csv",
        "research/priority_outcomes/daily_research_outcomes.csv",
        "contents: read",
    ])
    forbid("daily-priority-outcomes.yml", priority, [
        "python live_session_readiness.py build",
        "EMAIL_APP_PASSWORD",
        "data/paper_portfolio.csv \\",
    ])

    paper_regime = load_workflow("paper-regime-validation.yml")
    require("paper-regime-validation.yml", paper_regime, [
        "Paper Portfolio Regime Validation",
        "paper_regime_validation.py build",
        "research/paper_regime_validation_policy.yaml",
        "data/paper_equity_history.csv",
        "data/paper_trade_history.csv",
        "data/execution_audit.csv",
        "data/market_temperature.csv",
        "production_state_mutations",
        "automatic_paper_rule_change",
        "contents: read",
        "retention-days: 180",
    ])
    forbid("paper-regime-validation.yml", paper_regime, [
        "git push",
        "contents: write",
        "EMAIL_APP_PASSWORD",
    ])

    smoke = load_workflow("smoke.yml")
    require("smoke.yml", smoke, [
        "MOMENTUM_MAX_SYMBOLS",
        "python main.py",
        "operations.py heartbeat",
        "strategy_governance.py snapshot",
        "python smoke_validate.py",
        "contents: read",
        "retention-days: 14",
        "Smoke workflow never commits or pushes production state.",
    ])
    forbid("smoke.yml", smoke, ["git push", "EMAIL_APP_PASSWORD"])

    backfill = load_workflow("historical-backfill.yml")
    require("historical-backfill.yml", backfill, [
        "python historical_backfill.py",
        "evidence_provenance.py seal-derived",
        "python historical_price_panel.py",
        "historical_price_panel.csv",
        "python replay.py",
        "python research_scorecard.py",
        "python robustness_analysis.py",
        "python execution_realism.py",
        "python portfolio_research.py",
        "portfolio_research_manifest.json",
        "STOP_FIRST_CONSERVATIVE",
        "python portfolio_filter_lab.py",
        "portfolio_filter_manifest.json",
        "automatic_filter_activation",
        "python portfolio_exit_lab.py",
        "portfolio_exit_manifest.json",
        "automatic_exit_activation",
        "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "execution_benchmarked_outcomes.csv",
        "--base-cost-bps 0",
        "evidence_provenance.py governance-audit",
        "promotion_evidence_allowed",
        "contents: read",
        "retention-days: 30",
    ])
    forbid("historical-backfill.yml", backfill, [
        "strategy_governance.py audit",
        "git push",
        "EMAIL_APP_PASSWORD",
    ])

    assert daily.index("Snapshot governed strategy fingerprint before report") < daily.index("Run report")

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

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    require("main.py", main_source, [
        "def attach_strategy_provenance(",
        "MOMENTUM_STRATEGY_FINGERPRINT",
        "all_ranked = attach_strategy_provenance(all_ranked)",
        "def evaluate_market_data_freshness(",
        "def attach_market_data_freshness_health(",
        "if state_update_allowed:\n        write_ranking_history",
        "paper_result = load_existing_paper_state(regime, run_health)",
        "state_snapshots = pd.DataFrame(columns=STATE_SNAPSHOT_COLUMNS)",
        '"状態更新実行": "YES" if state_update_allowed else "NO"',
    ])

    registry = yaml.safe_load((ROOT / "research" / "experiment_registry.yaml").read_text(encoding="utf-8"))
    policy = registry["policy"]
    assert policy["automatic_promotion"] is False
    assert policy["require_manual_approval"] is True
    assert policy["allowed_promotion_evidence_origins"] == ["LIVE_FORWARD_RANKING_HISTORY"]
    assert policy["required_promotion_execution_model"] == "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"

    forward_registry = yaml.safe_load(
        (ROOT / "research" / "volume_component_forward_evidence.yaml").read_text(encoding="utf-8")
    )
    assert forward_registry["study"]["registered_at"] == "2026-07-12"
    assert forward_registry["study"]["eligible_signal_date_from"] == "2026-07-13"
    assert forward_registry["governance"]["promotion_evidence_allowed"] is False
    assert forward_registry["governance"]["automatic_weight_change"] is False
    assert forward_registry["governance"]["automatic_strategy_change"] is False

    print("workflow policy validation passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"workflow policy validation failed: {exc}", file=sys.stderr)
        raise
