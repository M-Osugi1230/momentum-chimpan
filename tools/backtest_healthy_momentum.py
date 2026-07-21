#!/usr/bin/env python3
"""Reproduce Healthy Momentum v1 against persisted ranking history.

Historical replay is diagnostic only. It compares report-close outcomes and is not valid
promotion evidence. Production promotion still requires live forward outcomes using the
governed next-available-session execution model and explicit manual approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import healthy_momentum


def dataframe_sha256(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def top100_fingerprint(frame: pd.DataFrame) -> str:
    rank = pd.to_numeric(frame.get("rank"), errors="coerce")
    work = frame[rank.between(1, 100, inclusive="both")].copy()
    work["rank"] = pd.to_numeric(work["rank"], errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work["score"] = pd.to_numeric(work["score"], errors="coerce")
    stable = work.sort_values(["rank", "code"])[["rank", "code", "close", "score"]]
    return dataframe_sha256(stable)


def canonical_reports(history: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Collapse exact reruns while preferring the later, fuller report."""
    work = history.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date_sort", "code", "close"])
    diagnostics: list[dict[str, Any]] = []
    groups: list[tuple[pd.Timestamp, str, pd.DataFrame]] = []
    for date, group in work.groupby("date_sort", sort=True):
        fingerprint = top100_fingerprint(group)
        groups.append((date, fingerprint, group.copy()))

    seen: set[str] = set()
    retained: list[pd.DataFrame] = []
    # Later report wins when two reports contain the same complete ranking.
    for date, fingerprint, group in reversed(groups):
        duplicate = fingerprint in seen
        diagnostics.append({
            "report_date": date.date().isoformat(),
            "row_count": int(len(group)),
            "top100_fingerprint": fingerprint,
            "exact_duplicate_later_report": duplicate,
            "retained": not duplicate,
        })
        if duplicate:
            continue
        seen.add(fingerprint)
        retained.append(group)
    canonical = pd.concat(reversed(retained), ignore_index=True, sort=False) if retained else pd.DataFrame(columns=history.columns)
    canonical = canonical.drop(columns=["date_sort"], errors="ignore")
    diagnostics_frame = pd.DataFrame(diagnostics).sort_values("report_date").reset_index(drop=True)
    return canonical, diagnostics_frame


def attach_future_returns(frame: pd.DataFrame, horizons: list[int]) -> tuple[pd.DataFrame, list[str]]:
    work = frame.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work["code"] = work["code"].map(healthy_momentum.normalized_code)
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["date_sort", "code", "close"])
    report_dates = sorted(work["date_sort"].drop_duplicates())
    date_index = {date: index for index, date in enumerate(report_dates)}
    base = work[["date_sort", "code", "close"]].drop_duplicates(["date_sort", "code"], keep="last")

    for horizon in horizons:
        pairs = [
            {"date_sort": date, "target_date": report_dates[date_index[date] + horizon]}
            for date in report_dates
            if date_index[date] + horizon < len(report_dates)
        ]
        pair_frame = pd.DataFrame(pairs)
        if pair_frame.empty:
            work[f"forward_return_{horizon}"] = np.nan
            continue
        current = base.merge(pair_frame, on="date_sort", how="inner")
        future = base.rename(columns={"date_sort": "target_date", "close": "future_close"})
        current = current.merge(future, on=["target_date", "code"], how="left")
        current[f"forward_return_{horizon}"] = current["future_close"] / current["close"] - 1.0
        work = work.merge(
            current[["date_sort", "code", f"forward_return_{horizon}"]],
            on=["date_sort", "code"],
            how="left",
        )
    work["date"] = work["date_sort"].dt.date.astype(str)
    return work.drop(columns=["date_sort"]), [date.date().isoformat() for date in report_dates]


def metric_row(values: pd.Series, date_means: list[float]) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return {
        "observations": int(len(clean)),
        "report_dates": int(len(date_means)),
        "mean_return": float(clean.mean()) if len(clean) else None,
        "median_return": float(clean.median()) if len(clean) else None,
        "win_rate": float(clean.gt(0).mean()) if len(clean) else None,
        "mean_daily_return": float(np.mean(date_means)) if date_means else None,
    }


