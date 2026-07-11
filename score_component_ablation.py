"""Distribution-preserving ablation of Momentum score components.

Each leave-one-component-out variant keeps the exact daily score multiset and
all downstream thresholds unchanged. Only the ordering of stocks is changed by
removing one component from the sorting signal, after which the original daily
score distribution is reassigned by the alternative rank. This isolates the
component's ranking contribution without silently changing candidate counts,
Action Priority thresholds, market regime logic, execution assumptions, or
production state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import main
import portfolio_exit_lab as exit_lab
import portfolio_regime_attribution as attribution
import portfolio_research as portfolio
import relative_strength_lifecycle as rs_lifecycle
import replay

ABLATION_VERSION = "2026-07-11-score-component-ablation-v1"
DEFAULT_HISTORY = "output/ablation/historical_ranking.csv"
DEFAULT_PRICES = "output/ablation/historical_price_panel.csv"
DEFAULT_PROVENANCE = "output/ablation/replay/evidence_provenance.json"
DEFAULT_OUTPUT_DIR = "output/ablation/report"

COMPONENTS: dict[str, dict[str, Any]] = {
    "ytd_high": {"column": "score_ytd_high", "maximum_points": 30, "label": "年初来高値更新"},
    "ytd_streak": {"column": "score_ytd_streak", "maximum_points": 20, "label": "年初来高値連続更新"},
    "return_20d": {"column": "score_return_20d", "maximum_points": 20, "label": "20日騰落率"},
    "volume_ratio": {"column": "score_volume_ratio", "maximum_points": 15, "label": "出来高倍率"},
    "moving_average": {"column": "score_ma", "maximum_points": 10, "label": "20日・60日移動平均"},
    "trading_value": {"column": "score_trading_value", "maximum_points": 5, "label": "売買代金"},
}
VARIANTS = ("baseline",) + tuple(f"drop_{name}" for name in COMPONENTS)
BASELINE_SCENARIO = portfolio.PortfolioScenario("score_ablation_baseline", None, 0.0, 0.01)
BASELINE_EXIT_POLICY = portfolio.ExitPolicy("baseline", 0.08, 0.15, 0.10, 20, True)
MIN_FULL_TRADES = 30
MIN_SUBPERIOD_TRADES = 8
BOOTSTRAP_ITERATIONS = 2000
BLOCK_LENGTH = 5
MAXIMUM_FDR_Q = 0.05


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_history(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(target, dtype={"code": str})
    required = {"date", "rank", "code", "score", *[item["column"] for item in COMPONENTS.values()]}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"historical ranking missing score components: {missing}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date.astype(str)
    frame["code"] = frame["code"].map(main.normalize_code)
    numeric = ["rank", "score", *[item["column"] for item in COMPONENTS.values()]]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "rank", "code", "score"])
    if frame[list(item["column"] for item in COMPONENTS.values())].isna().any().any():
        raise ValueError("historical ranking contains missing score component values")
    component_sum = frame[[item["column"] for item in COMPONENTS.values()]].sum(axis=1).clip(upper=100)
    if not np.allclose(component_sum.to_numpy(dtype=float), frame["score"].to_numpy(dtype=float), atol=1e-9):
        raise ValueError("stored score does not equal the governed component sum")
    return frame.sort_values(["date", "rank", "code"]).drop_duplicates(["date", "code"], keep="last").reset_index(drop=True)


def variant_component(variant: str) -> str | None:
    if variant == "baseline":
        return None
    if not variant.startswith("drop_"):
        raise ValueError(f"unknown ablation variant: {variant}")
    component = variant.removeprefix("drop_")
    if component not in COMPONENTS:
        raise ValueError(f"unknown score component: {component}")
    return component


def build_variant_history(
    history: pd.DataFrame,
    variant: str,
    top_limit: int = 100,
) -> pd.DataFrame:
    component = variant_component(variant)
    prior = pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for report_date in sorted(history["date"].astype(str).unique()):
        day = history[history["date"].astype(str) == report_date].copy()
        original_scores = pd.to_numeric(day["score"], errors="coerce").sort_values(ascending=False).to_numpy(dtype=float)
        if component is None:
            day["ablation_raw_score"] = pd.to_numeric(day["score"], errors="coerce")
        else:
            column = str(COMPONENTS[component]["column"])
            day["ablation_raw_score"] = (
                pd.to_numeric(day["score"], errors="coerce")
                - pd.to_numeric(day[column], errors="coerce").fillna(0.0)
            )
        day["_original_score"] = pd.to_numeric(day["score"], errors="coerce")
        day["_original_rank"] = pd.to_numeric(day["rank"], errors="coerce")
        day = day.sort_values(
            ["ablation_raw_score", "_original_score", "_original_rank", "code"],
            ascending=[False, False, True, True],
        ).reset_index(drop=True)
        day["score"] = original_scores
        day["ablation_variant"] = variant
        day["ablation_removed_component"] = component or ""
        day["ablation_removed_points"] = (
            0.0 if component is None else pd.to_numeric(day[str(COMPONENTS[component]["column"])], errors="coerce").fillna(0.0)
        )
        day = day.drop(columns=["date", "rank"], errors="ignore")
        ranked = main.enrich_ranking_features(day, prior, report_date, top_limit)
        ranked = main.attach_relative_strength(ranked)
        ranked = rs_lifecycle.attach(ranked, prior, report_date)
        frames.append(ranked)
        prior = pd.concat([prior, ranked], ignore_index=True)
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not result.empty:
        result = result.drop(columns=["_original_score", "_original_rank"], errors="ignore")
    return result


def validate_distribution_preservation(baseline: pd.DataFrame, variant: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for report_date in sorted(set(baseline["date"].astype(str)) | set(variant["date"].astype(str))):
        base_day = baseline[baseline["date"].astype(str) == report_date]
        variant_day = variant[variant["date"].astype(str) == report_date]
        base_scores = sorted(pd.to_numeric(base_day["score"], errors="coerce").dropna().tolist(), reverse=True)
        variant_scores = sorted(pd.to_numeric(variant_day["score"], errors="coerce").dropna().tolist(), reverse=True)
        rows.append({
            "date": report_date,
            "baseline_rows": len(base_day),
            "variant_rows": len(variant_day),
            "score_multiset_equal": base_scores == variant_scores,
            "baseline_top100_count": int((pd.to_numeric(base_day["rank"], errors="coerce") <= 100).sum()),
            "variant_top100_count": int((pd.to_numeric(variant_day["rank"], errors="coerce") <= 100).sum()),
        })
    return pd.DataFrame(rows)


def rank_diagnostics(baseline: pd.DataFrame, variant: pd.DataFrame) -> dict[str, Any]:
    merged = baseline[["date", "code", "rank"]].merge(
        variant[["date", "code", "rank"]], on=["date", "code"], how="inner", suffixes=("_baseline", "_variant")
    )
    daily_corr: list[float] = []
    top30_overlap: list[float] = []
    top100_overlap: list[float] = []
    for _, group in merged.groupby("date"):
        baseline_ranks = pd.to_numeric(group["rank_baseline"], errors="coerce").rank(method="average")
        variant_ranks = pd.to_numeric(group["rank_variant"], errors="coerce").rank(method="average")
        corr = baseline_ranks.corr(variant_ranks, method="pearson")
        if pd.notna(corr):
            daily_corr.append(float(corr))
        for limit, target in ((30, top30_overlap), (100, top100_overlap)):
            baseline_codes = set(group.loc[group["rank_baseline"] <= limit, "code"])
            variant_codes = set(group.loc[group["rank_variant"] <= limit, "code"])
            union = baseline_codes | variant_codes
            target.append(len(baseline_codes & variant_codes) / len(union) if union else 1.0)
    return {
        "mean_daily_rank_spearman": float(np.mean(daily_corr)) if daily_corr else None,
        "minimum_daily_rank_spearman": float(np.min(daily_corr)) if daily_corr else None,
        "mean_top30_jaccard": float(np.mean(top30_overlap)) if top30_overlap else None,
        "mean_top100_jaccard": float(np.mean(top100_overlap)) if top100_overlap else None,
    }


def paired_sign_flip(
    baseline_equity: pd.DataFrame,
    variant_equity: pd.DataFrame,
    block_length: int = BLOCK_LENGTH,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = 20260711,
) -> dict[str, float | None]:
    baseline = baseline_equity[["date", "daily_return"]].rename(columns={"daily_return": "baseline"})
    variant = variant_equity[["date", "daily_return"]].rename(columns={"daily_return": "variant"})
    merged = baseline.merge(variant, on="date", how="inner")
    differences = (
        pd.to_numeric(merged["variant"], errors="coerce").fillna(0.0)
        - pd.to_numeric(merged["baseline"], errors="coerce").fillna(0.0)
    ).to_numpy(dtype=float)
    if len(differences) < max(10, block_length * 2):
        return {
            "mean_daily_difference": None,
            "ci_low": None,
            "ci_high": None,
            "two_sided_p_value": None,
            "improvement_p_value": None,
            "harm_p_value": None,
        }
    observed = float(differences.mean())
    block_length = max(1, min(int(block_length), len(differences)))
    blocks = [differences[index : index + block_length] for index in range(0, len(differences), block_length)]
    block_means = np.array([float(block.mean()) for block in blocks], dtype=float)
    rng = np.random.default_rng(seed)
    null_means = np.empty(iterations, dtype=float)
    for iteration in range(iterations):
        signs = rng.choice(np.array([-1.0, 1.0]), size=len(block_means))
        null_means[iteration] = float(np.mean(block_means * signs))
    improvement_p_value = float((1 + np.sum(null_means >= observed)) / (iterations + 1))
    harm_p_value = float((1 + np.sum(null_means <= observed)) / (iterations + 1))
    two_sided_p_value = float((1 + np.sum(np.abs(null_means) >= abs(observed))) / (iterations + 1))
    # Moving-block bootstrap confidence interval for the observed mean.
    start_count = max(len(differences) - block_length + 1, 1)
    blocks_needed = int(np.ceil(len(differences) / block_length))
    boot_means = np.empty(iterations, dtype=float)
    for iteration in range(iterations):
        starts = rng.integers(0, start_count, size=blocks_needed)
        sample = np.concatenate([differences[start : start + block_length] for start in starts])[: len(differences)]
        boot_means[iteration] = float(sample.mean())
    return {
        "mean_daily_difference": observed,
        "ci_low": float(np.quantile(boot_means, 0.025)),
        "ci_high": float(np.quantile(boot_means, 0.975)),
        "two_sided_p_value": two_sided_p_value,
        "improvement_p_value": improvement_p_value,
        "harm_p_value": harm_p_value,
    }


def bh_q_values(p_values: list[float | None]) -> list[float | None]:
    valid = [(index, float(value)) for index, value in enumerate(p_values) if value is not None and np.isfinite(value)]
    result: list[float | None] = [None] * len(p_values)
    if not valid:
        return result
    ordered = sorted(valid, key=lambda item: item[1])
    total = len(ordered)
    running = 1.0
    adjusted: list[float] = [1.0] * total
    for reverse_index in range(total - 1, -1, -1):
        rank = reverse_index + 1
        running = min(running, ordered[reverse_index][1] * total / rank)
        adjusted[reverse_index] = min(running, 1.0)
    for (original_index, _), value in zip(ordered, adjusted):
        result[original_index] = float(value)
    return result


def simulate_periods(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    variant: str,
    periods: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    for period_name, (start, end) in periods.items():
        period_signals, period_prices = exit_lab.slice_period(
            signals,
            prices,
            start,
            end,
            BASELINE_EXIT_POLICY.maximum_holding_sessions,
        )
        result = portfolio.simulate_scenario(
            period_signals,
            period_prices,
            portfolio.PortfolioScenario(f"{variant}_{period_name}", None, 0.0, 0.01),
            exit_policy=BASELINE_EXIT_POLICY,
        )
        metrics = dict(result["metrics"])
        metrics.update({
            "variant": variant,
            "period": period_name,
            "period_start": start.date().isoformat(),
            "period_end_signal": end.date().isoformat(),
            "signal_count": len(period_signals),
        })
        rows.append(metrics)
        results[period_name] = result
    return pd.DataFrame(rows), results


def run_ablation(
    history: pd.DataFrame,
    prices: pd.DataFrame,
    top_limit: int = 100,
) -> dict[str, pd.DataFrame]:
    variant_histories: dict[str, pd.DataFrame] = {}
    replay_results: dict[str, replay.ReplayResult] = {}
    diagnostics_rows: list[dict[str, Any]] = []
    distribution_frames: list[pd.DataFrame] = []

    for variant in VARIANTS:
        variant_history = build_variant_history(history, variant, top_limit=top_limit)
        variant_histories[variant] = variant_history
        replay_result = replay.run_walk_forward_replay(variant_history, top_limit=top_limit)
        replay_results[variant] = replay_result

    baseline_history = variant_histories["baseline"]
    baseline_signals = replay_results["baseline"].signals
    if baseline_signals is None or baseline_signals.empty:
        raise RuntimeError("baseline ablation replay produced no signals")
    aligned_prices = attribution.align_prices_to_signal_window(baseline_signals, prices)
    periods = exit_lab.period_ranges(baseline_signals)

    period_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    equity_frames: list[pd.DataFrame] = []
    simulation_results: dict[str, dict[str, dict[str, Any]]] = {}

    for variant in VARIANTS:
        variant_history = variant_histories[variant]
        distribution = validate_distribution_preservation(baseline_history, variant_history)
        distribution["variant"] = variant
        distribution_frames.append(distribution)
        diagnostics_rows.append({
            "variant": variant,
            "removed_component": variant_component(variant) or "",
            "removed_component_label": "" if variant == "baseline" else COMPONENTS[str(variant_component(variant))]["label"],
            "removed_maximum_points": 0 if variant == "baseline" else COMPONENTS[str(variant_component(variant))]["maximum_points"],
            "distribution_preserved_all_dates": bool(distribution["score_multiset_equal"].all()),
            **rank_diagnostics(baseline_history, variant_history),
            "replay_signal_count": len(replay_results[variant].signals),
        })
        signals = replay_results[variant].signals
        periods_frame, results_by_period = simulate_periods(signals, aligned_prices, variant, periods)
        period_frames.append(periods_frame)
        simulation_results[variant] = results_by_period
        for period_name, result in results_by_period.items():
            for key, frames in (("trades", trade_frames), ("equity", equity_frames)):
                frame = result[key].copy()
                if not frame.empty:
                    frame["variant"] = variant
                    frame["period"] = period_name
                    frames.append(frame)

    period_metrics = pd.concat(period_frames, ignore_index=True)
    baseline_period = {
        period: period_metrics[(period_metrics["variant"] == "baseline") & (period_metrics["period"] == period)].iloc[0]
        for period in ("full", "early", "late")
    }
    summary_rows: list[dict[str, Any]] = []
    p_values: list[float | None] = []
    for index, variant in enumerate(VARIANTS):
        full = period_metrics[(period_metrics["variant"] == variant) & (period_metrics["period"] == "full")].iloc[0]
        early = period_metrics[(period_metrics["variant"] == variant) & (period_metrics["period"] == "early")].iloc[0]
        late = period_metrics[(period_metrics["variant"] == variant) & (period_metrics["period"] == "late")].iloc[0]
        test = (
            {
                "mean_daily_difference": 0.0,
                "ci_low": 0.0,
                "ci_high": 0.0,
                "two_sided_p_value": None,
                "improvement_p_value": None,
                "harm_p_value": None,
            }
            if variant == "baseline"
            else paired_sign_flip(
                simulation_results["baseline"]["full"]["equity"],
                simulation_results[variant]["full"]["equity"],
                seed=20260711 + index,
            )
        )
        p_values.append(test["two_sided_p_value"])
        row = {
            "variant": variant,
            "removed_component": variant_component(variant) or "",
            "removed_component_label": "" if variant == "baseline" else COMPONENTS[str(variant_component(variant))]["label"],
            "removed_maximum_points": 0 if variant == "baseline" else COMPONENTS[str(variant_component(variant))]["maximum_points"],
            "full_closed_trades": int(full["closed_trades"]),
            "early_closed_trades": int(early["closed_trades"]),
            "late_closed_trades": int(late["closed_trades"]),
            "full_total_return": float(full["total_return"]),
            "full_benchmark_return": float(full["benchmark_total_return"]),
            "full_excess_return": float(full["excess_total_return"]),
            "full_max_drawdown": float(full["max_drawdown"]),
            "full_sharpe": full.get("sharpe"),
            "full_win_rate": full.get("win_rate"),
            "delta_excess_vs_baseline": float(full["excess_total_return"]) - float(baseline_period["full"]["excess_total_return"]),
            "delta_max_drawdown_vs_baseline": float(full["max_drawdown"]) - float(baseline_period["full"]["max_drawdown"]),
            "early_delta_excess": float(early["excess_total_return"]) - float(baseline_period["early"]["excess_total_return"]),
            "late_delta_excess": float(late["excess_total_return"]) - float(baseline_period["late"]["excess_total_return"]),
            **test,
        }
        summary_rows.append(row)

    q_values = bh_q_values(p_values)
    for row, q_value in zip(summary_rows, q_values):
        row["fdr_q_value"] = q_value
        if row["variant"] == "baseline":
            row["sample_adequate"] = True
            row["ablation_status"] = "BASELINE"
            continue
        adequate = (
            row["full_closed_trades"] >= MIN_FULL_TRADES
            and row["early_closed_trades"] >= MIN_SUBPERIOD_TRADES
            and row["late_closed_trades"] >= MIN_SUBPERIOD_TRADES
        )
        removal_improves = (
            row["delta_excess_vs_baseline"] > 0
            and row["delta_max_drawdown_vs_baseline"] >= 0
            and row["early_delta_excess"] >= 0
            and row["late_delta_excess"] >= 0
        )
        removal_hurts = (
            row["delta_excess_vs_baseline"] < 0
            and row["early_delta_excess"] <= 0
            and row["late_delta_excess"] <= 0
        )
        improvement_supported = (
            q_value is not None
            and q_value <= MAXIMUM_FDR_Q
            and row["ci_low"] is not None
            and row["ci_low"] > 0
        )
        harm_supported = (
            q_value is not None
            and q_value <= MAXIMUM_FDR_Q
            and row["ci_high"] is not None
            and row["ci_high"] < 0
        )
        row["sample_adequate"] = adequate
        if not adequate:
            row["ablation_status"] = "INSUFFICIENT"
        elif removal_improves and improvement_supported:
            row["ablation_status"] = "REMOVAL_IMPROVES_VALIDATED"
        elif removal_improves:
            row["ablation_status"] = "REMOVAL_IMPROVES_DIRECTIONAL"
        elif removal_hurts and harm_supported:
            row["ablation_status"] = "REMOVAL_HURTS_VALIDATED"
        elif removal_hurts:
            row["ablation_status"] = "REMOVAL_HURTS_DIRECTIONAL"
        else:
            row["ablation_status"] = "MIXED"
        row["automatic_weight_change_allowed"] = False

    summary = pd.DataFrame(summary_rows)
    diagnostics = pd.DataFrame(diagnostics_rows)
    summary = summary.merge(diagnostics, on=["variant", "removed_component", "removed_component_label", "removed_maximum_points"], how="left")
    status_order = {
        "REMOVAL_IMPROVES_VALIDATED": 0,
        "REMOVAL_IMPROVES_DIRECTIONAL": 1,
        "BASELINE": 2,
        "REMOVAL_HURTS_VALIDATED": 3,
        "REMOVAL_HURTS_DIRECTIONAL": 4,
        "MIXED": 5,
        "INSUFFICIENT": 6,
    }
    summary["_status_order"] = summary["ablation_status"].map(status_order).fillna(9)
    summary = summary.sort_values(
        ["_status_order", "delta_excess_vs_baseline", "delta_max_drawdown_vs_baseline"],
        ascending=[True, False, False],
    ).drop(columns="_status_order").reset_index(drop=True)
    return {
        "summary": summary,
        "period_metrics": period_metrics,
        "rank_diagnostics": diagnostics,
        "distribution_audit": pd.concat(distribution_frames, ignore_index=True),
        "trades": pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame(),
        "equity": pd.concat(equity_frames, ignore_index=True) if equity_frames else pd.DataFrame(),
        "variant_signal_counts": pd.DataFrame([
            {"variant": variant, "signal_count": len(result.signals), "lookahead_violations": result.manifest.get("lookahead_violations")}
            for variant, result in replay_results.items()
        ]),
    }


def write_outputs(results: dict[str, pd.DataFrame], provenance_path: str, history_path: str, output_dir: str) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    paths = {
        "summary": output / "score_component_ablation_summary.csv",
        "period_metrics": output / "score_component_ablation_period_metrics.csv",
        "rank_diagnostics": output / "score_component_rank_diagnostics.csv",
        "distribution_audit": output / "score_distribution_preservation_audit.csv",
        "trades": output / "score_component_ablation_trades.csv",
        "equity": output / "score_component_ablation_equity.csv",
        "signal_counts": output / "score_component_variant_signal_counts.csv",
        "excel": output / "score_component_ablation.xlsx",
        "manifest": output / "score_component_ablation_manifest.json",
    }
    mapping = {
        "summary": "summary", "period_metrics": "period_metrics",
        "rank_diagnostics": "rank_diagnostics", "distribution_audit": "distribution_audit",
        "trades": "trades", "equity": "equity", "signal_counts": "variant_signal_counts",
    }
    for path_key, result_key in mapping.items():
        results[result_key].to_csv(paths[path_key], index=False)
    manifest = {
        "ablation_version": ABLATION_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "portfolio_engine_version": portfolio.PORTFOLIO_RESEARCH_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_history_sha256": sha256_file(history_path),
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "variants": list(VARIANTS),
        "component_definitions": COMPONENTS,
        "daily_score_distribution_preserved": bool(results["distribution_audit"]["score_multiset_equal"].all()),
        "automatic_weight_change": False,
        "automatic_component_removal": False,
        "promotion_evidence_allowed": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([{key: value for key, value in manifest.items() if key not in {"variants", "component_definitions"}}]).to_excel(writer, sheet_name="Manifest", index=False)
        results["summary"].to_excel(writer, sheet_name="Ablation Summary", index=False)
        results["period_metrics"].to_excel(writer, sheet_name="Period Metrics", index=False)
        results["rank_diagnostics"].to_excel(writer, sheet_name="Rank Diagnostics", index=False)
        results["distribution_audit"].to_excel(writer, sheet_name="Distribution Audit", index=False)
        results["variant_signal_counts"].to_excel(writer, sheet_name="Signal Counts", index=False)
        results["trades"].to_excel(writer, sheet_name="Trades", index=False)
        results["equity"].to_excel(writer, sheet_name="Equity", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"manifest": manifest, "paths": {key: str(value) for key, value in paths.items()}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run distribution-preserving score component ablation")
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--prices", default=DEFAULT_PRICES)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-limit", type=int, default=100)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before = replay.live_state_hashes()
    history = load_history(args.history)
    prices = portfolio.load_prices(args.prices)
    results = run_ablation(history, prices, top_limit=args.top_limit)
    output = write_outputs(results, args.provenance, args.history, args.output_dir)
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    output["manifest"]["production_state_mutations"] = mutations
    Path(output["paths"]["manifest"]).write_text(
        json.dumps(output["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        if mutations:
            raise RuntimeError(f"production state mutated: {mutations}")
        if set(results["summary"]["variant"]) != set(VARIANTS):
            raise RuntimeError("one or more ablation variants are missing")
        if not results["distribution_audit"]["score_multiset_equal"].all():
            raise RuntimeError("daily score distribution changed during ablation")
        if not results["variant_signal_counts"]["lookahead_violations"].fillna(0).eq(0).all():
            raise RuntimeError("lookahead violation detected in ablation replay")
        if results["equity"].empty:
            raise RuntimeError("ablation produced no equity curves")
    print(results["summary"][[
        "variant", "full_closed_trades", "full_total_return", "full_excess_return",
        "full_max_drawdown", "delta_excess_vs_baseline",
        "delta_max_drawdown_vs_baseline", "early_delta_excess", "late_delta_excess",
        "fdr_q_value", "ablation_status",
    ]].to_string(index=False))
    print(json.dumps(output["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
