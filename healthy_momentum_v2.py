"""Healthy Momentum v2 confirmation shadow ranking.

Healthy Momentum v1 removes falling, broken, overheated, illiquid, and data-risk
rows.  V2 adds a second confirmation layer intended to distinguish a continuing
uptrend from a stale 20-day winner or a one-week spike.

The production Momentum rank, Healthy Momentum v1 rank, Daily Action List, and
paper execution remain unchanged.  This module is deterministic, explainable,
research-only, and read-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

import healthy_momentum

DEFAULT_POLICY_PATH = "research/healthy_momentum_v2_policy.yaml"
HEALTHY_MOMENTUM_V2_VERSION = "2026-07-21-healthy-momentum-v2-confirmation-shadow"

V2_COLUMNS = [
    "healthy_v2_version",
    "healthy_v2_eligible",
    "healthy_v2_exclusion_reasons",
    "healthy_v2_confirmation_state",
    "healthy_v2_recent_pace_ratio",
    "healthy_v2_market_relative_5d",
    "healthy_v2_sector_relative_5d",
    "healthy_v2_market_relative_5d_percentile",
    "healthy_v2_sector_relative_5d_percentile",
    "healthy_v2_component_recent_pace",
    "healthy_v2_component_market_relative_5d",
    "healthy_v2_component_sector_relative_5d",
    "healthy_v2_component_rank_direction",
    "healthy_v2_component_current_day_return",
    "healthy_v2_component_drawdown",
    "healthy_v2_component_long_relative_strength",
    "healthy_v2_confirmation_score",
    "healthy_v2_selection_score",
    "healthy_v2_rank",
]


def load_policy(path: str = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"healthy momentum v2 policy not found: {path}")
    parsed = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if parsed.get("mode") != "SHADOW_ONLY":
        raise ValueError("Healthy Momentum v2 must remain SHADOW_ONLY")
    for key in (
        "automatic_promotion",
        "automatic_production_ranking_change",
        "automatic_paper_change",
    ):
        if parsed.get(key) is not False:
            raise ValueError(f"{key} must be false")
    weights = parsed.get("selection_score", {})
    total = float(weights.get("healthy_v1_weight", 0.0)) + float(
        weights.get("confirmation_weight", 0.0)
    )
    if abs(total - 1.0) > 1e-9:
        raise ValueError("Healthy Momentum v2 selection weights must sum to 1.0")
    return parsed


def numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    return healthy_momentum.numeric(frame, column, default)


def normalized_code(value: Any) -> str:
    return healthy_momentum.normalized_code(value)


def report_group_key(frame: pd.DataFrame) -> pd.Series:
    return healthy_momentum.report_group_key(frame)


def triangular_score(values: pd.Series, low: float, peak: float, high: float) -> pd.Series:
    return healthy_momentum.triangular_score(values, low, peak, high)


def bounded_linear_score(
    values: pd.Series,
    low: float,
    high: float,
    missing_score: float = 0.5,
) -> pd.Series:
    if not low < high:
        raise ValueError("bounded linear score requires low < high")
    numbers = pd.to_numeric(values, errors="coerce")
    result = ((numbers - low) / (high - low)).clip(lower=0.0, upper=1.0)
    return result.fillna(float(missing_score))


def recent_pace_ratio(frame: pd.DataFrame) -> pd.Series:
    return_5d = numeric(frame, "return_5d")
    return_20d = numeric(frame, "return_20d")
    ratio = return_5d / return_20d.where(return_20d.gt(0))
    return ratio.replace([np.inf, -np.inf], np.nan)


def attach_short_relative_strength(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach report-local 5-day market and sector relative strength.

    This uses only data known on the report date.  It does not use future returns.
    """
    work = frame.copy()
    group_key = report_group_key(work)
    sector = work.get("sector33", pd.Series("", index=work.index)).fillna("").astype(str)
    return_5d = numeric(work, "return_5d")
    market_median = return_5d.groupby(group_key).transform("median")
    sector_median = return_5d.groupby([group_key, sector], dropna=False).transform("median")
    sector_median = sector_median.where(sector.ne(""), market_median).fillna(market_median)
    work["healthy_v2_market_relative_5d"] = return_5d - market_median
    work["healthy_v2_sector_relative_5d"] = return_5d - sector_median
    work["healthy_v2_market_relative_5d_percentile"] = (
        work["healthy_v2_market_relative_5d"]
        .groupby(group_key)
        .rank(method="average", pct=True)
        .fillna(0.5)
    )
    work["healthy_v2_sector_relative_5d_percentile"] = (
        work["healthy_v2_sector_relative_5d"]
        .groupby([group_key, sector], dropna=False)
        .rank(method="average", pct=True)
        .fillna(0.5)
    )
    return work


