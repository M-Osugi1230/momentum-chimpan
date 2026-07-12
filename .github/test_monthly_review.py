from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import monthly_review as review


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def build_fixture(root: Path) -> None:
    audit = pd.DataFrame([
        {
            "workflow_run_id": "1001",
            "workflow_run_url": "https://example.test/runs/1001",
            "upstream_conclusion": "success",
            "upstream_event": "schedule",
            "created_at_utc": "2026-07-01T07:45:00Z",
            "updated_at_utc": "2026-07-01T08:00:00Z",
            "duration_seconds": 900,
            "intended_date_jst": "2026-07-01",
            "report_date": "2026-07-01",
            "audit_status": "PASS",
            "audit_failures": "",
            "full_state_update": True,
            "report_present": True,
            "retrieval_coverage": 0.99,
            "current_day_price_ratio": 1.0,
            "market_data_freshness": "FRESH",
            "ranking_duplicate_count": 0,
            "market_temperature_duplicate_count": 0,
            "recovery_status": "SEALED",
            "recovery_complete": True,
            "maintenance_status": "PASS",
            "notification_present": False,
            "workbook_universe_count": 3800,
            "workbook_scan_count": 3700,
        },
        {
            "workflow_run_id": "1002",
            "workflow_run_url": "https://example.test/runs/1002",
            "upstream_conclusion": "failure",
            "upstream_event": "schedule",
            "created_at_utc": "2026-07-02T07:45:00Z",
            "updated_at_utc": "2026-07-02T07:50:00Z",
            "duration_seconds": 300,
            "intended_date_jst": "2026-07-02",
            "report_date": "2026-07-02",
            "audit_status": "FAIL",
            "audit_failures": "daily report missing",
            "full_state_update": False,
            "report_present": False,
            "notification_present": True,
            "ranking_duplicate_count": 0,
            "market_temperature_duplicate_count": 0,
        },
        {
            "workflow_run_id": "1003",
            "workflow_run_url": "https://example.test/runs/1003",
            "upstream_conclusion": "success",
            "upstream_event": "workflow_dispatch",
            "created_at_utc": "2026-07-02T09:00:00Z",
            "updated_at_utc": "2026-07-02T09:20:00Z",
            "duration_seconds": 1200,
            "intended_date_jst": "2026-07-02",
            "report_date": "2026-07-02",
            "audit_status": "PASS",
            "audit_failures": "",
            "full_state_update": True,
            "report_present": True,
            "retrieval_coverage": 0.98,
            "current_day_price_ratio": 1.0,
            "market_data_freshness": "FRESH",
            "ranking_duplicate_count": 0,
            "market_temperature_duplicate_count": 0,
            "recovery_status": "SEALED",
            "recovery_complete": True,
            "maintenance_status": "PASS",
            "notification_present": False,
            "workbook_universe_count": 3810,
            "workbook_scan_count": 3710,
        },
        {
            "workflow_run_id": "1004",
            "workflow_run_url": "https://example.test/runs/1004",
            "upstream_conclusion": "success",
            "upstream_event": "schedule",
            "created_at_utc": "2026-07-03T07:45:00Z",
            "updated_at_utc": "2026-07-03T08:18:20Z",
            "duration_seconds": 2000,
            "intended_date_jst": "2026-07-03",
            "report_date": "2026-07-03",
            "audit_status": "PASS",
            "audit_failures": "",
            "full_state_update": True,
            "report_present": True,
            "retrieval_coverage": 0.995,
            "current_day_price_ratio": 1.0,
            "market_data_freshness": "FRESH",
            "ranking_duplicate_count": 0,
            "market_temperature_duplicate_count": 0,
            "recovery_status": "SEALED",
            "recovery_complete": True,
            "maintenance_status": "PASS",
            "notification_present": False,
            "workbook_universe_count": 3820,
            "workbook_scan_count": 3720,
        },
    ])
    audit_path = root / review.CANONICAL_PATHS["operations_audit"]
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(audit_path, index=False)

    write_json(root / review.CANONICAL_PATHS["operations_status"], {
        "audit_state": "ACCUMULATING",
        "market_session_count": 3,
        "automatic_strategy_change": False,
        "automatic_weight_change": False,
    })
    write_json(root / review.CANONICAL_PATHS["operations_heartbeat"], {
        "workflow_status": "SUCCESS",
        "report_date": "2026-07-03",
    })

    grades = ["A", "A", "A", "A", "B", "B", "C", "D"]
    ranking = pd.DataFrame([
        {
            "date": "2026-07-01" if index < 4 else "2026-07-02",
            "code": str(1100 + index),
            "data_quality_grade": grade,
            "data_quality_current": grade != "D",
            "data_quality_corporate_action_suspected": grade == "C",
        }
        for index, grade in enumerate(grades, start=1)
    ])
    ranking_path = root / review.CANONICAL_PATHS["ranking_history"]
    ranking_path.parent.mkdir(parents=True, exist_ok=True)
    ranking.to_csv(ranking_path, index=False)

    decision_rows = []
    buckets = ["A", "B", "C", "Watch", "Skip"]
    for day in ["2026-07-01", "2026-07-02"]:
        for position, bucket in enumerate(buckets, start=1):
            decision_rows.append({
                "decision_id": f"{day}-{bucket}",
                "decision_date": day,
                "code": str(2000 + position),
                "research_bucket": bucket,
                "daily_action_list": bucket in {"A", "B"},
                "daily_action_rank": position if bucket in {"A", "B"} else None,
                "data_quality_grade": "A" if bucket == "A" else "B" if bucket == "B" else "C",
                "why_today": f"{bucket} why",
                "what_changed": f"{bucket} change",
                "risk_summary": f"{bucket} risk",
                "next_research_questions": f"{bucket} next",
            })
    decisions = pd.DataFrame(decision_rows)
    decisions_path = root / review.CANONICAL_PATHS["priority_decisions"]
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions.to_csv(decisions_path, index=False)

    outcome_rows = []
    for bucket in ["A", "B"]:
        for horizon, net_return, market_excess in [
            (5, 0.04, 0.02),
            (10, 0.06, 0.03),
            (20, 0.08, 0.04),
        ]:
            outcome_rows.append({
                "decision_id": f"2026-07-01-{bucket}",
                "decision_date": "2026-07-01",
                "code": "2001" if bucket == "A" else "2002",
                "research_bucket": bucket,
                "horizon_sessions": horizon,
                "outcome_status": "COMPLETE",
                "net_return": net_return,
                "market_excess_return": market_excess,
            })
    outcome_rows.append({
        "decision_id": "2026-07-02-A",
        "decision_date": "2026-07-02",
        "code": "2001",
        "research_bucket": "A",
        "horizon_sessions": 20,
        "outcome_status": "PENDING",
    })
    outcomes = pd.DataFrame(outcome_rows)
    outcomes_path = root / review.CANONICAL_PATHS["priority_outcomes"]
    outcomes_path.parent.mkdir(parents=True, exist_ok=True)
    outcomes.to_csv(outcomes_path, index=False)

    gates = []
    for bucket in ["A", "B"]:
        for horizon in [5, 10, 20]:
            gates.append({
                "research_bucket": bucket,
                "horizon_sessions": horizon,
                "sample_size": 1,
                "distinct_decision_dates": 1,
                "minimum_sample_size": 30,
                "minimum_distinct_decision_dates": 20,
                "passed": False,
            })
    write_json(root / review.CANONICAL_PATHS["priority_calibration"], {
        "decision_count": 10,
        "complete_outcome_count": 6,
        "pending_outcome_count": 1,
        "lookahead_violation_count": 0,
        "review_gates": gates,
        "ready_for_human_priority_rule_review": False,
        "production_rule_change_allowed": False,
    })

    write_json(root / review.CANONICAL_PATHS["forward_status"], {
        "study_id": "volume-component-forward-evidence-v1",
        "evidence_status": "ACCUMULATING",
        "sample_adequate": False,
        "strategy_fingerprint": "f" * 64,
        "source_run_id": "run-500",
        "horizons": {
            "10": {
                "horizon_days": 10,
                "baseline_outcome_count": 20,
                "tested_outcome_count": 20,
                "required_outcomes_per_variant": 100,
                "paired_date_count": 4,
                "required_paired_dates": 20,
                "sample_adequate": False,
            },
            "20": {
                "horizon_days": 20,
                "baseline_outcome_count": 10,
                "tested_outcome_count": 10,
                "required_outcomes_per_variant": 100,
                "paired_date_count": 2,
                "required_paired_dates": 20,
                "sample_adequate": False,
            },
        },
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "manual_review_required": True,
    })

    write_yaml(root / review.CANONICAL_PATHS["evidence_catalog"], {
        "subject": {
            "current_production_weight_points": 15,
            "current_decision": "HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE",
            "historical_consensus": "CONFLICTED_TIME_UNSTABLE",
            "governing_study_id": "volume-component-forward-evidence-v1",
            "automatic_weight_change_allowed": False,
            "automatic_strategy_change_allowed": False,
            "manual_review_required": True,
        }
    })
    write_yaml(root / review.CANONICAL_PATHS["strategy_approvals"], {
        "schema_version": 1,
        "policy": {"automatic_activation": False},
        "approvals": [],
    })


