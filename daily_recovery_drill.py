"""Verify the exact snapshot sealed by one Daily Momentum Report run.

The drill restores governed state files into an isolated output sandbox only.
It never writes restored data into production paths and never enables automatic
production restore. The source of truth is the exact recovery audit generated
inside the same daily workflow run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import state_recovery

DRILL_VERSION = "2026-07-13-daily-exact-recovery-v1"
DEFAULT_AUDIT = "output/recovery_snapshot_audit.json"
DEFAULT_SNAPSHOT_ROOT = "data/state_snapshots"
DEFAULT_OUTPUT_DIR = "output/recovery"


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def optional_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def valid_sha256(value: Any) -> bool:
    text = optional_text(value).lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be a mapping: {target}")
    return payload


def production_hashes(production_root: str) -> dict[str, str]:
    root = Path(production_root)
    return {
        state_name: state_recovery.sha256_file(root / relative_path)
        for state_name, relative_path in state_recovery.STATE_FILES.items()
    }


def empty_catalog() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "snapshot_date",
        "snapshot_path",
        "valid",
        "issue_count",
        "issues",
        "strategy_fingerprint",
        "app_version",
        "manifest_sha256",
    ])


def empty_plan() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "state_name",
        "snapshot_path",
        "production_path",
        "snapshot_sha256",
        "production_sha256",
        "production_exists",
        "action",
    ])


def empty_restore() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "state_name",
        "snapshot_path",
        "sandbox_path",
        "snapshot_sha256",
        "sandbox_sha256",
        "verified",
    ])


def write_outputs(
    output_dir: str | Path,
    manifest: dict[str, Any],
    catalog: pd.DataFrame,
    plan: pd.DataFrame,
    restored: pd.DataFrame,
) -> None:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    state_recovery.atomic_write_json(manifest, root / "recovery_drill_manifest.json")
    catalog.to_csv(root / "recovery_snapshot_catalog.csv", index=False)
    plan.to_csv(root / "recovery_plan.csv", index=False)
    restored.to_csv(root / "recovery_restore_verification.csv", index=False)
    with pd.ExcelWriter(root / "recovery_drill.xlsx", engine="openpyxl") as writer:
        pd.DataFrame([{
            "drill_version": manifest.get("drill_version"),
            "status": manifest.get("status"),
            "operational_gate_passed": manifest.get("operational_gate_passed"),
            "state_update_executed": manifest.get("state_update_executed"),
            "expected_snapshot_date": manifest.get("expected_snapshot_date"),
            "selected_snapshot_date": manifest.get("selected_snapshot_date"),
            "expected_manifest_sha256": manifest.get("expected_manifest_sha256"),
            "selected_manifest_sha256": manifest.get("selected_manifest_sha256"),
            "expected_snapshot_match": manifest.get("expected_snapshot_match"),
            "snapshot_valid": manifest.get("snapshot_valid"),
            "verified_state_file_count": manifest.get("verified_state_file_count"),
            "production_state_unchanged": manifest.get("production_state_unchanged"),
            "production_state_mutated": manifest.get("production_state_mutated"),
            "automatic_production_restore": manifest.get("automatic_production_restore"),
            "manual_restore_only": manifest.get("manual_restore_only"),
            "issue_count": len(manifest.get("issues", [])),
            "drill_fingerprint": manifest.get("drill_fingerprint"),
            "status_sha256": manifest.get("status_sha256"),
        }]).to_excel(writer, sheet_name="Summary", index=False)
        catalog.to_excel(writer, sheet_name="Snapshot Catalog", index=False)
        plan.to_excel(writer, sheet_name="Recovery Plan", index=False)
        restored.to_excel(writer, sheet_name="Sandbox Verification", index=False)


def signed_manifest(substantive: dict[str, Any]) -> dict[str, Any]:
    payload = {
        **substantive,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "drill_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def skipped_manifest(audit: dict[str, Any]) -> dict[str, Any]:
    substantive = {
        "drill_version": DRILL_VERSION,
        "status": "SKIPPED_NO_STATE_UPDATE",
        "operational_gate_passed": True,
        "evidence_eligible": False,
        "state_update_executed": False,
        "expected_snapshot_date": optional_text(audit.get("snapshot_date")),
        "selected_snapshot_date": "",
        "expected_manifest_sha256": "",
        "selected_manifest_sha256": "",
        "expected_snapshot_match": False,
        "snapshot_valid": False,
        "snapshot_issue_count": 0,
        "snapshot_issues": [],
        "planned_state_file_count": 0,
        "would_restore_count": 0,
        "verified_state_file_count": 0,
        "production_state_unchanged": True,
        "production_state_mutated": False,
        "automatic_production_restore": False,
        "manual_restore_only": True,
        "sandbox_only": True,
        "issues": [],
        "research_only": True,
    }
    return signed_manifest(substantive)


def run_exact_drill(
    audit_path: str = DEFAULT_AUDIT,
    snapshot_root: str = DEFAULT_SNAPSHOT_ROOT,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    production_root: str = ".",
) -> dict[str, Any]:
    audit = load_json(audit_path)
    state_update = audit.get("state_update_executed") is True
    if not state_update:
        issues: list[str] = []
        if audit.get("status") != "SKIPPED_NO_STATE_UPDATE":
            issues.append("no-state-update audit status must be SKIPPED_NO_STATE_UPDATE")
        manifest = skipped_manifest(audit)
        if issues:
            substantive = dict(manifest)
            substantive.pop("generated_at_utc", None)
            substantive.pop("drill_fingerprint", None)
            substantive.pop("status_sha256", None)
            substantive.update({
                "status": "FAIL",
                "operational_gate_passed": False,
                "production_state_unchanged": True,
                "issues": issues,
            })
            manifest = signed_manifest(substantive)
        write_outputs(output_dir, manifest, empty_catalog(), empty_plan(), empty_restore())
        return manifest

    expected_date = optional_text(audit.get("snapshot_date"))
    expected_manifest_sha = optional_text(audit.get("snapshot_manifest_sha256"))
    snapshot_dir = Path(snapshot_root) / expected_date
    catalog = state_recovery.snapshot_catalog(snapshot_root)
    plan = empty_plan()
    restored = empty_restore()
    issues: list[str] = []

    if audit.get("status") != "SEALED":
        issues.append("recovery audit status is not SEALED")
    if audit.get("complete") is not True:
        issues.append("recovery audit is not complete")
    if not expected_date:
        issues.append("recovery audit snapshot_date is missing")
    if not valid_sha256(expected_manifest_sha):
        issues.append("recovery audit snapshot_manifest_sha256 is invalid")

    selected_manifest_sha = state_recovery.sha256_file(
        snapshot_dir / "snapshot_manifest.json"
    )
    expected_snapshot_match = bool(
        expected_date
        and snapshot_dir.is_dir()
        and valid_sha256(expected_manifest_sha)
        and selected_manifest_sha == expected_manifest_sha
    )
    if not expected_snapshot_match:
        issues.append("exact snapshot date or manifest SHA-256 does not match the source audit")

    snapshot_valid = False
    snapshot_manifest: dict[str, Any] = {}
    snapshot_issues: list[str] = []
    if snapshot_dir.is_dir():
        snapshot_valid, snapshot_manifest, snapshot_issues = state_recovery.validate_snapshot(
            snapshot_dir
        )
    else:
        snapshot_issues = ["expected snapshot directory is missing"]
    if not snapshot_valid:
        issues.extend(snapshot_issues)
    if optional_text(snapshot_manifest.get("snapshot_date")) != expected_date:
        issues.append("snapshot manifest date does not match source audit")

    before_hashes = production_hashes(production_root)
    if not issues:
        plan = state_recovery.build_recovery_plan(snapshot_dir, production_root)
        restored = state_recovery.restore_to_sandbox(
            snapshot_dir, str(Path(output_dir) / "sandbox")
        )
    after_hashes = production_hashes(production_root)
    production_state_unchanged = before_hashes == after_hashes
    if not production_state_unchanged:
        issues.append("production state changed during isolated recovery drill")

    verified_count = int(restored["verified"].sum()) if not restored.empty else 0
    expected_file_count = len(state_recovery.STATE_FILES)
    if snapshot_valid and verified_count != expected_file_count:
        issues.append(
            f"sandbox verification incomplete: {verified_count}/{expected_file_count}"
        )

    status = "PASS" if not issues else "FAIL"
    substantive = {
        "drill_version": DRILL_VERSION,
        "status": status,
        "operational_gate_passed": status == "PASS",
        "evidence_eligible": status == "PASS",
        "state_update_executed": True,
        "expected_snapshot_date": expected_date,
        "selected_snapshot_date": optional_text(snapshot_manifest.get("snapshot_date")),
        "expected_manifest_sha256": expected_manifest_sha,
        "selected_manifest_sha256": selected_manifest_sha,
        "expected_snapshot_match": expected_snapshot_match,
        "snapshot_valid": snapshot_valid,
        "snapshot_issue_count": len(snapshot_issues),
        "snapshot_issues": snapshot_issues,
        "strategy_fingerprint": optional_text(
            snapshot_manifest.get("strategy_fingerprint")
        ),
        "planned_state_file_count": len(plan),
        "would_restore_count": int((plan["action"] == "WOULD_RESTORE").sum())
        if not plan.empty
        else 0,
        "verified_state_file_count": verified_count,
        "production_state_unchanged": production_state_unchanged,
        "production_state_mutated": False,
        "automatic_production_restore": False,
        "manual_restore_only": True,
        "sandbox_only": True,
        "issues": sorted(set(issues)),
        "research_only": True,
    }
    manifest = signed_manifest(substantive)
    write_outputs(output_dir, manifest, catalog, plan, restored)
    return manifest


def validate_manifest(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("drill_version") != DRILL_VERSION:
        issues.append("invalid drill_version")
    if payload.get("status") not in {"PASS", "FAIL", "SKIPPED_NO_STATE_UPDATE"}:
        issues.append("invalid status")
    if payload.get("production_state_mutated") is not False:
        issues.append("production_state_mutated must be false")
    if payload.get("automatic_production_restore") is not False:
        issues.append("automatic_production_restore must be false")
    if payload.get("manual_restore_only") is not True:
        issues.append("manual_restore_only must be true")
    if payload.get("sandbox_only") is not True:
        issues.append("sandbox_only must be true")
    if payload.get("research_only") is not True:
        issues.append("research_only must be true")

    status = payload.get("status")
    if status == "PASS":
        for key in (
            "operational_gate_passed",
            "evidence_eligible",
            "state_update_executed",
            "expected_snapshot_match",
            "snapshot_valid",
            "production_state_unchanged",
        ):
            if payload.get(key) is not True:
                issues.append(f"PASS drill requires {key}=true")
        if payload.get("expected_snapshot_date") != payload.get(
            "selected_snapshot_date"
        ):
            issues.append("PASS drill snapshot date mismatch")
        if payload.get("expected_manifest_sha256") != payload.get(
            "selected_manifest_sha256"
        ):
            issues.append("PASS drill manifest SHA-256 mismatch")
        if int(payload.get("verified_state_file_count", 0) or 0) != len(
            state_recovery.STATE_FILES
        ):
            issues.append("PASS drill verified state file count mismatch")
        if payload.get("issues") != []:
            issues.append("PASS drill issues must be empty")
    elif status == "SKIPPED_NO_STATE_UPDATE":
        if payload.get("operational_gate_passed") is not True:
            issues.append("SKIPPED drill must pass the operational gate")
        if payload.get("evidence_eligible") is not False:
            issues.append("SKIPPED drill cannot be evidence eligible")
        if payload.get("state_update_executed") is not False:
            issues.append("SKIPPED drill must have state_update_executed=false")
    elif status == "FAIL" and payload.get("operational_gate_passed") is not False:
        issues.append("FAIL drill must fail the operational gate")

    status_copy = dict(payload)
    supplied_status = status_copy.pop("status_sha256", "")
    if supplied_status != canonical_hash(status_copy):
        issues.append("status_sha256 mismatch")
    substantive = dict(status_copy)
    substantive.pop("generated_at_utc", None)
    supplied_fingerprint = substantive.pop("drill_fingerprint", "")
    if supplied_fingerprint != canonical_hash(substantive):
        issues.append("drill_fingerprint mismatch")
    return sorted(set(issues))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the exact state snapshot sealed by one daily run"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--audit", default=DEFAULT_AUDIT)
    run.add_argument("--snapshot-root", default=DEFAULT_SNAPSHOT_ROOT)
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--production-root", default=".")
    run.add_argument("--strict", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument(
        "--manifest", default=f"{DEFAULT_OUTPUT_DIR}/recovery_drill_manifest.json"
    )
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "run":
        payload = run_exact_drill(
            audit_path=args.audit,
            snapshot_root=args.snapshot_root,
            output_dir=args.output_dir,
            production_root=args.production_root,
        )
        issues = validate_manifest(payload)
        result = {"payload": payload, "validation_issues": issues}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        failed = bool(issues) or payload.get("status") == "FAIL"
        return 1 if args.strict and failed else 0

    payload = load_json(args.manifest)
    issues = validate_manifest(payload)
    print(json.dumps({"passed": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
