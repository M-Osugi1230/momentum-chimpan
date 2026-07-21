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
            {**common, "rank": 20, "code": "1001", "name": "Balanced A"},
            {**common, "rank": 25, "code": "1002", "name": "Balanced B", "return_5d": 0.03},
            {**common, "rank": 1, "code": "1003", "name": "Stalling", "return_5d": 0.004},
            {**common, "rank": 2, "code": "1004", "name": "Short spike", "return_5d": 0.15, "close": 114.0, "prev_close": 110.0, "recent_high": 115.0},
            {**common, "rank": 3, "code": "1005", "name": "Rank falling", "rank_change": -30},
            {**common, "rank": 4, "code": "1006", "name": "Long trend weak", "return_60d": -0.02},
            {**common, "rank": 5, "code": "1007", "name": "Relative crowded", "return_5d": 0.08},
            {**common, "rank": 6, "code": "1008", "name": "V1 broken", "return_5d": -0.02, "above_ma20": False},
        ]
    )


def test_policy_is_shadow_only_and_conservative() -> None:
    policy = healthy_momentum_v2.load_policy()
    assert policy["mode"] == "SHADOW_ONLY"
    assert policy["automatic_promotion"] is False
    assert policy["automatic_production_ranking_change"] is False
    assert policy["automatic_paper_change"] is False
    assert policy["v1_is_only_eligibility_gate"] is True
    weights = policy["selection_score"]
    assert weights["healthy_v1_weight"] == 0.85
    assert weights["confirmation_weight"] == 0.15
    assert weights["healthy_v1_weight"] + weights["confirmation_weight"] == 1.0
    assert sum(
        component["weight"]
        for component in policy["confirmation_components"].values()
    ) == 100


def test_cautions_do_not_remove_v1_eligible_stocks() -> None:
    original = synthetic_frame()
    enriched = healthy_momentum_v2.attach(original)
    by_code = enriched.set_index("code")

    for code in ["1001", "1002", "1003", "1004", "1005", "1006", "1007"]:
        assert bool(by_code.loc[code, "healthy_eligible"]) is True
        assert bool(by_code.loc[code, "healthy_v2_eligible"]) is True
        assert by_code.loc[code, "healthy_v2_exclusion_reasons"] == ""

    assert "RECENT_PACE_STALLING" in by_code.loc["1003", "healthy_v2_caution_reasons"]
    assert by_code.loc["1003", "healthy_v2_confirmation_state"] == "STALLING_RISK"
    assert "RECENT_PACE_CONCENTRATED" in by_code.loc["1004", "healthy_v2_caution_reasons"]
    assert "CURRENT_DAY_SPIKE" in by_code.loc["1004", "healthy_v2_caution_reasons"]
    assert by_code.loc["1004", "healthy_v2_confirmation_state"] == "SHORT_TERM_SPIKE"
    assert "RANK_DETERIORATION_WATCH" in by_code.loc["1005", "healthy_v2_caution_reasons"]
    assert by_code.loc["1005", "healthy_v2_confirmation_state"] == "RANK_DETERIORATION_WATCH"
    assert bool(by_code.loc["1006", "healthy_v2_eligible"]) is True
    assert "MARKET_RELATIVE_CROWDED" in by_code.loc["1007", "healthy_v2_caution_reasons"]
    assert by_code.loc["1007", "healthy_v2_confirmation_state"] == "CROWDING_RISK"

    assert bool(by_code.loc["1008", "healthy_eligible"]) is False
    assert bool(by_code.loc["1008", "healthy_v2_eligible"]) is False
    assert "V1_" in by_code.loc["1008", "healthy_v2_exclusion_reasons"]
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


def test_v2_rank_is_contiguous_for_v1_eligible_rows() -> None:
    enriched = healthy_momentum_v2.attach(synthetic_frame())
    eligible = enriched[enriched["healthy_v2_eligible"]].sort_values("healthy_v2_rank")
    assert len(eligible) == 7
    assert eligible["healthy_v2_rank"].tolist() == list(range(1, 8))
    rejected = enriched[~enriched["healthy_v2_eligible"]]
    assert rejected["healthy_v2_rank"].isna().all()


def test_latest_shadow_table_keeps_balanced_and_caution_rows() -> None:
    table = healthy_momentum_v2.latest_shadow_table(synthetic_frame(), limit=100)
    assert len(table) == 7
    assert table["healthy_v2_eligible"].all()
    assert table["healthy_v2_rank"].tolist() == list(range(1, 8))
    assert table["healthy_v2_caution_reasons"].astype(str).str.len().gt(0).any()


if __name__ == "__main__":
    test_policy_is_shadow_only_and_conservative()
    test_cautions_do_not_remove_v1_eligible_stocks()
    test_production_and_v1_fields_are_preserved()
    test_v2_rank_is_contiguous_for_v1_eligible_rows()
    test_latest_shadow_table_keeps_balanced_and_caution_rows()
    print("healthy momentum v2 balanced tests passed")