def evaluate_method(
    frame: pd.DataFrame,
    method: str,
    rank_column: str,
    top_sizes: list[int],
    horizons: list[int],
    eligible_only: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    per_date: list[dict[str, Any]] = []
    for top_size in top_sizes:
        for horizon in horizons:
            all_values: list[float] = []
            date_means: list[float] = []
            for date, group in frame.groupby("date", sort=True):
                selected = group.copy()
                if eligible_only:
                    selected = selected[selected["healthy_eligible"]]
                selected = selected.sort_values(rank_column, na_position="last").head(top_size)
                returns = pd.to_numeric(selected[f"forward_return_{horizon}"], errors="coerce").dropna()
                if returns.empty:
                    continue
                all_values.extend(returns.tolist())
                date_means.append(float(returns.mean()))
                per_date.append({
                    "method": method,
                    "top_size": top_size,
                    "horizon_reports": horizon,
                    "signal_date": date,
                    "observations": int(len(returns)),
                    "mean_return": float(returns.mean()),
                    "median_return": float(returns.median()),
                    "win_rate": float(returns.gt(0).mean()),
                })
            metrics = metric_row(pd.Series(all_values, dtype=float), date_means)
            summaries.append({
                "method": method,
                "top_size": top_size,
                "horizon_reports": horizon,
                **metrics,
            })
    return pd.DataFrame(summaries), pd.DataFrame(per_date)


def compare_methods(baseline: pd.DataFrame, healthy: pd.DataFrame) -> pd.DataFrame:
    keys = ["top_size", "horizon_reports"]
    left = baseline.rename(columns={
        column: f"baseline_{column}"
        for column in baseline.columns
        if column not in keys + ["method"]
    }).drop(columns=["method"])
    right = healthy.rename(columns={
        column: f"healthy_{column}"
        for column in healthy.columns
        if column not in keys + ["method"]
    }).drop(columns=["method"])
    comparison = left.merge(right, on=keys, how="outer")
    for metric in ("mean_return", "median_return", "win_rate", "mean_daily_return"):
        comparison[f"improvement_{metric}"] = comparison[f"healthy_{metric}"] - comparison[f"baseline_{metric}"]
    return comparison.sort_values(keys).reset_index(drop=True)


def period_comparison(per_date: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for (top_size, horizon), group in per_date.groupby(["top_size", "horizon_reports"]):
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
            records.append({
                "top_size": int(top_size),
                "horizon_reports": int(horizon),
                "period": period,
                "report_dates": int(len(selected_dates)),
                "baseline_mean_daily_return": float(pivot.get("production", np.nan)),
                "healthy_mean_daily_return": float(pivot.get("healthy_v1", np.nan)),
                "improvement": float(pivot.get("healthy_v1", np.nan) - pivot.get("production", np.nan)),
            })
    return pd.DataFrame(records)


def latest_tables(enriched: pd.DataFrame, top_size: int = 100) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.to_datetime(enriched["date"], errors="coerce")
    latest_date = dates.max()
    latest = enriched[dates == latest_date].copy()
    healthy_top = latest[latest["healthy_eligible"]].sort_values("healthy_rank").head(top_size).copy()
    production_top = latest[pd.to_numeric(latest["rank"], errors="coerce").le(top_size)].sort_values("rank").copy()
    excluded = latest[~latest["healthy_eligible"]].sort_values(
        ["score", "rank"], ascending=[False, True], na_position="last"
    ).copy()
    return healthy_top, production_top, excluded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", default="data/momentum_daily_ranking.csv")
    parser.add_argument("--policy", default=healthy_momentum.DEFAULT_POLICY_PATH)
    parser.add_argument("--output-dir", default="output/healthy-momentum-shadow")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    policy = healthy_momentum.load_policy(args.policy)
    history = pd.read_csv(args.history, dtype={"code": str}, low_memory=False)
    canonical, report_diagnostics = canonical_reports(history)
    enriched = healthy_momentum.attach(canonical, policy)
    horizons = [int(value) for value in policy["shadow_backtest"]["horizons"]]
    top_sizes = [int(value) for value in policy["shadow_backtest"]["top_sizes"]]
    enriched, report_dates = attach_future_returns(enriched, horizons)

    baseline, baseline_daily = evaluate_method(
        enriched, "production", "rank", top_sizes, horizons, eligible_only=False
    )
    healthy, healthy_daily = evaluate_method(
        enriched, "healthy_v1", "healthy_rank", top_sizes, horizons, eligible_only=True
    )
    comparison = compare_methods(baseline, healthy)
    per_date = pd.concat([baseline_daily, healthy_daily], ignore_index=True, sort=False)
    period = period_comparison(per_date)
    healthy_top, production_top, excluded = latest_tables(enriched, max(top_sizes))
    exclusions = healthy_momentum.exclusion_summary(enriched[pd.to_datetime(enriched["date"]) == pd.to_datetime(enriched["date"]).max()])

    output_frames = {
        "historical_comparison.csv": comparison,
        "historical_per_date.csv": per_date,
        "historical_period_stability.csv": period,
        "latest_healthy_top100.csv": healthy_top,
        "latest_production_top100.csv": production_top,
        "latest_excluded.csv": excluded,
        "latest_exclusion_summary.csv": exclusions,
        "report_diagnostics.csv": report_diagnostics,
    }
    for filename, frame in output_frames.items():
        frame.to_csv(output_dir / filename, index=False)

    key = comparison[(comparison["top_size"] == 30) & (comparison["horizon_reports"] == 3)]
    key_record = {} if key.empty else key.iloc[0].to_dict()
    latest_date = max(report_dates) if report_dates else None
    summary = {
        "version": healthy_momentum.HEALTHY_MOMENTUM_VERSION,
        "mode": policy["mode"],
        "research_only": True,
        "automatic_promotion": False,
        "production_ranking_changed": False,
        "paper_strategy_changed": False,
        "historical_results_are_promotion_evidence": False,
        "source_history": args.history,
        "source_rows": int(len(history)),
        "canonical_rows": int(len(canonical)),
        "canonical_report_dates": int(len(report_dates)),
        "first_report_date": min(report_dates) if report_dates else None,
        "latest_report_date": latest_date,
        "latest_eligible_count": int(healthy_top["healthy_eligible"].sum()) if not healthy_top.empty else 0,
        "latest_top100_count": int(len(healthy_top)),
        "key_diagnostic": {
            "top_size": 30,
            "horizon_reports": 3,
            "baseline_mean_return": key_record.get("baseline_mean_return"),
            "healthy_mean_return": key_record.get("healthy_mean_return"),
            "improvement_mean_return": key_record.get("improvement_mean_return"),
            "baseline_win_rate": key_record.get("baseline_win_rate"),
            "healthy_win_rate": key_record.get("healthy_win_rate"),
        },
        "policy_sha256": hashlib.sha256(Path(args.policy).read_bytes()).hexdigest(),
        "files": {
            filename: {"rows": int(len(frame)), "sha256": dataframe_sha256(frame)}
            for filename, frame in output_frames.items()
        },
        "limitations": [
            "The available report history is short and covers a limited market regime.",
            "Report-close replay is not the governed next-available-session execution model.",
            "Historical thresholds were informed by the same broad sample and may overfit.",
            "No production or paper strategy may be promoted from this replay alone.",
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
