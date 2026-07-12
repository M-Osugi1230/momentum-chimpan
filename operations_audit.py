"""Build an append-only operational audit from Daily Momentum Report artifacts.

This module never changes scores, rankings, paper decisions, or production state.
It converts one completed GitHub Actions run into a compact audit row and a
signed rolling status used for the ten-market-session production audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

AUDIT_VERSION = "2026-07-12-daily-production-audit-v1"
DEFAULT_HISTORY = "research/operations/daily_production_audit.csv"
DEFAULT_STATUS = "research/operations/daily_production_audit_status.json"
TARGET_MARKET_SESSIONS = 10

HISTORY_COLUMNS = [
    "audit_version",
    "workflow_run_id",
    "workflow_run_url",
    "upstream_conclusion",
    "upstream_event",
    "head_sha",
    "head_branch",
    "created_at_utc",
    "updated_at_utc",
    "duration_seconds",
    "intended_date_jst",
    "report_date",
    "app_version",
    "execution_mode",
    "workflow_status",
    "run_health",
    "release_status",
    "market_data_freshness",
    "latest_price_date",
    "current_day_price_ratio",
    "state_update_executed",
    "p0_alerts",
    "p1_alerts",
    "strategy_fingerprint",
    "fingerprint_present",
    "report_present",
    "heartbeat_present",
    "evidence_audit_present",
    "evidence_stamped_rows",
    "evidence_fingerprint_matches",
    "recovery_audit_present",
    "recovery_status",
    "recovery_complete",
    "maintenance_present",
    "maintenance_status",
    "maintenance_failures",
    "ranking_present",
    "ranking_rows",
    "ranking_date_rows",
    "ranking_duplicate_count",
    "ranking_fingerprint_matches",
    "market_temperature_present",
    "market_temperature_duplicate_count",
    "workbook_universe_count",
    "workbook_scan_count",
    "workbook_success_count",
    "workbook_failure_count",
    "retrieval_coverage",
    "notification_present",
    "artifact_file_count",
    "artifact_fingerprint",
    "full_state_update",
    "audit_status",
    "audit_failures",
]

BOOL_COLUMNS = {
    "state_update_executed",
    "fingerprint_present",
    "report_present",
    "heartbeat_present",
    "evidence_audit_present",
    "evidence_fingerprint_matches",
    "recovery_audit_present",
    "recovery_complete",
    "maintenance_present",
    "ranking_present",
    "ranking_fingerprint_matches",
    "market_temperature_present",
    "notification_present",
    "full_state_update",
}

NUMERIC_COLUMNS = {
    "duration_seconds",
    "current_day_price_ratio",
    "evidence_stamped_rows",
    "maintenance_failures",
    "ranking_rows",
    "ranking_date_rows",
    "ranking_duplicate_count",
    "market_temperature_duplicate_count",
    "workbook_universe_count",
    "workbook_scan_count",
    "workbook_success_count",
    "workbook_failure_count",
    "retrieval_coverage",
    "artifact_file_count",
}


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_fingerprint(root: Path) -> str:
    entries: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            entries.append({
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return canonical_hash(entries)


def find_file(root: Path, name: str) -> Path | None:
    matches = sorted(path for path in root.rglob(name) if path.is_file()) if root.exists() else []
    return matches[0] if matches else None


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_csv(path: Path | None, dtype: dict[str, Any] | None = None) -> pd.DataFrame:
    if path is None or not path.is_file() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=dtype)
    except Exception:
        return pd.DataFrame()


def parse_time(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def intended_date_jst(created_at: str) -> str:
    timestamp = parse_time(created_at)
    if timestamp is None:
        return ""
    return (timestamp.astimezone(timezone.utc) + pd.Timedelta(hours=9)).date().isoformat()


def duration_seconds(created_at: str, updated_at: str) -> float | None:
    start = parse_time(created_at)
    end = parse_time(updated_at)
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


def scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def first_value(mapping: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        value = mapping.get(name)
        if scalar(value) is not None:
            return scalar(value)
    return None


def read_workbook_summary(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        frame = pd.read_excel(path, sheet_name="Summary")
    except Exception:
        return {}
    if frame.empty:
        return {}
    return {str(key): scalar(value) for key, value in frame.iloc[0].to_dict().items()}


def to_float(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return None if number is None else int(number)


def duplicate_count(frame: pd.DataFrame, columns: list[str]) -> int | None:
    if frame.empty or not set(columns).issubset(frame.columns):
        return None
    return int(frame.duplicated(columns, keep=False).sum())


def notification_present(path: Path | None) -> bool:
    if path is None or not path.is_file() or path.stat().st_size == 0:
        return False
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return bool(text)


def build_record(
    artifact_root: str | Path,
    workflow_run_id: str,
    workflow_run_url: str,
    upstream_conclusion: str,
    upstream_event: str,
    head_sha: str,
    head_branch: str,
    created_at_utc: str,
    updated_at_utc: str,
) -> dict[str, Any]:
    root = Path(artifact_root)
    report_path = find_file(root, "daily_report.xlsx")
    heartbeat_path = find_file(root, "operations_heartbeat.json")
    evidence_path = find_file(root, "evidence_stamp_audit.json")
    recovery_path = find_file(root, "recovery_snapshot_audit.json")
    maintenance_path = find_file(root, "state_maintenance.json")
    fingerprint_path = find_file(root, "strategy_fingerprint.json")
    ranking_path = find_file(root, "momentum_daily_ranking.csv")
    temperature_path = find_file(root, "market_temperature.csv")
    notification_path = find_file(root, "ops_notification.txt")

    summary = read_workbook_summary(report_path)
    heartbeat = load_json(heartbeat_path)
    evidence = load_json(evidence_path)
    recovery = load_json(recovery_path)
    maintenance = load_json(maintenance_path)
    fingerprint_manifest = load_json(fingerprint_path)
    ranking = read_csv(ranking_path, dtype={"code": str})
    temperature = read_csv(temperature_path)

    report_date = str(
        heartbeat.get("report_date")
        or evidence.get("report_date")
        or recovery.get("snapshot_date")
        or summary.get("実行日")
        or ""
    ).strip()
    strategy_fingerprint = str(
        fingerprint_manifest.get("strategy_fingerprint")
        or evidence.get("strategy_fingerprint")
        or ""
    ).strip()
    state_update = heartbeat.get("state_update_executed") is True

    ranking_date_rows: int | None = None
    ranking_fingerprint_matches = False
    if not ranking.empty and report_date and "date" in ranking.columns:
        dated = ranking[ranking["date"].astype(str) == report_date]
        ranking_date_rows = len(dated)
        if (
            strategy_fingerprint
            and not dated.empty
            and "strategy_fingerprint" in dated.columns
        ):
            ranking_fingerprint_matches = bool(
                dated["strategy_fingerprint"]
                .fillna("")
                .astype(str)
                .str.strip()
                .eq(strategy_fingerprint)
                .all()
            )

    success_count = to_int(first_value(summary, ["取得成功数", "取得成功", "価格取得成功数"]))
    failure_count = to_int(first_value(summary, ["取得失敗数", "取得失敗", "価格取得失敗数"]))
    scan_count = to_int(first_value(summary, ["実スキャン対象銘柄数", "スキャン対象銘柄数", "スキャン銘柄数"]))
    universe_count = to_int(first_value(summary, ["通常株ユニバース数", "ユニバース数", "JPX通常株数"]))
    coverage: float | None = None
    denominator = scan_count
    if denominator is None and success_count is not None and failure_count is not None:
        denominator = success_count + failure_count
    if success_count is not None and denominator and denominator > 0:
        coverage = success_count / denominator
    if coverage is None:
        coverage = to_float(heartbeat.get("current_day_price_ratio"))

    evidence_matches = bool(
        strategy_fingerprint
        and str(evidence.get("strategy_fingerprint", "")).strip() == strategy_fingerprint
    )
    upstream_success = str(upstream_conclusion).lower() == "success"
    recovery_status = str(recovery.get("status", "")).strip()
    maintenance_status = str(maintenance.get("validation_status", "")).strip()

    failures: list[str] = []
    if not upstream_success:
        failures.append(f"upstream conclusion={upstream_conclusion}")
    if not report_path:
        failures.append("daily report missing")
    if not heartbeat:
        failures.append("heartbeat missing or unreadable")
    elif str(heartbeat.get("workflow_status", "")).upper() != "SUCCESS":
        failures.append("heartbeat workflow status is not SUCCESS")
    if not strategy_fingerprint:
        failures.append("strategy fingerprint missing")
    if state_update:
        if not evidence:
            failures.append("evidence audit missing or unreadable")
        elif not evidence_matches:
            failures.append("evidence fingerprint mismatch")
        if int(evidence.get("stamped_rows", 0) or 0) <= 0:
            failures.append("no ranking rows stamped")
        if not recovery:
            failures.append("recovery audit missing or unreadable")
        elif recovery_status != "SEALED" or recovery.get("complete") is not True:
            failures.append("recovery snapshot is not sealed and complete")
        if not maintenance:
            failures.append("maintenance audit missing or unreadable")
        elif maintenance_status != "PASS" or int(maintenance.get("validation_failures", 0) or 0) != 0:
            failures.append("state maintenance validation failed")
        if ranking.empty:
            failures.append("ranking history missing or unreadable")
        else:
            duplicates = duplicate_count(ranking, ["date", "code"])
            if duplicates not in (0, None):
                failures.append(f"ranking duplicate rows={duplicates}")
            if ranking_date_rows in (None, 0):
                failures.append("ranking has no rows for report date")
            if not ranking_fingerprint_matches:
                failures.append("ranking fingerprint mismatch")
        if not temperature.empty:
            temperature_duplicates = duplicate_count(temperature, ["date"])
            if temperature_duplicates not in (0, None):
                failures.append(f"market-temperature duplicate rows={temperature_duplicates}")

    audit_status = "PASS" if not failures else "FAIL"
    file_count = sum(1 for path in root.rglob("*") if path.is_file()) if root.exists() else 0

    record = {
        "audit_version": AUDIT_VERSION,
        "workflow_run_id": str(workflow_run_id),
        "workflow_run_url": workflow_run_url,
        "upstream_conclusion": upstream_conclusion,
        "upstream_event": upstream_event,
        "head_sha": head_sha,
        "head_branch": head_branch,
        "created_at_utc": created_at_utc,
        "updated_at_utc": updated_at_utc,
        "duration_seconds": duration_seconds(created_at_utc, updated_at_utc),
        "intended_date_jst": intended_date_jst(created_at_utc),
        "report_date": report_date,
        "app_version": heartbeat.get("app_version") or summary.get("アプリ版") or "",
        "execution_mode": heartbeat.get("execution_mode") or summary.get("実行モード") or "",
        "workflow_status": heartbeat.get("workflow_status") or "",
        "run_health": heartbeat.get("run_health") or summary.get("Run Health") or "",
        "release_status": heartbeat.get("release_status") or summary.get("リリース判定") or "",
        "market_data_freshness": heartbeat.get("market_data_freshness") or summary.get("市場データ鮮度") or "",
        "latest_price_date": heartbeat.get("latest_price_date") or summary.get("最新株価日") or "",
        "current_day_price_ratio": to_float(heartbeat.get("current_day_price_ratio")),
        "state_update_executed": state_update,
        "p0_alerts": heartbeat.get("p0_alerts"),
        "p1_alerts": heartbeat.get("p1_alerts"),
        "strategy_fingerprint": strategy_fingerprint,
        "fingerprint_present": bool(strategy_fingerprint),
        "report_present": report_path is not None,
        "heartbeat_present": bool(heartbeat),
        "evidence_audit_present": bool(evidence),
        "evidence_stamped_rows": int(evidence.get("stamped_rows", 0) or 0),
        "evidence_fingerprint_matches": evidence_matches,
        "recovery_audit_present": bool(recovery),
        "recovery_status": recovery_status,
        "recovery_complete": recovery.get("complete") is True,
        "maintenance_present": bool(maintenance),
        "maintenance_status": maintenance_status,
        "maintenance_failures": int(maintenance.get("validation_failures", 0) or 0),
        "ranking_present": not ranking.empty,
        "ranking_rows": len(ranking) if not ranking.empty else 0,
        "ranking_date_rows": ranking_date_rows,
        "ranking_duplicate_count": duplicate_count(ranking, ["date", "code"]),
        "ranking_fingerprint_matches": ranking_fingerprint_matches,
        "market_temperature_present": not temperature.empty,
        "market_temperature_duplicate_count": duplicate_count(temperature, ["date"]),
        "workbook_universe_count": universe_count,
        "workbook_scan_count": scan_count,
        "workbook_success_count": success_count,
        "workbook_failure_count": failure_count,
        "retrieval_coverage": coverage,
        "notification_present": notification_present(notification_path),
        "artifact_file_count": file_count,
        "artifact_fingerprint": artifact_fingerprint(root),
        "full_state_update": bool(state_update and upstream_success),
        "audit_status": audit_status,
        "audit_failures": " | ".join(failures),
    }
    return {column: record.get(column) for column in HISTORY_COLUMNS}


def empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=HISTORY_COLUMNS)


def load_history(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        return empty_history()
    try:
        frame = pd.read_csv(target, dtype={"workflow_run_id": str, "head_sha": str})
    except Exception:
        return empty_history()
    for column in HISTORY_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[HISTORY_COLUMNS]


def normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in BOOL_COLUMNS:
        work[column] = work[column].map(
            lambda value: value if isinstance(value, bool) else str(value).strip().lower() in {"true", "1", "yes"}
        )
    for column in NUMERIC_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    for column in set(HISTORY_COLUMNS) - BOOL_COLUMNS - NUMERIC_COLUMNS:
        work[column] = work[column].fillna("").astype(str)
    return work[HISTORY_COLUMNS]


def append_record(history: pd.DataFrame, record: dict[str, Any]) -> pd.DataFrame:
    work = pd.concat([history, pd.DataFrame([record], columns=HISTORY_COLUMNS)], ignore_index=True)
    work["workflow_run_id"] = work["workflow_run_id"].fillna("").astype(str)
    work = work.drop_duplicates(["workflow_run_id"], keep="last")
    work["_sort"] = pd.to_datetime(work["created_at_utc"], errors="coerce", utc=True)
    work = work.sort_values(["_sort", "workflow_run_id"], na_position="first").drop(columns="_sort")
    return normalize_history(work.reset_index(drop=True))


def latest_market_sessions(history: pd.DataFrame, limit: int = TARGET_MARKET_SESSIONS) -> pd.DataFrame:
    if history.empty:
        return history.copy()
    work = normalize_history(history)
    work = work[
        work["report_date"].ne("")
        & work["full_state_update"]
    ].copy()
    if work.empty:
        return work
    work["_sort"] = pd.to_datetime(work["updated_at_utc"], errors="coerce", utc=True)
    work = work.sort_values(["report_date", "_sort", "workflow_run_id"])
    work = work.drop_duplicates(["report_date"], keep="last")
    return work.sort_values("report_date").tail(limit).drop(columns="_sort")


def build_status(history: pd.DataFrame) -> dict[str, Any]:
    work = normalize_history(history) if not history.empty else empty_history()
    sessions = latest_market_sessions(work)
    scheduled = work[work["upstream_event"].eq("schedule")] if not work.empty else work
    successful_scheduled = int(scheduled["upstream_conclusion"].str.lower().eq("success").sum()) if not scheduled.empty else 0
    scheduled_count = len(scheduled)
    retrieval = pd.to_numeric(sessions.get("retrieval_coverage", pd.Series(dtype=float)), errors="coerce").dropna()
    duplicate_total = int(
        pd.to_numeric(sessions.get("ranking_duplicate_count", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
        + pd.to_numeric(sessions.get("market_temperature_duplicate_count", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    ) if not sessions.empty else 0
    failures = int((sessions.get("audit_status", pd.Series(dtype=str)) != "PASS").sum()) if not sessions.empty else 0
    session_count = len(sessions)

    if session_count < TARGET_MARKET_SESSIONS:
        audit_state = "ACCUMULATING"
    else:
        passes = bool(
            failures == 0
            and duplicate_total == 0
            and (retrieval.empty or float(retrieval.min()) >= 0.98)
        )
        audit_state = "PASS" if passes else "REVIEW_REQUIRED"

    session_rows = []
    for _, row in sessions.iterrows():
        session_rows.append({
            "report_date": row["report_date"],
            "workflow_run_id": row["workflow_run_id"],
            "audit_status": row["audit_status"],
            "retrieval_coverage": scalar(row["retrieval_coverage"]),
            "run_health": row["run_health"],
            "market_data_freshness": row["market_data_freshness"],
            "ranking_duplicate_count": scalar(row["ranking_duplicate_count"]),
            "market_temperature_duplicate_count": scalar(row["market_temperature_duplicate_count"]),
            "strategy_fingerprint": row["strategy_fingerprint"],
        })

    substantive = {
        "audit_version": AUDIT_VERSION,
        "target_market_sessions": TARGET_MARKET_SESSIONS,
        "audit_state": audit_state,
        "market_session_count": session_count,
        "remaining_market_sessions": max(TARGET_MARKET_SESSIONS - session_count, 0),
        "scheduled_run_count": scheduled_count,
        "successful_scheduled_run_count": successful_scheduled,
        "scheduled_success_rate": successful_scheduled / scheduled_count if scheduled_count else None,
        "audited_run_count": len(work),
        "audited_run_failure_count": int((work["audit_status"] != "PASS").sum()) if not work.empty else 0,
        "market_session_failure_count": failures,
        "minimum_retrieval_coverage": float(retrieval.min()) if not retrieval.empty else None,
        "average_retrieval_coverage": float(retrieval.mean()) if not retrieval.empty else None,
        "duplicate_row_count": duplicate_total,
        "sessions": session_rows,
        "production_strategy_mutations": [],
        "automatic_strategy_change": False,
        "automatic_weight_change": False,
        "research_only": True,
    }
    payload = {
        **substantive,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audit_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def validate_status(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("audit_version") != AUDIT_VERSION:
        errors.append("invalid audit_version")
    if payload.get("target_market_sessions") != TARGET_MARKET_SESSIONS:
        errors.append("invalid target_market_sessions")
    if payload.get("audit_state") not in {"ACCUMULATING", "PASS", "REVIEW_REQUIRED"}:
        errors.append("invalid audit_state")
    if payload.get("production_strategy_mutations") != []:
        errors.append("production_strategy_mutations must be empty")
    for key in ("automatic_strategy_change", "automatic_weight_change"):
        if payload.get(key) is not False:
            errors.append(f"{key} must be false")
    if payload.get("research_only") is not True:
        errors.append("research_only must be true")
    sessions = payload.get("sessions")
    if not isinstance(sessions, list) or len(sessions) > TARGET_MARKET_SESSIONS:
        errors.append("invalid sessions")
    status_copy = dict(payload)
    supplied_status_hash = status_copy.pop("status_sha256", "")
    if supplied_status_hash != canonical_hash(status_copy):
        errors.append("status_sha256 mismatch")
    substantive = dict(status_copy)
    substantive.pop("generated_at_utc", None)
    supplied_fingerprint = substantive.pop("audit_fingerprint", "")
    if supplied_fingerprint != canonical_hash(substantive):
        errors.append("audit_fingerprint mismatch")
    return errors


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


def update_audit(args: argparse.Namespace) -> dict[str, Any]:
    history = load_history(args.history)
    record = build_record(
        args.artifact_root,
        args.workflow_run_id,
        args.workflow_run_url,
        args.upstream_conclusion,
        args.upstream_event,
        args.head_sha,
        args.head_branch,
        args.created_at_utc,
        args.updated_at_utc,
    )
    updated = append_record(history, record)
    status = build_status(updated)
    errors = validate_status(status)
    if errors:
        raise ValueError("; ".join(errors))
    atomic_write_csv(updated, args.history)
    atomic_write_json(status, args.status)
    return {"record": record, "status": status}


def initialize(history_path: str, status_path: str) -> dict[str, Any]:
    history = empty_history()
    status = build_status(history)
    atomic_write_csv(history, history_path)
    atomic_write_json(status, status_path)
    return status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or validate daily production audit state")
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize_parser = subparsers.add_parser("initialize")
    initialize_parser.add_argument("--history", default=DEFAULT_HISTORY)
    initialize_parser.add_argument("--status", default=DEFAULT_STATUS)

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("--artifact-root", required=True)
    update_parser.add_argument("--workflow-run-id", required=True)
    update_parser.add_argument("--workflow-run-url", default="")
    update_parser.add_argument("--upstream-conclusion", required=True)
    update_parser.add_argument("--upstream-event", required=True)
    update_parser.add_argument("--head-sha", default="")
    update_parser.add_argument("--head-branch", default="")
    update_parser.add_argument("--created-at-utc", default="")
    update_parser.add_argument("--updated-at-utc", default="")
    update_parser.add_argument("--history", default=DEFAULT_HISTORY)
    update_parser.add_argument("--status", default=DEFAULT_STATUS)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--history", default=DEFAULT_HISTORY)
    validate_parser.add_argument("--status", default=DEFAULT_STATUS)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "initialize":
        payload = initialize(args.history, args.status)
    elif args.command == "update":
        payload = update_audit(args)
    else:
        history = load_history(args.history)
        payload = json.loads(Path(args.status).read_text(encoding="utf-8"))
        errors = validate_status(payload)
        if errors:
            print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        rebuilt = build_status(history)
        comparable = dict(rebuilt)
        comparable.pop("generated_at_utc", None)
        comparable.pop("status_sha256", None)
        stored = dict(payload)
        stored.pop("generated_at_utc", None)
        stored.pop("status_sha256", None)
        if comparable != stored:
            print(json.dumps({"valid": False, "errors": ["history/status semantic mismatch"]}, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
