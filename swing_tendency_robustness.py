"""Robust comparisons and casebook for the three-month swing tendency study.

Research-only. This module does not optimize thresholds or mutate any production state.
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

VERSION = "2026-07-22-swing-tendency-robustness-v1"
METHOD_PAIRS = (
    ("healthy_v3", "healthy_v1"),
    ("healthy_v1", "production"),
)


def trimmed_mean(series: pd.Series, fraction: float = 0.05) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().sort_values().to_numpy(float)
    if len(values) == 0:
        return np.nan
    trim = int(np.floor(len(values) * fraction))
    if trim == 0 or trim * 2 >= len(values):
        return float(values.mean())
    return float(values[trim:-trim].mean())


def paired_bootstrap(
    left: pd.Series,
    right: pd.Series,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    pair = pd.concat([left.rename("left"), right.rename("right")], axis=1).dropna()
    if pair.empty:
        return {
            "paired_dates": 0,
            "mean_delta": np.nan,
            "ci_low": np.nan,
            "ci_high": np.nan,
            "daily_outperformance_rate": np.nan,
        }
    delta = (pair["left"] - pair["right"]).to_numpy(float)
    rng = np.random.default_rng(seed)
    if len(delta) == 1:
        sampled = delta
    else:
        sampled = rng.choice(delta, size=(iterations, len(delta)), replace=True).mean(axis=1)
    low, high = np.quantile(sampled, [0.025, 0.975])
    return {
        "paired_dates": len(delta),
        "mean_delta": float(delta.mean()),
        "ci_low": float(low),
        "ci_high": float(high),
        "daily_outperformance_rate": float((delta > 0).mean()),
    }


def load_events(path: str) -> pd.DataFrame:
    events = pd.read_csv(path, dtype={"code": str}, low_memory=False)
    events["code"] = events["code"].astype(str).str.split(".").str[0].str.zfill(4)
    events["signal_date"] = pd.to_datetime(events["signal_date"], errors="coerce").dt.normalize()
    for column in ("horizon_sessions", "method_rank", "net_return", "market_excess_net", "mfe", "mae"):
        events[column] = pd.to_numeric(events[column], errors="coerce")
    events["year"] = events["signal_date"].dt.year.astype("Int64")
    return events[events["method"].isin({"production", "healthy_v1", "healthy_v3"})].copy()


def paired_horizons(events: pd.DataFrame, top_sizes: tuple[int, ...], horizons: tuple[int, ...], iterations: int) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for method, benchmark in METHOD_PAIRS:
        for top_size in top_sizes:
            top = events[events["method_rank"].le(top_size)]
            for horizon in horizons:
                horizon_rows = top[top["horizon_sessions"].eq(horizon)]
                for year_label, group in [(str(year), horizon_rows[horizon_rows["year"].eq(year)]) for year in sorted(horizon_rows["year"].dropna().unique())] + [("ALL", horizon_rows)]:
                    left = group[group["method"].eq(method)]
                    right = group[group["method"].eq(benchmark)]
                    left_daily = left.groupby("signal_date")["net_return"].mean()
                    right_daily = right.groupby("signal_date")["net_return"].mean()
                    stats = paired_bootstrap(left_daily, right_daily, iterations, 20260722 + top_size + horizon + sum(map(ord, method)) + (0 if year_label == "ALL" else int(year_label)))
                    records.append(
                        {
                            "year": year_label,
                            "method": method,
                            "benchmark_method": benchmark,
                            "top_size": top_size,
                            "horizon_sessions": horizon,
                            "method_mean": left_daily.mean(),
                            "benchmark_mean": right_daily.mean(),
                            "method_trimmed_mean": trimmed_mean(left["net_return"]),
                            "benchmark_trimmed_mean": trimmed_mean(right["net_return"]),
                            "trimmed_delta": trimmed_mean(left["net_return"]) - trimmed_mean(right["net_return"]),
                            **stats,
                        }
                    )
    return pd.DataFrame(records)


def paired_marginals(detail: pd.DataFrame, top_sizes: tuple[int, ...], iterations: int) -> pd.DataFrame:
    detail["signal_date"] = pd.to_datetime(detail["signal_date"], errors="coerce").dt.normalize()
    records: list[dict[str, Any]] = []
    for method, benchmark in METHOD_PAIRS:
        for top_size in top_sizes:
            top = detail[pd.to_numeric(detail["method_rank"], errors="coerce").le(top_size)]
            for (start, end), interval_rows in top.groupby(["interval_start", "interval_end"], sort=True):
                years = sorted(pd.to_numeric(interval_rows["year"], errors="coerce").dropna().astype(int).unique())
                for year_label, group in [(str(year), interval_rows[pd.to_numeric(interval_rows["year"], errors="coerce").eq(year)]) for year in years] + [("ALL", interval_rows)]:
                    left = group[group["method"].eq(method)]
                    right = group[group["method"].eq(benchmark)]
                    left_daily = left.groupby("signal_date")["interval_return"].mean()
                    right_daily = right.groupby("signal_date")["interval_return"].mean()
                    stats = paired_bootstrap(left_daily, right_daily, iterations, 20260723 + top_size + int(start) + int(end) + sum(map(ord, method)) + (0 if year_label == "ALL" else int(year_label)))
                    records.append(
                        {
                            "year": year_label,
                            "method": method,
                            "benchmark_method": benchmark,
                            "top_size": top_size,
                            "interval_start": int(start),
                            "interval_end": int(end),
                            "method_mean": left_daily.mean(),
                            "benchmark_mean": right_daily.mean(),
                            "method_trimmed_mean": trimmed_mean(left["interval_return"]),
                            "benchmark_trimmed_mean": trimmed_mean(right["interval_return"]),
                            "trimmed_delta": trimmed_mean(left["interval_return"]) - trimmed_mean(right["interval_return"]),
                            **stats,
                        }
                    )
    return pd.DataFrame(records)


def leave_one_sector(events: pd.DataFrame, top_sizes: tuple[int, ...], horizons: tuple[int, ...]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    sectors = sorted(value for value in events["sector33"].fillna("").astype(str).unique() if value)
    for method, benchmark in METHOD_PAIRS:
        for top_size in top_sizes:
            top = events[events["method_rank"].le(top_size)]
            for horizon in horizons:
                rows = top[top["horizon_sessions"].eq(horizon)]
                for sector in sectors:
                    reduced = rows[rows["sector33"].fillna("").astype(str).ne(sector)]
                    left = reduced[reduced["method"].eq(method)].groupby("signal_date")["net_return"].mean()
                    right = reduced[reduced["method"].eq(benchmark)].groupby("signal_date")["net_return"].mean()
                    pair = pd.concat([left.rename("left"), right.rename("right")], axis=1).dropna()
                    if pair.empty:
                        continue
                    records.append(
                        {
                            "method": method,
                            "benchmark_method": benchmark,
                            "top_size": top_size,
                            "horizon_sessions": horizon,
                            "excluded_sector": sector,
                            "paired_dates": len(pair),
                            "delta": (pair["left"] - pair["right"]).mean(),
                        }
                    )
    return pd.DataFrame(records)


def path_timing(path: pd.DataFrame, top_sizes: tuple[int, ...]) -> pd.DataFrame:
    path["signal_date"] = pd.to_datetime(path["signal_date"], errors="coerce").dt.normalize()
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = path[pd.to_numeric(path["method_rank"], errors="coerce").le(top_size)]
        for year_label, year_rows in [(str(year), top[top["year"].eq(year)]) for year in sorted(top["year"].unique())] + [("ALL", top)]:
            for method, group in year_rows.groupby("method", sort=True):
                records.append(
                    {
                        "year": year_label,
                        "method": method,
                        "top_size": top_size,
                        "observations": len(group),
                        "dates": group["signal_date"].nunique(),
                        "mean_mfe60": group["mfe60"].mean(),
                        "median_mfe60": group["mfe60"].median(),
                        "mean_mae60": group["mae60"].mean(),
                        "median_mae60": group["mae60"].median(),
                        "mean_max_dd60": group["max_dd60"].mean(),
                        "median_time_mfe": group["time_mfe"].median(),
                        "median_time_mae": group["time_mae"].median(),
                        "mfe_after_day20_rate": group["time_mfe"].gt(20).mean(),
                        "mfe_after_day40_rate": group["time_mfe"].gt(40).mean(),
                        "mae_by_day10_rate": group["time_mae"].le(10).mean(),
                        "mae_by_day20_rate": group["time_mae"].le(20).mean(),
                        "mean_return20": group["return20"].mean(),
                        "mean_return40": group["return40"].mean(),
                        "mean_return60": group["return60"].mean(),
                    }
                )
    return pd.DataFrame(records)


def threshold_summary(touch: pd.DataFrame, top_sizes: tuple[int, ...]) -> pd.DataFrame:
    touch["signal_date"] = pd.to_datetime(touch["signal_date"], errors="coerce").dt.normalize()
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = touch[pd.to_numeric(touch["method_rank"], errors="coerce").le(top_size)]
        for keys, group in top.groupby(["year", "method", "upside_threshold", "downside_threshold"], sort=True):
            decisive = group[group["first_touch"].isin(["UP_FIRST", "DOWN_FIRST"])]
            records.append(
                {
                    "year": int(keys[0]),
                    "method": keys[1],
                    "top_size": top_size,
                    "upside_threshold": keys[2],
                    "downside_threshold": keys[3],
                    "observations": len(group),
                    "dates": group["signal_date"].nunique(),
                    "up_first_rate": group["first_touch"].eq("UP_FIRST").mean(),
                    "down_first_rate": group["first_touch"].eq("DOWN_FIRST").mean(),
                    "both_same_day_rate": group["first_touch"].eq("BOTH").mean(),
                    "neither_rate": group["first_touch"].eq("NEITHER").mean(),
                    "decisive_up_share": decisive["first_touch"].eq("UP_FIRST").mean() if len(decisive) else np.nan,
                    "median_up_session": group["up_session"].median(),
                    "median_down_session": group["down_session"].median(),
                }
            )
    return pd.DataFrame(records)


def adverse_summary(adverse: pd.DataFrame, top_sizes: tuple[int, ...]) -> pd.DataFrame:
    adverse["signal_date"] = pd.to_datetime(adverse["signal_date"], errors="coerce").dt.normalize()
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = adverse[pd.to_numeric(adverse["method_rank"], errors="coerce").le(top_size)]
        for keys, group in top.groupby(["year", "method", "upside_threshold"], sort=True):
            reached = group[group["reached"].astype(str).str.lower().isin({"true", "1"})]
            records.append(
                {
                    "year": int(keys[0]),
                    "method": keys[1],
                    "top_size": top_size,
                    "upside_threshold": keys[2],
                    "observations": len(group),
                    "dates": group["signal_date"].nunique(),
                    "reach_rate": len(reached) / len(group) if len(group) else np.nan,
                    "median_reach_session": reached["first_reach_session"].median(),
                    "median_pre_profit_mae_reached": reached["pre_profit_mae"].median(),
                    "p25_pre_profit_mae_reached": reached["pre_profit_mae"].quantile(0.25),
                    "mean_pre_profit_mae_reached": reached["pre_profit_mae"].mean(),
                }
            )
    return pd.DataFrame(records)


def context_consistency(path: str, group_columns: list[str], label: str, minimum_years: int, minimum_observations: int, minimum_dates: int) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    keys = ["method", "top_size", "horizon_sessions", *group_columns]
    records: list[dict[str, Any]] = []
    for key, group in frame.groupby(keys, observed=True, sort=True):
        valid = group[(group["observations"] >= minimum_observations) & (group["dates"] >= minimum_dates)]
        if valid.empty:
            continue
        if not isinstance(key, tuple):
            key = (key,)
        row = dict(zip(keys, key))
        positive = valid["mean_20bps"].gt(0) & valid["trimmed_mean_20bps"].gt(0)
        negative = valid["mean_20bps"].lt(0) & valid["trimmed_mean_20bps"].lt(0)
        row.update(
            {
                "context_type": label,
                "valid_years": valid["year"].nunique(),
                "positive_confirmed_years": int(positive.sum()),
                "negative_confirmed_years": int(negative.sum()),
                "mean_of_year_means": valid["mean_20bps"].mean(),
                "mean_of_year_trimmed_means": valid["trimmed_mean_20bps"].mean(),
                "consistent_positive": int(positive.sum()) >= minimum_years,
                "consistent_negative": int(negative.sum()) >= minimum_years,
                "status": "DESCRIPTIVE_ONLY",
            }
        )
        records.append(row)
    return pd.DataFrame(records)


def casebook(path: pd.DataFrame) -> pd.DataFrame:
    base = path.dropna(subset=["return60"]).drop_duplicates(["method", "signal_date", "code"]).copy()
    groups: list[pd.DataFrame] = []
    definitions = (
        ("TOP_RETURN_60", base.nlargest(30, "return60")),
        ("BOTTOM_RETURN_60", base.nsmallest(30, "return60")),
        ("LARGE_ADVERSE_THEN_WIN", base[(base["return60"] >= 0.10) & (base["mae60"] <= -0.08)].nsmallest(30, "mae60")),
        ("GAIN_GIVEBACK", base[(base["mfe60"] >= 0.10) & (base["return60"] < 0)].nlargest(30, "mfe60")),
    )
    for case_type, group in definitions:
        if group.empty:
            continue
        copy = group.copy()
        copy.insert(0, "case_type", case_type)
        groups.append(copy)
    return pd.concat(groups, ignore_index=True) if groups else pd.DataFrame()


def report(horizon: pd.DataFrame, marginal: pd.DataFrame, consistency: pd.DataFrame) -> str:
    lines = [
        "# Swing Tendency Study v1｜頑健性補足",
        "",
        "> 研究専用です。利確・損切り・本番ランキングを変更する根拠には使用しません。",
        "",
        "## Healthy v3とHealthy v1の通算比較",
        "",
        "| Top | 期間 | 平均差 | 95%CI | 日次超過率 | 5%トリム差 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    focus = horizon[(horizon["year"].astype(str) == "ALL") & horizon["method"].eq("healthy_v3") & horizon["benchmark_method"].eq("healthy_v1")]
    for row in focus.sort_values(["top_size", "horizon_sessions"]).itertuples(index=False):
        lines.append(
            f"| {row.top_size} | {row.horizon_sessions}日 | {row.mean_delta:+.2%} | {row.ci_low:+.2%}〜{row.ci_high:+.2%} | {row.daily_outperformance_rate:.1%} | {row.trimmed_delta:+.2%} |"
        )
    lines += ["", "## 追加保有の通算比較", "", "| Top | 区間 | 平均差 | 95%CI | 5%トリム差 |", "|---:|---:|---:|---:|---:|"]
    focus_m = marginal[(marginal["year"].astype(str) == "ALL") & marginal["method"].eq("healthy_v3") & marginal["benchmark_method"].eq("healthy_v1")]
    for row in focus_m.sort_values(["top_size", "interval_start", "interval_end"]).itertuples(index=False):
        lines.append(
            f"| {row.top_size} | {row.interval_start}→{row.interval_end}日 | {row.mean_delta:+.2%} | {row.ci_low:+.2%}〜{row.ci_high:+.2%} | {row.trimmed_delta:+.2%} |"
        )
    lines += ["", "## 複数年で確認された条件", ""]
    stable = consistency[consistency["consistent_positive"].fillna(False) | consistency["consistent_negative"].fillna(False)]
    if stable.empty:
        lines.append("- 観測数・日数・複数年の固定条件をすべて満たす条件はありません。")
    else:
        for row in stable.sort_values(["context_type", "method", "top_size", "horizon_sessions"]).head(40).itertuples(index=False):
            direction = "プラス" if row.consistent_positive else "マイナス"
            lines.append(f"- {row.context_type} / {row.method} / Top{row.top_size} / {row.horizon_sessions}日：{direction}方向（有効年{row.valid_years}）")
    lines += [
        "",
        "## 解釈上の注意",
        "",
        "- 同一銘柄・近接日付の重複シグナルを含むため、独立取引の損益曲線ではありません。",
        "- 日足OHLCで上昇・下落水準を同日に触れた場合、日中の先後は判定できません。",
        "- 現在上場銘柄を過去へ遡るサバイバーシップ・構成銘柄バイアスがあります。",
        "- 同じ期間内で保有日数や閾値を最適化していません。",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--marginal-detail", required=True)
    parser.add_argument("--path-detail", required=True)
    parser.add_argument("--first-touch", required=True)
    parser.add_argument("--adverse", required=True)
    parser.add_argument("--signal-state-summary", required=True)
    parser.add_argument("--regime-summary", required=True)
    parser.add_argument("--liquidity-summary", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    protocol = yaml.safe_load(Path(args.protocol).read_text(encoding="utf-8")) or {}
    evaluation = protocol["evaluation"]
    interpretation = protocol["interpretation"]
    top_sizes = tuple(int(value) for value in evaluation["top_sizes"])
    horizons = tuple(int(value) for value in evaluation["focus_horizons"])
    iterations = int(evaluation["robust_statistics"]["paired_bootstrap_iterations"])

    events = load_events(args.events)
    marginal_detail = pd.read_csv(args.marginal_detail, dtype={"code": str}, low_memory=False)
    path_detail = pd.read_csv(args.path_detail, dtype={"code": str}, low_memory=False)
    first_touch = pd.read_csv(args.first_touch, dtype={"code": str}, low_memory=False)
    adverse = pd.read_csv(args.adverse, dtype={"code": str}, low_memory=False)

    horizon_pairs = paired_horizons(events, top_sizes, horizons, iterations)
    marginal_pairs = paired_marginals(marginal_detail, top_sizes, iterations)
    sector = leave_one_sector(events, top_sizes, horizons)
    timing = path_timing(path_detail, top_sizes)
    thresholds = threshold_summary(first_touch, top_sizes)
    adverse_stats = adverse_summary(adverse, top_sizes)
    context_frames = [
        context_consistency(args.signal_state_summary, ["signal_state"], "SIGNAL_STATE", int(interpretation["minimum_years_same_direction"]), int(interpretation["minimum_observations_per_cell"]), int(interpretation["minimum_signal_dates_per_cell"])),
        context_consistency(args.regime_summary, ["breadth_regime", "trend_regime", "vol_regime"], "MARKET_REGIME", int(interpretation["minimum_years_same_direction"]), int(interpretation["minimum_observations_per_cell"]), int(interpretation["minimum_signal_dates_per_cell"])),
        context_consistency(args.liquidity_summary, ["liquidity_band", "ma20_band"], "LIQUIDITY_MA20", int(interpretation["minimum_years_same_direction"]), int(interpretation["minimum_observations_per_cell"]), int(interpretation["minimum_signal_dates_per_cell"])),
    ]
    context = pd.concat([frame for frame in context_frames if not frame.empty], ignore_index=True, sort=False)
    cases = casebook(path_detail)

    outputs = {
        "paired_horizon_comparison.csv": horizon_pairs,
        "paired_marginal_comparison.csv": marginal_pairs,
        "leave_one_sector_swing.csv": sector,
        "path_timing_summary.csv": timing,
        "threshold_first_touch_summary.csv": thresholds,
        "pre_profit_adverse_summary.csv": adverse_stats,
        "context_consistency.csv": context,
        "swing_casebook.csv": cases,
    }
    for name, frame in outputs.items():
        frame.to_csv(output / name, index=False)
    (output / "swing_robustness_report_ja.md").write_text(report(horizon_pairs, marginal_pairs, context), encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "paired_horizon_rows": len(horizon_pairs),
        "paired_marginal_rows": len(marginal_pairs),
        "leave_one_sector_rows": len(sector),
        "path_timing_rows": len(timing),
        "threshold_summary_rows": len(thresholds),
        "adverse_summary_rows": len(adverse_stats),
        "context_consistency_rows": len(context),
        "casebook_rows": len(cases),
        "research_only": True,
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "automatic_exit_rule_change": False,
        "production_state_mutations": [],
    }
    (output / "robustness_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.strict:
        assert not horizon_pairs.empty and not marginal_pairs.empty and not timing.empty
        assert set(horizon_pairs["horizon_sessions"]) == set(horizons)
        assert set(horizon_pairs["benchmark_method"]) == {"healthy_v1", "production"}
        assert path_detail["path_data_quality"].eq("OK").all()
        assert not manifest["production_state_mutations"]
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
