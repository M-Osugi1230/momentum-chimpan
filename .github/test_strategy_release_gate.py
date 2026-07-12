from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import strategy_release_gate as gate


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def make_surface(root: Path, score_points: int = 20, quality_threshold: float = 0.45, a_limit: int = 5) -> None:
    write(
        root / "main.py",
        f'''PAPER_INITIAL_CAPITAL = 1000000\n\ndef momentum_score(value):\n    return value + {score_points}\n\ndef priority_values(value):\n    return value\n\ndef plain_section(value):\n    return str(value)\n''',
    )
    write_yaml(
        root / "config.yaml",
        {
            "market": {"min_trading_value": 100000000},
            "ranking": {"email_top_n": 30},
            "signals": {"score_points": score_points},
            "display": {"title": "ignored"},
        },
    )
    write(
        root / "data_quality.py",
        f'''def evaluate_row(row, minimum_trading_value, policy):\n    return "C" if row > {quality_threshold} else "A"\n\ndef apply_priority_gate(action, top100):\n    return action\n\ndef helper_text(value):\n    return str(value)\n''',
    )
    write(
        root / "daily_research_focus.py",
        f'''def base_bucket(row, policy):\n    return "A" if row < {a_limit} else "B"\n\ndef attach_daily_focus(action, top100):\n    return action\n\ndef html_section(value):\n    return str(value)\n''',
    )
    write_yaml(
        root / "research/data_quality_policy.yaml",
        {
            "thresholds": {"corporate_action_absolute_daily_return": quality_threshold},
            "grades": {"A": {"eligible_for_priority_A": True}, "C": {"eligible_for_priority_A": False}},
            "priority_boundary": {"grade_C_max_priority": "B"},
            "documentation": {"ignored": True},
        },
    )
    write_yaml(
        root / "research/daily_research_focus_policy.yaml",
        {
            "limits": {"maximum_A_candidates": a_limit, "maximum_daily_action_list": 10},
            "bucket_mapping": {"A": "A", "B": "B"},
            "watch_rules": {"C_minimum_action_score": 55},
            "governance": {
                "preserve_momentum_score": True,
                "preserve_momentum_rank": True,
                "preserve_paper_execution": True,
                "ignored": "documentation",
            },
        },
    )


def approval_registry(proposed: str, evidence_hash: str = HASH_B, packet_hash: str = HASH_C) -> dict:
    return {
        "schema_version": 1,
        "policy": {
            "automatic_activation": False,
            "require_exact_strategy_fingerprint": True,
            "require_exact_evidence_status_sha256": True,
            "require_exact_review_packet_sha256": True,
            "allowed_scope": "MANUAL_REVIEW_ONLY",
        },
        "approvals": [
            {
                "approval_id": "approval-valid-001",
                "decision": "APPROVE",
                "strategy_fingerprint": proposed,
                "evidence_status_sha256": evidence_hash,
                "review_packet_sha256": packet_hash,
                "reviewer": "repository-owner",
                "approved_at_utc": "2026-09-02T00:00:00+00:00",
                "scope": "MANUAL_REVIEW_ONLY",
            }
        ],
    }


