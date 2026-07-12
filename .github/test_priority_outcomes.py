from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import priority_outcomes as tracker


FINGERPRINT = "a" * 64
DECISION_DATE = "2026-07-13"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_artifact(root: Path) -> Path:
    artifact = root / "momentum-operations-123"
    output = artifact / "output"
    data = artifact / "data"
    output.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    summary.append(["実行日", "状態更新実行", "アプリ版"])
    summary.append([DECISION_DATE, "YES", "test-app"])
    action = workbook.create_sheet("Action Priority")
    columns = [
        "code",
        "name",
        "sector33",
        "research_bucket",
        "daily_action_list",
        "daily_action_rank",
        "action_priority",
        "action_priority_before_quality",
        "action_priority_before_daily_focus",
        "momentum_rank",
        "momentum_score",
        "action_score",
        "expectancy_score",
        "expectancy_confidence",
        "lifecycle_status",
        "market_regime",
        "relative_strength_grade",
        "data_quality_grade",
        "data_quality_reason_codes",
        "why_today",
        "what_changed",
        "risk_summary",
        "next_research_questions",
        "focus_adjustment_reason",
        "daily_focus_version",
    ]
    action.append(columns)
    buckets = ["A", "B", "C", "Watch", "Skip"]
    sectors = ["電気機器", "電気機器", "電気機器", "電気機器", "機械"]
    for index, bucket in enumerate(buckets, start=1):
        row = {
            "code": str(1000 + index),
            "name": f"Candidate {bucket}",
            "sector33": sectors[index - 1],
            "research_bucket": bucket,
            "daily_action_list": bucket in {"A", "B"},
            "daily_action_rank": index if bucket in {"A", "B"} else None,
            "action_priority": bucket if bucket in {"A", "B", "C"} else "見送り",
            "action_priority_before_quality": "A" if bucket == "A" else bucket,
            "action_priority_before_daily_focus": "A" if bucket == "A" else bucket,
            "momentum_rank": index,
            "momentum_score": 90 - index,
            "action_score": 95 - index,
            "expectancy_score": 70 + index,
            "expectancy_confidence": "中",
            "lifecycle_status": "継続" if index <= 4 else "初登場",
            "market_regime": "やや強気",
            "relative_strength_grade": "A",
            "data_quality_grade": "A" if index <= 4 else "B",
            "data_quality_reason_codes": "" if index <= 4 else "BLANK_SECTOR",
            "why_today": f"{bucket}の今日の理由",
            "what_changed": f"{bucket}の変化",
            "risk_summary": f"{bucket}の注意",
            "next_research_questions": f"{bucket}の次の調査",
            "focus_adjustment_reason": "品質ゲートによる変更なし",
            "daily_focus_version": "2026-07-12-daily-research-focus-v1",
        }
        action.append([row[column] for column in columns])
    workbook.save(output / "daily_report.xlsx")

    write_json(data / "operations_heartbeat.json", {
        "workflow_status": "SUCCESS",
        "state_update_executed": True,
        "report_date": DECISION_DATE,
        "research_only": True,
    })
    write_json(data / "strategy_fingerprint.json", {
        "strategy_fingerprint": FINGERPRINT,
    })
    return artifact


