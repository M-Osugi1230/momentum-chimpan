"""Research-only evidence lab for relative-strength signals.

This module rebuilds daily cross-sectional relative strength from stored ranking
history, evaluates forward returns against same-window market and sector medians,
and reports evidence for the governed ``relative-strength-alpha-v18`` experiment.

It never changes production thresholds, live state, paper positions, or the
experiment registry.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml

import main
import replay
import research_scorecard
import robustness_analysis

EVIDENCE_VERSION = "2026-07-11-relative-strength-evidence-v1"
EXPERIMENT_ID = "relative-strength-alpha-v18"
DEFAULT_HISTORY = "data/momentum_daily_ranking.csv"
DEFAULT_JPX_CACHE = "data/jpx_list_cache.csv"
DEFAULT_REGISTRY = "research/experiment_registry.yaml"
DEFAULT_OUTPUT_DIR = "output/relative_strength"
DEFAULT_HORIZONS = (5, 10, 20)
DEFAULT_COST_BPS = 30

SIGNAL_COLUMNS = [
    "signal_date", "code", "name", "sector33", "rank", "score", "close",
    "relative_strength_score", "relative_strength_rank", "relative_strength_grade",
    "relative_strength_decile", "relative_strength_quintile", "dual_outperformer",
    "market_relative_20d", "market_relative_60d",
    "sector_relative_20d", "sector_relative_60d",
    "trading_value", "volume_ratio",
]

OUTCOME_COLUMNS = SIGNAL_COLUMNS + [
    "horizon_days", "entry_price_date", "exit_price_date", "entry_close", "exit_close",
    "forward_return", "market_benchmark_return", "top100_benchmark_return",
    "sector_benchmark_return", "market_excess_return", "top100_excess_return",
    "sector_excess_return", "beat_market", "beat_top100", "beat_sector",
    "market_peer_count", "top100_peer_count", "sector_peer_count", "calendar_days",
]


def _safe_number(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def _safe_bool(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def _mean(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return None if values.empty else float(values.mean())


def _median(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return None if values.empty else float(values.median())


def _rate(series: pd.Series) -> float | None:
    values = series.dropna()
    return None if values.empty else float(values.astype(bool).mean())


def load_history(history_path: str, jpx_cache_path: str) -> pd.DataFrame:
    target = Path(history_path)
    if not target.exists() or target.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return replay.prepare_history(history_path, jpx_cache_path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def analysis_dates(history: pd.DataFrame, max_dates: int | None = None) -> list[str]:
    if history is None or history.empty or "date" not in history.columns:
        return []
    dates = sorted(history["date"].dropna().astype(str).unique().tolist())
    if max_dates and max_dates > 0:
        dates = dates[-max_dates:]
    return dates


def _bucket_labels(values: pd.Series, buckets: int, prefix: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series("", index=values.index, dtype=object)
    valid = numeric.notna()
    if not valid.any():
        return result
    percentile = numeric[valid].rank(method="average", pct=True)
    bucket_number = np.ceil(percentile * buckets).clip(1, buckets).astype(int)
    result.loc[valid] = bucket_number.map(lambda value: f"{prefix}{value}")
    return result


def build_signal_panel(
    history: pd.DataFrame,
    top_limit: int = 100,
    max_dates: int | None = None,
) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)

    frames: list[pd.DataFrame] = []
    for signal_date in analysis_dates(history, max_dates):
        day = history[history["date"].astype(str) == signal_date].copy()
        if day.empty:
            continue
        enriched = main.attach_relative_strength(day)
        enriched["rank"] = pd.to_numeric(enriched.get("rank"), errors="coerce")
        enriched["close"] = pd.to_numeric(enriched.get("close"), errors="coerce")
        enriched = enriched[
            enriched["rank"].le(top_limit)
            & enriched["close"].gt(0)
            & pd.to_numeric(enriched.get("relative_strength_score"), errors="coerce").notna()
        ].copy()
        if enriched.empty:
            continue

        enriched["signal_date"] = signal_date
        enriched["code"] = enriched["code"].map(main.normalize_code)
        enriched["sector33"] = enriched.get(
            "sector33", pd.Series(index=enriched.index, dtype=str)
        ).map(main.normalize_sector33)
        enriched["relative_strength_decile"] = _bucket_labels(
            enriched["relative_strength_score"], 10, "D"
        )
        enriched["relative_strength_quintile"] = _bucket_labels(
            enriched["relative_strength_score"], 5, "Q"
        )
        enriched["dual_outperformer"] = enriched.get(
            "dual_outperformer", pd.Series(False, index=enriched.index)
        ).map(_safe_bool)
        for column in SIGNAL_COLUMNS:
            if column not in enriched.columns:
                enriched[column] = None
        frames.append(enriched[SIGNAL_COLUMNS])

    if not frames:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(["signal_date", "code"], keep="last")
        .sort_values(["signal_date", "relative_strength_rank", "rank", "code"])
        .reset_index(drop=True)
    )


def _pair_returns(history: pd.DataFrame, entry_date: str, exit_date: str) -> pd.DataFrame:
    entry = history[history["date"].astype(str) == str(entry_date)][
        ["code", "close", "rank", "sector33"]
    ].copy()
    exit_frame = history[history["date"].astype(str) == str(exit_date)][
        ["code", "close"]
    ].copy()
    if entry.empty or exit_frame.empty:
        return pd.DataFrame(
            columns=["code", "rank", "sector33", "benchmark_return"]
        )
    entry["code"] = entry["code"].map(main.normalize_code)
    exit_frame["code"] = exit_frame["code"].map(main.normalize_code)
    entry = entry.rename(columns={"close": "entry_benchmark_close"})
    exit_frame = exit_frame.rename(columns={"close": "exit_benchmark_close"})
    merged = entry.merge(exit_frame, on="code", how="inner")
    merged["entry_benchmark_close"] = pd.to_numeric(
        merged["entry_benchmark_close"], errors="coerce"
    )
    merged["exit_benchmark_close"] = pd.to_numeric(
        merged["exit_benchmark_close"], errors="coerce"
    )
    merged["rank"] = pd.to_numeric(merged["rank"], errors="coerce")
    merged["sector33"] = merged["sector33"].map(main.normalize_sector33)
    merged = merged.dropna(subset=["entry_benchmark_close", "exit_benchmark_close"])
    merged = merged[merged["entry_benchmark_close"] > 0].copy()
    merged["benchmark_return"] = (
        merged["exit_benchmark_close"] / merged["entry_benchmark_close"] - 1
    )
    return merged


def build_forward_outcomes(
    signals: pd.DataFrame,
    history: pd.DataFrame,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    if signals is None or signals.empty or history is None or history.empty:
        return pd.DataFrame(columns=OUTCOME_COLUMNS)

    prices = history[["date", "code", "close"]].copy()
    prices["date_sort"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["code"] = prices["code"].map(main.normalize_code)
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = (
        prices.dropna(subset=["date_sort", "close"])
        .drop_duplicates(["code", "date_sort"], keep="last")
        .sort_values(["code", "date_sort"])
    )
    price_groups = {code: group for code, group in prices.groupby("code")}
    benchmark_cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []

    for _, signal in signals.iterrows():
        code = main.normalize_code(signal.get("code"))
        if code not in price_groups:
            continue
        entry_date = pd.to_datetime(signal.get("signal_date"), errors="coerce")
        entry_close = _safe_number(signal.get("close"))
        if pd.isna(entry_date) or entry_close is None or entry_close <= 0:
            continue
        future = price_groups[code][price_groups[code]["date_sort"] > entry_date]
        for horizon in horizons:
            if len(future) < int(horizon):
                continue
            exit_row = future.iloc[int(horizon) - 1]
            exit_date = exit_row["date_sort"].date().isoformat()
            entry_date_text = entry_date.date().isoformat()
            exit_close = float(exit_row["close"])
            forward_return = exit_close / entry_close - 1

            cache_key = (entry_date_text, exit_date)
            if cache_key not in benchmark_cache:
                benchmark_cache[cache_key] = _pair_returns(
                    history, entry_date_text, exit_date
                )
            peers = benchmark_cache[cache_key]
            market_return = _median(peers.get("benchmark_return", pd.Series(dtype=float)))
            top100 = peers[pd.to_numeric(peers.get("rank"), errors="coerce").le(100)]
            top100_return = _median(
                top100.get("benchmark_return", pd.Series(dtype=float))
            )
            sector_name = main.normalize_sector33(signal.get("sector33"))
            sector = peers[
                peers.get("sector33", pd.Series(index=peers.index, dtype=str))
                .map(main.normalize_sector33)
                .eq(sector_name)
            ]
            sector_return = _median(
                sector.get("benchmark_return", pd.Series(dtype=float))
            )

            record = signal.to_dict()
            record.update({
                "horizon_days": int(horizon),
                "entry_price_date": entry_date_text,
                "exit_price_date": exit_date,
                "entry_close": entry_close,
                "exit_close": exit_close,
                "forward_return": forward_return,
                "market_benchmark_return": market_return,
                "top100_benchmark_return": top100_return,
                "sector_benchmark_return": sector_return,
                "market_excess_return": (
                    None if market_return is None else forward_return - market_return
                ),
                "top100_excess_return": (
                    None if top100_return is None else forward_return - top100_return
                ),
                "sector_excess_return": (
                    None if sector_return is None else forward_return - sector_return
                ),
                "beat_market": (
                    None if market_return is None else forward_return > market_return
                ),
                "beat_top100": (
                    None if top100_return is None else forward_return > top100_return
                ),
                "beat_sector": (
                    None if sector_return is None else forward_return > sector_return
                ),
                "market_peer_count": int(len(peers)),
                "top100_peer_count": int(len(top100)),
                "sector_peer_count": int(len(sector)),
                "calendar_days": int(
                    (exit_row["date_sort"] - entry_date).days
                ),
            })
            rows.append(record)

    if not rows:
        return pd.DataFrame(columns=OUTCOME_COLUMNS)
    result = pd.DataFrame(rows)
    for column in OUTCOME_COLUMNS:
        if column not in result.columns:
            result[column] = None
    return result[OUTCOME_COLUMNS].sort_values(
        ["signal_date", "horizon_days", "relative_strength_rank", "code"]
    ).reset_index(drop=True)


def _performance_record(
    group_type: str,
    group_value: str,
    horizon: int,
    subset: pd.DataFrame,
) -> dict[str, Any]:
    market_excess = pd.to_numeric(
        subset.get("market_excess_return"), errors="coerce"
    ).dropna()
    sector_excess = pd.to_numeric(
        subset.get("sector_excess_return"), errors="coerce"
    ).dropna()
    market_ci_low, market_ci_high = research_scorecard.bootstrap_mean_ci(
        market_excess, samples=2000, seed=4000 + int(horizon)
    )
    sector_ci_low, sector_ci_high = research_scorecard.bootstrap_mean_ci(
        sector_excess, samples=2000, seed=5000 + int(horizon)
    )
    returns = pd.to_numeric(subset.get("forward_return"), errors="coerce").dropna()
    return {
        "group_type": group_type,
        "group_value": group_value,
        "horizon_days": int(horizon),
        "count": int(len(subset)),
        "unique_codes": int(subset["code"].nunique()) if "code" in subset else 0,
        "unique_signal_dates": (
            int(subset["signal_date"].nunique()) if "signal_date" in subset else 0
        ),
        "average_relative_strength_score": _mean(
            subset.get("relative_strength_score", pd.Series(dtype=float))
        ),
        "average_forward_return": _mean(returns),
        "median_forward_return": _median(returns),
        "win_rate": float((returns > 0).mean()) if len(returns) else None,
        "average_market_excess": _mean(market_excess),
        "median_market_excess": _median(market_excess),
        "market_outperformance_rate": _rate(
            subset.get("beat_market", pd.Series(dtype=object))
        ),
        "market_excess_ci_low_95": market_ci_low,
        "market_excess_ci_high_95": market_ci_high,
        "average_sector_excess": _mean(sector_excess),
        "median_sector_excess": _median(sector_excess),
        "sector_outperformance_rate": _rate(
            subset.get("beat_sector", pd.Series(dtype=object))
        ),
        "sector_excess_ci_low_95": sector_ci_low,
        "sector_excess_ci_high_95": sector_ci_high,
    }


def build_bucket_performance(outcomes: pd.DataFrame) -> pd.DataFrame:
    if outcomes is None or outcomes.empty:
        return pd.DataFrame()
    records: list[dict[str, Any]] = []
    groupings = (
        ("decile", "relative_strength_decile"),
        ("quintile", "relative_strength_quintile"),
        ("grade", "relative_strength_grade"),
        ("dual_outperformer", "dual_outperformer"),
    )
    for horizon, horizon_rows in outcomes.groupby("horizon_days"):
        records.append(
            _performance_record("overall", "all", int(horizon), horizon_rows)
        )
        for group_type, column in groupings:
            if column not in horizon_rows.columns:
                continue
            for value, subset in horizon_rows.groupby(column, dropna=False):
                records.append(
                    _performance_record(
                        group_type, str(value), int(horizon), subset
                    )
                )
    result = pd.DataFrame(records)
    return result.sort_values(
        ["horizon_days", "group_type", "group_value"]
    ).reset_index(drop=True)


def _spearman(x: pd.Series, y: pd.Series, minimum: int = 5) -> float | None:
    pair = pd.DataFrame({
        "x": pd.to_numeric(x, errors="coerce"),
        "y": pd.to_numeric(y, errors="coerce"),
    }).dropna()
    if len(pair) < minimum or pair["x"].nunique() < 2 or pair["y"].nunique() < 2:
        return None
    ranked_x = pair["x"].rank(method="average")
    ranked_y = pair["y"].rank(method="average")
    value = ranked_x.corr(ranked_y)
    return None if pd.isna(value) else float(value)


def build_daily_information_coefficients(outcomes: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "signal_date", "horizon_days", "count", "ic_forward_return",
        "ic_market_excess", "ic_sector_excess",
    ]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for (signal_date, horizon), subset in outcomes.groupby(
        ["signal_date", "horizon_days"]
    ):
        rows.append({
            "signal_date": str(signal_date),
            "horizon_days": int(horizon),
            "count": int(len(subset)),
            "ic_forward_return": _spearman(
                subset["relative_strength_score"], subset["forward_return"]
            ),
            "ic_market_excess": _spearman(
                subset["relative_strength_score"], subset["market_excess_return"]
            ),
            "ic_sector_excess": _spearman(
                subset["relative_strength_score"], subset["sector_excess_return"]
            ),
        })
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["signal_date", "horizon_days"]
    ).reset_index(drop=True)


def _ic_stat(series: pd.Series) -> dict[str, Any]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {
            "date_count": 0,
            "mean_ic": None,
            "median_ic": None,
            "positive_ic_rate": None,
            "ic_information_ratio": None,
        }
    standard_deviation = float(values.std(ddof=1)) if len(values) > 1 else 0.0
    return {
        "date_count": int(len(values)),
        "mean_ic": float(values.mean()),
        "median_ic": float(values.median()),
        "positive_ic_rate": float((values > 0).mean()),
        "ic_information_ratio": (
            None if standard_deviation <= 0
            else float(values.mean() / standard_deviation)
        ),
    }


def build_ic_summary(daily_ic: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "target", "horizon_days", "date_count", "mean_ic", "median_ic",
        "positive_ic_rate", "ic_information_ratio",
    ]
    if daily_ic is None or daily_ic.empty:
        return pd.DataFrame(columns=columns)
    records: list[dict[str, Any]] = []
    targets = (
        ("forward_return", "ic_forward_return"),
        ("market_excess", "ic_market_excess"),
        ("sector_excess", "ic_sector_excess"),
    )
    for horizon, subset in daily_ic.groupby("horizon_days"):
        for target, column in targets:
            records.append({
                "target": target,
                "horizon_days": int(horizon),
                **_ic_stat(subset[column]),
            })
    return pd.DataFrame(records, columns=columns)


def _bucket_number(value: Any) -> int | None:
    text = str(value or "")
    digits = "".join(character for character in text if character.isdigit())
    return int(digits) if digits else None


def build_monotonicity(bucket_performance: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "horizon_days", "decile_count", "market_excess_monotonicity",
        "sector_excess_monotonicity", "d10_minus_d1_market_excess",
        "d10_minus_d1_sector_excess", "monotonicity_status",
    ]
    if bucket_performance is None or bucket_performance.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    deciles = bucket_performance[
        bucket_performance["group_type"] == "decile"
    ].copy()
    for horizon, subset in deciles.groupby("horizon_days"):
        subset["bucket_number"] = subset["group_value"].map(_bucket_number)
        subset = subset.dropna(subset=["bucket_number"]).sort_values("bucket_number")
        market_corr = _spearman(
            subset["bucket_number"], subset["average_market_excess"], minimum=3
        )
        sector_corr = _spearman(
            subset["bucket_number"], subset["average_sector_excess"], minimum=3
        )
        d1 = subset[subset["bucket_number"] == 1]
        d10 = subset[subset["bucket_number"] == 10]
        d1_market = None if d1.empty else _safe_number(d1.iloc[0]["average_market_excess"])
        d10_market = None if d10.empty else _safe_number(d10.iloc[0]["average_market_excess"])
        d1_sector = None if d1.empty else _safe_number(d1.iloc[0]["average_sector_excess"])
        d10_sector = None if d10.empty else _safe_number(d10.iloc[0]["average_sector_excess"])
        market_spread = (
            None if d1_market is None or d10_market is None
            else d10_market - d1_market
        )
        sector_spread = (
            None if d1_sector is None or d10_sector is None
            else d10_sector - d1_sector
        )
        passed = (
            market_corr is not None and market_corr > 0
            and sector_corr is not None and sector_corr > 0
            and market_spread is not None and market_spread > 0
            and sector_spread is not None and sector_spread > 0
        )
        rows.append({
            "horizon_days": int(horizon),
            "decile_count": int(len(subset)),
            "market_excess_monotonicity": market_corr,
            "sector_excess_monotonicity": sector_corr,
            "d10_minus_d1_market_excess": market_spread,
            "d10_minus_d1_sector_excess": sector_spread,
            "monotonicity_status": "PASS" if passed else "INCONCLUSIVE",
        })
    return pd.DataFrame(rows, columns=columns)


def build_rank_stability(signals: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    columns = [
        "previous_date", "current_date", "common_code_count", "score_spearman",
        "rank_spearman", "average_absolute_rank_change", "top_n_retention_rate",
        "top_n_turnover_rate", "dual_retention_rate",
    ]
    if signals is None or signals.empty:
        return pd.DataFrame(columns=columns)
    dates = sorted(signals["signal_date"].dropna().astype(str).unique().tolist())
    rows: list[dict[str, Any]] = []
    for previous_date, current_date in zip(dates, dates[1:]):
        previous = signals[signals["signal_date"].astype(str) == previous_date].copy()
        current = signals[signals["signal_date"].astype(str) == current_date].copy()
        common = previous.merge(
            current,
            on="code",
            suffixes=("_previous", "_current"),
            how="inner",
        )
        previous_top = set(
            previous.nsmallest(top_n, "relative_strength_rank")["code"].astype(str)
        )
        current_top = set(
            current.nsmallest(top_n, "relative_strength_rank")["code"].astype(str)
        )
        previous_dual = set(
            previous[previous["dual_outperformer"].map(_safe_bool)]["code"].astype(str)
        )
        current_dual = set(
            current[current["dual_outperformer"].map(_safe_bool)]["code"].astype(str)
        )
        retention = (
            len(previous_top & current_top) / len(previous_top)
            if previous_top else None
        )
        dual_retention = (
            len(previous_dual & current_dual) / len(previous_dual)
            if previous_dual else None
        )
        rows.append({
            "previous_date": previous_date,
            "current_date": current_date,
            "common_code_count": int(len(common)),
            "score_spearman": _spearman(
                common.get("relative_strength_score_previous", pd.Series(dtype=float)),
                common.get("relative_strength_score_current", pd.Series(dtype=float)),
            ),
            "rank_spearman": _spearman(
                common.get("relative_strength_rank_previous", pd.Series(dtype=float)),
                common.get("relative_strength_rank_current", pd.Series(dtype=float)),
            ),
            "average_absolute_rank_change": _mean(
                (
                    pd.to_numeric(
                        common.get("relative_strength_rank_current"), errors="coerce"
                    )
                    - pd.to_numeric(
                        common.get("relative_strength_rank_previous"), errors="coerce"
                    )
                ).abs()
            ),
            "top_n_retention_rate": retention,
            "top_n_turnover_rate": None if retention is None else 1 - retention,
            "dual_retention_rate": dual_retention,
        })
    return pd.DataFrame(rows, columns=columns)


def _robustness_groups(outcomes: pd.DataFrame) -> Iterable[tuple[str, str, int, pd.DataFrame]]:
    for horizon, horizon_rows in outcomes.groupby("horizon_days"):
        yield "overall", "all", int(horizon), horizon_rows
        for value, subset in horizon_rows.groupby("dual_outperformer", dropna=False):
            yield "dual_outperformer", str(_safe_bool(value)), int(horizon), subset
        for value, subset in horizon_rows.groupby("relative_strength_grade", dropna=False):
            yield "grade", str(value), int(horizon), subset
        for value, subset in horizon_rows.groupby("relative_strength_decile", dropna=False):
            yield "decile", str(value), int(horizon), subset


def _subperiod_metrics(subset: pd.DataFrame, values: pd.Series) -> tuple[float | None, float | None]:
    dates = sorted(subset["signal_date"].dropna().astype(str).unique().tolist())
    if not dates:
        return None, None
    midpoint = max(len(dates) // 2, 1)
    early_dates = set(dates[:midpoint])
    late_dates = set(dates[midpoint:])
    early = values.loc[subset["signal_date"].astype(str).isin(early_dates)]
    late = values.loc[subset["signal_date"].astype(str).isin(late_dates)]
    return _mean(early), _mean(late)


def _worst_leave_one_sector(subset: pd.DataFrame, values: pd.Series) -> float | None:
    means: list[float] = []
    sectors = subset["sector33"].dropna().astype(str).unique().tolist()
    for sector in sectors:
        remaining_index = subset.index[subset["sector33"].astype(str) != sector]
        remaining = values.loc[values.index.intersection(remaining_index)].dropna()
        if len(remaining):
            means.append(float(remaining.mean()))
    return min(means) if means else None


def _evidence_status(
    count: int,
    q_value: float | None,
    market_mean: float | None,
    sector_mean: float | None,
    market_early: float | None,
    market_late: float | None,
    market_worst_sector: float | None,
    market_beat_rate: float | None,
    sector_beat_rate: float | None,
) -> str:
    if count < 30:
        return "INSUFFICIENT"
    required_positive = (
        market_mean, sector_mean, market_early, market_late, market_worst_sector
    )
    if any(value is None or pd.isna(value) or float(value) <= 0 for value in required_positive):
        return "FRAGILE"
    if q_value is None or pd.isna(q_value) or float(q_value) > 0.10:
        return "DEVELOPING"
    if (
        count >= 100
        and float(q_value) <= 0.05
        and market_beat_rate is not None and market_beat_rate >= 0.55
        and sector_beat_rate is not None and sector_beat_rate >= 0.55
    ):
        return "ROBUST"
    if count >= 50:
        return "PROMISING"
    return "DEVELOPING"


def build_robustness(
    outcomes: pd.DataFrame,
    cost_bps: int = DEFAULT_COST_BPS,
) -> pd.DataFrame:
    columns = [
        "group_type", "group_value", "horizon_days", "round_trip_cost_bps",
        "count", "net_average_market_excess", "net_average_sector_excess",
        "market_outperformance_rate", "sector_outperformance_rate",
        "early_net_average_excess", "late_net_average_excess",
        "worst_leave_one_sector_excess", "one_sided_sign_flip_p_value",
        "fdr_q_value", "robustness_status",
    ]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)

    cost = cost_bps / 10_000
    rows: list[dict[str, Any]] = []
    for index, (group_type, group_value, horizon, subset) in enumerate(
        _robustness_groups(outcomes)
    ):
        market_excess = (
            pd.to_numeric(subset["market_excess_return"], errors="coerce") - cost
        )
        sector_excess = (
            pd.to_numeric(subset["sector_excess_return"], errors="coerce") - cost
        )
        clean_market = market_excess.dropna()
        market_early, market_late = _subperiod_metrics(subset, market_excess)
        market_worst_sector = _worst_leave_one_sector(subset, market_excess)
        rows.append({
            "group_type": group_type,
            "group_value": group_value,
            "horizon_days": int(horizon),
            "round_trip_cost_bps": int(cost_bps),
            "count": int(len(clean_market)),
            "net_average_market_excess": _mean(clean_market),
            "net_average_sector_excess": _mean(sector_excess),
            "market_outperformance_rate": float((clean_market > 0).mean()) if len(clean_market) else None,
            "sector_outperformance_rate": float((sector_excess.dropna() > 0).mean()) if len(sector_excess.dropna()) else None,
            "early_net_average_excess": market_early,
            "late_net_average_excess": market_late,
            "worst_leave_one_sector_excess": market_worst_sector,
            "one_sided_sign_flip_p_value": robustness_analysis.sign_flip_p_value(
                clean_market, samples=5000, seed=7000 + index
            ),
        })
    result = pd.DataFrame(rows)
    result["fdr_q_value"] = robustness_analysis.benjamini_hochberg(
        result["one_sided_sign_flip_p_value"]
    )
    result["robustness_status"] = result.apply(
        lambda row: _evidence_status(
            int(row["count"]),
            _safe_number(row.get("fdr_q_value")),
            _safe_number(row.get("net_average_market_excess")),
            _safe_number(row.get("net_average_sector_excess")),
            _safe_number(row.get("early_net_average_excess")),
            _safe_number(row.get("late_net_average_excess")),
            _safe_number(row.get("worst_leave_one_sector_excess")),
            _safe_number(row.get("market_outperformance_rate")),
            _safe_number(row.get("sector_outperformance_rate")),
        ),
        axis=1,
    )
    return result[columns].sort_values(
        ["horizon_days", "group_type", "group_value"]
    ).reset_index(drop=True)


def load_experiment(registry_path: str, experiment_id: str = EXPERIMENT_ID) -> dict[str, Any]:
    target = Path(registry_path)
    if not target.exists() or target.stat().st_size == 0:
        return {}
    registry = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    for experiment in registry.get("experiments", []):
        if str(experiment.get("experiment_id")) == experiment_id:
            return {
                "policy": registry.get("policy", {}) or {},
                "experiment": experiment,
            }
    return {"policy": registry.get("policy", {}) or {}, "experiment": {}}


def build_readiness(
    robustness: pd.DataFrame,
    ic_summary: pd.DataFrame,
    monotonicity: pd.DataFrame,
    experiment_context: dict[str, Any],
    horizon: int = 10,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    experiment = experiment_context.get("experiment", {}) or {}
    policy = experiment_context.get("policy", {}) or {}
    success = experiment.get("success_criteria", {}) or {}
    candidate = robustness[
        (robustness.get("group_type", pd.Series(dtype=str)) == "dual_outperformer")
        & (robustness.get("group_value", pd.Series(dtype=str)) == "True")
        & (pd.to_numeric(robustness.get("horizon_days"), errors="coerce") == horizon)
    ]
    row = candidate.iloc[0].to_dict() if not candidate.empty else {}

    market_ic_rows = ic_summary[
        (ic_summary.get("target", pd.Series(dtype=str)) == "market_excess")
        & (pd.to_numeric(ic_summary.get("horizon_days"), errors="coerce") == horizon)
    ]
    sector_ic_rows = ic_summary[
        (ic_summary.get("target", pd.Series(dtype=str)) == "sector_excess")
        & (pd.to_numeric(ic_summary.get("horizon_days"), errors="coerce") == horizon)
    ]
    monotonic_rows = monotonicity[
        pd.to_numeric(monotonicity.get("horizon_days"), errors="coerce") == horizon
    ]
    market_ic = _safe_number(
        market_ic_rows.iloc[0].get("mean_ic")
        if not market_ic_rows.empty else None
    )
    sector_ic = _safe_number(
        sector_ic_rows.iloc[0].get("mean_ic")
        if not sector_ic_rows.empty else None
    )
    monotonic_status = (
        str(monotonic_rows.iloc[0].get("monotonicity_status"))
        if not monotonic_rows.empty else "INCONCLUSIVE"
    )

    minimum_count = int(
        success.get(
            "minimum_outcome_count",
            policy.get("minimum_outcome_count", 100),
        )
    )
    required_status = str(
        success.get(
            "required_robustness_status",
            policy.get("required_robustness_status", "ROBUST"),
        )
    )
    maximum_q = float(
        success.get(
            "maximum_fdr_q_value",
            policy.get("maximum_fdr_q_value", 0.05),
        )
    )
    minimum_market_rate = float(
        success.get("minimum_market_outperformance_rate", 0.55)
    )
    minimum_sector_rate = float(
        success.get("minimum_sector_outperformance_rate", 0.55)
    )

    checks = [
        (
            "minimum_outcome_count",
            _safe_number(row.get("count")),
            f">={minimum_count}",
            (_safe_number(row.get("count")) or 0) >= minimum_count,
        ),
        (
            "positive_market_excess_after_30bps",
            _safe_number(row.get("net_average_market_excess")),
            ">0",
            (_safe_number(row.get("net_average_market_excess")) or -math.inf) > 0,
        ),
        (
            "positive_sector_excess_after_30bps",
            _safe_number(row.get("net_average_sector_excess")),
            ">0",
            (_safe_number(row.get("net_average_sector_excess")) or -math.inf) > 0,
        ),
        (
            "market_outperformance_rate",
            _safe_number(row.get("market_outperformance_rate")),
            f">={minimum_market_rate:.2f}",
            (_safe_number(row.get("market_outperformance_rate")) or 0) >= minimum_market_rate,
        ),
        (
            "sector_outperformance_rate",
            _safe_number(row.get("sector_outperformance_rate")),
            f">={minimum_sector_rate:.2f}",
            (_safe_number(row.get("sector_outperformance_rate")) or 0) >= minimum_sector_rate,
        ),
        (
            "fdr_q_value",
            _safe_number(row.get("fdr_q_value")),
            f"<={maximum_q:.2f}",
            (
                _safe_number(row.get("fdr_q_value")) is not None
                and _safe_number(row.get("fdr_q_value")) <= maximum_q
            ),
        ),
        (
            "robustness_status",
            str(row.get("robustness_status", "")),
            required_status,
            str(row.get("robustness_status", "")) == required_status,
        ),
        (
            "positive_market_information_coefficient",
            market_ic,
            ">0",
            market_ic is not None and market_ic > 0,
        ),
        (
            "positive_sector_information_coefficient",
            sector_ic,
            ">0",
            sector_ic is not None and sector_ic > 0,
        ),
        (
            "decile_monotonicity",
            monotonic_status,
            "PASS",
            monotonic_status == "PASS",
        ),
    ]
    readiness = pd.DataFrame([
        {
            "experiment_id": EXPERIMENT_ID,
            "horizon_days": horizon,
            "criterion": criterion,
            "actual": actual,
            "required": required,
            "passed": bool(passed),
        }
        for criterion, actual, required, passed in checks
    ])
    evidence_ready = bool(len(readiness) and readiness["passed"].all())
    manual_approval = experiment.get("manual_approval", {}) or {}
    manually_approved = bool(manual_approval.get("approved") is True)
    automatic_promotion = bool(
        experiment.get("automatic_promotion", policy.get("automatic_promotion", False))
    )
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "registered_status": experiment.get("status", "missing"),
        "evidence_ready": evidence_ready,
        "manual_approval_valid": manually_approved,
        "automatic_promotion": automatic_promotion,
        "promotion_status": (
            "ELIGIBLE_FOR_MANUAL_REVIEW"
            if evidence_ready and not manually_approved
            else "APPROVED_NOT_AUTOMATIC"
            if evidence_ready and manually_approved and not automatic_promotion
            else "EVIDENCE_ACCUMULATING"
        ),
        "production_change_authorized": False,
    }
    return readiness, summary


def build_methodology() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "item": "Signal construction",
            "detail": "Each report date recomputes relative strength only from that date's stored 20/60-day returns and cross-section.",
        },
        {
            "item": "Signal universe",
            "detail": "Momentum Top100 on each report date. Original Momentum score and ranking are not changed.",
        },
        {
            "item": "Forward horizon",
            "detail": "The 5th, 10th, and 20th later stored observation for the same code.",
        },
        {
            "item": "Market benchmark",
            "detail": "Median return of codes present on both the entry and exit report dates.",
        },
        {
            "item": "Sector benchmark",
            "detail": "Median return of entry-date same-sector codes present on the exit report date.",
        },
        {
            "item": "Information coefficient",
            "detail": "Daily Spearman correlation between relative-strength score and future raw/market-excess/sector-excess return.",
        },
        {
            "item": "Robustness",
            "detail": "30 bps round-trip cost, early/late split, leave-one-sector-out, sign-flip p-value, and Benjamini-Hochberg FDR.",
        },
        {
            "item": "Governance",
            "detail": "Research only. No registry status, production threshold, paper position, or live state is changed automatically.",
        },
    ])


def _empty_outputs() -> dict[str, pd.DataFrame]:
    return {
        "signals": pd.DataFrame(columns=SIGNAL_COLUMNS),
        "outcomes": pd.DataFrame(columns=OUTCOME_COLUMNS),
        "bucket_performance": pd.DataFrame(),
        "daily_ic": pd.DataFrame(),
        "ic_summary": pd.DataFrame(),
        "monotonicity": pd.DataFrame(),
        "stability": pd.DataFrame(),
        "robustness": pd.DataFrame(),
        "readiness": pd.DataFrame(),
    }


def run_evidence_lab(
    history: pd.DataFrame,
    registry_path: str = DEFAULT_REGISTRY,
    top_limit: int = 100,
    max_dates: int | None = None,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    cost_bps: int = DEFAULT_COST_BPS,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    if history is None or history.empty:
        outputs = _empty_outputs()
        summary = {
            "experiment_id": EXPERIMENT_ID,
            "registered_status": "unknown",
            "evidence_ready": False,
            "manual_approval_valid": False,
            "automatic_promotion": False,
            "promotion_status": "INSUFFICIENT_SOURCE_DATA",
            "production_change_authorized": False,
        }
        return outputs, summary

    signals = build_signal_panel(history, top_limit=top_limit, max_dates=max_dates)
    outcomes = build_forward_outcomes(signals, history, horizons=horizons)
    bucket_performance = build_bucket_performance(outcomes)
    daily_ic = build_daily_information_coefficients(outcomes)
    ic_summary = build_ic_summary(daily_ic)
    monotonicity = build_monotonicity(bucket_performance)
    stability = build_rank_stability(signals)
    robustness = build_robustness(outcomes, cost_bps=cost_bps)
    readiness, readiness_summary = build_readiness(
        robustness,
        ic_summary,
        monotonicity,
        load_experiment(registry_path),
        horizon=10,
    )
    outputs = {
        "signals": signals,
        "outcomes": outcomes,
        "bucket_performance": bucket_performance,
        "daily_ic": daily_ic,
        "ic_summary": ic_summary,
        "monotonicity": monotonicity,
        "stability": stability,
        "robustness": robustness,
        "readiness": readiness,
    }
    return outputs, readiness_summary


def write_outputs(
    outputs: dict[str, pd.DataFrame],
    readiness_summary: dict[str, Any],
    output_dir: str,
    history_path: str,
    history: pd.DataFrame,
    before_hashes: dict[str, str],
    after_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "excel": target / "relative_strength_evidence.xlsx",
        "signals": target / "relative_strength_signals.csv",
        "outcomes": target / "relative_strength_outcomes.csv",
        "bucket_performance": target / "relative_strength_bucket_performance.csv",
        "daily_ic": target / "relative_strength_daily_ic.csv",
        "ic_summary": target / "relative_strength_ic_summary.csv",
        "monotonicity": target / "relative_strength_monotonicity.csv",
        "stability": target / "relative_strength_stability.csv",
        "robustness": target / "relative_strength_robustness.csv",
        "readiness": target / "relative_strength_readiness.csv",
        "manifest": target / "relative_strength_evidence_manifest.json",
    }
    mapping = {
        "signals": "signals",
        "outcomes": "outcomes",
        "bucket_performance": "bucket_performance",
        "daily_ic": "daily_ic",
        "ic_summary": "ic_summary",
        "monotonicity": "monotonicity",
        "stability": "stability",
        "robustness": "robustness",
        "readiness": "readiness",
    }
    for key, output_key in mapping.items():
        outputs.get(output_key, pd.DataFrame()).to_csv(paths[key], index=False)

    after = after_hashes or replay.live_state_hashes()
    mutated = [
        path for path, digest in before_hashes.items()
        if digest != after.get(path, "")
    ]
    signals = outputs.get("signals", pd.DataFrame())
    outcomes = outputs.get("outcomes", pd.DataFrame())
    manifest = {
        "evidence_version": EVIDENCE_VERSION,
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "experiment_id": EXPERIMENT_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_history_path": history_path,
        "source_history_sha256": replay.sha256_file(history_path),
        "source_row_count": int(len(history)),
        "source_date_count": int(history["date"].nunique()) if not history.empty else 0,
        "signal_count": int(len(signals)),
        "outcome_count": int(len(outcomes)),
        "first_signal_date": (
            str(signals["signal_date"].min()) if not signals.empty else ""
        ),
        "last_signal_date": (
            str(signals["signal_date"].max()) if not signals.empty else ""
        ),
        "benchmark_coverage": (
            float(outcomes["market_excess_return"].notna().mean())
            if not outcomes.empty else 0.0
        ),
        "live_state_mutations": mutated,
        "live_state_unchanged": not mutated,
        "research_only": True,
        "automatic_promotion": False,
        "production_change_authorized": False,
        **readiness_summary,
    }
    paths["manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    sheet_map = {
        "signals": "Signals",
        "outcomes": "Outcomes",
        "bucket_performance": "Bucket Performance",
        "daily_ic": "Daily IC",
        "ic_summary": "IC Summary",
        "monotonicity": "Monotonicity",
        "stability": "Rank Stability",
        "robustness": "Robustness",
        "readiness": "Experiment Readiness",
    }
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(
            writer, sheet_name="Evidence Summary", index=False
        )
        for key, sheet_name in sheet_map.items():
            outputs.get(key, pd.DataFrame()).to_excel(
                writer, sheet_name=sheet_name, index=False
            )
        build_methodology().to_excel(
            writer, sheet_name="Methodology", index=False
        )
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {
        "paths": {key: str(value) for key, value in paths.items()},
        "manifest": manifest,
    }


def parse_horizons(value: str) -> tuple[int, ...]:
    horizons = tuple(
        sorted({
            int(item.strip())
            for item in str(value).split(",")
            if item.strip() and int(item.strip()) > 0
        })
    )
    if not horizons:
        raise argparse.ArgumentTypeError("at least one positive horizon is required")
    return horizons


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build governed relative-strength evidence without changing production state."
    )
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--jpx-cache", default=DEFAULT_JPX_CACHE)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-limit", type=int, default=100)
    parser.add_argument("--max-dates", type=int)
    parser.add_argument("--horizons", type=parse_horizons, default=DEFAULT_HORIZONS)
    parser.add_argument("--cost-bps", type=int, default=DEFAULT_COST_BPS)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before_hashes = replay.live_state_hashes()
    history = load_history(args.history, args.jpx_cache)
    outputs, readiness_summary = run_evidence_lab(
        history,
        registry_path=args.registry,
        top_limit=args.top_limit,
        max_dates=args.max_dates,
        horizons=args.horizons,
        cost_bps=args.cost_bps,
    )
    after_analysis_hashes = replay.live_state_hashes()
    result = write_outputs(
        outputs,
        readiness_summary,
        args.output_dir,
        args.history,
        history,
        before_hashes,
        after_hashes=after_analysis_hashes,
    )
    manifest = result["manifest"]
    if args.strict:
        if not manifest["live_state_unchanged"]:
            raise RuntimeError(
                f"live state mutated during evidence analysis: {manifest['live_state_mutations']}"
            )
        if len(history) and manifest["benchmark_coverage"] < 0.90:
            raise RuntimeError(
                f"benchmark coverage below 90%: {manifest['benchmark_coverage']:.1%}"
            )
        if manifest["automatic_promotion"]:
            raise RuntimeError("automatic promotion must remain disabled")
        if manifest["production_change_authorized"]:
            raise RuntimeError("evidence lab cannot authorize production changes")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
