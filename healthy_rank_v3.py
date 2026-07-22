"""Healthy Rank v3 Equal-Weight Reorder.

Research-only candidate. Healthy v1 eligibility is preserved exactly; only the ordering of
eligible rows is changed. The candidate was pre-registered before opening the 2018-2021
holdout and must not be tuned against holdout outcomes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VERSION = "2026-07-22-healthy-rank-v3-equal-weight-reorder"
POLICY_ID = "healthy-rank-v3-equal-weight-reorder"
COMPONENT_WEIGHT = 0.25
MA20_PEAK = 0.04
MA20_ZERO_DISTANCE = 0.16


def _bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(False, index=frame.index, dtype=bool)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(False)
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def attach(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach v3 score/rank without modifying existing production or Healthy columns."""
    result = frame.copy()
    if "date" not in result or "code" not in result:
        raise ValueError("Healthy Rank v3 requires date and code columns")

    healthy_eligible = _bool_series(result, "healthy_eligible")
    return_5d = _numeric(result, "return_5d")
    return_20d = _numeric(result, "return_20d")
    relative_strength = _numeric(result, "healthy_relative_strength_score")
    ma20_deviation = _numeric(result, "ma20_deviation")

    complete = (
        healthy_eligible
        & return_5d.notna()
        & return_20d.notna()
        & relative_strength.notna()
        & ma20_deviation.notna()
    )

    result["healthy_v3_return_5d_percentile"] = np.nan
    result["healthy_v3_return_20d_percentile"] = np.nan
    result["healthy_v3_relative_strength_percentile"] = np.nan
    result["healthy_v3_ma20_middle_preference"] = np.nan
    result["healthy_v3_selection_score"] = np.nan
    result["healthy_v3_rank"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["healthy_v3_eligible"] = complete
    result["healthy_v3_policy_id"] = POLICY_ID
    result["healthy_v3_version"] = VERSION
    result["healthy_v3_exclusion_reasons"] = ""

    result.loc[~healthy_eligible, "healthy_v3_exclusion_reasons"] = "REJECTED_BY_HEALTHY_V1"
    result.loc[healthy_eligible & ~complete, "healthy_v3_exclusion_reasons"] = "MISSING_V3_COMPONENT"

    eligible = result.loc[complete, ["date", "code"]].copy()
    if eligible.empty:
        return result

    eligible["return_5d_percentile"] = return_5d.loc[complete].groupby(
        result.loc[complete, "date"]
    ).rank(method="average", pct=True)
    eligible["return_20d_percentile"] = return_20d.loc[complete].groupby(
        result.loc[complete, "date"]
    ).rank(method="average", pct=True)
    eligible["relative_strength_percentile"] = relative_strength.loc[complete].groupby(
        result.loc[complete, "date"]
    ).rank(method="average", pct=True)
    eligible["ma20_middle_preference"] = (
        1.0 - (ma20_deviation.loc[complete] - MA20_PEAK).abs() / MA20_ZERO_DISTANCE
    ).clip(0.0, 1.0)
    eligible["selection_score"] = 100.0 * COMPONENT_WEIGHT * (
        eligible["return_5d_percentile"]
        + eligible["return_20d_percentile"]
        + eligible["relative_strength_percentile"]
        + eligible["ma20_middle_preference"]
    )

    eligible = eligible.sort_values(
        ["date", "selection_score", "code"], ascending=[True, False, True]
    )
    eligible["rank"] = eligible.groupby("date", sort=False).cumcount() + 1

    for target, source in (
        ("healthy_v3_return_5d_percentile", "return_5d_percentile"),
        ("healthy_v3_return_20d_percentile", "return_20d_percentile"),
        ("healthy_v3_relative_strength_percentile", "relative_strength_percentile"),
        ("healthy_v3_ma20_middle_preference", "ma20_middle_preference"),
        ("healthy_v3_selection_score", "selection_score"),
    ):
        result.loc[eligible.index, target] = eligible[source]
    result.loc[eligible.index, "healthy_v3_rank"] = eligible["rank"].astype("Int64")
    return result
