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

import strategy_release_gate as gate


POLICY = {
    "policy_version": "2026-07-13-strategy-release-gate-v1",
    "execution_mode": "RESEARCH_AND_PAPER_ONLY",
    "automatic_activation": False,
    "manual_human_decision_required": True,
    "separate_production_change_pr_required": True,
    "minimum_shadow_market_sessions": 20,
    "protected_surface": {
        "strategy_python": "main.py",
        "strategy_config": "config.yaml",
        "exact_files": [
            "research/daily_research_focus_policy.yaml",
            "research/data_quality_policy.yaml",
        ],
    },
    "covered_change_types": [
        "SCORE_WEIGHT",
        "SCORE_COMPONENT",
        "FILTER",
        "EXIT",
        "PRIORITY_RULE",
        "PRODUCTION_ELIGIBILITY",
        "EXECUTION_ASSUMPTION",
    ],
    "required_evidence": sorted(gate.EXPECTED_EVIDENCE),
    "required_process": list(gate.EXPECTED_REQUIRED_PROCESS),
    "prohibited": sorted(gate.EXPECTED_PROHIBITED),
    "candidate_registry": gate.REGISTRY_PATH,
    "approval_registry": gate.APPROVALS_PATH,
}


def write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def build_root(root: Path, score_points: int = 10, focus_limit: int = 5) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text(
        "PAPER_MAX_POSITIONS = 5\n"
        f"def score_candidate(value):\n    return value + {score_points}\n"
        "def plain_summary():\n    return 'display only'\n",
        encoding="utf-8",
    )
    write_yaml(
        root / "config.yaml",
        {
            "market": {"minimum_trading_value": 100000000},
            "ranking": {"top_n": 100},
            "signals": {"minimum_score": 60},
            "presentation": {"email_top_n": 30},
        },
    )
    write_yaml(
        root / "research/daily_research_focus_policy.yaml",
        {
            "limits": {"maximum_A_candidates": focus_limit},
            "governance": {"automatic_strategy_change": False},
        },
    )
    write_yaml(
        root / "research/data_quality_policy.yaml",
        {
            "grades": {"C": {"eligible_for_priority_A": False}},
            "governance": {"automatic_strategy_change": False},
        },
    )
    write_yaml(root / gate.POLICY_PATH, POLICY)
    write_yaml(
        root / gate.REGISTRY_PATH,
        {
            "schema_version": 1,
            "policy_version": POLICY["policy_version"],
            "automatic_activation": False,
            "candidates": [],
        },
    )
    write_yaml(
        root / gate.APPROVALS_PATH,
        {
            "schema_version": 1,
            "policy": {"automatic_activation": False},
            "approvals": [],
        },
    )


def approved_candidate(base_fingerprint: str, proposed_fingerprint: str) -> dict:
    return {
        "candidate_id": "score-change-v1",
        "change_type": "SCORE_WEIGHT",
        "status": "APPROVED",
        "registered_at": "2026-07-13",
        "registered_before_results": True,
        "hypothesis": "A bounded score change improves prospective research prioritization.",
        "acceptance_criteria": [
            "positive market excess",
            "confidence interval excludes material harm",
        ],
        "change_summary": "Change one governed score contribution.",
        "research_pr": "101",
        "production_change_pr": "202",
        "current_release_fingerprint": base_fingerprint,
        "proposed_release_fingerprint": proposed_fingerprint,
        "rollback_plan": "Revert PR 202 and restore the prior fingerprint.",
        "automatic_activation": False,
        "evidence": {
            "complete": True,
            "origin": "PROSPECTIVE_LIVE",
            "discovery_holdout": {"separated": True, "overlap_count": 0},
            "execution": {
                "no_lookahead": True,
                "same_day_close_entry_allowed": False,
                "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
            },
            "transaction_costs": {"sensitivity_tested": True},
            "stability": {
                "early_period_tested": True,
                "late_period_tested": True,
                "market_regimes_tested": True,
            },
            "statistics": {
                "sample_size": 100,
                "confidence_interval_available": True,
            },
            "review_packet": {
                "artifact": "actions://review-packet/1",
                "sha256": "a" * 64,
            },
        },
        "shadow_comparison": {
            "market_sessions": 20,
            "status": "PASS",
            "artifact": "actions://shadow/1",
            "sha256": "b" * 64,
        },
        "manual_decision": {
            "approval_id": "approval-score-change-v1",
            "decision": "APPROVED",
            "decided_by": "repository-owner",
            "decided_at": "2026-08-20",
            "record": "research/strategy_approvals.yaml#score-change-v1",
            "record_sha256": "c" * 64,
        },
    }


