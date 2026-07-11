from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import sys

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import strategy_governance as governance


BASE_SOURCE = '''
PAPER_MAX_POSITIONS = 10
APP_VERSION = "ignored"

def score(metrics, minimum):
    return 10 if metrics["value"] >= minimum else 0

def calculate_market_regime(rows):
    return "strong" if len(rows) >= 5 else "weak"

def html_score_section(rows):
    return "old presentation"

def unrelated_operational_helper():
    return 1
'''

STRATEGY_CHANGED_SOURCE = BASE_SOURCE.replace(">= minimum", "> minimum")
PRESENTATION_CHANGED_SOURCE = BASE_SOURCE.replace("old presentation", "new presentation")
CONFIG = {
    "market": {"min_trading_value": 100000000},
    "ranking": {"buy_candidate_limit": 100},
    "signals": {"stop_loss": 0.08},
    "data": {"output_path": "ignored.xlsx"},
}

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    base_main = root / "base_main.py"
    current_main = root / "current_main.py"
    base_config = root / "base_config.yaml"
    current_config = root / "current_config.yaml"
    base_main.write_text(BASE_SOURCE, encoding="utf-8")
    current_main.write_text(BASE_SOURCE, encoding="utf-8")
    base_config.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    current_config.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")

    first = governance.strategy_fingerprint(str(base_main), str(base_config))
    second = governance.strategy_fingerprint(str(base_main), str(base_config))
    assert first["sha256"] == second["sha256"]
    assert first["selected_function_count"] == 2
    assert first["selected_constant_count"] == 1
    assert "data" not in first["config_sections"]

    current_main.write_text(PRESENTATION_CHANGED_SOURCE, encoding="utf-8")
    presentation = governance.strategy_fingerprint(str(current_main), str(current_config))
    assert presentation["sha256"] == first["sha256"], "presentation changes must not alter strategy fingerprint"

    current_main.write_text(STRATEGY_CHANGED_SOURCE, encoding="utf-8")
    changed = governance.strategy_fingerprint(str(current_main), str(current_config))
    assert changed["sha256"] != first["sha256"]

    empty_registry = {
        "schema_version": 1,
        "policy": {},
        "experiments": [],
    }
    rejected = governance.ci_strategy_change_check(
        str(base_main), str(current_main), str(base_config), str(current_config), empty_registry
    )
    assert rejected["strategy_changed"] is True
    assert rejected["passed"] is False

    candidate = {
        "experiment_id": "exp-score-threshold",
        "experiment_type": "candidate",
        "status": "proposed",
        "hypothesis": "A strict comparison improves excess return.",
        "strategy_fingerprint": changed["sha256"],
        "change_summary": "Change score comparison from >= to >.",
        "evidence_scope": {"group_type": "overall", "group_value": "all", "horizon_days": 10},
        "manual_approval": {"approved": False, "approved_by": "", "approved_at": ""},
    }
    registered = {"schema_version": 1, "policy": {}, "experiments": [candidate]}
    accepted = governance.ci_strategy_change_check(
        str(base_main), str(current_main), str(base_config), str(current_config), registered
    )
    assert accepted["passed"] is True
    assert accepted["matching_experiments"] == ["exp-score-threshold"]

    no_change = governance.ci_strategy_change_check(
        str(base_main), str(base_main), str(base_config), str(base_config), empty_registry
    )
    assert no_change["strategy_changed"] is False
    assert no_change["passed"] is True

policy = {
    "minimum_outcome_count": 100,
    "required_robustness_status": "ROBUST",
    "maximum_fdr_q_value": 0.05,
    "require_positive_early_period": True,
    "require_positive_late_period": True,
    "require_positive_leave_one_sector": True,
    "require_manual_approval": True,
}
robust_evidence = {
    "count": 120,
    "robustness_status": "ROBUST",
    "fdr_q_value": 0.03,
    "early_net_average_excess": 0.01,
    "late_net_average_excess": 0.012,
    "worst_leave_one_sector_excess": 0.008,
}
weak_evidence = dict(robust_evidence, count=20, robustness_status="INSUFFICIENT")
assert governance.evidence_eligibility(candidate, policy, robust_evidence)["eligible"] is True
assert governance.evidence_eligibility(candidate, policy, weak_evidence)["eligible"] is False

registry = {
    "schema_version": 1,
    "policy": policy,
    "experiments": [candidate],
}
assert governance.validate_registry(registry) == []
duplicate_registry = copy.deepcopy(registry)
duplicate_registry["experiments"].append(copy.deepcopy(candidate))
assert any(issue["issue"] == "duplicate experiment_id" for issue in governance.validate_registry(duplicate_registry))

fingerprint = candidate["strategy_fingerprint"]
robustness = pd.DataFrame([{
    "group_type": "overall",
    "group_value": "all",
    "horizon_days": 10,
    **robust_evidence,
}])
promoted_without_approval = copy.deepcopy(candidate)
promoted_without_approval["status"] = "promoted"
audit, issues = governance.audit_registry(
    {"policy": policy, "experiments": [promoted_without_approval]}, fingerprint, robustness
)
assert not issues.empty
assert audit.iloc[0]["promotion_valid"] == False

promoted = copy.deepcopy(promoted_without_approval)
promoted["manual_approval"] = {
    "approved": True,
    "approved_by": "repository-owner",
    "approved_at": "2026-07-11",
}
audit, issues = governance.audit_registry(
    {"policy": policy, "experiments": [promoted]}, fingerprint, robustness
)
assert issues.empty
assert bool(audit.iloc[0]["promotion_valid"])
assert bool(audit.iloc[0]["evidence_eligible"])

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    main_path = root / "main.py"
    config_path = root / "config.yaml"
    output_path = root / "fingerprint.json"
    main_path.write_text(BASE_SOURCE, encoding="utf-8")
    config_path.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    snapshot = governance.write_snapshot(str(output_path), str(main_path), str(config_path))
    assert output_path.exists()
    assert snapshot["research_only"] is True
    assert snapshot["execution_mode"] == "RESEARCH_AND_PAPER_ONLY"

print("strategy experiment governance validation passed")
