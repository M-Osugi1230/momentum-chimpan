from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import email_delivery
import email_delivery_audit
import monthly_review
import monthly_review_runner


class Environment:
    def __init__(self, values: dict[str, str | None]):
        self.values = values
        self.previous: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.previous[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, exc_type, exc, traceback):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


summary = {"実行日": "2026-07-13"}
address_from = "private-sender@example.com"
address_to = "first@example.com, second@example.com"
password = "very-secret-password"

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    receipt_path = root / "accepted.json"
    calls: list[str] = []

    def successful_send(*args, **kwargs):
        calls.append("sent")
        return "ok"

    with Environment({
        "EMAIL_FROM": address_from,
        "EMAIL_TO": address_to,
        "EMAIL_APP_PASSWORD": password,
        "GITHUB_RUN_ID": "1001",
        "RUN_URL": "https://example.test/runs/1001",
    }):
        result = email_delivery.send_with_receipt(
            successful_send,
            summary,
            receipt_path=receipt_path,
        )
    assert result == "ok"
    assert calls == ["sent"]
    accepted = load(receipt_path)
    assert email_delivery.validate_receipt(accepted) == []
    assert accepted["status"] == "SMTP_ACCEPTED"
    assert accepted["attempted"] is True
    assert accepted["smtp_accepted"] is True
    assert accepted["inbox_delivery_claimed"] is False
    assert accepted["recipient_count"] == 2
    assert accepted["secret_configuration_complete"] is True
    assert accepted["run_id"] == "1001"
    serialized = json.dumps(accepted, ensure_ascii=False)
    assert address_from not in serialized
    assert "first@example.com" not in serialized
    assert "second@example.com" not in serialized
    assert password not in serialized

    skipped_path = root / "skipped.json"
    calls.clear()
    with Environment({
        "EMAIL_FROM": None,
        "EMAIL_TO": None,
        "EMAIL_APP_PASSWORD": None,
    }):
        email_delivery.send_with_receipt(
            successful_send,
            summary,
            receipt_path=skipped_path,
        )
    skipped = load(skipped_path)
    assert calls == ["sent"]
    assert email_delivery.validate_receipt(skipped) == []
    assert skipped["status"] == "SKIPPED_SECRETS_MISSING"
    assert skipped["attempted"] is False
    assert skipped["smtp_accepted"] is False
    assert skipped["recipient_count"] == 0

    failure_path = root / "failed.json"

    def failing_send(*args, **kwargs):
        raise RuntimeError(f"failed for {address_from} with {password}")

    with Environment({
        "EMAIL_FROM": address_from,
        "EMAIL_TO": address_to,
        "EMAIL_APP_PASSWORD": password,
    }):
        try:
            email_delivery.send_with_receipt(
                failing_send,
                summary,
                receipt_path=failure_path,
            )
            raise AssertionError("failure must be re-raised")
        except RuntimeError:
            pass
    failed = load(failure_path)
    assert email_delivery.validate_receipt(failed) == []
    assert failed["status"] == "FAILED"
    assert failed["attempted"] is True
    assert failed["smtp_accepted"] is False
    assert failed["error_class"] == "RuntimeError"
    failed_serialized = json.dumps(failed, ensure_ascii=False)
    assert address_from not in failed_serialized
    assert password not in failed_serialized
    assert "failed for" not in failed_serialized

    tampered = copy.deepcopy(accepted)
    tampered["smtp_accepted"] = False
    issues = email_delivery.validate_receipt(tampered)
    assert "smtp_accepted flag mismatch" in issues
    assert "status_sha256 mismatch" in issues

    artifact = root / "artifact" / "output"
    artifact.mkdir(parents=True)
    (artifact / "email_delivery_receipt.json").write_text(
        json.dumps(accepted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    record = email_delivery_audit.build_record(
        root / "artifact",
        workflow_run_id="1001",
        workflow_run_url="https://example.test/runs/1001",
        upstream_conclusion="success",
        upstream_event="schedule",
        head_sha="a" * 40,
        created_at_utc="2026-07-13T07:45:00Z",
        updated_at_utc="2026-07-13T08:00:00Z",
    )
    assert record["audit_status"] == "PASS"
    assert record["receipt_valid"] is True
    assert record["receipt_status"] == "SMTP_ACCEPTED"
    assert record["smtp_accepted"] is True
    assert record["inbox_delivery_claimed"] is False

    history = email_delivery_audit.append_record(email_delivery_audit.empty_history(), record)
    history = email_delivery_audit.append_record(history, record)
    assert len(history) == 1

    skipped_artifact = root / "skipped-artifact" / "output"
    skipped_artifact.mkdir(parents=True)
    (skipped_artifact / "email_delivery_receipt.json").write_text(
        json.dumps(skipped, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    skipped_record = email_delivery_audit.build_record(
        root / "skipped-artifact",
        workflow_run_id="1002",
        workflow_run_url="https://example.test/runs/1002",
        upstream_conclusion="success",
        upstream_event="schedule",
        head_sha="b" * 40,
        created_at_utc="2026-07-14T07:45:00Z",
        updated_at_utc="2026-07-14T08:00:00Z",
    )
    history = email_delivery_audit.append_record(history, skipped_record)

    failed_artifact = root / "failed-artifact" / "output"
    failed_artifact.mkdir(parents=True)
    (failed_artifact / "email_delivery_receipt.json").write_text(
        json.dumps(failed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failed_record = email_delivery_audit.build_record(
        root / "failed-artifact",
        workflow_run_id="1003",
        workflow_run_url="https://example.test/runs/1003",
        upstream_conclusion="failure",
        upstream_event="workflow_dispatch",
        head_sha="c" * 40,
        created_at_utc="2026-07-15T07:45:00Z",
        updated_at_utc="2026-07-15T08:00:00Z",
    )
    history = email_delivery_audit.append_record(history, failed_record)
    assert len(history) == 3

    missing = email_delivery_audit.build_record(
        root / "missing-artifact",
        workflow_run_id="1004",
        workflow_run_url="https://example.test/runs/1004",
        upstream_conclusion="failure",
        upstream_event="schedule",
        head_sha="d" * 40,
        created_at_utc="2026-07-16T07:45:00Z",
        updated_at_utc="2026-07-16T08:00:00Z",
    )
    assert missing["audit_status"] == "FAIL"
    assert "receipt missing" in missing["audit_failures"]

    metrics = email_delivery_audit.monthly_metrics(history, "2026-07")
    assert metrics["email_receipt_run_count"] == 3
    assert metrics["email_receipt_valid_count"] == 3
    assert metrics["scheduled_email_receipt_count"] == 2
    assert metrics["scheduled_email_attempt_count"] == 1
    assert metrics["scheduled_smtp_accepted_count"] == 1
    assert metrics["scheduled_smtp_acceptance_rate"] == 0.5
    assert metrics["scheduled_email_skipped_count"] == 1
    assert metrics["scheduled_email_failed_count"] == 0
    assert metrics["email_delivery_observable"] is True
    assert metrics["email_delivery_status"] == "SMTP_ACCEPTANCE_TRACKED"
    assert metrics["inbox_delivery_observable"] is False

    status = email_delivery_audit.build_status(history)
    assert email_delivery_audit.validate_status(status) == []
    assert status["scheduled_receipt_count"] == 2
    assert status["scheduled_smtp_accepted_count"] == 1
    assert status["scheduled_smtp_acceptance_rate"] == 0.5
    assert status["delivery_claim"] == "SMTP_ACCEPTANCE_ONLY"
    assert status["inbox_delivery_claimed"] is False

committed_history = email_delivery_audit.load_history(ROOT / email_delivery_audit.DEFAULT_HISTORY)
committed_status = json.loads((ROOT / email_delivery_audit.DEFAULT_STATUS).read_text(encoding="utf-8"))
assert committed_history.empty
assert email_delivery_audit.validate_status(committed_status) == []
assert committed_status["audited_run_count"] == 0
assert committed_status["delivery_claim"] == "SMTP_ACCEPTANCE_ONLY"

monthly_review_runner.install_email_overlay()
payload = monthly_review_runner.build_review(
    "2026-06",
    repository="M-Osugi1230/momentum-chimpan",
    commit_sha="a" * 40,
    generated_at_utc="2026-07-13T00:00:00+00:00",
)
assert monthly_review.validate_review(payload) == []
assert len(payload["canonical_sources"]) == 12
assert payload["email_delivery_measurement"]["claim"] == "SMTP_ACCEPTANCE_ONLY"
assert payload["email_delivery_measurement"]["inbox_delivery_observable"] is False
assert any("final inbox delivery" in gap for gap in payload["known_measurement_gaps"])
flat = monthly_review.flatten_summary(payload)
assert "scheduled_smtp_acceptance_rate" in flat
markdown = monthly_review.markdown_report(payload)
assert "Scheduled SMTP acceptance" in markdown
assert "Inbox delivery" in markdown
assert "NOT_CAPTURED_SEPARATELY_FROM_WORKFLOW_SUCCESS" not in markdown

runner_source = (ROOT / "daily_runner.py").read_text(encoding="utf-8")
assert "email_delivery.send_with_receipt" in runner_source
assert "main_module.send_email = patched_send_email" in runner_source
assert "inbox_delivery_claimed=false" in runner_source

daily_workflow = (ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8")
assert "output/email_delivery_receipt.json" in daily_workflow
assert "RUN_URL:" in daily_workflow

audit_workflow = (ROOT / ".github/workflows/email-delivery-audit.yml").read_text(encoding="utf-8")
assert "Daily Momentum Report" in audit_workflow
assert "actions: read" in audit_workflow
assert "contents: write" in audit_workflow
assert "research/operations/email_delivery_audit.csv" in audit_workflow
assert "research/operations/email_delivery_audit_status.json" in audit_workflow
assert "git add --" in audit_workflow
staged = audit_workflow.split("git add --", 1)[1]
assert "data/momentum_daily_ranking.csv" not in staged
assert "config.yaml" not in staged
assert ("EMAIL_" + "APP_PASSWORD") not in audit_workflow

monthly_workflow = (ROOT / ".github/workflows/monthly-operations-review.yml").read_text(encoding="utf-8")
assert "monthly_review_runner.py build" in monthly_workflow
assert "monthly_review_runner.py validate" in monthly_workflow
assert "scheduled_smtp_acceptance_rate" in monthly_workflow
assert "inbox_delivery_observable" in monthly_workflow

print("email SMTP acceptance receipt validation passed")
