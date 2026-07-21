"""Robust, benchmark-paired evidence tables for Detailed OOS Evidence v2.

This module intentionally separates ranking quality from outlier-sensitive arithmetic means.
Healthy v1 is paired against Production. Balanced v2 is paired against Healthy v1, matching
the pre-registered H4 requirement. All results remain historical and non-promotable.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import detailed_oos_analysis as core

VERSION = "2026-07-22-detailed-robust-statistics-v2"
BASE_COST_BPS = 20.0
COST_LEVELS_BPS = (20, 50, 100)
BENCHMARKS = {"healthy_v1": "production", "balanced_v2": "healthy_v1"}


def trimmed_mean(values: pd.Series, fraction: float = 0.05) -> float:
    array = pd.to_numeric(values, errors="coerce").dropna().sort_values().to_numpy(float)
    if len(array) == 0:
        return np.nan
    trim = int(np.floor(len(array) * fraction))
    if trim == 0 or trim * 2 >= len(array):
        return float(array.mean())
    return float(array[trim:-trim].mean())


def winsorized_mean(values: pd.Series, fraction: float = 0.01) -> float:
    array = pd.to_numeric(values, errors="coerce").dropna().to_numpy(float)
    if len(array) == 0:
        return np.nan
    low, high = np.quantile(array, [fraction, 1.0 - fraction])
    return float(np.clip(array, low, high).mean())


def daily_return(frame: pd.DataFrame, return_column: str = "net_return") -> pd.Series:
    return frame.groupby("signal_date", sort=True)[return_column].mean()


def paired_bootstrap(
    left: pd.Series,
    right: pd.Series,
    iterations: int = 2000,
    seed: int = 20220722,
) -> tuple[float, float, float, float, int]:
    pair = pd.concat([left.rename("left"), right.rename("right")], axis=1).dropna()
    if pair.empty:
        return np.nan, np.nan, np.nan, np.nan, 0
    delta = pair["left"] - pair["right"]
    if len(delta) == 1:
        value = float(delta.iloc[0])
        return value, value, value, float(value > 0), 1
    rng = np.random.default_rng(seed)
    array = delta.to_numpy(float)
    sampled = rng.choice(array, size=(iterations, len(array)), replace=True).mean(axis=1)
    low, high = np.quantile(sampled, [0.025, 0.975])
    return (
        float(array.mean()),
        float(low),
        float(high),
        float((array > 0).mean()),
        len(array),
    )


def robust_method_summary(events: pd.DataFrame, top_sizes: tuple[int, ...]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = events[pd.to_numeric(events["method_rank"], errors="coerce").le(top_size)]
        for (year, method, horizon), group in top.groupby(
            ["year", "method", "horizon_sessions"], sort=True
        ):
            record: dict[str, Any] = {
                "year": int(year),
                "method": method,
                "top_size": int(top_size),
                "horizon_sessions": int(horizon),
                "observations": len(group),
                "stocks": group["code"].nunique(),
                "dates": group["signal_date"].nunique(),
                "mean_net_return_20bps": group["net_return"].mean(),
                "date_weighted_mean_net_return_20bps": daily_return(group).mean(),
                "median_net_return_20bps": group["net_return"].median(),
                "trimmed_mean_net_return_5pct_20bps": trimmed_mean(group["net_return"], 0.05),
                "winsorized_mean_net_return_1pct_20bps": winsorized_mean(group["net_return"], 0.01),
                "win_rate_20bps": group["net_return"].gt(0).mean(),
                "loss_below_minus_10pct_rate": group["net_return"].lt(-0.10).mean(),
                "gain_above_10pct_rate": group["net_return"].gt(0.10).mean(),
                "p05_net_return": group["net_return"].quantile(0.05),
                "p95_net_return": group["net_return"].quantile(0.95),
                "mean_market_excess_net_20bps": group["market_excess_net"].mean(),
                "mean_mae": group["mae"].mean(),
                "mean_mfe": group["mfe"].mean(),
            }
            for cost in COST_LEVELS_BPS:
                adjusted = group["net_return"] - (float(cost) - BASE_COST_BPS) / 10_000.0
                record[f"date_weighted_mean_net_return_{cost}bps"] = (
                    adjusted.groupby(group["signal_date"]).mean().mean()
                )
                record[f"median_net_return_{cost}bps"] = adjusted.median()
                record[f"win_rate_{cost}bps"] = adjusted.gt(0).mean()
            records.append(record)
    return pd.DataFrame(records)


def paired_benchmark_by_year(
    events: pd.DataFrame,
    top_sizes: tuple[int, ...],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for method, benchmark in BENCHMARKS.items():
        for top_size in top_sizes:
            subset = events[pd.to_numeric(events["method_rank"], errors="coerce").le(top_size)]
            for (year, horizon), year_group in subset.groupby(
                ["year", "horizon_sessions"], sort=True
            ):
                left_group = year_group[year_group["method"] == method]
                right_group = year_group[year_group["method"] == benchmark]
                left_daily = daily_return(left_group)
                right_daily = daily_return(right_group)
                mean_delta, ci_low, ci_high, outperformance_rate, paired_dates = paired_bootstrap(
                    left_daily, right_daily, seed=20220722 + int(year) + int(horizon) + int(top_size)
                )
                record: dict[str, Any] = {
                    "year": int(year),
                    "method": method,
                    "benchmark_method": benchmark,
                    "top_size": int(top_size),
                    "horizon_sessions": int(horizon),
                    "paired_dates": paired_dates,
                    "method_date_weighted_mean_net_return_20bps": left_daily.mean(),
                    "benchmark_date_weighted_mean_net_return_20bps": right_daily.mean(),
                    "mean_daily_delta_vs_benchmark_20bps": mean_delta,
                    "delta_ci_low_20bps": ci_low,
                    "delta_ci_high_20bps": ci_high,
                    "daily_outperformance_rate": outperformance_rate,
                    "method_trimmed_mean_5pct_20bps": trimmed_mean(left_group["net_return"], 0.05),
                    "benchmark_trimmed_mean_5pct_20bps": trimmed_mean(
                        right_group["net_return"], 0.05
                    ),
                }
                record["trimmed_mean_delta_vs_benchmark_20bps"] = (
                    record["method_trimmed_mean_5pct_20bps"]
                    - record["benchmark_trimmed_mean_5pct_20bps"]
                )
                for cost in COST_LEVELS_BPS:
                    extra = (float(cost) - BASE_COST_BPS) / 10_000.0
                    record[f"method_absolute_return_{cost}bps"] = left_daily.mean() - extra
                    record[f"benchmark_absolute_return_{cost}bps"] = right_daily.mean() - extra
                    record[f"delta_vs_benchmark_{cost}bps"] = mean_delta
                records.append(record)
    return pd.DataFrame(records)


def leave_one_sector_benchmark(
    events: pd.DataFrame,
    top_sizes: tuple[int, ...],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    sectors = sorted(
        value for value in events["sector33"].fillna("").astype(str).unique() if value
    )
    for method, benchmark in BENCHMARKS.items():
        for top_size in top_sizes:
            top = events[pd.to_numeric(events["method_rank"], errors="coerce").le(top_size)]
            for (year, horizon), group in top.groupby(["year", "horizon_sessions"], sort=True):
                for sector in sectors:
                    reduced = group[group["sector33"].fillna("").astype(str) != sector]
                    left = reduced[reduced["method"] == method]
                    right = reduced[reduced["method"] == benchmark]
                    if left.empty or right.empty:
                        continue
                    left_daily = daily_return(left)
                    right_daily = daily_return(right)
                    pair = pd.concat(
                        [left_daily.rename("left"), right_daily.rename("right")], axis=1
                    ).dropna()
                    if pair.empty:
                        continue
                    records.append(
                        {
                            "year": int(year),
                            "method": method,
                            "benchmark_method": benchmark,
                            "top_size": int(top_size),
                            "horizon_sessions": int(horizon),
                            "excluded_sector": sector,
                            "paired_dates": len(pair),
                            "method_return": pair["left"].mean(),
                            "benchmark_return": pair["right"].mean(),
                            "delta_vs_benchmark": (pair["left"] - pair["right"]).mean(),
                        }
                    )
    return pd.DataFrame(records)


def build_scorecard(
    paired: pd.DataFrame,
    robust: pd.DataFrame,
    rank_ic: pd.DataFrame,
    leave_sector: pd.DataFrame,
    placebo: pd.DataFrame,
    protocol: core.Protocol,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for method, benchmark in BENCHMARKS.items():
        for top_size in protocol.primary_top_sizes:
            for horizon in protocol.primary_horizons:
                cells = paired[
                    (paired["method"] == method)
                    & (paired["top_size"] == top_size)
                    & (paired["horizon_sessions"] == horizon)
                ]
                method_robust = robust[
                    (robust["method"] == method)
                    & (robust["top_size"] == top_size)
                    & (robust["horizon_sessions"] == horizon)
                ]
                ic = rank_ic[
                    (rank_ic["method"] == method)
                    & (rank_ic["horizon_sessions"] == horizon)
                ]
                loso = leave_sector[
                    (leave_sector["method"] == method)
                    & (leave_sector["top_size"] == top_size)
                    & (leave_sector["horizon_sessions"] == horizon)
                ]
                placebo_cells = placebo[
                    (placebo["method"] == method)
                    & (placebo["top_size"] == top_size)
                    & (placebo["horizon_sessions"] == horizon)
                ]
                metrics = {
                    "years_available": cells["year"].nunique(),
                    "years_outperforming_benchmark": int(
                        cells["mean_daily_delta_vs_benchmark_20bps"].gt(0).sum()
                    ),
                    "years_trimmed_outperforming_benchmark": int(
                        cells["trimmed_mean_delta_vs_benchmark_20bps"].gt(0).sum()
                    ),
                    "years_positive_absolute_return_50bps": int(
                        method_robust["date_weighted_mean_net_return_50bps"].gt(0).sum()
                    ),
                    "years_positive_market_excess": int(
                        method_robust["mean_market_excess_net_20bps"].gt(0).sum()
                    ),
                    "mean_delta_vs_benchmark_20bps": cells[
                        "mean_daily_delta_vs_benchmark_20bps"
                    ].mean(),
                    "mean_daily_outperformance_rate": cells[
                        "daily_outperformance_rate"
                    ].mean(),
                    "mean_positive_rank_ic_rate": ic["positive_rank_ic_rate"].mean(),
                    "leave_one_sector_positive_delta_rate": loso[
                        "delta_vs_benchmark"
                    ].gt(0).mean()
                    if len(loso)
                    else np.nan,
                    "placebo_pass_year_rate": placebo_cells[
                        "one_sided_empirical_p"
                    ].le(0.10).mean()
                    if len(placebo_cells)
                    else np.nan,
                    "years_ci_entirely_positive": int(cells["delta_ci_low_20bps"].gt(0).sum()),
                }
                passes = {
                    "year_consistency": metrics["years_outperforming_benchmark"]
                    >= protocol.minimum_years_positive,
                    "trimmed_year_consistency": metrics[
                        "years_trimmed_outperforming_benchmark"
                    ]
                    >= protocol.minimum_years_positive,
                    "cost_50_absolute": metrics["years_positive_absolute_return_50bps"]
                    >= protocol.minimum_years_positive,
                    "market_excess": metrics["years_positive_market_excess"]
                    >= protocol.minimum_years_positive,
                    "rank_ic_consistency": bool(
                        pd.notna(metrics["mean_positive_rank_ic_rate"])
                        and metrics["mean_positive_rank_ic_rate"]
                        >= protocol.minimum_rank_ic_positive_rate
                    ),
                    "leave_one_sector": bool(
                        pd.notna(metrics["leave_one_sector_positive_delta_rate"])
                        and metrics["leave_one_sector_positive_delta_rate"]
                        >= protocol.minimum_leave_one_sector_positive_rate
                    ),
                    "placebo": bool(
                        pd.notna(metrics["placebo_pass_year_rate"])
                        and metrics["placebo_pass_year_rate"] >= 0.50
                    ),
                }
                records.append(
                    {
                        "method": method,
                        "benchmark_method": benchmark,
                        "top_size": int(top_size),
                        "horizon_sessions": int(horizon),
                        **metrics,
                        **{f"pass_{key}": value for key, value in passes.items()},
                        "all_research_gates_pass": all(passes.values()),
                        "promotion_status": "RESEARCH_SUPPORT_ONLY_NON_PROMOTABLE",
                    }
                )
    return pd.DataFrame(records)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-events", required=True)
    parser.add_argument("--rank-ic-summary", required=True)
    parser.add_argument("--random-placebo", required=True)
    parser.add_argument("--protocol", default="research/detailed_oos_protocol.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol, _ = core.load_protocol(args.protocol)
    events = pd.read_csv(args.selection_events, dtype={"code": str}, low_memory=False)
    events["code"] = events["code"].astype(str).str.split(".").str[0].str.zfill(4)
    events["signal_date"] = pd.to_datetime(events["signal_date"], errors="coerce").dt.normalize()
    events["year"] = events["signal_date"].dt.year.astype(int)
    rank_ic = pd.read_csv(args.rank_ic_summary, low_memory=False)
    placebo = pd.read_csv(args.random_placebo, low_memory=False)

    robust = robust_method_summary(events, protocol.top_sizes)
    paired = paired_benchmark_by_year(events, protocol.top_sizes)
    leave_sector = leave_one_sector_benchmark(events, protocol.top_sizes)
    scorecard = build_scorecard(paired, robust, rank_ic, leave_sector, placebo, protocol)

    robust.to_csv(output_dir / "robust_method_summary.csv", index=False)
    paired.to_csv(output_dir / "paired_benchmark_by_year.csv", index=False)
    leave_sector.to_csv(output_dir / "leave_one_sector_benchmark.csv", index=False)
    scorecard.to_csv(output_dir / "evidence_scorecard_v2.csv", index=False)
    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cost_levels_bps": list(COST_LEVELS_BPS),
        "benchmarks": BENCHMARKS,
        "robust_summary_rows": len(robust),
        "paired_benchmark_rows": len(paired),
        "leave_one_sector_rows": len(leave_sector),
        "scorecard_rows": len(scorecard),
        "research_only": True,
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "selection_events_sha256": core.sha256_file(args.selection_events),
        "rank_ic_summary_sha256": core.sha256_file(args.rank_ic_summary),
        "random_placebo_sha256": core.sha256_file(args.random_placebo),
        "protocol_sha256": core.sha256_file(args.protocol),
    }
    (output_dir / "robust_statistics_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        if len(scorecard) != 12:
            raise RuntimeError(f"scorecard rows {len(scorecard)} != 12")
        if set(scorecard["benchmark_method"]) != {"production", "healthy_v1"}:
            raise RuntimeError("benchmark pairing is incomplete")
        if set(scorecard["promotion_status"]) != {
            "RESEARCH_SUPPORT_ONLY_NON_PROMOTABLE"
        }:
            raise RuntimeError("invalid promotion status")
        if manifest["production_state_mutations"]:
            raise RuntimeError("production state mutated")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
