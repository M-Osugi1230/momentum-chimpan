from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import volume_component_forward_evidence as forward


def synthetic_outcomes(date_count: int = 40) -> pd.DataFrame:
    dates = pd.bdate_range("2026-07-13", periods=date_count)
    rows: list[dict[str, object]] = []
    for date_index, signal_date in enumerate(dates):
        for horizon in (5, 10, 20):
            for code_index in range(4):
                baseline_return = (
                    0.0020
                    + horizon * 0.00005
                    + code_index * 0.0001
                    + (date_index % 5) * 0.00002
                )
                tested_return = baseline_return - 0.0020
                universe_return = 0.0005 + horizon * 0.00001
                sector_return = 0.0004 + horizon * 0.00001
                for variant, result in (
                    (forward.BASELINE_VARIANT, baseline_return),
                    (forward.TEST_VARIANT, tested_return),
                ):
                    rows.append({
                        "signal_date": signal_date.date().isoformat(),
                        "entry_price_date": signal_date.date().isoformat(),
                        "exit_price_date": (
                            signal_date + pd.offsets.BDay(horizon - 1)
                        ).date().isoformat(),
                        "horizon_days": horizon,
                        "code": f"9{code_index + 1:03d}",
                        "variant": variant,
                        "forward_return": result,
                        "excess_vs_universe": result - universe_return,
                        "excess_vs_sector": result - sector_return,
                        "beat_universe": result > universe_return,
                        "beat_sector": result > sector_return,
                    })
    return pd.DataFrame(rows)


registry = {
    "study": {
        "id": "volume-component-forward-evidence-v1",
        "registered_at": "2026-07-12",
        "eligible_signal_date_from": "2026-07-13",
    },
    "comparison": {
        "baseline_variant": forward.BASELINE_VARIANT,
        "tested_variant": forward.TEST_VARIANT,
        "horizons": [5, 10, 20],
    },
    "evidence_gate": {
        "primary_target": "excess_vs_universe",
        "required_horizons": [10, 20],
        "minimum_outcomes_per_variant_per_horizon": 100,
        "minimum_paired_dates_per_horizon": 20,
        "maximum_two_sided_p_value": 0.05,
    },
    "governance": {
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
    },
}

outcomes = synthetic_outcomes()
analysis = forward.analyze_forward_outcomes(outcomes, registry)
metrics = analysis["variant_metrics"]
pairs = analysis["daily_pairs"]
stats = analysis["statistical_summary"]
status = analysis["evidence_status"].iloc[0]

assert set(metrics["variant"]) == {
    forward.BASELINE_VARIANT,
    forward.TEST_VARIANT,
}
assert set(metrics["horizon_days"]) == {5, 10, 20}
assert set(pairs["target"]) == set(forward.TARGET_COLUMNS)
assert pairs["delta"].lt(0).all()
assert status["evidence_status"] == "ROBUSTLY_SUPPORTED"
assert bool(status["sample_adequate"])
assert not bool(status["automatic_weight_change_allowed"])
assert not bool(status["promotion_evidence_allowed"])

primary_stats = stats[
    stats["target"].eq("excess_vs_universe")
    & stats["horizon_days"].isin([10, 20])
]
assert len(primary_stats) == 2
assert primary_stats["paired_date_count"].eq(40).all()
assert primary_stats["mean_daily_difference"].lt(0).all()
assert primary_stats["early_mean_difference"].lt(0).all()
assert primary_stats["late_mean_difference"].lt(0).all()
assert primary_stats["ci_high"].lt(0).all()
assert primary_stats["two_sided_p_value"].le(0.05).all()

small_analysis = forward.analyze_forward_outcomes(
    synthetic_outcomes(date_count=5), registry
)
small_status = small_analysis["evidence_status"].iloc[0]
assert small_status["evidence_status"] == "ACCUMULATING"
assert not bool(small_status["sample_adequate"])

with TemporaryDirectory() as temporary:
    output = Path(temporary)
    distribution = pd.DataFrame([{
        "date": "2026-07-13",
        "baseline_rows": 100,
        "variant_rows": 100,
        "score_multiset_equal": True,
    }])
    replay_audit = pd.DataFrame([{
        "signal_date": "2026-07-13",
        "variant": forward.BASELINE_VARIANT,
        "lookahead_violations": 0,
        "status": "PASS",
    }])
    signals = pd.DataFrame([{
        "signal_date": "2026-07-13",
        "code": "9001",
        "variant": forward.BASELINE_VARIANT,
    }])
    coverage = pd.DataFrame([{
        "signal_date": "2026-07-13",
        "code": "9001",
        "variant": forward.BASELINE_VARIANT,
        "status": "EXECUTABLE",
    }])
    manifest = {
        "forward_version": forward.FORWARD_VERSION,
        "evidence_status": status["evidence_status"],
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "production_state_mutations": [],
    }
    paths = forward.write_outputs(
        str(output),
        analysis,
        signals,
        outcomes,
        coverage,
        distribution,
        replay_audit,
        manifest,
    )
    for path in paths.values():
        assert Path(path).exists(), path
    saved_manifest = json.loads(
        Path(paths["manifest"]).read_text(encoding="utf-8")
    )
    assert saved_manifest["evidence_status"] == "ROBUSTLY_SUPPORTED"
    workbook = pd.ExcelFile(paths["excel"])
    assert {
        "Manifest",
        "Status",
        "Variant Metrics",
        "Statistics",
        "Daily Pairs",
        "Signals",
        "Outcomes",
        "Coverage",
        "Distribution Audit",
        "Replay Audit",
    }.issubset(workbook.sheet_names)

print("volume component forward evidence validation passed")