assert review.default_review_month(pd.Timestamp("2026-08-15").date()) == "2026-07"
start, end = review.month_bounds("2026-07")
assert start.isoformat() == "2026-07-01T00:00:00"
assert end.isoformat() == "2026-08-01T00:00:00"

with TemporaryDirectory() as temporary:
    fixture_root = Path(temporary)
    build_fixture(fixture_root)
    previous = Path.cwd()
    os.chdir(fixture_root)
    try:
        payload = review.build_review(
            "2026-07",
            repository="M-Osugi1230/momentum-chimpan",
            commit_sha="a" * 40,
            generated_at_utc="2026-08-01T09:30:00+00:00",
        )
        assert review.validate_review(payload) == []
        assert payload["review_state"] == "REVIEW_REQUIRED"
        assert "one or more operational audit failures" in payload["review_reasons"]

        operations = payload["sections"]["operations"]
        assert operations["audited_run_count"] == 4
        assert operations["scheduled_run_count"] == 3
        assert operations["scheduled_success_count"] == 2
        assert operations["scheduled_success_rate"] == 2 / 3
        assert operations["audit_pass_count"] == 3
        assert operations["audit_pass_rate"] == 3 / 4
        assert operations["report_generation_rate"] == 3 / 4
        assert operations["completion_slo_rate"] == 3 / 4
        assert operations["minimum_retrieval_coverage"] == 0.98
        assert operations["ranking_duplicate_row_count"] == 0
        assert operations["market_temperature_duplicate_row_count"] == 0
        assert operations["recovery_sealed_rate"] == 1.0
        assert operations["maintenance_pass_rate"] == 1.0
        assert operations["failure_notification_coverage"] == 1.0
        assert operations["email_delivery_observable"] is False
        assert len(operations["incidents"]) == 1
        assert operations["incidents"][0]["resolution_status"] == "RECOVERED_BY_LATER_PASS"

        quality = payload["sections"]["data_quality"]
        assert quality["grade_counts"] == {"A": 4, "B": 2, "C": 1, "D": 1}
        assert quality["grade_a_or_b_rate"] == 0.75
        assert quality["current_date_rate"] == 0.875
        assert quality["possible_corporate_action_warning_count"] == 1
        assert quality["quality_c_or_d_in_priority_a_count"] == 0
        assert quality["quality_gate_passed"] is True

        user = payload["sections"]["user_value"]
        assert user["decision_count"] == 10
        assert user["decision_date_count"] == 2
        assert user["bucket_counts"] == {"A": 2, "B": 2, "C": 2, "Watch": 2, "Skip": 2}
        assert user["daily_action_list_count"] == 4
        assert user["average_action_list_size"] == 2.0
        assert user["maximum_action_list_size"] == 2
        assert user["maximum_priority_a_size"] == 1
        assert user["priority_a_cap_violation_days"] == 0
        assert user["action_list_cap_violation_days"] == 0
        assert user["explanation_complete_rate"] == 1.0

        outcome = payload["sections"]["priority_outcomes"]
        assert outcome["monthly_outcome_row_count"] == 7
        assert outcome["monthly_status_counts"] == {"COMPLETE": 6, "PENDING": 1}
        assert len(outcome["monthly_bucket_calibration"]) == 6
        assert all(row["sample_size"] == 1 for row in outcome["monthly_bucket_calibration"])
        assert all(row["small_sample_warning"] for row in outcome["monthly_bucket_calibration"])
        assert outcome["global_complete_outcome_count"] == 6
        assert outcome["global_pending_outcome_count"] == 1
        assert outcome["global_lookahead_violation_count"] == 0
        assert outcome["ready_for_human_priority_rule_review"] is False
        assert outcome["production_rule_change_allowed"] is False

        forward = payload["sections"]["forward_evidence"]
        assert forward["evidence_status"] == "ACCUMULATING"
        assert forward["sample_adequate"] is False
        assert forward["horizons"][0]["baseline_outcome_count"] == 20
        assert forward["horizons"][1]["paired_date_count"] == 2
        assert forward["automatic_weight_change"] is False
        assert forward["automatic_strategy_change"] is False

        strategy = payload["sections"]["strategy_governance"]
        assert strategy["current_production_weight_points"] == 15
        assert strategy["current_decision"] == "HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE"
        assert strategy["approved_strategy_change_count"] == 0
        assert strategy["expected_no_change"] is True
        assert strategy["automatic_weight_change_allowed"] is False
        assert strategy["automatic_strategy_change_allowed"] is False

        assert all(source["exists"] for source in payload["canonical_sources"])
        assert all("/blob/" in source["url"] for source in payload["canonical_sources"])
        assert payload["production_state_mutations"] == []
        assert payload["automatic_strategy_change"] is False
        assert payload["automatic_priority_rule_change"] is False

        summary = review.flatten_summary(payload)
        assert summary["production_weight_points"] == 15
        assert summary["approved_strategy_change_count"] == 0
        assert summary["global_complete_outcome_count"] == 6

        markdown = review.markdown_report(payload)
        assert "Monthly Operations and Evidence Review — 2026-07" in markdown
        assert "NOT_CAPTURED_SEPARATELY_FROM_WORKFLOW_SUCCESS" in markdown
        assert "RECOVERED_BY_LATER_PASS" in markdown
        assert "Current volume-ratio weight: **15 points**" in markdown
        assert "Approved strategy changes this month: **0**" in markdown
        assert "Production rule change allowed: **False**" in markdown
        assert "Manual review remains mandatory" in markdown

        outputs = review.write_outputs(payload, fixture_root / "output")
        assert Path(outputs["json"]).is_file()
        assert Path(outputs["csv"]).is_file()
        assert Path(outputs["markdown"]).is_file()
        written = json.loads(Path(outputs["json"]).read_text(encoding="utf-8"))
        assert review.validate_review(written) == []
        flat = pd.read_csv(outputs["csv"])
        assert flat.iloc[0]["review_state"] == "REVIEW_REQUIRED"
        assert int(flat.iloc[0]["production_weight_points"]) == 15

        tampered = copy.deepcopy(payload)
        tampered["automatic_strategy_change"] = True
        errors = review.validate_review(tampered)
        assert any("automatic_strategy_change must be false" in error for error in errors)
        assert any("status_sha256 mismatch" in error for error in errors)
    finally:
        os.chdir(previous)

workflow_path = ROOT / ".github" / "workflows" / "monthly-operations-review.yml"
workflow_text = workflow_path.read_text(encoding="utf-8")
workflow = yaml.safe_load(workflow_text)
assert "schedule" in workflow[True]
assert workflow["permissions"]["contents"] == "read"
assert ("git" + " push") not in workflow_text
assert ("contents:" + " write") not in workflow_text
assert ("EMAIL_" + "APP_PASSWORD") not in workflow_text
assert "monthly_review.py build" in workflow_text
assert "monthly_review.py validate" in workflow_text
assert "actions/upload-artifact@v4" in workflow_text
assert "retention-days: 365" in workflow_text

print("monthly operations review validation passed")
