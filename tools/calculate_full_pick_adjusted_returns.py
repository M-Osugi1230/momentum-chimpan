#!/usr/bin/env python3
"""Calculate split/dividend-adjusted returns for every historically picked stock.

Uses yfinance adjusted closes from before the first pick through the current date. Outputs
separate audit files and never mutates production state.
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


def extract_rows(data: pd.DataFrame, tickers: list[str]) -> list[dict[str, Any]]:
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
            else:
                block = data if len(tickers) == 1 else pd.DataFrame()
            if block.empty or "Close" not in block.columns:
                continue
            raw = pd.to_numeric(block["Close"], errors="coerce")
            adjusted = pd.to_numeric(
                block["Adj Close"] if "Adj Close" in block.columns else block["Close"],
                errors="coerce",
            )
            frame = pd.DataFrame({"raw_close": raw, "adjusted_close": adjusted}).dropna(subset=["raw_close"])
            frame["adjusted_close"] = frame["adjusted_close"].fillna(frame["raw_close"])
            for date, row in frame.iterrows():
                rows.append({
                    "code": ticker.split(".")[0].zfill(4),
                    "price_date": pd.Timestamp(date).tz_localize(None).normalize(),
                    "raw_close": float(row["raw_close"]),
                    "adjusted_close": float(row["adjusted_close"]),
                })
        except Exception:
            continue
    return rows


def fetch_adjusted_history(codes: list[str], start: str, end: str, batch_size: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for offset in range(0, len(codes), batch_size):
        batch_codes = codes[offset:offset + batch_size]
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
                timeout=45,
            )
            batch_rows = extract_rows(data, tickers)
            rows.extend(batch_rows)
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
    history = pd.DataFrame(rows)
    if not history.empty:
        history = history.sort_values(["code", "price_date"]).drop_duplicates(["code", "price_date"], keep="last")
    return history, pd.DataFrame(statuses)


def locate_entry(history: pd.DataFrame, selection_date: pd.Timestamp) -> pd.Series | None:
    exact = history[history["price_date"] == selection_date]
    if not exact.empty:
        return exact.iloc[-1]
    prior = history[history["price_date"] <= selection_date]
    if not prior.empty:
        return prior.iloc[-1]
    return None


def enrich_universe(universe: pd.DataFrame, histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = universe.copy()
    out["code"] = out["code"].map(normalize_code)
    new_cols = [
        "adjusted_entry_price", "adjusted_latest_price", "adjusted_return_from_first_pick",
        "adjusted_max_return_from_first_pick", "adjusted_min_return_from_first_pick",
        "adjusted_next_session_return", "raw_vs_adjusted_return_gap",
        "corporate_action_suspected", "adjusted_latest_price_date",
    ]
    for col in new_cols:
        out[col] = None
    for index, row in out.iterrows():
        history = histories.get(row["code"])
        if history is None or history.empty:
            continue
        selection_date = pd.Timestamp(row["first_selection_price_date"])
        entry = locate_entry(history, selection_date)
        if entry is None:
            continue
        latest = history.iloc[-1]
        forward = history[history["price_date"] >= entry["price_date"]].copy()
        if forward.empty:
            continue
        entry_adj = float(entry["adjusted_close"])
        latest_adj = float(latest["adjusted_close"])
        adjusted_returns = forward["adjusted_close"] / entry_adj - 1
        after = forward[forward["price_date"] > entry["price_date"]]
        next_return = None if after.empty else float(after.iloc[0]["adjusted_close"] / entry_adj - 1)
        adjusted_return = float(latest_adj / entry_adj - 1)
        raw_return = float(row["return_from_first_pick"])
        gap = adjusted_return - raw_return
        out.at[index, "adjusted_entry_price"] = entry_adj
        out.at[index, "adjusted_latest_price"] = latest_adj
        out.at[index, "adjusted_return_from_first_pick"] = adjusted_return
        out.at[index, "adjusted_max_return_from_first_pick"] = float(adjusted_returns.max())
        out.at[index, "adjusted_min_return_from_first_pick"] = float(adjusted_returns.min())
        out.at[index, "adjusted_next_session_return"] = next_return
        out.at[index, "raw_vs_adjusted_return_gap"] = gap
        out.at[index, "corporate_action_suspected"] = bool(abs(gap) >= 0.03)
        out.at[index, "adjusted_latest_price_date"] = latest["price_date"].date().isoformat()
    return out


def enrich_events(events: pd.DataFrame, histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = events.copy()
    out["code"] = out["code"].map(normalize_code)
    for col in [
        "adjusted_entry_price", "adjusted_latest_price", "adjusted_current_return",
        "adjusted_max_forward_return", "adjusted_min_forward_return",
        "adjusted_next_session_return", "raw_vs_adjusted_return_gap",
        "corporate_action_suspected", "adjusted_latest_price_date",
    ]:
        out[col] = None
    for index, row in out.iterrows():
        history = histories.get(row["code"])
        if history is None or history.empty:
            continue
        selection_date = pd.Timestamp(row["selection_price_date"])
        entry = locate_entry(history, selection_date)
        if entry is None:
            continue
        latest = history.iloc[-1]
        forward = history[history["price_date"] >= entry["price_date"]].copy()
        entry_adj = float(entry["adjusted_close"])
        latest_adj = float(latest["adjusted_close"])
        adjusted_returns = forward["adjusted_close"] / entry_adj - 1
        after = forward[forward["price_date"] > entry["price_date"]]
        next_return = None if after.empty else float(after.iloc[0]["adjusted_close"] / entry_adj - 1)
        adjusted_return = float(latest_adj / entry_adj - 1)
        raw_return = float(row["current_return"])
        gap = adjusted_return - raw_return
        out.at[index, "adjusted_entry_price"] = entry_adj
        out.at[index, "adjusted_latest_price"] = latest_adj
        out.at[index, "adjusted_current_return"] = adjusted_return
        out.at[index, "adjusted_max_forward_return"] = float(adjusted_returns.max())
        out.at[index, "adjusted_min_forward_return"] = float(adjusted_returns.min())
        out.at[index, "adjusted_next_session_return"] = next_return
        out.at[index, "raw_vs_adjusted_return_gap"] = gap
        out.at[index, "corporate_action_suspected"] = bool(abs(gap) >= 0.03)
        out.at[index, "adjusted_latest_price_date"] = latest["price_date"].date().isoformat()
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", default="output/full_pick_history")
    parser.add_argument("--start", default="2026-06-20")
    parser.add_argument("--end", default="2026-07-23")
    parser.add_argument("--batch-size", type=int, default=80)
    args = parser.parse_args()

    audit_dir = Path(args.audit_dir)
    universe = pd.read_csv(audit_dir / "full_pick_universe_latest.csv", dtype={"code": str}, low_memory=False)
    events = pd.read_csv(audit_dir / "full_pick_events_latest.csv", dtype={"code": str}, low_memory=False)
    codes = sorted(universe["code"].map(normalize_code).unique())
    history, statuses = fetch_adjusted_history(codes, args.start, args.end, args.batch_size)
    histories = {code: group.sort_values("price_date") for code, group in history.groupby("code")}
    adjusted_universe = enrich_universe(universe, histories)
    adjusted_events = enrich_events(events, histories)

    history.to_csv(audit_dir / "adjusted_price_history.csv", index=False)
    statuses.to_csv(audit_dir / "adjusted_price_fetch_status.csv", index=False)
    adjusted_universe.to_csv(audit_dir / "full_pick_universe_adjusted.csv", index=False)
    adjusted_events.to_csv(audit_dir / "full_pick_events_adjusted.csv", index=False)

    usable = adjusted_universe.dropna(subset=["adjusted_return_from_first_pick"]).copy()
    r = pd.to_numeric(usable["adjusted_return_from_first_pick"], errors="coerce")
    corporate = adjusted_universe[
        adjusted_universe["corporate_action_suspected"].astype(str).str.lower().isin({"true", "1"})
    ]
    summary = {
        "adjusted_return_version": "2026-07-21-full-pick-adjusted-return-v1",
        "requested_codes": int(len(codes)),
        "codes_with_adjusted_history": int(history["code"].nunique()) if not history.empty else 0,
        "latest_adjusted_price_date": history["price_date"].max().date().isoformat() if not history.empty else None,
        "usable_unique_stocks": int(r.notna().sum()),
        "winners": int((r > 0).sum()),
        "losers": int((r < 0).sum()),
        "flat": int((r == 0).sum()),
        "win_rate": float((r > 0).mean()) if len(r) else None,
        "mean_adjusted_return": float(r.mean()) if len(r) else None,
        "median_adjusted_return": float(r.median()) if len(r) else None,
        "corporate_action_suspected_count": int(len(corporate)),
        "corporate_action_suspected_codes": corporate[[
            "code", "name", "return_from_first_pick", "adjusted_return_from_first_pick",
            "raw_vs_adjusted_return_gap"
        ]].to_dict("records"),
        "daido_metal": adjusted_universe.loc[adjusted_universe["code"] == "7245"].to_dict("records"),
        "research_only": True,
        "strategy_changed": False,
        "production_state_mutated": False,
    }
    (audit_dir / "adjusted_return_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
