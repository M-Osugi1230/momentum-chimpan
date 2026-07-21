"""Independent 2018-2021 holdout for Healthy Rank v3.

The candidate definition is imported from healthy_rank_v3.py and is not tuned here. This
script compares v3 primarily against Healthy v1 inside the unchanged Healthy v1 eligible
set. Historical evidence remains research-only and non-promotable.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

import detailed_oos_analysis as core
from detailed_robust_statistics import paired_bootstrap, trimmed_mean, winsorized_mean
import healthy_rank_v3

VERSION = "2026-07-22-healthy-rank-v3-holdout-backtest-v1"
BASE_COST_BPS = 20.0
COST_LEVELS_BPS = (20, 50, 100)
METHODS = {
    "production": ("rank", "score"),
    "healthy_v1": ("healthy_rank", "healthy_selection_score"),
    "balanced_v2": ("healthy_v2_rank", "healthy_v2_selection_score"),
    "healthy_v3": ("healthy_v3_rank", "healthy_v3_selection_score"),
}
PAIRINGS = {
    "healthy_v3_vs_healthy_v1": ("healthy_v3", "healthy_v1"),
    "healthy_v3_vs_production": ("healthy_v3", "production"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enriched-ranking", required=True)
    parser.add_argument("--universe-outcomes", required=True)
    parser.add_argument("--backfill-manifest", required=True)
    parser.add_argument("--protocol", default="research/healthy_rank_v3_protocol.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def load_protocol(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "RESEARCH_ONLY_NON_PROMOTABLE":
        raise ValueError("v3 holdout must remain RESEARCH_ONLY_NON_PROMOTABLE")
    if raw.get("promotion_evidence_allowed") is not False:
        raise ValueError("historical holdout cannot be promotion evidence")
    if raw.get("automatic_strategy_change") is not False:
        raise ValueError("automatic strategy change must be false")
    candidate = raw.get("candidate") or {}
    components = candidate.get("components") or {}
    expected = {
        "return_5d_cross_section_percentile": 0.25,
        "return_20d_cross_section_percentile": 0.25,
        "healthy_relative_strength_cross_section_percentile": 0.25,
        "ma20_deviation_middle_preference": 0.25,
    }
    if components != expected:
        raise ValueError("v3 component weights differ from preregistration")
    return raw


def load_inputs(
    ranking_path: str,
    outcomes_path: str,
    manifest_path: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    ranking = pd.read_csv(ranking_path, dtype={"code": str}, low_memory=False)
    ranking["code"] = ranking["code"].astype(str).str.split(".").str[0].str.zfill(4)
    ranking["date"] = pd.to_datetime(ranking["date"], errors="coerce").dt.normalize()
    outcomes = pd.read_csv(outcomes_path, dtype={"code": str}, low_memory=False)
    outcomes["code"] = outcomes["code"].astype(str).str.split(".").str[0].str.zfill(4)
    outcomes["signal_date"] = pd.to_datetime(
        outcomes["signal_date"], errors="coerce"
    ).dt.normalize()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("promotion_evidence_allowed") is not False:
        raise ValueError("source backfill must be non-promotable")
    if manifest.get("production_state_mutations") not in ([], None):
        raise ValueError("source backfill mutated production state")
    return ranking, outcomes, manifest


def attach_and_merge(
    ranking: pd.DataFrame,
    outcomes: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ranked = healthy_rank_v3.attach(ranking)
    v3_columns = [
        "date",
        "code",
        "healthy_v3_eligible",
        "healthy_v3_rank",
        "healthy_v3_selection_score",
        "healthy_v3_return_5d_percentile",
        "healthy_v3_return_20d_percentile",
        "healthy_v3_relative_strength_percentile",
        "healthy_v3_ma20_middle_preference",
        "healthy_v3_exclusion_reasons",
        "healthy_v3_policy_id",
        "healthy_v3_version",
    ]
    merged = outcomes.merge(
        ranked[v3_columns].rename(columns={"date": "signal_date"}),
        on=["signal_date", "code"],
        how="left",
        validate="many_to_one",
    )
    return ranked, merged


def method_events(outcomes: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    base = [
        "signal_date",
        "code",
        "name",
        "sector33",
        "horizon_sessions",
        "entry_date",
        "exit_date",
        "net_return",
        "market_excess_net",
        "sector_excess_net",
        "mfe",
        "mae",
    ]
    for method, (rank_column, score_column) in METHODS.items():
        if rank_column not in outcomes or score_column not in outcomes:
            continue
        columns = [column for column in base + [rank_column, score_column] if column in outcomes]
        frame = outcomes[columns].copy()
        frame[rank_column] = pd.to_numeric(frame[rank_column], errors="coerce")
        frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")
        frame = frame[frame[rank_column].notna()].copy()
        frame = frame.rename(
            columns={rank_column: "method_rank", score_column: "method_score"}
        )
        frame["method"] = method
        frame["year"] = frame["signal_date"].dt.year.astype(int)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    events = pd.concat(frames, ignore_index=True, sort=False)
    return events.sort_values(
        ["signal_date", "method", "method_rank", "code", "horizon_sessions"]
    ).reset_index(drop=True)


def robust_summary(
    events: pd.DataFrame,
    top_sizes: tuple[int, ...],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = events[events["method_rank"].le(top_size)]
        for (year, method, horizon), group in top.groupby(
            ["year", "method", "horizon_sessions"], sort=True
        ):
            daily = group.groupby("signal_date")["net_return"].mean()
            record: dict[str, Any] = {
                "year": int(year),
                "method": method,
                "top_size": int(top_size),
                "horizon_sessions": int(horizon),
                "observations": len(group),
                "stocks": group["code"].nunique(),
                "dates": group["signal_date"].nunique(),
                "date_weighted_mean_net_return_20bps": daily.mean(),
                "mean_net_return_20bps": group["net_return"].mean(),
                "median_net_return_20bps": group["net_return"].median(),
                "trimmed_mean_net_return_5pct_20bps": trimmed_mean(
                    group["net_return"], 0.05
                ),
                "winsorized_mean_net_return_1pct_20bps": winsorized_mean(
                    group["net_return"], 0.01
                ),
                "win_rate_20bps": group["net_return"].gt(0).mean(),
                "mean_market_excess_net_20bps": group["market_excess_net"].mean(),
                "mean_sector_excess_net_20bps": group["sector_excess_net"].mean(),
                "mean_mfe": group["mfe"].mean(),
                "mean_mae": group["mae"].mean(),
                "loss_below_minus_10pct_rate": group["net_return"].lt(-0.10).mean(),
                "gain_above_10pct_rate": group["net_return"].gt(0.10).mean(),
            }
            for cost in COST_LEVELS_BPS:
                extra = (float(cost) - BASE_COST_BPS) / 10_000.0
                adjusted = group["net_return"] - extra
                record[f"date_weighted_mean_net_return_{cost}bps"] = (
                    adjusted.groupby(group["signal_date"]).mean().mean()
                )
                record[f"median_net_return_{cost}bps"] = adjusted.median()
                record[f"win_rate_{cost}bps"] = adjusted.gt(0).mean()
            records.append(record)
    return pd.DataFrame(records)


def paired_comparisons(
    events: pd.DataFrame,
    top_sizes: tuple[int, ...],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for comparison_id, (method, benchmark) in PAIRINGS.items():
        for top_size in top_sizes:
            top = events[events["method_rank"].le(top_size)]
            for (year, horizon), group in top.groupby(
                ["year", "horizon_sessions"], sort=True
            ):
                left = group[group["method"] == method]
                right = group[group["method"] == benchmark]
                left_daily = left.groupby("signal_date")["net_return"].mean()
                right_daily = right.groupby("signal_date")["net_return"].mean()
                delta, ci_low, ci_high, outperformance_rate, paired_dates = paired_bootstrap(
                    left_daily,
                    right_daily,
                    iterations=3000,
                    seed=20180722 + int(year) + int(horizon) + int(top_size),
                )
                records.append(
                    {
                        "comparison_id": comparison_id,
                        "year": int(year),
                        "method": method,
                        "benchmark_method": benchmark,
                        "top_size": int(top_size),
                        "horizon_sessions": int(horizon),
                        "paired_dates": paired_dates,
                        "method_date_weighted_mean_net_return_20bps": left_daily.mean(),
                        "benchmark_date_weighted_mean_net_return_20bps": right_daily.mean(),
                        "mean_daily_delta_20bps": delta,
                        "delta_ci_low_20bps": ci_low,
                        "delta_ci_high_20bps": ci_high,
                        "daily_outperformance_rate": outperformance_rate,
                        "method_trimmed_mean_5pct_20bps": trimmed_mean(
                            left["net_return"], 0.05
                        ),
                        "benchmark_trimmed_mean_5pct_20bps": trimmed_mean(
                            right["net_return"], 0.05
                        ),
                    }
                )
    result = pd.DataFrame(records)
    if not result.empty:
        result["trimmed_mean_delta_20bps"] = (
            result["method_trimmed_mean_5pct_20bps"]
            - result["benchmark_trimmed_mean_5pct_20bps"]
        )
    return result


def rank_ic(
    outcomes: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    records: list[dict[str, Any]] = []
    for method, (rank_column, score_column) in METHODS.items():
        if rank_column not in outcomes or score_column not in outcomes:
            continue
        subset = outcomes[
            ["signal_date", "horizon_sessions", rank_column, score_column, "net_return"]
        ].copy()
        subset[rank_column] = pd.to_numeric(subset[rank_column], errors="coerce")
        subset[score_column] = pd.to_numeric(subset[score_column], errors="coerce")
        subset = subset[subset[rank_column].notna()]
        for (date, horizon), group in subset.groupby(
            ["signal_date", "horizon_sessions"], sort=True
        ):
            records.append(
                {
                    "signal_date": date,
                    "year": int(pd.Timestamp(date).year),
                    "method": method,
                    "horizon_sessions": int(horizon),
                    "observations": len(group),
                    "rank_ic": core.spearman_pair(-group[rank_column], group["net_return"]),
                    "score_ic": core.spearman_pair(group[score_column], group["net_return"]),
                }
            )
    daily = pd.DataFrame(records)
    summary_records: list[dict[str, Any]] = []
    for (year, method, horizon), group in daily.groupby(
        ["year", "method", "horizon_sessions"], sort=True
    ):
        values = group["rank_ic"].dropna()
        summary_records.append(
            {
                "year": int(year),
                "method": method,
                "horizon_sessions": int(horizon),
                "dates": group["signal_date"].nunique(),
                "mean_rank_ic": values.mean(),
                "median_rank_ic": values.median(),
                "positive_rank_ic_rate": values.gt(0).mean(),
            }
        )
    return daily, pd.DataFrame(summary_records)


def rank_monotonicity(
    outcomes: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    bins = [0, 10, 30, 50, 100, np.inf]
    labels = ["1-10", "11-30", "31-50", "51-100", "101+"]
    for method, (rank_column, _) in METHODS.items():
        if rank_column not in outcomes:
            continue
        subset = outcomes[
            ["signal_date", "code", "horizon_sessions", rank_column, "net_return", "market_excess_net", "mae"]
        ].copy()
        subset[rank_column] = pd.to_numeric(subset[rank_column], errors="coerce")
        subset = subset[subset[rank_column].notna()]
        subset["year"] = subset["signal_date"].dt.year.astype(int)
        subset["rank_band"] = pd.cut(
            subset[rank_column], bins=bins, labels=labels, right=True
        )
        for (year, horizon, band), group in subset.groupby(
            ["year", "horizon_sessions", "rank_band"], observed=True, sort=True
        ):
            records.append(
                {
                    "year": int(year),
                    "method": method,
                    "horizon_sessions": int(horizon),
                    "rank_band": str(band),
                    "observations": len(group),
                    "date_weighted_mean_net_return": group.groupby("signal_date")[
                        "net_return"
                    ].mean().mean(),
                    "median_net_return": group["net_return"].median(),
                    "win_rate": group["net_return"].gt(0).mean(),
                    "mean_market_excess_net": group["market_excess_net"].mean(),
                    "mean_mae": group["mae"].mean(),
                }
            )
    return pd.DataFrame(records)


def leave_one_sector(
    events: pd.DataFrame,
    top_sizes: tuple[int, ...],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    sectors = sorted(
        value for value in events["sector33"].fillna("").astype(str).unique() if value
    )
    for comparison_id, (method, benchmark) in PAIRINGS.items():
        for top_size in top_sizes:
            top = events[events["method_rank"].le(top_size)]
            for (year, horizon), group in top.groupby(
                ["year", "horizon_sessions"], sort=True
            ):
                for sector in sectors:
                    reduced = group[group["sector33"].fillna("").astype(str) != sector]
                    left = reduced[reduced["method"] == method].groupby("signal_date")[
                        "net_return"
                    ].mean()
                    right = reduced[reduced["method"] == benchmark].groupby("signal_date")[
                        "net_return"
                    ].mean()
                    pair = pd.concat([left.rename("left"), right.rename("right")], axis=1).dropna()
                    if pair.empty:
                        continue
                    records.append(
                        {
                            "comparison_id": comparison_id,
                            "year": int(year),
                            "method": method,
                            "benchmark_method": benchmark,
                            "top_size": int(top_size),
                            "horizon_sessions": int(horizon),
                            "excluded_sector": sector,
                            "paired_dates": len(pair),
                            "delta_vs_benchmark": (pair["left"] - pair["right"]).mean(),
                        }
                    )
    return pd.DataFrame(records)


def random_placebo(
    outcomes: pd.DataFrame,
    events: pd.DataFrame,
    top_sizes: tuple[int, ...],
    repetitions: int,
) -> pd.DataFrame:
    rng = np.random.default_rng(20180722)
    healthy_universe = outcomes[pd.to_numeric(outcomes["healthy_rank"], errors="coerce").notna()][
        ["signal_date", "code", "horizon_sessions", "net_return"]
    ].copy()
    actual = events[events["method"] == "healthy_v3"].copy()
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        actual_top = actual[actual["method_rank"].le(top_size)]
        for (year, horizon), group in healthy_universe.groupby(
            [healthy_universe["signal_date"].dt.year, "horizon_sessions"], sort=True
        ):
            by_date = {
                date: date_group["net_return"].dropna().to_numpy(float)
                for date, date_group in group.groupby("signal_date", sort=True)
            }
            actual_daily = actual_top[
                (actual_top["year"] == int(year))
                & (actual_top["horizon_sessions"] == int(horizon))
            ].groupby("signal_date")["net_return"].mean()
            if actual_daily.empty:
                continue
            distribution: list[float] = []
            for _ in range(int(repetitions)):
                daily: list[float] = []
                for date in actual_daily.index:
                    array = by_date.get(date)
                    if array is None or len(array) == 0:
                        continue
                    n = min(int(top_size), len(array))
                    chosen = rng.choice(array, size=n, replace=False)
                    daily.append(float(np.mean(chosen)))
                distribution.append(float(np.mean(daily)) if daily else np.nan)
            values = np.asarray(distribution, dtype=float)
            values = values[np.isfinite(values)]
            if len(values) == 0:
                continue
            actual_value = float(actual_daily.mean())
            records.append(
                {
                    "year": int(year),
                    "method": "healthy_v3",
                    "placebo_universe": "healthy_v1_eligible",
                    "top_size": int(top_size),
                    "horizon_sessions": int(horizon),
                    "actual_return": actual_value,
                    "random_mean": float(values.mean()),
                    "random_p05": float(np.quantile(values, 0.05)),
                    "random_p50": float(np.quantile(values, 0.50)),
                    "random_p95": float(np.quantile(values, 0.95)),
                    "actual_minus_random_mean": actual_value - float(values.mean()),
                    "one_sided_empirical_p": float(
                        (1 + np.sum(values >= actual_value)) / (len(values) + 1)
                    ),
                    "repetitions": len(values),
                }
            )
    return pd.DataFrame(records)


def evidence_scorecard(
    paired: pd.DataFrame,
    summary: pd.DataFrame,
    rank_ic_summary: pd.DataFrame,
    leave_sector_frame: pd.DataFrame,
    placebo: pd.DataFrame,
    protocol: dict[str, Any],
) -> pd.DataFrame:
    evaluation = protocol.get("evaluation") or {}
    gates = protocol.get("evidence_gates") or {}
    primary_horizons = tuple(int(v) for v in evaluation.get("primary_horizons", [5, 10, 20]))
    primary_top_sizes = tuple(int(v) for v in evaluation.get("primary_top_sizes", [10, 30]))
    records: list[dict[str, Any]] = []
    primary_pairs = paired[paired["comparison_id"] == "healthy_v3_vs_healthy_v1"]
    for top_size in primary_top_sizes:
        for horizon in primary_horizons:
            cells = primary_pairs[
                (primary_pairs["top_size"] == top_size)
                & (primary_pairs["horizon_sessions"] == horizon)
            ]
            v3_summary = summary[
                (summary["method"] == "healthy_v3")
                & (summary["top_size"] == top_size)
                & (summary["horizon_sessions"] == horizon)
            ]
            ic = rank_ic_summary[
                (rank_ic_summary["method"] == "healthy_v3")
                & (rank_ic_summary["horizon_sessions"] == horizon)
            ]
            loso = leave_sector_frame[
                (leave_sector_frame["comparison_id"] == "healthy_v3_vs_healthy_v1")
                & (leave_sector_frame["top_size"] == top_size)
                & (leave_sector_frame["horizon_sessions"] == horizon)
            ]
            placebo_cells = placebo[
                (placebo["top_size"] == top_size)
                & (placebo["horizon_sessions"] == horizon)
            ]
            metrics = {
                "years_available": cells["year"].nunique(),
                "years_outperforming_healthy_v1": int(cells["mean_daily_delta_20bps"].gt(0).sum()),
                "years_trimmed_outperforming_healthy_v1": int(cells["trimmed_mean_delta_20bps"].gt(0).sum()),
                "mean_daily_delta_vs_healthy_v1": cells["mean_daily_delta_20bps"].mean(),
                "mean_daily_outperformance_rate": cells["daily_outperformance_rate"].mean(),
                "mean_positive_rank_ic_rate": ic["positive_rank_ic_rate"].mean(),
                "leave_one_sector_positive_delta_rate": loso["delta_vs_benchmark"].gt(0).mean() if len(loso) else np.nan,
                "years_positive_after_50bps": int(v3_summary["date_weighted_mean_net_return_50bps"].gt(0).sum()),
                "years_random_placebo_p_le_0_10": int(placebo_cells["one_sided_empirical_p"].le(0.10).sum()),
                "years_ci_entirely_positive": int(cells["delta_ci_low_20bps"].gt(0).sum()),
            }
            passes = {
                "year_consistency": metrics["years_outperforming_healthy_v1"] >= int(gates["minimum_years_outperforming_healthy_v1"]),
                "trimmed_year_consistency": metrics["years_trimmed_outperforming_healthy_v1"] >= int(gates["minimum_years_trimmed_outperforming_healthy_v1"]),
                "daily_outperformance": bool(pd.notna(metrics["mean_daily_outperformance_rate"]) and metrics["mean_daily_outperformance_rate"] >= float(gates["minimum_daily_outperformance_rate"])),
                "rank_ic": bool(pd.notna(metrics["mean_positive_rank_ic_rate"]) and metrics["mean_positive_rank_ic_rate"] >= float(gates["minimum_rank_ic_positive_rate"])),
                "leave_one_sector": bool(pd.notna(metrics["leave_one_sector_positive_delta_rate"]) and metrics["leave_one_sector_positive_delta_rate"] >= float(gates["minimum_leave_one_sector_positive_delta_rate"])),
                "cost_50": metrics["years_positive_after_50bps"] >= int(gates["minimum_years_positive_after_50bps"]),
                "random_placebo": metrics["years_random_placebo_p_le_0_10"] >= int(gates["minimum_years_random_placebo_p_le_0_10"]),
            }
            records.append(
                {
                    "method": "healthy_v3",
                    "benchmark_method": "healthy_v1",
                    "top_size": int(top_size),
                    "horizon_sessions": int(horizon),
                    **metrics,
                    **{f"pass_{key}": value for key, value in passes.items()},
                    "all_research_gates_pass": all(passes.values()),
                    "promotion_status": "RESEARCH_SUPPORT_ONLY_NON_PROMOTABLE",
                }
            )
    return pd.DataFrame(records)


def report_markdown(
    manifest: dict[str, Any],
    summary: pd.DataFrame,
    paired: pd.DataFrame,
    rank_ic_summary: pd.DataFrame,
    scorecard: pd.DataFrame,
) -> str:
    primary_summary = summary[
        summary["method"].isin(["production", "healthy_v1", "healthy_v3"])
        & summary["top_size"].isin([10, 30])
        & summary["horizon_sessions"].isin([5, 10, 20])
    ]
    lines = [
        "# Healthy Rank v3｜2018–2021 独立ホールドアウト",
        "",
        "> 研究専用・本番変更なし。Healthy v1 Eligibilityは変更せず、Eligible内の順位だけを検証しています。",
        "",
        "## 検証範囲",
        "",
        f"- 期間：{manifest['evaluation_start']}〜{manifest['evaluation_end']}",
        f"- ランキング日数：{manifest['ranking_date_count']}",
        f"- ランキング行数：{manifest['ranking_row_count']:,}",
        f"- v3 eligible行数：{manifest['v3_eligible_rows']:,}",
        "- 候補式と合格基準は結果確認前に固定済み",
        "",
        "## 年別主要成績",
        "",
        "| 年 | 手法 | Top | 期間 | 20bp後平均 | 5%トリム平均 | 50bp後平均 | 勝率 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in primary_summary.sort_values(
        ["year", "horizon_sessions", "top_size", "method"]
    ).itertuples(index=False):
        lines.append(
            f"| {row.year} | {row.method} | {row.top_size} | {row.horizon_sessions}日 | "
            f"{row.date_weighted_mean_net_return_20bps:.3%} | "
            f"{row.trimmed_mean_net_return_5pct_20bps:.3%} | "
            f"{row.date_weighted_mean_net_return_50bps:.3%} | {row.win_rate_20bps:.1%} |"
        )
    lines += [
        "",
        "## Healthy v1との年別ペア比較",
        "",
        "| 年 | Top | 期間 | v3差 | 95%CI下限 | 95%CI上限 | 日次超過率 | トリム差 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    focus_pairs = paired[
        (paired["comparison_id"] == "healthy_v3_vs_healthy_v1")
        & paired["top_size"].isin([10, 30])
        & paired["horizon_sessions"].isin([5, 10, 20])
    ]
    for row in focus_pairs.sort_values(
        ["year", "horizon_sessions", "top_size"]
    ).itertuples(index=False):
        lines.append(
            f"| {row.year} | {row.top_size} | {row.horizon_sessions}日 | "
            f"{row.mean_daily_delta_20bps:+.3%} | {row.delta_ci_low_20bps:+.3%} | "
            f"{row.delta_ci_high_20bps:+.3%} | {row.daily_outperformance_rate:.1%} | "
            f"{row.trimmed_mean_delta_20bps:+.3%} |"
        )
    lines += [
        "",
        "## Evidence Scorecard",
        "",
        "| Top | 期間 | 超過年数 | トリム超過年数 | 日次超過率 | Rank IC正の日率 | 業種除外後超過率 | 50bp後プラス年数 | Placebo通過年数 | 判定 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in scorecard.sort_values(["top_size", "horizon_sessions"]).itertuples(index=False):
        lines.append(
            f"| {row.top_size} | {row.horizon_sessions}日 | "
            f"{row.years_outperforming_healthy_v1}/{row.years_available} | "
            f"{row.years_trimmed_outperforming_healthy_v1}/{row.years_available} | "
            f"{row.mean_daily_outperformance_rate:.1%} | {row.mean_positive_rank_ic_rate:.1%} | "
            f"{row.leave_one_sector_positive_delta_rate:.1%} | "
            f"{row.years_positive_after_50bps}/{row.years_available} | "
            f"{row.years_random_placebo_p_le_0_10}/{row.years_available} | "
            f"{'PASS' if row.all_research_gates_pass else 'NOT PASS'} |"
        )
    lines += [
        "",
        "## Rank IC",
        "",
        "| 年 | 手法 | 期間 | 平均Rank IC | 正の日率 |",
        "|---:|---|---:|---:|---:|",
    ]
    focus_ic = rank_ic_summary[
        rank_ic_summary["method"].isin(["healthy_v1", "healthy_v3"])
        & rank_ic_summary["horizon_sessions"].isin([5, 10, 20])
    ]
    for row in focus_ic.sort_values(
        ["year", "horizon_sessions", "method"]
    ).itertuples(index=False):
        lines.append(
            f"| {row.year} | {row.method} | {row.horizon_sessions}日 | "
            f"{row.mean_rank_ic:.4f} | {row.positive_rank_ic_rate:.1%} |"
        )
    lines += [
        "",
        "## 制約",
        "",
        "- 現在の上場一覧を過去へ遡るため、上場廃止・構成銘柄バイアスがあります。",
        "- 1,500銘柄・5営業日間隔であり、全銘柄・全営業日ではありません。",
        "- 市場比較は対象断面中央値であり、TOPIXそのものではありません。",
        "- 合格してもライブ前向きシャドー検証なしに本番昇格しません。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol = load_protocol(args.protocol)
    ranking, outcomes, backfill_manifest = load_inputs(
        args.enriched_ranking, args.universe_outcomes, args.backfill_manifest
    )
    ranked, merged_outcomes = attach_and_merge(ranking, outcomes)
    events = method_events(merged_outcomes)
    evaluation = protocol.get("evaluation") or {}
    top_sizes = tuple(int(v) for v in evaluation.get("top_sizes", [10, 30, 100]))
    repetitions = int(evaluation.get("random_placebo_repetitions", 500))

    summary = robust_summary(events, top_sizes)
    paired = paired_comparisons(events, top_sizes)
    ic_daily, ic_summary = rank_ic(merged_outcomes)
    monotonicity = rank_monotonicity(merged_outcomes)
    loso = leave_one_sector(events, top_sizes)
    placebo = random_placebo(merged_outcomes, events, top_sizes, repetitions)
    scorecard = evidence_scorecard(
        paired, summary, ic_summary, loso, placebo, protocol
    )

    ranking_columns = [
        "date",
        "code",
        "name",
        "sector33",
        "rank",
        "healthy_rank",
        "healthy_v2_rank",
        "healthy_v3_eligible",
        "healthy_v3_rank",
        "healthy_v3_selection_score",
        "healthy_v3_return_5d_percentile",
        "healthy_v3_return_20d_percentile",
        "healthy_v3_relative_strength_percentile",
        "healthy_v3_ma20_middle_preference",
        "healthy_v3_exclusion_reasons",
        "healthy_v3_policy_id",
        "healthy_v3_version",
    ]
    ranked[[column for column in ranking_columns if column in ranked]].to_csv(
        output_dir / "healthy_rank_v3_rankings.csv", index=False
    )
    events.to_csv(output_dir / "healthy_rank_v3_events.csv", index=False)
    summary.to_csv(output_dir / "robust_summary_by_year.csv", index=False)
    paired.to_csv(output_dir / "paired_comparisons_by_year.csv", index=False)
    ic_daily.to_csv(output_dir / "rank_ic_daily.csv", index=False)
    ic_summary.to_csv(output_dir / "rank_ic_summary.csv", index=False)
    monotonicity.to_csv(output_dir / "rank_monotonicity.csv", index=False)
    loso.to_csv(output_dir / "leave_one_sector_out.csv", index=False)
    placebo.to_csv(output_dir / "random_placebo.csv", index=False)
    scorecard.to_csv(output_dir / "evidence_scorecard.csv", index=False)

    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evaluation_start": str(ranking["date"].min().date()),
        "evaluation_end": str(ranking["date"].max().date()),
        "ranking_date_count": int(ranking["date"].nunique()),
        "ranking_row_count": len(ranking),
        "outcome_row_count": len(merged_outcomes),
        "event_row_count": len(events),
        "v3_eligible_rows": int(ranked["healthy_v3_eligible"].fillna(False).sum()),
        "years": sorted(int(v) for v in ranking["date"].dt.year.unique()),
        "methods": sorted(events["method"].unique().tolist()),
        "horizons": sorted(int(v) for v in events["horizon_sessions"].unique()),
        "top_sizes": list(top_sizes),
        "random_placebo_repetitions": repetitions,
        "candidate_version": healthy_rank_v3.VERSION,
        "candidate_policy_id": healthy_rank_v3.POLICY_ID,
        "research_only": True,
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "healthy_v1_eligibility_mutations": [],
        "source_backfill_manifest_sha256": core.sha256_file(args.backfill_manifest),
        "ranking_sha256": core.sha256_file(args.enriched_ranking),
        "outcomes_sha256": core.sha256_file(args.universe_outcomes),
        "protocol_sha256": core.sha256_file(args.protocol),
        "source_backfill_freshness_wrapper": backfill_manifest.get(
            "detailed_freshness_wrapper_version"
        ),
        "scorecard_pass_cells": int(scorecard["all_research_gates_pass"].sum()),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report_ja.md").write_text(
        report_markdown(manifest, summary, paired, ic_summary, scorecard),
        encoding="utf-8",
    )

    if args.strict:
        expected_years = {2018, 2019, 2020, 2021}
        if set(manifest["years"]) != expected_years:
            raise RuntimeError(f"holdout years mismatch: {manifest['years']}")
        if not {"production", "healthy_v1", "healthy_v3"}.issubset(
            set(manifest["methods"])
        ):
            raise RuntimeError("required methods missing")
        if set(scorecard["promotion_status"]) != {
            "RESEARCH_SUPPORT_ONLY_NON_PROMOTABLE"
        }:
            raise RuntimeError("invalid promotion status")
        if len(scorecard) != 6:
            raise RuntimeError(f"scorecard rows {len(scorecard)} != 6")
        if not ranked.loc[ranked["healthy_v3_eligible"], "healthy_eligible"].astype(bool).all():
            raise RuntimeError("v3 admitted a Healthy v1 ineligible row")
        if ranked.loc[ranked["healthy_v3_eligible"], "healthy_v3_rank"].isna().any():
            raise RuntimeError("v3 eligible row lacks rank")
        if manifest["production_state_mutations"] or manifest["healthy_v1_eligibility_mutations"]:
            raise RuntimeError("forbidden state mutation")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
