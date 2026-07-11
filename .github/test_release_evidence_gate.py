from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
import json
import os
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main


run_health = pd.DataFrame([
    {"check_name": "overall", "status": "PASS", "actual": "PASS", "expected": "PASS", "detail": "ok"},
])
signal_governance = pd.DataFrame(columns=["status"])
paper_performance = pd.DataFrame([{
    "equity": main.PAPER_INITIAL_CAPITAL * 1.05,
    "win_rate": 0.60,
    "drawdown": -0.05,
}])
paper_trade_history = pd.DataFrame([{"trade": index} for index in range(20)])
paper_risk_budget = pd.DataFrame([{"status": "PASS"}])
sector_leader_performance = pd.DataFrame()

ready_evidence = {
    "readiness": "ELIGIBLE_FOR_MANUAL_REVIEW",
    "manual_review_eligible": True,
    "outcome_count": 120,
    "robustness_status": "ROBUST",
}
not_ready_evidence = {
    "readiness": "ACCUMULATING",
    "manual_review_eligible": False,
    "outcome_count": 40,
    "robustness_status": "ROBUST",
}

original_stats = main.performance_overall_stats
main.performance_overall_stats = lambda frame, horizon: {
    "count": 30 if horizon == 10 else 0,
    "win_rate": 0.60,
    "average_return": 0.02,
}
try:
    ready = main.build_release_readiness(
        run_health,
        signal_governance,
        sector_leader_performance,
        paper_performance,
        paper_trade_history,
        paper_risk_budget,
        ready_evidence,
    )
    assert main.release_status_value(ready) == "READY_FOR_MANUAL_REVIEW"
    evidence_row = ready[ready["criterion"] == "ライブ実行証拠"].iloc[0]
    assert bool(evidence_row["passed"])
    assert "120件" in str(evidence_row["actual"])

    blocked = main.build_release_readiness(
        run_health,
        signal_governance,
        sector_leader_performance,
        paper_performance,
        paper_trade_history,
        paper_risk_budget,
        not_ready_evidence,
    )
    assert main.release_status_value(blocked) == "PAPER_VALIDATION"
    evidence_row = blocked[blocked["criterion"] == "ライブ実行証拠"].iloc[0]
    assert not bool(evidence_row["passed"])

    # Old callers remain safe and can never become ready without the new evidence input.
    missing = main.build_release_readiness(
        run_health,
        signal_governance,
        sector_leader_performance,
        paper_performance,
        paper_trade_history,
        paper_risk_budget,
    )
    assert main.release_status_value(missing) == "PAPER_VALIDATION"
finally:
    main.performance_overall_stats = original_stats

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    status_path = root / "research_evidence_status.json"
    fingerprint = "current-strategy-fingerprint"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "execution_mode": main.EXECUTION_MODE,
        "strategy_fingerprint": fingerprint,
        "execution_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "provenance_valid": True,
        "manual_review_eligible": True,
        "readiness": "ELIGIBLE_FOR_MANUAL_REVIEW",
        "outcome_count": 120,
        "robustness_status": "ROBUST",
    }
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = main.load_research_evidence_status(
        str(status_path),
        current_fingerprint=fingerprint,
        now_utc=datetime.now(timezone.utc),
    )
    assert loaded["manual_review_eligible"] is True
    assert loaded["fingerprint_matches"] is True
    assert loaded["status_fresh"] is True

    mismatch = main.load_research_evidence_status(
        str(status_path),
        current_fingerprint="different",
        now_utc=datetime.now(timezone.utc),
    )
    assert mismatch["manual_review_eligible"] is False
    assert mismatch["fingerprint_matches"] is False

    payload["generated_at_utc"] = "2026-01-01T00:00:00+00:00"
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    stale = main.load_research_evidence_status(
        str(status_path),
        current_fingerprint=fingerprint,
        now_utc=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )
    assert stale["manual_review_eligible"] is False
    assert stale["status_fresh"] is False

    absent = main.load_research_evidence_status(str(root / "missing.json"), fingerprint)
    assert absent["readiness"] == "MISSING"
    assert absent["manual_review_eligible"] is False

print("daily release execution evidence gate validation passed")
