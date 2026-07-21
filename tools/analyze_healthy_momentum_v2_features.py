#!/usr/bin/env python3
"""Diagnose each v2 confirmation feature among Healthy Momentum v1 eligible rows.

This script is read-only and diagnostic. It does not optimize thresholds automatically and
never mutates production, v1, Daily Action List, or paper state.
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


def gate_masks(frame: pd.DataFrame, policy: dict[str, Any]) -> dict[str, pd.Series]:
    cfg = policy["confirmation_eligibility"]
    number = lambda column: pd.to_numeric(frame[column], errors="coerce")
    return_60d = number("return_60d")
    pace = number("healthy_v2_recent_pace_ratio")
    market_relative = number("healthy_market_relative_20d")
    sector_relative = number("healthy_sector_relative_20d")
    rank_change = number("rank_change")
    day_return = number("healthy_current_day_return")
    drawdown = number("healthy_drawdown_from_recent_high")
    return {
        "SIXTY_DAY_RISING": return_60d.gt(float(cfg["min_return_60d"])),
        "PACE_NOT_STALLING": pace.ge(float(cfg["min_recent_pace_ratio"])),
        "PACE_NOT_SPIKE": pace.le(float(cfg["max_recent_pace_ratio"])),
        "MARKET_RELATIVE_20D": market_relative.ge(float(cfg["min_market_relative_20d"])),
        "SECTOR_RELATIVE_20D": sector_relative.ge(float(cfg["min_sector_relative_20d"])),
        "RANK_NOT_DETERIORATING": rank_change.isna()
        | rank_change.ge(float(cfg["min_rank_change"])),
        "CURRENT_DAY_CONFIRMATION": day_return.ge(float(cfg["min_current_day_return"])),
        "RECENT_HIGH_CONFIRMATION": drawdown.ge(
            float(cfg["min_drawdown_from_recent_high"])
        ),
    }


def metric(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return {
        "observations": int(len(clean)),
        "mean_return": float(clean.mean()) if len(clean) else None,
        "median_return": float(clean.median()) if len(clean) else None,
        "win_rate": float(clean.gt(0).mean()) if len(clean) else None,
    }


def gate_ablation(
    frame: pd.DataFrame,
    policy: dict[str, Any],
    horizons: list[int],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    masks = gate_masks(frame, policy)
    for gate, raw_mask in masks.items():
        mask = raw_mask.fillna(False)
        for horizon in horizons:
            returns = pd.to_numeric(frame[f"forward_return_{horizon}"], errors="coerce")
            passed = metric(returns[mask])
            failed = metric(returns[~mask])
            records.append(
                {
                    "gate": gate,
                    "horizon_reports": horizon,
                    "pass_count": passed["observations"],
                    "fail_count": failed["observations"],
                    "pass_mean_return": passed["mean_return"],
                    "fail_mean_return": failed["mean_return"],
                    "pass_minus_fail_mean_return": (
                        passed["mean_return"] - failed["mean_return"]
                        if passed["mean_return"] is not None
                        and failed["mean_return"] is not None
                        else None
                    ),
                    "pass_win_rate": passed["win_rate"],
                    "fail_win_rate": failed["win_rate"],
                    "pass_minus_fail_win_rate": (
                        passed["win_rate"] - failed["win_rate"]
                        if passed["win_rate"] is not None
                        and failed["win_rate"] is not None
                        else None
                    ),
                    "pass_median_return": passed["median_return"],
                    "fail_median_return": failed["median_return"],
                }
            )
    return pd.DataFrame(records)


def all_gate_count_diagnostics(
    frame: pd.DataFrame,
    policy: dict[str, Any],
    horizons: list[int],
) -> pd.DataFrame:
    masks = gate_masks(frame, policy)
    count = pd.DataFrame(masks).fillna(False).sum(axis=1)
    records: list[dict[str, Any]] = []
    for horizon in horizons:
        returns = pd.to_numeric(frame[f"forward_return_{horizon}"], errors="coerce")
        work = pd.DataFrame(
            {"passed_gate_count": count, "forward_return": returns}
        ).dropna(subset=["forward_return"])
        for passed, group in work.groupby("passed_gate_count", sort=True):
            records.append(
                {
                    "horizon_reports": horizon,
                    "passed_gate_count": int(passed),
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
    gates = gate_ablation(v1_rows, policy, horizons)
    gate_counts = all_gate_count_diagnostics(v1_rows, policy, horizons)
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
        *FEATURES,
        *[f"forward_return_{horizon}" for horizon in horizons],
    ]
    row_columns = [column for column in row_columns if column in v1_rows.columns]
    feature_rows = v1_rows[row_columns].copy()

    buckets.to_csv(output_dir / "feature_bucket_diagnostics.csv", index=False)
    gates.to_csv(output_dir / "gate_ablation.csv", index=False)
    gate_counts.to_csv(output_dir / "gate_count_diagnostics.csv", index=False)
    feature_rows.to_csv(output_dir / "historical_v1_feature_rows.csv", index=False)

    summary = {
        "mode": "SHADOW_ONLY",
        "research_only": True,
        "automatic_threshold_change": False,
        "automatic_promotion": False,
        "canonical_report_dates": len(report_dates),
        "v1_eligible_rows": int(len(v1_rows)),
        "features": FEATURES,
        "gate_count": len(gate_masks(v1_rows, policy)),
        "limitations": [
            "Diagnostics are based on short overlapping report history.",
            "Feature direction may reverse in future market regimes.",
            "No threshold is changed automatically from these diagnostics.",
        ],
    }
    (output_dir / "feature_diagnostics_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
