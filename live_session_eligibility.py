"""Persist exact-artifact eligibility for later Forward Evidence filtering.

The ledger is research-only. It never changes production ranking, score weights,
priority rules, paper execution, or live orders. Each row binds one completed
Daily Momentum Report run to its signed readiness decision and to a canonical
hash of that run's report-date ranking rows.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import live_session_readiness as readiness

ELIGIBILITY_VERSION = "2026-07-13-live-session-eligibility-v1"
DEFAULT_LEDGER = "research/evidence/live_session_eligibility.csv"
DEFAULT_STATUS = "research/evidence/live_session_eligibility_status.json"

HISTORY_COLUMNS = [
    "eligibility_version",
    "source_run_id",
    "source_run_url",
    "upstream_conclusion",
    "upstream_event",
    "head_sha",
    "created_at_utc",
    "updated_at_utc",
    "recorded_at_utc",
    "report_date",
    "strategy_fingerprint",
    "readiness_state",
    "eligible_for_forward_evidence",
    "eligible_for_priority_outcome_ingestion",
    "artifact_fingerprint",
    "readiness_fingerprint",
    "readiness_status_sha256",
    "ranking_date_row_count",
    "ranking_date_sha256",
    "critical_failure_count",
    "review_warning_count",
    "readiness_details",
    "research_only",
]

BOOL_COLUMNS = {
    "eligible_for_forward_evidence",
    "eligible_for_priority_outcome_ingestion",
    "research_only",
}
NUMERIC_COLUMNS = {
    "ranking_date_row_count",
    "critical_failure_count",
    "review_warning_count",
}


def canonical_hash(value: Any) -> str:
    return readiness.canonical_hash(value)


def sha256_file(path: str | Path) -> str:
    return readiness.sha256_file(path)


def optional_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    return optional_text(value).lower() in {"true", "1", "yes", "y"}


def normalized_scalar(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return "true" if value else "false"
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value).strip()


def ranking_date_payload(frame: pd.DataFrame, report_date: str) -> dict[str, Any]:
    if frame is None or frame.empty or "date" not in frame.columns:
        return {"report_date": report_date, "columns": [], "rows": []}
    work = frame[frame["date"].astype(str).eq(report_date)].copy()
    columns = sorted(str(column) for column in work.columns)
    if work.empty:
        return {"report_date": report_date, "columns": columns, "rows": []}
    if "code" in work.columns:
        work["code"] = work["code"].map(
            lambda value: optional_text(value).split(".")[0].zfill(4)
        )
    sort_columns = [column for column in ("code", "rank") if column in work.columns]
    if sort_columns:
        work = work.sort_values(sort_columns, kind="mergesort")
    rows = [
        {column: normalized_scalar(row.get(column)) for column in columns}
        for row in work.to_dict(orient="records")
    ]
    return {"report_date": report_date, "columns": columns, "rows": rows}


def ranking_date_fingerprint(path: str | Path, report_date: str) -> tuple[int, str]:
    target = Path(path)
    if not target.is_file() or not report_date:
        return 0, ""
    try:
        frame = pd.read_csv(target, dtype={"code": str, "date": str})
    except Exception:
        return 0, ""
    payload = ranking_date_payload(frame, report_date)
    rows = payload["rows"]
    return len(rows), canonical_hash(payload) if rows else ""


def empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=HISTORY_COLUMNS)


def load_history(path: str | Path = DEFAULT_LEDGER) -> pd.DataFrame:
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        return empty_history()
    try:
        frame = pd.read_csv(target, dtype={"source_run_id": str, "head_sha": str})
    except Exception:
        return empty_history()
    for column in HISTORY_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return normalize_history(frame[HISTORY_COLUMNS])


def normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in HISTORY_COLUMNS:
        if column not in work.columns:
            work[column] = None
    for column in BOOL_COLUMNS:
        work[column] = work[column].map(to_bool)
    for column in NUMERIC_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce").fillna(0).astype(int)
    for column in set(HISTORY_COLUMNS) - BOOL_COLUMNS - NUMERIC_COLUMNS:
        work[column] = work[column].fillna("").astype(str)
    return work[HISTORY_COLUMNS]


def append_record(history: pd.DataFrame, record: dict[str, Any]) -> pd.DataFrame:
    combined = pd.concat(
        [normalize_history(history), pd.DataFrame([record], columns=HISTORY_COLUMNS)],
        ignore_index=True,
    )
    combined["source_run_id"] = combined["source_run_id"].fillna("").astype(str)
    combined = combined.drop_duplicates(["source_run_id"], keep="last")
    combined["_sort"] = pd.to_datetime(combined["updated_at_utc"], errors="coerce", utc=True)
    combined = combined.sort_values(["_sort", "source_run_id"], na_position="first")
    return normalize_history(combined.drop(columns="_sort").reset_index(drop=True))


def critical_gate_details(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    for gate in payload.get("gates", []):
        if not isinstance(gate, dict):
            continue
        state = optional_text(gate.get("state"))
        detail = f"{optional_text(gate.get('gate'))}: {optional_text(gate.get('detail'))}".strip()
        if state == "FAIL":
            failures.append(detail)
        elif state == "REVIEW_REQUIRED":
            warnings.append(detail)
    return failures, warnings


def build_record(
    artifact_root: str | Path,
    source_run_id: str,
    source_run_url: str,
    upstream_conclusion: str,
    upstream_event: str,
    head_sha: str,
    created_at_utc: str,
    updated_at_utc: str,
    recorded_at_utc: str,
    readiness_output_dir: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = readiness.build_readiness(
        artifact_root=artifact_root,
        source_run_id=source_run_id,
        source_run_url=source_run_url,
        upstream_conclusion=upstream_conclusion,
        upstream_event=upstream_event,
        head_sha=head_sha,
        created_at_utc=created_at_utc,
        updated_at_utc=updated_at_utc,
    )
    issues = readiness.validate_readiness(payload)
    if issues:
        raise ValueError("invalid readiness payload: " + "; ".join(issues))

    output = Path(readiness_output_dir)
    output.mkdir(parents=True, exist_ok=True)
    readiness.atomic_write_json(payload, output / "live_session_readiness.json")
    readiness.atomic_write_text(
        readiness.readiness_markdown(payload),
        output / "live_session_readiness.md",
    )

    ranking_path = readiness.find_file(Path(artifact_root), "momentum_daily_ranking.csv")
    row_count, ranking_hash = ranking_date_fingerprint(
        ranking_path or "", optional_text(payload.get("report_date"))
    )
    failures, warnings = critical_gate_details(payload)
    forward_eligible = bool(payload.get("eligible_for_forward_evidence"))
    if forward_eligible and (row_count <= 0 or not ranking_hash):
        raise ValueError("eligible readiness is missing a report-date ranking fingerprint")

    record = {
        "eligibility_version": ELIGIBILITY_VERSION,
        "source_run_id": str(source_run_id),
        "source_run_url": source_run_url,
        "upstream_conclusion": upstream_conclusion,
        "upstream_event": upstream_event,
        "head_sha": head_sha,
        "created_at_utc": created_at_utc,
        "updated_at_utc": updated_at_utc,
        "recorded_at_utc": recorded_at_utc,
        "report_date": optional_text(payload.get("report_date")),
        "strategy_fingerprint": optional_text(payload.get("strategy_fingerprint")),
        "readiness_state": optional_text(payload.get("readiness_state")),
        "eligible_for_forward_evidence": forward_eligible,
        "eligible_for_priority_outcome_ingestion": bool(
            payload.get("eligible_for_priority_outcome_ingestion")
        ),
        "artifact_fingerprint": optional_text(payload.get("artifact_fingerprint")),
        "readiness_fingerprint": optional_text(payload.get("readiness_fingerprint")),
        "readiness_status_sha256": optional_text(payload.get("status_sha256")),
        "ranking_date_row_count": row_count,
        "ranking_date_sha256": ranking_hash,
        "critical_failure_count": len(failures),
        "review_warning_count": len(warnings),
        "readiness_details": " | ".join(failures + warnings),
        "research_only": True,
    }
    return record, payload


def valid_sha256(value: Any) -> bool:
    text = optional_text(value).lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def validate_history(history: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    work = normalize_history(history)
    if work["source_run_id"].eq("").any():
        issues.append("source_run_id is required")
    duplicates = int(work.duplicated(["source_run_id"], keep=False).sum())
    if duplicates:
        issues.append(f"duplicate source_run_id rows={duplicates}")
    if not work["eligibility_version"].eq(ELIGIBILITY_VERSION).all():
        issues.append("invalid eligibility_version")
    if not work["research_only"].all():
        issues.append("research_only must be true")

    for _, row in work.iterrows():
        run_id = row["source_run_id"] or "<missing>"
        if row["readiness_state"] not in {"PASS", "REVIEW_REQUIRED", "FAIL"}:
            issues.append(f"{run_id}: invalid readiness_state")
        for field in ("artifact_fingerprint", "readiness_fingerprint", "readiness_status_sha256"):
            if not valid_sha256(row[field]):
                issues.append(f"{run_id}: invalid {field}")
        if row["eligible_for_forward_evidence"]:
            if row["readiness_state"] not in {"PASS", "REVIEW_REQUIRED"}:
                issues.append(f"{run_id}: eligible run has invalid readiness_state")
            if not row["report_date"]:
                issues.append(f"{run_id}: eligible run is missing report_date")
            if not valid_sha256(row["strategy_fingerprint"]):
                issues.append(f"{run_id}: eligible run has invalid strategy_fingerprint")
            if int(row["ranking_date_row_count"]) <= 0:
                issues.append(f"{run_id}: eligible run has no ranking rows")
            if not valid_sha256(row["ranking_date_sha256"]):
                issues.append(f"{run_id}: eligible run has invalid ranking_date_sha256")
            if int(row["critical_failure_count"]) != 0:
                issues.append(f"{run_id}: eligible run has critical failures")
    return sorted(set(issues))


def build_status(history: pd.DataFrame) -> dict[str, Any]:
    work = normalize_history(history)
    eligible = work[work["eligible_for_forward_evidence"]].copy()
    eligible_dates = sorted(eligible["report_date"].loc[eligible["report_date"].ne("")].unique().tolist())
    substantive = {
        "eligibility_version": ELIGIBILITY_VERSION,
        "ledger_state": "ACCUMULATING" if len(work) else "EMPTY",
        "run_count": len(work),
        "eligible_forward_run_count": len(eligible),
        "eligible_forward_date_count": len(eligible_dates),
        "latest_eligible_report_date": eligible_dates[-1] if eligible_dates else "",
        "failed_readiness_run_count": int(work["readiness_state"].eq("FAIL").sum()),
        "review_required_run_count": int(work["readiness_state"].eq("REVIEW_REQUIRED").sum()),
        "duplicate_source_run_count": int(work.duplicated(["source_run_id"], keep=False).sum()),
        "automatic_score_change": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "automatic_priority_rule_change": False,
        "production_state_mutations": [],
        "research_only": True,
    }
    payload = {
        **substantive,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ledger_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def validate_status(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("eligibility_version") != ELIGIBILITY_VERSION:
        issues.append("invalid eligibility_version")
    if payload.get("ledger_state") not in {"EMPTY", "ACCUMULATING"}:
        issues.append("invalid ledger_state")
    for key in (
        "automatic_score_change",
        "automatic_weight_change",
        "automatic_strategy_change",
        "automatic_priority_rule_change",
    ):
        if payload.get(key) is not False:
            issues.append(f"{key} must be false")
    if payload.get("production_state_mutations") != []:
        issues.append("production_state_mutations must be empty")
    if payload.get("research_only") is not True:
        issues.append("research_only must be true")
    status_copy = dict(payload)
    supplied_status = status_copy.pop("status_sha256", "")
    if supplied_status != canonical_hash(status_copy):
        issues.append("status_sha256 mismatch")
    substantive = dict(status_copy)
    substantive.pop("generated_at_utc", None)
    supplied_fingerprint = substantive.pop("ledger_fingerprint", "")
    if supplied_fingerprint != canonical_hash(substantive):
        issues.append("ledger_fingerprint mismatch")
    return issues


def atomic_write_csv(frame: pd.DataFrame, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(target)


def atomic_write_json(payload: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def update_ledger(args: argparse.Namespace) -> dict[str, Any]:
    history = load_history(args.ledger)
    record, readiness_payload = build_record(
        artifact_root=args.artifact_root,
        source_run_id=args.source_run_id,
        source_run_url=args.source_run_url,
        upstream_conclusion=args.upstream_conclusion,
        upstream_event=args.upstream_event,
        head_sha=args.head_sha,
        created_at_utc=args.created_at_utc,
        updated_at_utc=args.updated_at_utc,
        recorded_at_utc=args.recorded_at_utc,
        readiness_output_dir=args.readiness_output_dir,
    )
    updated = append_record(history, record)
    issues = validate_history(updated)
    if issues:
        raise ValueError("invalid eligibility ledger: " + "; ".join(issues))
    status = build_status(updated)
    status_issues = validate_status(status)
    if status_issues:
        raise ValueError("invalid eligibility status: " + "; ".join(status_issues))
    atomic_write_csv(updated, args.ledger)
    atomic_write_json(status, args.status)
    return {
        "record": record,
        "readiness_state": readiness_payload["readiness_state"],
        "status": status,
    }


def validate_committed(ledger_path: str, status_path: str) -> dict[str, Any]:
    history = load_history(ledger_path)
    status_target = Path(status_path)
    if not status_target.is_file():
        raise FileNotFoundError(status_path)
    status = json.loads(status_target.read_text(encoding="utf-8"))
    issues = validate_history(history) + validate_status(status)
    expected = build_status(history)
    for field in (
        "run_count",
        "eligible_forward_run_count",
        "eligible_forward_date_count",
        "latest_eligible_report_date",
        "failed_readiness_run_count",
        "review_required_run_count",
        "duplicate_source_run_count",
    ):
        if status.get(field) != expected.get(field):
            issues.append(f"status {field} does not match ledger")
    return {"passed": not issues, "issues": sorted(set(issues)), "status": status}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain signed live-session eligibility")
    commands = parser.add_subparsers(dest="command", required=True)

    update = commands.add_parser("update")
    update.add_argument("--artifact-root", required=True)
    update.add_argument("--source-run-id", required=True)
    update.add_argument("--source-run-url", required=True)
    update.add_argument("--upstream-conclusion", required=True)
    update.add_argument("--upstream-event", required=True)
    update.add_argument("--head-sha", required=True)
    update.add_argument("--created-at-utc", required=True)
    update.add_argument("--updated-at-utc", required=True)
    update.add_argument("--recorded-at-utc", required=True)
    update.add_argument("--ledger", default=DEFAULT_LEDGER)
    update.add_argument("--status", default=DEFAULT_STATUS)
    update.add_argument("--readiness-output-dir", default="output/live-session-eligibility")

    validate = commands.add_parser("validate")
    validate.add_argument("--ledger", default=DEFAULT_LEDGER)
    validate.add_argument("--status", default=DEFAULT_STATUS)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    result = update_ledger(args) if args.command == "update" else validate_committed(args.ledger, args.status)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "validate" and not result["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