def valid_candidate(current: str, proposed: str, production_pr: int = 777) -> dict:
    criteria = {
        "minimum_outcome_count": 100,
        "minimum_distinct_signal_dates": 20,
        "mean_market_excess_positive": True,
        "bootstrap_ci_lower_positive": True,
        "early_and_late_positive": True,
        "all_required_regimes_non_negative": True,
    }
    candidate = {
        "release_id": "release-score-weight-001",
        "title": "Governed score weight update",
        "change_type": "SCORE_WEIGHT",
        "status": "APPROVED_FOR_PRODUCTION_PR",
        "registered_at_utc": "2026-07-01T00:00:00+00:00",
        "acceptance_criteria_frozen_at_utc": "2026-07-01T01:00:00+00:00",
        "hypothesis": "The proposed score allocation improves prospective market excess without worsening risk.",
        "expected_mechanism": "The revised allocation reduces noisy signals while preserving persistent momentum.",
        "primary_metric": "20-session mean TOPIX excess return",
        "failure_conditions": [
            "non-positive holdout excess",
            "negative late-period result",
            "unresolved shadow incident",
        ],
        "acceptance_criteria": criteria,
        "acceptance_criteria_sha256": gate.canonical_hash(criteria),
        "current_strategy_fingerprint": current,
        "proposed_strategy_fingerprint": proposed,
        "research_pr_number": 700,
        "registration": {
            "gate_changed_after_results": False,
            "favorable_subperiod_only": False,
        },
        "evidence": {
            "first_evidence_at_utc": "2026-07-02T00:00:00+00:00",
            "completed_at_utc": "2026-08-01T00:00:00+00:00",
            "discovery_holdout_separated": True,
            "prospective_or_shadow_evidence": True,
            "no_lookahead_verified": True,
            "transaction_cost_sensitivity": True,
            "early_late_stability": True,
            "regime_stability": True,
            "sector_or_concentration_stability": True,
            "sample_size_adequate": True,
            "confidence_interval_reported": True,
            "multiple_testing_control_applicable": True,
            "multiple_testing_control_applied": True,
            "evidence_origin_registered": True,
            "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
            "same_day_close_entry": False,
            "outcome_count": 180,
            "distinct_signal_dates": 30,
            "evidence_status_sha256": HASH_B,
            "evidence_packet_sha256": HASH_D,
            "review_packet_sha256": HASH_C,
        },
        "shadow": {
            "started_at_utc": "2026-08-02T00:00:00+00:00",
            "completed_at_utc": "2026-09-01T00:00:00+00:00",
            "distinct_market_sessions": 22,
            "current_and_proposed_run_in_parallel": True,
            "production_behavior_unchanged": True,
            "current_strategy_fingerprint": current,
            "proposed_strategy_fingerprint": proposed,
            "result_sha256": HASH_A,
            "unresolved_incident_count": 0,
            "acceptance_criteria_passed": True,
        },
        "approval": {"approval_id": "approval-valid-001"},
        "production_pr": {
            "pr_number": production_pr,
            "target_branch": "main",
        },
        "rollback": {
            "plan": "Restore the exact pre-release strategy surface and rerun the full validation suite.",
            "rollback_ref": "refs/tags/strategy-before-release-001",
            "owner": "repository-owner",
            "trigger_conditions": [
                "operational SLO breach",
                "unexpected drawdown or priority calibration deterioration",
            ],
            "automatic_rollback": False,
        },
        "status_history": [
            {"status": "REGISTERED_RESEARCH", "at_utc": "2026-07-01T00:00:00+00:00"},
            {"status": "EVIDENCE_READY", "at_utc": "2026-08-01T00:00:00+00:00"},
            {"status": "SHADOW_RUNNING", "at_utc": "2026-08-02T00:00:00+00:00"},
            {"status": "READY_FOR_MANUAL_APPROVAL", "at_utc": "2026-09-01T00:00:00+00:00"},
            {"status": "APPROVED_FOR_PRODUCTION_PR", "at_utc": "2026-09-02T00:00:00+00:00"},
        ],
    }
    return candidate


policy = gate.load_policy(ROOT / gate.POLICY_PATH)
assert gate.validate_policy(policy) == []
assert policy["shadow"]["minimum_distinct_market_sessions"] == 20
assert policy["production_pr"]["separate_from_research_pr"] is True
assert policy["policy"]["automatic_activation"] is False
assert policy["policy"]["automatic_merge"] is False

committed_registry = gate.load_candidates(ROOT / gate.CANDIDATES_PATH)
committed_approvals = gate.load_approvals(ROOT / gate.APPROVALS_PATH)
assert committed_registry["candidates"] == []
assert gate.validate_candidate_registry(committed_registry, policy, committed_approvals) == []
current_repo_fingerprint = gate.release_surface_fingerprint(ROOT, policy)["sha256"]
assert gate.valid_sha256(current_repo_fingerprint)
initial_report = gate.build_report(
    policy,
    committed_registry,
    committed_approvals,
    current_repo_fingerprint,
    generated_at_utc="2026-07-13T00:00:00+00:00",
)
assert initial_report["candidate_count"] == 0
assert initial_report["registry_valid"] is True
assert gate.validate_report(initial_report) == []
assert "_none_" in gate.report_markdown(initial_report)

