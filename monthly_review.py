"""Build a read-only monthly operations, quality, and evidence review.

The report aggregates existing canonical sources and writes only to a caller-
provided output directory. It never changes production state, strategy, score,
priority rules, paper execution, or live orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

REVIEW_VERSION = "2026-07-13-monthly-operations-review-v1"
DEFAULT_OUTPUT_DIR = "output/monthly_review"

CANONICAL_PATHS = {
    "operations_audit": "research/operations/daily_production_audit.csv",
    "operations_status": "research/operations/daily_production_audit_status.json",
    "ranking_history": "data/momentum_daily_ranking.csv",
    "operations_heartbeat": "data/operations_heartbeat.json",
    "priority_decisions": "research/priority_outcomes/daily_research_decisions.csv",
    "priority_outcomes": "research/priority_outcomes/daily_research_outcomes.csv",
    "priority_calibration": "research/priority_outcomes/latest_calibration.json",
    "forward_status": "data/volume_component_forward_status.json",
    "evidence_catalog": "research/evidence_catalog.yaml",
    "strategy_approvals": "research/strategy_approvals.yaml",
}


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def optional_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "nat"} else text


def to_float(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def to_int(value: Any) -> int | None:
    converted = to_float(value)
    return None if converted is None else int(converted)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    return optional_text(value).lower() in {"true", "1", "yes", "y"}


def safe_ratio(numerator: int | float, denominator: int | float) -> float | None:
    return None if not denominator else float(numerator) / float(denominator)


def load_csv(path: str | Path, dtype: dict[str, Any] | None = None) -> pd.DataFrame:
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(target, dtype=dtype)
    except Exception:
        return pd.DataFrame()


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_yaml(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def month_bounds(review_month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    try:
        start = pd.Timestamp(f"{review_month}-01")
    except Exception as exc:
        raise ValueError(f"invalid review month: {review_month}") from exc
    if start.strftime("%Y-%m") != review_month:
        raise ValueError(f"invalid review month: {review_month}")
    return start, start + pd.offsets.MonthBegin(1)


def default_review_month(today_value: date | None = None) -> str:
    today_value = today_value or date.today()
    current = pd.Timestamp(today_value.replace(day=1))
    return (current - pd.offsets.MonthBegin(1)).strftime("%Y-%m")


def filter_month(frame: pd.DataFrame, column: str, review_month: str) -> pd.DataFrame:
    if frame.empty or column not in frame.columns:
        return frame.iloc[0:0].copy()
    start, end = month_bounds(review_month)
    values = pd.to_datetime(frame[column], errors="coerce")
    return frame[values.ge(start) & values.lt(end)].copy()


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame.columns:
        return pd.Series(False, index=frame.index, dtype=bool)
    return frame[column].map(to_bool).astype(bool)


def bootstrap_mean_ci(values: pd.Series, seed_text: str, iterations: int = 2000) -> tuple[float | None, float | None]:
    numeric = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(numeric) < 2:
        return None, None
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    samples = rng.choice(numeric, size=(iterations, len(numeric)), replace=True).mean(axis=1)
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def source_entry(key: str, repository: str, commit_sha: str) -> dict[str, Any]:
    path = CANONICAL_PATHS[key]
    url = ""
    if repository:
        ref = commit_sha or "main"
        url = f"https://github.com/{repository}/blob/{ref}/{path}"
    return {"key": key, "path": path, "url": url, "exists": Path(path).is_file()}


def resolve_incidents(monthly_runs: pd.DataFrame) -> list[dict[str, Any]]:
    if monthly_runs.empty:
        return []
    work = monthly_runs.copy()
    work["_time"] = pd.to_datetime(work.get("updated_at_utc"), errors="coerce", utc=True)
    failure_mask = (
        work.get("audit_status", pd.Series(index=work.index, dtype=str)).astype(str).ne("PASS")
        | work.get("upstream_conclusion", pd.Series(index=work.index, dtype=str)).astype(str).str.lower().ne("success")
    )
    incidents: list[dict[str, Any]] = []
    for _, row in work[failure_mask].sort_values("_time").iterrows():
        report_date = optional_text(row.get("report_date")) or optional_text(row.get("intended_date_jst"))
        later_pass = work[
            work.get("report_date", pd.Series(index=work.index, dtype=str)).astype(str).eq(report_date)
            & work.get("audit_status", pd.Series(index=work.index, dtype=str)).astype(str).eq("PASS")
            & work["_time"].gt(row["_time"])
        ]
        incidents.append({
            "workflow_run_id": optional_text(row.get("workflow_run_id")),
            "workflow_run_url": optional_text(row.get("workflow_run_url")),
            "report_date": report_date,
            "failure": optional_text(row.get("audit_failures")) or f"upstream={optional_text(row.get('upstream_conclusion'))}",
            "notification_present": to_bool(row.get("notification_present")),
            "resolution_status": "RECOVERED_BY_LATER_PASS" if not later_pass.empty else "OPEN_OR_UNRECORDED",
            "corrective_action": "LATER_PASS_RECORDED" if not later_pass.empty else "NOT_RECORDED_IN_AUDIT_LEDGER",
        })
    return incidents


def operations_section(audit: pd.DataFrame, review_month: str) -> dict[str, Any]:
    monthly = filter_month(audit, "intended_date_jst", review_month)
    if monthly.empty:
        monthly = filter_month(audit, "report_date", review_month)
    scheduled = monthly[
        monthly.get("upstream_event", pd.Series(index=monthly.index, dtype=str)).astype(str).eq("schedule")
    ] if not monthly.empty else monthly
    full_runs = monthly[bool_series(monthly, "full_state_update")] if not monthly.empty else monthly
    upstream_success = int(
        scheduled.get("upstream_conclusion", pd.Series(index=scheduled.index, dtype=str))
        .astype(str).str.lower().eq("success").sum()
    ) if not scheduled.empty else 0
    audit_pass = int(
        monthly.get("audit_status", pd.Series(index=monthly.index, dtype=str)).astype(str).eq("PASS").sum()
    ) if not monthly.empty else 0
    report_present = int(bool_series(monthly, "report_present").sum()) if not monthly.empty else 0
    durations = numeric_series(monthly, "duration_seconds").dropna()
    retrieval = numeric_series(full_runs, "retrieval_coverage").dropna()
    ranking_duplicates = numeric_series(monthly, "ranking_duplicate_count").fillna(0)
    market_duplicates = numeric_series(monthly, "market_temperature_duplicate_count").fillna(0)
    recovery_eligible = full_runs if not full_runs.empty else monthly.iloc[0:0]
    recovery_pass = int(
        (
            recovery_eligible.get("recovery_status", pd.Series(index=recovery_eligible.index, dtype=str)).astype(str).eq("SEALED")
            & bool_series(recovery_eligible, "recovery_complete")
        ).sum()
    ) if not recovery_eligible.empty else 0
    maintenance_pass = int(
        recovery_eligible.get("maintenance_status", pd.Series(index=recovery_eligible.index, dtype=str)).astype(str).eq("PASS").sum()
    ) if not recovery_eligible.empty else 0
    stale_mask = pd.Series(False, index=monthly.index, dtype=bool)
    if not monthly.empty:
        freshness = monthly.get("market_data_freshness", pd.Series(index=monthly.index, dtype=str)).fillna("").astype(str)
        current_ratio = numeric_series(monthly, "current_day_price_ratio")
        stale_mask = (freshness.ne("") & ~freshness.isin(["FRESH", "CURRENT", "PASS"])) | current_ratio.lt(1.0).fillna(False)
    failures = monthly[
        monthly.get("audit_status", pd.Series(index=monthly.index, dtype=str)).astype(str).ne("PASS")
    ] if not monthly.empty else monthly
    notification_covered = int(bool_series(failures, "notification_present").sum()) if not failures.empty else 0
    incidents = resolve_incidents(monthly)
    return {
        "audited_run_count": int(len(monthly)),
        "scheduled_run_count": int(len(scheduled)),
        "scheduled_success_count": upstream_success,
        "scheduled_success_rate": safe_ratio(upstream_success, len(scheduled)),
        "audit_pass_count": audit_pass,
        "audit_pass_rate": safe_ratio(audit_pass, len(monthly)),
        "report_present_count": report_present,
        "report_generation_rate": safe_ratio(report_present, len(monthly)),
        "completion_slo_seconds": 1800,
        "completion_slo_pass_count": int(durations.le(1800).sum()),
        "completion_slo_rate": safe_ratio(int(durations.le(1800).sum()), len(durations)),
        "average_duration_seconds": float(durations.mean()) if len(durations) else None,
        "maximum_duration_seconds": float(durations.max()) if len(durations) else None,
        "average_universe_count": float(numeric_series(full_runs, "workbook_universe_count").dropna().mean()) if numeric_series(full_runs, "workbook_universe_count").notna().any() else None,
        "average_scan_count": float(numeric_series(full_runs, "workbook_scan_count").dropna().mean()) if numeric_series(full_runs, "workbook_scan_count").notna().any() else None,
        "minimum_retrieval_coverage": float(retrieval.min()) if len(retrieval) else None,
        "average_retrieval_coverage": float(retrieval.mean()) if len(retrieval) else None,
        "stale_or_partial_run_count": int(stale_mask.sum()),
        "ranking_duplicate_row_count": int(ranking_duplicates.sum()),
        "market_temperature_duplicate_row_count": int(market_duplicates.sum()),
        "recovery_eligible_run_count": int(len(recovery_eligible)),
        "recovery_sealed_count": recovery_pass,
        "recovery_sealed_rate": safe_ratio(recovery_pass, len(recovery_eligible)),
        "maintenance_pass_count": maintenance_pass,
        "maintenance_pass_rate": safe_ratio(maintenance_pass, len(recovery_eligible)),
        "failed_audit_run_count": int(len(failures)),
        "failure_notification_count": notification_covered,
        "failure_notification_coverage": safe_ratio(notification_covered, len(failures)),
        "email_delivery_observable": False,
        "email_delivery_status": "NOT_CAPTURED_SEPARATELY_FROM_WORKFLOW_SUCCESS",
        "incidents": incidents,
    }


def data_quality_section(ranking: pd.DataFrame, decisions: pd.DataFrame, review_month: str) -> dict[str, Any]:
    monthly_ranking = filter_month(ranking, "date", review_month)
    monthly_decisions = filter_month(decisions, "decision_date", review_month)
    grade_counts: dict[str, int] = {grade: 0 for grade in ["A", "B", "C", "D"]}
    source = monthly_ranking if not monthly_ranking.empty and "data_quality_grade" in monthly_ranking.columns else monthly_decisions
    if not source.empty and "data_quality_grade" in source.columns:
        counts = source["data_quality_grade"].fillna("").astype(str).value_counts()
        for grade in grade_counts:
            grade_counts[grade] = int(counts.get(grade, 0))
    current_rate = None
    if not monthly_ranking.empty and "data_quality_current" in monthly_ranking.columns:
        current_rate = float(bool_series(monthly_ranking, "data_quality_current").mean())
    corporate_action_count = int(bool_series(monthly_ranking, "data_quality_corporate_action_suspected").sum()) if not monthly_ranking.empty else 0
    invalid_a = 0
    if not monthly_decisions.empty:
        invalid_a = int(
            (
                monthly_decisions.get("research_bucket", pd.Series(index=monthly_decisions.index, dtype=str)).astype(str).eq("A")
                & monthly_decisions.get("data_quality_grade", pd.Series(index=monthly_decisions.index, dtype=str)).astype(str).isin(["C", "D"])
            ).sum()
        )
    return {
        "assessed_row_count": int(len(source)),
        "grade_counts": grade_counts,
        "grade_a_or_b_rate": safe_ratio(grade_counts["A"] + grade_counts["B"], len(source)),
        "current_date_rate": current_rate,
        "possible_corporate_action_warning_count": corporate_action_count,
        "quality_c_or_d_in_priority_a_count": invalid_a,
        "quality_gate_passed": invalid_a == 0,
    }


def user_value_section(decisions: pd.DataFrame, review_month: str) -> dict[str, Any]:
    monthly = filter_month(decisions, "decision_date", review_month)
    bucket_counts = {bucket: 0 for bucket in ["A", "B", "C", "Watch", "Skip"]}
    if not monthly.empty and "research_bucket" in monthly.columns:
        counts = monthly["research_bucket"].fillna("").astype(str).value_counts()
        for bucket in bucket_counts:
            bucket_counts[bucket] = int(counts.get(bucket, 0))
    dates = int(monthly["decision_date"].nunique()) if not monthly.empty and "decision_date" in monthly.columns else 0
    action_list = bool_series(monthly, "daily_action_list") if not monthly.empty else pd.Series(dtype=bool)
    action_per_day = monthly[action_list].groupby("decision_date").size() if not monthly.empty and "decision_date" in monthly.columns else pd.Series(dtype=float)
    a_per_day = monthly[monthly.get("research_bucket", pd.Series(index=monthly.index, dtype=str)).astype(str).eq("A")].groupby("decision_date").size() if not monthly.empty and "decision_date" in monthly.columns else pd.Series(dtype=float)
    required = ["why_today", "what_changed", "risk_summary", "next_research_questions"]
    explanation_complete = pd.Series(True, index=monthly.index, dtype=bool)
    for column in required:
        if column not in monthly.columns:
            explanation_complete &= False
        else:
            explanation_complete &= monthly[column].fillna("").astype(str).str.strip().ne("")
    return {
        "decision_count": int(len(monthly)),
        "decision_date_count": dates,
        "bucket_counts": bucket_counts,
        "daily_action_list_count": int(action_list.sum()) if len(action_list) else 0,
        "average_action_list_size": float(action_per_day.mean()) if len(action_per_day) else None,
        "maximum_action_list_size": int(action_per_day.max()) if len(action_per_day) else 0,
        "maximum_priority_a_size": int(a_per_day.max()) if len(a_per_day) else 0,
        "action_list_cap_violation_days": int((action_per_day > 10).sum()) if len(action_per_day) else 0,
        "priority_a_cap_violation_days": int((a_per_day > 5).sum()) if len(a_per_day) else 0,
        "explanation_complete_count": int(explanation_complete.sum()) if len(monthly) else 0,
        "explanation_complete_rate": float(explanation_complete.mean()) if len(monthly) else None,
    }


def monthly_outcome_rows(outcomes: pd.DataFrame, review_month: str) -> list[dict[str, Any]]:
    monthly = filter_month(outcomes, "decision_date", review_month)
    complete = monthly[monthly.get("outcome_status", pd.Series(index=monthly.index, dtype=str)).astype(str).eq("COMPLETE")].copy() if not monthly.empty else monthly
    rows: list[dict[str, Any]] = []
    if complete.empty:
        return rows
    for (bucket, horizon), group in complete.groupby(["research_bucket", "horizon_sessions"], dropna=False):
        market_excess = numeric_series(group, "market_excess_return").dropna()
        net_return = numeric_series(group, "net_return").dropna()
        low, high = bootstrap_mean_ci(market_excess, f"{review_month}|{bucket}|{horizon}")
        rows.append({
            "research_bucket": optional_text(bucket) or "UNKNOWN",
            "horizon_sessions": int(horizon),
            "sample_size": int(len(group)),
            "mean_net_return": float(net_return.mean()) if len(net_return) else None,
            "mean_market_excess_return": float(market_excess.mean()) if len(market_excess) else None,
            "positive_market_excess_rate": float((market_excess > 0).mean()) if len(market_excess) else None,
            "bootstrap_ci_lower": low,
            "bootstrap_ci_upper": high,
            "small_sample_warning": len(group) < 30,
        })
    return sorted(rows, key=lambda row: (row["research_bucket"], row["horizon_sessions"]))


def outcome_section(outcomes: pd.DataFrame, calibration: dict[str, Any], review_month: str) -> dict[str, Any]:
    monthly = filter_month(outcomes, "decision_date", review_month)
    status_counts: dict[str, int] = {}
    if not monthly.empty and "outcome_status" in monthly.columns:
        status_counts = {str(key): int(value) for key, value in monthly["outcome_status"].fillna("").astype(str).value_counts().items()}
    horizon_counts: list[dict[str, Any]] = []
    if not monthly.empty and "horizon_sessions" in monthly.columns:
        for horizon, group in monthly.groupby("horizon_sessions"):
            horizon_counts.append({
                "horizon_sessions": int(horizon),
                "row_count": int(len(group)),
                "complete_count": int(group.get("outcome_status", pd.Series(index=group.index, dtype=str)).astype(str).eq("COMPLETE").sum()),
                "pending_count": int(group.get("outcome_status", pd.Series(index=group.index, dtype=str)).astype(str).eq("PENDING").sum()),
            })
    return {
        "monthly_outcome_row_count": int(len(monthly)),
        "monthly_status_counts": status_counts,
        "monthly_horizon_counts": sorted(horizon_counts, key=lambda row: row["horizon_sessions"]),
        "monthly_bucket_calibration": monthly_outcome_rows(outcomes, review_month),
        "global_decision_count": int(calibration.get("decision_count", 0) or 0),
        "global_complete_outcome_count": int(calibration.get("complete_outcome_count", 0) or 0),
        "global_pending_outcome_count": int(calibration.get("pending_outcome_count", 0) or 0),
        "global_lookahead_violation_count": int(calibration.get("lookahead_violation_count", 0) or 0),
        "global_review_gates": calibration.get("review_gates", []),
        "ready_for_human_priority_rule_review": calibration.get("ready_for_human_priority_rule_review") is True,
        "production_rule_change_allowed": calibration.get("production_rule_change_allowed") is True,
    }


def forward_section(status: dict[str, Any]) -> dict[str, Any]:
    horizons: list[dict[str, Any]] = []
    for key, value in sorted((status.get("horizons") or {}).items(), key=lambda pair: str(pair[0])):
        if not isinstance(value, dict):
            continue
        horizons.append({
            "horizon": int(value.get("horizon_days", key)),
            "baseline_outcome_count": int(value.get("baseline_outcome_count", 0) or 0),
            "tested_outcome_count": int(value.get("tested_outcome_count", 0) or 0),
            "required_outcomes_per_variant": int(value.get("required_outcomes_per_variant", 0) or 0),
            "paired_date_count": int(value.get("paired_date_count", 0) or 0),
            "required_paired_dates": int(value.get("required_paired_dates", 0) or 0),
            "sample_adequate": value.get("sample_adequate") is True,
            "mean_daily_difference": to_float(value.get("mean_daily_difference")),
            "ci_low": to_float(value.get("ci_low")),
            "ci_high": to_float(value.get("ci_high")),
            "two_sided_p_value": to_float(value.get("two_sided_p_value")),
        })
    return {
        "study_id": optional_text(status.get("study_id")),
        "evidence_status": optional_text(status.get("evidence_status")) or "UNKNOWN",
        "sample_adequate": status.get("sample_adequate") is True,
        "strategy_fingerprint": optional_text(status.get("strategy_fingerprint")),
        "source_run_id": optional_text(status.get("source_run_id")),
        "horizons": horizons,
        "promotion_evidence_allowed": status.get("promotion_evidence_allowed") is True,
        "automatic_weight_change": status.get("automatic_weight_change") is True,
        "automatic_strategy_change": status.get("automatic_strategy_change") is True,
        "manual_review_required": status.get("manual_review_required") is True,
    }


def approval_timestamp(record: dict[str, Any]) -> str:
    for key in ["approved_at_utc", "reviewed_at_utc", "timestamp_utc", "created_at_utc", "date"]:
        value = optional_text(record.get(key))
        if value:
            return value
    return ""


def strategy_section(catalog: dict[str, Any], approvals: dict[str, Any], review_month: str) -> dict[str, Any]:
    subject = catalog.get("subject", {}) if isinstance(catalog.get("subject"), dict) else {}
    records = approvals.get("approvals", []) if isinstance(approvals.get("approvals"), list) else []
    monthly_records: list[dict[str, Any]] = []
    start, end = month_bounds(review_month)
    for record in records:
        if not isinstance(record, dict):
            continue
        timestamp = pd.to_datetime(approval_timestamp(record), errors="coerce", utc=True)
        if pd.isna(timestamp):
            continue
        naive = timestamp.tz_convert(None)
        if start <= naive < end:
            monthly_records.append(record)
    changes: list[dict[str, Any]] = []
    for record in monthly_records:
        changes.append({
            "approval_id": optional_text(record.get("approval_id")) or optional_text(record.get("id")),
            "decision": optional_text(record.get("decision")) or optional_text(record.get("status")),
            "scope": optional_text(record.get("scope")),
            "timestamp": approval_timestamp(record),
            "strategy_fingerprint": optional_text(record.get("strategy_fingerprint")),
        })
    return {
        "current_production_weight_points": to_int(subject.get("current_production_weight_points")),
        "current_decision": optional_text(subject.get("current_decision")),
        "historical_consensus": optional_text(subject.get("historical_consensus")),
        "governing_study_id": optional_text(subject.get("governing_study_id")),
        "automatic_weight_change_allowed": subject.get("automatic_weight_change_allowed") is True,
        "automatic_strategy_change_allowed": subject.get("automatic_strategy_change_allowed") is True,
        "manual_review_required": subject.get("manual_review_required") is True,
        "approved_strategy_change_count": len(changes),
        "approved_strategy_changes": changes,
        "expected_no_change": len(changes) == 0,
    }


def review_state(sections: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    operations = sections["operations"]
    quality = sections["data_quality"]
    outcomes = sections["priority_outcomes"]
    forward = sections["forward_evidence"]
    strategy = sections["strategy_governance"]
    if operations["audited_run_count"] == 0:
        reasons.append("no audited production runs in review month")
    if operations["failed_audit_run_count"] > 0:
        reasons.append("one or more operational audit failures")
    if operations["ranking_duplicate_row_count"] > 0 or operations["market_temperature_duplicate_row_count"] > 0:
        reasons.append("duplicate production rows detected")
    if operations["stale_or_partial_run_count"] > 0:
        reasons.append("stale or partial market data detected")
    if quality["quality_c_or_d_in_priority_a_count"] > 0:
        reasons.append("quality C/D remained in priority A")
    if outcomes["global_lookahead_violation_count"] > 0:
        reasons.append("priority outcome lookahead violation")
    if outcomes["production_rule_change_allowed"]:
        reasons.append("priority outcome source unexpectedly permits production rule changes")
    if forward["automatic_weight_change"] or forward["automatic_strategy_change"]:
        reasons.append("forward evidence source unexpectedly permits automatic changes")
    if strategy["automatic_weight_change_allowed"] or strategy["automatic_strategy_change_allowed"]:
        reasons.append("evidence catalog unexpectedly permits automatic changes")
    if reasons == ["no audited production runs in review month"]:
        return "ACCUMULATING", reasons
    return ("REVIEW_REQUIRED" if reasons else "PASS"), reasons


def build_review(
    review_month: str,
    repository: str = "",
    commit_sha: str = "",
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    month_bounds(review_month)
    audit = load_csv(CANONICAL_PATHS["operations_audit"], dtype={"workflow_run_id": str})
    ranking = load_csv(CANONICAL_PATHS["ranking_history"], dtype={"code": str})
    decisions = load_csv(CANONICAL_PATHS["priority_decisions"], dtype={"code": str, "decision_id": str})
    outcomes = load_csv(CANONICAL_PATHS["priority_outcomes"], dtype={"code": str, "decision_id": str})
    calibration = load_json(CANONICAL_PATHS["priority_calibration"])
    forward_status = load_json(CANONICAL_PATHS["forward_status"])
    evidence_catalog = load_yaml(CANONICAL_PATHS["evidence_catalog"])
    approvals = load_yaml(CANONICAL_PATHS["strategy_approvals"])
    sections = {
        "operations": operations_section(audit, review_month),
        "data_quality": data_quality_section(ranking, decisions, review_month),
        "user_value": user_value_section(decisions, review_month),
        "priority_outcomes": outcome_section(outcomes, calibration, review_month),
        "forward_evidence": forward_section(forward_status),
        "strategy_governance": strategy_section(evidence_catalog, approvals, review_month),
    }
    state, reasons = review_state(sections)
    sources = [source_entry(key, repository, commit_sha) for key in CANONICAL_PATHS]
    substantive = {
        "review_version": REVIEW_VERSION,
        "review_month": review_month,
        "review_state": state,
        "review_reasons": reasons,
        "repository": repository,
        "commit_sha": commit_sha,
        "sections": sections,
        "canonical_sources": sources,
        "known_measurement_gaps": [
            "email delivery is not captured separately from workflow/report success",
            "sector outcome comparison is a same-date decision-cohort proxy, not a licensed sector index",
            "small samples are not evidence of validated priority quality",
        ],
        "production_state_mutations": [],
        "automatic_score_change": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "automatic_priority_rule_change": False,
        "manual_review_required": True,
        "research_only": True,
    }
    payload = {
        **substantive,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def validate_review(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("review_version") != REVIEW_VERSION:
        errors.append("invalid review_version")
    try:
        month_bounds(str(payload.get("review_month", "")))
    except ValueError as exc:
        errors.append(str(exc))
    if payload.get("review_state") not in {"ACCUMULATING", "PASS", "REVIEW_REQUIRED"}:
        errors.append("invalid review_state")
    if payload.get("production_state_mutations") != []:
        errors.append("production_state_mutations must be empty")
    for key in [
        "automatic_score_change",
        "automatic_weight_change",
        "automatic_strategy_change",
        "automatic_priority_rule_change",
    ]:
        if payload.get(key) is not False:
            errors.append(f"{key} must be false")
    if payload.get("manual_review_required") is not True:
        errors.append("manual_review_required must be true")
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        errors.append("sections must be an object")
    else:
        required = {"operations", "data_quality", "user_value", "priority_outcomes", "forward_evidence", "strategy_governance"}
        if set(sections) != required:
            errors.append("section set mismatch")
        if sections.get("priority_outcomes", {}).get("production_rule_change_allowed") is True:
            errors.append("priority outcomes permit production rule change")
        forward = sections.get("forward_evidence", {})
        if forward.get("automatic_weight_change") is True or forward.get("automatic_strategy_change") is True:
            errors.append("forward evidence permits automatic changes")
    status_copy = dict(payload)
    supplied_status_hash = status_copy.pop("status_sha256", "")
    if supplied_status_hash != canonical_hash(status_copy):
        errors.append("status_sha256 mismatch")
    substantive = dict(status_copy)
    substantive.pop("generated_at_utc", None)
    supplied_fingerprint = substantive.pop("review_fingerprint", "")
    if supplied_fingerprint != canonical_hash(substantive):
        errors.append("review_fingerprint mismatch")
    return errors


def flatten_summary(payload: dict[str, Any]) -> dict[str, Any]:
    sections = payload["sections"]
    operations = sections["operations"]
    quality = sections["data_quality"]
    user = sections["user_value"]
    outcomes = sections["priority_outcomes"]
    forward = sections["forward_evidence"]
    strategy = sections["strategy_governance"]
    return {
        "review_version": payload["review_version"],
        "review_month": payload["review_month"],
        "review_state": payload["review_state"],
        "audited_run_count": operations["audited_run_count"],
        "scheduled_success_rate": operations["scheduled_success_rate"],
        "audit_pass_rate": operations["audit_pass_rate"],
        "report_generation_rate": operations["report_generation_rate"],
        "completion_slo_rate": operations["completion_slo_rate"],
        "minimum_retrieval_coverage": operations["minimum_retrieval_coverage"],
        "average_retrieval_coverage": operations["average_retrieval_coverage"],
        "stale_or_partial_run_count": operations["stale_or_partial_run_count"],
        "duplicate_row_count": operations["ranking_duplicate_row_count"] + operations["market_temperature_duplicate_row_count"],
        "recovery_sealed_rate": operations["recovery_sealed_rate"],
        "maintenance_pass_rate": operations["maintenance_pass_rate"],
        "data_quality_a": quality["grade_counts"]["A"],
        "data_quality_b": quality["grade_counts"]["B"],
        "data_quality_c": quality["grade_counts"]["C"],
        "data_quality_d": quality["grade_counts"]["D"],
        "quality_c_or_d_in_priority_a_count": quality["quality_c_or_d_in_priority_a_count"],
        "priority_a_count": user["bucket_counts"]["A"],
        "priority_b_count": user["bucket_counts"]["B"],
        "priority_c_count": user["bucket_counts"]["C"],
        "priority_watch_count": user["bucket_counts"]["Watch"],
        "priority_skip_count": user["bucket_counts"]["Skip"],
        "average_action_list_size": user["average_action_list_size"],
        "explanation_complete_rate": user["explanation_complete_rate"],
        "global_complete_outcome_count": outcomes["global_complete_outcome_count"],
        "global_pending_outcome_count": outcomes["global_pending_outcome_count"],
        "global_lookahead_violation_count": outcomes["global_lookahead_violation_count"],
        "priority_review_ready": outcomes["ready_for_human_priority_rule_review"],
        "forward_evidence_status": forward["evidence_status"],
        "forward_sample_adequate": forward["sample_adequate"],
        "production_weight_points": strategy["current_production_weight_points"],
        "strategy_decision": strategy["current_decision"],
        "approved_strategy_change_count": strategy["approved_strategy_change_count"],
        "generated_at_utc": payload["generated_at_utc"],
        "review_fingerprint": payload["review_fingerprint"],
    }


def fmt_pct(value: Any) -> str:
    number = to_float(value)
    return "-" if number is None else f"{number:.1%}"


def fmt_num(value: Any, digits: int = 1) -> str:
    number = to_float(value)
    return "-" if number is None else f"{number:.{digits}f}"


def markdown_report(payload: dict[str, Any]) -> str:
    sections = payload["sections"]
    operations = sections["operations"]
    quality = sections["data_quality"]
    user = sections["user_value"]
    outcomes = sections["priority_outcomes"]
    forward = sections["forward_evidence"]
    strategy = sections["strategy_governance"]
    lines = [
        f"# Monthly Operations and Evidence Review — {payload['review_month']}",
        "",
        f"State: **{payload['review_state']}**",
        f"Generated: `{payload['generated_at_utc']}`",
        f"Commit: `{payload.get('commit_sha') or '-'}`",
        "",
    ]
    if payload["review_reasons"]:
        lines.extend(["## Review reasons", ""])
        lines.extend(f"- {reason}" for reason in payload["review_reasons"])
        lines.append("")
    lines.extend([
        "## Operations",
        "",
        f"- Audited runs: **{operations['audited_run_count']}**",
        f"- Scheduled success: **{fmt_pct(operations['scheduled_success_rate'])}**",
        f"- Audit pass: **{fmt_pct(operations['audit_pass_rate'])}**",
        f"- Report generation: **{fmt_pct(operations['report_generation_rate'])}**",
        f"- Completion within 30 minutes: **{fmt_pct(operations['completion_slo_rate'])}**",
        f"- Retrieval coverage: min **{fmt_pct(operations['minimum_retrieval_coverage'])}**, average **{fmt_pct(operations['average_retrieval_coverage'])}**",
        f"- Stale/partial runs: **{operations['stale_or_partial_run_count']}**",
        f"- Duplicate rows: **{operations['ranking_duplicate_row_count'] + operations['market_temperature_duplicate_row_count']}**",
        f"- Recovery sealed: **{fmt_pct(operations['recovery_sealed_rate'])}**",
        f"- Maintenance pass: **{fmt_pct(operations['maintenance_pass_rate'])}**",
        f"- Email delivery: **{operations['email_delivery_status']}**",
        "",
        "## Data Quality",
        "",
        f"- Grades: A **{quality['grade_counts']['A']}** / B **{quality['grade_counts']['B']}** / C **{quality['grade_counts']['C']}** / D **{quality['grade_counts']['D']}**",
        f"- A/B rate: **{fmt_pct(quality['grade_a_or_b_rate'])}**",
        f"- Current-date rate: **{fmt_pct(quality['current_date_rate'])}**",
        f"- Possible corporate-action warnings: **{quality['possible_corporate_action_warning_count']}**",
        f"- Quality C/D remaining in priority A: **{quality['quality_c_or_d_in_priority_a_count']}**",
        "",
        "## Daily Research Focus",
        "",
        f"- Decisions: **{user['decision_count']}** across **{user['decision_date_count']}** dates",
        f"- Buckets: A **{user['bucket_counts']['A']}** / B **{user['bucket_counts']['B']}** / C **{user['bucket_counts']['C']}** / Watch **{user['bucket_counts']['Watch']}** / Skip **{user['bucket_counts']['Skip']}**",
        f"- Average action list: **{fmt_num(user['average_action_list_size'])}**; maximum **{user['maximum_action_list_size']}**",
        f"- A-cap violation days: **{user['priority_a_cap_violation_days']}**",
        f"- Action-list cap violation days: **{user['action_list_cap_violation_days']}**",
        f"- Explanation completeness: **{fmt_pct(user['explanation_complete_rate'])}**",
        "",
        "## 5/10/20-session calibration",
        "",
        f"- Global complete outcomes: **{outcomes['global_complete_outcome_count']}**",
        f"- Global pending outcomes: **{outcomes['global_pending_outcome_count']}**",
        f"- Lookahead violations: **{outcomes['global_lookahead_violation_count']}**",
        f"- Ready for human priority-rule review: **{outcomes['ready_for_human_priority_rule_review']}**",
        f"- Production rule change allowed: **{outcomes['production_rule_change_allowed']}**",
        "",
        "| Bucket | Horizon | N | Mean net | Mean TOPIX excess | 95% CI | Positive excess | Warning |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in outcomes["monthly_bucket_calibration"]:
        ci = f"{fmt_pct(row['bootstrap_ci_lower'])} to {fmt_pct(row['bootstrap_ci_upper'])}"
        lines.append(
            f"| {row['research_bucket']} | {row['horizon_sessions']} | {row['sample_size']} | "
            f"{fmt_pct(row['mean_net_return'])} | {fmt_pct(row['mean_market_excess_return'])} | {ci} | "
            f"{fmt_pct(row['positive_market_excess_rate'])} | {'SMALL SAMPLE' if row['small_sample_warning'] else ''} |"
        )
    lines.extend([
        "",
        "## Forward Evidence",
        "",
        f"- Study: **{forward['study_id'] or '-'}**",
        f"- Status: **{forward['evidence_status']}**",
        f"- Sample adequate: **{forward['sample_adequate']}**",
        f"- Source run: **{forward['source_run_id'] or '-'}**",
        "",
        "| Horizon | Baseline | Tested | Paired dates | Gate |",
        "|---:|---:|---:|---:|---|",
    ])
    for horizon in forward["horizons"]:
        lines.append(
            f"| {horizon['horizon']} | {horizon['baseline_outcome_count']}/{horizon['required_outcomes_per_variant']} | "
            f"{horizon['tested_outcome_count']}/{horizon['required_outcomes_per_variant']} | "
            f"{horizon['paired_date_count']}/{horizon['required_paired_dates']} | "
            f"{'PASS' if horizon['sample_adequate'] else 'ACCUMULATING'} |"
        )
    lines.extend([
        "",
        "## Strategy governance",
        "",
        f"- Current volume-ratio weight: **{strategy['current_production_weight_points']} points**",
        f"- Current decision: **{strategy['current_decision'] or '-'}**",
        f"- Historical consensus: **{strategy['historical_consensus'] or '-'}**",
        f"- Approved strategy changes this month: **{strategy['approved_strategy_change_count']}**",
        f"- Automatic weight change allowed: **{strategy['automatic_weight_change_allowed']}**",
        f"- Automatic strategy change allowed: **{strategy['automatic_strategy_change_allowed']}**",
        "",
        "## Incidents and corrective actions",
        "",
    ])
    if operations["incidents"]:
        for incident in operations["incidents"]:
            lines.append(
                f"- `{incident['report_date']}` run `{incident['workflow_run_id']}`: {incident['failure']} — "
                f"{incident['resolution_status']} / {incident['corrective_action']}"
            )
    else:
        lines.append("- No incidents recorded in the audit ledger for this month.")
    lines.extend(["", "## Canonical sources", ""])
    for source in payload["canonical_sources"]:
        if source["url"]:
            lines.append(f"- [{source['path']}]({source['url']})")
        else:
            lines.append(f"- `{source['path']}`")
    lines.extend(["", "## Known measurement gaps", ""])
    lines.extend(f"- {gap}" for gap in payload["known_measurement_gaps"])
    lines.extend([
        "",
        "## Governance statement",
        "",
        "This report is read-only. It does not authorize an automatic score, weight, strategy, priority-rule, paper-execution, or production-state change. Manual review remains mandatory.",
        "",
    ])
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "monthly_review.json"
    csv_path = target / "monthly_review_summary.csv"
    markdown_path = target / "monthly_review.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame([flatten_summary(payload)]).to_csv(csv_path, index=False)
    markdown_path.write_text(markdown_report(payload), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(markdown_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the monthly operations and evidence review")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--month", default="")
    build.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    build.add_argument("--repository", default="")
    build.add_argument("--commit-sha", default="")
    build.add_argument("--generated-at-utc", default="")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--json", required=True)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "build":
        review_month = args.month or default_review_month()
        payload = build_review(
            review_month,
            repository=args.repository,
            commit_sha=args.commit_sha,
            generated_at_utc=args.generated_at_utc or None,
        )
        errors = validate_review(payload)
        if errors:
            print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        paths = write_outputs(payload, args.output_dir)
        print(json.dumps({"review": payload, "outputs": paths}, ensure_ascii=False, indent=2))
        return 0
    payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
    errors = validate_review(payload)
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
