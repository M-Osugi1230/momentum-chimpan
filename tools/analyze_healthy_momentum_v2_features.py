#!/usr/bin/env python3
"""Diagnose balanced v2 features among Healthy Momentum v1 eligible rows.

The diagnostics divide each feature into quintiles and compare the bounded preference
zone against the outside zone. They are read-only and never optimize or change thresholds
automatically.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import healthy_momentum_v2
from tools import backtest_healthy_momentum as v1_backtest


FEATURES = [
    "healthy_v2_recent_pace_ratio",
    "healthy_v2_market_relative_5d",
    "healthy_v2_sector_relative_5d",
    "healthy_v2_market_relative_5d_percentile",
    "healthy_v2_sector_relative_5d_percentile",
    "rank_change",
    "healthy_current_day_return",
    "healthy_drawdown_from_recent_high",
    "healthy_relative_strength_score",
    "healthy_v2_confirmation_score",
]


def report_percentile(frame: pd.DataFrame, column: str) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce")
    return values.groupby(frame["date"]).rank(method="average", pct=True)


def quintile(values: pd.Series) -> pd.Series:
    return np.ceil(values.clip(lower=1e-12, upper=1.0) * 5).astype("Int64")


def feature_bucket_diagnostics(
    frame: pd.DataFrame,
    horizons: list[int],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for feature in FEATURES:
        if feature not in frame.columns:
            continue
        percentile = report_percentile(frame, feature)
        buckets = quintile(percentile)
        for horizon in horizons:
            returns = pd.to_numeric(frame[f"forward_return_{horizon}"], errors="coerce")
            work = pd.DataFrame(
                {
                    "date": frame["date"],
                    "feature": feature,
                    "feature_value": pd.to_numeric(frame[feature], errors="coerce"),
                    "feature_percentile": percentile,
                    "quintile": buckets,
                    "forward_return": returns,
                }
            ).dropna(subset=["quintile", "forward_return"])
            for bucket, group in work.groupby("quintile", sort=True):
                date_means = group.groupby("date")["forward_return"].mean()
                records.append(
                    {
                        "feature": feature,
                        "horizon_reports": horizon,
                        "quintile": int(bucket),
                        "observations": int(len(group)),
                        "report_dates": int(group["date"].nunique()),
                        "mean_feature_value": float(group["feature_value"].mean()),
                        "mean_return": float(group["forward_return"].mean()),
                        "median_return": float(group["forward_return"].median()),
                        "win_rate": float(group["forward_return"].gt(0).mean()),
                        "mean_daily_return": float(date_means.mean()),
                    }
                )
    return pd.DataFrame(records)


def preference_zone_masks(frame: pd.DataFrame, policy: dict[str, Any]) -> dict[str, pd.Series]:
    components = policy["confirmation_components"]
    sources = {
        "RECENT_PACE_BALANCED": (
            "healthy_v2_recent_pace_ratio",
            components["recent_pace_ratio"],
        ),
        "MARKET_RELATIVE_5D_BALANCED": (
            "healthy_v2_market_relative_5d_percentile",
            components["market_relative_5d_percentile"],
        ),
        "SECTOR_RELATIVE_5D_BALANCED": (
            "healthy_v2_sector_relative_5d_percentile",
            components["sector_relative_5d_percentile"],
        ),
        "CURRENT_DAY_BALANCED": (
            "healthy_current_day_return",
            components["current_day_return"],
        ),
        "RECENT_HIGH_DISTANCE_BALANCED": (
            "healthy_drawdown_from_recent_high",
            components["drawdown_from_recent_high"],
        ),
        "LONG_RELATIVE_STRENGTH_BALANCED": (
            "healthy_relative_strength_score",
            components["long_relative_strength"],
        ),
    }
    masks: dict[str, pd.Series] = {}
    for label, (column, config) in sources.items():
        values = pd.to_numeric(frame[column], errors="coerce")
        masks[label] = values.between(
            float(config["low"]), float(config["high"]), inclusive="both"
        )
    return masks


def metric(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return {
        "observations": int(len(clean)),
        "mean_return": float(clean.mean()) if len(clean) else None,
        "median_return": float(clean.median()) if len(clean) else None,
        "win_rate": float(clean.gt(0).mean()) if len(clean) else None,
    }


def zone_ablation(
    frame: pd.DataFrame,
    policy: dict[str, Any],
    horizons: list[int],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    masks = preference_zone_masks(frame, policy)
    for zone, raw_mask in masks.items():
        mask = raw_mask.fillna(False)
        for horizon in horizons:
            returns = pd.to_numeric(frame[f"forward_return_{horizon}"], errors="coerce")
            inside = metric(returns[mask])
            outside = metric(returns[~mask])
            records.append(
                {
                    "preference_zone": zone,
                    "horizon_reports": horizon,
                    "inside_count": inside["observations"],
                    "outside_count": outside["observations"],
                    "inside_mean_return": inside["mean_return"],
                    "outside_mean_return": outside["mean_return"],
                    "inside_minus_outside_mean_return": (
                        inside["mean_return"] - outside["mean_return"]
                        if inside["mean_return"] is not None
                        and outside["mean_return"] is not None
                        else None
                    ),
                    "inside_win_rate": inside["win_rate"],
                    "outside_win_rate": outside["win_rate"],
                    "inside_minus_outside_win_rate": (
                        inside["win_rate"] - outside["win_rate"]
                        if inside["win_rate"] is not None
                        and outside["win_rate"] is not None
                        else None
                    ),
                    "inside_median_return": inside["median_return"],
                    "outside_median_return": outside["median_return"],
                }
            )
    return pd.DataFrame(records)


def zone_count_diagnostics(
    frame: pd.DataFrame,
    policy: dict[str, Any],
    horizons: list[int],
) -> pd.DataFrame:
    masks = preference_zone_masks(frame, policy)
    count = pd.DataFrame(masks).fillna(False).sum(axis=1)
    records: list[dict[str, Any]] = []
    for horizon in horizons:
        returns = pd.to_numeric(frame[f"forward_return_{horizon}"], errors="coerce")
        work = pd.DataFrame(
            {"balanced_zone_count": count, "forward_return": returns}
        ).dropna(subset=["forward_return"])
        for passed, group in work.groupby("balanced_zone_count", sort=True):
            records.append(
                {
                    "horizon_reports": horizon,
                    "balanced_zone_count": int(passed),
                    "observations": int(len(group)),
                    "mean_return": float(group["forward_return"].mean()),
                    "median_return": float(group["forward_return"].median()),
                    "win_rate": float(group["forward_return"].gt(0).mean()),
                }
            )
    return pd.DataFrame(records)


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
    canonical, _ = v1_backtest.canonical_reports(history)
    enriched = healthy_momentum_v2.attach(canonical, policy)
    horizons = [int(value) for value in policy["shadow_backtest"]["horizons"]]
    enriched, report_dates = v1_backtest.attach_future_returns(enriched, horizons)
    v1_rows = enriched[enriched["healthy_eligible"].fillna(False)].copy()

    buckets = feature_bucket_diagnostics(v1_rows, horizons)
    zones = zone_ablation(v1_rows, policy, horizons)
    zone_counts = zone_count_diagnostics(v1_rows, policy, horizons)
    row_columns = [
        "date",
        "code",
        "name",
        "sector33",
        "rank",
        "score",
        "healthy_rank",
        "healthy_selection_score",
        "healthy_v2_rank",
        "healthy_v2_selection_score",
        "healthy_v2_eligible",
        "healthy_v2_confirmation_state",
        "healthy_v2_exclusion_reasons",
        "healthy_v2_caution_reasons",
        *FEATURES,
        *[f"forward_return_{horizon}" for horizon in horizons],
    ]
    row_columns = [column for column in row_columns if column in v1_rows.columns]
    feature_rows = v1_rows[row_columns].copy()

    buckets.to_csv(output_dir / "feature_bucket_diagnostics.csv", index=False)
    zones.to_csv(output_dir / "preference_zone_ablation.csv", index=False)
    zone_counts.to_csv(output_dir / "preference_zone_count_diagnostics.csv", index=False)
    feature_rows.to_csv(output_dir / "historical_v1_feature_rows.csv", index=False)

    summary = {
        "mode": "SHADOW_ONLY",
        "research_only": True,
        "automatic_threshold_change": False,
        "automatic_promotion": False,
        "canonical_report_dates": len(report_dates),
        "v1_eligible_rows": int(len(v1_rows)),
        "features": FEATURES,
        "preference_zone_count": len(preference_zone_masks(v1_rows, policy)),
        "initial_hard_confirmation_candidate_rejected": True,
        "limitations": [
            "Diagnostics are based on short overlapping report history.",
            "Feature direction may reverse in future market regimes.",
            "No threshold is changed automatically from these diagnostics.",
            "The balanced candidate remains shadow-only even when historical results improve.",
        ],
    }
    (output_dir / "feature_diagnostics_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
