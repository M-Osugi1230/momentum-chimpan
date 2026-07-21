#!/usr/bin/env python3
"""Refresh the complete historical-pick audit with the latest available closes.

This utility reads the audit CSVs, fetches recent market closes in batches from yfinance,
and writes separate ``*_latest`` audit files. It never changes production state.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().split(".")[0]
    return text.zfill(4) if text else ""


def extract_batch_closes(data: pd.DataFrame, tickers: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if data is None or data.empty:
        return rows
    for ticker in tickers:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if ticker in data.columns.get_level_values(0):
                    block = data[ticker]
                elif ticker in data.columns.get_level_values(-1):
                    block = data.xs(ticker, axis=1, level=-1)
                else:
                    continue
                close = block["Close"] if "Close" in block.columns else None
            else:
                close = data["Close"] if len(tickers) == 1 and "Close" in data.columns else None
            if close is None:
                continue
            close = pd.to_numeric(close, errors="coerce").dropna()
            for date, value in close.items():
                rows.append({
                    "code": ticker.split(".")[0].zfill(4),
                    "price_date": pd.Timestamp(date).tz_localize(None).normalize(),
                    "close": float(value),
                    "price_source": "yfinance",
                })
        except Exception:
            continue
    return rows


def fetch_recent_prices(codes: list[str], start: str, end: str, batch_size: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    price_rows: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for offset in range(0, len(codes), batch_size):
        batch_codes = codes[offset: offset + batch_size]
        tickers = [f"{code}.T" for code in batch_codes]
        error = ""
        try:
            data = yf.download(
                tickers=tickers,
                start=start,
                end=end,
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=True,
                group_by="ticker",
                timeout=30,
            )
            batch_rows = extract_batch_closes(data, tickers)
            price_rows.extend(batch_rows)
            available = {row["code"] for row in batch_rows}
        except Exception as exc:
            available = set()
            error = f"{type(exc).__name__}: {exc}"
        for code in batch_codes:
            statuses.append({
                "code": code,
                "batch_number": offset // batch_size + 1,
                "fetched": code in available,
                "batch_error": error,
            })
        time.sleep(0.2)
    prices = pd.DataFrame(price_rows)
    if not prices.empty:
        prices = prices.sort_values(["code", "price_date"]).drop_duplicates(["code", "price_date"], keep="last")
    return prices, pd.DataFrame(statuses)


def update_universe(universe: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    out = universe.copy()
    out["code"] = out["code"].map(normalize_code)
    prices = prices.copy()
    prices["code"] = prices["code"].map(normalize_code)
    prices["price_date"] = pd.to_datetime(prices["price_date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    by_code = {code: g.sort_values("price_date") for code, g in prices.groupby("code")}
    for index, row in out.iterrows():
        history = by_code.get(row["code"])
        if history is None or history.empty:
            continue
        first_date = pd.Timestamp(row["first_selection_price_date"])
        first_close = float(row["first_selection_close"])
        forward = history[history["price_date"] >= first_date]
        if forward.empty:
            continue
        latest = forward.iloc[-1]
        returns = forward["close"] / first_close - 1
        after = forward[forward["price_date"] > first_date]
        out.at[index, "latest_price_date"] = latest["price_date"].date().isoformat()
        out.at[index, "latest_close"] = float(latest["close"])
        out.at[index, "return_from_first_pick"] = float(latest["close"] / first_close - 1)
        out.at[index, "max_return_from_first_pick"] = float(returns.max())
        out.at[index, "min_return_from_first_pick"] = float(returns.min())
        out.at[index, "observed_sessions_from_first_pick"] = int(after["price_date"].nunique())
        out.at[index, "mature"] = bool(len(after) > 0)
    return out


def update_events(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    out = events.copy()
    out["code"] = out["code"].map(normalize_code)
    prices = prices.copy()
    prices["code"] = prices["code"].map(normalize_code)
    prices["price_date"] = pd.to_datetime(prices["price_date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    by_code = {code: g.sort_values("price_date") for code, g in prices.groupby("code")}
    for index, row in out.iterrows():
        history = by_code.get(row["code"])
        if history is None or history.empty:
            continue
        selection_date = pd.Timestamp(row["selection_price_date"])
        selection_close = float(row["selection_close"])
        forward = history[history["price_date"] >= selection_date]
        if forward.empty:
            continue
        latest = forward.iloc[-1]
        returns = forward["close"] / selection_close - 1
        after = forward[forward["price_date"] > selection_date]
        out.at[index, "latest_price_date"] = latest["price_date"].date().isoformat()
        out.at[index, "latest_close"] = float(latest["close"])
        out.at[index, "current_return"] = float(latest["close"] / selection_close - 1)
        out.at[index, "max_forward_return"] = float(returns.max())
        out.at[index, "min_forward_return"] = float(returns.min())
        out.at[index, "observed_sessions"] = int(after["price_date"].nunique())
        out.at[index, "latest_available_date"] = prices["price_date"].max().date().isoformat()
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", default="output/full_pick_history")
    parser.add_argument("--start", default="2026-07-16")
    parser.add_argument("--end", default="2026-07-23")
    parser.add_argument("--batch-size", type=int, default=80)
    args = parser.parse_args()

    audit_dir = Path(args.audit_dir)
    universe = pd.read_csv(audit_dir / "full_pick_universe.csv", dtype={"code": str}, low_memory=False)
    events = pd.read_csv(audit_dir / "full_pick_events.csv", dtype={"code": str}, low_memory=False)
    persisted = pd.read_csv(audit_dir / "combined_price_snapshots.csv", dtype={"code": str}, low_memory=False)
    persisted["price_date"] = pd.to_datetime(persisted["price_date"], errors="coerce")
    persisted["price_source"] = "persisted_snapshot"

    codes = sorted(universe["code"].map(normalize_code).unique())
    recent, statuses = fetch_recent_prices(codes, args.start, args.end, args.batch_size)
    combined = pd.concat([persisted, recent], ignore_index=True, sort=False)
    combined["price_date"] = pd.to_datetime(combined["price_date"], errors="coerce")
    combined = combined.dropna(subset=["code", "price_date", "close"])
    combined = combined.sort_values(["code", "price_date", "price_source"]).drop_duplicates(
        ["code", "price_date"], keep="last"
    )

    updated_universe = update_universe(universe, combined)
    updated_events = update_events(events, combined)
    combined.to_csv(audit_dir / "combined_price_snapshots_latest.csv", index=False)
    updated_universe.to_csv(audit_dir / "full_pick_universe_latest.csv", index=False)
    updated_events.to_csv(audit_dir / "full_pick_events_latest.csv", index=False)
    statuses.to_csv(audit_dir / "current_price_fetch_status.csv", index=False)

    latest_date = combined["price_date"].max().date().isoformat()
    current_date_rows = recent[recent["price_date"] == recent["price_date"].max()] if not recent.empty else recent
    mature = updated_universe[updated_universe["mature"].astype(str).str.lower().isin({"true", "1"})]
    refresh_summary = {
        "refresh_version": "2026-07-21-full-pick-latest-price-v1",
        "requested_codes": int(len(codes)),
        "codes_with_any_recent_price": int(recent["code"].nunique()) if not recent.empty else 0,
        "latest_available_price_date": latest_date,
        "codes_on_latest_fetched_date": int(current_date_rows["code"].nunique()) if not current_date_rows.empty else 0,
        "mature_unique_stocks": int(len(mature)),
        "mature_winners": int((mature["return_from_first_pick"] > 0).sum()),
        "mature_losers": int((mature["return_from_first_pick"] < 0).sum()),
        "mature_flat": int((mature["return_from_first_pick"] == 0).sum()),
        "mature_win_rate": float((mature["return_from_first_pick"] > 0).mean()) if len(mature) else None,
        "mature_mean_return": float(mature["return_from_first_pick"].mean()) if len(mature) else None,
        "mature_median_return": float(mature["return_from_first_pick"].median()) if len(mature) else None,
        "daido_metal": updated_universe.loc[updated_universe["code"] == "7245"].to_dict("records"),
        "research_only": True,
        "strategy_changed": False,
        "production_state_mutated": False,
    }
    (audit_dir / "latest_price_summary.json").write_text(
        json.dumps(refresh_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(refresh_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
