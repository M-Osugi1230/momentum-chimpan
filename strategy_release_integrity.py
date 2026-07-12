"""Independent immutability checks for strategy-release governance.

This module supplements ``strategy_release_gate.py``. It pins the critical
policy contract, makes candidate and approval registries append-only, preserves
registered acceptance criteria across PRs, and binds Git object IDs to explicit
SHA-256 digests. It never mutates production state or activates a strategy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml

POLICY_PATH = "research/strategy_release_policy.yaml"
CANDIDATES_PATH = "research/strategy_release_candidates.yaml"
APPROVALS_PATH = "research/strategy_approvals.yaml"
INTEGRITY_VERSION = "2026-07-13-strategy-release-integrity-v1"

EXPECTED_RELEASE_SURFACE = {
    "main_strategy": {
        "main_path": "main.py",
        "config_path": "config.yaml",
        "config_sections": ["market", "ranking", "signals"],
    },
    "additional_python_ast": {
        "data_quality.py": ["evaluate_row", "apply_priority_gate"],
        "daily_research_focus.py": ["base_bucket", "attach_daily_focus"],
    },
    "semantic_yaml": {
        "research/data_quality_policy.yaml": [
            "thresholds",
            "grades",
            "priority_boundary",
        ],
        "research/daily_research_focus_policy.yaml": [
            "limits",
            "bucket_mapping",
            "watch_rules",
            "governance.preserve_momentum_score",
            "governance.preserve_momentum_rank",
            "governance.preserve_paper_execution",
        ],
    },
}

EXPECTED_IMMUTABLE_FIELDS = [
    "release_id",
    "registered_at_utc",
    "change_type",
    "hypothesis",
    "expected_mechanism",
    "primary_metric",
    "acceptance_criteria_sha256",
    "current_strategy_fingerprint",
    "proposed_strategy_fingerprint",
    "research_pr_number",
]

CRITICAL_POLICY_EXPECTATIONS = {
    "policy.automatic_activation": False,
    "policy.automatic_merge": False,
    "policy.automatic_score_change": False,
    "policy.automatic_weight_change": False,
    "policy.automatic_strategy_change": False,
    "policy.automatic_priority_rule_change": False,
    "policy.live_orders": False,
    "policy.manual_review_required": True,
    "registration.must_precede_first_evidence_at": True,
    "registration.acceptance_criteria_hash_required": True,
    "registration.favorable_subperiod_only_prohibited": True,
    "registration.post_result_gate_change_prohibited": True,
    "research_evidence.discovery_holdout_separation_required": True,
    "research_evidence.prospective_or_shadow_evidence_required": True,
    "research_evidence.no_lookahead_required": True,
    "research_evidence.required_entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
    "research_evidence.same_day_close_entry_allowed": False,
    "research_evidence.transaction_cost_sensitivity_required": True,
    "research_evidence.early_late_stability_required": True,
    "research_evidence.regime_stability_required": True,
    "research_evidence.sector_or_concentration_stability_required": True,
    "research_evidence.sample_size_required": True,
    "research_evidence.confidence_interval_required": True,
    "shadow.minimum_distinct_market_sessions": 20,
    "shadow.current_and_proposed_run_in_parallel": True,
    "shadow.production_behavior_unchanged_during_shadow": True,
    "approval.decision_required": "APPROVE",
    "approval.scope_required": "MANUAL_REVIEW_ONLY",
    "production_pr.separate_from_research_pr": True,
    "production_pr.candidate_status_required": "APPROVED_FOR_PRODUCTION_PR",
    "production_pr.direct_research_result_activation_prohibited": True,
    "post_release.minimum_audit_market_sessions": 10,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def optional_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def nested_get(mapping: dict[str, Any], dotted_path: str) -> Any:
    current: Any = mapping
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def load_yaml_text(text: str, source: str) -> dict[str, Any]:
    payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be a mapping: {source}")
    return payload


def root_reader(root: str | Path) -> Callable[[str], str]:
    base = Path(root)

    def read(path: str) -> str:
        target = base / path
        return target.read_text(encoding="utf-8") if target.is_file() else ""

    return read


def git_reader(ref: str) -> Callable[[str], str]:
    def read(path: str) -> str:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout if result.returncode == 0 else ""

    return read


def load_from_reader(reader: Callable[[str], str], path: str) -> dict[str, Any]:
    text = reader(path)
    if not text:
        return {}
    return load_yaml_text(text, path)


def map_unique(entries: Any, key: str, label: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    issues: list[str] = []
    if not isinstance(entries, list):
        return {}, [f"{label} must be a list"]
    mapped: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            issues.append(f"{label}[{index}] must be a mapping")
            continue
        identifier = optional_text(entry.get(key))
        if not identifier:
            issues.append(f"{label}[{index}].{key} is required")
            continue
        if identifier in mapped:
            issues.append(f"duplicate {key}: {identifier}")
            continue
        mapped[identifier] = entry
    return mapped, issues


def validate_policy_contract(policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if int(policy.get("schema_version", 0)) != 1:
        issues.append("policy schema_version must be 1")
    if policy.get("release_surface") != EXPECTED_RELEASE_SURFACE:
        issues.append("critical release_surface contract differs from the pinned definition")
    immutable = (policy.get("candidate_registry") or {}).get("immutable_registration_fields")
    if immutable != EXPECTED_IMMUTABLE_FIELDS:
        issues.append("immutable registration fields differ from the pinned definition")
    for path, expected in CRITICAL_POLICY_EXPECTATIONS.items():
        actual = nested_get(policy, path)
        if actual != expected:
            issues.append(f"critical policy mismatch at {path}: expected {expected!r}, got {actual!r}")
    if (policy.get("candidate_registry") or {}).get("production_state_mutations") != []:
        issues.append("candidate registry production_state_mutations must remain empty")
    return issues


def valid_git_oid(value: Any) -> bool:
    text = optional_text(value).lower()
    return len(text) in {40, 64} and all(char in "0123456789abcdef" for char in text)


def oid_digest(value: Any) -> str:
    return hashlib.sha256(optional_text(value).lower().encode("utf-8")).hexdigest()


def validate_object_id_bindings(candidate: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    status = optional_text(candidate.get("status"))
    if status in {"RELEASED", "POST_RELEASE_AUDIT_COMPLETE", "ROLLED_BACK"}:
        release = candidate.get("release") or {}
        oid = release.get("merge_commit_oid")
        if not valid_git_oid(oid):
            issues.append("release.merge_commit_oid must be a 40- or 64-character Git object ID")
        if release.get("merge_commit_sha") != oid_digest(oid):
            issues.append("release.merge_commit_sha must equal SHA-256(lowercase merge_commit_oid)")
    if status == "ROLLED_BACK":
        rollback = candidate.get("rollback") or {}
        oid = rollback.get("rollback_commit_oid")
        if not valid_git_oid(oid):
            issues.append("rollback.rollback_commit_oid must be a 40- or 64-character Git object ID")
        if rollback.get("rollback_commit_sha") != oid_digest(oid):
            issues.append("rollback.rollback_commit_sha must equal SHA-256(lowercase rollback_commit_oid)")
    return issues


def validate_registries(candidates: dict[str, Any], approvals: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    candidate_map, candidate_issues = map_unique(candidates.get("candidates", []), "release_id", "candidates")
    approval_map, approval_issues = map_unique(approvals.get("approvals", []), "approval_id", "approvals")
    issues.extend(candidate_issues)
    issues.extend(approval_issues)
    approval_policy = approvals.get("policy") or {}
    if approval_policy.get("automatic_activation") is not False:
        issues.append("approval policy automatic_activation must remain false")
    if approval_policy.get("require_exact_strategy_fingerprint") is not True:
        issues.append("approval policy must require exact strategy fingerprint")
    if approval_policy.get("require_exact_evidence_status_sha256") is not True:
        issues.append("approval policy must require exact evidence status SHA-256")
    if approval_policy.get("require_exact_review_packet_sha256") is not True:
        issues.append("approval policy must require exact review packet SHA-256")
    if approval_policy.get("allowed_scope") != "MANUAL_REVIEW_ONLY":
        issues.append("approval policy scope must remain MANUAL_REVIEW_ONLY")
    for release_id, candidate in candidate_map.items():
        for issue in validate_object_id_bindings(candidate):
            issues.append(f"{release_id}: {issue}")
    return issues


def immutable_candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    payload = {field: candidate.get(field) for field in EXPECTED_IMMUTABLE_FIELDS}
    payload["acceptance_criteria"] = candidate.get("acceptance_criteria")
    payload["failure_conditions"] = candidate.get("failure_conditions")
    payload["registration"] = candidate.get("registration")
    return payload


def history_is_prefix(base: Any, head: Any) -> bool:
    return isinstance(base, list) and isinstance(head, list) and head[: len(base)] == base


def compare_append_only(
    base_candidates: dict[str, Any],
    head_candidates: dict[str, Any],
    base_approvals: dict[str, Any],
    head_approvals: dict[str, Any],
    release_surface_changed: bool,
) -> list[str]:
    issues: list[str] = []
    base_map, base_candidate_issues = map_unique(base_candidates.get("candidates", []), "release_id", "base candidates")
    head_map, head_candidate_issues = map_unique(head_candidates.get("candidates", []), "release_id", "head candidates")
    base_approval_map, base_approval_issues = map_unique(base_approvals.get("approvals", []), "approval_id", "base approvals")
    head_approval_map, head_approval_issues = map_unique(head_approvals.get("approvals", []), "approval_id", "head approvals")
    issues.extend(base_candidate_issues + head_candidate_issues + base_approval_issues + head_approval_issues)

    for release_id, base in base_map.items():
        head = head_map.get(release_id)
        if head is None:
            issues.append(f"candidate {release_id} was deleted; use a terminal status instead")
            continue
        if immutable_candidate_payload(base) != immutable_candidate_payload(head):
            issues.append(f"candidate {release_id} immutable registration fields changed")
        if not history_is_prefix(base.get("status_history", []), head.get("status_history", [])):
            issues.append(f"candidate {release_id} status_history is not append-only")

    new_candidates = sorted(set(head_map) - set(base_map))
    if release_surface_changed and new_candidates:
        issues.append(
            "a release-surface-changing PR cannot introduce its authorizing candidate for the first time: "
            + ", ".join(new_candidates)
        )

    for approval_id, base in base_approval_map.items():
        head = head_approval_map.get(approval_id)
        if head is None:
            issues.append(f"approval {approval_id} was deleted")
        elif canonical_json(base) != canonical_json(head):
            issues.append(f"approval {approval_id} changed after being recorded")
    return sorted(set(issues))


def release_surface_changed(base_ref: str, head_root: str | Path, policy: dict[str, Any]) -> tuple[bool, str, str]:
    import strategy_release_gate

    base = strategy_release_gate.release_surface_fingerprint_git(base_ref, policy)["sha256"]
    head = strategy_release_gate.release_surface_fingerprint(head_root, policy)["sha256"]
    return base != head, base, head


def validate_current(root: str | Path) -> dict[str, Any]:
    read = root_reader(root)
    policy = load_from_reader(read, POLICY_PATH)
    candidates = load_from_reader(read, CANDIDATES_PATH)
    approvals = load_from_reader(read, APPROVALS_PATH)
    issues = validate_policy_contract(policy) + validate_registries(candidates, approvals)
    substantive = {
        "integrity_version": INTEGRITY_VERSION,
        "policy_contract_sha256": canonical_hash({
            "release_surface": EXPECTED_RELEASE_SURFACE,
            "immutable_fields": EXPECTED_IMMUTABLE_FIELDS,
            "critical_expectations": CRITICAL_POLICY_EXPECTATIONS,
        }),
        "candidate_count": len(candidates.get("candidates", [])) if isinstance(candidates.get("candidates"), list) else 0,
        "approval_count": len(approvals.get("approvals", [])) if isinstance(approvals.get("approvals"), list) else 0,
        "automatic_activation": False,
        "automatic_merge": False,
        "production_state_mutations": [],
        "issues": sorted(set(issues)),
    }
    return {**substantive, "passed": not substantive["issues"], "status_sha256": canonical_hash(substantive)}


def pr_integrity(base_ref: str, head_root: str | Path) -> dict[str, Any]:
    head_read = root_reader(head_root)
    base_read = git_reader(base_ref)
    head_policy = load_from_reader(head_read, POLICY_PATH)
    base_policy = load_from_reader(base_read, POLICY_PATH)
    head_candidates = load_from_reader(head_read, CANDIDATES_PATH)
    base_candidates = load_from_reader(base_read, CANDIDATES_PATH)
    head_approvals = load_from_reader(head_read, APPROVALS_PATH)
    base_approvals = load_from_reader(base_read, APPROVALS_PATH)

    issues = validate_policy_contract(head_policy) + validate_registries(head_candidates, head_approvals)
    if base_policy and validate_policy_contract(base_policy):
        issues.append("base branch does not contain the pinned strategy-release policy contract")
    if base_policy and canonical_hash(base_policy.get("release_surface")) != canonical_hash(head_policy.get("release_surface")):
        issues.append("release_surface policy changed in the pull request")
    if base_policy and canonical_hash(base_policy.get("candidate_registry", {}).get("immutable_registration_fields")) != canonical_hash(head_policy.get("candidate_registry", {}).get("immutable_registration_fields")):
        issues.append("immutable candidate-field policy changed in the pull request")

    changed, base_fingerprint, head_fingerprint = release_surface_changed(base_ref, head_root, head_policy)
    issues.extend(compare_append_only(base_candidates, head_candidates, base_approvals, head_approvals, changed))
    substantive = {
        "integrity_version": INTEGRITY_VERSION,
        "base_ref": base_ref,
        "base_release_surface_fingerprint": base_fingerprint,
        "head_release_surface_fingerprint": head_fingerprint,
        "release_surface_changed": changed,
        "automatic_activation": False,
        "automatic_merge": False,
        "production_state_mutations": [],
        "issues": sorted(set(issues)),
    }
    return {**substantive, "passed": not substantive["issues"], "status_sha256": canonical_hash(substantive)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate strategy-release governance immutability")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--root", default=".")
    validate.add_argument("--output", default="")
    gate = commands.add_parser("pr-gate")
    gate.add_argument("--base-ref", required=True)
    gate.add_argument("--head-root", default=".")
    gate.add_argument("--output", default="")
    args = parser.parse_args()
    result = validate_current(args.root) if args.command == "validate" else pr_integrity(args.base_ref, args.head_root)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