with TemporaryDirectory() as temporary:
    base = Path(temporary) / "base"
    head = Path(temporary) / "head"
    make_surface(base, score_points=20, quality_threshold=0.45, a_limit=5)
    make_surface(head, score_points=22, quality_threshold=0.45, a_limit=5)
    base_fingerprint = gate.release_surface_fingerprint(base, policy)["sha256"]
    head_fingerprint = gate.release_surface_fingerprint(head, policy)["sha256"]
    assert base_fingerprint != head_fingerprint

    candidate = valid_candidate(base_fingerprint, head_fingerprint)
    approvals = approval_registry(head_fingerprint)
    assert gate.validate_candidate(candidate, policy, approvals) == []
    registry = {
        "schema_version": 1,
        "policy_id": "strategy-release-governance-v1",
        "candidates": [candidate],
    }
    assert gate.validate_candidate_registry(registry, policy, approvals) == []
    readiness = gate.candidate_readiness(candidate, policy, approvals)
    assert readiness["validation_passed"] is True
    assert readiness["production_pr_ready"] is True

    quality_changed = Path(temporary) / "quality"
    make_surface(quality_changed, score_points=20, quality_threshold=0.50, a_limit=5)
    assert gate.release_surface_fingerprint(quality_changed, policy)["sha256"] != base_fingerprint

    focus_changed = Path(temporary) / "focus"
    make_surface(focus_changed, score_points=20, quality_threshold=0.45, a_limit=4)
    assert gate.release_surface_fingerprint(focus_changed, policy)["sha256"] != base_fingerprint

    ignored_changed = Path(temporary) / "ignored"
    make_surface(ignored_changed, score_points=20, quality_threshold=0.45, a_limit=5)
    with (ignored_changed / "main.py").open("a", encoding="utf-8") as handle:
        handle.write("\ndef html_extra(value):\n    return f'<b>{value}</b>'\n")
    ignored_policy = yaml.safe_load(
        (ignored_changed / "research/data_quality_policy.yaml").read_text(encoding="utf-8")
    )
    ignored_policy["documentation"] = {"ignored": False, "text": "changed"}
    write_yaml(ignored_changed / "research/data_quality_policy.yaml", ignored_policy)
    assert gate.release_surface_fingerprint(ignored_changed, policy)["sha256"] == base_fingerprint

    nineteen = copy.deepcopy(candidate)
    nineteen["shadow"]["distinct_market_sessions"] = 19
    issues = gate.validate_candidate(nineteen, policy, approvals)
    assert any("at least 20" in issue for issue in issues)

    same_pr = copy.deepcopy(candidate)
    same_pr["production_pr"]["pr_number"] = same_pr["research_pr_number"]
    issues = gate.validate_candidate(same_pr, policy, approvals)
    assert "research and production PR numbers must differ" in issues

    missing_rollback = copy.deepcopy(candidate)
    missing_rollback["rollback"]["plan"] = ""
    issues = gate.validate_candidate(missing_rollback, policy, approvals)
    assert "rollback.plan is required" in issues

    changed_gate = copy.deepcopy(candidate)
    changed_gate["registration"]["gate_changed_after_results"] = True
    issues = gate.validate_candidate(changed_gate, policy, approvals)
    assert "post-result gate changes are prohibited" in issues

    cherry_picked = copy.deepcopy(candidate)
    cherry_picked["registration"]["favorable_subperiod_only"] = True
    issues = gate.validate_candidate(cherry_picked, policy, approvals)
    assert "favorable-subperiod-only evidence is prohibited" in issues

    approval_mismatch = approval_registry(head_fingerprint, evidence_hash=HASH_A)
    issues = gate.validate_candidate(candidate, policy, approval_mismatch)
    assert "approval evidence status hash mismatch" in issues

    no_holdout = copy.deepcopy(candidate)
    no_holdout["evidence"]["discovery_holdout_separated"] = False
    issues = gate.validate_candidate(no_holdout, policy, approvals)
    assert "evidence.discovery_holdout_separated must be true" in issues

    duplicate_registry = {
        "schema_version": 1,
        "policy_id": "strategy-release-governance-v1",
        "candidates": [candidate, copy.deepcopy(candidate)],
    }
    registry_issues = gate.validate_candidate_registry(duplicate_registry, policy, approvals)
    assert any(item["issue"] == "duplicate release_id" for item in registry_issues)

    released = copy.deepcopy(candidate)
    released["status"] = "RELEASED"
    released["release"] = {
        "merge_commit_sha": HASH_D,
        "released_at_utc": "2026-09-10T00:00:00+00:00",
        "production_pr_merged": True,
        "automatic_activation": False,
    }
    released["status_history"].append({"status": "RELEASED", "at_utc": "2026-09-10T00:00:00+00:00"})
    assert gate.validate_candidate(released, policy, approvals) == []

    audited = copy.deepcopy(released)
    audited["status"] = "POST_RELEASE_AUDIT_COMPLETE"
    audited["post_release_audit"] = {
        "market_sessions": 10,
        "operational_incidents_reviewed": True,
        "performance_and_risk_reviewed": True,
        "rollback_decision": "KEEP",
        "audit_sha256": HASH_A,
    }
    audited["status_history"].append({"status": "POST_RELEASE_AUDIT_COMPLETE", "at_utc": "2026-09-25T00:00:00+00:00"})
    assert gate.validate_candidate(audited, policy, approvals) == []

    short_audit = copy.deepcopy(audited)
    short_audit["post_release_audit"]["market_sessions"] = 9
    assert any("at least 10" in issue for issue in gate.validate_candidate(short_audit, policy, approvals))

