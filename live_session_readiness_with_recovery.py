"""Extend Live Session Readiness with exact same-day recovery verification.

The base readiness contract remains the source of truth for artifact, data,
priority, explanation, receipt, and state gates. This wrapper adds one critical
gate backed by ``daily_recovery_drill.py`` and re-signs the complete readiness
payload. It does not mutate production state or activate any strategy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import daily_recovery_drill
import live_session_readiness as base

READINESS_VERSION = base.READINESS_VERSION
RECOVERY_GATE_VERSION = "2026-07-13-readiness-exact-recovery-v1"

canonical_hash = base.canonical_hash
sha256_file = base.sha256_file
atomic_write_json = base.atomic_write_json
atomic_write_text = base.atomic_write_text
readiness_markdown = base.readiness_markdown
find_file = base.find_file


def optional_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be a mapping: {target}")
    return payload


def recovery_gate(
    artifact_root: str | Path,
    report_date: str,
    strategy_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(artifact_root)
    manifest_path = find_file(root, "recovery_drill_manifest.json")
    metadata: dict[str, Any] = {
        "gate_version": RECOVERY_GATE_VERSION,
        "manifest_path": str(manifest_path or ""),
        "manifest_sha256": sha256_file(manifest_path) if manifest_path else "",
        "status": "MISSING",
        "drill_fingerprint": "",
        "status_sha256": "",
        "expected_snapshot_date": "",
        "selected_snapshot_date": "",
        "strategy_fingerprint": "",
        "expected_snapshot_match": False,
        "production_state_unchanged": False,
        "production_state_mutated": False,
        "automatic_production_restore": False,
        "manual_restore_only": True,
        "sandbox_only": True,
        "validation_issues": [],
    }
    if not manifest_path:
        gate = {
            "gate": "exact_recovery_drill",
            "state": "FAIL",
            "critical": True,
            "detail": "signed exact recovery drill manifest is missing",
        }
        return gate, metadata

    try:
        manifest = load_json(manifest_path)
    except Exception as error:
        metadata["validation_issues"] = [f"manifest read failed: {error}"]
        gate = {
            "gate": "exact_recovery_drill",
            "state": "FAIL",
            "critical": True,
            "detail": metadata["validation_issues"][0],
        }
        return gate, metadata

    issues = daily_recovery_drill.validate_manifest(manifest)
    metadata.update({
        "status": optional_text(manifest.get("status")),
        "drill_fingerprint": optional_text(manifest.get("drill_fingerprint")),
        "status_sha256": optional_text(manifest.get("status_sha256")),
        "expected_snapshot_date": optional_text(
            manifest.get("expected_snapshot_date")
        ),
        "selected_snapshot_date": optional_text(
            manifest.get("selected_snapshot_date")
        ),
        "strategy_fingerprint": optional_text(
            manifest.get("strategy_fingerprint")
        ),
        "expected_snapshot_match": manifest.get("expected_snapshot_match") is True,
        "production_state_unchanged": manifest.get("production_state_unchanged")
        is True,
        "production_state_mutated": manifest.get("production_state_mutated") is True,
        "automatic_production_restore": manifest.get(
            "automatic_production_restore"
        )
        is True,
        "manual_restore_only": manifest.get("manual_restore_only") is True,
        "sandbox_only": manifest.get("sandbox_only") is True,
        "validation_issues": list(issues),
    })

    if manifest.get("status") != "PASS":
        issues.append(
            f"exact recovery drill status is {optional_text(manifest.get('status')) or 'UNKNOWN'}"
        )
    if manifest.get("operational_gate_passed") is not True:
        issues.append("exact recovery operational gate did not pass")
    if manifest.get("evidence_eligible") is not True:
        issues.append("exact recovery drill is not evidence eligible")
    if manifest.get("state_update_executed") is not True:
        issues.append("exact recovery drill did not verify a state-updating run")
    if optional_text(manifest.get("expected_snapshot_date")) != report_date:
        issues.append("exact recovery expected snapshot date differs from report date")
    if optional_text(manifest.get("selected_snapshot_date")) != report_date:
        issues.append("exact recovery selected snapshot date differs from report date")
    recovery_fingerprint = optional_text(manifest.get("strategy_fingerprint"))
    if recovery_fingerprint and recovery_fingerprint != strategy_fingerprint:
        issues.append("exact recovery strategy fingerprint mismatch")
    if manifest.get("expected_snapshot_match") is not True:
        issues.append("exact recovery snapshot date or manifest SHA-256 did not match")
    if manifest.get("production_state_unchanged") is not True:
        issues.append("exact recovery did not prove production state was unchanged")
    if manifest.get("production_state_mutated") is not False:
        issues.append("exact recovery reports production-state mutation")
    if manifest.get("automatic_production_restore") is not False:
        issues.append("automatic production restore must remain disabled")
    if manifest.get("manual_restore_only") is not True:
        issues.append("manual_restore_only must remain true")
    if manifest.get("sandbox_only") is not True:
        issues.append("sandbox_only must remain true")

    metadata["validation_issues"] = sorted(set(issues))
    if metadata["validation_issues"]:
        gate = {
            "gate": "exact_recovery_drill",
            "state": "FAIL",
            "critical": True,
            "detail": "; ".join(metadata["validation_issues"]),
        }
    else:
        gate = {
            "gate": "exact_recovery_drill",
            "state": "PASS",
            "critical": True,
            "detail": (
                f"same-day snapshot {report_date} restored and verified in isolated sandbox; "
                "production state unchanged"
            ),
        }
    return gate, metadata


def resign_payload(payload: dict[str, Any]) -> dict[str, Any]:
    work = dict(payload)
    work.pop("status_sha256", None)
    work.pop("readiness_fingerprint", None)
    generated_at = work.pop("generated_at_utc", "")
    substantive = work
    signed = {
        **substantive,
        "generated_at_utc": generated_at,
        "readiness_fingerprint": canonical_hash(substantive),
    }
    signed["status_sha256"] = canonical_hash(signed)
    return signed


def build_readiness(
    artifact_root: str | Path,
    source_run_id: str,
    source_run_url: str,
    upstream_conclusion: str,
    upstream_event: str,
    head_sha: str,
    created_at_utc: str,
    updated_at_utc: str,
) -> dict[str, Any]:
    payload = base.build_readiness(
        artifact_root=artifact_root,
        source_run_id=source_run_id,
        source_run_url=source_run_url,
        upstream_conclusion=upstream_conclusion,
        upstream_event=upstream_event,
        head_sha=head_sha,
        created_at_utc=created_at_utc,
        updated_at_utc=updated_at_utc,
    )
    gate, metadata = recovery_gate(
        artifact_root=artifact_root,
        report_date=optional_text(payload.get("report_date")),
        strategy_fingerprint=optional_text(payload.get("strategy_fingerprint")),
    )
    gates = [
        existing
        for existing in payload.get("gates", [])
        if isinstance(existing, dict)
        and optional_text(existing.get("gate")) != "exact_recovery_drill"
    ]
    gates.append(gate)
    payload["gates"] = gates
    payload["exact_recovery_drill"] = metadata

    critical_failures = [
        item
        for item in gates
        if isinstance(item, dict)
        and item.get("critical") is True
        and item.get("state") == "FAIL"
    ]
    review_warnings = [
        item
        for item in gates
        if isinstance(item, dict) and item.get("state") == "REVIEW_REQUIRED"
    ]
    if critical_failures:
        payload["readiness_state"] = "FAIL"
    elif review_warnings:
        payload["readiness_state"] = "REVIEW_REQUIRED"
    else:
        payload["readiness_state"] = "PASS"
    payload["critical_failure_count"] = len(critical_failures)
    payload["review_warning_count"] = len(review_warnings)
    recovery_passed = gate["state"] == "PASS"
    payload["eligible_for_forward_evidence"] = bool(
        payload.get("eligible_for_forward_evidence") and recovery_passed
    )
    payload["eligible_for_priority_outcome_ingestion"] = bool(
        payload.get("eligible_for_priority_outcome_ingestion") and recovery_passed
    )
    return resign_payload(payload)


def validate_readiness(payload: dict[str, Any]) -> list[str]:
    issues = list(base.validate_readiness(payload))
    exact = payload.get("exact_recovery_drill")
    if not isinstance(exact, dict):
        issues.append("exact_recovery_drill metadata is required")
    gates = [
        item
        for item in payload.get("gates", [])
        if isinstance(item, dict)
        and optional_text(item.get("gate")) == "exact_recovery_drill"
    ]
    if len(gates) != 1:
        issues.append("exact_recovery_drill gate must appear exactly once")
    elif gates[0].get("critical") is not True:
        issues.append("exact_recovery_drill gate must remain critical")

    if payload.get("readiness_state") in {"PASS", "REVIEW_REQUIRED"}:
        if not gates or gates[0].get("state") != "PASS":
            issues.append("evidence-ready payload requires exact recovery PASS")
        if payload.get("eligible_for_forward_evidence") is not True:
            issues.append("evidence-ready payload must allow Forward Evidence")
        if payload.get("eligible_for_priority_outcome_ingestion") is not True:
            issues.append("evidence-ready payload must allow priority outcomes")
    if payload.get("readiness_state") == "FAIL":
        if payload.get("eligible_for_forward_evidence") is not False:
            issues.append("FAIL payload cannot allow Forward Evidence")
        if payload.get("eligible_for_priority_outcome_ingestion") is not False:
            issues.append("FAIL payload cannot allow priority outcomes")
    return sorted(set(issues))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Live Session Readiness with exact recovery verification"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--artifact-root", required=True)
    build.add_argument("--source-run-id", required=True)
    build.add_argument("--source-run-url", required=True)
    build.add_argument("--upstream-conclusion", required=True)
    build.add_argument("--upstream-event", required=True)
    build.add_argument("--head-sha", required=True)
    build.add_argument("--created-at-utc", required=True)
    build.add_argument("--updated-at-utc", required=True)
    build.add_argument("--output-dir", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "build":
        payload = build_readiness(
            artifact_root=args.artifact_root,
            source_run_id=args.source_run_id,
            source_run_url=args.source_run_url,
            upstream_conclusion=args.upstream_conclusion,
            upstream_event=args.upstream_event,
            head_sha=args.head_sha,
            created_at_utc=args.created_at_utc,
            updated_at_utc=args.updated_at_utc,
        )
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        atomic_write_json(payload, output / "live_session_readiness.json")
        atomic_write_text(
            readiness_markdown(payload), output / "live_session_readiness.md"
        )
        issues = validate_readiness(payload)
        print(json.dumps({"payload": payload, "issues": issues}, ensure_ascii=False, indent=2))
        return 0 if not issues else 1

    payload = load_json(args.manifest)
    issues = validate_readiness(payload)
    print(json.dumps({"passed": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
