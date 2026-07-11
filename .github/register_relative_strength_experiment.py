from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import strategy_governance


REGISTRY_PATH = REPOSITORY_ROOT / "research/experiment_registry.yaml"
EXPERIMENT_ID = "relative-strength-alpha-v18"

registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
registry.setdefault("experiments", [])
fingerprint = strategy_governance.strategy_fingerprint(
    str(REPOSITORY_ROOT / "main.py"),
    str(REPOSITORY_ROOT / "config.yaml"),
)["sha256"]

experiment = {
    "experiment_id": EXPERIMENT_ID,
    "experiment_type": "relative-strength-feature",
    "status": "proposed",
    "hypothesis": (
        "Stocks outperforming both the scanned market median and their JPX 33-sector median "
        "over 20 and 60 trading days will produce stronger 10-day forward excess returns "
        "than otherwise similar momentum leaders."
    ),
    "strategy_fingerprint": fingerprint,
    "change_summary": (
        "Add market-relative and sector-relative 20/60-day strength, include a bounded "
        "relative-strength contribution in sector-leader scoring, and evaluate forward "
        "returns against same-window market and sector medians. Base Momentum scoring is unchanged."
    ),
    "evidence_scope": {
        "group_type": "overall",
        "group_value": "all",
        "horizon_days": 10,
    },
    "success_criteria": {
        "minimum_outcome_count": 100,
        "positive_market_excess_return": True,
        "positive_sector_excess_return": True,
        "minimum_market_outperformance_rate": 0.55,
        "minimum_sector_outperformance_rate": 0.55,
        "required_robustness_status": "ROBUST",
        "maximum_fdr_q_value": 0.05,
    },
    "manual_approval": {
        "approved": False,
        "approved_by": "",
        "approved_at": "",
    },
    "automatic_promotion": False,
    "notes": (
        "Research and paper validation only. Registration permits governed measurement but does not "
        "authorize automatic threshold changes, promotion, or live order execution."
    ),
}

existing_index = next(
    (index for index, item in enumerate(registry["experiments"]) if item.get("experiment_id") == EXPERIMENT_ID),
    None,
)
if existing_index is None:
    registry["experiments"].append(experiment)
else:
    registry["experiments"][existing_index] = experiment

issues = strategy_governance.validate_registry(registry)
if issues:
    raise RuntimeError(f"experiment registry validation failed: {issues}")

REGISTRY_PATH.write_text(
    yaml.safe_dump(registry, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
print(f"registered {EXPERIMENT_ID} with fingerprint {fingerprint}")
