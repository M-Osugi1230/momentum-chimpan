"""Freshness-safe extended-horizon entrypoint for historical OOS analysis."""
from __future__ import annotations

import numpy as np
import pandas as pd

import analyze_historical_oos as analysis
import run_historical_oos_analysis as safe

DETAILED_HORIZONS = (1, 3, 5, 10, 20, 40, 60)
MAX_ENTRY_GAP_DAYS = 7
MAX_SESSION_GAP_DAYS = 10
MAX_ADJACENT_PRICE_MULTIPLIER = 4.0


def one_outcome_strict(
    prices: pd.DataFrame,
    signal_date: pd.Timestamp,
    horizon: int,
) -> dict | None:
    dates = prices["date"].to_numpy(dtype="datetime64[ns]")
    entry_position = int(np.searchsorted(dates, np.datetime64(signal_date), side="right"))
    exit_position = entry_position + int(horizon) - 1
    if entry_position >= len(prices) or exit_position >= len(prices):
        return None
    window = prices.iloc[entry_position : exit_position + 1].copy()
    entry = window.iloc[0]
    exit_row = window.iloc[-1]
    entry_date = pd.Timestamp(entry["date"])
    entry_gap_days = int((entry_date.normalize() - pd.Timestamp(signal_date).normalize()).days)
    if entry_gap_days < 1 or entry_gap_days > MAX_ENTRY_GAP_DAYS:
        return None
    volume = pd.to_numeric(window.get("volume"), errors="coerce")
    if volume is not None and (volume.isna().any() or volume.le(0).any()):
        return None
    session_gaps = pd.to_datetime(window["date"], errors="coerce").diff().dt.days.dropna()
    max_session_gap_days = int(session_gaps.max()) if len(session_gaps) else 0
    if max_session_gap_days > MAX_SESSION_GAP_DAYS:
        return None
    closes = pd.to_numeric(window["adjusted_close"], errors="coerce")
    adjacent_ratio = closes / closes.shift(1)
    valid_ratio = adjacent_ratio.dropna()
    if (
        valid_ratio.gt(MAX_ADJACENT_PRICE_MULTIPLIER).any()
        or valid_ratio.lt(1.0 / MAX_ADJACENT_PRICE_MULTIPLIER).any()
    ):
        return None
    entry_price = float(entry["adjusted_open"])
    exit_price = float(exit_row["adjusted_close"])
    if not np.isfinite(entry_price) or not np.isfinite(exit_price) or entry_price <= 0:
        return None
    gross_return = exit_price / entry_price - 1.0
    maximum_high = float(pd.to_numeric(window["adjusted_high"], errors="coerce").max())
    minimum_low = float(pd.to_numeric(window["adjusted_low"], errors="coerce").min())
    return {
        "entry_date": entry_date,
        "entry_price": entry_price,
        "exit_date": pd.Timestamp(exit_row["date"]),
        "exit_price": exit_price,
        "gross_return": gross_return,
        "net_return": gross_return - analysis.ROUND_TRIP_COST_BPS / 10_000.0,
        "mfe": maximum_high / entry_price - 1.0,
        "mae": minimum_low / entry_price - 1.0,
        "entry_gap_days": entry_gap_days,
        "max_session_gap_days": max_session_gap_days,
        "outcome_data_quality": "OK",
    }


def main_cli() -> int:
    analysis.DEFAULT_HORIZONS = DETAILED_HORIZONS
    safe.analysis.DEFAULT_HORIZONS = DETAILED_HORIZONS
    analysis.one_outcome = one_outcome_strict
    return safe.main_cli()


if __name__ == "__main__":
    raise SystemExit(main_cli())
