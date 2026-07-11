from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import capacity_analysis as capacity


entry_date = pd.Timestamp("2026-01-05")
exit_date = pd.Timestamp("2026-01-09")
outcomes = pd.DataFrame([
    {
        "signal_date": pd.Timestamp("2026-01-02"),
        "entry_price_date": entry_date,
        "exit_price_date": exit_date,
        "code": "1001",
        "name": "Liquid Leader",
        "sector33": "電気機器",
        "sector_research_priority": "最優先",
        "sector_leader_grade": "A",
        "sector_rotation": "加速",
        "horizon_days": 5,
        "next_session_open": 1000.0,
        "exit_close": 1100.0,
        "entry_gap_return": 0.02,
        "universe_equal_weight_return": 0.03,
    },
    {
        "signal_date": pd.Timestamp("2026-01-02"),
        "entry_price_date": entry_date,
        "exit_price_date": exit_date,
        "code": "1002",
        "name": "Gap Chase",
        "sector33": "電気機器",
        "sector_research_priority": "優先",
        "sector_leader_grade": "B",
        "sector_rotation": "主導",
        "horizon_days": 5,
        "next_session_open": 1000.0,
        "exit_close": 1080.0,
        "entry_gap_return": 0.06,
        "universe_equal_weight_return": 0.03,
    },
    {
        "signal_date": pd.Timestamp("2026-01-02"),
        "entry_price_date": entry_date,
        "exit_price_date": exit_date,
        "code": "1003",
        "name": "Illiquid",
        "sector33": "機械",
        "sector_research_priority": "優先",
        "sector_leader_grade": "B",
        "sector_rotation": "改善",
        "horizon_days": 5,
        "next_session_open": 2000.0,
        "exit_close": 2100.0,
        "entry_gap_return": 0.01,
        "universe_equal_weight_return": 0.03,
    },
])
panel = pd.DataFrame([
    {"date": entry_date, "code": "1001", "raw_trading_value": 2_000_000_000.0},
    {"date": exit_date, "code": "1001", "raw_trading_value": 2_200_000_000.0},
    {"date": entry_date, "code": "1002", "raw_trading_value": 1_000_000_000.0},
    {"date": exit_date, "code": "1002", "raw_trading_value": 1_100_000_000.0},
    {"date": entry_date, "code": "1003", "raw_trading_value": 100_000_000.0},
    {"date": exit_date, "code": "1003", "raw_trading_value": 120_000_000.0},
])

assert capacity.order_quantity(1_000_000, 1000) == 1000
assert capacity.order_quantity(50_000, 1000) == 0
assert np.isclose(capacity.impact_bps(0.01), 15.0)
assert np.isclose(capacity.impact_bps(0.0001), 6.0)

simulations = capacity.simulate_capacity_scenarios(
    outcomes,
    panel,
    capitals=(1_000_000, 10_000_000),
    scenarios=capacity.SCENARIOS,
)
assert len(simulations) == len(outcomes) * 2 * len(capacity.SCENARIOS)

liquid = simulations[
    (simulations["code"] == "1001")
    & (simulations["requested_capital"] == 1_000_000)
    & (simulations["scenario"] == "baseline_1pct")
].iloc[0]
assert bool(liquid["eligible"])
assert liquid["quantity"] == 1000
assert np.isclose(liquid["entry_participation"], 0.0005)
expected_entry_impact = capacity.BASE_SLIPPAGE_BPS + capacity.IMPACT_COEFFICIENT_BPS * np.sqrt(0.0005)
expected_exit_participation = 1_100_000 / 2_200_000_000
expected_exit_impact = capacity.BASE_SLIPPAGE_BPS + capacity.IMPACT_COEFFICIENT_BPS * np.sqrt(expected_exit_participation)
expected_return = (
    1100 * (1 - expected_exit_impact / 10_000)
    / (1000 * (1 + expected_entry_impact / 10_000))
    - 1
    - capacity.FEES_BPS / 10_000
)
assert np.isclose(liquid["capacity_net_return"], expected_return)
assert np.isclose(liquid["capacity_excess_vs_universe"], expected_return - 0.03)

chase = simulations[
    (simulations["code"] == "1002")
    & (simulations["requested_capital"] == 1_000_000)
    & (simulations["scenario"] == "no_gap_chase_3pct")
].iloc[0]
assert not bool(chase["eligible"])
assert chase["rejection_reason"] == "POSITIVE_GAP_ABOVE_LIMIT"

