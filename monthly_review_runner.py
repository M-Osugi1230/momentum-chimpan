"""Monthly review entrypoint with SMTP acceptance audit metrics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import email_delivery_audit
import monthly_review

EMAIL_HISTORY_PATH = "research/operations/email_delivery_audit.csv"
EMAIL_STATUS_PATH = "research/operations/email_delivery_audit_status.json"

ORIGINAL_OPERATIONS_SECTION = monthly_review.operations_section
ORIGINAL_FLATTEN_SUMMARY = monthly_review.flatten_summary
ORIGINAL_MARKDOWN_REPORT = monthly_review.markdown_report


def install_email_overlay() -> None:
    monthly_review.CANONICAL_PATHS.setdefault("email_delivery_audit", EMAIL_HISTORY_PATH)
    monthly_review.CANONICAL_PATHS.setdefault("email_delivery_status", EMAIL_STATUS_PATH)

    def operations_section(audit: Any, review_month: str) -> dict[str, Any]:
        result = ORIGINAL_OPERATIONS_SECTION(audit, review_month)
        result.update(
            email_delivery_audit.monthly_metrics(
                email_delivery_audit.load_history(EMAIL_HISTORY_PATH),
                review_month,
            )
        )
        return result

    def flatten_summary(payload: dict[str, Any]) -> dict[str, Any]:
        result = ORIGINAL_FLATTEN_SUMMARY(payload)
        operations = payload["sections"]["operations"]
        result.update({
            "email_receipt_run_count": operations.get("email_receipt_run_count", 0),
            "email_receipt_valid_rate": operations.get("email_receipt_valid_rate"),
            "scheduled_email_receipt_count": operations.get("scheduled_email_receipt_count", 0),
            "scheduled_email_attempt_count": operations.get("scheduled_email_attempt_count", 0),
            "scheduled_smtp_accepted_count": operations.get("scheduled_smtp_accepted_count", 0),
            "scheduled_smtp_acceptance_rate": operations.get("scheduled_smtp_acceptance_rate"),
            "scheduled_email_skipped_count": operations.get("scheduled_email_skipped_count", 0),
            "scheduled_email_failed_count": operations.get("scheduled_email_failed_count", 0),
            "inbox_delivery_observable": operations.get("inbox_delivery_observable", False),
        })
        return result

    def markdown_report(payload: dict[str, Any]) -> str:
        text = ORIGINAL_MARKDOWN_REPORT(payload)
        operations = payload["sections"]["operations"]
        old = f"- Email delivery: **{operations.get('email_delivery_status', '')}**"
        new = "\n".join([
            f"- SMTP receipt validity: **{monthly_review.fmt_pct(operations.get('email_receipt_valid_rate'))}**",
            f"- Scheduled SMTP acceptance: **{operations.get('scheduled_smtp_accepted_count', 0)}/{operations.get('scheduled_email_receipt_count', 0)} ({monthly_review.fmt_pct(operations.get('scheduled_smtp_acceptance_rate'))})**",
            f"- Scheduled email skipped: **{operations.get('scheduled_email_skipped_count', 0)}**",
            f"- Scheduled email failed: **{operations.get('scheduled_email_failed_count', 0)}**",
            f"- Inbox delivery: **{operations.get('inbox_delivery_status', 'NOT_OBSERVED_BY_SMTP_ACCEPTANCE_RECEIPT')}**",
        ])
        return text.replace(old, new)

    monthly_review.operations_section = operations_section
    monthly_review.flatten_summary = flatten_summary
    monthly_review.markdown_report = markdown_report


def rebuild_signature(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("status_sha256", None)
    result.pop("review_fingerprint", None)
    substantive = dict(result)
    substantive.pop("generated_at_utc", None)
    result["review_fingerprint"] = monthly_review.canonical_hash(substantive)
    result["status_sha256"] = monthly_review.canonical_hash(result)
    return result


def build_review(
    review_month: str,
    *,
    repository: str = "",
    commit_sha: str = "",
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    install_email_overlay()
    payload = monthly_review.build_review(
        review_month,
        repository=repository,
        commit_sha=commit_sha,
        generated_at_utc=generated_at_utc,
    )
    operations = payload["sections"]["operations"]
    gaps = [
        gap
        for gap in payload.get("known_measurement_gaps", [])
        if "email delivery is not captured separately" not in gap
    ]
    gaps.append(
        "SMTP server acceptance is tracked, but final inbox delivery, spam placement, opening, and reading are not observable"
    )
    payload["known_measurement_gaps"] = list(dict.fromkeys(gaps))
    payload["email_delivery_measurement"] = {
        "source": EMAIL_HISTORY_PATH,
        "claim": "SMTP_ACCEPTANCE_ONLY",
        "smtp_acceptance_observable": operations.get("email_delivery_observable", False),
        "inbox_delivery_observable": False,
    }
    return rebuild_signature(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build monthly review with SMTP acceptance metrics")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--month", default="")
    build.add_argument("--output-dir", default=monthly_review.DEFAULT_OUTPUT_DIR)
    build.add_argument("--repository", default="")
    build.add_argument("--commit-sha", default="")
    build.add_argument("--generated-at-utc", default="")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--json", required=True)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    install_email_overlay()
    if args.command == "build":
        review_month = args.month or monthly_review.default_review_month()
        payload = build_review(
            review_month,
            repository=args.repository,
            commit_sha=args.commit_sha,
            generated_at_utc=args.generated_at_utc or None,
        )
        issues = monthly_review.validate_review(payload)
        if issues:
            print(json.dumps({"valid": False, "issues": issues}, ensure_ascii=False, indent=2))
            return 1
        outputs = monthly_review.write_outputs(payload, args.output_dir)
        print(json.dumps({"review": payload, "outputs": outputs}, ensure_ascii=False, indent=2))
        return 0
    payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
    issues = monthly_review.validate_review(payload)
    measurement = payload.get("email_delivery_measurement", {})
    if measurement.get("claim") != "SMTP_ACCEPTANCE_ONLY":
        issues.append("email delivery claim must be SMTP_ACCEPTANCE_ONLY")
    if measurement.get("inbox_delivery_observable") is not False:
        issues.append("inbox delivery must remain unobservable")
    print(json.dumps({"valid": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
