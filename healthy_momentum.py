"""Healthy Momentum v1 shadow ranking.

The production Momentum rank remains unchanged. This module implements a two-stage
research ranking:

1. Exclude falling, structurally weak, illiquid, overheated, or data-risk rows.
2. Rank eligible rows by a bounded healthy-quality score blended with the current
   production Momentum score.

The module is intentionally deterministic, explainable, read-only, and safe to run
against one report or an entire ranking-history file. It does not place orders, change
paper positions, or promote itself into production.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

DEFAULT_POLICY_PATH = "research/healthy_momentum_policy.yaml"
HEALTHY_MOMENTUM_VERSION = "2026-07-21-healthy-momentum-v1-shadow"

HEALTHY_COLUMNS = [
    "healthy_momentum_version",
    "healthy_eligible",
    "healthy_exclusion_reasons",
    "healthy_trend_state",
    "healthy_component_return_20d",
    "healthy_component_return_5d",
    "healthy_component_return_60d",
    "healthy_component_ma20_deviation",
    "healthy_component_ma60_deviation",
    "healthy_component_volume_ratio",
    "healthy_component_current_day_return",
    "healthy_component_drawdown",
    "healthy_component_relative_strength",
    "healthy_quality_score",
    "healthy_selection_score",
    "healthy_rank",
]


def load_policy(path: str = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"healthy momentum policy not found: {path}")
    parsed = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if parsed.get("mode") != "SHADOW_ONLY":
        raise ValueError("Healthy Momentum v1 must remain SHADOW_ONLY")
    if parsed.get("automatic_promotion") is not False:
        raise ValueError("automatic_promotion must be false")
    if parsed.get("automatic_production_ranking_change") is not False:
        raise ValueError("automatic_production_ranking_change must be false")
    return parsed


def numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def flag(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(default)
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def normalized_code(value: Any) -> str:
    text = str(value or "").strip().split(".")[0]
    return text.zfill(4) if text else ""


def report_group_key(frame: pd.DataFrame) -> pd.Series:
    if "date" in frame.columns:
        values = pd.to_datetime(frame["date"], errors="coerce")
        fallback = pd.Series("single-report", index=frame.index, dtype="object")
        return values.dt.date.astype("string").fillna(fallback)
    return pd.Series("single-report", index=frame.index, dtype="object")


def triangular_score(values: pd.Series, low: float, peak: float, high: float) -> pd.Series:
    """Return a bounded 0..1 preference centered on ``peak``.

    A triangular preference avoids rewarding ever-larger returns, volume, or moving-
    average deviation. Extreme momentum therefore stops receiving more points and is
    instead removed by the eligibility gate.
    """
    if not low < peak < high:
        raise ValueError(f"triangular band must satisfy low < peak < high: {low}, {peak}, {high}")
    x = pd.to_numeric(values, errors="coerce")
    left = (x - low) / (peak - low)
    right = (high - x) / (high - peak)
    return pd.concat([left, right], axis=1).min(axis=1).clip(lower=0.0, upper=1.0).fillna(0.0)


def attach_relative_strength_fallback(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach one consistent cross-sectional relative-strength score.

    Current production history contains native relative-strength fields only after the
    feature launch. The shadow backtest recomputes the same market/sector comparison for
    every report, allowing older and newer reports to be compared consistently.
    """
    work = frame.copy()
    group_key = report_group_key(work)
    sector = work.get("sector33", pd.Series("", index=work.index)).fillna("").astype(str)

    component_columns: list[str] = []
    weights = {
        "healthy_market_relative_20d": 0.30,
        "healthy_market_relative_60d": 0.25,
        "healthy_sector_relative_20d": 0.25,
        "healthy_sector_relative_60d": 0.20,
    }
    for horizon in (20, 60):
        source = numeric(work, f"return_{horizon}d")
        market_median = source.groupby(group_key).transform("median")
        sector_median = source.groupby([group_key, sector], dropna=False).transform("median")
        sector_median = sector_median.where(sector.ne(""), market_median).fillna(market_median)
        market_column = f"healthy_market_relative_{horizon}d"
        sector_column = f"healthy_sector_relative_{horizon}d"
        work[market_column] = source - market_median
        work[sector_column] = source - sector_median
        component_columns.extend([market_column, sector_column])

    relative_score = pd.Series(0.0, index=work.index, dtype=float)
    for column in component_columns:
        percentile = work[column].groupby(group_key).rank(method="average", pct=True).fillna(0.5)
        relative_score += percentile * weights[column] * 100.0
    work["healthy_relative_strength_score"] = relative_score.round(4).clip(lower=0.0, upper=100.0)
    return work


def current_day_return(frame: pd.DataFrame) -> pd.Series:
    close = numeric(frame, "close")
    previous = numeric(frame, "prev_close")
    return (close / previous - 1.0).replace([np.inf, -np.inf], np.nan)