illiquid = simulations[
    (simulations["code"] == "1003")
    & (simulations["requested_capital"] == 1_000_000)
    & (simulations["scenario"] == "minimum_500m")
].iloc[0]
assert not bool(illiquid["eligible"])
assert illiquid["rejection_reason"] == "ENTRY_TRADING_VALUE_BELOW_MINIMUM"

large_order = simulations[
    (simulations["code"] == "1003")
    & (simulations["requested_capital"] == 10_000_000)
    & (simulations["scenario"] == "strict_capacity_0_5pct")
].iloc[0]
assert not bool(large_order["eligible"])
assert large_order["rejection_reason"] in {
    "ENTRY_PARTICIPATION_ABOVE_LIMIT",
    "EXIT_PARTICIPATION_ABOVE_LIMIT",
}

# Build enough independent-looking rows to exercise confidence and FDR outputs.
expanded_outcomes = []
expanded_panel = []
for index in range(60):
    code = f"{2000 + index:04d}"
    signal_date = pd.Timestamp("2026-02-02") + pd.offsets.BDay(index)
    entry = signal_date + pd.offsets.BDay(1)
    exit_day = entry + pd.offsets.BDay(4)
    expanded_outcomes.append({
        "signal_date": signal_date,
        "entry_price_date": entry,
        "exit_price_date": exit_day,
        "code": code,
        "name": code,
        "sector33": "電気機器" if index % 2 == 0 else "機械",
        "sector_research_priority": "最優先" if index % 2 == 0 else "優先",
        "sector_leader_grade": "A",
        "sector_rotation": "加速",
        "horizon_days": 5,
        "next_session_open": 1000.0,
        "exit_close": 1080.0 + (index % 3),
        "entry_gap_return": 0.01,
        "universe_equal_weight_return": 0.02,
    })
    expanded_panel.extend([
        {"date": entry, "code": code, "raw_trading_value": 2_000_000_000.0},
        {"date": exit_day, "code": code, "raw_trading_value": 2_200_000_000.0},
    ])
expanded_simulations = capacity.simulate_capacity_scenarios(
    pd.DataFrame(expanded_outcomes),
    pd.DataFrame(expanded_panel),
    capitals=(1_000_000,),
    scenarios=(capacity.SCENARIOS[0], capacity.SCENARIOS[2]),
)
summary, rejections = capacity.scenario_statistics(expanded_simulations)
assert len(summary) == 2
assert "fdr_q_value" in summary.columns
assert "scenario_status" in summary.columns
assert (summary["eligible_count"] == 60).all()
assert (summary["average_excess_vs_universe"] > 0).all()
assert set(summary["scenario_status"]).issubset({"PROMISING", "ROBUST", "DEVELOPING"})
frontier = capacity.build_frontier(summary)
assert not frontier.empty
assert len(frontier) <= 2

assert capacity.scenario_status(10, 0.01, 0.005, 0.01, 0.6) == "INSUFFICIENT"
assert capacity.scenario_status(50, -0.01, -0.02, 0.01, 0.6) == "FRAGILE"
assert capacity.scenario_status(60, 0.01, 0.005, 0.20, 0.6) == "DEVELOPING"
assert capacity.scenario_status(60, 0.01, 0.005, 0.05, 0.6) == "PROMISING"
assert capacity.scenario_status(120, 0.01, 0.005, 0.04, 0.6) == "ROBUST"

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    provenance = root / "provenance.json"
    provenance.write_text(json.dumps({
        "evidence_origin": "HISTORICAL_CURRENT_UNIVERSE_BACKFILL",
        "promotion_evidence_allowed": False,
    }), encoding="utf-8")
    result = capacity.write_outputs(
        expanded_simulations,
        summary,
        rejections,
        str(root / "capacity"),
        str(provenance),
    )
    for path in result["paths"].values():
        assert Path(path).exists(), path
    manifest = result["manifest"]
    assert manifest["promotion_evidence_allowed"] is False
    assert manifest["automatic_strategy_change"] is False
    assert manifest["portfolio_simulation"] is False
    assert manifest["impact_model"] == "HEURISTIC_BASE_PLUS_COEFFICIENT_TIMES_SQRT_PARTICIPATION"
    workbook = pd.ExcelFile(result["paths"]["excel"])
    assert {
        "Capacity Summary", "Scenario Summary", "Research Frontier",
        "Rejections", "Scenario Sample",
    }.issubset(workbook.sheet_names)

print("capacity and liquidity scenario validation passed")
