"""Research-only capacity and liquidity stress tests for executable signals.

The analysis applies 100-share lots, requested order sizes, participation caps,
positive-gap filters, liquidity floors, and heuristic market-impact costs to the
next-session execution outcomes. It never alters production selection rules,
thresholds, paper positions, or live state.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import main
import replay
import research_scorecard
import robustness_analysis

CAPACITY_VERSION = "2026-07-11-capacity-scenarios-v1"
DEFAULT_OUTCOMES = "output/backfill/execution/execution_benchmarked_outcomes.csv"
DEFAULT_PRICES = "output/backfill/historical_price_panel.csv"
DEFAULT_PROVENANCE = "output/backfill/replay/evidence_provenance.json"
DEFAULT_OUTPUT_DIR = "output/backfill/capacity"
DEFAULT_CAPITALS = (1_000_000, 3_000_000, 10_000_000)
LOT_SIZE = 100
BASE_SLIPPAGE_BPS = 5.0
IMPACT_COEFFICIENT_BPS = 100.0
FEES_BPS = 20.0


@dataclass(frozen=True)
class Scenario:
    name: str
    maximum_positive_gap: float | None
    minimum_entry_trading_value: float
    maximum_participation: float


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("baseline_1pct", None, 0.0, 0.01),
    Scenario("no_gap_chase_5pct", 0.05, 0.0, 0.01),
    Scenario("no_gap_chase_3pct", 0.03, 0.0, 0.01),
    Scenario("minimum_500m", None, 500_000_000.0, 0.01),
    Scenario("gap3_minimum_500m", 0.03, 500_000_000.0, 0.01),
    Scenario("strict_capacity_0_5pct", None, 0.0, 0.005),
    Scenario("broad_capacity_2pct", None, 0.0, 0.02),
)


def load_outcomes(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(target, dtype={"code": str})
    required = {
        "signal_date", "entry_price_date", "exit_price_date", "code", "sector33",
        "horizon_days", "next_session_open", "exit_close", "entry_gap_return",
        "universe_equal_weight_return",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"execution outcomes missing columns: {missing}")
    frame["code"] = frame["code"].map(main.normalize_code)
    for column in ("signal_date", "entry_price_date", "exit_price_date"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    numeric_columns = [
        "horizon_days", "next_session_open", "exit_close", "entry_gap_return",
        "universe_equal_weight_return",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=[
        "entry_price_date", "exit_price_date", "code", "horizon_days",
        "next_session_open", "exit_close", "universe_equal_weight_return",
    ]).copy()


def load_price_panel(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(target, dtype={"code": str})
    required = {"date", "code", "raw_trading_value"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"price panel missing columns: {missing}")
    frame["code"] = frame["code"].map(main.normalize_code)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["raw_trading_value"] = pd.to_numeric(frame["raw_trading_value"], errors="coerce")
    return frame.dropna(subset=["date", "code", "raw_trading_value"]).drop_duplicates(["date", "code"], keep="last")


def attach_liquidity(outcomes: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    entry = panel.rename(columns={"date": "entry_price_date", "raw_trading_value": "entry_raw_trading_value"})[
        ["entry_price_date", "code", "entry_raw_trading_value"]
    ]
    exit_frame = panel.rename(columns={"date": "exit_price_date", "raw_trading_value": "exit_raw_trading_value"})[
        ["exit_price_date", "code", "exit_raw_trading_value"]
    ]
    merged = outcomes.merge(entry, on=["entry_price_date", "code"], how="left")
    merged = merged.merge(exit_frame, on=["exit_price_date", "code"], how="left")
    return merged


def impact_bps(participation: float) -> float:
    if participation < 0 or not math.isfinite(participation):
        return float("nan")
    return BASE_SLIPPAGE_BPS + IMPACT_COEFFICIENT_BPS * math.sqrt(participation)


def order_quantity(capital: float, entry_price: float) -> int:
    if capital <= 0 or entry_price <= 0:
        return 0
    return int(capital // (entry_price * LOT_SIZE)) * LOT_SIZE


def scenario_rejection_reason(
    scenario: Scenario,
    positive_gap: float,
    entry_trading_value: float,
    exit_trading_value: float,
    quantity: int,
    entry_participation: float,
    exit_participation: float,
) -> str:
    if quantity <= 0:
        return "ORDER_BELOW_100_SHARE_LOT"
    if not math.isfinite(entry_trading_value) or entry_trading_value <= 0:
        return "MISSING_ENTRY_LIQUIDITY"
    if not math.isfinite(exit_trading_value) or exit_trading_value <= 0:
        return "MISSING_EXIT_LIQUIDITY"
    if scenario.maximum_positive_gap is not None and positive_gap > scenario.maximum_positive_gap:
        return "POSITIVE_GAP_ABOVE_LIMIT"
    if entry_trading_value < scenario.minimum_entry_trading_value:
        return "ENTRY_TRADING_VALUE_BELOW_MINIMUM"
    if entry_participation > scenario.maximum_participation:
        return "ENTRY_PARTICIPATION_ABOVE_LIMIT"
    if exit_participation > scenario.maximum_participation:
        return "EXIT_PARTICIPATION_ABOVE_LIMIT"
    return ""


def simulate_capacity_scenarios(
    outcomes: pd.DataFrame,
    panel: pd.DataFrame,
    capitals: tuple[int, ...] = DEFAULT_CAPITALS,
    scenarios: tuple[Scenario, ...] = SCENARIOS,
) -> pd.DataFrame:
    enriched = attach_liquidity(outcomes, panel)
    rows: list[dict[str, Any]] = []
    for _, source in enriched.iterrows():
        entry_price = float(source["next_session_open"])
        exit_price = float(source["exit_close"])
        entry_tv = float(source["entry_raw_trading_value"]) if pd.notna(source.get("entry_raw_trading_value")) else float("nan")
        exit_tv = float(source["exit_raw_trading_value"]) if pd.notna(source.get("exit_raw_trading_value")) else float("nan")
        positive_gap = max(float(source.get("entry_gap_return", 0.0) or 0.0), 0.0)
        benchmark = float(source["universe_equal_weight_return"])
        for capital in capitals:
            quantity = order_quantity(float(capital), entry_price)
            entry_notional = quantity * entry_price
            exit_notional = quantity * exit_price
            entry_participation = entry_notional / entry_tv if math.isfinite(entry_tv) and entry_tv > 0 else float("nan")
            exit_participation = exit_notional / exit_tv if math.isfinite(exit_tv) and exit_tv > 0 else float("nan")
            entry_impact = impact_bps(entry_participation)
            exit_impact = impact_bps(exit_participation)
            for scenario in scenarios:
                reason = scenario_rejection_reason(
                    scenario,
                    positive_gap,
                    entry_tv,
                    exit_tv,
                    quantity,
                    entry_participation,
                    exit_participation,
                )
                eligible = reason == ""
                if eligible:
                    effective_entry = entry_price * (1 + entry_impact / 10_000)
                    effective_exit = exit_price * (1 - exit_impact / 10_000)
                    net_return = effective_exit / effective_entry - 1 - FEES_BPS / 10_000
                    excess = net_return - benchmark
                else:
                    net_return = float("nan")
                    excess = float("nan")
                record = source.to_dict()
                record.update({
                    "scenario": scenario.name,
                    "requested_capital": int(capital),
                    "lot_size": LOT_SIZE,
                    "quantity": quantity,
                    "entry_notional": entry_notional,
                    "exit_notional": exit_notional,
                    "entry_raw_trading_value": entry_tv,
                    "exit_raw_trading_value": exit_tv,
                    "entry_participation": entry_participation,
                    "exit_participation": exit_participation,
                    "maximum_positive_gap": scenario.maximum_positive_gap,
                    "minimum_entry_trading_value": scenario.minimum_entry_trading_value,
                    "maximum_participation": scenario.maximum_participation,
                    "entry_impact_bps": entry_impact,
                    "exit_impact_bps": exit_impact,
                    "fees_bps": FEES_BPS,
                    "eligible": eligible,
                    "rejection_reason": reason,
                    "capacity_net_return": net_return,
                    "capacity_excess_vs_universe": excess,
                    "capacity_beat_universe": bool(excess > 0) if eligible else None,
                })
                rows.append(record)
    return pd.DataFrame(rows)


def scenario_statistics(simulations: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []
    grouped = simulations.groupby(["scenario", "requested_capital", "horizon_days"], dropna=False)
    for index, ((scenario, capital, horizon), group) in enumerate(grouped):
        eligible = group[group["eligible"] == True].copy()
        excess = pd.to_numeric(eligible["capacity_excess_vs_universe"], errors="coerce").dropna()
        returns = pd.to_numeric(eligible["capacity_net_return"], errors="coerce").dropna()
        ci_low, ci_high = research_scorecard.bootstrap_mean_ci(excess, samples=2000, seed=5000 + index)
        p_value = robustness_analysis.sign_flip_p_value(excess, samples=5000, seed=7000 + index)
        requested_count = len(group)
        eligible_count = len(eligible)
        summary_rows.append({
            "scenario": scenario,
            "requested_capital": int(capital),
            "horizon_days": int(horizon),
            "requested_count": requested_count,
            "eligible_count": eligible_count,
            "eligibility_rate": eligible_count / requested_count if requested_count else 0.0,
            "average_quantity": float(pd.to_numeric(eligible.get("quantity"), errors="coerce").mean()) if eligible_count else None,
            "average_entry_participation": float(pd.to_numeric(eligible.get("entry_participation"), errors="coerce").mean()) if eligible_count else None,
            "p95_entry_participation": float(pd.to_numeric(eligible.get("entry_participation"), errors="coerce").quantile(0.95)) if eligible_count else None,
            "average_entry_impact_bps": float(pd.to_numeric(eligible.get("entry_impact_bps"), errors="coerce").mean()) if eligible_count else None,
            "average_exit_impact_bps": float(pd.to_numeric(eligible.get("exit_impact_bps"), errors="coerce").mean()) if eligible_count else None,
            "average_net_return": float(returns.mean()) if len(returns) else None,
            "median_net_return": float(returns.median()) if len(returns) else None,
            "positive_rate": float((returns > 0).mean()) if len(returns) else None,
            "average_excess_vs_universe": float(excess.mean()) if len(excess) else None,
            "median_excess_vs_universe": float(excess.median()) if len(excess) else None,
            "beat_universe_rate": float((excess > 0).mean()) if len(excess) else None,
            "excess_ci_low_95": ci_low,
            "excess_ci_high_95": ci_high,
            "one_sided_sign_flip_p_value": p_value,
        })
        rejection_counts = group.loc[group["eligible"] != True, "rejection_reason"].value_counts()
        for reason, count in rejection_counts.items():
            test_rows.append({
                "scenario": scenario,
                "requested_capital": int(capital),
                "horizon_days": int(horizon),
                "rejection_reason": reason,
                "count": int(count),
                "share_of_requested": int(count) / requested_count if requested_count else 0.0,
            })
    summary = pd.DataFrame(summary_rows)
    if not summary.empty:
        summary["fdr_q_value"] = robustness_analysis.benjamini_hochberg(summary["one_sided_sign_flip_p_value"])
        summary["scenario_status"] = summary.apply(
            lambda row: scenario_status(
                int(row["eligible_count"]),
                row.get("average_excess_vs_universe"),
                row.get("excess_ci_low_95"),
                row.get("fdr_q_value"),
                row.get("beat_universe_rate"),
            ),
            axis=1,
        )
    return summary, pd.DataFrame(test_rows)


def scenario_status(
    count: int,
    mean_excess: float | None,
    ci_low: float | None,
    q_value: float | None,
    beat_rate: float | None,
) -> str:
    if count < 30:
        return "INSUFFICIENT"
    numeric = [mean_excess, ci_low]
    if any(value is None or pd.isna(value) or float(value) <= 0 for value in numeric):
        return "FRAGILE"
    if q_value is None or pd.isna(q_value) or float(q_value) > 0.10:
        return "DEVELOPING"
    if count >= 100 and float(q_value) <= 0.05 and beat_rate is not None and float(beat_rate) >= 0.55:
        return "ROBUST"
    if count >= 50:
        return "PROMISING"
    return "DEVELOPING"


def build_frontier(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    eligible = summary[summary["eligible_count"] >= 30].copy()
    if eligible.empty:
        return eligible
    eligible["rank_score"] = (
        pd.to_numeric(eligible["average_excess_vs_universe"], errors="coerce").fillna(-999)
        + 0.10 * pd.to_numeric(eligible["eligibility_rate"], errors="coerce").fillna(0)
    )
    return (
        eligible.sort_values(
            ["horizon_days", "requested_capital", "rank_score", "eligible_count"],
            ascending=[True, True, False, False],
        )
        .groupby(["horizon_days", "requested_capital"], as_index=False)
        .head(3)
        .drop(columns=["rank_score"])
        .reset_index(drop=True)
    )


def write_outputs(
    simulations: pd.DataFrame,
    summary: pd.DataFrame,
    rejections: pd.DataFrame,
    output_dir: str,
    provenance_path: str,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    frontier = build_frontier(summary)
    paths = {
        "simulations": target / "capacity_scenario_outcomes.csv",
        "summary": target / "capacity_scenario_summary.csv",
        "rejections": target / "capacity_rejections.csv",
        "frontier": target / "capacity_frontier.csv",
        "excel": target / "capacity_analysis.xlsx",
        "manifest": target / "capacity_manifest.json",
    }
    simulations.to_csv(paths["simulations"], index=False)
    summary.to_csv(paths["summary"], index=False)
    rejections.to_csv(paths["rejections"], index=False)
    frontier.to_csv(paths["frontier"], index=False)
    manifest = {
        "capacity_version": CAPACITY_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "promotion_evidence_allowed": False,
        "scenario_count": len(SCENARIOS),
        "capital_scenarios": list(DEFAULT_CAPITALS),
        "lot_size": LOT_SIZE,
        "base_slippage_bps": BASE_SLIPPAGE_BPS,
        "impact_coefficient_bps": IMPACT_COEFFICIENT_BPS,
        "fees_bps": FEES_BPS,
        "impact_model": "HEURISTIC_BASE_PLUS_COEFFICIENT_TIMES_SQRT_PARTICIPATION",
        "portfolio_simulation": False,
        "automatic_strategy_change": False,
        "research_only": True,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Capacity Summary", index=False)
        summary.to_excel(writer, sheet_name="Scenario Summary", index=False)
        frontier.to_excel(writer, sheet_name="Research Frontier", index=False)
        rejections.to_excel(writer, sheet_name="Rejections", index=False)
        simulations.head(20000).to_excel(writer, sheet_name="Scenario Sample", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"manifest": manifest, "paths": {key: str(value) for key, value in paths.items()}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress-test execution capacity and liquidity")
    parser.add_argument("--outcomes", default=DEFAULT_OUTCOMES)
    parser.add_argument("--prices", default=DEFAULT_PRICES)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before = replay.live_state_hashes()
    outcomes = load_outcomes(args.outcomes)
    panel = load_price_panel(args.prices)
    simulations = simulate_capacity_scenarios(outcomes, panel)
    summary, rejections = scenario_statistics(simulations)
    result = write_outputs(simulations, summary, rejections, args.output_dir, args.provenance)
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    result["manifest"]["production_state_mutations"] = mutations
    Path(result["paths"]["manifest"]).write_text(
        json.dumps(result["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        if mutations:
            raise RuntimeError(f"production state mutated: {mutations}")
        if simulations.empty or summary.empty:
            raise RuntimeError("capacity analysis produced no results")
        if set(DEFAULT_CAPITALS) - set(summary["requested_capital"].astype(int)):
            raise RuntimeError("one or more capital scenarios are missing")
        if {scenario.name for scenario in SCENARIOS} - set(summary["scenario"]):
            raise RuntimeError("one or more execution scenarios are missing")
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
