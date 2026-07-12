from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_evidence_catalog as evidence


catalog_path = ROOT / "research" / "evidence_catalog.yaml"
markdown_path = ROOT / "research" / "evidence_catalog.md"
catalog = evidence.load_catalog(catalog_path)

errors = evidence.validate_catalog(catalog, ROOT)
assert errors == [], errors
assert catalog["subject"]["current_production_weight_points"] == 15
assert catalog["subject"]["current_decision"] == evidence.HOLD_DECISION
assert catalog["subject"]["historical_consensus"] == evidence.CONFLICTED_CONSENSUS
assert catalog["precedence"][0] == "PROSPECTIVE_LIVE"
assert len(catalog["studies"]) == 4
assert {study["status"] for study in catalog["studies"]} >= {
    "REMOVAL_HURTS_VALIDATED",
    "DIRECTIONALLY_SUPPORTED",
    "NOT_SUPPORTED",
    "ACCUMULATING",
}

rendered = evidence.render_markdown(catalog)
committed = markdown_path.read_text(encoding="utf-8")
assert rendered == committed, "evidence_catalog.md is not synchronized with YAML"

weight_change = copy.deepcopy(catalog)
weight_change["subject"]["current_production_weight_points"] = 10
weight_change["subject"]["current_decision"] = "REDUCE_WEIGHT"
weight_change["subject"]["automatic_weight_change_allowed"] = True
weight_errors = evidence.validate_catalog(weight_change, ROOT)
assert any("current_production_weight_points" in error for error in weight_errors)
assert any("current_decision" in error for error in weight_errors)
assert any("automatic_weight_change_allowed" in error for error in weight_errors)

precedence_change = copy.deepcopy(catalog)
precedence_change["precedence"] = list(reversed(precedence_change["precedence"]))
precedence_errors = evidence.validate_catalog(precedence_change, ROOT)
assert any("highest evidence precedence" in error for error in precedence_errors)

conflict_suppression = copy.deepcopy(catalog)
conflict_suppression["subject"]["historical_consensus"] = "VALIDATED"
conflict_errors = evidence.validate_catalog(conflict_suppression, ROOT)
assert any("historical_consensus" in error for error in conflict_errors)
assert any("conflicting historical evidence" in error for error in conflict_errors)

forward_backdating = copy.deepcopy(catalog)
forward_study = next(
    study
    for study in forward_backdating["studies"]
    if study["evidence_class"] == "PROSPECTIVE_LIVE"
)
forward_study["eligible_signal_date_from"] = forward_study["registered_at"]
forward_errors = evidence.validate_catalog(forward_backdating, ROOT)
assert any("must be after registration" in error for error in forward_errors)

unsafe_path = copy.deepcopy(catalog)
expanded = next(
    study
    for study in unsafe_path["studies"]
    if study["id"] == "volume-component-expanded-5fold-v1"
)
expanded["result_files"].append("../outside-repository.csv")
path_errors = evidence.validate_catalog(unsafe_path, ROOT)
assert any("unsafe result path" in error for error in path_errors)

missing_path = copy.deepcopy(catalog)
expanded_missing = next(
    study
    for study in missing_path["studies"]
    if study["id"] == "volume-component-expanded-5fold-v1"
)
expanded_missing["result_files"].append(
    "research/results/definitely_missing_evidence.csv"
)
missing_errors = evidence.validate_catalog(missing_path, ROOT)
assert any("missing result file" in error for error in missing_errors)

promotion = copy.deepcopy(catalog)
promotion["studies"][0]["promotion_evidence_allowed"] = True
promotion_errors = evidence.validate_catalog(promotion, ROOT)
assert any("promotion_evidence_allowed must be false" in error for error in promotion_errors)

print("research evidence catalog validation passed")