def eligibility_reasons(frame: pd.DataFrame, policy: dict[str, Any]) -> pd.Series:
    cfg = policy["confirmation_eligibility"]
    v1_reasons = frame.get(
        "healthy_exclusion_reasons", pd.Series("", index=frame.index, dtype="object")
    ).fillna("").astype(str)
    return_60d = numeric(frame, "return_60d")
    pace = numeric(frame, "healthy_v2_recent_pace_ratio")
    market_relative_20d = numeric(frame, "healthy_market_relative_20d")
    sector_relative_20d = numeric(frame, "healthy_sector_relative_20d")
    rank_change = numeric(frame, "rank_change")
    day_return = numeric(frame, "healthy_current_day_return")
    drawdown = numeric(frame, "healthy_drawdown_from_recent_high")

    reasons: list[list[str]] = []
    for value in v1_reasons:
        reasons.append([f"V1_{part}" for part in value.split("|") if part])

    def add(mask: pd.Series, reason: str) -> None:
        for position in np.flatnonzero(mask.fillna(False).to_numpy()):
            reasons[position].append(reason)

    add(return_60d.le(float(cfg["min_return_60d"])) | return_60d.isna(), "SIXTY_DAY_NOT_RISING")
    add(pace.lt(float(cfg["min_recent_pace_ratio"])) | pace.isna(), "RECENT_PACE_STALLING")
    add(pace.gt(float(cfg["max_recent_pace_ratio"])), "RECENT_PACE_SPIKE")
    add(
        market_relative_20d.lt(float(cfg["min_market_relative_20d"]))
        | market_relative_20d.isna(),
        "MARKET_RELATIVE_20D_WEAK",
    )
    add(
        sector_relative_20d.lt(float(cfg["min_sector_relative_20d"]))
        | sector_relative_20d.isna(),
        "SECTOR_RELATIVE_20D_WEAK",
    )
    add(
        rank_change.notna() & rank_change.lt(float(cfg["min_rank_change"])),
        "RANK_DETERIORATING",
    )
    add(
        day_return.lt(float(cfg["min_current_day_return"])) | day_return.isna(),
        "CURRENT_DAY_CONFIRMATION_FAILED",
    )
    add(
        drawdown.lt(float(cfg["min_drawdown_from_recent_high"])) | drawdown.isna(),
        "RECENT_HIGH_CONFIRMATION_FAILED",
    )
    return pd.Series(["|".join(items) for items in reasons], index=frame.index, dtype="object")


def confirmation_state(reasons: pd.Series) -> pd.Series:
    def classify(value: Any) -> str:
        found = {part for part in str(value or "").split("|") if part}
        if not found:
            return "CONFIRMED_RISING"
        if any(part.startswith("V1_") for part in found):
            return "REJECTED_BY_V1"
        if "RANK_DETERIORATING" in found:
            return "RANK_DETERIORATING"
        if "RECENT_PACE_STALLING" in found:
            return "STALLING"
        if "RECENT_PACE_SPIKE" in found:
            return "SPIKE_RISK"
        if {"MARKET_RELATIVE_20D_WEAK", "SECTOR_RELATIVE_20D_WEAK"} & found:
            return "RELATIVE_WEAK"
        if {"CURRENT_DAY_CONFIRMATION_FAILED", "RECENT_HIGH_CONFIRMATION_FAILED"} & found:
            return "SHORT_TERM_BREAKDOWN"
        return "UNCONFIRMED"

    return reasons.map(classify)