def drawdown_from_recent_high(frame: pd.DataFrame) -> pd.Series:
    close = numeric(frame, "close")
    high = numeric(frame, "recent_high")
    return (close / high - 1.0).replace([np.inf, -np.inf], np.nan)


def eligibility_reasons(frame: pd.DataFrame, policy: dict[str, Any]) -> pd.Series:
    cfg = policy["eligibility"]
    return_5d = numeric(frame, "return_5d")
    return_20d = numeric(frame, "return_20d")
    ma20_deviation = numeric(frame, "ma20_deviation")
    volume_ratio = numeric(frame, "volume_ratio")
    trading_value = numeric(frame, "trading_value")
    day_return = current_day_return(frame)
    drawdown = drawdown_from_recent_high(frame)
    above_ma20 = flag(frame, "above_ma20")
    above_ma60 = flag(frame, "above_ma60")
    corporate_action = flag(frame, "data_quality_corporate_action_suspected")
    abnormal_price = flag(frame, "data_quality_abnormal_price")

    reasons: list[list[str]] = [[] for _ in range(len(frame))]

    def add(mask: pd.Series, reason: str) -> None:
        for position in np.flatnonzero(mask.fillna(True).to_numpy()):
            reasons[position].append(reason)

    add(trading_value.lt(float(cfg["min_trading_value"])) | trading_value.isna(), "LOW_LIQUIDITY")
    if cfg.get("require_above_ma20", True):
        add(~above_ma20, "BELOW_MA20")
    if cfg.get("require_above_ma60", True):
        add(~above_ma60, "BELOW_MA60")
    add(return_5d.le(float(cfg["min_return_5d"])) | return_5d.isna(), "FIVE_DAY_NOT_RISING")
    add(return_20d.lt(float(cfg["min_return_20d"])) | return_20d.isna(), "TWENTY_DAY_TOO_WEAK")
    add(return_20d.gt(float(cfg["max_return_20d"])), "TWENTY_DAY_OVERHEATED")
    add(ma20_deviation.lt(float(cfg["min_ma20_deviation"])) | ma20_deviation.isna(), "BELOW_MA20_STRUCTURE")
    add(ma20_deviation.gt(float(cfg["max_ma20_deviation"])), "MA20_OVEREXTENDED")
    add(volume_ratio.lt(float(cfg["min_volume_ratio"])) | volume_ratio.isna(), "VOLUME_TOO_LOW")
    add(volume_ratio.gt(float(cfg["max_volume_ratio"])), "VOLUME_SPIKE_OVERHEATED")
    add(day_return.lt(float(cfg["min_current_day_return"])) | day_return.isna(), "CURRENT_DAY_DROP")
    add(drawdown.lt(float(cfg["min_drawdown_from_recent_high"])) | drawdown.isna(), "RECENT_HIGH_BREAKDOWN")
    if cfg.get("exclude_corporate_action_suspected", True):
        add(corporate_action, "CORPORATE_ACTION_UNRESOLVED")
    if cfg.get("exclude_abnormal_price", True):
        add(abnormal_price, "ABNORMAL_PRICE")

    return pd.Series(["|".join(item) for item in reasons], index=frame.index, dtype="object")


def trend_state(reasons: pd.Series) -> pd.Series:
    data_risk = {"CORPORATE_ACTION_UNRESOLVED", "ABNORMAL_PRICE"}
    falling = {"BELOW_MA20", "BELOW_MA60", "FIVE_DAY_NOT_RISING", "CURRENT_DAY_DROP", "RECENT_HIGH_BREAKDOWN", "BELOW_MA20_STRUCTURE"}
    overheated = {"TWENTY_DAY_OVERHEATED", "MA20_OVEREXTENDED", "VOLUME_SPIKE_OVERHEATED"}
    illiquid = {"LOW_LIQUIDITY", "VOLUME_TOO_LOW"}

    def classify(value: Any) -> str:
        found = {item for item in str(value or "").split("|") if item}
        if not found:
            return "RISING_HEALTHY"
        if found & data_risk:
            return "DATA_RISK"
        if found & falling:
            return "FALLING_OR_BROKEN"
        if found & overheated:
            return "OVERHEATED"
        if found & illiquid:
            return "ILLIQUID"
        return "MIXED_OR_TOO_WEAK"

    return reasons.map(classify)


def component_score(frame: pd.DataFrame, policy: dict[str, Any], component: str, source: pd.Series) -> pd.Series:
    cfg = policy["healthy_quality_components"][component]
    return float(cfg["weight"]) * triangular_score(source, float(cfg["low"]), float(cfg["peak"]), float(cfg["high"]))


