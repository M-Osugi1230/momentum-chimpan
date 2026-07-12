"""Complete-case aggregate guard for cross-fold volume-component evidence.

The underlying fold analyzer writes one summary and one equity file per fold.
This guard ensures the aggregate statistical test uses only sample-adequate
folds and only dates where every evaluable fold has both baseline and tested
daily returns. It preserves all fold summaries for reporting while preventing
incomplete folds or changing date composition from influencing inference.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pandas as pd

import replay
import volume_component_robustness as robustness

GUARD_VERSION = "2026-07-12-volume-component-complete-case-v1"


def _boolean_mask(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def collect_fold_inputs(fold_root: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    root = Path(fold_root)
    summaries: list[pd.DataFrame] = []
    equities: list[pd.DataFrame] = []
    for analysis_dir in sorted(root.glob("fold_*/analysis")):
        if not analysis_dir.is_dir():
            continue
        summary_path = analysis_dir / "volume_fold_summary.csv"
        equity_path = analysis_dir / "volume_fold_equity.csv"
        if not summary_path.exists() or not equity_path.exists():
            continue
        summary = pd.read_csv(summary_path, dtype={"fold_id": str})
        equity = pd.read_csv(equity_path, dtype={"fold_id": str})
        if len(summary) != 1:
            raise RuntimeError(f"{summary_path} must contain exactly one row")
        fold_id = str(summary.iloc[0]["fold_id"])
        if not fold_id:
            raise RuntimeError(f"{summary_path} has an empty fold_id")
        equity_ids = set(equity.get("fold_id", pd.Series(dtype=str)).dropna().astype(str))
        if equity_ids and equity_ids != {fold_id}:
            raise RuntimeError(f"{equity_path} contains unexpected fold ids: {sorted(equity_ids)}")
        summaries.append(summary)
        equities.append(equity)
    if not summaries or len(summaries) != len(equities):
        raise RuntimeError("complete fold summary/equity pairs were not found")
    return (
        pd.concat(summaries, ignore_index=True),
        pd.concat(equities, ignore_index=True),
    )


def _complete_case_dates(
    equities: pd.DataFrame,
    evaluable_fold_ids: list[str],
) -> tuple[list[str], int]:
    required = {"fold_id", "date", "variant", "period", "daily_return"}
    missing = sorted(required - set(equities.columns))
    if missing:
        raise ValueError(f"fold equity is missing columns: {missing}")

    full = equities[
        equities["fold_id"].astype(str).isin(evaluable_fold_ids)
        & equities["period"].astype(str).eq("full")
    ].copy()
    full["_parsed_date"] = pd.to_datetime(full["date"], errors="coerce")
    full["daily_return"] = pd.to_numeric(full["daily_return"], errors="coerce")
    full = full.dropna(subset=["_parsed_date", "daily_return"])
    full["date"] = full["_parsed_date"].dt.date.astype(str)

    paired = full.pivot_table(
        index=["fold_id", "date"],
        columns="variant",
        values="daily_return",
        aggfunc="last",
    ).reset_index()
    required_variants = [robustness.BASELINE_VARIANT, robustness.TEST_VARIANT]
    missing_variants = [variant for variant in required_variants if variant not in paired.columns]
    if missing_variants:
        raise RuntimeError(f"aggregate equity is missing variants: {missing_variants}")
    paired = paired.dropna(subset=required_variants)

    coverage = paired.groupby("date")["fold_id"].nunique()
    common_dates = sorted(
        coverage[coverage.eq(len(evaluable_fold_ids))].index.astype(str).tolist()
    )
    incomplete_date_count = int((coverage < len(evaluable_fold_ids)).sum())
    if len(common_dates) < 10:
        raise RuntimeError(
            f"only {len(common_dates)} complete-case dates are available across "
            f"{len(evaluable_fold_ids)} evaluable folds"
        )
    return common_dates, incomplete_date_count


def aggregate_guarded(
    fold_root: str,
    registry: dict[str, Any],
) -> dict[str, Any]:
    all_summaries, all_equities = collect_fold_inputs(fold_root)
    if "sample_adequate" not in all_summaries.columns:
        raise ValueError("fold summaries are missing sample_adequate")

    evaluable = all_summaries[_boolean_mask(all_summaries["sample_adequate"])].copy()
    evaluable_fold_ids = sorted(evaluable["fold_id"].astype(str).unique().tolist())
    if not evaluable_fold_ids:
        raise RuntimeError("no sample-adequate folds are available for aggregate inference")

    common_dates, incomplete_date_count = _complete_case_dates(
        all_equities,
        evaluable_fold_ids,
    )
    common_date_set = set(common_dates)

    with TemporaryDirectory() as temporary:
        filtered_root = Path(temporary) / "folds"
        for fold_id in evaluable_fold_ids:
            analysis = filtered_root / fold_id / "analysis"
            analysis.mkdir(parents=True, exist_ok=True)
            summary = evaluable[evaluable["fold_id"].astype(str).eq(fold_id)].copy()
            equity = all_equities[
                all_equities["fold_id"].astype(str).eq(fold_id)
                & all_equities["period"].astype(str).eq("full")
            ].copy()
            equity["_parsed_date"] = pd.to_datetime(equity["date"], errors="coerce")
            equity = equity.dropna(subset=["_parsed_date"])
            equity["date"] = equity["_parsed_date"].dt.date.astype(str)
            equity = equity[equity["date"].isin(common_date_set)].drop(
                columns=["_parsed_date"], errors="ignore"
            )
            summary.to_csv(analysis / "volume_fold_summary.csv", index=False)
            equity.to_csv(analysis / "volume_fold_equity.csv", index=False)

        results = robustness.aggregate_folds(str(filtered_root), registry)

    aggregate_fold_ids = "|".join(evaluable_fold_ids)
    aggregate_summary = results["aggregate_summary"].copy()
    aggregate_summary.loc[:, "fold_count"] = int(len(all_summaries))
    aggregate_summary.loc[:, "evaluable_fold_count"] = int(len(evaluable_fold_ids))
    aggregate_summary.loc[:, "complete_case_date_count"] = int(len(common_dates))
    aggregate_summary.loc[:, "excluded_incomplete_date_count"] = int(incomplete_date_count)
    aggregate_summary.loc[:, "aggregate_fold_ids"] = aggregate_fold_ids

    manifest = dict(results["manifest"])
    manifest.update({
        "aggregate_guard_version": GUARD_VERSION,
        "fold_count": int(len(all_summaries)),
        "evaluable_fold_count": int(len(evaluable_fold_ids)),
        "aggregate_fold_ids": aggregate_fold_ids,
        "aggregate_fold_id_count": int(len(evaluable_fold_ids)),
        "complete_case_date_count": int(len(common_dates)),
        "excluded_incomplete_date_count": int(incomplete_date_count),
        "aggregate_uses_sample_adequate_folds_only": True,
        "aggregate_requires_complete_fold_date_coverage": True,
    })

    results["fold_summary"] = all_summaries.sort_values("fold_id").reset_index(drop=True)
    results["aggregate_summary"] = aggregate_summary
    results["manifest"] = manifest
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guarded complete-case aggregation for volume-component folds"
    )
    parser.add_argument("--fold-root", required=True)
    parser.add_argument("--registry", default=robustness.DEFAULT_REGISTRY)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before = replay.live_state_hashes()
    registry = robustness.load_registry(args.registry)
    results = aggregate_guarded(args.fold_root, registry)
    output = robustness.write_aggregate_outputs(results, args.output_dir)

    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    output["manifest"]["production_state_mutations"] = mutations
    Path(output["paths"]["manifest"]).write_text(
        json.dumps(output["manifest"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.strict:
        if mutations:
            raise RuntimeError(f"production state mutated: {mutations}")
        summary = results["aggregate_summary"]
        if summary.empty:
            raise RuntimeError("aggregate robustness summary is empty")
        if not bool(output["manifest"]["aggregate_uses_sample_adequate_folds_only"]):
            raise RuntimeError("aggregate included sample-inadequate folds")
        if not bool(output["manifest"]["aggregate_requires_complete_fold_date_coverage"]):
            raise RuntimeError("aggregate did not enforce complete fold/date coverage")

    print(results["aggregate_summary"].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
