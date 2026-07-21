"""Analyze explicit-period historical rankings across production, Healthy v1, and v2.

The analysis uses next-available-session adjusted open as entry and adjusted closes at
1/3/5/10/20 sessions. It reports event-weighted, date-weighted, first-pick, and
non-overlapping samples, plus market/sector-relative results, MFE/MAE, quarter, sector,
feature, concentration, turnover, and paired method comparisons.

Historical current-universe backfills remain research-only and non-promotable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

import healthy_momentum_v2
import main

VERSION = "2026-07-21-historical-oos-analysis-v1"
METHODS = {
    "production": ("rank", None, "score"),
    "healthy_v1": ("healthy_rank", "healthy_eligible", "healthy_selection_score"),
    "balanced_v2": ("healthy_v2_rank", "healthy_v2_eligible", "healthy_v2_selection_score"),
}
DEFAULT_HORIZONS = (1, 3, 5, 10, 20)
DEFAULT_TOP_SIZES = (10, 30, 100)
ROUND_TRIP_COST_BPS = 20.0


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_inputs(
    ranking_path: str,
    panel_path: str,
    backfill_manifest_path: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    ranking = pd.read_csv(ranking_path, dtype={"code": str}, low_memory=False)
    panel = pd.read_csv(panel_path, dtype={"code": str}, low_memory=False)
    manifest = json.loads(Path(backfill_manifest_path).read_text(encoding="utf-8"))
    if manifest.get("promotion_evidence_allowed") is not False:
        raise ValueError("historical OOS analysis requires non-promotable source data")
    if manifest.get("production_state_mutations") not in ([], None):
        raise ValueError("historical source mutated production state")
    required_ranking = {"date", "code", "rank", "score", "sector33"}
    required_panel = {
        "date",
        "code",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
    }
    if not required_ranking.issubset(ranking.columns):
        raise ValueError(f"ranking missing columns: {required_ranking - set(ranking.columns)}")
    if not required_panel.issubset(panel.columns):
        raise ValueError(f"panel missing columns: {required_panel - set(panel.columns)}")
    ranking["code"] = ranking["code"].map(main.normalize_code)
    ranking["date"] = pd.to_datetime(ranking["date"], errors="coerce").dt.normalize()
    ranking = ranking.dropna(subset=["date", "code"])
    panel["code"] = panel["code"].map(main.normalize_code)
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.normalize()
    for column in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"):
        panel[column] = pd.to_numeric(panel[column], errors="coerce")
    panel = panel.dropna(
        subset=["date", "code", "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"]
    )
    panel = panel[
        (panel["adjusted_open"] > 0)
        & (panel["adjusted_high"] > 0)
        & (panel["adjusted_low"] > 0)
        & (panel["adjusted_close"] > 0)
    ]
    panel = panel.drop_duplicates(["date", "code"], keep="last").sort_values(["code", "date"])
    return ranking, panel.reset_index(drop=True), manifest


def attach_methods(ranking: pd.DataFrame) -> pd.DataFrame:
    enriched = healthy_momentum_v2.attach(ranking)
    for column in ("rank", "healthy_rank", "healthy_v2_rank"):
        enriched[column] = pd.to_numeric(enriched[column], errors="coerce")
    return enriched


def price_lookup(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        str(code): group.sort_values("date").reset_index(drop=True)
        for code, group in panel.groupby("code", sort=False)
    }


def one_outcome(
    prices: pd.DataFrame,
    signal_date: pd.Timestamp,
    horizon: int,
) -> dict[str, Any] | None:
    dates = prices["date"].to_numpy(dtype="datetime64[ns]")
    entry_position = int(np.searchsorted(dates, np.datetime64(signal_date), side="right"))
    exit_position = entry_position + int(horizon) - 1
    if entry_position >= len(prices) or exit_position >= len(prices):
        return None
    window = prices.iloc[entry_position : exit_position + 1]
    entry = prices.iloc[entry_position]
    exit_row = prices.iloc[exit_position]
    entry_price = float(entry["adjusted_open"])
    exit_price = float(exit_row["adjusted_close"])
    if not np.isfinite(entry_price) or not np.isfinite(exit_price) or entry_price <= 0:
        return None
    gross_return = exit_price / entry_price - 1.0
    maximum_high = float(pd.to_numeric(window["adjusted_high"], errors="coerce").max())
    minimum_low = float(pd.to_numeric(window["adjusted_low"], errors="coerce").min())
    mfe = maximum_high / entry_price - 1.0
    mae = minimum_low / entry_price - 1.0
    return {
        "entry_date": pd.Timestamp(entry["date"]),
        "entry_price": entry_price,
        "exit_date": pd.Timestamp(exit_row["date"]),
        "exit_price": exit_price,
        "gross_return": gross_return,
        "net_return": gross_return - ROUND_TRIP_COST_BPS / 10_000.0,
        "mfe": mfe,
        "mae": mae,
    }


def build_universe_outcomes(
    ranking: pd.DataFrame,
    panel_by_code: dict[str, pd.DataFrame],
    horizons: Iterable[int],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    base_columns = [
        "date",
        "code",
        "name",
        "sector33",
        "rank",
        "score",
        "return_5d",
        "return_20d",
        "return_60d",
        "ma20_deviation",
        "ma60_deviation",
        "volume_ratio",
        "trading_value",
        "healthy_rank",
        "healthy_selection_score",
        "healthy_v2_rank",
        "healthy_v2_selection_score",
        "healthy_v2_confirmation_score",
        "healthy_v2_confirmation_state",
        "healthy_v2_caution_reasons",
    ]
    available_columns = [column for column in base_columns if column in ranking.columns]
    for row in ranking[available_columns].itertuples(index=False):
        payload = row._asdict()
        code = main.normalize_code(payload.get("code", ""))
        prices = panel_by_code.get(code)
        if prices is None or prices.empty:
            continue
        signal_date = pd.Timestamp(payload["date"])
        for horizon in horizons:
            outcome = one_outcome(prices, signal_date, int(horizon))
            if outcome is None:
                continue
            records.append(
                {
                    "signal_date": signal_date,
                    "code": code,
                    "name": payload.get("name", ""),
                    "sector33": payload.get("sector33", ""),
                    "horizon_sessions": int(horizon),
                    **{key: payload.get(key) for key in available_columns if key not in {"date", "code", "name", "sector33"}},
                    **outcome,
                }
            )
    outcomes = pd.DataFrame(records)
    if outcomes.empty:
        return outcomes
    outcomes["sector33"] = outcomes["sector33"].fillna("").astype(str)
    benchmark = outcomes.groupby(["signal_date", "horizon_sessions"], dropna=False)[
        "gross_return"
    ].median().rename("market_median_return")
    sector_benchmark = outcomes.groupby(
        ["signal_date", "horizon_sessions", "sector33"], dropna=False
    )["gross_return"].median().rename("sector_median_return")
    outcomes = outcomes.join(benchmark, on=["signal_date", "horizon_sessions"])
    outcomes = outcomes.join(
        sector_benchmark, on=["signal_date", "horizon_sessions", "sector33"]
    )
    outcomes["market_excess_gross"] = outcomes["gross_return"] - outcomes["market_median_return"]
    outcomes["market_excess_net"] = outcomes["net_return"] - outcomes["market_median_return"]
    outcomes["sector_excess_gross"] = outcomes["gross_return"] - outcomes["sector_median_return"]
    outcomes["sector_excess_net"] = outcomes["net_return"] - outcomes["sector_median_return"]
    outcomes["quarter"] = outcomes["signal_date"].dt.to_period("Q").astype(str)
    outcomes["month"] = outcomes["signal_date"].dt.to_period("M").astype(str)
    daily_breadth = outcomes.drop_duplicates(["signal_date", "code"]).groupby("signal_date")[
        "return_20d"
    ].median()
    if daily_breadth.notna().nunique() >= 5:
        ranks = daily_breadth.rank(method="average", pct=True)
        breadth_quintile = np.ceil(ranks.clip(lower=1e-12, upper=1.0) * 5).astype(int)
        outcomes = outcomes.join(breadth_quintile.rename("market_breadth_quintile"), on="signal_date")
    else:
        outcomes["market_breadth_quintile"] = pd.NA
    return outcomes


def select_method_events(
    ranking: pd.DataFrame,
    universe_outcomes: pd.DataFrame,
    top_limit: int,
) -> pd.DataFrame:
    selections: list[pd.DataFrame] = []
    for method, (rank_column, eligible_column, score_column) in METHODS.items():
        for signal_date, group in ranking.groupby("date", sort=True):
            candidates = group.copy()
            if eligible_column:
                candidates = candidates[candidates[eligible_column].fillna(False)]
            candidates = candidates.sort_values(rank_column, na_position="last").head(top_limit)
            if candidates.empty:
                continue
            selected_columns = [
                "date",
                "code",
                "name",
                "sector33",
                rank_column,
                score_column,
                "rank",
                "score",
                "return_5d",
                "return_20d",
                "return_60d",
                "ma20_deviation",
                "ma60_deviation",
                "volume_ratio",
                "trading_value",
                "healthy_v2_confirmation_score",
                "healthy_v2_confirmation_state",
                "healthy_v2_caution_reasons",
            ]
            selected_columns = [column for column in selected_columns if column in candidates.columns]
            selected = candidates[selected_columns].copy()
            selected = selected.rename(columns={"date": "signal_date", rank_column: "method_rank", score_column: "method_score"})
            selected["method"] = method
            selections.append(selected)
    if not selections:
        return pd.DataFrame()
    selection_table = pd.concat(selections, ignore_index=True, sort=False)
    selection_table["code"] = selection_table["code"].map(main.normalize_code)
    selection_table["signal_date"] = pd.to_datetime(selection_table["signal_date"], errors="coerce").dt.normalize()
    outcome_columns = [
        "signal_date",
        "code",
        "horizon_sessions",
        "entry_date",
        "entry_price",
        "exit_date",
        "exit_price",
        "gross_return",
        "net_return",
        "mfe",
        "mae",
        "market_median_return",
        "sector_median_return",
        "market_excess_gross",
        "market_excess_net",
        "sector_excess_gross",
        "sector_excess_net",
        "quarter",
        "month",
        "market_breadth_quintile",
    ]
    merged = selection_table.merge(
        universe_outcomes[outcome_columns],
        on=["signal_date", "code"],
        how="inner",
        validate="one_to_many",
    )
    return merged.sort_values(["method", "signal_date", "method_rank", "horizon_sessions"]).reset_index(drop=True)


def bootstrap_date_mean_ci(
    frame: pd.DataFrame,
    column: str,
    iterations: int = 2000,
    seed: int = 20250721,
) -> tuple[float | None, float | None]:
    if frame.empty:
        return None, None
    daily = frame.groupby("signal_date")[column].mean().dropna().to_numpy(dtype=float)
    if len(daily) < 2:
        value = float(daily.mean()) if len(daily) else None
        return value, value
    rng = np.random.default_rng(seed)
    samples = rng.choice(daily, size=(iterations, len(daily)), replace=True).mean(axis=1)
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return float(lower), float(upper)


def mark_first_pick(frame: pd.DataFrame) -> pd.Series:
    return frame["signal_date"].eq(frame.groupby("code")["signal_date"].transform("min"))


def mark_non_overlapping(frame: pd.DataFrame) -> pd.Series:
    keep = pd.Series(False, index=frame.index)
    for _, group in frame.sort_values(["code", "entry_date", "exit_date"]).groupby("code", sort=False):
        last_exit: pd.Timestamp | None = None
        for index, row in group.iterrows():
            entry_date = pd.Timestamp(row["entry_date"])
            exit_date = pd.Timestamp(row["exit_date"])
            if last_exit is None or entry_date > last_exit:
                keep.loc[index] = True
                last_exit = exit_date
    return keep


def sample_frames(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "ALL_EVENTS": frame,
        "FIRST_PICK_PER_CODE": frame[mark_first_pick(frame)],
        "NON_OVERLAPPING_PER_CODE": frame[mark_non_overlapping(frame)],
    }


def summarize_events(events: pd.DataFrame, top_sizes: Iterable[int]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for method in METHODS:
        method_frame = events[events["method"] == method]
        for top_size in top_sizes:
            top_frame = method_frame[pd.to_numeric(method_frame["method_rank"], errors="coerce") <= int(top_size)]
            for horizon, horizon_frame in top_frame.groupby("horizon_sessions", sort=True):
                for sample_type, sample in sample_frames(horizon_frame).items():
                    if sample.empty:
                        continue
                    lower, upper = bootstrap_date_mean_ci(sample, "net_return")
                    records.append(
                        {
                            "method": method,
                            "top_size": int(top_size),
                            "horizon_sessions": int(horizon),
                            "sample_type": sample_type,
                            "observations": len(sample),
                            "unique_stocks": sample["code"].nunique(),
                            "signal_dates": sample["signal_date"].nunique(),
                            "mean_gross_return": sample["gross_return"].mean(),
                            "mean_net_return": sample["net_return"].mean(),
                            "median_net_return": sample["net_return"].median(),
                            "win_rate_net": sample["net_return"].gt(0).mean(),
                            "mean_date_weighted_net_return": sample.groupby("signal_date")["net_return"].mean().mean(),
                            "mean_market_excess_net": sample["market_excess_net"].mean(),
                            "beat_market_rate_net": sample["market_excess_net"].gt(0).mean(),
                            "mean_sector_excess_net": sample["sector_excess_net"].mean(),
                            "beat_sector_rate_net": sample["sector_excess_net"].gt(0).mean(),
                            "mean_mfe": sample["mfe"].mean(),
                            "mean_mae": sample["mae"].mean(),
                            "net_return_bootstrap_ci_low": lower,
                            "net_return_bootstrap_ci_high": upper,
                        }
                    )
    return pd.DataFrame(records)


def paired_method_comparison(
    events: pd.DataFrame,
    top_sizes: Iterable[int],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    comparisons = [("healthy_v1", "production"), ("balanced_v2", "healthy_v1"), ("balanced_v2", "production")]
    for top_size in top_sizes:
        subset = events[pd.to_numeric(events["method_rank"], errors="coerce") <= int(top_size)]
        daily = subset.groupby(["method", "horizon_sessions", "signal_date"])["net_return"].mean().reset_index()
        for horizon, horizon_frame in daily.groupby("horizon_sessions"):
            pivot = horizon_frame.pivot(index="signal_date", columns="method", values="net_return")
            for left, right in comparisons:
                pair = pivot[[left, right]].dropna() if {left, right}.issubset(pivot.columns) else pd.DataFrame()
                if pair.empty:
                    continue
                delta = pair[left] - pair[right]
                lower, upper = bootstrap_date_mean_ci(
                    pd.DataFrame({"signal_date": pair.index, "delta": delta.values}), "delta"
                )
                records.append(
                    {
                        "left_method": left,
                        "right_method": right,
                        "top_size": int(top_size),
                        "horizon_sessions": int(horizon),
                        "paired_dates": len(pair),
                        "mean_daily_delta": delta.mean(),
                        "median_daily_delta": delta.median(),
                        "left_outperformance_rate": delta.gt(0).mean(),
                        "delta_ci_low": lower,
                        "delta_ci_high": upper,
                    }
                )
    return pd.DataFrame(records)


def grouped_summary(
    events: pd.DataFrame,
    top_size: int,
    horizon: int,
    group_column: str,
) -> pd.DataFrame:
    subset = events[
        (pd.to_numeric(events["method_rank"], errors="coerce") <= int(top_size))
        & (events["horizon_sessions"] == int(horizon))
    ].copy()
    records: list[dict[str, Any]] = []
    for (method, group_value), group in subset.groupby(["method", group_column], dropna=False):
        records.append(
            {
                "method": method,
                "top_size": int(top_size),
                "horizon_sessions": int(horizon),
                group_column: group_value,
                "observations": len(group),
                "unique_stocks": group["code"].nunique(),
                "signal_dates": group["signal_date"].nunique(),
                "mean_net_return": group["net_return"].mean(),
                "median_net_return": group["net_return"].median(),
                "win_rate_net": group["net_return"].gt(0).mean(),
                "mean_market_excess_net": group["market_excess_net"].mean(),
                "mean_sector_excess_net": group["sector_excess_net"].mean(),
                "mean_mfe": group["mfe"].mean(),
                "mean_mae": group["mae"].mean(),
            }
        )
    return pd.DataFrame(records)


def feature_bands(events: pd.DataFrame, top_size: int, horizon: int) -> pd.DataFrame:
    subset = events[
        (pd.to_numeric(events["method_rank"], errors="coerce") <= int(top_size))
        & (events["horizon_sessions"] == int(horizon))
    ].copy()
    definitions = {
        "method_rank_band": pd.cut(
            pd.to_numeric(subset["method_rank"], errors="coerce"),
            bins=[0, 10, 30, 50, 100],
            labels=["1-10", "11-30", "31-50", "51-100"],
        ),
        "return_20d_band": pd.cut(
            pd.to_numeric(subset["return_20d"], errors="coerce"),
            bins=[-np.inf, 0.05, 0.10, 0.20, 0.40, np.inf],
            labels=["<=5%", "5-10%", "10-20%", "20-40%", ">40%"],
        ),
        "ma20_deviation_band": pd.cut(
            pd.to_numeric(subset["ma20_deviation"], errors="coerce"),
            bins=[-np.inf, 0.05, 0.10, 0.15, 0.20, np.inf],
            labels=["<=5%", "5-10%", "10-15%", "15-20%", ">20%"],
        ),
        "volume_ratio_band": pd.cut(
            pd.to_numeric(subset["volume_ratio"], errors="coerce"),
            bins=[-np.inf, 1, 2, 3, 5, 10, np.inf],
            labels=["<=1x", "1-2x", "2-3x", "3-5x", "5-10x", ">10x"],
        ),
    }
    records: list[dict[str, Any]] = []
    for feature, bands in definitions.items():
        work = subset.assign(feature_value=bands)
        for (method, value), group in work.groupby(["method", "feature_value"], observed=True, dropna=False):
            records.append(
                {
                    "feature": feature,
                    "band": str(value),
                    "method": method,
                    "top_size": int(top_size),
                    "horizon_sessions": int(horizon),
                    "observations": len(group),
                    "mean_net_return": group["net_return"].mean(),
                    "median_net_return": group["net_return"].median(),
                    "win_rate_net": group["net_return"].gt(0).mean(),
                    "mean_market_excess_net": group["market_excess_net"].mean(),
                }
            )
    if "healthy_v2_confirmation_state" in subset.columns:
        for (method, value), group in subset.groupby(["method", "healthy_v2_confirmation_state"], dropna=False):
            records.append(
                {
                    "feature": "healthy_v2_confirmation_state",
                    "band": str(value),
                    "method": method,
                    "top_size": int(top_size),
                    "horizon_sessions": int(horizon),
                    "observations": len(group),
                    "mean_net_return": group["net_return"].mean(),
                    "median_net_return": group["net_return"].median(),
                    "win_rate_net": group["net_return"].gt(0).mean(),
                    "mean_market_excess_net": group["market_excess_net"].mean(),
                }
            )
    return pd.DataFrame(records)


def overlap_and_turnover(events: pd.DataFrame, top_sizes: Iterable[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    unique = events.drop_duplicates(["method", "signal_date", "code"])
    overlap_records: list[dict[str, Any]] = []
    turnover_records: list[dict[str, Any]] = []
    method_pairs = [("production", "healthy_v1"), ("production", "balanced_v2"), ("healthy_v1", "balanced_v2")]
    for top_size in top_sizes:
        top = unique[pd.to_numeric(unique["method_rank"], errors="coerce") <= int(top_size)]
        sets = {
            (method, date): set(group["code"])
            for (method, date), group in top.groupby(["method", "signal_date"])
        }
        dates = sorted(top["signal_date"].unique())
        for date_value in dates:
            for left, right in method_pairs:
                left_set = sets.get((left, date_value), set())
                right_set = sets.get((right, date_value), set())
                union = left_set | right_set
                overlap_records.append(
                    {
                        "signal_date": date_value,
                        "top_size": int(top_size),
                        "left_method": left,
                        "right_method": right,
                        "left_count": len(left_set),
                        "right_count": len(right_set),
                        "intersection_count": len(left_set & right_set),
                        "jaccard": len(left_set & right_set) / len(union) if union else np.nan,
                    }
                )
        for method in METHODS:
            method_dates = sorted(date for (candidate_method, date) in sets if candidate_method == method)
            for previous, current in zip(method_dates, method_dates[1:]):
                previous_set = sets[(method, previous)]
                current_set = sets[(method, current)]
                union = previous_set | current_set
                turnover_records.append(
                    {
                        "method": method,
                        "top_size": int(top_size),
                        "previous_date": previous,
                        "signal_date": current,
                        "previous_count": len(previous_set),
                        "current_count": len(current_set),
                        "retained_count": len(previous_set & current_set),
                        "jaccard": len(previous_set & current_set) / len(union) if union else np.nan,
                        "turnover_rate": 1.0 - len(previous_set & current_set) / max(len(previous_set), 1),
                    }
                )
    return pd.DataFrame(overlap_records), pd.DataFrame(turnover_records)


def concentration(events: pd.DataFrame, top_sizes: Iterable[int]) -> pd.DataFrame:
    unique = events.drop_duplicates(["method", "signal_date", "code"])
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = unique[pd.to_numeric(unique["method_rank"], errors="coerce") <= int(top_size)]
        for (method, signal_date), group in top.groupby(["method", "signal_date"]):
            shares = group["sector33"].fillna("").value_counts(normalize=True)
            records.append(
                {
                    "method": method,
                    "signal_date": signal_date,
                    "top_size": int(top_size),
                    "selected_count": len(group),
                    "sector_count": shares.size,
                    "sector_hhi": float((shares**2).sum()) if len(shares) else np.nan,
                    "largest_sector_share": float(shares.max()) if len(shares) else np.nan,
                }
            )
    return pd.DataFrame(records)


def stock_summary(events: pd.DataFrame, top_size: int, horizon: int) -> pd.DataFrame:
    subset = events[
        (pd.to_numeric(events["method_rank"], errors="coerce") <= int(top_size))
        & (events["horizon_sessions"] == int(horizon))
    ]
    records: list[dict[str, Any]] = []
    for (method, code, name, sector), group in subset.groupby(
        ["method", "code", "name", "sector33"], dropna=False
    ):
        records.append(
            {
                "method": method,
                "top_size": int(top_size),
                "horizon_sessions": int(horizon),
                "code": code,
                "name": name,
                "sector33": sector,
                "pick_count": len(group),
                "first_signal_date": group["signal_date"].min(),
                "last_signal_date": group["signal_date"].max(),
                "best_method_rank": pd.to_numeric(group["method_rank"], errors="coerce").min(),
                "mean_net_return": group["net_return"].mean(),
                "median_net_return": group["net_return"].median(),
                "win_rate_net": group["net_return"].gt(0).mean(),
                "mean_market_excess_net": group["market_excess_net"].mean(),
                "best_event_net_return": group["net_return"].max(),
                "worst_event_net_return": group["net_return"].min(),
                "mean_mfe": group["mfe"].mean(),
                "mean_mae": group["mae"].mean(),
            }
        )
    return pd.DataFrame(records).sort_values(["method", "mean_net_return"], ascending=[True, False])


def winners_losers(events: pd.DataFrame, top_size: int, horizon: int, limit: int = 50) -> pd.DataFrame:
    subset = events[
        (pd.to_numeric(events["method_rank"], errors="coerce") <= int(top_size))
        & (events["horizon_sessions"] == int(horizon))
    ].copy()
    records: list[pd.DataFrame] = []
    for method, group in subset.groupby("method"):
        winners = group.nlargest(limit, "net_return").assign(result_bucket="WINNER")
        losers = group.nsmallest(limit, "net_return").assign(result_bucket="LOSER")
        records.extend([winners, losers])
    return pd.concat(records, ignore_index=True, sort=False) if records else pd.DataFrame()


def render_markdown_report(
    manifest: dict[str, Any],
    summary: pd.DataFrame,
    paired: pd.DataFrame,
) -> str:
    focus = summary[
        (summary["sample_type"] == "ALL_EVENTS")
        & (summary["top_size"].isin([10, 30, 100]))
        & (summary["horizon_sessions"].isin([5, 20]))
    ].copy()
    lines = [
        "# 2025 Historical Ranking OOS Study",
        "",
        "## Scope",
        "",
        f"- Evaluation: {manifest.get('evaluation_start')} to {manifest.get('evaluation_end')}",
        f"- Ranking dates: {manifest.get('ranking_date_count')}",
        f"- Selected universe: {manifest.get('selected_universe_count')}",
        f"- Universe bias: {manifest.get('universe_bias')}",
        "- Entry: next available session adjusted open",
        "- Exits: adjusted close after 1/3/5/10/20 sessions",
        f"- Round-trip cost: {ROUND_TRIP_COST_BPS:.0f} bps",
        "- Research-only; not promotion evidence",
        "",
        "## Core Results",
        "",
        "| Method | Top | Horizon | N | Mean net | Median | Win rate | Market excess |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in focus.sort_values(["horizon_sessions", "top_size", "method"]).itertuples(index=False):
        lines.append(
            f"| {row.method} | {row.top_size} | {row.horizon_sessions} | {row.observations} | "
            f"{row.mean_net_return:.3%} | {row.median_net_return:.3%} | {row.win_rate_net:.1%} | "
            f"{row.mean_market_excess_net:.3%} |"
        )
    lines += ["", "## Paired Date Comparisons", ""]
    key = paired[paired["horizon_sessions"].isin([5, 20])].sort_values(
        ["horizon_sessions", "top_size", "left_method", "right_method"]
    )
    lines += [
        "| Left | Right | Top | Horizon | Paired dates | Mean daily delta | Outperformance |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in key.itertuples(index=False):
        lines.append(
            f"| {row.left_method} | {row.right_method} | {row.top_size} | {row.horizon_sessions} | "
            f"{row.paired_dates} | {row.mean_daily_delta:.3%} | {row.left_outperformance_rate:.1%} |"
        )
    lines += [
        "",
        "## Limitations",
        "",
        "- The universe is reconstructed from the current JPX cache, not point-in-time membership.",
        "- Delisted names and some renamed/reorganized securities may be absent.",
        "- Yahoo Finance adjusted prices can contain missing or revised observations.",
        "- Repeated selections create overlapping exposures; non-overlapping samples are reported separately.",
        "- The 2025 period precedes the 2026 v1/v2 design, but this remains retrospective research.",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(
    output_dir: Path,
    tables: dict[str, pd.DataFrame],
    manifest: dict[str, Any],
    report: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, table in tables.items():
        table.to_csv(output_dir / filename, index=False)
    (output_dir / "analysis_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze historical OOS rankings")
    parser.add_argument("--ranking", default="output/oos-2025/historical_ranking.csv")
    parser.add_argument("--prices", default="output/oos-2025/historical_price_panel.csv")
    parser.add_argument("--backfill-manifest", default="output/oos-2025/backfill_manifest.json")
    parser.add_argument("--output-dir", default="output/oos-2025/analysis")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    ranking, panel, source_manifest = load_inputs(
        args.ranking, args.prices, args.backfill_manifest
    )
    enriched = attach_methods(ranking)
    panel_by_code = price_lookup(panel)
    universe_outcomes = build_universe_outcomes(
        enriched, panel_by_code, DEFAULT_HORIZONS
    )
    events = select_method_events(
        enriched, universe_outcomes, max(DEFAULT_TOP_SIZES)
    )
    summary = summarize_events(events, DEFAULT_TOP_SIZES)
    paired = paired_method_comparison(events, DEFAULT_TOP_SIZES)
    quarter = grouped_summary(events, 100, 5, "quarter")
    sector = grouped_summary(events, 100, 5, "sector33")
    month = grouped_summary(events, 100, 5, "month")
    breadth = grouped_summary(events, 100, 5, "market_breadth_quintile")
    features = feature_bands(events, 100, 5)
    overlap, turnover = overlap_and_turnover(events, DEFAULT_TOP_SIZES)
    concentration_table = concentration(events, DEFAULT_TOP_SIZES)
    stocks = stock_summary(events, 100, 20)
    extremes = winners_losers(events, 30, 20)
    date_summary = (
        events.groupby(["method", "horizon_sessions", "signal_date"], dropna=False)
        .agg(
            selected_count=("code", "nunique"),
            mean_net_return=("net_return", "mean"),
            median_net_return=("net_return", "median"),
            win_rate_net=("net_return", lambda value: value.gt(0).mean()),
            mean_market_excess_net=("market_excess_net", "mean"),
            mean_sector_excess_net=("sector_excess_net", "mean"),
            mean_mfe=("mfe", "mean"),
            mean_mae=("mae", "mean"),
        )
        .reset_index()
    )

    tables = {
        "method_summary.csv": summary,
        "paired_method_comparison.csv": paired,
        "selection_events.csv": events,
        "universe_outcomes.csv": universe_outcomes,
        "date_summary.csv": date_summary,
        "quarter_summary_top100_5d.csv": quarter,
        "month_summary_top100_5d.csv": month,
        "sector_summary_top100_5d.csv": sector,
        "market_breadth_summary_top100_5d.csv": breadth,
        "feature_band_summary_top100_5d.csv": features,
        "method_overlap.csv": overlap,
        "ranking_turnover.csv": turnover,
        "sector_concentration.csv": concentration_table,
        "stock_summary_top100_20d.csv": stocks,
        "winners_losers_top30_20d.csv": extremes,
        "enriched_historical_ranking.csv": enriched,
    }
    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_backfill_manifest": args.backfill_manifest,
        "source_backfill_manifest_sha256": sha256_file(args.backfill_manifest),
        "source_ranking_sha256": sha256_file(args.ranking),
        "source_price_panel_sha256": sha256_file(args.prices),
        "evaluation_start": source_manifest.get("evaluation_start"),
        "evaluation_end": source_manifest.get("evaluation_end"),
        "ranking_date_count": enriched["date"].nunique(),
        "ranking_row_count": len(enriched),
        "price_panel_row_count": len(panel),
        "universe_outcome_count": len(universe_outcomes),
        "selection_event_count": len(events),
        "methods": list(METHODS),
        "top_sizes": list(DEFAULT_TOP_SIZES),
        "horizons": list(DEFAULT_HORIZONS),
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "exit_model": "HORIZONTH_SESSION_ADJUSTED_CLOSE_INCLUSIVE_OF_ENTRY_SESSION",
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "market_benchmark": "CROSS_SECTIONAL_MEDIAN_GROSS_RETURN_BY_SIGNAL_DATE_AND_HORIZON",
        "sector_benchmark": "SECTOR_MEDIAN_GROSS_RETURN_BY_SIGNAL_DATE_AND_HORIZON",
        "universe_bias": source_manifest.get("universe_bias"),
        "design_period_relationship": source_manifest.get("design_period_relationship"),
        "sample_types": ["ALL_EVENTS", "FIRST_PICK_PER_CODE", "NON_OVERLAPPING_PER_CODE"],
        "automatic_threshold_change": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "promotion_evidence_allowed": False,
        "research_only": True,
        "files": {},
        "limitations": [
            "Current-list-only universe creates survivorship, delisting, and historical membership bias.",
            "Historical prices are sourced from Yahoo Finance through yfinance and may be revised or missing.",
            "The strategy definitions are current 2026 definitions retrospectively applied to 2025.",
            "Repeated weekly signals overlap; event, first-pick, and non-overlapping samples are separated.",
            "Historical results cannot activate thresholds, rankings, paper execution, or live execution.",
        ],
    }
    for filename, table in tables.items():
        csv_payload = table.to_csv(index=False, lineterminator="\n").encode("utf-8")
        manifest["files"][filename] = {
            "rows": len(table),
            "sha256": hashlib.sha256(csv_payload).hexdigest(),
        }
    report = render_markdown_report({**source_manifest, **manifest}, summary, paired)
    write_outputs(Path(args.output_dir), tables, manifest, report)

    if args.strict:
        if enriched.empty or enriched["date"].nunique() < 20:
            raise RuntimeError("insufficient ranking dates")
        if universe_outcomes.empty or events.empty:
            raise RuntimeError("historical outcomes are empty")
        if set(events["method"].unique()) != set(METHODS):
            raise RuntimeError("not all methods produced selection events")
        expected = len(METHODS) * len(DEFAULT_TOP_SIZES) * len(DEFAULT_HORIZONS) * 3
        if len(summary) != expected:
            raise RuntimeError(f"method summary rows {len(summary)} != expected {expected}")
        if events["entry_date"].le(events["signal_date"]).any():
            raise RuntimeError("same-day or pre-signal entry detected")
        if events["exit_date"].lt(events["entry_date"]).any():
            raise RuntimeError("exit precedes entry")
        if source_manifest.get("promotion_evidence_allowed") is not False:
            raise RuntimeError("source evidence is unexpectedly promotable")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
