from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import robustness_analysis as robustness


rows = []
sectors = ["電気機器", "銀行業", "機械", "情報・通信業"]
dates = pd.bdate_range("2026-01-05", periods=30)
for index in range(120):
    signal_date = dates[index % len(dates)]
    rows.append({
        "signal_date": signal_date,
        "entry_price_date": signal_date,
        "exit_price_date": signal_date + pd.offsets.BDay(5),
        "code": f"{1000 + index % 40:04d}",
        "sector33": sectors[index % len(sectors)],
        "sector_research_priority": "最優先" if index % 2 == 0 else "優先",
        "sector_leader_grade": "A" if index % 3 else "B",
        "sector_rotation": "加速" if index % 2 == 0 else "主導",
        "horizon_days": 5,
        "forward_return": 0.018 + (index % 5) * 0.001,
        "excess_vs_universe": 0.012 + (index % 7) * 0.001,
    })
frame = pd.DataFrame(rows)

p1 = robustness.sign_flip_p_value(frame["excess_vs_universe"], samples=1000, seed=123)
p2 = robustness.sign_flip_p_value(frame["excess_vs_universe"], samples=1000, seed=123)
assert p1 == p2
assert p1 is not None and p1 < 0.05
assert robustness.sign_flip_p_value(pd.Series([0.01, 0.02])) is None

p_values = pd.Series([0.01, 0.02, 0.20, np.nan])
q_values = robustness.benjamini_hochberg(p_values)
assert np.isclose(q_values.iloc[0], 0.03)
assert np.isclose(q_values.iloc[1], 0.03)
assert np.isclose(q_values.iloc[2], 0.20)
assert pd.isna(q_values.iloc[3])

cost = robustness.build_cost_sensitivity(frame)
overall = cost[(cost["group_type"] == "overall") & (cost["horizon_days"] == 5)]
assert set(overall["round_trip_cost_bps"]) == {0, 10, 30, 50}
mean_by_cost = overall.set_index("round_trip_cost_bps")["net_average_excess"]
assert mean_by_cost.loc[0] > mean_by_cost.loc[10] > mean_by_cost.loc[30] > mean_by_cost.loc[50]
assert np.isclose(mean_by_cost.loc[0] - mean_by_cost.loc[30], 0.003)

subperiod = robustness.build_subperiod_stability(frame, base_cost_bps=30)
overall_periods = subperiod[subperiod["group_type"] == "overall"]
assert set(overall_periods["period"]) == {"early", "late"}
assert (overall_periods["net_average_excess"] > 0).all()

clusters = robustness.build_cluster_robustness(frame, base_cost_bps=30)
overall_clusters = clusters[clusters["group_type"] == "overall"]
assert set(overall_clusters["cluster_type"]) == {"sector", "signal_date", "code"}
assert (overall_clusters["worst_excluded_mean_excess"] > 0).all()
assert (overall_clusters["positive_exclusion_rate"] == 1.0).all()

tests = robustness.build_statistical_tests(frame, base_cost_bps=30)
assert not tests.empty
assert "fdr_q_value" in tests.columns
assert tests.loc[tests["group_type"] == "overall", "fdr_q_value"].iloc[0] <= 0.05

summary = robustness.build_robustness_summary(cost, subperiod, clusters, tests, base_cost_bps=30)
overall_summary = summary[summary["group_type"] == "overall"].iloc[0]
assert overall_summary["robustness_status"] == "ROBUST"
assert overall_summary["net_average_excess"] > 0
assert overall_summary["early_net_average_excess"] > 0
assert overall_summary["late_net_average_excess"] > 0
assert overall_summary["worst_leave_one_sector_excess"] > 0

assert robustness.robustness_status(10, 0.01, 0.01, 0.01, 0.01, 0.01, 0.6) == "INSUFFICIENT"
assert robustness.robustness_status(50, -0.01, 0.01, 0.01, 0.01, 0.01, 0.6) == "FRAGILE"
assert robustness.robustness_status(50, 0.01, 0.20, 0.01, 0.01, 0.01, 0.6) == "DEVELOPING"
assert robustness.robustness_status(60, 0.01, 0.05, 0.01, 0.01, 0.01, 0.6) == "PROMISING"
assert robustness.robustness_status(120, 0.01, 0.04, 0.01, 0.01, 0.01, 0.6) == "ROBUST"

with TemporaryDirectory() as temporary:
    result = robustness.write_outputs(
        cost,
        subperiod,
        clusters,
        tests,
        summary,
        temporary,
        source_hash="abc123",
    )
    for path in result["paths"].values():
        assert Path(path).exists(), path
    assert result["manifest"]["research_only"] is True
    assert result["manifest"]["automatic_threshold_changes"] is False
    workbook = pd.ExcelFile(result["paths"]["excel"])
    required_sheets = {
        "Robustness Summary",
        "Decision Table",
        "Cost Sensitivity",
        "Subperiod Stability",
        "Cluster Robustness",
        "Statistical Tests",
        "Methodology",
    }
    assert required_sheets.issubset(workbook.sheet_names)

print("replay robustness validation passed")
