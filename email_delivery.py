"""Privacy-preserving SMTP acceptance receipts for the daily report.

A receipt proves only what the application observed: skipped because credentials
were absent, accepted by the configured SMTP server, or failed with an exception.
It does not claim inbox delivery, opening, or reading. Email addresses, passwords,
message bodies, and exception text are never stored.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

RECEIPT_VERSION = "2026-07-13-smtp-acceptance-receipt-v1"
DEFAULT_RECEIPT_PATH = "output/email_delivery_receipt.json"
ALLOWED_STATUSES = {
    "SMTP_ACCEPTED",
    "SKIPPED_SECRETS_MISSING",
    "FAILED",
}


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def identity_hash(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def recipients(value: str) -> list[str]:
    normalized = str(value or "").replace(";", ",")
    return sorted({item.strip().lower() for item in normalized.split(",") if item.strip()})


def safe_report_date(summary: Any) -> str:
    if isinstance(summary, dict):
        return str(summary.get("実行日") or summary.get("report_date") or "").strip()
    return ""


def subject_for(summary: Any) -> str:
    return f"【モメンタムチンパン】{safe_report_date(summary)} 引け後レポート"


def build_receipt(
    *,
    status: str,
    summary: Any,
    sender: str,
    recipient_text: str,
    started_at_utc: str,
    completed_at_utc: str,
    error_class: str = "",
) -> dict[str, Any]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid email receipt status: {status}")
    recipient_list = recipients(recipient_text)
    substantive = {
        "receipt_version": RECEIPT_VERSION,
        "status": status,
        "report_date": safe_report_date(summary),
        "attempted": status in {"SMTP_ACCEPTED", "FAILED"},
        "smtp_accepted": status == "SMTP_ACCEPTED",
        "inbox_delivery_claimed": False,
        "secret_configuration_complete": bool(sender and recipient_list and os.getenv("EMAIL_APP_PASSWORD")),
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "sender_identity_sha256": identity_hash(sender),
        "recipient_set_sha256": canonical_hash(recipient_list) if recipient_list else "",
        "recipient_count": len(recipient_list),
        "subject_sha256": identity_hash(subject_for(summary)),
        "error_class": str(error_class or "")[:120],
        "error_message_stored": False,
        "run_id": str(os.getenv("GITHUB_RUN_ID", "")),
        "run_url": str(os.getenv("RUN_URL", "")),
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "automatic_retry": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
    }
    payload = {
        **substantive,
        "receipt_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def validate_receipt(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("receipt_version") != RECEIPT_VERSION:
        issues.append("invalid receipt_version")
    if payload.get("status") not in ALLOWED_STATUSES:
        issues.append("invalid status")
    status = payload.get("status")
    expected_attempted = status in {"SMTP_ACCEPTED", "FAILED"}
    if payload.get("attempted") is not expected_attempted:
        issues.append("attempted flag mismatch")
    if payload.get("smtp_accepted") is not (status == "SMTP_ACCEPTED"):
        issues.append("smtp_accepted flag mismatch")
    if payload.get("inbox_delivery_claimed") is not False:
        issues.append("inbox delivery must not be claimed")
    if payload.get("error_message_stored") is not False:
        issues.append("raw error messages must not be stored")
    if payload.get("automatic_retry") is not False:
        issues.append("automatic_retry must be false")
    if payload.get("automatic_strategy_change") is not False:
        issues.append("automatic_strategy_change must be false")
    if payload.get("production_state_mutations") != []:
        issues.append("production_state_mutations must be empty")
    if status == "SMTP_ACCEPTED":
        if payload.get("secret_configuration_complete") is not True:
            issues.append("SMTP_ACCEPTED requires complete secret configuration")
        if int(payload.get("recipient_count", 0) or 0) <= 0:
            issues.append("SMTP_ACCEPTED requires at least one recipient")
        if not payload.get("sender_identity_sha256"):
            issues.append("SMTP_ACCEPTED requires sender identity hash")
    if status == "FAILED" and not payload.get("error_class"):
        issues.append("FAILED requires error_class")
    if status != "FAILED" and payload.get("error_class"):
        issues.append("error_class is allowed only for FAILED")
    status_copy = dict(payload)
    supplied_status_hash = status_copy.pop("status_sha256", "")
    if supplied_status_hash != canonical_hash(status_copy):
        issues.append("status_sha256 mismatch")
    substantive = dict(status_copy)
    supplied_fingerprint = substantive.pop("receipt_fingerprint", "")
    if supplied_fingerprint != canonical_hash(substantive):
        issues.append("receipt_fingerprint mismatch")
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for key in ("email_app_password", "password", "message_body"):
        if key in serialized:
            issues.append(f"receipt contains prohibited field or value: {key}")
    return issues


def atomic_write_receipt(payload: dict[str, Any], path: str | Path = DEFAULT_RECEIPT_PATH) -> None:
    issues = validate_receipt(payload)
    if issues:
        raise ValueError("; ".join(issues))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def send_with_receipt(
    original_send: Callable[..., Any],
    *args: Any,
    receipt_path: str | Path = DEFAULT_RECEIPT_PATH,
    **kwargs: Any,
) -> Any:
    summary = args[0] if args else kwargs.get("summary", {})
    sender = str(os.getenv("EMAIL_FROM", ""))
    recipient_text = str(os.getenv("EMAIL_TO", ""))
    password = str(os.getenv("EMAIL_APP_PASSWORD", ""))
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not sender or not recipient_text or not password:
        result = original_send(*args, **kwargs)
        completed = datetime.now(timezone.utc).isoformat(timespec="seconds")
        atomic_write_receipt(
            build_receipt(
                status="SKIPPED_SECRETS_MISSING",
                summary=summary,
                sender=sender,
                recipient_text=recipient_text,
                started_at_utc=started,
                completed_at_utc=completed,
            ),
            receipt_path,
        )
        return result
    try:
        result = original_send(*args, **kwargs)
    except Exception as exc:
        completed = datetime.now(timezone.utc).isoformat(timespec="seconds")
        atomic_write_receipt(
            build_receipt(
                status="FAILED",
                summary=summary,
                sender=sender,
                recipient_text=recipient_text,
                started_at_utc=started,
                completed_at_utc=completed,
                error_class=type(exc).__name__,
            ),
            receipt_path,
        )
        raise
    completed = datetime.now(timezone.utc).isoformat(timespec="seconds")
    atomic_write_receipt(
        build_receipt(
            status="SMTP_ACCEPTED",
            summary=summary,
            sender=sender,
            recipient_text=recipient_text,
            started_at_utc=started,
            completed_at_utc=completed,
        ),
        receipt_path,
    )
    return result