def synthetic_prices(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    dates = pd.bdate_range("2026-07-13", periods=30)
    if ticker == "^TOPX":
        base = 2000.0
        slope = 2.0
    else:
        numeric = int(ticker.split(".")[0])
        base = 100.0 + (numeric - 1000) * 10.0
        slope = float(numeric - 998)
    return pd.DataFrame({
        "date": [value.date() for value in dates],
        "adjusted_open": [base + slope * index for index in range(len(dates))],
        "adjusted_close": [base + slope * index + 1.0 for index in range(len(dates))],
    })


policy = tracker.load_policy(ROOT / tracker.POLICY_PATH)
tracker.validate_policy(policy)
assert policy["execution_model"]["entry"] == "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"
assert policy["execution_model"]["same_day_close_entry_allowed"] is False
assert policy["execution_model"]["horizons_sessions"] == [5, 10, 20]
assert policy["governance"]["automatic_priority_rule_change"] is False
assert policy["governance"]["production_state_mutations"] == []

committed_decisions = tracker.load_decisions(ROOT / tracker.DEFAULT_DECISIONS)
committed_outcomes = tracker.load_outcomes(ROOT / tracker.DEFAULT_OUTCOMES)
committed_calibration = json.loads((ROOT / tracker.DEFAULT_CALIBRATION_JSON).read_text(encoding="utf-8"))
assert committed_decisions.empty
assert committed_outcomes.empty
assert tracker.validate_calibration(committed_calibration) == []
assert committed_calibration["ready_for_human_priority_rule_review"] is False
assert len(committed_calibration["review_gates"]) == 6
assert all(not gate["passed"] for gate in committed_calibration["review_gates"])

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    artifact = build_artifact(root)
    decisions = tracker.extract_decisions(
        artifact,
        source_run_id="123",
        source_run_url="https://example.test/runs/123",
        recorded_at_utc="2026-07-13T08:00:00+00:00",
        policy=policy,
    )
    assert len(decisions) == 5
    assert decisions["decision_id"].nunique() == 5
    assert decisions["strategy_fingerprint"].eq(FINGERPRINT).all()
    assert decisions["decision_date"].eq(DECISION_DATE).all()
    assert decisions["focus_policy_version"].eq("2026-07-12-daily-research-focus-v1").all()
    assert decisions["research_bucket"].tolist() == ["A", "B", "C", "Watch", "Skip"]
    assert decisions["daily_action_list"].tolist() == [True, True, False, False, False]
    assert decisions["why_today"].astype(str).str.strip().ne("").all()
    assert decisions["next_research_questions"].astype(str).str.strip().ne("").all()

    history = tracker.append_decisions(tracker.empty_decisions(), decisions)
    history = tracker.append_decisions(history, decisions)
    assert len(history) == 5, "same artifact decisions must be idempotent"

    outcomes = tracker.update_outcomes(
        history,
        tracker.empty_outcomes(),
        policy,
        price_loader=synthetic_prices,
        as_of_date="2026-08-31",
    )
    assert len(outcomes) == 15
    assert outcomes["outcome_status"].eq("COMPLETE").all()
    assert outcomes["same_day_close_entry"].eq(False).all()
    assert outcomes["no_lookahead_verified"].all()
    assert outcomes["entry_date"].eq("2026-07-14").all()
    first = outcomes[
        outcomes["code"].eq("1001")
        & outcomes["horizon_sessions"].eq(5)
    ].iloc[0]
    assert first["exit_date"] == "2026-07-20"
    expected_gross = 126.0 / 113.0 - 1.0
    assert np.isclose(first["gross_return"], expected_gross)
    assert np.isclose(first["net_return"], expected_gross - 0.002)
    assert first["market_return"] is not None
    assert first["market_excess_return"] is not None
    assert first["sector_peer_count"] == 3
    assert first["sector_proxy_return"] is not None
    assert first["sector_excess_return"] is not None

    preserved = tracker.update_outcomes(
        history,
        outcomes,
        policy,
        price_loader=lambda *args: (_ for _ in ()).throw(RuntimeError("must not refetch complete rows")),
        as_of_date="2026-09-30",
    )
    assert preserved["outcome_status"].eq("COMPLETE").all()
    assert preserved["price_fingerprint"].eq(outcomes["price_fingerprint"]).all()

    calibration = tracker.build_calibration(history, outcomes, policy)
    assert tracker.validate_calibration(calibration) == []
    assert calibration["decision_count"] == 5
    assert calibration["complete_outcome_count"] == 15
    assert calibration["lookahead_violation_count"] == 0
    assert calibration["ready_for_human_priority_rule_review"] is False
    assert calibration["production_rule_change_allowed"] is False
    bucket_rows = [
        row for row in calibration["calibration_rows"]
        if row["dimension"] == "research_bucket"
    ]
    assert len(bucket_rows) == 15
    assert all(row["small_sample_warning"] for row in bucket_rows)
    assert all(row["sample_size"] == 1 for row in bucket_rows)
    markdown = tracker.calibration_markdown(calibration)
    assert "A/B review gates" in markdown
    assert "SMALL SAMPLE" in markdown
    assert "Production rule change allowed: **False**" in markdown

    errors = tracker.validate_histories(history, outcomes, policy)
    assert errors == []

    tampered = copy.deepcopy(calibration)
    tampered["automatic_priority_rule_change"] = True
    tamper_errors = tracker.validate_calibration(tampered)
    assert any("automatic_priority_rule_change must be false" in error for error in tamper_errors)
    assert any("status_sha256 mismatch" in error for error in tamper_errors)

    short_dates = pd.bdate_range("2026-07-13", periods=4)
    def short_prices(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame({
            "date": [value.date() for value in short_dates],
            "adjusted_open": [100.0, 101.0, 102.0, 103.0],
            "adjusted_close": [100.5, 101.5, 102.5, 103.5],
        })
    pending = tracker.update_outcomes(
        history.iloc[[0]],
        tracker.empty_outcomes(),
        policy,
        price_loader=short_prices,
        as_of_date="2026-07-16",
    )
    assert pending["outcome_status"].eq("PENDING").all()
    assert pending["entry_date"].eq("2026-07-14").all()
    assert pending["same_day_close_entry"].eq(False).all()

    raw = pd.DataFrame({
        "Date": pd.bdate_range("2026-07-13", periods=2),
        "Open": [100.0, 102.0],
        "Close": [101.0, 103.0],
        "Adj Close": [50.5, 51.5],
    })
    adjusted = tracker.adjusted_price_frame(raw)
    assert np.isclose(adjusted.iloc[0]["adjusted_open"], 50.0)
    assert np.isclose(adjusted.iloc[1]["adjusted_close"], 51.5)

workflow_text = (ROOT / ".github" / "workflows" / "daily-priority-outcomes.yml").read_text(encoding="utf-8")
assert "Daily Momentum Report" in workflow_text
assert "workflow_run" in workflow_text
assert "schedule" in workflow_text
assert "actions: read" in workflow_text
assert "contents: write" in workflow_text
assert "research/priority_outcomes/daily_research_decisions.csv" in workflow_text
assert "research/priority_outcomes/daily_research_outcomes.csv" in workflow_text
assert "research/priority_outcomes/latest_calibration.json" in workflow_text
assert "research/priority_outcomes/latest_calibration.md" in workflow_text
assert "git add --" in workflow_text
staged = workflow_text.split("git add --", 1)[1]
assert "data/momentum_daily_ranking.csv" not in staged
assert "config.yaml" not in staged
assert "paper_portfolio.csv" not in staged
assert ("EMAIL_" + "APP_PASSWORD") not in workflow_text

print("priority outcome tracking validation passed")
