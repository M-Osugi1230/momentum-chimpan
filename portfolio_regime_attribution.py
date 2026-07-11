"""Research-only attribution of execution-aware portfolio performance.

This module explains where the baseline portfolio made or lost money across
market regimes, sectors, relative-strength states, exit reasons and time
windows. It also runs pre-declared entry-only regime exclusions and leave-one-
sector-out counterfactuals. Historical current-universe results are permanently
non-promotable and no production state or strategy parameter is changed.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import main
import portfolio_filter_lab as filter_lab
import portfolio_research as portfolio
import replay

ATTRIBUTION_VERSION = "2026-07-11-portfolio-regime-attribution-v1"
DEFAULT_SIGNALS = "output/backfill/replay/replay_signals.csv"
DEFAULT_HISTORY = "output/backfill/historical_ranking.csv"
DEFAULT_PRICES = "output/backfill/historical_price_panel.csv"
DEFAULT_PROVENANCE = "output/backfill/replay/evidence_provenance.json"
DEFAULT_OUTPUT_DIR = "output/backfill/portfolio_attribution"
MAX_SECTOR_COUNTERFACTUALS = 10
ROLLING_SESSIONS = 63
ROLLING_STEP = 21

BASELINE_SCENARIO = portfolio.PortfolioScenario("attribution_baseline", None, 0.0, 0.01)
BASELINE_EXIT_POLICY = portfolio.ExitPolicy("baseline", 0.08, 0.15, 0.10, 20, True)
RISK_ON_REGIMES = {"強気", "やや強気"}
NON_WEAK_REGIMES = {"強気", "やや強気", "中立"}


def number(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def safe_profit_factor(pnl: pd.Series) -> float | None:
    values = pd.to_numeric(pnl, errors="coerce").dropna()
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    if gross_loss > 0:
        return gross_profit / gross_loss
    return None if gross_profit == 0 else float("inf")


def run_baseline(signals: pd.DataFrame, prices: pd.DataFrame) -> dict[str, Any]:
    prepared = signals.copy()
    prepared["portfolio_eligible"] = True
    prepared["portfolio_hold_eligible"] = True
    return portfolio.simulate_scenario(
        prepared,
        prices,
        BASELINE_SCENARIO,
        exit_policy=BASELINE_EXIT_POLICY,
    )


def enrich_trades(trades: pd.DataFrame, enriched_signals: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    result = trades.copy()
    result["signal_date_key"] = pd.to_datetime(result["signal_date"], errors="coerce").dt.date.astype(str)
    result["code"] = result["code"].map(main.normalize_code)
    context = enriched_signals.copy()
    context["signal_date_key"] = pd.to_datetime(context["signal_date"], errors="coerce").dt.date.astype(str)
    context["code"] = context["code"].map(main.normalize_code)
    context_columns = [
        "signal_date_key", "code", "market_regime", "market_risk_budget",
        "relative_strength_score", "relative_strength_rank", "relative_strength_grade",
        "relative_strength_lifecycle", "relative_strength_alert",
        "relative_strength_trajectory_score", "dual_outperformer",
        "sector_rotation", "sector_research_priority", "action_priority", "action_score",
    ]
    available = [column for column in context_columns if column in context.columns]
    context = context[available].drop_duplicates(["signal_date_key", "code"], keep="last")
    result = result.merge(context, on=["signal_date_key", "code"], how="left", suffixes=("", "_context"))
    for column in context_columns:
        context_column = f"{column}_context"
        if context_column not in result.columns:
            continue
        if column not in result.columns:
            result[column] = result[context_column]
        else:
            result[column] = result[column].where(result[column].notna(), result[context_column])
        result = result.drop(columns=[context_column])
    result["market_regime"] = result.get("market_regime", pd.Series(index=result.index, dtype=str)).fillna("未分類")
    result["relative_strength_lifecycle"] = result.get(
        "relative_strength_lifecycle", pd.Series(index=result.index, dtype=str)
    ).fillna("未分類")
    result["relative_strength_grade"] = result.get(
        "relative_strength_grade", pd.Series(index=result.index, dtype=str)
    ).fillna("未分類")
    return result


def attribution_record(dimension: str, value: str, group: pd.DataFrame) -> dict[str, Any]:
    returns = pd.to_numeric(group.get("realized_return"), errors="coerce").dropna()
    pnl = pd.to_numeric(group.get("realized_pnl"), errors="coerce").dropna()
    wins = returns > 0
    entry_notional = pd.to_numeric(group.get("cost_basis"), errors="coerce").dropna()
    return {
        "dimension": dimension,
        "value": value,
        "trade_count": int(len(returns)),
        "win_count": int(wins.sum()),
        "win_rate": float(wins.mean()) if len(wins) else None,
        "average_return": float(returns.mean()) if len(returns) else None,
        "median_return": float(returns.median()) if len(returns) else None,
        "best_return": float(returns.max()) if len(returns) else None,
        "worst_return": float(returns.min()) if len(returns) else None,
        "realized_pnl": float(pnl.sum()) if len(pnl) else 0.0,
        "pnl_contribution_ratio": None,
        "profit_factor": safe_profit_factor(pnl),
        "average_holding_sessions": float(pd.to_numeric(group.get("holding_sessions"), errors="coerce").mean()),
        "average_entry_gap": float(pd.to_numeric(group.get("entry_gap_return"), errors="coerce").mean()),
        "average_entry_impact_bps": float(pd.to_numeric(group.get("entry_impact_bps"), errors="coerce").mean()),
        "average_exit_impact_bps": float(pd.to_numeric(group.get("exit_impact_bps"), errors="coerce").mean()),
        "entry_notional": float(entry_notional.sum()) if len(entry_notional) else 0.0,
    }


def build_trade_attribution(trades: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "dimension", "value", "trade_count", "win_count", "win_rate",
        "average_return", "median_return", "best_return", "worst_return",
        "realized_pnl", "pnl_contribution_ratio", "profit_factor",
        "average_holding_sessions", "average_entry_gap",
        "average_entry_impact_bps", "average_exit_impact_bps", "entry_notional",
    ]
    if trades is None or trades.empty:
        return pd.DataFrame(columns=columns)
    records = [attribution_record("overall", "ALL", trades)]
    dimensions = [
        "market_regime", "sector33", "relative_strength_lifecycle",
        "relative_strength_grade", "exit_reason", "sector_rotation",
        "sector_research_priority",
    ]
    for dimension in dimensions:
        if dimension not in trades.columns:
            continue
        values = trades[dimension].fillna("未分類").astype(str).replace("", "未分類")
        for value, group in trades.assign(_group_value=values).groupby("_group_value", dropna=False):
            records.append(attribution_record(dimension, str(value), group))
    result = pd.DataFrame(records, columns=columns)
    total_pnl = float(pd.to_numeric(trades.get("realized_pnl"), errors="coerce").sum())
    if total_pnl != 0:
        result["pnl_contribution_ratio"] = result["realized_pnl"] / abs(total_pnl)
    return result.sort_values(["dimension", "realized_pnl", "trade_count"], ascending=[True, False, False]).reset_index(drop=True)


def attach_daily_regime(equity: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if equity is None or equity.empty:
        return pd.DataFrame()
    result = equity.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.normalize()
    regimes = filter_lab.build_market_regime_panel(history).copy()
    regimes["date"] = pd.to_datetime(regimes["signal_date"], errors="coerce").dt.normalize()
    regimes = regimes.sort_values("date")[["date", "market_regime", "market_risk_budget", "market_regime_reason"]]
    result = pd.merge_asof(result.sort_values("date"), regimes, on="date", direction="backward")
    result["market_regime"] = result["market_regime"].fillna("未分類")
    result["daily_return"] = pd.to_numeric(result.get("daily_return"), errors="coerce").fillna(0.0)
    result["benchmark_daily_return"] = pd.to_numeric(
        result.get("benchmark_daily_return"), errors="coerce"
    ).fillna(0.0)
    result["daily_excess_return"] = result["daily_return"] - result["benchmark_daily_return"]
    return result


def build_daily_regime_attribution(daily: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "market_regime", "session_count", "portfolio_return", "benchmark_return",
        "excess_return", "average_daily_return", "average_daily_excess_return",
        "annualized_volatility", "sharpe", "positive_session_rate",
        "average_exposure", "maximum_drawdown",
    ]
    if daily is None or daily.empty:
        return pd.DataFrame(columns=columns)
    records: list[dict[str, Any]] = []
    for regime, group in daily.groupby("market_regime", dropna=False):
        portfolio_returns = pd.to_numeric(group["daily_return"], errors="coerce").fillna(0.0)
        benchmark_returns = pd.to_numeric(group["benchmark_daily_return"], errors="coerce").fillna(0.0)
        portfolio_return = float((1 + portfolio_returns).prod() - 1)
        benchmark_return = float((1 + benchmark_returns).prod() - 1)
        volatility = float(portfolio_returns.std(ddof=1) * math.sqrt(252)) if len(group) > 1 else 0.0
        sharpe = (
            float(portfolio_returns.mean() / portfolio_returns.std(ddof=1) * math.sqrt(252))
            if len(group) > 1 and portfolio_returns.std(ddof=1) > 0 else None
        )
        local_equity = (1 + portfolio_returns).cumprod()
        local_drawdown = local_equity / local_equity.cummax() - 1
        records.append({
            "market_regime": str(regime),
            "session_count": len(group),
            "portfolio_return": portfolio_return,
            "benchmark_return": benchmark_return,
            "excess_return": portfolio_return - benchmark_return,
            "average_daily_return": float(portfolio_returns.mean()),
            "average_daily_excess_return": float((portfolio_returns - benchmark_returns).mean()),
            "annualized_volatility": volatility,
            "sharpe": sharpe,
            "positive_session_rate": float((portfolio_returns > 0).mean()),
            "average_exposure": float(pd.to_numeric(group.get("exposure_ratio"), errors="coerce").mean()),
            "maximum_drawdown": float(local_drawdown.min()),
        })
    return pd.DataFrame(records, columns=columns).sort_values("excess_return", ascending=False).reset_index(drop=True)


def period_record(label: str, group: pd.DataFrame) -> dict[str, Any]:
    group = group.sort_values("date")
    portfolio_returns = pd.to_numeric(group["daily_return"], errors="coerce").fillna(0.0)
    benchmark_returns = pd.to_numeric(group["benchmark_daily_return"], errors="coerce").fillna(0.0)
    portfolio_return = float((1 + portfolio_returns).prod() - 1)
    benchmark_return = float((1 + benchmark_returns).prod() - 1)
    local_equity = (1 + portfolio_returns).cumprod()
    drawdown = local_equity / local_equity.cummax() - 1
    return {
        "period": label,
        "start_date": group["date"].min().date().isoformat(),
        "end_date": group["date"].max().date().isoformat(),
        "session_count": len(group),
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return,
        "excess_return": portfolio_return - benchmark_return,
        "maximum_drawdown": float(drawdown.min()),
        "average_exposure": float(pd.to_numeric(group.get("exposure_ratio"), errors="coerce").mean()),
        "average_positions": float(pd.to_numeric(group.get("open_positions"), errors="coerce").mean()),
    }


def build_quarterly_stability(daily: pd.DataFrame) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame()
    work = daily.copy()
    work["quarter"] = work["date"].dt.to_period("Q").astype(str)
    return pd.DataFrame([period_record(str(quarter), group) for quarter, group in work.groupby("quarter")])


def build_rolling_stability(daily: pd.DataFrame) -> pd.DataFrame:
    if daily is None or daily.empty or len(daily) < ROLLING_SESSIONS:
        return pd.DataFrame()
    work = daily.sort_values("date").reset_index(drop=True)
    records: list[dict[str, Any]] = []
    for start in range(0, len(work) - ROLLING_SESSIONS + 1, ROLLING_STEP):
        window = work.iloc[start : start + ROLLING_SESSIONS]
        records.append(period_record(f"rolling_{start}_{start + ROLLING_SESSIONS - 1}", window))
    return pd.DataFrame(records)


def counterfactual_result(
    name: str,
    description: str,
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    eligible: pd.Series,
    baseline_metrics: dict[str, Any],
) -> dict[str, Any]:
    prepared = signals.copy()
    prepared["portfolio_eligible"] = eligible.fillna(False).astype(bool)
    # Entry attribution should not force a sale when the market regime or sector
    # label changes after entry. Existing risk/stop/signal exits remain active.
    prepared["portfolio_hold_eligible"] = True
    scenario = portfolio.PortfolioScenario(name, None, 0.0, 0.01)
    result = portfolio.simulate_scenario(prepared, prices, scenario, exit_policy=BASELINE_EXIT_POLICY)
    metrics = dict(result["metrics"])
    baseline_return = number(baseline_metrics.get("total_return"))
    baseline_excess = number(baseline_metrics.get("excess_total_return"))
    baseline_dd = number(baseline_metrics.get("max_drawdown"))
    total_return = number(metrics.get("total_return"))
    excess_return = number(metrics.get("excess_total_return"))
    drawdown = number(metrics.get("max_drawdown"))
    metrics.update({
        "counterfactual": name,
        "description": description,
        "eligible_signal_count": int(eligible.fillna(False).sum()),
        "total_signal_count": len(signals),
        "eligible_signal_ratio": float(eligible.fillna(False).mean()) if len(signals) else 0.0,
        "delta_total_return_vs_baseline": None if baseline_return is None or total_return is None else total_return - baseline_return,
        "delta_excess_return_vs_baseline": None if baseline_excess is None or excess_return is None else excess_return - baseline_excess,
        "delta_max_drawdown_vs_baseline": None if baseline_dd is None or drawdown is None else drawdown - baseline_dd,
    })
    return metrics


def run_counterfactuals(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    baseline_metrics: dict[str, Any],
) -> pd.DataFrame:
    regimes = signals.get("market_regime", pd.Series("未分類", index=signals.index)).fillna("未分類").astype(str)
    sectors = signals.get("sector33", pd.Series("未分類", index=signals.index)).fillna("未分類").astype(str)
    records = [counterfactual_result(
        "baseline", "全シグナル", signals, prices,
        pd.Series(True, index=signals.index), baseline_metrics,
    )]
    records.append(counterfactual_result(
        "risk_on_entries", "強気・やや強気の日だけ新規entry", signals, prices,
        regimes.isin(RISK_ON_REGIMES), baseline_metrics,
    ))
    records.append(counterfactual_result(
        "non_weak_entries", "強気・やや強気・中立の日だけ新規entry", signals, prices,
        regimes.isin(NON_WEAK_REGIMES), baseline_metrics,
    ))
    regime_counts = regimes.value_counts()
    for regime in regime_counts.index:
        records.append(counterfactual_result(
            f"exclude_regime_{regime}", f"{regime}の日の新規entryを除外", signals, prices,
            ~regimes.eq(regime), baseline_metrics,
        ))
    sector_counts = sectors.value_counts().head(MAX_SECTOR_COUNTERFACTUALS)
    for sector in sector_counts.index:
        records.append(counterfactual_result(
            f"exclude_sector_{sector}", f"{sector}を除外", signals, prices,
            ~sectors.eq(sector), baseline_metrics,
        ))
    result = pd.DataFrame(records)
    result["sample_status"] = pd.to_numeric(result.get("closed_trades"), errors="coerce").fillna(0).map(
        lambda value: "INSUFFICIENT" if int(value) < 12 else "EVALUABLE"
    )
    result["diagnostic_status"] = "NO_CLEAR_IMPROVEMENT"
    improvement = (
        result["sample_status"].eq("EVALUABLE")
        & pd.to_numeric(result["delta_excess_return_vs_baseline"], errors="coerce").gt(0)
        & pd.to_numeric(result["delta_max_drawdown_vs_baseline"], errors="coerce").ge(0)
    )
    result.loc[improvement, "diagnostic_status"] = "RETURN_AND_DD_IMPROVED"
    result.loc[result["counterfactual"].eq("baseline"), "diagnostic_status"] = "BASELINE"
    return result.sort_values(
        ["diagnostic_status", "excess_total_return", "max_drawdown"],
        ascending=[True, False, False],
    ).reset_index(drop=True)


def run_attribution(signals: pd.DataFrame, history: pd.DataFrame, prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    enriched_signals, coverage = filter_lab.attach_filter_context(signals, history)
    baseline = run_baseline(enriched_signals, prices)
    trades = enrich_trades(baseline["trades"], enriched_signals)
    daily = attach_daily_regime(baseline["equity"], history)
    return {
        "baseline_metrics": pd.DataFrame([baseline["metrics"]]),
        "trades": trades,
        "trade_attribution": build_trade_attribution(trades),
        "daily_equity": daily,
        "daily_regime_attribution": build_daily_regime_attribution(daily),
        "quarterly_stability": build_quarterly_stability(daily),
        "rolling_stability": build_rolling_stability(daily),
        "counterfactuals": run_counterfactuals(enriched_signals, prices, baseline["metrics"]),
        "context_coverage": coverage,
    }


def write_outputs(results: dict[str, pd.DataFrame], provenance_path: str, output_dir: str) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    paths = {
        "baseline": output / "portfolio_attribution_baseline.csv",
        "trades": output / "portfolio_attribution_trades.csv",
        "trade_attribution": output / "portfolio_trade_attribution.csv",
        "daily": output / "portfolio_daily_regime_panel.csv",
        "daily_regime": output / "portfolio_daily_regime_attribution.csv",
        "quarterly": output / "portfolio_quarterly_stability.csv",
        "rolling": output / "portfolio_rolling_stability.csv",
        "counterfactuals": output / "portfolio_regime_sector_counterfactuals.csv",
        "coverage": output / "portfolio_attribution_context_coverage.csv",
        "excel": output / "portfolio_regime_attribution.xlsx",
        "manifest": output / "portfolio_regime_attribution_manifest.json",
    }
    mapping = {
        "baseline": "baseline_metrics", "trades": "trades",
        "trade_attribution": "trade_attribution", "daily": "daily_equity",
        "daily_regime": "daily_regime_attribution", "quarterly": "quarterly_stability",
        "rolling": "rolling_stability", "counterfactuals": "counterfactuals",
        "coverage": "context_coverage",
    }
    for path_key, result_key in mapping.items():
        results[result_key].to_csv(paths[path_key], index=False)
    manifest = {
        "attribution_version": ATTRIBUTION_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "portfolio_engine_version": portfolio.PORTFOLIO_RESEARCH_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "promotion_evidence_allowed": False,
        "automatic_regime_filter_activation": False,
        "automatic_sector_exclusion": False,
        "automatic_strategy_change": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "rolling_sessions": ROLLING_SESSIONS,
        "rolling_step": ROLLING_STEP,
        "maximum_sector_counterfactuals": MAX_SECTOR_COUNTERFACTUALS,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    sheet_map = {
        "Summary": "baseline_metrics", "Trades": "trades", "Trade Attribution": "trade_attribution",
        "Daily Regime": "daily_regime_attribution", "Quarterly": "quarterly_stability",
        "Rolling 63D": "rolling_stability", "Counterfactuals": "counterfactuals",
        "Context Coverage": "context_coverage",
    }
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Manifest", index=False)
        for sheet, result_key in sheet_map.items():
            results[result_key].to_excel(writer, sheet_name=sheet, index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"manifest": manifest, "paths": {key: str(value) for key, value in paths.items()}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attribute portfolio performance by regime and sector")
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
    results = run_attribution(signals, history, prices)
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
        if float(coverage["relative_strength_score_coverage"]) < 0.99:
            raise RuntimeError("relative strength context coverage below 99%")
        if float(coverage["market_regime_coverage"]) < 0.99:
            raise RuntimeError("market regime context coverage below 99%")
        if results["baseline_metrics"].empty or results["daily_equity"].empty:
            raise RuntimeError("baseline attribution is empty")
        if results["counterfactuals"].empty:
            raise RuntimeError("counterfactual attribution is empty")
        if not results["trades"].empty and pd.to_datetime(results["trades"]["entry_date"]).le(
            pd.to_datetime(results["trades"]["signal_date"])
        ).any():
            raise RuntimeError("same-day or pre-signal entry detected")
    print(results["baseline_metrics"].to_string(index=False))
    print(results["daily_regime_attribution"].to_string(index=False))
    print(results["counterfactuals"][[
        "counterfactual", "closed_trades", "total_return", "max_drawdown",
        "excess_total_return", "delta_excess_return_vs_baseline",
        "delta_max_drawdown_vs_baseline", "diagnostic_status",
    ]].to_string(index=False))
    print(json.dumps(output["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
