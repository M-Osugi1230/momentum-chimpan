"""Invariant tests for preregistered Healthy Rank v3 and its holdout data contract."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import detailed_oos_path
import healthy_rank_v3
import run_detailed_historical_oos as strict_outcomes


def fixture() -> pd.DataFrame:
    records = []
    for date in pd.to_datetime(["2020-01-10", "2020-01-17"]):
        for index in range(1, 13):
            records.append(
                {
                    "date": date,
                    "code": str(index).zfill(4),
                    "healthy_eligible": index <= 10,
                    "return_5d": index / 100,
                    "return_20d": index / 50,
                    "healthy_relative_strength_score": index * 5,
                    "ma20_deviation": 0.04 + abs(index - 6) / 100,
                    "rank": 13 - index,
                    "score": 100 - index,
                    "healthy_rank": 11 - index if index <= 10 else np.nan,
                    "healthy_selection_score": 90 - index,
                }
            )
    return pd.DataFrame(records)


def test_attach() -> None:
    source = fixture()
    original = source.copy(deep=True)
    result = healthy_rank_v3.attach(source)
    assert source.equals(original)
    assert result["healthy_v3_policy_id"].eq(healthy_rank_v3.POLICY_ID).all()
    assert result["healthy_v3_version"].eq(healthy_rank_v3.VERSION).all()
    assert result["healthy_v3_eligible"].sum() == 20
    assert result.loc[result["healthy_v3_eligible"], "healthy_eligible"].all()
    assert result.loc[~result["healthy_eligible"], "healthy_v3_rank"].isna().all()
    assert result.loc[result["healthy_v3_eligible"], "healthy_v3_rank"].notna().all()
    assert result.loc[result["healthy_v3_eligible"], "healthy_v3_selection_score"].between(0, 100).all()
    counts = result[result["healthy_v3_eligible"]].groupby("date")["healthy_v3_rank"].nunique()
    assert counts.eq(10).all()
    rerun = healthy_rank_v3.attach(source)
    columns = ["date", "code", "healthy_v3_rank", "healthy_v3_selection_score"]
    pd.testing.assert_frame_equal(result[columns], rerun[columns])


def test_missing_component() -> None:
    source = fixture()
    source.loc[source["code"] == "0003", "return_20d"] = np.nan
    result = healthy_rank_v3.attach(source)
    missing = result[source["code"] == "0003"]
    assert not missing["healthy_v3_eligible"].any()
    assert missing["healthy_v3_rank"].isna().all()
    assert missing["healthy_v3_exclusion_reasons"].eq("MISSING_V3_COMPONENT").all()


def test_v1_ineligible_never_admitted() -> None:
    source = fixture()
    source.loc[source["code"] == "0011", ["return_5d", "return_20d", "healthy_relative_strength_score", "ma20_deviation"]] = [0.99, 1.99, 100, 0.04]
    result = healthy_rank_v3.attach(source)
    row = result[result["code"] == "0011"]
    assert not row["healthy_v3_eligible"].any()
    assert row["healthy_v3_exclusion_reasons"].eq("REJECTED_BY_HEALTHY_V1").all()


def price_rows(dates: list[str], volumes: list[int], closes: list[float]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "adjusted_open": closes,
            "adjusted_high": np.asarray(closes) * 1.01,
            "adjusted_low": np.asarray(closes) * 0.99,
            "adjusted_close": closes,
            "volume": volumes,
        }
    )
    return frame


def test_positive_volume_session_definition() -> None:
    # A zero-volume row is not an executable stock session and must be skipped, not used
    # to invalidate an otherwise executable multi-session outcome.
    prices = price_rows(
        ["2019-04-26", "2019-04-29", "2019-05-07", "2019-05-08"],
        [1000, 0, 1200, 1300],
        [100.0, 999.0, 102.0, 103.0],
    )
    result = strict_outcomes.one_outcome_strict(prices, pd.Timestamp("2019-04-25"), 2)
    assert result is not None
    assert result["entry_date"] == pd.Timestamp("2019-04-26")
    assert result["exit_date"] == pd.Timestamp("2019-05-07")
    assert result["max_session_gap_days"] == 11
    assert result["session_definition"] == "POSITIVE_VOLUME_OBSERVATIONS_ONLY"
    assert np.isclose(result["gross_return"], 0.02)


def test_market_closure_and_suspension_boundary() -> None:
    assert strict_outcomes.MAX_SESSION_GAP_DAYS == 14
    assert detailed_oos_path.MAX_SESSION_GAP_DAYS == 14
    accepted = price_rows(
        ["2019-04-26", "2019-05-07"],
        [1000, 1000],
        [100.0, 101.0],
    )
    rejected = price_rows(
        ["2019-04-26", "2019-05-13"],
        [1000, 1000],
        [100.0, 101.0],
    )
    assert strict_outcomes.one_outcome_strict(accepted, pd.Timestamp("2019-04-25"), 2) is not None
    assert strict_outcomes.one_outcome_strict(rejected, pd.Timestamp("2019-04-25"), 2) is None


if __name__ == "__main__":
    test_attach()
    test_missing_component()
    test_v1_ineligible_never_admitted()
    test_positive_volume_session_definition()
    test_market_closure_and_suspension_boundary()
    print("Healthy Rank v3 invariant tests passed")
