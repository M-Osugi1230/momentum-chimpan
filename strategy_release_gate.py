"""Mandatory release governance for production strategy changes.

The gate complements the existing experiment registry and manual review packet.
A release-surface change is rejected unless one candidate has completed the
registered evidence and shadow process, has an exact human approval, identifies
a separate production PR, and contains a rollback plan. Nothing in this module
can activate, merge, trade, or mutate production state automatically.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

import strategy_governance

POLICY_PATH = "research/strategy_release_policy.yaml"
CANDIDATES_PATH = "research/strategy_release_candidates.yaml"
APPROVALS_PATH = "research/strategy_approvals.yaml"
DEFAULT_OUTPUT_DIR = "output/strategy-release-gate"
GATE_VERSION = "2026-07-13-strategy-release-gate-v1"
SHA256_LENGTH = 64


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def optional_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "none", "nan", "nat"} else text


def valid_sha256(value: Any) -> bool:
    text = optional_text(value).lower()
    return len(text) == SHA256_LENGTH and all(character in "0123456789abcdef" for character in text)


def parse_timestamp(value: Any) -> datetime | None:
    text = optional_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def nested_get(mapping: dict[str, Any], dotted_path: str) -> Any:
    current: Any = mapping
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def load_yaml(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(str(path))
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return payload


def load_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = load_yaml(path)
    issues = validate_policy(payload)
    if issues:
        raise ValueError("; ".join(issues))
    return payload


def load_candidates(path: str | Path = CANDIDATES_PATH) -> dict[str, Any]:
    return load_yaml(path)


def load_approvals(path: str | Path = APPROVALS_PATH) -> dict[str, Any]:
    return load_yaml(path)


def validate_policy(policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if int(policy.get("schema_version", 0)) != 1:
        issues.append("policy schema_version must be 1")
    metadata = policy.get("policy", {})
    if metadata.get("id") != "strategy-release-governance-v1":
        issues.append("invalid policy id")
    for key in (
        "automatic_activation",
        "automatic_merge",
        "automatic_score_change",
        "automatic_weight_change",
        "automatic_strategy_change",
        "automatic_priority_rule_change",
        "live_orders",
    ):
        if metadata.get(key) is not False:
            issues.append(f"policy.{key} must be false")
    if metadata.get("manual_review_required") is not True:
        issues.append("manual review must be required")
    statuses = policy.get("statuses", [])
    if not isinstance(statuses, list) or len(statuses) != len(set(statuses)):
        issues.append("statuses must be a unique list")
    transitions = policy.get("allowed_transitions", {})
    if not isinstance(transitions, dict) or set(transitions) != set(statuses):
        issues.append("allowed_transitions must cover every status exactly")
    else:
        for source, targets in transitions.items():
            if not isinstance(targets, list) or any(target not in statuses for target in targets):
                issues.append(f"invalid transitions for {source}")
    change_types = policy.get("change_types", [])
    if not isinstance(change_types, list) or not change_types:
        issues.append("change_types must be a non-empty list")
    shadow = policy.get("shadow", {})
    if int(shadow.get("minimum_distinct_market_sessions", 0)) < 20:
        issues.append("shadow requires at least 20 distinct market sessions")
    research = policy.get("research_evidence", {})
    if research.get("required_entry_model") != "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN":
        issues.append("required entry model must be next-session adjusted open")
    if research.get("same_day_close_entry_allowed") is not False:
        issues.append("same-day close entry must be prohibited")
    production = policy.get("production_pr", {})
    if production.get("separate_from_research_pr") is not True:
        issues.append("research and production PRs must be separate")
    if production.get("candidate_status_required") != "APPROVED_FOR_PRODUCTION_PR":
        issues.append("production PR candidate status must be APPROVED_FOR_PRODUCTION_PR")
    post = policy.get("post_release", {})
    if int(post.get("minimum_audit_market_sessions", 0)) < 10:
        issues.append("post-release audit requires at least 10 sessions")
    if policy.get("candidate_registry", {}).get("production_state_mutations") != []:
        issues.append("release registry production_state_mutations must be empty")
    return issues


def selected_function_ast(source: str, function_names: list[str]) -> dict[str, str]:
    if not source:
        return {name: "MISSING" for name in function_names}
    tree = ast.parse(source)
    selected: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in function_names:
            selected[node.name] = ast.dump(node, annotate_fields=True, include_attributes=False)
    for name in function_names:
        selected.setdefault(name, "MISSING")
    return dict(sorted(selected.items()))


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


def parse_yaml_text(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        payload = yaml.safe_load(text) or {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def release_surface_payload(reader: Callable[[str], str], policy: dict[str, Any]) -> dict[str, Any]:
    surface = policy["release_surface"]
    main_settings = surface["main_strategy"]
    main_source = reader(main_settings["main_path"])
    config_payload = parse_yaml_text(reader(main_settings["config_path"]))
    selected_config = {
        section: config_payload.get(section)
        for section in main_settings.get("config_sections", [])
        if section in config_payload
    }
    payload: dict[str, Any] = {
        "main_strategy_ast": strategy_governance.extract_strategy_ast(main_source),
        "main_strategy_config": selected_config,
        "additional_python_ast": {},
        "semantic_yaml": {},
    }
    for path, names in sorted(surface.get("additional_python_ast", {}).items()):
        payload["additional_python_ast"][path] = selected_function_ast(reader(path), list(names))
    for path, keys in sorted(surface.get("semantic_yaml", {}).items()):
        source = parse_yaml_text(reader(path))
        payload["semantic_yaml"][path] = {
            key: nested_get(source, key)
            for key in keys
        }
    return payload


def release_surface_fingerprint(root: str | Path, policy: dict[str, Any]) -> dict[str, Any]:
    payload = release_surface_payload(root_reader(root), policy)
    return {"sha256": canonical_hash(payload), "payload": payload}


def release_surface_fingerprint_git(ref: str, policy: dict[str, Any]) -> dict[str, Any]:
    payload = release_surface_payload(git_reader(ref), policy)
    return {"sha256": canonical_hash(payload), "payload": payload}


def git_changed_files(base_ref: str, head_ref: str = "HEAD") -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def acceptance_criteria_hash(candidate: dict[str, Any]) -> str:
    return canonical_hash(candidate.get("acceptance_criteria", {}))


def approval_map(approvals: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = approvals.get("approvals", [])
    if not isinstance(entries, list):
        return {}
    return {
        optional_text(entry.get("approval_id")): entry
        for entry in entries
        if isinstance(entry, dict) and optional_text(entry.get("approval_id"))
    }


def validate_transition_history(candidate: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    history = candidate.get("status_history", [])
    if not isinstance(history, list) or not history:
        return ["status_history must be a non-empty list"]
    transitions = policy["allowed_transitions"]
    previous_status = ""
    previous_time: datetime | None = None
    for index, entry in enumerate(history):
        if not isinstance(entry, dict):
            issues.append(f"status_history[{index}] must be a mapping")
            continue
        status = optional_text(entry.get("status"))
        timestamp = parse_timestamp(entry.get("at_utc"))
        if status not in policy["statuses"]:
            issues.append(f"status_history[{index}] has invalid status")
        if timestamp is None:
            issues.append(f"status_history[{index}] has invalid timestamp")
        if index == 0 and status != "REGISTERED_RESEARCH":
            issues.append("first status must be REGISTERED_RESEARCH")
        if previous_status and status not in transitions.get(previous_status, []):
            issues.append(f"invalid status transition {previous_status}->{status}")
        if previous_time and timestamp and timestamp < previous_time:
            issues.append("status_history timestamps must be monotonic")
        previous_status = status
        previous_time = timestamp or previous_time
    if previous_status != optional_text(candidate.get("status")):
        issues.append("current status must equal final status_history entry")
    return issues


def validate_registration(candidate: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required_text = (
        "release_id",
        "title",
        "change_type",
        "status",
        "registered_at_utc",
        "hypothesis",
        "expected_mechanism",
        "primary_metric",
        "acceptance_criteria_sha256",
        "current_strategy_fingerprint",
        "proposed_strategy_fingerprint",
    )
    for field in required_text:
        if not optional_text(candidate.get(field)):
            issues.append(f"{field} is required")
    if candidate.get("change_type") not in policy["change_types"]:
        issues.append("invalid change_type")
    if candidate.get("status") not in policy["statuses"]:
        issues.append("invalid status")
    registered_at = parse_timestamp(candidate.get("registered_at_utc"))
    frozen_at = parse_timestamp(candidate.get("acceptance_criteria_frozen_at_utc"))
    if registered_at is None:
        issues.append("registered_at_utc must be a valid timestamp")
    if frozen_at is None:
        issues.append("acceptance_criteria_frozen_at_utc must be a valid timestamp")
    if registered_at and frozen_at and frozen_at < registered_at:
        issues.append("acceptance criteria cannot be frozen before registration")
    if not isinstance(candidate.get("acceptance_criteria"), dict) or not candidate.get("acceptance_criteria"):
        issues.append("acceptance_criteria must be a non-empty mapping")
    elif candidate.get("acceptance_criteria_sha256") != acceptance_criteria_hash(candidate):
        issues.append("acceptance_criteria_sha256 mismatch")
    for field in ("current_strategy_fingerprint", "proposed_strategy_fingerprint"):
        if not valid_sha256(candidate.get(field)):
            issues.append(f"{field} must be a SHA-256 hex string")
    if (
        valid_sha256(candidate.get("current_strategy_fingerprint"))
        and candidate.get("current_strategy_fingerprint") == candidate.get("proposed_strategy_fingerprint")
    ):
        issues.append("current and proposed strategy fingerprints must differ")
    research_pr = int(candidate.get("research_pr_number", 0) or 0)
    if research_pr <= 0:
        issues.append("research_pr_number must be positive")
    registration = candidate.get("registration", {}) or {}
    if registration.get("gate_changed_after_results") is not False:
        issues.append("post-result gate changes are prohibited")
    if registration.get("favorable_subperiod_only") is not False:
        issues.append("favorable-subperiod-only evidence is prohibited")
    failure_conditions = candidate.get("failure_conditions", [])
    if not isinstance(failure_conditions, list) or not failure_conditions:
        issues.append("failure_conditions must be a non-empty list")
    issues.extend(validate_transition_history(candidate, policy))
    return issues


def validate_evidence(candidate: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    evidence = candidate.get("evidence", {}) or {}
    registered = parse_timestamp(candidate.get("registered_at_utc"))
    frozen = parse_timestamp(candidate.get("acceptance_criteria_frozen_at_utc"))
    first_evidence = parse_timestamp(evidence.get("first_evidence_at_utc"))
    completed = parse_timestamp(evidence.get("completed_at_utc"))
    if first_evidence is None or completed is None:
        issues.append("evidence timestamps are required")
    if registered and first_evidence and registered >= first_evidence:
        issues.append("registration must strictly precede first evidence")
    if frozen and first_evidence and frozen >= first_evidence:
        issues.append("acceptance criteria must be frozen before first evidence")
    if first_evidence and completed and completed < first_evidence:
        issues.append("evidence completion cannot precede first evidence")
    required_true = (
        "discovery_holdout_separated",
        "prospective_or_shadow_evidence",
        "no_lookahead_verified",
        "transaction_cost_sensitivity",
        "early_late_stability",
        "regime_stability",
        "sector_or_concentration_stability",
        "sample_size_adequate",
        "confidence_interval_reported",
        "evidence_origin_registered",
    )
    for field in required_true:
        if evidence.get(field) is not True:
            issues.append(f"evidence.{field} must be true")
    if evidence.get("multiple_testing_control_applicable") is True and evidence.get("multiple_testing_control_applied") is not True:
        issues.append("multiple-testing control must be applied when applicable")
    if evidence.get("entry_model") != policy["research_evidence"]["required_entry_model"]:
        issues.append("evidence entry model mismatch")
    if evidence.get("same_day_close_entry") is not False:
        issues.append("same-day close entry is prohibited")
    for field in ("evidence_status_sha256", "evidence_packet_sha256", "review_packet_sha256"):
        if not valid_sha256(evidence.get(field)):
            issues.append(f"evidence.{field} must be a SHA-256 hex string")
    if int(evidence.get("outcome_count", 0) or 0) <= 0:
        issues.append("evidence.outcome_count must be positive")
    if int(evidence.get("distinct_signal_dates", 0) or 0) <= 0:
        issues.append("evidence.distinct_signal_dates must be positive")
    return issues


def validate_shadow(candidate: dict[str, Any], policy: dict[str, Any], require_complete: bool) -> list[str]:
    issues: list[str] = []
    shadow = candidate.get("shadow", {}) or {}
    if shadow.get("current_and_proposed_run_in_parallel") is not True:
        issues.append("shadow must run current and proposed versions in parallel")
    if shadow.get("production_behavior_unchanged") is not True:
        issues.append("production behavior must remain unchanged during shadow")
    if shadow.get("current_strategy_fingerprint") != candidate.get("current_strategy_fingerprint"):
        issues.append("shadow current fingerprint mismatch")
    if shadow.get("proposed_strategy_fingerprint") != candidate.get("proposed_strategy_fingerprint"):
        issues.append("shadow proposed fingerprint mismatch")
    if parse_timestamp(shadow.get("started_at_utc")) is None:
        issues.append("shadow.started_at_utc is required")
    if require_complete:
        if parse_timestamp(shadow.get("completed_at_utc")) is None:
            issues.append("shadow.completed_at_utc is required")
        minimum = int(policy["shadow"]["minimum_distinct_market_sessions"])
        if int(shadow.get("distinct_market_sessions", 0) or 0) < minimum:
            issues.append(f"shadow requires at least {minimum} distinct market sessions")
        if not valid_sha256(shadow.get("result_sha256")):
            issues.append("shadow.result_sha256 must be a SHA-256 hex string")
        if int(shadow.get("unresolved_incident_count", -1) or 0) != 0:
            issues.append("shadow unresolved_incident_count must be zero")
        if shadow.get("acceptance_criteria_passed") is not True:
            issues.append("shadow acceptance criteria must pass")
    return issues


def matching_approval(candidate: dict[str, Any], approvals: dict[str, Any]) -> dict[str, Any] | None:
    approval_id = optional_text((candidate.get("approval", {}) or {}).get("approval_id"))
    return approval_map(approvals).get(approval_id)


def validate_approval(candidate: dict[str, Any], approvals: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    approval = matching_approval(candidate, approvals)
    if approval is None:
        return ["exact approval_id was not found in the approval registry"]
    evidence = candidate.get("evidence", {}) or {}
    if optional_text(approval.get("decision")).upper() != policy["approval"]["decision_required"]:
        issues.append("approval decision must be APPROVE")
    if approval.get("scope") != policy["approval"]["scope_required"]:
        issues.append("approval scope mismatch")
    if approval.get("strategy_fingerprint") != candidate.get("proposed_strategy_fingerprint"):
        issues.append("approval proposed fingerprint mismatch")
    if approval.get("evidence_status_sha256") != evidence.get("evidence_status_sha256"):
        issues.append("approval evidence status hash mismatch")
    if approval.get("review_packet_sha256") != evidence.get("review_packet_sha256"):
        issues.append("approval review packet hash mismatch")
    reviewer = optional_text(approval.get("reviewer"))
    approved_at = parse_timestamp(approval.get("approved_at_utc"))
    if not reviewer:
        issues.append("approval reviewer is required")
    if approved_at is None:
        issues.append("approval timestamp is invalid")
    shadow_completed = parse_timestamp((candidate.get("shadow", {}) or {}).get("completed_at_utc"))
    if approved_at and shadow_completed and approved_at <= shadow_completed:
        issues.append("approval must occur after shadow completion")
    return issues


def validate_production_pr(candidate: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    production = candidate.get("production_pr", {}) or {}
    research_pr = int(candidate.get("research_pr_number", 0) or 0)
    production_pr = int(production.get("pr_number", 0) or 0)
    if production_pr <= 0:
        issues.append("production_pr.pr_number must be positive")
    if production_pr == research_pr:
        issues.append("research and production PR numbers must differ")
    if production.get("target_branch") != "main":
        issues.append("production PR target_branch must be main")
    rollback = candidate.get("rollback", {}) or {}
    for field in ("plan", "rollback_ref", "owner", "trigger_conditions"):
        value = rollback.get(field)
        if isinstance(value, list):
            valid = bool(value)
        else:
            valid = bool(optional_text(value))
        if not valid:
            issues.append(f"rollback.{field} is required")
    if rollback.get("automatic_rollback") is not False:
        issues.append("automatic rollback must remain false")
    return issues


def validate_release(candidate: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    release = candidate.get("release", {}) or {}
    if not valid_sha256(release.get("merge_commit_sha")):
        issues.append("release.merge_commit_sha must be SHA-256 length")
    if parse_timestamp(release.get("released_at_utc")) is None:
        issues.append("release.released_at_utc is required")
    if release.get("production_pr_merged") is not True:
        issues.append("production PR must be marked merged")
    if release.get("automatic_activation") is not False:
        issues.append("automatic activation must remain false")
    return issues


def validate_post_release(candidate: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    audit = candidate.get("post_release_audit", {}) or {}
    minimum = int(policy["post_release"]["minimum_audit_market_sessions"])
    if int(audit.get("market_sessions", 0) or 0) < minimum:
        issues.append(f"post-release audit requires at least {minimum} market sessions")
    if audit.get("operational_incidents_reviewed") is not True:
        issues.append("post-release operational incidents must be reviewed")
    if audit.get("performance_and_risk_reviewed") is not True:
        issues.append("post-release performance and risk must be reviewed")
    if audit.get("rollback_decision") not in {"KEEP", "ROLLBACK"}:
        issues.append("post-release rollback_decision must be KEEP or ROLLBACK")
    if not valid_sha256(audit.get("audit_sha256")):
        issues.append("post-release audit_sha256 must be a SHA-256 hex string")
    return issues


def status_index(status: str, policy: dict[str, Any]) -> int:
    order = policy["statuses"]
    return order.index(status) if status in order else -1


def validate_candidate(candidate: dict[str, Any], policy: dict[str, Any], approvals: dict[str, Any]) -> list[str]:
    issues = validate_registration(candidate, policy)
    status = optional_text(candidate.get("status"))
    if status in {"REJECTED"}:
        return issues
    if status_index(status, policy) >= status_index("EVIDENCE_READY", policy):
        issues.extend(validate_evidence(candidate, policy))
    if status_index(status, policy) >= status_index("SHADOW_RUNNING", policy):
        issues.extend(validate_shadow(candidate, policy, require_complete=status_index(status, policy) >= status_index("READY_FOR_MANUAL_APPROVAL", policy)))
    if status_index(status, policy) >= status_index("APPROVED_FOR_PRODUCTION_PR", policy):
        issues.extend(validate_approval(candidate, approvals, policy))
        issues.extend(validate_production_pr(candidate, policy))
    if status_index(status, policy) >= status_index("RELEASED", policy):
        issues.extend(validate_release(candidate))
    if status == "POST_RELEASE_AUDIT_COMPLETE":
        issues.extend(validate_post_release(candidate, policy))
    if status == "ROLLED_BACK":
        issues.extend(validate_release(candidate))
        rollback = candidate.get("rollback", {}) or {}
        if parse_timestamp(rollback.get("executed_at_utc")) is None:
            issues.append("rollback.executed_at_utc is required for ROLLED_BACK")
        if not valid_sha256(rollback.get("rollback_commit_sha")):
            issues.append("rollback.rollback_commit_sha must be a SHA-256 hex string")
    return sorted(set(issues))


def validate_candidate_registry(
    registry: dict[str, Any],
    policy: dict[str, Any],
    approvals: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if int(registry.get("schema_version", 0)) != 1:
        issues.append({"release_id": "", "issue": "candidate registry schema_version must be 1"})
    if registry.get("policy_id") != policy["policy"]["id"]:
        issues.append({"release_id": "", "issue": "candidate registry policy_id mismatch"})
    candidates = registry.get("candidates", [])
    if not isinstance(candidates, list):
        return issues + [{"release_id": "", "issue": "candidates must be a list"}]
    seen: set[str] = set()
    proposed_seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            issues.append({"release_id": "", "issue": "candidate entries must be mappings"})
            continue
        release_id = optional_text(candidate.get("release_id"))
        if release_id in seen:
            issues.append({"release_id": release_id, "issue": "duplicate release_id"})
        seen.add(release_id)
        proposed = optional_text(candidate.get("proposed_strategy_fingerprint"))
        if proposed and proposed in proposed_seen and candidate.get("status") not in {"REJECTED", "ROLLED_BACK"}:
            issues.append({"release_id": release_id, "issue": "duplicate active proposed fingerprint"})
        proposed_seen.add(proposed)
        for issue in validate_candidate(candidate, policy, approvals):
            issues.append({"release_id": release_id, "issue": issue})
    return issues


def candidate_readiness(candidate: dict[str, Any], policy: dict[str, Any], approvals: dict[str, Any]) -> dict[str, Any]:
    status = optional_text(candidate.get("status"))
    issues = validate_candidate(candidate, policy, approvals)
    return {
        "release_id": optional_text(candidate.get("release_id")),
        "title": optional_text(candidate.get("title")),
        "change_type": optional_text(candidate.get("change_type")),
        "status": status,
        "current_strategy_fingerprint": optional_text(candidate.get("current_strategy_fingerprint")),
        "proposed_strategy_fingerprint": optional_text(candidate.get("proposed_strategy_fingerprint")),
        "research_pr_number": int(candidate.get("research_pr_number", 0) or 0),
        "production_pr_number": int((candidate.get("production_pr", {}) or {}).get("pr_number", 0) or 0),
        "validation_passed": not issues,
        "production_pr_ready": bool(status == "APPROVED_FOR_PRODUCTION_PR" and not issues),
        "issues": issues,
        "automatic_activation": False,
    }


def build_report(
    policy: dict[str, Any],
    registry: dict[str, Any],
    approvals: dict[str, Any],
    repository_fingerprint: str,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    registry_issues = validate_candidate_registry(registry, policy, approvals)
    candidates = [
        candidate_readiness(candidate, policy, approvals)
        for candidate in registry.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    substantive = {
        "gate_version": GATE_VERSION,
        "policy_id": policy["policy"]["id"],
        "repository_release_surface_fingerprint": repository_fingerprint,
        "candidate_count": len(candidates),
        "active_candidate_count": sum(item["status"] not in {"REJECTED", "ROLLED_BACK", "POST_RELEASE_AUDIT_COMPLETE"} for item in candidates),
        "registry_valid": not registry_issues,
        "registry_issues": registry_issues,
        "candidates": candidates,
        "automatic_activation": False,
        "automatic_merge": False,
        "automatic_strategy_change": False,
        "automatic_priority_rule_change": False,
        "production_state_mutations": [],
        "manual_review_required": True,
        "research_only": True,
    }
    payload = {
        **substantive,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gate_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def validate_report(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("gate_version") != GATE_VERSION:
        issues.append("invalid gate_version")
    for key in ("automatic_activation", "automatic_merge", "automatic_strategy_change", "automatic_priority_rule_change"):
        if payload.get(key) is not False:
            issues.append(f"{key} must be false")
    if payload.get("production_state_mutations") != []:
        issues.append("production_state_mutations must be empty")
    if payload.get("manual_review_required") is not True:
        issues.append("manual review must be required")
    status_copy = dict(payload)
    supplied_status_hash = status_copy.pop("status_sha256", "")
    if supplied_status_hash != canonical_hash(status_copy):
        issues.append("status_sha256 mismatch")
    substantive = dict(status_copy)
    substantive.pop("generated_at_utc", None)
    supplied_gate_hash = substantive.pop("gate_fingerprint", "")
    if supplied_gate_hash != canonical_hash(substantive):
        issues.append("gate_fingerprint mismatch")
    return issues


def pr_gate(
    base_ref: str,
    head_root: str | Path,
    pr_number: int,
    policy: dict[str, Any],
    registry: dict[str, Any],
    approvals: dict[str, Any],
    head_ref: str = "HEAD",
) -> dict[str, Any]:
    base = release_surface_fingerprint_git(base_ref, policy)
    head = release_surface_fingerprint(head_root, policy)
    changed = base["sha256"] != head["sha256"]
    changed_files = git_changed_files(base_ref, head_ref)
    matching: list[dict[str, Any]] = []
    if changed:
        for candidate in registry.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            production_pr = candidate.get("production_pr", {}) or {}
            if (
                candidate.get("current_strategy_fingerprint") == base["sha256"]
                and candidate.get("proposed_strategy_fingerprint") == head["sha256"]
                and candidate.get("status") == policy["production_pr"]["candidate_status_required"]
                and int(production_pr.get("pr_number", 0) or 0) == int(pr_number)
            ):
                readiness = candidate_readiness(candidate, policy, approvals)
                if readiness["production_pr_ready"]:
                    matching.append(readiness)
    passed = (not changed) or len(matching) == 1
    reasons: list[str] = []
    if changed and not matching:
        reasons.append("release surface changed without one exact APPROVED_FOR_PRODUCTION_PR candidate")
    if changed and len(matching) > 1:
        reasons.append("multiple approved release candidates match the same production PR")
    return {
        "gate_version": GATE_VERSION,
        "pr_number": int(pr_number),
        "base_ref": base_ref,
        "base_release_surface_fingerprint": base["sha256"],
        "head_release_surface_fingerprint": head["sha256"],
        "release_surface_changed": changed,
        "changed_files": changed_files,
        "matching_release_candidates": matching,
        "passed": passed,
        "reasons": reasons,
        "automatic_activation": False,
        "automatic_merge": False,
        "manual_review_required": True,
    }


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Release Governance",
        "",
        f"- Registry valid: **{report['registry_valid']}**",
        f"- Candidates: **{report['candidate_count']}**",
        f"- Active candidates: **{report['active_candidate_count']}**",
        f"- Repository release-surface fingerprint: `{report['repository_release_surface_fingerprint']}`",
        f"- Generated: `{report['generated_at_utc']}`",
        "- Automatic activation: **False**",
        "- Automatic merge: **False**",
        "",
        "## Candidates",
        "",
        "| Release | Type | Status | Research PR | Production PR | Valid | Production-ready |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for candidate in report["candidates"]:
        lines.append(
            f"| {candidate['release_id']} | {candidate['change_type']} | {candidate['status']} | "
            f"{candidate['research_pr_number']} | {candidate['production_pr_number']} | "
            f"{candidate['validation_passed']} | {candidate['production_pr_ready']} |"
        )
        for issue in candidate["issues"]:
            lines.append(f"  - `{candidate['release_id']}`: {issue}")
    if not report["candidates"]:
        lines.append("| _none_ | - | - | - | - | - | - |")
    if report["registry_issues"]:
        lines.extend(["", "## Registry issues", ""])
        lines.extend(f"- `{item['release_id']}`: {item['issue']}" for item in report["registry_issues"])
    lines.extend([
        "",
        "## Enforcement",
        "",
        "A release-surface change in a pull request is blocked unless exactly one candidate matches the base and head fingerprints, has status `APPROVED_FOR_PRODUCTION_PR`, references the current production PR, passes at least 20 shadow market sessions, has an exact human approval, and includes a rollback plan.",
        "",
        "The gate never merges, activates, trades, or mutates production state automatically.",
        "",
    ])
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "strategy_release_gate.json"
    markdown_path = target / "strategy_release_gate.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(report_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and enforce strategy release governance")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--policy", default=POLICY_PATH)
        command.add_argument("--candidates", default=CANDIDATES_PATH)
        command.add_argument("--approvals", default=APPROVALS_PATH)

    validate = subparsers.add_parser("validate")
    common(validate)
    validate.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)

    gate = subparsers.add_parser("pr-gate")
    common(gate)
    gate.add_argument("--base-ref", required=True)
    gate.add_argument("--head-root", default=".")
    gate.add_argument("--head-ref", default="HEAD")
    gate.add_argument("--pr-number", required=True, type=int)
    gate.add_argument("--output", default="")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    policy = load_policy(args.policy)
    registry = load_candidates(args.candidates)
    approvals = load_approvals(args.approvals)
    registry_issues = validate_candidate_registry(registry, policy, approvals)
    if args.command == "validate":
        fingerprint = release_surface_fingerprint(".", policy)["sha256"]
        report = build_report(policy, registry, approvals, fingerprint)
        report_issues = validate_report(report)
        paths = write_report(report, args.output_dir)
        result = {"valid": not registry_issues and not report_issues, "registry_issues": registry_issues, "report_issues": report_issues, "outputs": paths, "report": report}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["valid"] else 1
    result = pr_gate(
        args.base_ref,
        args.head_root,
        args.pr_number,
        policy,
        registry,
        approvals,
        args.head_ref,
    )
    if registry_issues:
        result["passed"] = False
        result["reasons"].append("candidate registry validation failed")
        result["registry_issues"] = registry_issues
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
