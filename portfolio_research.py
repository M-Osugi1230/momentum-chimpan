"""Execution-aware, research-only portfolio simulation.

The simulator processes signals in chronological order, enters at the next
available adjusted open, sizes in 100-share lots, applies cash/position/sector/
risk/participation limits, and resolves exits from daily OHLC without using
future information. Historical current-universe evidence remains permanently
non-promotable and no production state is mutated.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import capacity_analysis
import main
import replay

PORTFOLIO_RESEARCH_VERSION = "2026-07-11-execution-portfolio-v1"
DEFAULT_SIGNALS = "output/backfill/replay/replay_signals.csv"
DEFAULT_PRICES = "output/backfill/historical_price_panel.csv"
DEFAULT_PROVENANCE = "output/backfill/replay/evidence_provenance.json"
DEFAULT_OUTPUT_DIR = "output/backfill/portfolio"

INITIAL_CAPITAL = 10_000_000.0
MAX_POSITIONS = 10
MAX_POSITION_WEIGHT = 0.12
MAX_SECTOR_WEIGHT = 0.25
RISK_PER_TRADE = 0.01
LOT_SIZE = 100
MAX_HOLDING_SESSIONS = 20
STOP_LOSS_PCT = 0.08
TARGET_GAIN_PCT = 0.15
TRAILING_STOP_PCT = 0.10
ROUND_TRIP_FEES_BPS = 20.0
HALF_FEES_BPS = ROUND_TRIP_FEES_BPS / 2


@dataclass(frozen=True)
class PortfolioScenario:
    name: str
    maximum_positive_gap: float | None
    minimum_entry_trading_value: float
    maximum_participation: float


SCENARIOS: tuple[PortfolioScenario, ...] = (
    PortfolioScenario("baseline", None, 0.0, 0.01),
    PortfolioScenario("no_gap_chase_3pct", 0.03, 0.0, 0.01),
    PortfolioScenario("gap3_minimum_500m", 0.03, 500_000_000.0, 0.01),
)

PRIORITY_ORDER = {"最優先": 0, "優先": 1, "監視": 2, "見送り": 3}


def load_signals(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(target, dtype={"code": str})
    required = {"signal_date", "code", "name", "sector33", "entry_close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"signals missing columns: {missing}")
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce")
    frame["code"] = frame["code"].map(main.normalize_code)
    frame["entry_close"] = pd.to_numeric(frame["entry_close"], errors="coerce")
    if "sector_leader_score" not in frame.columns:
        frame["sector_leader_score"] = 0.0
    frame["sector_leader_score"] = pd.to_numeric(frame["sector_leader_score"], errors="coerce").fillna(0)
    if "sector_research_priority" not in frame.columns:
        frame["sector_research_priority"] = "優先"
    return frame.dropna(subset=["signal_date", "code", "entry_close"]).copy()


def load_prices(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(target, dtype={"code": str})
    required = {
        "date", "code", "sector33", "adjusted_open", "adjusted_high",
        "adjusted_low", "adjusted_close", "raw_trading_value",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"price panel missing columns: {missing}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["code"] = frame["code"].map(main.normalize_code)
    for column in (
        "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close",
        "raw_trading_value",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=[
        "date", "code", "adjusted_open", "adjusted_high", "adjusted_low",
        "adjusted_close", "raw_trading_value",
    ])
    frame = frame[
        (frame["adjusted_open"] > 0)
        & (frame["adjusted_high"] > 0)
        & (frame["adjusted_low"] > 0)
        & (frame["adjusted_close"] > 0)
        & (frame["raw_trading_value"] > 0)
    ]
    return frame.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"]).reset_index(drop=True)


def build_entry_events(signals: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    price_dates = {
        code: group["date"].sort_values().drop_duplicates().to_numpy(dtype="datetime64[ns]")
        for code, group in prices.groupby("code", sort=False)
    }
    rows: list[dict[str, Any]] = []
    for _, signal in signals.iterrows():
        code = main.normalize_code(signal.get("code"))
        dates = price_dates.get(code)
        if dates is None or len(dates) == 0:
            continue
        signal_date = pd.Timestamp(signal["signal_date"]).normalize()
        index = int(np.searchsorted(dates, np.datetime64(signal_date), side="right"))
        if index >= len(dates):
            continue
        row = signal.to_dict()
        row["signal_date"] = signal_date
        row["entry_date"] = pd.Timestamp(dates[index]).normalize()
        row["priority_order"] = PRIORITY_ORDER.get(str(signal.get("sector_research_priority", "優先")), 9)
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    events = pd.DataFrame(rows)
    events = events.sort_values(
        ["entry_date", "priority_order", "sector_leader_score", "code"],
        ascending=[True, True, False, True],
    )
    return events.drop_duplicates(["entry_date", "code"], keep="first").reset_index(drop=True)


def market_benchmark_curve(prices: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    pivot = prices.pivot(index="date", columns="code", values="adjusted_close").sort_index()
    returns = pivot.pct_change(fill_method=None)
    daily_return = returns.mean(axis=1, skipna=True).fillna(0.0)
    equity = initial_capital * (1 + daily_return).cumprod()
    return pd.DataFrame({
        "date": equity.index,
        "benchmark_daily_return": daily_return.values,
        "benchmark_equity": equity.values,
    })


def floor_lot(value: float) -> int:
    if value <= 0 or not math.isfinite(value):
        return 0
    return int(value // LOT_SIZE) * LOT_SIZE


def dynamic_impact_bps(notional: float, trading_value: float) -> float:
    if notional <= 0 or trading_value <= 0:
        return float("nan")
    return capacity_analysis.impact_bps(notional / trading_value)


def entry_rejection(
    scenario: PortfolioScenario,
    gap: float,
    trading_value: float,
) -> str:
    if scenario.maximum_positive_gap is not None and max(gap, 0.0) > scenario.maximum_positive_gap:
        return "POSITIVE_GAP_ABOVE_LIMIT"
    if trading_value < scenario.minimum_entry_trading_value:
        return "ENTRY_TRADING_VALUE_BELOW_MINIMUM"
    return ""


def execution_price(raw_price: float, impact_bps: float, side: str) -> float:
    total_bps = impact_bps + HALF_FEES_BPS
    if side == "buy":
        return raw_price * (1 + total_bps / 10_000)
    return raw_price * (1 - total_bps / 10_000)


def current_equity_at_open(
    cash: float,
    positions: dict[str, dict[str, Any]],
    rows_by_code: dict[str, dict[str, Any]],
) -> float:
    value = cash
    for code, position in positions.items():
        row = rows_by_code.get(code)
        mark = float(row["adjusted_open"]) if row is not None else float(position["last_close"])
        value += int(position["quantity"]) * mark
    return value


def sector_value_at_open(
    sector: str,
    positions: dict[str, dict[str, Any]],
    rows_by_code: dict[str, dict[str, Any]],
) -> float:
    total = 0.0
    for code, position in positions.items():
        if main.normalize_sector33(position.get("sector33")) != sector:
            continue
        row = rows_by_code.get(code)
        mark = float(row["adjusted_open"]) if row is not None else float(position["last_close"])
        total += int(position["quantity"]) * mark
    return total


def resolve_exit(
    position: dict[str, Any],
    price_row: dict[str, Any],
    report_day: bool,
    active_codes: set[str],
) -> tuple[str, float] | None:
    open_price = float(price_row["adjusted_open"])
    high = float(price_row["adjusted_high"])
    low = float(price_row["adjusted_low"])
    close = float(price_row["adjusted_close"])
    trailing_stop = float(position["highest_close"]) * (1 - TRAILING_STOP_PCT)
    effective_stop = max(float(position["fixed_stop"]), trailing_stop)
    target = float(position["target_price"])
    stop_hit = low <= effective_stop
    target_hit = high >= target
    if stop_hit:
        # If stop and target are both inside the same daily bar, use the
        # conservative stop-first assumption. Gap-down fills at the open.
        return ("STOP_CONSERVATIVE" if target_hit else "STOP", min(open_price, effective_stop))
    if target_hit:
        return "TARGET", max(open_price, target)
    if int(position["holding_sessions"]) >= MAX_HOLDING_SESSIONS:
        return "TIME_EXIT", close
    if report_day and position["code"] not in active_codes:
        return "SIGNAL_EXIT", close
    return None


def simulate_scenario(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    scenario: PortfolioScenario,
    initial_capital: float = INITIAL_CAPITAL,
) -> dict[str, pd.DataFrame | dict[str, Any]]:
    events = build_entry_events(signals, prices)
    report_dates = set(pd.to_datetime(signals["signal_date"], errors="coerce").dropna().dt.normalize())
    active_codes_by_report = {
        pd.Timestamp(date).normalize(): set(group["code"].map(main.normalize_code))
        for date, group in signals.groupby(pd.to_datetime(signals["signal_date"], errors="coerce").dt.normalize())
    }
    price_by_date = {
        pd.Timestamp(date).normalize(): {
            main.normalize_code(row["code"]): row.to_dict()
            for _, row in group.iterrows()
        }
        for date, group in prices.groupby("date")
    }
    events_by_date = {
        pd.Timestamp(date).normalize(): group.copy()
        for date, group in events.groupby("entry_date")
    } if not events.empty else {}
    dates = sorted(price_by_date)
    if not dates:
        return {
            "trades": pd.DataFrame(),
            "equity": pd.DataFrame(),
            "skips": pd.DataFrame(),
            "positions": pd.DataFrame(),
            "metrics": {},
        }
    last_date = dates[-1]
    cash = float(initial_capital)
    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    cumulative_turnover = 0.0

    for current_date in dates:
        rows_by_code = price_by_date[current_date]
        report_day = current_date in report_dates
        active_codes = active_codes_by_report.get(current_date, set())

        # Exits are evaluated before new entries so released cash is available.
        for code in list(positions):
            position = positions[code]
            price_row = rows_by_code.get(code)
            if price_row is None:
                continue
            exit_signal = resolve_exit(position, price_row, report_day, active_codes)
            if exit_signal is None and current_date == last_date:
                exit_signal = ("END_OF_SAMPLE", float(price_row["adjusted_close"]))
            if exit_signal is None:
                position["highest_close"] = max(float(position["highest_close"]), float(price_row["adjusted_close"]))
                position["last_close"] = float(price_row["adjusted_close"])
                position["holding_sessions"] = int(position["holding_sessions"]) + 1
                continue
            exit_reason, raw_exit = exit_signal
            quantity = int(position["quantity"])
            raw_notional = quantity * raw_exit
            trading_value = float(price_row["raw_trading_value"])
            participation = raw_notional / trading_value
            impact = dynamic_impact_bps(raw_notional, trading_value)
            effective_exit = execution_price(raw_exit, impact, "sell")
            proceeds = quantity * effective_exit
            cash += proceeds
            cumulative_turnover += raw_notional
            pnl = quantity * (effective_exit - float(position["effective_entry_price"]))
            trades.append({
                **position,
                "exit_date": current_date.date().isoformat(),
                "exit_reason": exit_reason,
                "raw_exit_price": raw_exit,
                "effective_exit_price": effective_exit,
                "exit_trading_value": trading_value,
                "exit_participation": participation,
                "exit_impact_bps": impact,
                "proceeds": proceeds,
                "realized_pnl": pnl,
                "realized_return": effective_exit / float(position["effective_entry_price"]) - 1,
            })
            del positions[code]

        # Process ranked candidates whose tradable entry is today's open.
        if current_date != last_date and current_date in events_by_date:
            candidates = events_by_date[current_date]
            for _, signal in candidates.iterrows():
                code = main.normalize_code(signal.get("code"))
                priority = str(signal.get("sector_research_priority", "優先"))
                if priority not in {"最優先", "優先"}:
                    skips.append({"date": current_date.date().isoformat(), "code": code, "reason": "PRIORITY_NOT_ELIGIBLE", "scenario": scenario.name})
                    continue
                if code in positions:
                    skips.append({"date": current_date.date().isoformat(), "code": code, "reason": "ALREADY_HELD", "scenario": scenario.name})
                    continue
                if len(positions) >= MAX_POSITIONS:
                    skips.append({"date": current_date.date().isoformat(), "code": code, "reason": "MAX_POSITIONS", "scenario": scenario.name})
                    continue
                price_row = rows_by_code.get(code)
                if price_row is None:
                    skips.append({"date": current_date.date().isoformat(), "code": code, "reason": "MISSING_ENTRY_PRICE", "scenario": scenario.name})
                    continue
                raw_entry = float(price_row["adjusted_open"])
                entry_tv = float(price_row["raw_trading_value"])
                signal_close = float(signal["entry_close"])
                gap = raw_entry / signal_close - 1
                rejection = entry_rejection(scenario, gap, entry_tv)
                if rejection:
                    skips.append({"date": current_date.date().isoformat(), "code": code, "reason": rejection, "scenario": scenario.name})
                    continue

                equity_open = current_equity_at_open(cash, positions, rows_by_code)
                sector = main.normalize_sector33(signal.get("sector33"))
                sector_value = sector_value_at_open(sector, positions, rows_by_code)
                position_notional_limit = equity_open * MAX_POSITION_WEIGHT
                sector_notional_limit = max(equity_open * MAX_SECTOR_WEIGHT - sector_value, 0.0)
                risk_quantity = equity_open * RISK_PER_TRADE / (raw_entry * STOP_LOSS_PCT)
                position_quantity = position_notional_limit / raw_entry
                sector_quantity = sector_notional_limit / raw_entry
                participation_quantity = entry_tv * scenario.maximum_participation / raw_entry
                cash_quantity = cash / raw_entry
                quantity = floor_lot(min(
                    risk_quantity,
                    position_quantity,
                    sector_quantity,
                    participation_quantity,
                    cash_quantity,
                ))
                if quantity < LOT_SIZE:
                    skips.append({"date": current_date.date().isoformat(), "code": code, "reason": "ORDER_BELOW_100_SHARE_LOT", "scenario": scenario.name})
                    continue
                raw_notional = quantity * raw_entry
                participation = raw_notional / entry_tv
                impact = dynamic_impact_bps(raw_notional, entry_tv)
                effective_entry = execution_price(raw_entry, impact, "buy")
                cost = quantity * effective_entry
                while quantity >= LOT_SIZE and cost > cash:
                    quantity -= LOT_SIZE
                    raw_notional = quantity * raw_entry
                    participation = raw_notional / entry_tv if quantity else 0.0
                    impact = dynamic_impact_bps(raw_notional, entry_tv) if quantity else float("nan")
                    effective_entry = execution_price(raw_entry, impact, "buy") if quantity else float("nan")
                    cost = quantity * effective_entry if quantity else 0.0
                if quantity < LOT_SIZE:
                    skips.append({"date": current_date.date().isoformat(), "code": code, "reason": "INSUFFICIENT_CASH_AFTER_COSTS", "scenario": scenario.name})
                    continue
                cash -= cost
                cumulative_turnover += raw_notional
                positions[code] = {
                    "scenario": scenario.name,
                    "code": code,
                    "name": str(signal.get("name", "")),
                    "sector33": sector,
                    "signal_date": pd.Timestamp(signal["signal_date"]).date().isoformat(),
                    "entry_date": current_date.date().isoformat(),
                    "sector_research_priority": priority,
                    "sector_leader_score": float(signal.get("sector_leader_score", 0) or 0),
                    "sector_rotation": str(signal.get("sector_rotation", "")),
                    "quantity": quantity,
                    "raw_entry_price": raw_entry,
                    "effective_entry_price": effective_entry,
                    "entry_trading_value": entry_tv,
                    "entry_participation": participation,
                    "entry_impact_bps": impact,
                    "cost_basis": cost,
                    "fixed_stop": raw_entry * (1 - STOP_LOSS_PCT),
                    "target_price": raw_entry * (1 + TARGET_GAIN_PCT),
                    "highest_close": float(price_row["adjusted_close"]),
                    "last_close": float(price_row["adjusted_close"]),
                    "holding_sessions": 1,
                    "entry_gap_return": gap,
                }

        market_value = 0.0
        sector_values: dict[str, float] = {}
        for code, position in positions.items():
            price_row = rows_by_code.get(code)
            mark = float(price_row["adjusted_close"]) if price_row is not None else float(position["last_close"])
            position["last_close"] = mark
            value = int(position["quantity"]) * mark
            market_value += value
            sector = main.normalize_sector33(position.get("sector33"))
            sector_values[sector] = sector_values.get(sector, 0.0) + value
        equity = cash + market_value
        equity_rows.append({
            "scenario": scenario.name,
            "date": current_date.date().isoformat(),
            "cash": cash,
            "market_value": market_value,
            "equity": equity,
            "open_positions": len(positions),
            "exposure_ratio": market_value / equity if equity else 0.0,
            "largest_sector_weight": max(sector_values.values(), default=0.0) / equity if equity else 0.0,
            "cumulative_turnover": cumulative_turnover,
        })

    equity_frame = pd.DataFrame(equity_rows)
    benchmark = market_benchmark_curve(prices, initial_capital)
    if not equity_frame.empty:
        equity_frame["date_dt"] = pd.to_datetime(equity_frame["date"])
        equity_frame = equity_frame.merge(benchmark, left_on="date_dt", right_on="date", how="left", suffixes=("", "_benchmark"))
        equity_frame = equity_frame.drop(columns=["date_dt", "date_benchmark"], errors="ignore")
        equity_frame["peak_equity"] = equity_frame["equity"].cummax()
        equity_frame["drawdown"] = equity_frame["equity"] / equity_frame["peak_equity"] - 1
        equity_frame["daily_return"] = equity_frame["equity"].pct_change().fillna(0.0)
    trades_frame = pd.DataFrame(trades)
    skips_frame = pd.DataFrame(skips)
    positions_frame = pd.DataFrame(list(positions.values()))
    metrics = calculate_metrics(equity_frame, trades_frame, initial_capital, cumulative_turnover)
    metrics.update({"scenario": scenario.name, **asdict(scenario)})
    return {
        "trades": trades_frame,
        "equity": equity_frame,
        "skips": skips_frame,
        "positions": positions_frame,
        "metrics": metrics,
    }


def calculate_metrics(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    initial_capital: float,
    cumulative_turnover: float,
) -> dict[str, Any]:
    if equity.empty:
        return {
            "initial_capital": initial_capital,
            "final_equity": initial_capital,
            "total_return": 0.0,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "annualized_volatility": 0.0,
            "sharpe": None,
            "benchmark_total_return": 0.0,
            "excess_total_return": 0.0,
            "closed_trades": 0,
            "win_rate": None,
            "profit_factor": None,
            "turnover_ratio": 0.0,
        }
    final_equity = float(equity.iloc[-1]["equity"])
    total_return = final_equity / initial_capital - 1
    sessions = max(len(equity) - 1, 1)
    cagr = (final_equity / initial_capital) ** (252 / sessions) - 1 if final_equity > 0 else -1.0
    daily = pd.to_numeric(equity["daily_return"], errors="coerce").dropna()
    volatility = float(daily.std(ddof=1) * math.sqrt(252)) if len(daily) > 1 else 0.0
    sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252)) if len(daily) > 1 and daily.std(ddof=1) > 0 else None
    benchmark_final = float(pd.to_numeric(equity["benchmark_equity"], errors="coerce").dropna().iloc[-1]) if equity["benchmark_equity"].notna().any() else initial_capital
    benchmark_return = benchmark_final / initial_capital - 1
    realized = pd.to_numeric(trades.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").dropna()
    gross_profit = float(realized[realized > 0].sum()) if len(realized) else 0.0
    gross_loss = float(-realized[realized < 0].sum()) if len(realized) else 0.0
    return {
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": float(pd.to_numeric(equity["drawdown"], errors="coerce").min()),
        "annualized_volatility": volatility,
        "sharpe": sharpe,
        "benchmark_total_return": benchmark_return,
        "excess_total_return": total_return - benchmark_return,
        "closed_trades": len(trades),
        "win_rate": float((realized > 0).mean()) if len(realized) else None,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "average_trade_return": float(pd.to_numeric(trades.get("realized_return", pd.Series(dtype=float)), errors="coerce").mean()) if len(trades) else None,
        "average_holding_sessions": float(pd.to_numeric(trades.get("holding_sessions", pd.Series(dtype=float)), errors="coerce").mean()) if len(trades) else None,
        "turnover_ratio": cumulative_turnover / initial_capital,
        "average_exposure": float(pd.to_numeric(equity["exposure_ratio"], errors="coerce").mean()),
        "maximum_positions": int(pd.to_numeric(equity["open_positions"], errors="coerce").max()),
        "maximum_sector_weight": float(pd.to_numeric(equity["largest_sector_weight"], errors="coerce").max()),
    }


def run_all_scenarios(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    scenarios: tuple[PortfolioScenario, ...] = SCENARIOS,
) -> dict[str, pd.DataFrame]:
    metrics: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    equity_frames: list[pd.DataFrame] = []
    skip_frames: list[pd.DataFrame] = []
    for scenario in scenarios:
        result = simulate_scenario(signals, prices, scenario)
        metrics.append(result["metrics"])
        if not result["trades"].empty:
            trade_frames.append(result["trades"])
        if not result["equity"].empty:
            equity_frames.append(result["equity"])
        if not result["skips"].empty:
            skip_frames.append(result["skips"])
    return {
        "metrics": pd.DataFrame(metrics),
        "trades": pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame(),
        "equity": pd.concat(equity_frames, ignore_index=True) if equity_frames else pd.DataFrame(),
        "skips": pd.concat(skip_frames, ignore_index=True) if skip_frames else pd.DataFrame(),
    }


def write_outputs(results: dict[str, pd.DataFrame], provenance_path: str, output_dir: str) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    paths = {
        "metrics": output / "portfolio_scenario_metrics.csv",
        "trades": output / "portfolio_trades.csv",
        "equity": output / "portfolio_equity_curve.csv",
        "skips": output / "portfolio_skipped_entries.csv",
        "excel": output / "portfolio_research.xlsx",
        "manifest": output / "portfolio_research_manifest.json",
    }
    for key in ("metrics", "trades", "equity", "skips"):
        results[key].to_csv(paths[key], index=False)
    manifest = {
        "portfolio_research_version": PORTFOLIO_RESEARCH_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "promotion_evidence_allowed": False,
        "initial_capital": INITIAL_CAPITAL,
        "scenario_count": len(SCENARIOS),
        "max_positions": MAX_POSITIONS,
        "max_position_weight": MAX_POSITION_WEIGHT,
        "max_sector_weight": MAX_SECTOR_WEIGHT,
        "risk_per_trade": RISK_PER_TRADE,
        "lot_size": LOT_SIZE,
        "maximum_holding_sessions": MAX_HOLDING_SESSIONS,
        "stop_loss_pct": STOP_LOSS_PCT,
        "target_gain_pct": TARGET_GAIN_PCT,
        "trailing_stop_pct": TRAILING_STOP_PCT,
        "round_trip_fees_bps": ROUND_TRIP_FEES_BPS,
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "intraday_ambiguity_policy": "STOP_FIRST_CONSERVATIVE",
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "research_only": True,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Research Summary", index=False)
        results["metrics"].to_excel(writer, sheet_name="Scenario Metrics", index=False)
        results["trades"].to_excel(writer, sheet_name="Trades", index=False)
        results["equity"].to_excel(writer, sheet_name="Equity Curve", index=False)
        results["skips"].to_excel(writer, sheet_name="Skipped Entries", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"manifest": manifest, "paths": {key: str(path) for key, path in paths.items()}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run execution-aware portfolio research")
    parser.add_argument("--signals", default=DEFAULT_SIGNALS)
    parser.add_argument("--prices", default=DEFAULT_PRICES)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before = replay.live_state_hashes()
    signals = load_signals(args.signals)
    prices = load_prices(args.prices)
    results = run_all_scenarios(signals, prices)
    output = write_outputs(results, args.provenance, args.output_dir)
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    output["manifest"]["production_state_mutations"] = mutations
    Path(output["paths"]["manifest"]).write_text(
        json.dumps(output["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        if mutations:
            raise RuntimeError(f"production state mutated: {mutations}")
        if results["metrics"].empty:
            raise RuntimeError("portfolio research produced no scenario metrics")
        if set(results["metrics"]["scenario"]) != {scenario.name for scenario in SCENARIOS}:
            raise RuntimeError("one or more portfolio scenarios are missing")
        if (pd.to_numeric(results["metrics"]["maximum_positions"], errors="coerce") > MAX_POSITIONS).any():
            raise RuntimeError("portfolio exceeded maximum position count")
        if (pd.to_numeric(results["metrics"]["maximum_sector_weight"], errors="coerce") > MAX_SECTOR_WEIGHT + 0.01).any():
            raise RuntimeError("portfolio materially exceeded sector weight")
    print(json.dumps(output["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