def attach(frame: pd.DataFrame, policy: dict[str, Any] | None = None) -> pd.DataFrame:
    """Attach Healthy Momentum v2 fields without mutating production or v1 ranks."""
    policy = load_policy() if policy is None else policy
    work = healthy_momentum.attach(frame)
    work = attach_short_relative_strength(work)
    work["code"] = work.get("code", pd.Series("", index=work.index)).map(normalized_code)
    work["healthy_v2_recent_pace_ratio"] = recent_pace_ratio(work)

    components = policy["confirmation_components"]
    pace_cfg = components["recent_pace_ratio"]
    work["healthy_v2_component_recent_pace"] = (
        triangular_score(
            work["healthy_v2_recent_pace_ratio"],
            float(pace_cfg["low"]),
            float(pace_cfg["peak"]),
            float(pace_cfg["high"]),
        )
        * float(pace_cfg["weight"])
    ).round(4)

    market_cfg = components["market_relative_5d"]
    work["healthy_v2_component_market_relative_5d"] = (
        numeric(work, "healthy_v2_market_relative_5d_percentile", 0.5).fillna(0.5)
        * float(market_cfg["weight"])
    ).round(4)

    sector_cfg = components["sector_relative_5d"]
    work["healthy_v2_component_sector_relative_5d"] = (
        numeric(work, "healthy_v2_sector_relative_5d_percentile", 0.5).fillna(0.5)
        * float(sector_cfg["weight"])
    ).round(4)

    rank_cfg = components["rank_direction"]
    work["healthy_v2_component_rank_direction"] = (
        bounded_linear_score(
            numeric(work, "rank_change"),
            float(rank_cfg["low"]),
            float(rank_cfg["high"]),
            float(rank_cfg.get("missing_score", 0.5)),
        )
        * float(rank_cfg["weight"])
    ).round(4)

    day_cfg = components["current_day_return"]
    work["healthy_v2_component_current_day_return"] = (
        triangular_score(
            numeric(work, "healthy_current_day_return"),
            float(day_cfg["low"]),
            float(day_cfg["peak"]),
            float(day_cfg["high"]),
        )
        * float(day_cfg["weight"])
    ).round(4)

    drawdown_cfg = components["drawdown_from_recent_high"]
    work["healthy_v2_component_drawdown"] = (
        triangular_score(
            numeric(work, "healthy_drawdown_from_recent_high"),
            float(drawdown_cfg["low"]),
            float(drawdown_cfg["peak"]),
            float(drawdown_cfg["high"]),
        )
        * float(drawdown_cfg["weight"])
    ).round(4)

    long_cfg = components["long_relative_strength"]
    work["healthy_v2_component_long_relative_strength"] = (
        numeric(work, "healthy_relative_strength_score", 50.0).fillna(50.0)
        / 100.0
        * float(long_cfg["weight"])
    ).round(4)

    component_columns = [
        "healthy_v2_component_recent_pace",
        "healthy_v2_component_market_relative_5d",
        "healthy_v2_component_sector_relative_5d",
        "healthy_v2_component_rank_direction",
        "healthy_v2_component_current_day_return",
        "healthy_v2_component_drawdown",
        "healthy_v2_component_long_relative_strength",
    ]
    work["healthy_v2_confirmation_score"] = (
        work[component_columns].sum(axis=1).round(4).clip(lower=0.0, upper=100.0)
    )

    reasons = eligibility_reasons(work, policy)
    work["healthy_v2_exclusion_reasons"] = reasons
    work["healthy_v2_eligible"] = reasons.eq("")
    work["healthy_v2_confirmation_state"] = confirmation_state(reasons)

    selection = policy["selection_score"]
    work["healthy_v2_selection_score"] = (
        numeric(work, "healthy_selection_score", 0.0).fillna(0.0)
        * float(selection["healthy_v1_weight"])
        + work["healthy_v2_confirmation_score"]
        * float(selection["confirmation_weight"])
    ).round(4)

    group_key = report_group_key(work)
    rank = pd.Series(pd.NA, index=work.index, dtype="Int64")
    for _, positions in work.groupby(group_key, sort=False).groups.items():
        subset = work.loc[list(positions)]
        subset = subset[subset["healthy_v2_eligible"]].sort_values(
            [
                "healthy_v2_selection_score",
                "healthy_v2_confirmation_score",
                "healthy_selection_score",
                "trading_value",
                "code",
            ],
            ascending=[False, False, False, False, True],
            na_position="last",
        )
        rank.loc[subset.index] = pd.Series(
            range(1, len(subset) + 1), index=subset.index, dtype="Int64"
        )
    work["healthy_v2_rank"] = rank
    work["healthy_v2_version"] = HEALTHY_MOMENTUM_V2_VERSION
    return work


def latest_shadow_table(
    frame: pd.DataFrame,
    limit: int = 100,
    policy: dict[str, Any] | None = None,
) -> pd.DataFrame:
    enriched = attach(frame, policy)
    if enriched.empty:
        return enriched
    if "date" in enriched.columns:
        dates = pd.to_datetime(enriched["date"], errors="coerce")
        if dates.notna().any():
            enriched = enriched[dates == dates.max()].copy()
    return (
        enriched[enriched["healthy_v2_eligible"]]
        .sort_values("healthy_v2_rank")
        .head(limit)
        .reset_index(drop=True)
    )


def exclusion_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if "healthy_v2_exclusion_reasons" not in frame.columns:
        frame = attach(frame)
    exploded = (
        frame.loc[~frame["healthy_v2_eligible"], ["healthy_v2_exclusion_reasons"]]
        .assign(reason=lambda value: value["healthy_v2_exclusion_reasons"].str.split("|"))
        .explode("reason")
    )
    exploded = exploded[exploded["reason"].fillna("").ne("")]
    if exploded.empty:
        return pd.DataFrame(columns=["reason", "count", "ratio"])
    counts = exploded["reason"].value_counts().rename_axis("reason").reset_index(name="count")
    counts["ratio"] = counts["count"] / len(frame)
    return counts
