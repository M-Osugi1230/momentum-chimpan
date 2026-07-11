from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import evidence_provenance
import research_evidence_status as status_module


fingerprint = evidence_provenance.current_strategy_fingerprint()


def registry() -> dict:
    return {
        "schema_version": 1,
        "policy": {
            "automatic_promotion": False,
            "minimum_outcome_count": 100,
            "required_robustness_status": "ROBUST",
            "maximum_fdr_q_value": 0.05,
            "require_positive_early_period": True,
            "require_positive_late_period": True,
            "require_positive_leave_one_sector": True,
            "require_manual_approval": True,
            "allowed_promotion_evidence_origins": [evidence_provenance.LIVE_ORIGIN],
            "required_promotion_execution_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        },
        "experiments": [],
    }


def provenance(valid: bool = True) -> dict:
    return {
        "evidence_origin": evidence_provenance.LIVE_ORIGIN,
        "execution_origin": evidence_provenance.EXECUTION_ORIGIN,
        "execution_evidence": True,
        "promotion_evidence_allowed": valid,
        "strategy_fingerprint": fingerprint if valid else "wrong",
        "source_path": evidence_provenance.ALLOWED_LIVE_SOURCE,
        "execution_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "entry_slippage_bps": 5.0,
        "exit_slippage_bps": 5.0,
        "fees_bps": 20.0,
        "execution_outcome_count": 120,
        "research_only": True,
    }


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    registry_path = root / "registry.yaml"
    provenance_path = root / "provenance.json"
    robustness_path = root / "robustness.csv"
    issues_path = root / "issues.csv"
    execution_path = root / "execution.json"
    registry_path.write_text(yaml.safe_dump(registry(), sort_keys=False), encoding="utf-8")
    provenance_path.write_text(json.dumps(provenance()), encoding="utf-8")
    execution_path.write_text(json.dumps({
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "outcome_count": 120,
    }), encoding="utf-8")
    pd.DataFrame([{
        "group_type": "overall",
        "group_value": "all",
        "horizon_days": 10,
        "count": 120,
        "robustness_status": "ROBUST",
        "fdr_q_value": 0.03,
        "early_net_average_excess": 0.01,
        "late_net_average_excess": 0.012,
        "worst_leave_one_sector_excess": 0.008,
    }]).to_csv(robustness_path, index=False)
    pd.DataFrame(columns=["severity", "issue"]).to_csv(issues_path, index=False)

    ready = status_module.build_status(
        str(provenance_path),
        str(robustness_path),
        str(issues_path),
        str(execution_path),
        str(registry_path),
    )
    assert ready["manual_review_eligible"] is True
    assert ready["readiness"] == "ELIGIBLE_FOR_MANUAL_REVIEW"
    assert ready["provenance_valid"] is True
    assert ready["outcome_count"] == 120
    assert ready["robustness_status"] == "ROBUST"
    assert ready["governance_issue_count"] == 0
    assert ready["automatic_strategy_change"] is False
    assert ready["automatic_promotion"] is False
    assert len(ready["status_sha256"]) == 64

    paths = status_module.write_outputs(
        ready,
        str(root / "data" / "research_evidence_status.json"),
        str(root / "artifact"),
    )
    for path in paths.values():
        assert Path(path).exists(), path
    workbook = pd.ExcelFile(paths["excel"])
    assert {"Evidence Status", "Readiness Criteria"}.issubset(workbook.sheet_names)

    # Not enough outcomes keeps the system in accumulation mode.
    pd.DataFrame([{
        "group_type": "overall",
        "group_value": "all",
        "horizon_days": 10,
        "count": 40,
        "robustness_status": "ROBUST",
        "fdr_q_value": 0.03,
        "early_net_average_excess": 0.01,
        "late_net_average_excess": 0.012,
        "worst_leave_one_sector_excess": 0.008,
    }]).to_csv(robustness_path, index=False)
    accumulating = status_module.build_status(
        str(provenance_path), str(robustness_path), str(issues_path),
        str(execution_path), str(registry_path)
    )
    assert accumulating["manual_review_eligible"] is False
    assert accumulating["readiness"] == "ACCUMULATING"

    # Robustness failure blocks review even with sufficient observations.
    row = pd.read_csv(robustness_path)
    row.loc[:, "count"] = 120
    row.loc[:, "robustness_status"] = "FRAGILE"
    row.to_csv(robustness_path, index=False)
    fragile = status_module.build_status(
        str(provenance_path), str(robustness_path), str(issues_path),
        str(execution_path), str(registry_path)
    )
    assert fragile["manual_review_eligible"] is False
    assert fragile["readiness"] == "FRAGILE"

    # Provenance and governance failures are explicit.
    provenance_path.write_text(json.dumps(provenance(valid=False)), encoding="utf-8")
    blocked = status_module.build_status(
        str(provenance_path), str(robustness_path), str(issues_path),
        str(execution_path), str(registry_path)
    )
    assert blocked["manual_review_eligible"] is False
    assert blocked["readiness"] == "PROVENANCE_BLOCKED"

    provenance_path.write_text(json.dumps(provenance()), encoding="utf-8")
    pd.DataFrame([{"severity": "FAIL", "issue": "test failure"}]).to_csv(issues_path, index=False)
    governance_blocked = status_module.build_status(
        str(provenance_path), str(robustness_path), str(issues_path),
        str(execution_path), str(registry_path)
    )
    assert governance_blocked["manual_review_eligible"] is False
    assert governance_blocked["readiness"] == "GOVERNANCE_BLOCKED"

    # Missing evidence is a safe, non-ready state rather than a fabricated result.
    missing = status_module.build_status(
        str(root / "missing-provenance.json"),
        str(root / "missing-robustness.csv"),
        str(root / "missing-issues.csv"),
        str(root / "missing-execution.json"),
        str(registry_path),
    )
    assert missing["manual_review_eligible"] is False
    assert missing["readiness"] == "NO_EXECUTION_EVIDENCE"
    assert missing["outcome_count"] == 0

print("signed research evidence status validation passed")
