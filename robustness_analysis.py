"""Robustness analysis for benchmarked walk-forward replay outcomes.

This module is research-only. It stress-tests replay evidence for transaction
costs, subperiod stability, cluster concentration, and multiple testing. It does
not change production thresholds, live state, or paper positions.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import main
import replay
import research_scorecard

ROBUSTNESS_VERSION = "2026-07-11-replay-robustness-v1"
DEFAULT_INPUT = "output/replay/replay_benchmarked_outcomes.csv"
DEFAULT_OUTPUT_DIR = "output/replay"
DEFAULT_COST_SCENARIOS_BPS = (0, 10, 30, 50)

GROUPINGS: tuple[tuple[str, str | None], ...] = (
    ("overall", None),
    ("priority", "sector_research_priority"),
    ("grade", "sector_leader_grade"),
    ("rotation", "sector_rotation"),
)


def load_benchmarked_outcomes(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"benchmarked outcomes not found: {path}")
    frame = pd.read_csv(target, dtype={"code": str})
    required = {
        "signal_date", "entry_price_date", "exit_price_date", "code", "sector33",
        "horizon_days", "forward_return", "excess_vs_universe",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"benchmarked outcomes missing columns: {missing}")
    frame["code"] = frame["code"].map(main.normalize_code)
    frame["horizon_days"] = pd.to_numeric(frame["horizon_days"], errors="coerce")
    frame["forward_return"] = pd.to_numeric(frame["forward_return"], errors="coerce")
    frame["excess_vs_universe"] = pd.to_numeric(frame["excess_vs_universe"], errors="coerce")
    frame = frame.dropna(subset=["horizon_days", "forward_return", "excess_vs_universe"]).copy()
    frame["horizon_days"] = frame["horizon_days"].astype(int)
    for column in ("signal_date", "entry_price_date", "exit_price_date"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    frame = frame.dropna(subset=["signal_date", "entry_price_date", "exit_price_date"])
    return frame.sort_values(["signal_date", "horizon_days", "code"]).reset_index(drop=True)


def group_subsets(frame: pd.DataFrame):
    for horizon, horizon_rows in frame.groupby("horizon_days"):
        yield "overall", "all", int(horizon), horizon_rows
        for group_type, column in GROUPINGS[1:]:
            if column not in horizon_rows.columns:
                continue
            for value, subset in horizon_rows.groupby(column, dropna=False):
                yield group_type, str(value), int(horizon), subset


def sign_flip_p_value(values: pd.Series, samples: int = 5000, seed: int = 42) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(clean) < 5:
        return None
    observed = float(clean.mean())
    generator = np.random.default_rng(seed)
    signs = generator.choice(np.array([-1.0, 1.0]), size=(samples, len(clean)))
    permuted = (signs * clean).mean(axis=1)
    return float((np.count_nonzero(permuted >= observed) + 1) / (samples + 1))


def benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(p_values, errors="coerce")
    valid = numeric.dropna()
    result = pd.Series(np.nan, index=p_values.index, dtype=float)
    if valid.empty:
        return result
    order = valid.sort_values().index
    m = len(order)
    ranked = valid.loc[order].to_numpy(dtype=float)
    adjusted = ranked * m / np.arange(1, m + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    adjusted = np.clip(adjusted, 0.0, 1.0)
    result.loc[order] = adjusted
    return result


def build_cost_sensitivity(
    frame: pd.DataFrame,
    cost_scenarios_bps: tuple[int, ...] = DEFAULT_COST_SCENARIOS_BPS,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for group_type, group_value, horizon, subset in group_subsets(frame):
        gross_return = pd.to_numeric(subset["forward_return"], errors="coerce").dropna()
        gross_excess = pd.to_numeric(subset["excess_vs_universe"], errors="coerce").dropna()
        common = subset.loc[gross_excess.index]
        for cost_bps in cost_scenarios_bps:
            cost = cost_bps / 10_000
            net_return = pd.to_numeric(common["forward_return"], errors="coerce") - cost
            net_excess = pd.to_numeric(common["excess_vs_universe"], errors="coerce") - cost
            rows.append({
                "group_type": group_type,
                "group_value": group_value,
                "horizon_days": horizon,
                "round_trip_cost_bps": cost_bps,
                "count": int(len(net_excess)),
                "gross_average_return": float(gross_return.mean()) if len(gross_return) else None,
                "gross_average_excess": float(gross_excess.mean()) if len(gross_excess) else None,
                "net_average_return": float(net_return.mean()) if len(net_return) else None,
                "net_median_return": float(net_return.median()) if len(net_return) else None,
                "net_average_excess": float(net_excess.mean()) if len(net_excess) else None,
                "net_median_excess": float(net_excess.median()) if len(net_excess) else None,
                "net_positive_rate": float((net_return > 0).mean()) if len(net_return) else None,
                "net_beat_universe_rate": float((net_excess > 0).mean()) if len(net_excess) else None,
            })
    return pd.DataFrame(rows)


def build_subperiod_stability(frame: pd.DataFrame, base_cost_bps: int = 30) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cost = base_cost_bps / 10_000
    for group_type, group_value, horizon, subset in group_subsets(frame):
        dates = sorted(subset["signal_date"].dropna().unique())
        if not dates:
            continue
        midpoint = max(len(dates) // 2, 1)
        early_dates = set(dates[:midpoint])
        late_dates = set(dates[midpoint:])
        for period, selected_dates in (("early", early_dates), ("late", late_dates)):
            part = subset[subset["signal_date"].isin(selected_dates)] if selected_dates else subset.iloc[0:0]
            excess = pd.to_numeric(part["excess_vs_universe"], errors="coerce").dropna() - cost
            rows.append({
                "group_type": group_type,
                "group_value": group_value,
                "horizon_days": horizon,
                "period": period,
                "round_trip_cost_bps": base_cost_bps,
                "first_signal_date": part["signal_date"].min().date().isoformat() if not part.empty else "",
                "last_signal_date": part["signal_date"].max().date().isoformat() if not part.empty else "",
                "count": int(len(excess)),
                "net_average_excess": float(excess.mean()) if len(excess) else None,
                "net_beat_rate": float((excess > 0).mean()) if len(excess) else None,
            })
    return pd.DataFrame(rows)


def _leave_one_cluster_metrics(subset: pd.DataFrame, cluster_column: str, cost: float) -> dict[str, Any]:
    clusters = [value for value in subset[cluster_column].dropna().unique().tolist()]
    means: list[float] = []
    for cluster in clusters:
        remaining = subset[subset[cluster_column] != cluster]
        excess = pd.to_numeric(remaining["excess_vs_universe"], errors="coerce").dropna() - cost
        if len(excess):
            means.append(float(excess.mean()))
    return {
        "cluster_count": len(clusters),
        "exclusion_count": len(means),
        "worst_excluded_mean_excess": min(means) if means else None,
        "median_excluded_mean_excess": float(np.median(means)) if means else None,
        "positive_exclusion_rate": float(np.mean(np.array(means) > 0)) if means else None,
    }


def build_cluster_robustness(frame: pd.DataFrame, base_cost_bps: int = 30) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cost = base_cost_bps / 10_000
    cluster_specs = (("sector", "sector33"), ("signal_date", "signal_date"), ("code", "code"))
    for group_type, group_value, horizon, subset in group_subsets(frame):
        for cluster_type, column in cluster_specs:
            metrics = _leave_one_cluster_metrics(subset, column, cost)
            rows.append({
                "group_type": group_type,
                "group_value": group_value,
                "horizon_days": horizon,
                "cluster_type": cluster_type,
                "round_trip_cost_bps": base_cost_bps,
                **metrics,
            })
    return pd.DataFrame(rows)


def build_statistical_tests(frame: pd.DataFrame, base_cost_bps: int = 30) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cost = base_cost_bps / 10_000
    for index, (group_type, group_value, horizon, subset) in enumerate(group_subsets(frame)):
        excess = pd.to_numeric(subset["excess_vs_universe"], errors="coerce").dropna() - cost
        ci_low, ci_high = research_scorecard.bootstrap_mean_ci(
            excess, samples=2000, seed=1000 + index
        )
        p_value = sign_flip_p_value(excess, samples=5000, seed=2000 + index)
        rows.append({
            "group_type": group_type,
            "group_value": group_value,
            "horizon_days": horizon,
            "round_trip_cost_bps": base_cost_bps,
            "count": int(len(excess)),
            "net_average_excess": float(excess.mean()) if len(excess) else None,
            "net_excess_ci_low_95": ci_low,
            "net_excess_ci_high_95": ci_high,
            "one_sided_sign_flip_p_value": p_value,
        })
    result = pd.DataFrame(rows)
    if not result.empty:
        result["fdr_q_value"] = benjamini_hochberg(result["one_sided_sign_flip_p_value"])
    return result


def robustness_status(
    count: int,
    mean_excess: float | None,
    q_value: float | None,
    early_excess: float | None,
    late_excess: float | None,
    worst_sector_excess: float | None,
    beat_rate: float | None,
) -> str:
    if count < 30:
        return "INSUFFICIENT"
    numeric = [mean_excess, early_excess, late_excess, worst_sector_excess]
    if any(value is None or pd.isna(value) or float(value) <= 0 for value in numeric):
        return "FRAGILE"
    if q_value is None or pd.isna(q_value) or float(q_value) > 0.10:
        return "DEVELOPING"
    if count >= 100 and float(q_value) <= 0.05 and beat_rate is not None and float(beat_rate) >= 0.55:
        return "ROBUST"
    if count >= 50:
        return "PROMISING"
    return "DEVELOPING"


def build_robustness_summary(
    cost_sensitivity: pd.DataFrame,
    subperiod: pd.DataFrame,
    clusters: pd.DataFrame,
    tests: pd.DataFrame,
    base_cost_bps: int = 30,
) -> pd.DataFrame:
    if tests.empty:
        return pd.DataFrame()
    base_cost = cost_sensitivity[cost_sensitivity["round_trip_cost_bps"] == base_cost_bps]
    records: list[dict[str, Any]] = []
    for _, test in tests.iterrows():
        key = (
            (base_cost["group_type"] == test["group_type"])
            & (base_cost["group_value"] == test["group_value"])
            & (base_cost["horizon_days"] == test["horizon_days"])
        )
        cost_row = base_cost[key]
        period_rows = subperiod[
            (subperiod["group_type"] == test["group_type"])
            & (subperiod["group_value"] == test["group_value"])
            & (subperiod["horizon_days"] == test["horizon_days"])
        ]
        cluster_row = clusters[
            (clusters["group_type"] == test["group_type"])
            & (clusters["group_value"] == test["group_value"])
            & (clusters["horizon_days"] == test["horizon_days"])
            & (clusters["cluster_type"] == "sector")
        ]
        early = period_rows[period_rows["period"] == "early"]
        late = period_rows[period_rows["period"] == "late"]
        mean_excess = float(test["net_average_excess"]) if pd.notna(test["net_average_excess"]) else None
        q_value = float(test["fdr_q_value"]) if pd.notna(test["fdr_q_value"]) else None
        early_excess = float(early.iloc[0]["net_average_excess"]) if not early.empty and pd.notna(early.iloc[0]["net_average_excess"]) else None
        late_excess = float(late.iloc[0]["net_average_excess"]) if not late.empty and pd.notna(late.iloc[0]["net_average_excess"]) else None
        worst_sector = float(cluster_row.iloc[0]["worst_excluded_mean_excess"]) if not cluster_row.empty and pd.notna(cluster_row.iloc[0]["worst_excluded_mean_excess"]) else None
        beat_rate = float(cost_row.iloc[0]["net_beat_universe_rate"]) if not cost_row.empty and pd.notna(cost_row.iloc[0]["net_beat_universe_rate"]) else None
        status = robustness_status(
            int(test["count"]), mean_excess, q_value, early_excess, late_excess, worst_sector, beat_rate
        )
        records.append({
            "group_type": test["group_type"],
            "group_value": test["group_value"],
            "horizon_days": int(test["horizon_days"]),
            "round_trip_cost_bps": base_cost_bps,
            "count": int(test["count"]),
            "net_average_excess": mean_excess,
            "net_beat_universe_rate": beat_rate,
            "net_excess_ci_low_95": test["net_excess_ci_low_95"],
            "net_excess_ci_high_95": test["net_excess_ci_high_95"],
            "fdr_q_value": q_value,
            "early_net_average_excess": early_excess,
            "late_net_average_excess": late_excess,
            "worst_leave_one_sector_excess": worst_sector,
            "robustness_status": status,
        })
    return pd.DataFrame(records)


def methodology() -> pd.DataFrame:
    return pd.DataFrame([
        {"item": "Transaction costs", "detail": "Conservative round-trip cost is subtracted from signal return and excess; benchmark remains gross."},
        {"item": "Base cost", "detail": "30 bps round trip; sensitivity is shown at 0, 10, 30, and 50 bps."},
        {"item": "Subperiods", "detail": "Unique signal dates are split chronologically into early and late halves."},
        {"item": "Cluster robustness", "detail": "Mean net excess is recomputed after excluding each sector, signal date, and code."},
        {"item": "Permutation test", "detail": "Deterministic one-sided sign-flip test for mean net excess above zero."},
        {"item": "Multiple testing", "detail": "Benjamini-Hochberg false-discovery-rate q-values across reported groups."},
        {"item": "Production use", "detail": "Research only; results never change production thresholds or positions automatically."},
    ])


def write_outputs(
    cost_sensitivity: pd.DataFrame,
    subperiod: pd.DataFrame,
    clusters: pd.DataFrame,
    tests: pd.DataFrame,
    summary: pd.DataFrame,
    output_dir: str,
    source_hash: str,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "excel": target / "replay_robustness_analysis.xlsx",
        "summary": target / "replay_robustness_summary.csv",
        "cost": target / "replay_cost_sensitivity.csv",
        "subperiod": target / "replay_subperiod_stability.csv",
        "clusters": target / "replay_cluster_robustness.csv",
        "tests": target / "replay_statistical_tests.csv",
        "manifest": target / "replay_robustness_manifest.json",
    }
    summary.to_csv(paths["summary"], index=False)
    cost_sensitivity.to_csv(paths["cost"], index=False)
    subperiod.to_csv(paths["subperiod"], index=False)
    clusters.to_csv(paths["clusters"], index=False)
    tests.to_csv(paths["tests"], index=False)
    overall = summary[summary["group_type"] == "overall"] if not summary.empty else pd.DataFrame()
    manifest = {
        "robustness_version": ROBUSTNESS_VERSION,
        "scorecard_version": research_scorecard.SCORECARD_VERSION,
        "replay_version": replay.REPLAY_VERSION,
        "production_app_version": main.APP_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_benchmarked_outcomes_sha256": source_hash,
        "summary_row_count": len(summary),
        "overall_robust_count": int((overall.get("robustness_status", pd.Series(dtype=str)) == "ROBUST").sum()) if not overall.empty else 0,
        "overall_promising_count": int((overall.get("robustness_status", pd.Series(dtype=str)) == "PROMISING").sum()) if not overall.empty else 0,
        "research_only": True,
        "automatic_threshold_changes": False,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Robustness Summary", index=False)
        summary.to_excel(writer, sheet_name="Decision Table", index=False)
        cost_sensitivity.to_excel(writer, sheet_name="Cost Sensitivity", index=False)
        subperiod.to_excel(writer, sheet_name="Subperiod Stability", index=False)
        clusters.to_excel(writer, sheet_name="Cluster Robustness", index=False)
        tests.to_excel(writer, sheet_name="Statistical Tests", index=False)
        methodology().to_excel(writer, sheet_name="Methodology", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"paths": {key: str(value) for key, value in paths.items()}, "manifest": manifest}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress-test benchmarked replay evidence")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--base-cost-bps", type=int, default=30)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    frame = load_benchmarked_outcomes(args.input)
    cost = build_cost_sensitivity(frame)
    subperiod = build_subperiod_stability(frame, args.base_cost_bps)
    clusters = build_cluster_robustness(frame, args.base_cost_bps)
    tests = build_statistical_tests(frame, args.base_cost_bps)
    summary = build_robustness_summary(cost, subperiod, clusters, tests, args.base_cost_bps)
    result = write_outputs(
        cost, subperiod, clusters, tests, summary, args.output_dir, replay.sha256_file(args.input)
    )
    if args.strict and not frame.empty:
        invalid_dates = int((frame["exit_price_date"] <= frame["entry_price_date"]).sum())
        if invalid_dates:
            raise RuntimeError(f"invalid date ordering: {invalid_dates}")
        expected_costs = set(DEFAULT_COST_SCENARIOS_BPS)
        actual_costs = set(pd.to_numeric(cost["round_trip_cost_bps"], errors="coerce").dropna().astype(int))
        if not expected_costs.issubset(actual_costs):
            raise RuntimeError(f"missing cost scenarios: {sorted(expected_costs - actual_costs)}")
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