def approval_record(candidate: dict) -> dict:
    return {
        "approval_id": candidate["manual_decision"]["approval_id"],
        "candidate_id": candidate["candidate_id"],
        "decision": "APPROVED",
        "approved": True,
        "approved_by": candidate["manual_decision"]["decided_by"],
        "approved_at": candidate["manual_decision"]["decided_at"],
        "current_release_fingerprint": candidate["current_release_fingerprint"],
        "proposed_release_fingerprint": candidate["proposed_release_fingerprint"],
        "evidence_review_packet_sha256": candidate["evidence"]["review_packet"]["sha256"],
        "shadow_comparison_sha256": candidate["shadow_comparison"]["sha256"],
        "production_change_pr": candidate["production_change_pr"],
        "automatic_activation": False,
    }


def main() -> None:
    assert gate.validate_policy(POLICY) == []

    with TemporaryDirectory() as directory:
        root = Path(directory)
        base = root / "base"
        current = root / "current"
        build_root(base)
        build_root(current)

        current_validation = gate.validate_current(current)
        assert current_validation["passed"] is True
        assert current_validation["candidate_count"] == 0

        policy = gate.load_yaml_mapping(current / gate.POLICY_PATH)
        registry = gate.load_yaml_mapping(current / gate.REGISTRY_PATH)
        approvals = gate.load_yaml_mapping(current / gate.APPROVALS_PATH)

        unchanged = gate.evaluate_change(base, current, policy, registry, approvals)
        assert unchanged["passed"] is True
        assert unchanged["protected_strategy_changed"] is False

        build_root(current, score_points=15)
        policy = gate.load_yaml_mapping(current / gate.POLICY_PATH)
        registry = gate.load_yaml_mapping(current / gate.REGISTRY_PATH)
        blocked = gate.evaluate_change(base, current, policy, registry, approvals)
        assert blocked["passed"] is False
        assert blocked["protected_strategy_changed"] is True
        assert blocked["authorizing_candidate_id"] == ""
        assert any("without exactly one APPROVED/RELEASED candidate" in item for item in blocked["issues"])

        base_fingerprint = gate.release_surface(base, policy)["release_fingerprint"]
        proposed_fingerprint = gate.release_surface(current, policy)["release_fingerprint"]
        candidate = approved_candidate(base_fingerprint, proposed_fingerprint)
        registry["candidates"] = [candidate]
        approvals["approvals"] = [approval_record(candidate)]
        approved = gate.evaluate_change(base, current, policy, registry, approvals)
        assert approved["passed"] is True
        assert approved["authorizing_candidate_id"] == "score-change-v1"

        insufficient_shadow = copy.deepcopy(registry)
        insufficient_shadow["candidates"][0]["shadow_comparison"]["market_sessions"] = 19
        result = gate.evaluate_change(base, current, policy, insufficient_shadow, approvals)
        assert result["passed"] is False
        assert any("at least 20 market sessions" in item for item in result["issues"])

        automatic = copy.deepcopy(registry)
        automatic["candidates"][0]["automatic_activation"] = True
        result = gate.evaluate_change(base, current, policy, automatic, approvals)
        assert result["passed"] is False
        assert any("automatic_activation must be false" in item for item in result["issues"])

        build_root(current, score_points=10, focus_limit=4)
        policy = gate.load_yaml_mapping(current / gate.POLICY_PATH)
        registry = gate.load_yaml_mapping(current / gate.REGISTRY_PATH)
        approvals = gate.load_yaml_mapping(current / gate.APPROVALS_PATH)
        focus_change = gate.evaluate_change(base, current, policy, registry, approvals)
        assert focus_change["passed"] is False
        assert focus_change["protected_strategy_changed"] is True

        invalid_policy = copy.deepcopy(POLICY)
        invalid_policy["minimum_shadow_market_sessions"] = 19
        assert any("at least 20" in item for item in gate.validate_policy(invalid_policy))

    print(json.dumps({"strategy_release_gate_tests": "PASS"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