def attach(frame: pd.DataFrame, policy: dict[str, Any] | None = None) -> pd.DataFrame:
    """Attach Healthy Momentum v1 shadow fields without changing production ``rank``."""
    policy = load_policy() if policy is None else policy
    work = attach_relative_strength_fallback(frame)
    work["code"] = work.get("code", pd.Series("", index=work.index)).map(normalized_code)

    day_return = current_day_return(work)
    drawdown = drawdown_from_recent_high(work)
    component_sources = {
        "return_20d": numeric(work, "return_20d"),
        "return_5d": numeric(work, "return_5d"),
        "return_60d": numeric(work, "return_60d"),
        "ma20_deviation": numeric(work, "ma20_deviation"),
        "ma60_deviation": numeric(work, "ma60_deviation"),
        "volume_ratio": numeric(work, "volume_ratio"),
        "current_day_return": day_return,
        "drawdown_from_recent_high": drawdown,
    }
    component_output_names = {
        "return_20d": "healthy_component_return_20d",
        "return_5d": "healthy_component_return_5d",
        "return_60d": "healthy_component_return_60d",
        "ma20_deviation": "healthy_component_ma20_deviation",
        "ma60_deviation": "healthy_component_ma60_deviation",
        "volume_ratio": "healthy_component_volume_ratio",
        "current_day_return": "healthy_component_current_day_return",
        "drawdown_from_recent_high": "healthy_component_drawdown",
    }
    component_columns: list[str] = []
    for component, source in component_sources.items():
        output = component_output_names[component]
        work[output] = component_score(work, policy, component, source).round(4)
        component_columns.append(output)

    relative_weight = float(policy["healthy_quality_components"]["relative_strength"]["weight"])
    work["healthy_component_relative_strength"] = (
        numeric(work, "healthy_relative_strength_score", 50.0).fillna(50.0) / 100.0 * relative_weight
    ).round(4)
    component_columns.append("healthy_component_relative_strength")
    work["healthy_quality_score"] = work[component_columns].sum(axis=1).round(4).clip(lower=0.0, upper=100.0)

    reasons = eligibility_reasons(work, policy)
    work["healthy_exclusion_reasons"] = reasons
    work["healthy_eligible"] = reasons.eq("")
    work["healthy_trend_state"] = trend_state(reasons)

    selection = policy["selection_score"]
    current_score = numeric(work, "score", 0.0).fillna(0.0).clip(lower=0.0, upper=100.0)
    work["healthy_selection_score"] = (
        work["healthy_quality_score"] * float(selection["healthy_quality_weight"])
        + current_score * float(selection["existing_momentum_weight"])
    ).round(4)

    group_key = report_group_key(work)
    eligible_score = work["healthy_selection_score"].where(work["healthy_eligible"])
    # Stable tie-breaking: score, relative strength, liquidity, then code.
    rank = pd.Series(pd.NA, index=work.index, dtype="Int64")
    for _, positions in work.groupby(group_key, sort=False).groups.items():
        subset = work.loc[list(positions)].copy()
        subset = subset[subset["healthy_eligible"]].sort_values(
            ["healthy_selection_score", "healthy_relative_strength_score", "trading_value", "code"],
            ascending=[False, False, False, True],
            na_position="last",
        )
        rank.loc[subset.index] = pd.Series(range(1, len(subset) + 1), index=subset.index, dtype="Int64")
    work["healthy_rank"] = rank
    work["healthy_momentum_version"] = HEALTHY_MOMENTUM_VERSION
    work["healthy_current_day_return"] = day_return
    work["healthy_drawdown_from_recent_high"] = drawdown
    return work


def latest_shadow_table(frame: pd.DataFrame, limit: int = 100, policy: dict[str, Any] | None = None) -> pd.DataFrame:
    enriched = attach(frame, policy)
    if enriched.empty:
        return enriched
    if "date" in enriched.columns:
        dates = pd.to_datetime(enriched["date"], errors="coerce")
        if dates.notna().any():
            enriched = enriched[dates == dates.max()].copy()
    return enriched[enriched["healthy_eligible"]].sort_values("healthy_rank").head(limit).reset_index(drop=True)


def exclusion_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if "healthy_exclusion_reasons" not in frame.columns:
        frame = attach(frame)
    exploded = (
        frame.loc[~frame["healthy_eligible"], ["healthy_exclusion_reasons"]]
        .assign(reason=lambda x: x["healthy_exclusion_reasons"].str.split("|"))
        .explode("reason")
    )
    exploded = exploded[exploded["reason"].fillna("").ne("")]
    if exploded.empty:
        return pd.DataFrame(columns=["reason", "count", "ratio"])
    counts = exploded["reason"].value_counts().rename_axis("reason").reset_index(name="count")
    counts["ratio"] = counts["count"] / len(frame)
    return counts
