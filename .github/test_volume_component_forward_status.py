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

import volume_component_forward_status as status


committed = status.load_json(ROOT / status.DEFAULT_OUTPUT)
assert committed == status.build_initial_status()
assert status.validate_status(committed) == []
assert committed["evidence_status"] == "ACCUMULATING"
assert committed["horizons"]["10"]["baseline_outcome_count"] == 0
assert committed["automatic_weight_change"] is False


def write_inputs(
    root: Path,
    evidence_status: str,
    sample_adequate: bool,
    baseline_count: int,
    tested_count: int,
    paired_dates: int,
) -> dict[str, Path]:
    manifest = {
        "study_id": status.STUDY_ID,
        "generated_at_utc": "2026-09-01T03:30:00+00:00",
        "source_strategy_fingerprint": "a" * 64,
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "lookahead_violations": 0,
        "distribution_preserved": True,
    }
    provenance = {
        "strategy_fingerprint": "a" * 64,
        "evidence_origin": "LIVE_FORWARD_RANKING_HISTORY",
        "promotion_evidence_allowed": False,
    }
    status_frame = pd.DataFrame([{
        "evidence_status": evidence_status,
        "sample_adequate": sample_adequate,
        "primary_target": "excess_vs_universe",
        "required_horizons": "10|20",
        "minimum_outcomes_per_variant_per_horizon": 100,
        "minimum_paired_dates_per_horizon": 20,
        "automatic_weight_change_allowed": False,
        "promotion_evidence_allowed": False,
    }])
    metric_rows = []
    for horizon in (5, 10, 20):
        for variant, count in (
            ("baseline", baseline_count),
            ("drop_volume_ratio", tested_count),
        ):
            metric_rows.append({
                "variant": variant,
                "horizon_days": horizon,
                "outcome_count": count,
                "average_forward_return": 0.01,
            })
    statistics = pd.DataFrame([
        {
            "horizon_days": horizon,
            "target": "excess_vs_universe",
            "paired_date_count": paired_dates,
            "mean_daily_difference": -0.001,
            "early_mean_difference": -0.0012,
            "late_mean_difference": -0.0008,
            "ci_low": -0.0015,
            "ci_high": -0.0003,
            "two_sided_p_value": 0.02,
            "harm_p_value": 0.01,
        }
        for horizon in (5, 10, 20)
    ])

    paths = {
        "manifest": root / "manifest.json",
        "provenance": root / "provenance.json",
        "evidence_status": root / "status.csv",
        "metrics": root / "metrics.csv",
        "statistics": root / "statistics.csv",
    }
    paths["manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["provenance"].write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    status_frame.to_csv(paths["evidence_status"], index=False)
    pd.DataFrame(metric_rows).to_csv(paths["metrics"], index=False)
    statistics.to_csv(paths["statistics"], index=False)
    return paths


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    paths = write_inputs(root, "ACCUMULATING", False, 75, 70, 15)
    accumulating = status.build_status(
        str(paths["manifest"]),
        str(paths["evidence_status"]),
        str(paths["metrics"]),
        str(paths["statistics"]),
        str(paths["provenance"]),
        str(ROOT / status.DEFAULT_REGISTRY),
        "12345",
    )
    assert status.validate_status(accumulating) == []
    assert accumulating["evidence_status"] == "ACCUMULATING"
    assert accumulating["sample_adequate"] is False
    assert accumulating["horizons"]["10"]["baseline_outcome_count"] == 75
    assert accumulating["horizons"]["10"]["tested_outcome_count"] == 70
    assert accumulating["horizons"]["10"]["minimum_variant_outcome_count"] == 70
    assert accumulating["horizons"]["10"]["paired_date_count"] == 15
    assert accumulating["horizons"]["10"]["outcome_progress_ratio"] == 0.70
    assert accumulating["horizons"]["10"]["paired_date_progress_ratio"] == 0.75
    assert len(accumulating["evidence_fingerprint"]) == 64
    assert len(accumulating["status_sha256"]) == 64

    output = root / "signed.json"
    status.write_status(accumulating, output)
    assert status.load_json(output) == accumulating

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    paths = write_inputs(root, "ROBUSTLY_SUPPORTED", True, 125, 120, 25)
    robust = status.build_status(
        str(paths["manifest"]),
        str(paths["evidence_status"]),
        str(paths["metrics"]),
        str(paths["statistics"]),
        str(paths["provenance"]),
        str(ROOT / status.DEFAULT_REGISTRY),
        "67890",
    )
    assert status.validate_status(robust) == []
    assert robust["evidence_status"] == "ROBUSTLY_SUPPORTED"
    assert robust["sample_adequate"] is True
    assert all(record["sample_adequate"] for record in robust["horizons"].values())
    assert robust["horizons"]["20"]["two_sided_p_value"] == 0.02
    assert robust["horizons"]["20"]["ci_high"] == -0.0003

    tampered = copy.deepcopy(robust)
    tampered["horizons"]["20"]["tested_outcome_count"] = 999
    tamper_errors = status.validate_status(tampered)
    assert any("status_sha256 mismatch" in error for error in tamper_errors)
    assert any("evidence_fingerprint mismatch" in error for error in tamper_errors)

    unsafe = copy.deepcopy(robust)
    unsafe["automatic_weight_change"] = True
    unsafe_errors = status.validate_status(unsafe)
    assert any("automatic_weight_change must be false" in error for error in unsafe_errors)

    bad_cutoff = copy.deepcopy(robust)
    bad_cutoff["eligible_signal_date_from"] = "2026-07-12"
    cutoff_errors = status.validate_status(bad_cutoff)
    assert any("invalid prospective cutoff" in error for error in cutoff_errors)

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    paths = write_inputs(root, "DIRECTIONALLY_SUPPORTED", False, 40, 40, 8)
    try:
        status.build_status(
            str(paths["manifest"]),
            str(paths["evidence_status"]),
            str(paths["metrics"]),
            str(paths["statistics"]),
            str(paths["provenance"]),
            str(ROOT / status.DEFAULT_REGISTRY),
        )
        raise AssertionError("insufficient non-accumulating status was accepted")
    except ValueError as exc:
        assert "non-accumulating status requires adequate samples" in str(exc)

print("volume component forward status validation passed")
