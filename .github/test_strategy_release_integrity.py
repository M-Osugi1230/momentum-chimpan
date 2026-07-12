from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import strategy_release_integrity as integrity


def set_nested(mapping: dict, path: str, value: object) -> None:
    current = mapping
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def policy_fixture() -> dict:
    policy = {
        "schema_version": 1,
        "release_surface": copy.deepcopy(integrity.EXPECTED_RELEASE_SURFACE),
        "candidate_registry": {
            "immutable_registration_fields": list(integrity.EXPECTED_IMMUTABLE_FIELDS),
            "production_state_mutations": [],
        },
    }
    for path, value in integrity.CRITICAL_POLICY_EXPECTATIONS.items():
        set_nested(policy, path, value)
    return policy


def candidate_fixture(release_id: str = "release-v1", status: str = "REGISTERED_RESEARCH") -> dict:
    candidate = {
        "release_id": release_id,
        "registered_at_utc": "2026-07-13T00:00:00+00:00",
        "change_type": "SCORE_WEIGHT",
        "hypothesis": "A bounded change improves prospective prioritization.",
        "expected_mechanism": "Improved separation of high-quality candidates.",
        "primary_metric": "10-session TOPIX excess",
        "acceptance_criteria": {"minimum_excess": 0.0},
        "acceptance_criteria_sha256": "a" * 64,
        "failure_conditions": ["material harm"],
        "current_strategy_fingerprint": "b" * 64,
        "proposed_strategy_fingerprint": "c" * 64,
        "research_pr_number": 100,
        "registration": {
            "gate_changed_after_results": False,
            "favorable_subperiod_only": False,
        },
        "status": status,
        "status_history": [
            {"status": "REGISTERED_RESEARCH", "at_utc": "2026-07-13T00:00:00+00:00"}
        ],
    }
    if status in {"RELEASED", "POST_RELEASE_AUDIT_COMPLETE", "ROLLED_BACK"}:
        oid = "d" * 40
        candidate["release"] = {
            "merge_commit_oid": oid,
            "merge_commit_sha": integrity.oid_digest(oid),
        }
    if status == "ROLLED_BACK":
        oid = "e" * 40
        candidate["rollback"] = {
            "rollback_commit_oid": oid,
            "rollback_commit_sha": integrity.oid_digest(oid),
        }
    return candidate


def approval_fixture(approval_id: str = "approval-v1") -> dict:
    return {
        "approval_id": approval_id,
        "decision": "APPROVE",
        "scope": "MANUAL_REVIEW_ONLY",
        "strategy_fingerprint": "c" * 64,
        "evidence_status_sha256": "d" * 64,
        "review_packet_sha256": "e" * 64,
    }


def approvals_fixture(entries: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "policy": {
            "automatic_activation": False,
            "require_exact_strategy_fingerprint": True,
            "require_exact_evidence_status_sha256": True,
            "require_exact_review_packet_sha256": True,
            "allowed_scope": "MANUAL_REVIEW_ONLY",
        },
        "approvals": entries or [],
    }


