from pathlib import Path
from tempfile import TemporaryDirectory
import json
import math
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import portfolio_research as portfolio


assert portfolio.floor_lot(1099) == 1000
assert portfolio.floor_lot(99) == 0
assert np.isclose(
    portfolio.dynamic_impact_bps(10_000_000, 1_000_000_000),
    portfolio.capacity_analysis.impact_bps(0.01),
)
assert portfolio.entry_rejection(
    portfolio.PortfolioScenario("gap3", 0.03, 0.0, 0.01),
    0.04,
    1_000_000_000,
) == "POSITIVE_GAP_ABOVE_LIMIT"
assert portfolio.entry_rejection(
    portfolio.PortfolioScenario("liquidity", None, 500_000_000, 0.01),
    0.0,
    100_000_000,
) == "ENTRY_TRADING_VALUE_BELOW_MINIMUM"

position = {
    "code": "1001",
    "fixed_stop": 92.0,
    "target_price": 115.0,
    "highest_close": 100.0,
    "holding_sessions": 2,
}
both = portfolio.resolve_exit(
    position,
    {
        "adjusted_open": 90.0,
        "adjusted_high": 120.0,
        "adjusted_low": 85.0,
        "adjusted_close": 100.0,
    },
    False,
    set(),
)
assert both == ("STOP_CONSERVATIVE", 90.0)

target = portfolio.resolve_exit(
    position,
    {
        "adjusted_open": 116.0,
        "adjusted_high": 120.0,
        "adjusted_low": 100.0,
        "adjusted_close": 118.0,
    },
    False,
    set(),
)
assert target == ("TARGET", 116.0)

time_position = dict(position, holding_sessions=portfolio.MAX_HOLDING_SESSIONS - 1)
time_exit = portfolio.resolve_exit(
    time_position,
    {
        "adjusted_open": 100.0,
        "adjusted_high": 105.0,
        "adjusted_low": 95.0,
        "adjusted_close": 101.0,
    },
    False,
    set(),
)
assert time_exit == ("TIME_EXIT", 101.0)

# Build a small daily market. Signals are known on Friday and can enter only
# on the following business-day open.
dates = pd.bdate_range("2026-01-05", periods=25)
price_rows = []
for index, day in enumerate(dates):
    specs = {
        "1001": ("電気機器", 100.0, 1_500_000_000.0),
        "1002": ("電気機器", 106.0, 1_000_000_000.0),
        "1003": ("電気機器", 100.0, 800_000_000.0),
        "2001": ("銀行業", 200.0, 2_000_000_000.0),
        "3001": ("機械", 5000.0, 100_000_000.0),
    }
    for code, (sector, first_open, trading_value) in specs.items():
        open_price = first_open + index * 0.2
        high = open_price * 1.02
        low = open_price * 0.98
        close = open_price * 1.005
        if code == "1001" and index == 1:
            open_price = 90.0
            high = 120.0
            low = 85.0
            close = 100.0
        if code == "2001" and index == 3:
            open_price = 210.0
            high = 235.0
            low = 205.0
            close = 230.0
        price_rows.append({
            "date": day,
            "code": code,
            "name": code,
            "sector33": sector,
            "adjusted_open": open_price,
            "adjusted_high": high,
            "adjusted_low": low,
            "adjusted_close": close,
            "raw_close": close,
            "volume": max(int(trading_value / max(close, 1)), 1),
            "raw_trading_value": trading_value,
        })
prices = pd.DataFrame(price_rows)

signals = pd.DataFrame([
    {
        "signal_date": "2026-01-02",
        "code": "1001",
        "name": "Stop First",
        "sector33": "電気機器",
        "entry_close": 100.0,
        "sector_research_priority": "最優先",
        "sector_leader_score": 95,
        "sector_rotation": "加速",
    },
    {
        "signal_date": "2026-01-02",
        "code": "1002",
        "name": "Gap Chase",
        "sector33": "電気機器",
        "entry_close": 100.0,
        "sector_research_priority": "優先",
        "sector_leader_score": 90,
        "sector_rotation": "主導",
    },
    {
        "signal_date": "2026-01-02",
        "code": "1003",
        "name": "Sector Capacity",
        "sector33": "電気機器",
        "entry_close": 100.0,
        "sector_research_priority": "優先",
        "sector_leader_score": 80,
        "sector_rotation": "改善",
    },
    {
        "signal_date": "2026-01-02",
        "code": "2001",
        "name": "Target",
        "sector33": "銀行業",
        "entry_close": 200.0,
        "sector_research_priority": "最優先",
        "sector_leader_score": 92,
        "sector_rotation": "加速",
    },
    {
        "signal_date": "2026-01-02",
        "code": "3001",
        "name": "Illiquid Expensive",
        "sector33": "機械",
        "entry_close": 5000.0,
        "sector_research_priority": "優先",
        "sector_leader_score": 85,
        "sector_rotation": "改善",
    },
])
signals["signal_date"] = pd.to_datetime(signals["signal_date"])

