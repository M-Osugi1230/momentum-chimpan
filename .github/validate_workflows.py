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

    print("workflow policy validation passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"workflow policy validation failed: {exc}", file=sys.stderr)
        raise
