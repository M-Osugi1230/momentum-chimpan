from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import research_scorecard as scorecard


history_rows = []
for rank, (code, sector, entry_close, exit_close) in enumerate([
    ("1001", "電気機器", 100.0, 120.0),
    ("1002", "電気機器", 100.0, 110.0),
    ("1003", "電気機器", 100.0, 105.0),
    ("2001", "銀行業", 100.0, 102.0),
    ("2002", "銀行業", 100.0, 98.0),
    ("2003", "銀行業", 100.0, 100.0),
], start=1):
    history_rows.extend([
        {
            "date": "2026-07-01",
            "code": code,
            "close": entry_close,
            "rank": rank,
            "sector33": sector,
        },
        {
            "date": "2026-07-08",
            "code": code,
            "close": exit_close,
            "rank": rank,
            "sector33": sector,
        },
    ])
history = pd.DataFrame(history_rows)

outcomes = pd.DataFrame([
    {
        "signal_date": "2026-07-01",
        "entry_price_date": "2026-07-01",
        "exit_price_date": "2026-07-08",
        "code": "1001",
        "name": "Leader A",
        "sector33": "電気機器",
        "sector_research_priority": "最優先",
        "sector_leader_grade": "A",
        "sector_rotation": "加速",
        "horizon_days": 5,
        "forward_return": 0.20,
    },
    {
        "signal_date": "2026-07-01",
        "entry_price_date": "2026-07-01",
        "exit_price_date": "2026-07-08",
        "code": "1002",
        "name": "Leader B",
        "sector33": "電気機器",
        "sector_research_priority": "優先",
        "sector_leader_grade": "B",
        "sector_rotation": "主導",
        "horizon_days": 5,
        "forward_return": 0.10,
    },
])

enriched = scorecard.attach_benchmarks(outcomes, history)
assert len(enriched) == 2
expected_universe = np.mean([0.20, 0.10, 0.05, 0.02, -0.02, 0.0])
expected_sector = np.mean([0.20, 0.10, 0.05])
assert np.isclose(enriched.iloc[0]["universe_equal_weight_return"], expected_universe)
assert np.isclose(enriched.iloc[0]["top100_equal_weight_return"], expected_universe)
assert np.isclose(enriched.iloc[0]["sector_equal_weight_return"], expected_sector)
assert np.isclose(enriched.iloc[0]["excess_vs_universe"], 0.20 - expected_universe)
assert enriched.iloc[0]["beat_universe"]
assert enriched.iloc[0]["beat_sector"]

values = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05, 0.06])
first_ci = scorecard.bootstrap_mean_ci(values, samples=500, seed=123)
second_ci = scorecard.bootstrap_mean_ci(values, samples=500, seed=123)
assert first_ci == second_ci
assert first_ci[0] is not None and first_ci[1] is not None
assert first_ci[0] < first_ci[1]
assert scorecard.bootstrap_mean_ci(pd.Series([0.01, 0.02])) == (None, None)

assert scorecard.evidence_grade(5, 0.01, 0.8, False) == "INSUFFICIENT"
assert scorecard.evidence_grade(20, 0.01, 0.8, False) == "EARLY"
assert scorecard.evidence_grade(40, -0.01, 0.8, False) == "INCONCLUSIVE"
assert scorecard.evidence_grade(60, 0.01, 0.8, True) == "DEVELOPING"
assert scorecard.evidence_grade(60, 0.01, 0.8, False) == "PROMISING"
assert scorecard.evidence_grade(120, 0.01, 0.60, False) == "STRONG"

concentrated = pd.concat([enriched] * 10, ignore_index=True)
concentration = scorecard.build_concentration(concentrated)
assert len(concentration) == 1
assert bool(concentration.iloc[0]["concentration_flag"])
assert concentration.iloc[0]["top_signal_date_share"] == 1.0

score = scorecard.build_evidence_scorecard(concentrated)
assert not score.empty
assert {"overall", "priority", "grade", "rotation"}.issubset(set(score["group_type"]))
assert "evidence_grade" in score.columns
assert "excess_ci_low_95" in score.columns

with TemporaryDirectory() as temporary:
    result = scorecard.write_outputs(
        enriched,
        score,
        concentration,
        temporary,
        source_hash="abc123",
    )
    paths = result["paths"]
    for path in paths.values():
        assert Path(path).exists(), path
    assert result["manifest"]["research_only"] is True
    assert result["manifest"]["automatic_threshold_changes"] is False
    assert result["manifest"]["benchmark_coverage"] == 1.0
    workbook = pd.ExcelFile(paths["excel"])
    required_sheets = {
        "Evidence Summary",
        "Benchmarked Outcomes",
        "Evidence Scorecard",
        "Concentration",
        "Methodology",
    }
    assert required_sheets.issubset(workbook.sheet_names)

print("research evidence scorecard validation passed")