events = portfolio.build_entry_events(signals, prices)
assert len(events) == len(signals)
assert (events["entry_date"] > events["signal_date"]).all()
assert events.iloc[0]["code"] == "1001"

baseline = portfolio.simulate_scenario(
    signals,
    prices,
    portfolio.PortfolioScenario("baseline", None, 0.0, 0.01),
)
assert not baseline["equity"].empty
assert not baseline["trades"].empty
assert (baseline["equity"]["cash"] >= -1e-6).all()
assert int(baseline["equity"]["open_positions"].max()) <= portfolio.MAX_POSITIONS
assert baseline["metrics"]["maximum_positions"] <= portfolio.MAX_POSITIONS
assert baseline["metrics"]["turnover_ratio"] > 0
assert baseline["metrics"]["final_equity"] > 0
assert baseline["metrics"]["benchmark_total_return"] is not None

stop_trade = baseline["trades"][baseline["trades"]["code"] == "1001"].iloc[0]
assert stop_trade["entry_date"] == "2026-01-05"
assert stop_trade["exit_date"] == "2026-01-06"
assert stop_trade["exit_reason"] == "STOP_CONSERVATIVE"
assert np.isclose(stop_trade["raw_exit_price"], 90.0)
assert int(stop_trade["quantity"]) % portfolio.LOT_SIZE == 0
assert stop_trade["entry_participation"] <= 0.01 + 1e-12

# Position weights are constrained at entry. Market drift may move closing
# weights, but the initial raw notional must remain within 12% of capital.
assert stop_trade["quantity"] * stop_trade["raw_entry_price"] <= (
    portfolio.INITIAL_CAPITAL * portfolio.MAX_POSITION_WEIGHT + stop_trade["raw_entry_price"] * portfolio.LOT_SIZE
)

all_results = portfolio.run_all_scenarios(signals, prices)
assert set(all_results["metrics"]["scenario"]) == {
    "baseline", "no_gap_chase_3pct", "gap3_minimum_500m"
}
assert len(all_results["metrics"]) == 3

strict_skips = all_results["skips"][
    all_results["skips"]["scenario"] == "no_gap_chase_3pct"
]
assert (
    (strict_skips["code"] == "1002")
    & (strict_skips["reason"] == "POSITIVE_GAP_ABOVE_LIMIT")
).any()
liquidity_skips = all_results["skips"][
    all_results["skips"]["scenario"] == "gap3_minimum_500m"
]
assert (
    (liquidity_skips["code"] == "3001")
    & (liquidity_skips["reason"] == "ENTRY_TRADING_VALUE_BELOW_MINIMUM")
).any()

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    provenance = root / "provenance.json"
    provenance.write_text(json.dumps({
        "evidence_origin": "HISTORICAL_CURRENT_UNIVERSE_BACKFILL",
        "promotion_evidence_allowed": False,
    }), encoding="utf-8")
    output = portfolio.write_outputs(
        all_results,
        str(provenance),
        str(root / "portfolio"),
    )
    for path in output["paths"].values():
        assert Path(path).exists(), path
    manifest = output["manifest"]
    assert manifest["promotion_evidence_allowed"] is False
    assert manifest["automatic_strategy_change"] is False
    assert manifest["production_state_mutations"] == []
    assert manifest["entry_model"] == "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"
    assert manifest["intraday_ambiguity_policy"] == "STOP_FIRST_CONSERVATIVE"
    workbook = pd.ExcelFile(output["paths"]["excel"])
    assert {
        "Research Summary", "Scenario Metrics", "Trades", "Equity Curve",
        "Skipped Entries",
    }.issubset(workbook.sheet_names)

print("execution-aware portfolio research validation passed")
