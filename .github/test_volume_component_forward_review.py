from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import volume_component_forward_review as review
import volume_component_forward_status as status


def resign(payload: dict) -> dict:
    result = copy.deepcopy(payload)
    evidence_core = dict(result)
    evidence_core.pop("generated_at_utc", None)
    evidence_core.pop("evidence_fingerprint", None)
    evidence_core.pop("status_sha256", None)
    result["evidence_fingerprint"] = status.canonical_hash(evidence_core)
    envelope = dict(result)
    envelope.pop("status_sha256", None)
    result["status_sha256"] = status.canonical_hash(envelope)
    return result


def finalized_status(evidence_status: str) -> dict:
    payload = status.build_initial_status()
    payload["strategy_fingerprint"] = "a" * 64
    payload["evidence_status"] = evidence_status
    payload["sample_adequate"] = True
    payload["source_run_id"] = "123456"
    for horizon in ("10", "20"):
        record = payload["horizons"][horizon]
        record.update({
            "baseline_outcome_count": 125,
            "tested_outcome_count": 120,
            "minimum_variant_outcome_count": 120,
            "paired_date_count": 25,
            "outcome_progress_ratio": 1.0,
            "paired_date_progress_ratio": 1.0,
            "sample_adequate": True,
            "mean_daily_difference": -0.0010,
            "early_mean_difference": -0.0012,
            "late_mean_difference": -0.0008,
            "ci_low": -0.0015,
            "ci_high": -0.0003,
            "two_sided_p_value": 0.02,
            "harm_p_value": 0.01,
        })
    return resign(payload)


initial_packet = review.build_review_packet(
    str(ROOT / status.DEFAULT_OUTPUT),
    str(ROOT / review.DEFAULT_CATALOG),
    str(ROOT),
    current_fingerprint="a" * 64,
)
assert initial_packet["status"] == review.NOT_READY_STATUS
assert initial_packet["evidence_status"] == "ACCUMULATING"
assert initial_packet["current_weight_points"] == 15
assert initial_packet["automatic_weight_change"] is False
assert initial_packet["automatic_strategy_change"] is False
assert initial_packet["automatic_approval"] is False
assert initial_packet["manual_review_required"] is True
criteria = {row["criterion"]: row for row in initial_packet["criteria"]}
assert criteria["signed_forward_status_integrity"]["passed"] is True
assert criteria["canonical_evidence_catalog_integrity"]["passed"] is True
assert criteria["required_horizon_samples"]["passed"] is False
assert criteria["forward_evidence_finalized"]["passed"] is False
assert criteria["strategy_fingerprint_consistency"]["passed"] is False
assert "KEEP_15_POINTS" in initial_packet["allowed_human_decisions"]
assert "REGISTER_NEW_WEIGHT_EXPERIMENT" in initial_packet["allowed_human_decisions"]

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    robust_path = root / "robust.json"
    robust_payload = finalized_status("ROBUSTLY_SUPPORTED")
    robust_path.write_text(
        json.dumps(robust_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    robust_packet = review.build_review_packet(
        str(robust_path),
        str(ROOT / review.DEFAULT_CATALOG),
        str(ROOT),
        current_fingerprint="a" * 64,
    )
    assert robust_packet["status"] == review.READY_STATUS
    assert robust_packet["evidence_interpretation"] == (
        "ROBUST_COMPONENT_CONTRIBUTION_SUPPORT"
    )
    assert all(
        row["passed"]
        for row in robust_packet["criteria"]
        if row["blocking"]
    )
    assert len(robust_packet["packet_fingerprint"]) == 64
    assert len(robust_packet["packet_sha256"]) == 64
    assert robust_packet["production_state_mutations"] == []

    output = review.write_packet(robust_packet, str(root / "output"))
    for path in output.values():
        assert Path(path).is_file(), path
    workbook = pd.ExcelFile(output["excel"])
    assert {
        "Review Summary",
        "Readiness Criteria",
        "Forward Horizons",
        "Evidence Chronology",
    }.issubset(workbook.sheet_names)
    markdown = Path(output["markdown"]).read_text(encoding="utf-8")
    assert "READY_FOR_HUMAN_WEIGHT_REVIEW" in markdown
    assert "This packet does not change the 15-point production weight" in markdown

    mismatch_packet = review.build_review_packet(
        str(robust_path),
        str(ROOT / review.DEFAULT_CATALOG),
        str(ROOT),
        current_fingerprint="b" * 64,
    )
    assert mismatch_packet["status"] == review.NOT_READY_STATUS
    mismatch = {
        row["criterion"]: row for row in mismatch_packet["criteria"]
    }
    assert mismatch["strategy_fingerprint_consistency"]["passed"] is False

    unsupported_path = root / "unsupported.json"
    unsupported_path.write_text(
        json.dumps(
            finalized_status("NOT_SUPPORTED"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    unsupported_packet = review.build_review_packet(
        str(unsupported_path),
        str(ROOT / review.DEFAULT_CATALOG),
        str(ROOT),
        current_fingerprint="a" * 64,
    )
    assert unsupported_packet["status"] == review.READY_STATUS
    assert unsupported_packet["evidence_interpretation"] == (
        "COMPONENT_CONTRIBUTION_NOT_SUPPORTED"
    )

    tampered = finalized_status("ROBUSTLY_SUPPORTED")
    tampered["automatic_weight_change"] = True
    tampered_path = root / "tampered.json"
    tampered_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tampered_packet = review.build_review_packet(
        str(tampered_path),
        str(ROOT / review.DEFAULT_CATALOG),
        str(ROOT),
        current_fingerprint="a" * 64,
    )
    assert tampered_packet["status"] == review.NOT_READY_STATUS
    tampered_criteria = {
        row["criterion"]: row for row in tampered_packet["criteria"]
    }
    assert tampered_criteria["signed_forward_status_integrity"]["passed"] is False
    assert tampered_criteria["automatic_changes_locked"]["passed"] is False

print("volume component forward review packet validation passed")
