#!/usr/bin/env python3
"""Export every historical positive pick from the first persisted session.

This is a read-only audit utility. It reconstructs the exact legacy Top30 buy-candidate
rule from the final legacy history commit, then combines it with every Top100 row from
the dashboard-era ranking history. It does not change any score, rank, strategy, paper
state, or production file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_LEGACY_REF = "29d6a0791c208758cbbe7f1089e51d9c5f687277"


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().split(".")[0]
    return text.zfill(4) if text else ""


def as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def git_show(ref: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def load_legacy(ref: str, work_dir: Path) -> pd.DataFrame:
    raw = git_show(ref, "data/momentum_history.csv")
    path = work_dir / "legacy_momentum_history.csv"
    path.write_bytes(raw)
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].map(normalize_code)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["close", "score", "high", "volume", "ytd_high_streak", "ytd_high_count"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date", "code", "close"]).copy()


def load_dashboard(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].map(normalize_code)
    df["report_date"] = pd.to_datetime(df.get("date"), errors="coerce")
    if "price_date" in df.columns:
        price_date = pd.to_datetime(df["price_date"], errors="coerce")
        df["selection_date"] = price_date.fillna(df["report_date"])
    else:
        df["selection_date"] = df["report_date"]
    numeric_cols = [
        "rank", "close", "score", "return_5d", "return_20d", "return_60d",
        "volume_ratio", "trading_value", "ma20_deviation", "ma60_deviation",
        "relative_strength_score", "relative_strength_rank", "rank_change",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["selection_date", "code", "close"]).copy()


def legacy_pick_events(legacy: pd.DataFrame) -> pd.DataFrame:
    events: list[pd.DataFrame] = []
    # Exact historical production rule: close >= 100, sort score descending, head(30).
    for selection_date, group in legacy.groupby("date", sort=True):
        eligible = group[group["close"] >= 100].copy()
        selected = eligible.sort_values("score", ascending=False).head(30).copy()
        selected.insert(0, "pick_rank", range(1, len(selected) + 1))
        selected["report_date"] = selection_date
        selected["selection_date"] = selection_date
        selected["pick_rule"] = "LEGACY_BUY_TOP30"
        selected["era"] = "legacy"
        events.append(selected)
    return pd.concat(events, ignore_index=True) if events else pd.DataFrame()


def dashboard_pick_events(dashboard: pd.DataFrame) -> pd.DataFrame:
    if "is_top100" in dashboard.columns:
        selected = dashboard[as_bool(dashboard["is_top100"])].copy()
    else:
        selected = dashboard[dashboard["rank"].le(100)].copy()
    selected["pick_rank"] = selected["rank"]
    selected["pick_rule"] = "DASHBOARD_TOP100"
    selected["era"] = "dashboard"
    return selected


def canonicalize_events(events: pd.DataFrame) -> pd.DataFrame:
    events = events.sort_values(["selection_date", "report_date", "pick_rule", "pick_rank", "code"])
    # Weekend/manual reruns can use the same market close. Count that price-date signal once.
    return events.drop_duplicates(["selection_date", "code", "pick_rule"], keep="first").copy()


def build_price_history(legacy: pd.DataFrame, dashboard: pd.DataFrame) -> pd.DataFrame:
    old = legacy[["date", "code", "name", "close"]].rename(columns={"date": "price_date"}).copy()
    new = dashboard[["selection_date", "code", "name", "close"]].rename(columns={"selection_date": "price_date"}).copy()
    prices = pd.concat([old, new], ignore_index=True)
    prices["price_date"] = pd.to_datetime(prices["price_date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["price_date", "code", "close"])
    return prices.sort_values(["code", "price_date"]).drop_duplicates(["code", "price_date"], keep="last")


def enrich_events(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    latest_date = prices["price_date"].max()
    by_code = {code: g.sort_values("price_date") for code, g in prices.groupby("code")}
    rows: list[dict[str, Any]] = []
    for _, event in events.iterrows():
        code = event["code"]
        selection_date = pd.Timestamp(event["selection_date"])
        selection_close = float(event["close"])
        history = by_code.get(code, pd.DataFrame())
        forward = history[history["price_date"] >= selection_date].copy() if not history.empty else pd.DataFrame()
        if forward.empty:
            latest_price_date = pd.NaT
            latest_close = None
            current_return = None
            max_return = None
            min_return = None
            next_date = pd.NaT
            next_return = None
            observed_sessions = 0
        else:
            latest = forward.iloc[-1]
            latest_price_date = latest["price_date"]
            latest_close = float(latest["close"])
            current_return = latest_close / selection_close - 1
            returns = forward["close"] / selection_close - 1
            max_return = float(returns.max())
            min_return = float(returns.min())
            after = forward[forward["price_date"] > selection_date]
            if after.empty:
                next_date = pd.NaT
                next_return = None
                observed_sessions = 0
            else:
                first_after = after.iloc[0]
                next_date = first_after["price_date"]
                next_return = float(first_after["close"] / selection_close - 1)
                observed_sessions = int(after["price_date"].nunique())
        row = {
            "era": event.get("era", ""),
            "pick_rule": event.get("pick_rule", ""),
            "report_date": pd.Timestamp(event.get("report_date", selection_date)).date().isoformat(),
            "selection_date": selection_date.date().isoformat(),
            "pick_rank": int(event["pick_rank"]) if pd.notna(event.get("pick_rank")) else None,
            "code": code,
            "name": event.get("name", ""),
            "selection_close": selection_close,
            "selection_score": float(event["score"]) if pd.notna(event.get("score")) else None,
            "latest_price_date": latest_price_date.date().isoformat() if pd.notna(latest_price_date) else None,
            "latest_close": latest_close,
            "current_return": current_return,
            "max_forward_return": max_return,
            "min_forward_return": min_return,
            "next_price_date": next_date.date().isoformat() if pd.notna(next_date) else None,
            "next_session_return": next_return,
            "observed_sessions": observed_sessions,
            "latest_available_date": latest_date.date().isoformat(),
        }
        # Selection-time fields available in the dashboard era.
        for col in [
            "sector33", "return_5d", "return_20d", "return_60d", "volume_ratio",
            "trading_value", "ma20_deviation", "ma60_deviation", "relative_strength_score",
            "relative_strength_rank", "relative_strength_grade", "relative_strength_lifecycle",
            "relative_strength_alert", "rank_change", "is_new_entry", "is_rising_fast",
            "top30_streak", "data_quality_grade", "reason",
        ]:
            row[col] = event.get(col) if col in event.index else None
        rows.append(row)
    return pd.DataFrame(rows)


def build_unique(events: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for code, group in events.sort_values(["selection_date", "pick_rank"]).groupby("code"):
        first = group.iloc[0]
        observed = group[group["observed_sessions"] > 0]
        current_return = first.get("current_return")
        rules = sorted(set(group["pick_rule"].dropna().astype(str)))
        records.append({
            "code": code,
            "name": first.get("name", ""),
            "first_selection_date": first["selection_date"],
            "first_selection_close": first["selection_close"],
            "first_pick_rule": first["pick_rule"],
            "first_pick_rank": first["pick_rank"],
            "best_pick_rank": int(group["pick_rank"].min()),
            "pick_event_count": int(len(group)),
            "distinct_pick_dates": int(group["selection_date"].nunique()),
            "pick_rules": "|".join(rules),
            "latest_price_date": first.get("latest_price_date"),
            "latest_close": first.get("latest_close"),
            "return_from_first_pick": current_return,
            "max_return_from_first_pick": first.get("max_forward_return"),
            "min_return_from_first_pick": first.get("min_forward_return"),
            "next_session_return_from_first_pick": first.get("next_session_return"),
            "observed_sessions_from_first_pick": first.get("observed_sessions"),
            "ever_top30": bool((group["pick_rank"] <= 30).any()),
            "ever_top10": bool((group["pick_rank"] <= 10).any()),
            "mature": bool(len(observed) > 0),
        })
    return pd.DataFrame(records).sort_values(["first_selection_date", "first_pick_rank", "code"])


def dataframe_sha256(df: pd.DataFrame) -> str:
    payload = df.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-ref", default=DEFAULT_LEGACY_REF)
    parser.add_argument("--dashboard-path", default="data/momentum_daily_ranking.csv")
    parser.add_argument("--output-dir", default="output/full_pick_history")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "source"
    work_dir.mkdir(parents=True, exist_ok=True)

    legacy = load_legacy(args.legacy_ref, work_dir)
    dashboard = load_dashboard(Path(args.dashboard_path))
    legacy_events = legacy_pick_events(legacy)
    dashboard_events = dashboard_pick_events(dashboard)
    all_events = canonicalize_events(pd.concat([legacy_events, dashboard_events], ignore_index=True, sort=False))
    prices = build_price_history(legacy, dashboard)
    enriched = enrich_events(all_events, prices)
    unique = build_unique(enriched)

    event_path = out_dir / "full_pick_events.csv"
    unique_path = out_dir / "full_pick_universe.csv"
    prices_path = out_dir / "combined_price_snapshots.csv"
    enriched.to_csv(event_path, index=False)
    unique.to_csv(unique_path, index=False)
    prices.to_csv(prices_path, index=False)

    mature = unique[unique["mature"]]
    summary = {
        "audit_version": "2026-07-21-full-pick-history-v1",
        "legacy_ref": args.legacy_ref,
        "legacy_first_date": legacy["date"].min().date().isoformat(),
        "legacy_last_date": legacy["date"].max().date().isoformat(),
        "dashboard_first_date": dashboard["selection_date"].min().date().isoformat(),
        "dashboard_last_date": dashboard["selection_date"].max().date().isoformat(),
        "latest_available_price_date": prices["price_date"].max().date().isoformat(),
        "legacy_pick_events": int((enriched["pick_rule"] == "LEGACY_BUY_TOP30").sum()),
        "dashboard_pick_events": int((enriched["pick_rule"] == "DASHBOARD_TOP100").sum()),
        "total_pick_events": int(len(enriched)),
        "unique_picked_stocks": int(len(unique)),
        "mature_unique_stocks": int(len(mature)),
        "mature_winners": int((mature["return_from_first_pick"] > 0).sum()),
        "mature_losers": int((mature["return_from_first_pick"] < 0).sum()),
        "mature_flat": int((mature["return_from_first_pick"] == 0).sum()),
        "mature_win_rate": float((mature["return_from_first_pick"] > 0).mean()) if len(mature) else None,
        "mature_mean_return": float(mature["return_from_first_pick"].mean()) if len(mature) else None,
        "mature_median_return": float(mature["return_from_first_pick"].median()) if len(mature) else None,
        "daido_metal_included": bool((unique["code"] == "7245").any()),
        "files": {
            event_path.name: {"sha256": dataframe_sha256(enriched), "rows": int(len(enriched))},
            unique_path.name: {"sha256": dataframe_sha256(unique), "rows": int(len(unique))},
            prices_path.name: {"sha256": dataframe_sha256(prices), "rows": int(len(prices))},
        },
        "scope": {
            "positive_picks": "legacy daily Top30 buy candidates plus every dashboard Top100 appearance",
            "dedupe": "same code/rule/market-price-date counted once",
            "excluded": "sell candidates and non-Top100 scanned-universe rows",
            "research_only": True,
            "strategy_changed": False,
            "production_state_mutated": False,
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
