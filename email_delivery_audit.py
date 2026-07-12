"""Append-only SMTP acceptance audit derived from daily workflow artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import email_delivery

AUDIT_VERSION = "2026-07-13-email-delivery-audit-v1"
DEFAULT_HISTORY = "research/operations/email_delivery_audit.csv"
DEFAULT_STATUS = "research/operations/email_delivery_audit_status.json"

HISTORY_COLUMNS = [
    "audit_version",
    "workflow_run_id",
    "workflow_run_url",
    "upstream_conclusion",
    "upstream_event",
    "head_sha",
    "created_at_utc",
    "updated_at_utc",
    "report_date",
    "receipt_present",
    "receipt_valid",
    "receipt_status",
    "attempted",
    "smtp_accepted",
    "inbox_delivery_claimed",
    "secret_configuration_complete",
    "recipient_count",
    "error_class",
    "receipt_fingerprint",
    "receipt_status_sha256",
    "artifact_fingerprint",
    "audit_status",
    "audit_failures",
]

BOOL_COLUMNS = {
    "receipt_present",
    "receipt_valid",
    "attempted",
    "smtp_accepted",
    "inbox_delivery_claimed",
    "secret_configuration_complete",
}
NUMERIC_COLUMNS = {"recipient_count"}


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_fingerprint(root: Path) -> str:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
    ] if root.exists() else []
    return canonical_hash(entries)


def find_receipt(root: Path) -> Path | None:
    matches = sorted(root.rglob("email_delivery_receipt.json")) if root.exists() else []
    return matches[0] if matches else None


def load_receipt(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in HISTORY_COLUMNS:
        if column not in work.columns:
            work[column] = None
    for column in BOOL_COLUMNS:
        work[column] = work[column].map(to_bool)
    for column in NUMERIC_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    for column in set(HISTORY_COLUMNS) - BOOL_COLUMNS - NUMERIC_COLUMNS:
        work[column] = work[column].fillna("").astype(str)
    return work[HISTORY_COLUMNS]


def empty_history() -> pd.DataFrame:
    return normalize_history(pd.DataFrame(columns=HISTORY_COLUMNS))


def load_history(path: str | Path = DEFAULT_HISTORY) -> pd.DataFrame:
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        return empty_history()
    try:
        frame = pd.read_csv(target, dtype={"workflow_run_id": str, "head_sha": str})
    except Exception:
        return empty_history()
    return normalize_history(frame)


def build_record(
    artifact_root: str | Path,
    *,
    workflow_run_id: str,
    workflow_run_url: str,
    upstream_conclusion: str,
    upstream_event: str,
    head_sha: str,
    created_at_utc: str,
    updated_at_utc: str,
) -> dict[str, Any]:
    root = Path(artifact_root)
    receipt_path = find_receipt(root)
    receipt = load_receipt(receipt_path)
    receipt_issues = email_delivery.validate_receipt(receipt) if receipt else ["receipt missing or unreadable"]
    failures = list(receipt_issues)
    if receipt.get("inbox_delivery_claimed") is True:
        failures.append("receipt incorrectly claims inbox delivery")
    record = {
        "audit_version": AUDIT_VERSION,
        "workflow_run_id": str(workflow_run_id),
        "workflow_run_url": workflow_run_url,
        "upstream_conclusion": upstream_conclusion,
        "upstream_event": upstream_event,
        "head_sha": head_sha,
        "created_at_utc": created_at_utc,
        "updated_at_utc": updated_at_utc,
        "report_date": str(receipt.get("report_date", "")),
        "receipt_present": receipt_path is not None,
        "receipt_valid": bool(receipt and not receipt_issues),
        "receipt_status": str(receipt.get("status", "")),
        "attempted": receipt.get("attempted") is True,
        "smtp_accepted": receipt.get("smtp_accepted") is True,
        "inbox_delivery_claimed": receipt.get("inbox_delivery_claimed") is True,
        "secret_configuration_complete": receipt.get("secret_configuration_complete") is True,
        "recipient_count": int(receipt.get("recipient_count", 0) or 0),
        "error_class": str(receipt.get("error_class", "")),
        "receipt_fingerprint": str(receipt.get("receipt_fingerprint", "")),
        "receipt_status_sha256": str(receipt.get("status_sha256", "")),
        "artifact_fingerprint": artifact_fingerprint(root),
        "audit_status": "PASS" if not failures else "FAIL",
        "audit_failures": " | ".join(dict.fromkeys(failures)),
    }
    return {column: record.get(column) for column in HISTORY_COLUMNS}


def append_record(history: pd.DataFrame, record: dict[str, Any]) -> pd.DataFrame:
    combined = pd.concat([history, pd.DataFrame([record])], ignore_index=True)
    combined["workflow_run_id"] = combined["workflow_run_id"].fillna("").astype(str)
    combined = combined.drop_duplicates(["workflow_run_id"], keep="last")
    combined["_sort"] = pd.to_datetime(combined["updated_at_utc"], errors="coerce", utc=True)
    combined = combined.sort_values(["_sort", "workflow_run_id"], na_position="first").drop(columns="_sort")
    return normalize_history(combined.reset_index(drop=True))


def monthly_metrics(history: pd.DataFrame, review_month: str) -> dict[str, Any]:
    if history.empty:
        work = history.copy()
    else:
        values = pd.to_datetime(history["report_date"], errors="coerce")
        start = pd.Timestamp(f"{review_month}-01")
        end = start + pd.offsets.MonthBegin(1)
        work = history[values.ge(start) & values.lt(end)].copy()
    scheduled = work[work["upstream_event"].eq("schedule")] if not work.empty else work
    valid = int(work["receipt_valid"].sum()) if not work.empty else 0
    scheduled_valid = scheduled[scheduled["receipt_valid"]] if not scheduled.empty else scheduled
    accepted = int(scheduled_valid["smtp_accepted"].sum()) if not scheduled_valid.empty else 0
    attempted = int(scheduled_valid["attempted"].sum()) if not scheduled_valid.empty else 0
    skipped = int(scheduled_valid["receipt_status"].eq("SKIPPED_SECRETS_MISSING").sum()) if not scheduled_valid.empty else 0
    failed = int(scheduled_valid["receipt_status"].eq("FAILED").sum()) if not scheduled_valid.empty else 0
    return {
        "email_receipt_run_count": int(len(work)),
        "email_receipt_valid_count": valid,
        "email_receipt_valid_rate": (valid / len(work)) if len(work) else None,
        "scheduled_email_receipt_count": int(len(scheduled_valid)),
        "scheduled_email_attempt_count": attempted,
        "scheduled_smtp_accepted_count": accepted,
        "scheduled_smtp_acceptance_rate": (accepted / len(scheduled_valid)) if len(scheduled_valid) else None,
        "scheduled_email_skipped_count": skipped,
        "scheduled_email_failed_count": failed,
        "email_delivery_observable": bool(len(scheduled_valid)),
        "email_delivery_status": "SMTP_ACCEPTANCE_TRACKED" if len(scheduled_valid) else "NO_VALID_SMTP_RECEIPTS",
        "inbox_delivery_observable": False,
        "inbox_delivery_status": "NOT_OBSERVED_BY_SMTP_ACCEPTANCE_RECEIPT",
    }


def build_status(history: pd.DataFrame) -> dict[str, Any]:
    scheduled = history[history["upstream_event"].eq("schedule")] if not history.empty else history
    valid_scheduled = scheduled[scheduled["receipt_valid"]] if not scheduled.empty else scheduled
    accepted = int(valid_scheduled["smtp_accepted"].sum()) if not valid_scheduled.empty else 0
    substantive = {
        "audit_version": AUDIT_VERSION,
        "audited_run_count": int(len(history)),
        "valid_receipt_count": int(history["receipt_valid"].sum()) if not history.empty else 0,
        "scheduled_receipt_count": int(len(valid_scheduled)),
        "scheduled_smtp_accepted_count": accepted,
        "scheduled_smtp_acceptance_rate": (accepted / len(valid_scheduled)) if len(valid_scheduled) else None,
        "scheduled_skipped_count": int(valid_scheduled["receipt_status"].eq("SKIPPED_SECRETS_MISSING").sum()) if not valid_scheduled.empty else 0,
        "scheduled_failed_count": int(valid_scheduled["receipt_status"].eq("FAILED").sum()) if not valid_scheduled.empty else 0,
        "latest_workflow_run_id": str(history.iloc[-1]["workflow_run_id"]) if not history.empty else "",
        "latest_receipt_status": str(history.iloc[-1]["receipt_status"]) if not history.empty else "",
        "delivery_claim": "SMTP_ACCEPTANCE_ONLY",
        "inbox_delivery_claimed": False,
        "automatic_retry": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
    }
    payload = {
        **substantive,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audit_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def validate_status(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("audit_version") != AUDIT_VERSION:
        issues.append("invalid audit_version")
    if payload.get("delivery_claim") != "SMTP_ACCEPTANCE_ONLY":
        issues.append("delivery_claim must be SMTP_ACCEPTANCE_ONLY")
    if payload.get("inbox_delivery_claimed") is not False:
        issues.append("inbox delivery must not be claimed")
    if payload.get("automatic_retry") is not False:
        issues.append("automatic_retry must be false")
    if payload.get("automatic_strategy_change") is not False:
        issues.append("automatic_strategy_change must be false")
    if payload.get("production_state_mutations") != []:
        issues.append("production_state_mutations must be empty")
    status_copy = dict(payload)
    supplied_status_hash = status_copy.pop("status_sha256", "")
    if supplied_status_hash != canonical_hash(status_copy):
        issues.append("status_sha256 mismatch")
    substantive = dict(status_copy)
    substantive.pop("generated_at_utc", None)
    supplied_fingerprint = substantive.pop("audit_fingerprint", "")
    if supplied_fingerprint != canonical_hash(substantive):
        issues.append("audit_fingerprint mismatch")
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


def update(args: argparse.Namespace) -> dict[str, Any]:
    history = load_history(args.history)
    record = build_record(
        args.artifact_root,
        workflow_run_id=args.workflow_run_id,
        workflow_run_url=args.workflow_run_url,
        upstream_conclusion=args.upstream_conclusion,
        upstream_event=args.upstream_event,
        head_sha=args.head_sha,
        created_at_utc=args.created_at_utc,
        updated_at_utc=args.updated_at_utc,
    )
    history = append_record(history, record)
    status = build_status(history)
    issues = validate_status(status)
    if issues:
        raise ValueError("; ".join(issues))
    atomic_write_csv(history, args.history)
    atomic_write_json(status, args.status)
    return {"record": record, "status": status}


def initialize(history_path: str, status_path: str) -> dict[str, Any]:
    history = empty_history()
    status = build_status(history)
    atomic_write_csv(history, history_path)
    atomic_write_json(status, status_path)
    return status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit daily SMTP acceptance receipts")
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
        result = initialize(args.history, args.status)
    elif args.command == "update":
        result = update(args)
    else:
        history = load_history(args.history)
        payload = json.loads(Path(args.status).read_text(encoding="utf-8"))
        issues = validate_status(payload)
        rebuilt = build_status(history)
        for key in ("generated_at_utc", "status_sha256"):
            payload.pop(key, None)
            rebuilt.pop(key, None)
        if payload != rebuilt:
            issues.append("history/status semantic mismatch")
        if issues:
            print(json.dumps({"valid": False, "issues": issues}, ensure_ascii=False, indent=2))
            return 1
        result = {"valid": True, "row_count": len(history)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
