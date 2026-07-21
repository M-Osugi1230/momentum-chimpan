"""Healthy Momentum v2 balanced shadow ranking.

Healthy Momentum v1 remains the only eligibility gate. V2 adds a bounded, soft
confirmation overlay that prefers balanced continuation and penalizes crowded extremes
without excluding otherwise healthy v1 candidates.

The initial v2 hard-confirmation candidate was rejected after it underperformed v1 in all
nine historical comparisons. This revision intentionally removes the harmful hard gates,
keeps every v1-eligible stock rankable, and limits the overlay to 15% of the final score.

Production Momentum rank, Healthy Momentum v1 rank, Daily Action List, email, site, paper
execution, and live execution remain unchanged. This module is deterministic,
explainable, research-only, and read-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

import healthy_momentum

DEFAULT_POLICY_PATH = "research/healthy_momentum_v2_policy.yaml"
HEALTHY_MOMENTUM_V2_VERSION = "2026-07-21-healthy-momentum-v2-balanced-shadow"

V2_COLUMNS = [
    "healthy_v2_version",
    "healthy_v2_eligible",
    "healthy_v2_exclusion_reasons",
    "healthy_v2_caution_reasons",
    "healthy_v2_confirmation_state",
    "healthy_v2_recent_pace_ratio",
    "healthy_v2_market_relative_5d",
    "healthy_v2_sector_relative_5d",
    "healthy_v2_market_relative_5d_percentile",
    "healthy_v2_sector_relative_5d_percentile",
    "healthy_v2_component_recent_pace",
    "healthy_v2_component_market_relative_5d",
    "healthy_v2_component_sector_relative_5d",
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
    component_weight = sum(
        float(config.get("weight", 0.0))
        for config in parsed.get("confirmation_components", {}).values()
    )
    if abs(component_weight - 100.0) > 1e-9:
        raise ValueError("Healthy Momentum v2 confirmation component weights must sum to 100")
    return parsed


def numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    return healthy_momentum.numeric(frame, column, default)


def normalized_code(value: Any) -> str:
    return healthy_momentum.normalized_code(value)


def report_group_key(frame: pd.DataFrame) -> pd.Series:
    return healthy_momentum.report_group_key(frame)


def triangular_score(values: pd.Series, low: float, peak: float, high: float) -> pd.Series:
    return healthy_momentum.triangular_score(values, low, peak, high)


def recent_pace_ratio(frame: pd.DataFrame) -> pd.Series:
    return_5d = numeric(frame, "return_5d")
    return_20d = numeric(frame, "return_20d")
    ratio = return_5d / return_20d.where(return_20d.gt(0))
    return ratio.replace([np.inf, -np.inf], np.nan)


def attach_short_relative_strength(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach report-local 5-day market and sector relative strength.

    Only information available at the report close is used. The percentile is calculated
    independently for each report, and the sector percentile is calculated independently
    within each report and JPX 33-sector group.
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


def v1_exclusion_reasons(frame: pd.DataFrame) -> pd.Series:
    source = frame.get(
        "healthy_exclusion_reasons", pd.Series("", index=frame.index, dtype="object")
    ).fillna("").astype(str)
    return source.map(
        lambda value: "|".join(f"V1_{part}" for part in value.split("|") if part)
    )


def caution_reasons(frame: pd.DataFrame, policy: dict[str, Any]) -> pd.Series:
    """Return diagnostic cautions without changing v1 eligibility.

    A caution may lower the bounded confirmation score or explain why a stock is not near
    the top of v2, but it never removes a v1-eligible stock from the v2 universe.
    """
    cfg = policy["confirmation_cautions"]
    v1_eligible = frame.get(
        "healthy_eligible", pd.Series(False, index=frame.index, dtype=bool)
    ).fillna(False).astype(bool)
    pace = numeric(frame, "healthy_v2_recent_pace_ratio")
    market_percentile = numeric(frame, "healthy_v2_market_relative_5d_percentile")
    sector_percentile = numeric(frame, "healthy_v2_sector_relative_5d_percentile")
    long_relative = numeric(frame, "healthy_relative_strength_score")
    rank_change = numeric(frame, "rank_change")
    day_return = numeric(frame, "healthy_current_day_return")
    drawdown = numeric(frame, "healthy_drawdown_from_recent_high")

    reasons: list[list[str]] = [[] for _ in range(len(frame))]

    def add(mask: pd.Series, reason: str) -> None:
        active = mask.fillna(False) & v1_eligible
        for position in np.flatnonzero(active.to_numpy()):
            reasons[position].append(reason)

    add(pace.lt(float(cfg["min_recent_pace_ratio"])), "RECENT_PACE_STALLING")
    add(pace.gt(float(cfg["max_recent_pace_ratio"])), "RECENT_PACE_CONCENTRATED")
    add(
        market_percentile.gt(float(cfg["max_market_relative_5d_percentile"])),
        "MARKET_RELATIVE_CROWDED",
    )
    add(
        sector_percentile.gt(float(cfg["max_sector_relative_5d_percentile"])),
        "SECTOR_RELATIVE_CROWDED",
    )
    add(
        long_relative.gt(float(cfg["max_long_relative_strength_score"])),
        "LONG_RELATIVE_CROWDED",
    )
    add(day_return.lt(float(cfg["min_current_day_return"])), "CURRENT_DAY_WEAKNESS")
    add(day_return.gt(float(cfg["max_current_day_return"])), "CURRENT_DAY_SPIKE")
    add(drawdown.lt(float(cfg["min_drawdown_from_recent_high"])), "RECENT_HIGH_WEAKNESS")
    add(
        rank_change.notna() & rank_change.lt(float(cfg["min_rank_change"])),
        "RANK_DETERIORATION_WATCH",
    )
    return pd.Series(["|".join(items) for items in reasons], index=frame.index, dtype="object")


def confirmation_state(exclusions: pd.Series, cautions: pd.Series) -> pd.Series:
    def classify(pair: tuple[Any, Any]) -> str:
        exclusion_value, caution_value = pair
        excluded = {part for part in str(exclusion_value or "").split("|") if part}
        found = {part for part in str(caution_value or "").split("|") if part}
        if excluded:
            return "REJECTED_BY_V1"
        if not found:
            return "BALANCED_RISING"
        if len(found) >= 3:
            return "MIXED_CONFIRMATION"
        if "RECENT_PACE_STALLING" in found:
            return "STALLING_RISK"
        if {"RECENT_PACE_CONCENTRATED", "CURRENT_DAY_SPIKE"} & found:
            return "SHORT_TERM_SPIKE"
        if {
            "MARKET_RELATIVE_CROWDED",
            "SECTOR_RELATIVE_CROWDED",
            "LONG_RELATIVE_CROWDED",
        } & found:
            return "CROWDING_RISK"
        if {"CURRENT_DAY_WEAKNESS", "RECENT_HIGH_WEAKNESS"} & found:
            return "SHORT_TERM_WEAKNESS"
        if "RANK_DETERIORATION_WATCH" in found:
            return "RANK_DETERIORATION_WATCH"
        return "MIXED_CONFIRMATION"

    return pd.Series(
        [classify(pair) for pair in zip(exclusions, cautions)],
        index=exclusions.index,
        dtype="object",
    )


def component_score(source: pd.Series, config: dict[str, Any]) -> pd.Series:
    return (
        triangular_score(
            source,
            float(config["low"]),
            float(config["peak"]),
            float(config["high"]),
        )
        * float(config["weight"])
    ).round(4)


def attach(frame: pd.DataFrame, policy: dict[str, Any] | None = None) -> pd.DataFrame:
    """Attach balanced v2 fields without mutating production or v1 ranks."""
    policy = load_policy() if policy is None else policy
    work = healthy_momentum.attach(frame)
    work = attach_short_relative_strength(work)
    work["code"] = work.get("code", pd.Series("", index=work.index)).map(normalized_code)
    work["healthy_v2_recent_pace_ratio"] = recent_pace_ratio(work)

    components = policy["confirmation_components"]
    work["healthy_v2_component_recent_pace"] = component_score(
        work["healthy_v2_recent_pace_ratio"], components["recent_pace_ratio"]
    )
    work["healthy_v2_component_market_relative_5d"] = component_score(
        numeric(work, "healthy_v2_market_relative_5d_percentile", 0.5).fillna(0.5),
        components["market_relative_5d_percentile"],
    )
    work["healthy_v2_component_sector_relative_5d"] = component_score(
        numeric(work, "healthy_v2_sector_relative_5d_percentile", 0.5).fillna(0.5),
        components["sector_relative_5d_percentile"],
    )
    work["healthy_v2_component_current_day_return"] = component_score(
        numeric(work, "healthy_current_day_return"), components["current_day_return"]
    )
    work["healthy_v2_component_drawdown"] = component_score(
        numeric(work, "healthy_drawdown_from_recent_high"),
        components["drawdown_from_recent_high"],
    )
    work["healthy_v2_component_long_relative_strength"] = component_score(
        numeric(work, "healthy_relative_strength_score", 50.0).fillna(50.0),
        components["long_relative_strength"],
    )

    component_columns = [
        "healthy_v2_component_recent_pace",
        "healthy_v2_component_market_relative_5d",
        "healthy_v2_component_sector_relative_5d",
        "healthy_v2_component_current_day_return",
        "healthy_v2_component_drawdown",
        "healthy_v2_component_long_relative_strength",
    ]
    work["healthy_v2_confirmation_score"] = (
        work[component_columns].sum(axis=1).round(4).clip(lower=0.0, upper=100.0)
    )

    exclusions = v1_exclusion_reasons(work)
    cautions = caution_reasons(work, policy)
    work["healthy_v2_exclusion_reasons"] = exclusions
    work["healthy_v2_caution_reasons"] = cautions
    work["healthy_v2_eligible"] = work.get(
        "healthy_eligible", pd.Series(False, index=work.index, dtype=bool)
    ).fillna(False).astype(bool)
    work["healthy_v2_confirmation_state"] = confirmation_state(exclusions, cautions)

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
                "healthy_selection_score",
                "healthy_v2_confirmation_score",
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


def _reason_summary(frame: pd.DataFrame, column: str, eligible_mask: pd.Series) -> pd.DataFrame:
    exploded = (
        frame.loc[eligible_mask, [column]]
        .assign(reason=lambda value: value[column].fillna("").str.split("|"))
        .explode("reason")
    )
    exploded = exploded[exploded["reason"].fillna("").ne("")]
    if exploded.empty:
        return pd.DataFrame(columns=["reason", "count", "ratio"])
    counts = exploded["reason"].value_counts().rename_axis("reason").reset_index(name="count")
    counts["ratio"] = counts["count"] / max(int(eligible_mask.sum()), 1)
    return counts


def exclusion_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if "healthy_v2_exclusion_reasons" not in frame.columns:
        frame = attach(frame)
    return _reason_summary(
        frame,
        "healthy_v2_exclusion_reasons",
        ~frame["healthy_v2_eligible"].fillna(False),
    )


def caution_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if "healthy_v2_caution_reasons" not in frame.columns:
        frame = attach(frame)
    return _reason_summary(
        frame,
        "healthy_v2_caution_reasons",
        frame["healthy_v2_eligible"].fillna(False),
    )
