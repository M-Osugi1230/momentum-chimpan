from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import healthy_momentum


def synthetic_frame() -> pd.DataFrame:
    common = {
        "date": "2026-07-21",
        "sector33": "機械",
        "close": 110.0,
        "prev_close": 109.0,
        "recent_high": 113.0,
        "return_5d": 0.04,
        "return_20d": 0.10,
        "return_60d": 0.15,
        "ma20_deviation": 0.04,
        "ma60_deviation": 0.08,
        "volume_ratio": 1.5,
        "trading_value": 500_000_000,
        "above_ma20": True,
        "above_ma60": True,
        "score": 70,
        "data_quality_corporate_action_suspected": False,
        "data_quality_abnormal_price": False,
    }
    return pd.DataFrame([
        {**common, "rank": 20, "code": "1001", "name": "Healthy A"},
        {**common, "rank": 1, "code": "1002", "name": "Falling", "return_5d": -0.02, "above_ma20": False},
        {**common, "rank": 2, "code": "1003", "name": "Overheated", "return_20d": 0.45, "ma20_deviation": 0.22, "volume_ratio": 9.0},
        {**common, "rank": 3, "code": "1004", "name": "Data risk", "data_quality_corporate_action_suspected": True},
        {**common, "rank": 4, "code": "1005", "name": "Illiquid", "trading_value": 20_000_000},
        {**common, "rank": 50, "code": "1006", "name": "Healthy B", "return_20d": 0.08, "ma20_deviation": 0.03, "score": 60},
    ])


def test_policy_is_shadow_only() -> None:
    policy = healthy_momentum.load_policy()
    assert policy["mode"] == "SHADOW_ONLY"
    assert policy["automatic_promotion"] is False
    assert policy["automatic_production_ranking_change"] is False
    assert policy["automatic_paper_change"] is False


def test_triangular_score_rewards_moderate_not_extreme() -> None:
    values = pd.Series([0.03, 0.10, 0.20, 0.40])
    scores = healthy_momentum.triangular_score(values, 0.03, 0.10, 0.20)
    assert scores.iloc[1] == 1.0
    assert scores.iloc[0] == 0.0
    assert scores.iloc[2] == 0.0
    assert scores.iloc[3] == 0.0


def test_two_stage_ranking_excludes_falling_and_overheated() -> None:
    original = synthetic_frame()
    enriched = healthy_momentum.attach(original)
    by_code = enriched.set_index("code")

    assert bool(by_code.loc["1001", "healthy_eligible"]) is True
    assert bool(by_code.loc["1006", "healthy_eligible"]) is True
    assert bool(by_code.loc["1002", "healthy_eligible"]) is False
    assert "FIVE_DAY_NOT_RISING" in by_code.loc["1002", "healthy_exclusion_reasons"]
    assert by_code.loc["1002", "healthy_trend_state"] == "FALLING_OR_BROKEN"
    assert bool(by_code.loc["1003", "healthy_eligible"]) is False
    assert "TWENTY_DAY_OVERHEATED" in by_code.loc["1003", "healthy_exclusion_reasons"]
    assert by_code.loc["1003", "healthy_trend_state"] == "OVERHEATED"
    assert by_code.loc["1004", "healthy_trend_state"] == "DATA_RISK"
    assert by_code.loc["1005", "healthy_trend_state"] == "ILLIQUID"

    eligible = enriched[enriched["healthy_eligible"]].sort_values("healthy_rank")
    assert eligible["healthy_rank"].tolist() == [1, 2]
    assert enriched.loc[enriched["code"] == "1002", "healthy_rank"].isna().all()


def test_production_rank_and_score_are_never_mutated() -> None:
    original = synthetic_frame()
    enriched = healthy_momentum.attach(original)
    pd.testing.assert_series_equal(enriched["rank"], original["rank"], check_names=False)
    pd.testing.assert_series_equal(enriched["score"], original["score"], check_names=False)
    assert enriched["healthy_selection_score"].between(0, 100).all()
    assert set(healthy_momentum.HEALTHY_COLUMNS).issubset(enriched.columns)


def test_latest_shadow_table_contains_only_eligible_rows() -> None:
    table = healthy_momentum.latest_shadow_table(synthetic_frame(), limit=100)
    assert len(table) == 2
    assert table["healthy_eligible"].all()
    assert table["healthy_rank"].tolist() == [1, 2]


if __name__ == "__main__":
    test_policy_is_shadow_only()
    test_triangular_score_rewards_moderate_not_extreme()
    test_two_stage_ranking_excludes_falling_and_overheated()
    test_production_rank_and_score_are_never_mutated()
    test_latest_shadow_table_contains_only_eligible_rows()
    print("healthy momentum tests passed")
