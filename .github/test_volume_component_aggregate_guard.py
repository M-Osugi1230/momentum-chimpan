from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import volume_component_aggregate_guard as guard
import volume_component_robustness as robustness


def write_fold(
    root: Path,
    fold_id: str,
    adequate: bool,
    tested_shift: float,
    missing_tested_date: str | None = None,
) -> None:
    analysis = root / fold_id / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame([{
        "fold_id": fold_id,
        "sample_adequate": adequate,
        "delta_excess_return": tested_shift * 20,
        "delta_max_drawdown": -0.01,
        "early_delta_excess": tested_shift * 8,
        "late_delta_excess": tested_shift * 8,
    }])
    summary.to_csv(analysis / "volume_fold_summary.csv", index=False)

    dates = pd.bdate_range("2025-01-06", periods=40)
    rows: list[dict[str, object]] = []
    for date in dates:
        day = date.date().isoformat()
        rows.append({
            "date": day,
            "daily_return": 0.001,
            "fold_id": fold_id,
            "variant": robustness.BASELINE_VARIANT,
            "period": "full",
        })
        if day != missing_tested_date:
            rows.append({
                "date": day,
                "daily_return": 0.001 + tested_shift,
                "fold_id": fold_id,
                "variant": robustness.TEST_VARIANT,
                "period": "full",
            })
    pd.DataFrame(rows).to_csv(analysis / "volume_fold_equity.csv", index=False)


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    missing_date = pd.bdate_range("2025-01-06", periods=40)[5].date().isoformat()
    write_fold(root, "fold_01", True, -0.0015)
    write_fold(root, "fold_02", True, -0.0010, missing_tested_date=missing_date)
    # This large positive effect must never influence aggregate inference.
    write_fold(root, "fold_03", False, 0.0500)

    registry = {
        "validation_gate": {
            "minimum_evaluable_folds": 2,
            "minimum_harm_direction_fraction": 0.75,
            "maximum_two_sided_p_value": 0.05,
        }
    }
    results = guard.aggregate_guarded(str(root), registry)
    summary = results["aggregate_summary"].iloc[0]
    manifest = results["manifest"]

    assert int(summary["fold_count"]) == 3
    assert int(summary["evaluable_fold_count"]) == 2
    assert int(summary["complete_case_date_count"]) == 39
    assert int(summary["excluded_incomplete_date_count"]) == 1
    assert summary["aggregate_fold_ids"] == "fold_01|fold_02"
    assert float(summary["mean_daily_difference"]) < 0
    assert summary["robustness_status"] == "ROBUSTLY_SUPPORTED"
    assert len(results["fold_summary"]) == 3
    assert set(results["fold_summary"]["fold_id"]) == {"fold_01", "fold_02", "fold_03"}
    assert manifest["aggregate_fold_ids"] == ["fold_01", "fold_02"]
    assert manifest["aggregate_uses_sample_adequate_folds_only"] is True
    assert manifest["aggregate_requires_complete_fold_date_coverage"] is True

    output = robustness.write_aggregate_outputs(results, str(root / "report"))
    for path in output["paths"].values():
        assert Path(path).exists(), path
    saved_manifest = json.loads(Path(output["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert saved_manifest["complete_case_date_count"] == 39

print("volume component aggregate guard validation passed")
