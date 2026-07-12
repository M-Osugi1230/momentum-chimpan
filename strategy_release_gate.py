"""Mandatory CI gate for future production-strategy changes.

Research results never activate a production change automatically. A protected
change is accepted only when one approved candidate is bound to the exact base
and proposed release fingerprints and to the canonical human approval record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

import strategy_governance

POLICY_PATH = "research/strategy_release_policy.yaml"
REGISTRY_PATH = "research/strategy_release_candidates.yaml"
APPROVALS_PATH = "research/strategy_approvals.yaml"
APPROVED_STATUSES = {"APPROVED", "RELEASED"}
EXPECTED_PROCESS = [
    "RESEARCH_PR",
    "EVIDENCE_REVIEW_PACKET",
    "SHADOW_COMPARISON",
    "MANUAL_DECISION",
    "SEPARATE_PRODUCTION_CHANGE_PR",
    "POST_RELEASE_AUDIT",
]
EXPECTED_REQUIRED_PROCESS = EXPECTED_PROCESS
EXPECTED_EVIDENCE = {
    "preregistered_hypothesis_and_acceptance_criteria",
    "discovery_and_disjoint_holdout_separation",
    "prospective_or_shadow_evidence",
    "no_lookahead_execution_model",
    "transaction_cost_sensitivity",
    "early_late_and_market_regime_stability",
    "sample_size_and_confidence_interval",
    "current_and_proposed_release_fingerprints",
    "rollback_plan",
    "explicit_human_decision",
}

EXPECTED_PROHIBITED = {
    "DIRECT_PRODUCTION_EDIT_FROM_RESEARCH_RESULT",
    "AUTOMATIC_PROMOTION",
    "POST_RESULT_GATE_CHANGE",
    "FAVORABLE_RECENT_SUBPERIOD_ONLY",
}


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return text(value).lower() in {"true", "1", "yes", "y"}


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sha(value: Any) -> bool:
    candidate = text(value).lower()
    return len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate)


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping")
    return payload


def digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def release_surface(root: Path, policy: dict[str, Any]) -> dict[str, Any]:
    surface = policy.get("protected_surface") or {}
    strategy = strategy_governance.strategy_fingerprint(
        str(root / text(surface.get("strategy_python"))),
        str(root / text(surface.get("strategy_config"))),
    )
    exact_files = surface.get("exact_files") or []
    payload = {
        "strategy": strategy["sha256"],
        "exact_files": {
            text(path): file_digest(root / text(path))
            for path in exact_files
            if text(path)
        },
    }
    return {"release_fingerprint": digest(payload), "payload": payload}


def release_fingerprint(root: Path, policy: dict[str, Any]) -> str:
    return release_surface(root, policy)["release_fingerprint"]


def validate_policy(policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if text(policy.get("execution_mode")) != "RESEARCH_AND_PAPER_ONLY":
        issues.append("execution_mode must remain RESEARCH_AND_PAPER_ONLY")
    if boolean(policy.get("automatic_activation")):
        issues.append("automatic_activation must be false")
    if not boolean(policy.get("manual_human_decision_required")):
        issues.append("manual_human_decision_required must be true")
    if not boolean(policy.get("separate_production_change_pr_required")):
        issues.append("separate_production_change_pr_required must be true")
    if integer(policy.get("minimum_shadow_market_sessions")) < 20:
        issues.append("minimum_shadow_market_sessions must be at least 20")
    if policy.get("required_process") != EXPECTED_PROCESS:
        issues.append("the six release stages must remain ordered and complete")
    missing = EXPECTED_PROHIBITED - set(policy.get("prohibited") or [])
    if missing:
        issues.append(f"missing prohibited actions: {sorted(missing)}")
    surface = policy.get("protected_surface") or {}
    if not text(surface.get("strategy_python")) or not text(surface.get("strategy_config")):
        issues.append("protected strategy Python and config paths are required")
    if not isinstance(surface.get("exact_files"), list) or not surface.get("exact_files"):
        issues.append("at least one exact governed policy file is required")
    if text(policy.get("candidate_registry")) != REGISTRY_PATH:
        issues.append(f"candidate_registry must be {REGISTRY_PATH}")
    if text(policy.get("approval_registry")) != APPROVALS_PATH:
        issues.append(f"approval_registry must be {APPROVALS_PATH}")
    return issues


def validate_registry(registry: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if integer(registry.get("schema_version")) != 1:
        issues.append("candidate registry schema_version must be 1")
    if text(registry.get("policy_version")) != text(policy.get("policy_version")):
        issues.append("candidate registry policy_version must match policy")
    if boolean(registry.get("automatic_activation")):
        issues.append("candidate registry automatic_activation must be false")
    if not isinstance(registry.get("candidates"), list):
        issues.append("candidate registry candidates must be a list")
    return issues


def validate_approvals(approvals: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if boolean((approvals.get("policy") or {}).get("automatic_activation")):
        issues.append("approval registry automatic_activation must be false")
    if not isinstance(approvals.get("approvals"), list):
        issues.append("approval registry approvals must be a list")
    return issues


def approval_for(candidate: dict[str, Any], approvals: dict[str, Any]) -> dict[str, Any] | None:
    approval_id = text((candidate.get("manual_decision") or {}).get("approval_id"))
    matches = [
        row for row in approvals.get("approvals", [])
        if isinstance(row, dict)
        and text(row.get("approval_id")) == approval_id
        and text(row.get("candidate_id")) == text(candidate.get("candidate_id"))
    ]
    return matches[0] if len(matches) == 1 else None


def validate_candidate(
    candidate: dict[str, Any], policy: dict[str, Any], approvals: dict[str, Any]
) -> list[str]:
    cid = text(candidate.get("candidate_id")) or "<missing candidate_id>"
    prefix = f"{cid}: "
    issues: list[str] = []
    required_text = [
        "candidate_id", "registered_at", "hypothesis", "change_summary",
        "research_pr", "rollback_plan", "production_change_pr",
    ]
    for field in required_text:
        if not text(candidate.get(field)):
            issues.append(prefix + f"{field} is required")
    if text(candidate.get("status")) not in APPROVED_STATUSES:
        issues.append(prefix + "status must be APPROVED or RELEASED")
    if text(candidate.get("change_type")) not in set(policy.get("covered_change_types") or []):
        issues.append(prefix + "change_type is not covered by policy")
    if boolean(candidate.get("automatic_activation")):
        issues.append(prefix + "automatic_activation must be false")
    if not boolean(candidate.get("registered_before_results")):
        issues.append(prefix + "registration must precede results")
    if not isinstance(candidate.get("acceptance_criteria"), list) or not candidate.get("acceptance_criteria"):
        issues.append(prefix + "acceptance_criteria must be non-empty")
    for field in ("current_release_fingerprint", "proposed_release_fingerprint"):
        if not sha(candidate.get(field)):
            issues.append(prefix + f"{field} must be SHA-256")
    if text(candidate.get("research_pr")) == text(candidate.get("production_change_pr")):
        issues.append(prefix + "production change PR must be separate from research PR")

    evidence = candidate.get("evidence") or {}
    if not boolean(evidence.get("complete")):
        issues.append(prefix + "evidence must be complete")
    separation = evidence.get("discovery_holdout") or {}
    if not boolean(separation.get("separated")) or integer(separation.get("overlap_count"), -1) != 0:
        issues.append(prefix + "discovery and holdout must be disjoint")
    execution = evidence.get("execution") or {}
    if not boolean(execution.get("no_lookahead")) or boolean(execution.get("same_day_close_entry_allowed")):
        issues.append(prefix + "no-lookahead execution is required and same-day close entry is prohibited")
    if not boolean((evidence.get("transaction_costs") or {}).get("sensitivity_tested")):
        issues.append(prefix + "transaction-cost sensitivity is required")
    stability = evidence.get("stability") or {}
    for field in ("early_period_tested", "late_period_tested", "market_regimes_tested"):
        if not boolean(stability.get(field)):
            issues.append(prefix + f"{field} must be true")
    statistics = evidence.get("statistics") or {}
    if integer(statistics.get("sample_size")) <= 0 or not boolean(statistics.get("confidence_interval_available")):
        issues.append(prefix + "sample size and confidence interval are required")
    packet = evidence.get("review_packet") or {}
    if not text(packet.get("artifact")) or not sha(packet.get("sha256")):
        issues.append(prefix + "hash-bound evidence review packet is required")

    shadow = candidate.get("shadow_comparison") or {}
    if integer(shadow.get("market_sessions")) < integer(policy.get("minimum_shadow_market_sessions"), 20):
        issues.append(prefix + "at least 20 market sessions are required for shadow comparison")
    if text(shadow.get("status")) != "PASS" or not text(shadow.get("artifact")) or not sha(shadow.get("sha256")):
        issues.append(prefix + "a passing hash-bound shadow comparison is required")

    decision = candidate.get("manual_decision") or {}
    if text(decision.get("decision")) != "APPROVED":
        issues.append(prefix + "explicit human APPROVED decision is required")
    for field in ("approval_id", "decided_by", "decided_at", "record"):
        if not text(decision.get(field)):
            issues.append(prefix + f"manual_decision.{field} is required")
    if not sha(decision.get("record_sha256")):
        issues.append(prefix + "manual decision record SHA-256 is required")

    record = approval_for(candidate, approvals)
    if record is None:
        issues.append(prefix + "exactly one canonical approval record is required")
    else:
        expected = {
            "current_release_fingerprint": candidate.get("current_release_fingerprint"),
            "proposed_release_fingerprint": candidate.get("proposed_release_fingerprint"),
            "evidence_review_packet_sha256": packet.get("sha256"),
            "shadow_comparison_sha256": shadow.get("sha256"),
            "production_change_pr": candidate.get("production_change_pr"),
        }
        if text(record.get("decision")) != "APPROVED" or not boolean(record.get("approved")):
            issues.append(prefix + "canonical approval must explicitly approve")
        if boolean(record.get("automatic_activation")):
            issues.append(prefix + "canonical approval automatic_activation must be false")
        for field, value in expected.items():
            if text(record.get(field)) != text(value):
                issues.append(prefix + f"canonical approval {field} mismatch")
    return issues


def evaluate_change(
    base: Path,
    current: Path,
    policy: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    approvals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy or load_yaml_mapping(current / POLICY_PATH)
    registry = registry or load_yaml_mapping(current / REGISTRY_PATH)
    approvals = approvals or load_yaml_mapping(current / APPROVALS_PATH)
    base_fp = release_fingerprint(base, policy)
    current_fp = release_fingerprint(current, policy)
    changed = base_fp != current_fp
    issues = validate_policy(policy) + validate_registry(registry, policy) + validate_approvals(approvals)
    candidate: dict[str, Any] | None = None
    if changed and not issues:
        matches = [
            row for row in registry.get("candidates", [])
            if isinstance(row, dict)
            and text(row.get("status")) in APPROVED_STATUSES
            and text(row.get("current_release_fingerprint")) == base_fp
            and text(row.get("proposed_release_fingerprint")) == current_fp
        ]
        if len(matches) != 1:
            issues.append("protected strategy surface changed without exactly one APPROVED/RELEASED candidate bound to the base and proposed release fingerprints")
        else:
            candidate = matches[0]
            issues.extend(validate_candidate(candidate, policy, approvals))
    return {
        "policy_version": policy.get("policy_version"),
        "base_release_fingerprint": base_fp,
        "current_release_fingerprint": current_fp,
        "protected_strategy_changed": changed,
        "authorizing_candidate_id": text(candidate.get("candidate_id")) if candidate else "",
        "automatic_activation": False,
        "issues": issues,
        "passed": not issues,
    }


def validate_current(root: Path) -> dict[str, Any]:
    policy = load_yaml_mapping(root / POLICY_PATH)
    registry = load_yaml_mapping(root / REGISTRY_PATH)
    approvals = load_yaml_mapping(root / APPROVALS_PATH)
    issues = validate_policy(policy) + validate_registry(registry, policy) + validate_approvals(approvals)
    return {
        "policy_version": policy.get("policy_version"),
        "current_release_fingerprint": release_fingerprint(root, policy),
        "candidate_count": len(registry.get("candidates") or []),
        "automatic_activation": False,
        "issues": issues,
        "passed": not issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--root", default=".")
    check = commands.add_parser("check-change")
    check.add_argument("--base-root", required=True)
    check.add_argument("--current-root", required=True)
    args = parser.parse_args()
    result = (
        validate_current(Path(args.root).resolve())
        if args.command == "validate"
        else evaluate_change(Path(args.base_root).resolve(), Path(args.current_root).resolve())
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
