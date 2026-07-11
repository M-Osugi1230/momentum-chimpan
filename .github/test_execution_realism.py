from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import execution_realism as execution
import historical_backfill
import historical_price_panel


dates = pd.bdate_range("2026-01-02", periods=25)
panel_rows = []
price_specs = {
    "1001": ("電気機器", 110.0, 121.0),
    "1002": ("電気機器", 100.0, 105.0),
    "2001": ("銀行業", 100.0, 101.0),
}
for code, (sector, first_open, fifth_close) in price_specs.items():
    for index, day in enumerate(dates):
        if code == "1001":
            open_price = first_open + index * 0.5
            close_price = 111.0 + index * 2.5
            if index == 4:
                close_price = fifth_close
        elif code == "1002":
            open_price = first_open + index * 0.1
            close_price = 100.5 + index * 1.0
            if index == 4:
                close_price = fifth_close
        else:
            open_price = first_open
            close_price = 100.2 + index * 0.2
            if index == 4:
                close_price = fifth_close
        panel_rows.append({
            "date": day,
            "code": code,
            "name": code,
            "sector33": sector,
            "adjusted_open": open_price,
            "adjusted_high": max(open_price, close_price) + 1,
            "adjusted_low": min(open_price, close_price) - 1,
            "adjusted_close": close_price,
            "raw_close": close_price,
            "volume": 1_000_000,
            "raw_trading_value": close_price * 1_000_000,
        })
panel = pd.DataFrame(panel_rows)

signals = pd.DataFrame([{
    "signal_date": pd.Timestamp("2026-01-01"),
    "code": "1001",
    "name": "Leader",
    "sector33": "電気機器",
    "entry_close": 100.0,
    "sector_research_priority": "最優先",
    "sector_leader_grade": "A",
    "sector_rotation": "加速",
}])
ranking = pd.DataFrame([
    {"date": pd.Timestamp("2026-01-01"), "code": "1001", "rank": 1},
    {"date": pd.Timestamp("2026-01-01"), "code": "1002", "rank": 2},
    {"date": pd.Timestamp("2026-01-01"), "code": "2001", "rank": 101},
])

outcomes, coverage = execution.simulate_execution(
    signals,
    panel,
    ranking,
    horizons=(5,),
    entry_slippage_bps=5,
    exit_slippage_bps=5,
    fees_bps=20,
)
assert len(outcomes) == 1
row = outcomes.iloc[0]
assert row["entry_price_date"] == "2026-01-02"
assert row["entry_price_date"] > row["signal_date"]
assert row["exit_price_date"] == dates[4].date().isoformat()
assert np.isclose(row["entry_gap_return"], 0.10)
assert row["execution_status"] == "GAP_CHASE"
assert np.isclose(row["next_open_gross_return"], 121.0 / 110.0 - 1)
assert np.isclose(row["close_based_forward_return"], 121.0 / 100.0 - 1)
assert np.isclose(row["implementation_shortfall"], (121.0 / 110.0 - 1) - (121.0 / 100.0 - 1))
expected_net = (121.0 * (1 - 0.0005)) / (110.0 * (1 + 0.0005)) - 1 - 0.002
assert np.isclose(row["forward_return"], expected_net)
expected_universe = np.mean([
    121.0 / 110.0 - 1,
    105.0 / 100.0 - 1,
    101.0 / 100.0 - 1,
])
expected_top100 = np.mean([121.0 / 110.0 - 1, 105.0 / 100.0 - 1])
expected_sector = expected_top100
assert np.isclose(row["universe_equal_weight_return"], expected_universe)
assert np.isclose(row["top100_equal_weight_return"], expected_top100)
assert np.isclose(row["sector_equal_weight_return"], expected_sector)
assert np.isclose(row["excess_vs_universe"], expected_net - expected_universe)
assert len(coverage) == 1
assert coverage.iloc[0]["status"] == "EXECUTABLE"
assert coverage.iloc[0]["available_horizons"] == 1

summary = execution.execution_summary(outcomes, coverage)
assert len(summary) == 1
assert summary.iloc[0]["execution_coverage"] == 1.0
assert summary.iloc[0]["average_implementation_shortfall"] < 0

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    provenance_path = root / "provenance.json"
    provenance_path.write_text(json.dumps({
        "evidence_origin": "HISTORICAL_CURRENT_UNIVERSE_BACKFILL",
        "promotion_evidence_allowed": False,
    }), encoding="utf-8")
    result = execution.write_outputs(outcomes, coverage, str(root / "execution"), str(provenance_path))
    for path in result["paths"].values():
        assert Path(path).exists(), path
    assert result["manifest"]["promotion_evidence_allowed"] is False
    assert result["manifest"]["same_day_close_entry_allowed"] is False
    assert result["manifest"]["entry_model"] == "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"
    workbook = pd.ExcelFile(result["paths"]["excel"])
    assert {
        "Execution Summary", "Horizon Summary", "Execution Outcomes",
        "Coverage", "Evidence Scorecard", "Concentration",
    }.issubset(workbook.sheet_names)

members = [historical_backfill.UniverseMember("1001", "Leader", "Prime", "電気機器")]
raw_prices = {
    "1001": pd.DataFrame({
        "Date": dates[:3],
        "Open": [50.0, 51.0, 52.0],
        "High": [51.0, 52.0, 53.0],
        "Low": [49.0, 50.0, 51.0],
        "Close": [50.5, 51.5, 52.5],
        "Volume": [1000, 1100, 1200],
        "RawClose": [101.0, 103.0, 105.0],
    })
}
flattened = historical_price_panel.flatten_price_panel(members, raw_prices)
assert len(flattened) == 3
assert np.isclose(flattened.iloc[0]["adjusted_open"], 50.0)
assert np.isclose(flattened.iloc[0]["raw_close"], 101.0)
assert np.isclose(flattened.iloc[0]["raw_trading_value"], 101000.0)

print("next-session execution realism validation passed")
