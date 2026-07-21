#!/usr/bin/env python3
"""Compare production, Healthy Momentum v1, and v2 using persisted reports.

The replay is diagnostic only.  It uses report-close snapshots and cannot be used as
promotion evidence.  Production promotion still requires governed live forward outcomes
with next-available-session adjusted-open entry and explicit manual approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import healthy_momentum
import healthy_momentum_v2
from tools import backtest_healthy_momentum as v1_backtest


def dataframe_sha256(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evaluate_method(
    frame: pd.DataFrame,
    method: str,
    rank_column: str,
    top_sizes: list[int],
    horizons: list[int],
    eligible_column: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    per_date: list[dict[str, Any]] = []
    selections: list[pd.DataFrame] = []
    for top_size in top_sizes:
        for horizon in horizons:
            all_values: list[float] = []
            date_means: list[float] = []
            for date, group in frame.groupby("date", sort=True):
                selected = group.copy()
                if eligible_column:
                    selected = selected[selected[eligible_column].fillna(False)]
                selected = selected.sort_values(rank_column, na_position="last").head(top_size)
                selected = selected.copy()
                selected["method"] = method
                selected["top_size"] = top_size
                selected["horizon_reports"] = horizon
                selected["signal_date"] = date
                selected["selected_order"] = range(1, len(selected) + 1)
                selections.append(selected)
                returns = pd.to_numeric(
                    selected[f"forward_return_{horizon}"], errors="coerce"
                ).dropna()
                if returns.empty:
                    continue
                all_values.extend(returns.tolist())
                date_means.append(float(returns.mean()))
                per_date.append(
                    {
                        "method": method,
                        "top_size": top_size,
                        "horizon_reports": horizon,
                        "signal_date": date,
                        "observations": int(len(returns)),
                        "mean_return": float(returns.mean()),
                        "median_return": float(returns.median()),
                        "win_rate": float(returns.gt(0).mean()),
                    }
                )
            clean = pd.Series(all_values, dtype=float)
            summaries.append(
                {
                    "method": method,
                    "top_size": top_size,
                    "horizon_reports": horizon,
                    "observations": int(len(clean)),
                    "report_dates": int(len(date_means)),
                    "mean_return": float(clean.mean()) if len(clean) else None,
                    "median_return": float(clean.median()) if len(clean) else None,
                    "win_rate": float(clean.gt(0).mean()) if len(clean) else None,
                    "mean_daily_return": float(np.mean(date_means)) if date_means else None,
                }
            )
    selection_frame = (
        pd.concat(selections, ignore_index=True, sort=False) if selections else pd.DataFrame()
    )
    return pd.DataFrame(summaries), pd.DataFrame(per_date), selection_frame


def pairwise_comparison(
    summary: pd.DataFrame,
    left_method: str,
    right_method: str,
) -> pd.DataFrame:
    keys = ["top_size", "horizon_reports"]
    metrics = [
        "observations",
        "report_dates",
        "mean_return",
        "median_return",
        "win_rate",
        "mean_daily_return",
    ]
    left = summary[summary["method"] == left_method][keys + metrics].rename(
        columns={column: f"{left_method}_{column}" for column in metrics}
    )
    right = summary[summary["method"] == right_method][keys + metrics].rename(
        columns={column: f"{right_method}_{column}" for column in metrics}
    )
    compared = left.merge(right, on=keys, how="outer")
    for metric in ("mean_return", "median_return", "win_rate", "mean_daily_return"):
        compared[f"{right_method}_minus_{left_method}_{metric}"] = (
            compared[f"{right_method}_{metric}"] - compared[f"{left_method}_{metric}"]
        )
    return compared.sort_values(keys).reset_index(drop=True)


def period_stability(per_date: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for (top_size, horizon), group in per_date.groupby(
        ["top_size", "horizon_reports"]
    ):
        dates = sorted(group["signal_date"].unique())
        if not dates:
            continue
        split = max(len(dates) // 2, 1)
        periods = {"early": dates[:split], "late": dates[split:]}
        for period, selected_dates in periods.items():
            if not selected_dates:
                continue
            subset = group[group["signal_date"].isin(selected_dates)]
            pivot = subset.groupby("method")["mean_return"].mean()
            records.append(
                {
                    "top_size": int(top_size),
                    "horizon_reports": int(horizon),
                    "period": period,
                    "report_dates": int(len(selected_dates)),
                    "production_mean_daily_return": float(
                        pivot.get("production", np.nan)
                    ),
                    "healthy_v1_mean_daily_return": float(
                        pivot.get("healthy_v1", np.nan)
                    ),
                    "healthy_v2_mean_daily_return": float(
                        pivot.get("healthy_v2", np.nan)
                    ),
                    "v2_minus_v1": float(
                        pivot.get("healthy_v2", np.nan)
                        - pivot.get("healthy_v1", np.nan)
                    ),
                    "v2_minus_production": float(
                        pivot.get("healthy_v2", np.nan)
                        - pivot.get("production", np.nan)
                    ),
                }
            )
    return pd.DataFrame(records)


def sector_performance(selections: pd.DataFrame) -> pd.DataFrame:
    if selections.empty:
        return pd.DataFrame()
    records: list[dict[str, Any]] = []
    for (method, top_size, horizon, sector), group in selections.groupby(
        ["method", "top_size", "horizon_reports", "sector33"], dropna=False
    ):
        returns = pd.to_numeric(
            group[f"forward_return_{int(horizon)}"], errors="coerce"
        ).dropna()
        if returns.empty:
            continue
        records.append(
            {
                "method": method,
                "top_size": int(top_size),
                "horizon_reports": int(horizon),
                "sector33": str(sector or ""),
                "observations": int(len(returns)),
                "mean_return": float(returns.mean()),
                "median_return": float(returns.median()),
                "win_rate": float(returns.gt(0).mean()),
            }
        )
    return pd.DataFrame(records).sort_values(
        ["top_size", "horizon_reports", "method", "mean_return"],
        ascending=[True, True, True, False],
    )


def leave_one_sector_diagnostic(
    frame: pd.DataFrame,
    top_size: int = 30,
    horizon: int = 3,
) -> pd.DataFrame:
    sectors = sorted(
        value
        for value in frame.get("sector33", pd.Series(dtype=str)).fillna("").unique()
        if str(value).strip()
    )
    records: list[dict[str, Any]] = []
    for excluded_sector in sectors:
        method_values: dict[str, list[float]] = {
            "production": [],
            "healthy_v1": [],
            "healthy_v2": [],
        }
        for _, group in frame.groupby("date", sort=True):
            candidates = group[group["sector33"].fillna("") != excluded_sector]
            method_specs = [
                ("production", "rank", None),
                ("healthy_v1", "healthy_rank", "healthy_eligible"),
                ("healthy_v2", "healthy_v2_rank", "healthy_v2_eligible"),
            ]
            for method, rank_column, eligible_column in method_specs:
                selected = candidates
                if eligible_column:
                    selected = selected[selected[eligible_column].fillna(False)]
                selected = selected.sort_values(rank_column, na_position="last").head(top_size)
                returns = pd.to_numeric(
                    selected[f"forward_return_{horizon}"], errors="coerce"
                ).dropna()
                method_values[method].extend(returns.tolist())
        record: dict[str, Any] = {
            "excluded_sector33": excluded_sector,
            "top_size": top_size,
            "horizon_reports": horizon,
        }
        for method, values in method_values.items():
            series = pd.Series(values, dtype=float)
            record[f"{method}_observations"] = int(len(series))
            record[f"{method}_mean_return"] = (
                float(series.mean()) if len(series) else None
            )
            record[f"{method}_win_rate"] = (
                float(series.gt(0).mean()) if len(series) else None
            )
        record["v2_minus_v1_mean_return"] = (
            record["healthy_v2_mean_return"] - record["healthy_v1_mean_return"]
            if record["healthy_v2_mean_return"] is not None
            and record["healthy_v1_mean_return"] is not None
            else None
        )
        record["v2_minus_production_mean_return"] = (
            record["healthy_v2_mean_return"] - record["production_mean_return"]
            if record["healthy_v2_mean_return"] is not None
            and record["production_mean_return"] is not None
            else None
        )
        records.append(record)
    return pd.DataFrame(records)


def latest_tables(
    enriched: pd.DataFrame, top_size: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.to_datetime(enriched["date"], errors="coerce")
    latest = enriched[dates == dates.max()].copy()
    production = latest[pd.to_numeric(latest["rank"], errors="coerce").le(top_size)].sort_values("rank")
    v1 = latest[latest["healthy_eligible"]].sort_values("healthy_rank").head(top_size)
    v2 = latest[latest["healthy_v2_eligible"]].sort_values("healthy_v2_rank").head(top_size)
    excluded = latest[~latest["healthy_v2_eligible"]].sort_values(
        ["healthy_selection_score", "score", "rank"],
        ascending=[False, False, True],
        na_position="last",
    )
    return production, v1, v2, excluded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", default="data/momentum_daily_ranking.csv")
    parser.add_argument("--policy", default=healthy_momentum_v2.DEFAULT_POLICY_PATH)
    parser.add_argument("--output-dir", default="output/healthy-momentum-v2-shadow")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    policy = healthy_momentum_v2.load_policy(args.policy)
    history = pd.read_csv(args.history, dtype={"code": str}, low_memory=False)
    canonical, report_diagnostics = v1_backtest.canonical_reports(history)
    enriched = healthy_momentum_v2.attach(canonical, policy)
    horizons = [int(value) for value in policy["shadow_backtest"]["horizons"]]
    top_sizes = [int(value) for value in policy["shadow_backtest"]["top_sizes"]]
    enriched, report_dates = v1_backtest.attach_future_returns(enriched, horizons)

    method_specs = [
        ("production", "rank", None),
        ("healthy_v1", "healthy_rank", "healthy_eligible"),
        ("healthy_v2", "healthy_v2_rank", "healthy_v2_eligible"),
    ]
    summaries: list[pd.DataFrame] = []
    per_dates: list[pd.DataFrame] = []
    selection_frames: list[pd.DataFrame] = []
    for method, rank_column, eligible_column in method_specs:
        summary, per_date, selections = evaluate_method(
            enriched,
            method,
            rank_column,
            top_sizes,
            horizons,
            eligible_column,
        )
        summaries.append(summary)
        per_dates.append(per_date)
        selection_frames.append(selections)

    method_summary = pd.concat(summaries, ignore_index=True, sort=False)
    per_date = pd.concat(per_dates, ignore_index=True, sort=False)
    selections = pd.concat(selection_frames, ignore_index=True, sort=False)
    production_vs_v1 = pairwise_comparison(method_summary, "production", "healthy_v1")
    production_vs_v2 = pairwise_comparison(method_summary, "production", "healthy_v2")
    v1_vs_v2 = pairwise_comparison(method_summary, "healthy_v1", "healthy_v2")
    periods = period_stability(per_date)
    sectors = sector_performance(selections)
    leave_one = leave_one_sector_diagnostic(enriched)
    production_latest, v1_latest, v2_latest, excluded_latest = latest_tables(
        enriched, max(top_sizes)
    )
    latest_date_mask = pd.to_datetime(enriched["date"], errors="coerce") == pd.to_datetime(
        enriched["date"], errors="coerce"
    ).max()
    exclusions = healthy_momentum_v2.exclusion_summary(enriched[latest_date_mask])

    output_frames = {
        "method_summary.csv": method_summary,
        "production_vs_v1.csv": production_vs_v1,
        "production_vs_v2.csv": production_vs_v2,
        "v1_vs_v2.csv": v1_vs_v2,
        "historical_per_date.csv": per_date,
        "historical_period_stability.csv": periods,
        "historical_sector_performance.csv": sectors,
        "historical_leave_one_sector.csv": leave_one,
        "latest_production_top100.csv": production_latest,
        "latest_healthy_v1_top100.csv": v1_latest,
        "latest_healthy_v2_top100.csv": v2_latest,
        "latest_v2_excluded.csv": excluded_latest,
        "latest_v2_exclusion_summary.csv": exclusions,
        "report_diagnostics.csv": report_diagnostics,
    }
    for filename, frame in output_frames.items():
        frame.to_csv(output_dir / filename, index=False)

    key = v1_vs_v2[
        (v1_vs_v2["top_size"] == 30) & (v1_vs_v2["horizon_reports"] == 3)
    ]
    key_record = {} if key.empty else key.iloc[0].to_dict()
    improvement_column = "healthy_v2_minus_healthy_v1_mean_return"
    mean_comparisons = pd.to_numeric(v1_vs_v2[improvement_column], errors="coerce")
    summary = {
        "version": healthy_momentum_v2.HEALTHY_MOMENTUM_V2_VERSION,
        "mode": policy["mode"],
        "research_only": True,
        "automatic_promotion": False,
        "production_ranking_changed": False,
        "healthy_v1_ranking_changed": False,
        "paper_strategy_changed": False,
        "historical_results_are_promotion_evidence": False,
        "source_history": args.history,
        "source_rows": int(len(history)),
        "canonical_rows": int(len(canonical)),
        "canonical_report_dates": int(len(report_dates)),
        "first_report_date": min(report_dates) if report_dates else None,
        "latest_report_date": max(report_dates) if report_dates else None,
        "latest_v1_eligible_count": int(
            enriched.loc[latest_date_mask, "healthy_eligible"].fillna(False).sum()
        ),
        "latest_v2_eligible_count": int(
            enriched.loc[latest_date_mask, "healthy_v2_eligible"].fillna(False).sum()
        ),
        "latest_v2_top100_count": int(len(v2_latest)),
        "v2_better_than_v1_comparisons": int(mean_comparisons.gt(0).sum()),
        "v2_total_comparisons": int(mean_comparisons.notna().sum()),
        "key_diagnostic": {
            "top_size": 30,
            "horizon_reports": 3,
            "healthy_v1_mean_return": key_record.get("healthy_v1_mean_return"),
            "healthy_v2_mean_return": key_record.get("healthy_v2_mean_return"),
            "v2_minus_v1_mean_return": key_record.get(improvement_column),
            "healthy_v1_win_rate": key_record.get("healthy_v1_win_rate"),
            "healthy_v2_win_rate": key_record.get("healthy_v2_win_rate"),
        },
        "policy_sha256": hashlib.sha256(Path(args.policy).read_bytes()).hexdigest(),
        "files": {
            filename: {"rows": int(len(frame)), "sha256": dataframe_sha256(frame)}
            for filename, frame in output_frames.items()
        },
        "limitations": [
            "The available report history is short and covers a limited market regime.",
            "Report-close replay is not the governed next-available-session execution model.",
            "V1 and V2 thresholds were designed after reviewing overlapping history and may overfit.",
            "A worse V2 result is retained and reported; it must not silently replace V1.",
            "No production or paper strategy may be promoted from this replay alone.",
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
