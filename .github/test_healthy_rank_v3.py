"""Invariant tests for preregistered Healthy Rank v3."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import healthy_rank_v3


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


if __name__ == "__main__":
    test_attach()
    test_missing_component()
    test_v1_ineligible_never_admitted()
    print("Healthy Rank v3 invariant tests passed")