def write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def main() -> None:
    policy = policy_fixture()
    assert integrity.validate_policy_contract(policy) == []

    weakened = copy.deepcopy(policy)
    weakened["release_surface"]["additional_python_ast"].pop("data_quality.py")
    assert any("release_surface" in issue for issue in integrity.validate_policy_contract(weakened))

    lowered = copy.deepcopy(policy)
    lowered["shadow"]["minimum_distinct_market_sessions"] = 19
    assert any("minimum_distinct_market_sessions" in issue for issue in integrity.validate_policy_contract(lowered))

    duplicate_candidates = {"candidates": [candidate_fixture(), candidate_fixture()]}
    issues = integrity.validate_registries(duplicate_candidates, approvals_fixture())
    assert any("duplicate release_id" in issue for issue in issues)

    duplicate_approvals = approvals_fixture([approval_fixture(), approval_fixture()])
    issues = integrity.validate_registries({"candidates": []}, duplicate_approvals)
    assert any("duplicate approval_id" in issue for issue in issues)

    base_candidate = candidate_fixture()
    base_candidates = {"candidates": [base_candidate]}
    head_candidates = copy.deepcopy(base_candidates)
    head_candidates["candidates"][0]["status"] = "EVIDENCE_READY"
    head_candidates["candidates"][0]["status_history"].append(
        {"status": "EVIDENCE_READY", "at_utc": "2026-08-01T00:00:00+00:00"}
    )
    assert integrity.compare_append_only(
        base_candidates,
        head_candidates,
        approvals_fixture(),
        approvals_fixture(),
        False,
    ) == []

    mutated = copy.deepcopy(head_candidates)
    mutated["candidates"][0]["hypothesis"] = "Changed after results"
    issues = integrity.compare_append_only(
        base_candidates,
        mutated,
        approvals_fixture(),
        approvals_fixture(),
        False,
    )
    assert any("immutable registration fields changed" in issue for issue in issues)

    rewritten_history = copy.deepcopy(head_candidates)
    rewritten_history["candidates"][0]["status_history"][0]["at_utc"] = "2026-07-14T00:00:00+00:00"
    issues = integrity.compare_append_only(
        base_candidates,
        rewritten_history,
        approvals_fixture(),
        approvals_fixture(),
        False,
    )
    assert any("status_history is not append-only" in issue for issue in issues)

    issues = integrity.compare_append_only(
        base_candidates,
        {"candidates": []},
        approvals_fixture(),
        approvals_fixture(),
        False,
    )
    assert any("was deleted" in issue for issue in issues)

    new_candidate = {"candidates": [candidate_fixture("release-new")]}
    assert integrity.compare_append_only(
        {"candidates": []},
        new_candidate,
        approvals_fixture(),
        approvals_fixture(),
        False,
    ) == []
    issues = integrity.compare_append_only(
        {"candidates": []},
        new_candidate,
        approvals_fixture(),
        approvals_fixture(),
        True,
    )
    assert any("cannot introduce its authorizing candidate" in issue for issue in issues)

    base_approvals = approvals_fixture([approval_fixture()])
    changed_approvals = approvals_fixture([approval_fixture()])
    changed_approvals["approvals"][0]["strategy_fingerprint"] = "f" * 64
    issues = integrity.compare_append_only(
        {"candidates": []},
        {"candidates": []},
        base_approvals,
        changed_approvals,
        False,
    )
    assert any("changed after being recorded" in issue for issue in issues)

    released = candidate_fixture(status="RELEASED")
    assert integrity.validate_object_id_bindings(released) == []
    released["release"]["merge_commit_sha"] = "0" * 64
    assert any("SHA-256" in issue for issue in integrity.validate_object_id_bindings(released))

    released_64 = candidate_fixture(status="RELEASED")
    oid_64 = "a" * 64
    released_64["release"] = {
        "merge_commit_oid": oid_64,
        "merge_commit_sha": integrity.oid_digest(oid_64),
    }
    assert integrity.validate_object_id_bindings(released_64) == []

    with TemporaryDirectory() as directory:
        root = Path(directory)
        outside = root / "outside.yaml"
        outside.write_text("secret: true\n", encoding="utf-8")
        linked = root / "linked.yaml"
        linked.symlink_to(outside)
        assert integrity.root_reader(root)("linked.yaml") == ""

        write_yaml(root / integrity.POLICY_PATH, policy)
        write_yaml(root / integrity.CANDIDATES_PATH, {"schema_version": 1, "candidates": []})
        write_yaml(root / integrity.APPROVALS_PATH, approvals_fixture())
        report = integrity.validate_current(root)
        assert report["passed"] is True
        assert report["automatic_activation"] is False
        assert report["production_state_mutations"] == []
        supplied = report["status_sha256"]
        copy_report = dict(report)
        copy_report.pop("status_sha256")
        copy_report.pop("passed")
        assert supplied == integrity.canonical_hash(copy_report)

    print(json.dumps({"strategy_release_integrity_tests": "PASS"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
