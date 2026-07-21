from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analyze_historical_oos as analysis
import historical_oos_backfill
import run_historical_oos_analysis as runner


def synthetic_prices() -> pd.DataFrame:
    rows = []
    for code_index, code in enumerate(["1001", "1002", "1003", "1004", "1005", "1006"]):
        base = 100.0 + code_index * 10
        for day_index, day in enumerate(pd.date_range("2025-01-02", periods=12, freq="B")):
            price = base + day_index * (1 + code_index * 0.1)
            rows.append(
                {
                    "date": day,
                    "code": code,
                    "name": f"Stock {code}",
                    "sector33": "機械" if code_index < 3 else "サービス業",
                    "adjusted_open": price,
                    "adjusted_high": price * 1.02,
                    "adjusted_low": price * 0.98,
                    "adjusted_close": price * 1.005,
                    "raw_close": price,
                    "volume": 1_000_000,
                    "raw_trading_value": price * 1_000_000,
                }
            )
    return pd.DataFrame(rows)


def synthetic_ranking() -> pd.DataFrame:
    rows = []
    for date_index, day in enumerate(pd.to_datetime(["2025-01-03", "2025-01-08", "2025-01-13"])):
        for rank, code in enumerate(["1001", "1002", "1003", "1004", "1005", "1006"], start=1):
            rows.append(
                {
                    "date": day,
                    "code": code,
                    "name": f"Stock {code}",
                    "sector33": "機械" if rank <= 3 else "サービス業",
                    "rank": rank,
                    "score": 80 - rank,
                    "return_5d": 0.03 + rank * 0.001,
                    "return_20d": 0.08 + rank * 0.002,
                    "return_60d": 0.12 + rank * 0.002,
                    "ma20_deviation": 0.03 + rank * 0.002,
                    "ma60_deviation": 0.06 + rank * 0.002,
                    "volume_ratio": 1.2 + rank * 0.05,
                    "trading_value": 500_000_000,
                    "above_ma20": True,
                    "above_ma60": True,
                    "close": 110 + rank + date_index,
                    "prev_close": 109 + rank + date_index,
                    "recent_high": 114 + rank + date_index,
                    "rank_change": 0,
                    "data_quality_corporate_action_suspected": False,
                    "data_quality_abnormal_price": False,
                }
            )
    return pd.DataFrame(rows)


def test_next_session_entry_and_inclusive_horizon() -> None:
    prices = synthetic_prices()
    code_prices = prices[prices["code"] == "1001"].sort_values("date").reset_index(drop=True)
    signal = pd.Timestamp("2025-01-03")
    outcome_1 = analysis.one_outcome(code_prices, signal, 1)
    outcome_3 = analysis.one_outcome(code_prices, signal, 3)
    assert outcome_1 is not None and outcome_3 is not None
    assert outcome_1["entry_date"] == pd.Timestamp("2025-01-06")
    assert outcome_1["exit_date"] == pd.Timestamp("2025-01-06")
    assert outcome_3["exit_date"] == pd.Timestamp("2025-01-08")
    assert outcome_1["entry_date"] > signal


def test_selection_columns_and_methods() -> None:
    ranking = analysis.attach_methods(synthetic_ranking())
    lookup = analysis.price_lookup(synthetic_prices())
    outcomes = analysis.build_universe_outcomes(ranking, lookup, [1, 3])
    events = runner.select_method_events_fixed(ranking, outcomes, top_limit=5)
    assert not events.empty
    assert set(events["method"]) == set(analysis.METHODS)
    assert not events.columns.duplicated().any()
    assert events["entry_date"].gt(events["signal_date"]).all()
    assert events["method_rank"].le(5).all()
    assert events.groupby(["method", "signal_date", "code"])["horizon_sessions"].nunique().eq(2).all()


def test_summary_sample_types() -> None:
    ranking = analysis.attach_methods(synthetic_ranking())
    outcomes = analysis.build_universe_outcomes(
        ranking, analysis.price_lookup(synthetic_prices()), [1, 3]
    )
    events = runner.select_method_events_fixed(ranking, outcomes, top_limit=5)
    summary = analysis.summarize_events(events, [3, 5])
    assert set(summary["sample_type"]) == {
        "ALL_EVENTS",
        "FIRST_PICK_PER_CODE",
        "NON_OVERLAPPING_PER_CODE",
    }
    assert set(summary["method"]) == set(analysis.METHODS)
    assert summary["observations"].gt(0).all()


def test_stratified_sample_is_deterministic() -> None:
    members = [
        historical_oos_backfill.historical_backfill.UniverseMember(
            code=str(1000 + index),
            name=f"Stock {index}",
            market="Prime",
            sector33="機械" if index % 2 == 0 else "サービス業",
        )
        for index in range(20)
    ]
    first = historical_oos_backfill.stable_stratified_limit(members, 8)
    second = historical_oos_backfill.stable_stratified_limit(list(reversed(members)), 8)
    assert [member.code for member in first] == [member.code for member in second]
    assert len(first) == 8
    assert {member.sector33 for member in first} == {"機械", "サービス業"}


if __name__ == "__main__":
    test_next_session_entry_and_inclusive_horizon()
    test_selection_columns_and_methods()
    test_summary_sample_types()
    test_stratified_sample_is_deterministic()
    print(json.dumps({"status": "ok", "tests": 4}))
