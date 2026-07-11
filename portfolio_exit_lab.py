"""Governed, research-only comparison of portfolio exit policies.

The entry candidates, capital constraints, execution model and benchmark stay
fixed. Only pre-declared exit parameters and optional lifecycle deterioration
holds are varied. Historical current-universe evidence is permanently
non-promotable and no winning policy is activated automatically.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import main
import portfolio_filter_lab as filter_lab
import portfolio_research as portfolio
import replay

EXIT_LAB_VERSION = "2026-07-11-portfolio-exit-lab-v1"
DEFAULT_SIGNALS = "output/backfill/replay/replay_signals.csv"
DEFAULT_HISTORY = "output/backfill/historical_ranking.csv"
DEFAULT_PRICES = "output/backfill/historical_price_panel.csv"
DEFAULT_PROVENANCE = "output/backfill/replay/evidence_provenance.json"
DEFAULT_OUTPUT_DIR = "output/backfill/portfolio_exits"

DETERIORATION_STATES = ("失速警戒", "崩れ", "低位")
MIN_FULL_TRADES = 12
MIN_SUBPERIOD_TRADES = 4


@dataclass(frozen=True)
class ExitExperiment:
    name: str
    description: str
    exit_policy: portfolio.ExitPolicy
    entry_cohort: str = "baseline"
    deterioration_guard: bool = False


BASELINE_EXIT = portfolio.ExitPolicy("baseline", 0.08, 0.15, 0.10, 20, True)
EXIT_POLICIES: tuple[portfolio.ExitPolicy, ...] = (
    BASELINE_EXIT,
    portfolio.ExitPolicy("stop_4", 0.04, 0.15, 0.10, 20, True),
    portfolio.ExitPolicy("stop_6", 0.06, 0.15, 0.10, 20, True),
    portfolio.ExitPolicy("stop_10", 0.10, 0.15, 0.10, 20, True),
    portfolio.ExitPolicy("target_10", 0.08, 0.10, 0.10, 20, True),
    portfolio.ExitPolicy("target_20", 0.08, 0.20, 0.10, 20, True),
    portfolio.ExitPolicy("trail_6", 0.08, 0.15, 0.06, 20, True),
    portfolio.ExitPolicy("trail_8", 0.08, 0.15, 0.08, 20, True),
    portfolio.ExitPolicy("trail_12", 0.08, 0.15, 0.12, 20, True),
    portfolio.ExitPolicy("hold_5", 0.08, 0.15, 0.10, 5, True),
    portfolio.ExitPolicy("hold_10", 0.08, 0.15, 0.10, 10, True),
    portfolio.ExitPolicy("hold_30", 0.08, 0.15, 0.10, 30, True),
    portfolio.ExitPolicy("no_signal_exit", 0.08, 0.15, 0.10, 20, False),
)


def experiments() -> tuple[ExitExperiment, ...]:
    rows: list[ExitExperiment] = []
    for cohort in ("baseline", "relative_strength_a_s"):
        for policy in EXIT_POLICIES:
            rows.append(ExitExperiment(
                f"{cohort}__{policy.name}",
                f"{cohort} entry cohort with {policy.name} exit policy",
                policy,
                entry_cohort=cohort,
            ))
        rows.append(ExitExperiment(
            f"{cohort}__deterioration_guard",
            f"{cohort} entry cohort; exit when lifecycle is 失速警戒・崩れ・低位",
            BASELINE_EXIT,
            entry_cohort=cohort,
            deterioration_guard=True,
        ))
    return tuple(rows)


EXPERIMENTS = experiments()


def _number(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def entry_mask(signals: pd.DataFrame, cohort: str) -> pd.Series:
    if cohort == "baseline":
        return pd.Series(True, index=signals.index, dtype=bool)
    if cohort == "relative_strength_a_s":
        return signals.get(
            "relative_strength_grade", pd.Series(index=signals.index, dtype=str)
        ).fillna("").astype(str).isin({"S", "A"})
    raise ValueError(f"unknown entry cohort: {cohort}")


def prepare_experiment_signals(
    enriched_signals: pd.DataFrame,
    experiment: ExitExperiment,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    result = enriched_signals.copy()
    entries = entry_mask(result, experiment.entry_cohort)
    result["portfolio_eligible"] = entries
    if experiment.deterioration_guard:
        lifecycle = result.get(
            "relative_strength_lifecycle", pd.Series(index=result.index, dtype=str)
        ).fillna("").astype(str)
        hold = ~lifecycle.isin(DETERIORATION_STATES)
        result["portfolio_hold_eligible"] = hold
    else:
        result["portfolio_hold_eligible"] = True
    result["exit_experiment"] = experiment.name
    return result, {
        "entry_cohort": experiment.entry_cohort,
        "entry_eligible_count": int(entries.sum()),
        "entry_signal_count": len(result),
        "entry_eligible_ratio": float(entries.mean()) if len(result) else 0.0,
        "deterioration_guard": experiment.deterioration_guard,
        "hold_ineligible_count": int((~result["portfolio_hold_eligible"]).sum()),
    }


def period_ranges(signals: pd.DataFrame) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    dates = sorted(pd.to_datetime(signals["signal_date"], errors="coerce").dropna().dt.normalize().unique())
    if not dates:
        raise ValueError("no signal dates available")
    first = pd.Timestamp(dates[0])
    last = pd.Timestamp(dates[-1])
    midpoint = len(dates) // 2
    early_last = pd.Timestamp(dates[max(midpoint - 1, 0)])
    late_first = pd.Timestamp(dates[midpoint]) if midpoint < len(dates) else early_last
    return {
        "full": (first, last),
        "early": (first, early_last),
        "late": (late_first, last),
    }


def slice_period(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    maximum_holding_sessions: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    signal_dates = pd.to_datetime(signals["signal_date"], errors="coerce").dt.normalize()
    selected_signals = signals[(signal_dates >= start) & (signal_dates <= end)].copy()
    all_dates = pd.DatetimeIndex(sorted(pd.to_datetime(prices["date"], errors="coerce").dropna().dt.normalize().unique()))
    if all_dates.empty:
        return selected_signals, prices.iloc[0:0].copy()
    end_index = int(all_dates.searchsorted(end, side="right"))
    end_index = min(end_index + maximum_holding_sessions + 2, len(all_dates))
    price_end = all_dates[end_index - 1] if end_index else end
    selected_prices = prices[
        (pd.to_datetime(prices["date"], errors="coerce").dt.normalize() >= start)
        & (pd.to_datetime(prices["date"], errors="coerce").dt.normalize() <= price_end)
    ].copy()
    return selected_signals, selected_prices


def run_exit_lab(
    signals: pd.DataFrame,
    history: pd.DataFrame,
    prices: pd.DataFrame,
    experiment_rows: tuple[ExitExperiment, ...] = EXPERIMENTS,
) -> dict[str, pd.DataFrame]:
    enriched, coverage = filter_lab.attach_filter_context(signals, history)
    periods = period_ranges(enriched)
    metric_rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    equity_frames: list[pd.DataFrame] = []
    skip_frames: list[pd.DataFrame] = []
    experiment_audit: list[dict[str, Any]] = []

    for experiment in experiment_rows:
        prepared, audit = prepare_experiment_signals(enriched, experiment)
        experiment_audit.append({
            "experiment": experiment.name,
            "description": experiment.description,
            **audit,
            **asdict(experiment.exit_policy),
        })
        scenario = portfolio.PortfolioScenario(
            experiment.name,
            0.03 if experiment.entry_cohort == "relative_strength_a_s" else None,
            500_000_000.0 if experiment.entry_cohort == "relative_strength_a_s" else 0.0,
            0.01,
        )
        for period_name, (start, end) in periods.items():
            period_signals, period_prices = slice_period(
                prepared,
                prices,
                start,
                end,
                experiment.exit_policy.maximum_holding_sessions,
            )
            result = portfolio.simulate_scenario(
                period_signals,
                period_prices,
                scenario,
                exit_policy=experiment.exit_policy,
            )
            metrics = dict(result["metrics"])
            metrics.update({
                "experiment": experiment.name,
                "description": experiment.description,
                "period": period_name,
                "period_start": start.date().isoformat(),
                "period_end_signal": end.date().isoformat(),
                "entry_cohort": experiment.entry_cohort,
                "deterioration_guard": experiment.deterioration_guard,
                "entry_eligible_count": int(period_signals.get("portfolio_eligible", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()),
                "signal_count": len(period_signals),
            })
            metric_rows.append(metrics)
            for key, target in (("trades", trade_frames), ("equity", equity_frames), ("skips", skip_frames)):
                frame = result[key].copy()
                if not frame.empty:
                    frame["experiment"] = experiment.name
                    frame["period"] = period_name
                    target.append(frame)

    metrics = pd.DataFrame(metric_rows)
    full = metrics[metrics["period"] == "full"].copy()
    early = metrics[metrics["period"] == "early"].set_index("experiment")
    late = metrics[metrics["period"] == "late"].set_index("experiment")
    baseline_lookup = {
        cohort: rows.iloc[0]
        for cohort, rows in full[full["exit_policy"] == "baseline"].groupby("entry_cohort")
    }
    summary_rows: list[dict[str, Any]] = []
    for _, row in full.iterrows():
        experiment_name = str(row["experiment"])
        cohort = str(row["entry_cohort"])
        baseline = baseline_lookup.get(cohort)
        early_row = early.loc[experiment_name] if experiment_name in early.index else pd.Series(dtype=object)
        late_row = late.loc[experiment_name] if experiment_name in late.index else pd.Series(dtype=object)
        if isinstance(early_row, pd.DataFrame):
            early_row = early_row.iloc[0]
        if isinstance(late_row, pd.DataFrame):
            late_row = late_row.iloc[0]
        full_trades = int(_number(row.get("closed_trades")) or 0)
        early_trades = int(_number(early_row.get("closed_trades")) or 0)
        late_trades = int(_number(late_row.get("closed_trades")) or 0)
        adequate = (
            full_trades >= MIN_FULL_TRADES
            and early_trades >= MIN_SUBPERIOD_TRADES
            and late_trades >= MIN_SUBPERIOD_TRADES
        )
        baseline_excess = _number(baseline.get("excess_total_return")) if baseline is not None else None
        baseline_dd = _number(baseline.get("max_drawdown")) if baseline is not None else None
        excess = _number(row.get("excess_total_return"))
        drawdown = _number(row.get("max_drawdown"))
        delta_excess = None if baseline_excess is None or excess is None else excess - baseline_excess
        delta_dd = None if baseline_dd is None or drawdown is None else drawdown - baseline_dd
        early_return = _number(early_row.get("total_return"))
        late_return = _number(late_row.get("total_return"))
        early_excess = _number(early_row.get("excess_total_return"))
        late_excess = _number(late_row.get("excess_total_return"))
        robust_outperformance = bool(
            adequate
            and excess is not None and excess > 0
            and early_excess is not None and early_excess > 0
            and late_excess is not None and late_excess > 0
        )
        robust_improvement = bool(
            adequate
            and delta_excess is not None and delta_excess > 0
            and delta_dd is not None and delta_dd >= 0
            and early_return is not None and late_return is not None
            and early_return > 0 and late_return > 0
        )
        if row.get("exit_policy") == "baseline" and not bool(row.get("deterioration_guard")):
            status = "BASELINE"
        elif not adequate:
            status = "INSUFFICIENT"
        elif robust_outperformance:
            status = "ROBUST_OUTPERFORMANCE"
        elif robust_improvement:
            status = "ROBUST_IMPROVEMENT_ONLY"
        elif delta_excess is not None and delta_excess > 0 and delta_dd is not None and delta_dd >= 0:
            status = "FULL_PERIOD_IMPROVEMENT_ONLY"
        else:
            status = "NOT_IMPROVED"
        summary_rows.append({
            **row.to_dict(),
            "early_closed_trades": early_trades,
            "late_closed_trades": late_trades,
            "early_total_return": early_return,
            "late_total_return": late_return,
            "early_excess_total_return": early_excess,
            "late_excess_total_return": late_excess,
            "delta_excess_vs_cohort_baseline": delta_excess,
            "delta_max_drawdown_vs_cohort_baseline": delta_dd,
            "sample_adequate": adequate,
            "evidence_status": status,
        })
    summary = pd.DataFrame(summary_rows)
    status_order = {
        "ROBUST_OUTPERFORMANCE": 0,
        "ROBUST_IMPROVEMENT_ONLY": 1,
        "FULL_PERIOD_IMPROVEMENT_ONLY": 2,
        "BASELINE": 3,
        "NOT_IMPROVED": 4,
        "INSUFFICIENT": 5,
    }
    summary["_status_order"] = summary["evidence_status"].map(status_order).fillna(9)
    summary = summary.sort_values(
        ["_status_order", "entry_cohort", "excess_total_return", "max_drawdown"],
        ascending=[True, True, False, False],
    ).drop(columns="_status_order").reset_index(drop=True)
    return {
        "summary": summary,
        "period_metrics": metrics,
        "trades": pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame(),
        "equity": pd.concat(equity_frames, ignore_index=True) if equity_frames else pd.DataFrame(),
        "skips": pd.concat(skip_frames, ignore_index=True) if skip_frames else pd.DataFrame(),
        "experiment_audit": pd.DataFrame(experiment_audit),
        "context_coverage": coverage,
        "enriched_signals": enriched,
    }


def write_outputs(results: dict[str, pd.DataFrame], provenance_path: str, output_dir: str) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    paths = {
        "summary": output / "portfolio_exit_summary.csv",
        "period_metrics": output / "portfolio_exit_period_metrics.csv",
        "trades": output / "portfolio_exit_trades.csv",
        "equity": output / "portfolio_exit_equity.csv",
        "skips": output / "portfolio_exit_skips.csv",
        "audit": output / "portfolio_exit_experiment_audit.csv",
        "coverage": output / "portfolio_exit_context_coverage.csv",
        "excel": output / "portfolio_exit_lab.xlsx",
        "manifest": output / "portfolio_exit_manifest.json",
    }
    mapping = {
        "summary": "summary",
        "period_metrics": "period_metrics",
        "trades": "trades",
        "equity": "equity",
        "skips": "skips",
        "audit": "experiment_audit",
        "coverage": "context_coverage",
    }
    for path_key, result_key in mapping.items():
        results[result_key].to_csv(paths[path_key], index=False)
    manifest = {
        "exit_lab_version": EXIT_LAB_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "portfolio_engine_version": portfolio.PORTFOLIO_RESEARCH_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "promotion_evidence_allowed": False,
        "automatic_exit_activation": False,
        "automatic_strategy_change": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
        "experiment_count": len(EXPERIMENTS),
        "minimum_full_trades": MIN_FULL_TRADES,
        "minimum_subperiod_trades": MIN_SUBPERIOD_TRADES,
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "experiments": [
            {
                "name": row.name,
                "description": row.description,
                "entry_cohort": row.entry_cohort,
                "deterioration_guard": row.deterioration_guard,
                "exit_policy": asdict(row.exit_policy),
            }
            for row in EXPERIMENTS
        ],
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([{key: value for key, value in manifest.items() if key != "experiments"}]).to_excel(writer, sheet_name="Lab Summary", index=False)
        results["summary"].to_excel(writer, sheet_name="Exit Summary", index=False)
        results["period_metrics"].to_excel(writer, sheet_name="Period Metrics", index=False)
        results["trades"].to_excel(writer, sheet_name="Trades", index=False)
        results["equity"].to_excel(writer, sheet_name="Equity", index=False)
        results["skips"].to_excel(writer, sheet_name="Skipped Entries", index=False)
        results["experiment_audit"].to_excel(writer, sheet_name="Experiment Audit", index=False)
        results["context_coverage"].to_excel(writer, sheet_name="Context Coverage", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"manifest": manifest, "paths": {key: str(value) for key, value in paths.items()}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare governed portfolio exit policies")
    parser.add_argument("--signals", default=DEFAULT_SIGNALS)
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--prices", default=DEFAULT_PRICES)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before = replay.live_state_hashes()
    signals = portfolio.load_signals(args.signals)
    history = filter_lab.load_history(args.history)
    prices = portfolio.load_prices(args.prices)
    results = run_exit_lab(signals, history, prices)
    output = write_outputs(results, args.provenance, args.output_dir)
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    output["manifest"]["production_state_mutations"] = mutations
    Path(output["paths"]["manifest"]).write_text(
        json.dumps(output["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        coverage = results["context_coverage"].iloc[0]
        if mutations:
            raise RuntimeError(f"production state mutated: {mutations}")
        if set(results["summary"]["experiment"]) != {row.name for row in EXPERIMENTS}:
            raise RuntimeError("one or more exit experiments are missing")
        if set(results["period_metrics"]["period"]) != {"full", "early", "late"}:
            raise RuntimeError("early/late stability periods are missing")
        if float(coverage["relative_strength_score_coverage"]) < 0.99:
            raise RuntimeError("relative strength coverage below 99%")
        if float(coverage["lifecycle_coverage"]) < 0.99:
            raise RuntimeError("lifecycle coverage below 99%")
        if results["equity"].empty:
            raise RuntimeError("exit lab produced no equity curves")
        if not results["trades"].empty and pd.to_datetime(results["trades"]["entry_date"]).le(pd.to_datetime(results["trades"]["signal_date"])).any():
            raise RuntimeError("same-day or pre-signal entry detected")
    print(results["summary"][[
        "experiment", "entry_cohort", "closed_trades", "total_return",
        "max_drawdown", "excess_total_return", "early_total_return",
        "late_total_return", "evidence_status",
    ]].to_string(index=False))
    print(json.dumps(output["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
