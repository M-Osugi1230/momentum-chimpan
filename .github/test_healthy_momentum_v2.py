from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import healthy_momentum_v2


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
        "rank_change": 5,
        "data_quality_corporate_action_suspected": False,
        "data_quality_abnormal_price": False,
    }
    return pd.DataFrame(
        [
            {**common, "rank": 20, "code": "1001", "name": "Confirmed A"},
            {**common, "rank": 25, "code": "1002", "name": "Confirmed B", "return_5d": 0.03},
            {**common, "rank": 1, "code": "1003", "name": "Stalling", "return_5d": 0.005},
            {**common, "rank": 2, "code": "1004", "name": "Spike", "return_5d": 0.10},
            {**common, "rank": 3, "code": "1005", "name": "Rank falling", "rank_change": -30},
            {**common, "rank": 4, "code": "1006", "name": "Long trend weak", "return_60d": -0.02},
            {**common, "rank": 5, "code": "1007", "name": "Relative weak", "return_20d": 0.04, "return_5d": 0.01},
            {**common, "rank": 6, "code": "1008", "name": "V1 broken", "return_5d": -0.02, "above_ma20": False},
        ]
    )


def test_policy_is_shadow_only() -> None:
    policy = healthy_momentum_v2.load_policy()
    assert policy["mode"] == "SHADOW_ONLY"
    assert policy["automatic_promotion"] is False
    assert policy["automatic_production_ranking_change"] is False
    assert policy["automatic_paper_change"] is False
    weights = policy["selection_score"]
    assert weights["healthy_v1_weight"] + weights["confirmation_weight"] == 1.0


def test_recent_pace_and_direction_gate() -> None:
    original = synthetic_frame()
    enriched = healthy_momentum_v2.attach(original)
    by_code = enriched.set_index("code")

    assert bool(by_code.loc["1001", "healthy_v2_eligible"]) is True
    assert bool(by_code.loc["1002", "healthy_v2_eligible"]) is True
    assert bool(by_code.loc["1003", "healthy_v2_eligible"]) is False
    assert "RECENT_PACE_STALLING" in by_code.loc["1003", "healthy_v2_exclusion_reasons"]
    assert by_code.loc["1003", "healthy_v2_confirmation_state"] == "STALLING"
    assert "RECENT_PACE_SPIKE" in by_code.loc["1004", "healthy_v2_exclusion_reasons"]
    assert by_code.loc["1004", "healthy_v2_confirmation_state"] == "SPIKE_RISK"
    assert "RANK_DETERIORATING" in by_code.loc["1005", "healthy_v2_exclusion_reasons"]
    assert by_code.loc["1005", "healthy_v2_confirmation_state"] == "RANK_DETERIORATING"
    assert "SIXTY_DAY_NOT_RISING" in by_code.loc["1006", "healthy_v2_exclusion_reasons"]
    assert "MARKET_RELATIVE_20D_WEAK" in by_code.loc["1007", "healthy_v2_exclusion_reasons"]
    assert by_code.loc["1008", "healthy_v2_confirmation_state"] == "REJECTED_BY_V1"


def test_production_and_v1_fields_are_preserved() -> None:
    original = synthetic_frame()
    enriched = healthy_momentum_v2.attach(original)
    pd.testing.assert_series_equal(enriched["rank"], original["rank"], check_names=False)
    pd.testing.assert_series_equal(enriched["score"], original["score"], check_names=False)
    assert enriched["healthy_selection_score"].notna().all()
    assert enriched["healthy_v2_confirmation_score"].between(0, 100).all()
    assert enriched["healthy_v2_selection_score"].between(0, 100).all()
    assert set(healthy_momentum_v2.V2_COLUMNS).issubset(enriched.columns)


def test_v2_rank_is_contiguous_for_confirmed_rows() -> None:
    enriched = healthy_momentum_v2.attach(synthetic_frame())
    eligible = enriched[enriched["healthy_v2_eligible"]].sort_values("healthy_v2_rank")
    assert eligible["healthy_v2_rank"].tolist() == list(range(1, len(eligible) + 1))
    rejected = enriched[~enriched["healthy_v2_eligible"]]
    assert rejected["healthy_v2_rank"].isna().all()


def test_latest_shadow_table_contains_only_confirmed_rows() -> None:
    table = healthy_momentum_v2.latest_shadow_table(synthetic_frame(), limit=100)
    assert len(table) == 2
    assert table["healthy_v2_eligible"].all()
    assert table["healthy_v2_rank"].tolist() == [1, 2]


if __name__ == "__main__":
    test_policy_is_shadow_only()
    test_recent_pace_and_direction_gate()
    test_production_and_v1_fields_are_preserved()
    test_v2_rank_is_contiguous_for_confirmed_rows()
    test_latest_shadow_table_contains_only_confirmed_rows()
    print("healthy momentum v2 tests passed")
