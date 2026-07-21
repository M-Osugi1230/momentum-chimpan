"""Add matched-count and eligibility diagnostics to historical OOS artifacts.

Healthy v1 and Balanced v2 can have fewer than 100 eligible stocks on some dates. This
module compares production using the same per-date stock count, preventing breadth of the
selected portfolio from being confused with ranking quality.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

METHODS = ("production", "healthy_v1", "balanced_v2")
TOP_SIZES = (10, 30, 100)
HORIZONS = (1, 3, 5, 10, 20)


def build_matched_count(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    events = events.copy()
    events["signal_date"] = pd.to_datetime(events["signal_date"], errors="coerce")
    events["method_rank"] = pd.to_numeric(events["method_rank"], errors="coerce")
    daily_records: list[dict[str, Any]] = []
    for top_size in TOP_SIZES:
        for horizon in HORIZONS:
            horizon_frame = events[events["horizon_sessions"] == horizon]
            for signal_date, date_frame in horizon_frame.groupby("signal_date", sort=True):
                v1 = date_frame[
                    (date_frame["method"] == "healthy_v1")
                    & date_frame["method_rank"].le(top_size)
                ]
                matched_count = len(v1)
                if matched_count == 0:
                    continue
                record: dict[str, Any] = {
                    "signal_date": signal_date,
                    "top_size": top_size,
                    "horizon_sessions": horizon,
                    "matched_count": matched_count,
                }
                for method in METHODS:
                    selected = date_frame[
                        (date_frame["method"] == method)
                        & date_frame["method_rank"].le(matched_count)
                    ]
                    record[f"{method}_count"] = len(selected)
                    record[f"{method}_mean_net_return"] = selected["net_return"].mean()
                    record[f"{method}_median_net_return"] = selected["net_return"].median()
                    record[f"{method}_win_rate"] = selected["net_return"].gt(0).mean()
                    record[f"{method}_mean_market_excess_net"] = selected[
                        "market_excess_net"
                    ].mean()
                daily_records.append(record)
    daily = pd.DataFrame(daily_records)
    summary_records: list[dict[str, Any]] = []
    for (top_size, horizon), group in daily.groupby(
        ["top_size", "horizon_sessions"], sort=True
    ):
        record = {
            "top_size": int(top_size),
            "horizon_sessions": int(horizon),
            "paired_dates": group["signal_date"].nunique(),
            "mean_matched_count": group["matched_count"].mean(),
            "min_matched_count": group["matched_count"].min(),
            "max_matched_count": group["matched_count"].max(),
        }
        for method in METHODS:
            record[f"{method}_mean_daily_net_return"] = group[
                f"{method}_mean_net_return"
            ].mean()
            record[f"{method}_median_daily_net_return"] = group[
                f"{method}_mean_net_return"
            ].median()
            record[f"{method}_mean_daily_market_excess_net"] = group[
                f"{method}_mean_market_excess_net"
            ].mean()
        record["healthy_v1_minus_production"] = (
            record["healthy_v1_mean_daily_net_return"]
            - record["production_mean_daily_net_return"]
        )
        record["balanced_v2_minus_healthy_v1"] = (
            record["balanced_v2_mean_daily_net_return"]
            - record["healthy_v1_mean_daily_net_return"]
        )
        summary_records.append(record)
    return daily, pd.DataFrame(summary_records)


def build_eligibility(ranking: pd.DataFrame) -> pd.DataFrame:
    ranking = ranking.copy()
    ranking["date"] = pd.to_datetime(ranking["date"], errors="coerce")
    for column in ("healthy_eligible", "healthy_v2_eligible"):
        ranking[column] = ranking[column].astype(str).str.lower().isin({"true", "1"})
    result = (
        ranking.groupby("date", sort=True)
        .agg(
            ranked_count=("code", "count"),
            healthy_v1_eligible_count=("healthy_eligible", "sum"),
            balanced_v2_eligible_count=("healthy_v2_eligible", "sum"),
        )
        .reset_index()
    )
    result["healthy_v1_eligible_ratio"] = (
        result["healthy_v1_eligible_count"] / result["ranked_count"]
    )
    result["balanced_v2_eligible_ratio"] = (
        result["balanced_v2_eligible_count"] / result["ranked_count"]
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default="output/oos-2025/analysis/selection_events.csv")
    parser.add_argument(
        "--ranking", default="output/oos-2025/analysis/enriched_historical_ranking.csv"
    )
    parser.add_argument("--output-dir", default="output/oos-2025/analysis")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    events = pd.read_csv(args.events, dtype={"code": str}, low_memory=False)
    ranking = pd.read_csv(args.ranking, dtype={"code": str}, low_memory=False)
    daily, summary = build_matched_count(events)
    eligibility = build_eligibility(ranking)
    daily.to_csv(output_dir / "matched_count_daily.csv", index=False)
    summary.to_csv(output_dir / "matched_count_summary.csv", index=False)
    eligibility.to_csv(output_dir / "eligibility_by_date.csv", index=False)
    payload = {
        "matched_count_daily_rows": len(daily),
        "matched_count_summary_rows": len(summary),
        "eligibility_dates": eligibility["date"].nunique(),
        "mean_v1_eligible_count": eligibility["healthy_v1_eligible_count"].mean(),
        "min_v1_eligible_count": int(eligibility["healthy_v1_eligible_count"].min()),
        "max_v1_eligible_count": int(eligibility["healthy_v1_eligible_count"].max()),
        "automatic_strategy_change": False,
        "promotion_evidence_allowed": False,
    }
    (output_dir / "matched_count_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        if len(summary) != len(TOP_SIZES) * len(HORIZONS):
            raise RuntimeError("matched-count summary is incomplete")
        if eligibility["date"].nunique() < 45:
            raise RuntimeError("eligibility history is incomplete")
        if summary["paired_dates"].min() < 45:
            raise RuntimeError("matched-count comparison has insufficient dates")
        if not summary["mean_matched_count"].between(1, 100).all():
            raise RuntimeError("invalid matched count")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
