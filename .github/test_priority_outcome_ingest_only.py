from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import priority_outcome_ingest_only as ingest_only
import priority_outcomes as tracker


policy = tracker.load_policy(ROOT / tracker.POLICY_PATH)
decision = {column: "" for column in tracker.DECISION_COLUMNS}
decision.update({
    "decision_schema_version": tracker.DECISION_SCHEMA_VERSION,
    "decision_id": "d" * 64,
    "source_run_id": "123",
    "source_run_url": "https://example.test/runs/123",
    "source_artifact_sha256": "a" * 64,
    "recorded_at_utc": "2026-07-23T10:00:00+00:00",
    "decision_date": "2026-07-23",
    "strategy_fingerprint": "b" * 64,
    "focus_policy_version": "daily-research-focus-v1",
    "code": "1001",
    "name": "Test",
    "sector33": "電気機器",
    "research_bucket": "A",
    "daily_action_list": True,
    "daily_action_rank": 1,
    "action_priority": "A",
    "action_priority_before_quality": "A",
    "action_priority_before_daily_focus": "A",
    "momentum_rank": 1,
    "momentum_score": 90,
    "action_score": 90,
    "expectancy_score": 50,
    "expectancy_confidence": "中",
    "lifecycle_status": "初登場",
    "market_regime": "強気",
    "relative_strength_grade": "A",
    "data_quality_grade": "A",
    "why_today": "test",
    "what_changed": "new",
    "risk_summary": "none",
    "next_research_questions": "check",
    "focus_adjustment_reason": "none",
    "entry_model": policy["execution_model"]["entry"],
    "round_trip_cost_bps": policy["execution_model"]["round_trip_cost_bps"],
    "research_only": True,
})
decisions = tracker.normalize_frame(
    pd.DataFrame([decision], columns=tracker.DECISION_COLUMNS),
    tracker.DECISION_COLUMNS,
    tracker.BOOL_DECISION_COLUMNS,
    tracker.NUMERIC_DECISION_COLUMNS,
)
outcomes = ingest_only.ensure_pending_outcomes(
    decisions, tracker.empty_outcomes(), policy
)
assert len(outcomes) == 3
assert set(outcomes["horizon_sessions"]) == {5, 10, 20}
assert outcomes["outcome_status"].eq("PENDING").all()
assert outcomes["outcome_detail"].eq(
    "price refresh deferred until reconciliation finalization"
).all()
assert outcomes["entry_date"].eq("").all()
assert outcomes["price_fingerprint"].eq("").all()
assert outcomes["research_only"].all()
assert tracker.validate_histories(decisions, outcomes, policy) == []

complete = outcomes.iloc[[0]].copy()
complete.loc[:, "outcome_status"] = "COMPLETE"
complete.loc[:, "entry_date"] = "2026-07-24"
complete.loc[:, "exit_date"] = "2026-07-30"
complete.loc[:, "entry_adjusted_open"] = 100.0
complete.loc[:, "exit_adjusted_close"] = 105.0
complete.loc[:, "gross_return"] = 0.05
complete.loc[:, "net_return"] = 0.048
complete.loc[:, "market_entry_adjusted_open"] = 2000.0
complete.loc[:, "market_exit_adjusted_close"] = 2010.0
complete.loc[:, "market_return"] = 0.005
complete.loc[:, "market_excess_return"] = 0.043
complete.loc[:, "same_day_close_entry"] = False
complete.loc[:, "no_lookahead_verified"] = True
complete.loc[:, "price_fingerprint"] = "c" * 64
preserved = ingest_only.ensure_pending_outcomes(decisions, complete, policy)
assert len(preserved) == 3
assert int(preserved["outcome_status"].eq("COMPLETE").sum()) == 1
assert preserved.loc[
    preserved["outcome_status"].eq("COMPLETE"), "price_fingerprint"
].iloc[0] == "c" * 64

workflow = (ROOT / ".github" / "workflows" / "reconcile-research-ledgers.yml").read_text(encoding="utf-8")
assert "run.get('event') != 'schedule'" in workflow
assert "priority_outcome_ingest_only.py" in workflow
assert workflow.count("python priority_outcomes.py update") == 1
assert "Mature all available 5, 10, and 20-session outcomes once" in workflow
assert "live_session_readiness_with_recovery.py build" not in workflow.split(
    "Rebuild audit, eligibility, and decision ledgers", 1
)[1].split("Mature all available", 1)[0]

print("reconciliation ingest-only validation passed")