with TemporaryDirectory() as temporary:
    repo = Path(temporary) / "repo"
    repo.mkdir()
    make_surface(repo, score_points=20, quality_threshold=0.45, a_limit=5)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    make_surface(repo, score_points=22, quality_threshold=0.45, a_limit=5)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "head"], cwd=repo, check=True, capture_output=True)

    previous = Path.cwd()
    os.chdir(repo)
    try:
        base_fp = gate.release_surface_fingerprint_git(base_sha, policy)["sha256"]
        head_fp = gate.release_surface_fingerprint(repo, policy)["sha256"]
        assert base_fp != head_fp

        empty = {"schema_version": 1, "policy_id": policy["policy"]["id"], "candidates": []}
        blocked = gate.pr_gate(base_sha, repo, 777, policy, empty, approval_registry(head_fp))
        assert blocked["release_surface_changed"] is True
        assert blocked["passed"] is False
        assert "without one exact" in blocked["reasons"][0]

        candidate = valid_candidate(base_fp, head_fp, production_pr=777)
        registry = {"schema_version": 1, "policy_id": policy["policy"]["id"], "candidates": [candidate]}
        allowed = gate.pr_gate(base_sha, repo, 777, policy, registry, approval_registry(head_fp))
        assert allowed["passed"] is True
        assert len(allowed["matching_release_candidates"]) == 1
        assert allowed["matching_release_candidates"][0]["release_id"] == candidate["release_id"]

        wrong_pr = gate.pr_gate(base_sha, repo, 778, policy, registry, approval_registry(head_fp))
        assert wrong_pr["passed"] is False

        head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
        unchanged = gate.pr_gate(head_sha, repo, 999, policy, empty, {"approvals": []})
        assert unchanged["release_surface_changed"] is False
        assert unchanged["passed"] is True
    finally:
        os.chdir(previous)

report = gate.build_report(
    policy,
    {"schema_version": 1, "policy_id": policy["policy"]["id"], "candidates": []},
    {"approvals": []},
    current_repo_fingerprint,
    generated_at_utc="2026-07-13T01:00:00+00:00",
)
tampered = copy.deepcopy(report)
tampered["automatic_activation"] = True
errors = gate.validate_report(tampered)
assert "automatic_activation must be false" in errors
assert "status_sha256 mismatch" in errors

workflow_text = (ROOT / ".github/workflows/strategy-release-governance.yml").read_text(encoding="utf-8")
workflow = yaml.safe_load(workflow_text)
assert "pull_request" in workflow[True]
assert workflow["permissions"]["contents"] == "read"
assert "strategy_release_gate.py pr-gate" in workflow_text
assert "github.event.pull_request.base.sha" in workflow_text
assert "github.event.pull_request.number" in workflow_text
assert ("git" + " push") not in workflow_text
assert ("contents:" + " write") not in workflow_text
assert ("EMAIL_" + "APP_PASSWORD") not in workflow_text
assert "actions/upload-artifact@v4" in workflow_text

print("strategy release governance validation passed")
