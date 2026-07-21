#!/usr/bin/env python3
"""Export every historical positive pick from the first persisted production run.

Read-only audit utility:
- legacy era: reconstruct each run's exact Top30 buy-candidate table from that run's
  cumulative ``momentum_history.csv`` snapshot;
- dashboard era: collect every report's ranked Top100, collapsing exact duplicate reruns;
- calculate first-pick-to-latest results from persisted price snapshots.

No score, rank, strategy, paper state, or production file is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

# Daily production history commits before the dashboard-history schema was introduced.
# Multiple runs on one date are retained only when their Top30 candidate fingerprints differ.
LEGACY_RUNS: list[tuple[str, str]] = [
    ("2026-06-23T14:03:49Z", "7e34c3fdb86c25d9e0778aea7c720b5ef2d282d4"),
    ("2026-06-23T15:00:00Z", "ceb1d52e577d64a1bba100f2af7ae507ab9cfd09"),
    ("2026-06-24T10:46:03Z", "4a1a4fd6df886a3e9be31b28f8f63a4e62d01c05"),
    ("2026-06-24T17:24:19Z", "e48647287887886d46a1d0bbe3380b256bf982d5"),
    ("2026-06-25T10:39:10Z", "13da68e037464170d694d735b91c655c4b5258d5"),
    ("2026-06-25T18:29:44Z", "4d365edad296eac1330eef0f65b56e2b6d3e02a5"),
    ("2026-06-26T10:52:04Z", "14b735ffe75d113b81b14a8a6464bb7bfff3cdac"),
    ("2026-06-29T12:33:45Z", "e62621670c983dc7b119147727f3df7725d50ae5"),
    ("2026-06-30T11:08:29Z", "de66a0573bf9f6503f56e4093c29cc648a3712d9"),
    ("2026-07-01T11:09:43Z", "a392141b24e5ee478d0c4b3de603254dfb6a2476"),
    ("2026-07-02T10:32:52Z", "23f94217a1bf599f5e44fac241e5f4f78b0004e1"),
    ("2026-07-03T10:26:32Z", "29d6a0791c208758cbbe7f1089e51d9c5f687277"),
]
FINAL_LEGACY_REF = LEGACY_RUNS[-1][1]


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().split(".")[0]
    return text.zfill(4) if text else ""


def git_show(ref: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def load_legacy_snapshot(ref: str, work_dir: Path) -> pd.DataFrame:
    raw = git_show(ref, "data/momentum_history.csv")
    path = work_dir / f"legacy_{ref[:12]}.csv"
    path.write_bytes(raw)
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].map(normalize_code)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["close", "score", "high", "volume", "ytd_high_streak", "ytd_high_count"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date", "code", "close"]).copy()


def load_dashboard(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str}, low_memory=False)
    df["code"] = df["code"].map(normalize_code)
    df["report_date"] = pd.to_datetime(df.get("date"), errors="coerce")
    df["explicit_price_date"] = pd.to_datetime(df.get("price_date"), errors="coerce")
    numeric_cols = [
        "rank", "close", "score", "return_5d", "return_20d", "return_60d",
        "volume_ratio", "trading_value", "ma20_deviation", "ma60_deviation",
        "relative_strength_score", "relative_strength_rank", "rank_change",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["report_date", "code", "close"]).copy()


def candidate_fingerprint(df: pd.DataFrame, rank_col: str) -> str:
    cols = [rank_col, "code", "close", "score"]
    stable = df[cols].sort_values([rank_col, "code"]).to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def legacy_pick_events(work_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    event_frames: list[pd.DataFrame] = []
    diagnostics: list[dict[str, Any]] = []
    snapshots: list[pd.DataFrame] = []
    seen_fingerprints: set[str] = set()

    for run_timestamp, ref in LEGACY_RUNS:
        history = load_legacy_snapshot(ref, work_dir)
        snapshots.append(history)
        # The run evaluated one latest available row per security, including thinly traded
        # names whose market price date could precede the report date.
        current = history.sort_values("date").drop_duplicates("code", keep="last").copy()
        eligible = current[current["close"] >= 100].copy()
        selected = eligible.sort_values("score", ascending=False).head(30).copy()
        selected.insert(0, "pick_rank", range(1, len(selected) + 1))
        fingerprint = candidate_fingerprint(selected, "pick_rank")
        duplicate_run = fingerprint in seen_fingerprints
        diagnostics.append({
            "run_timestamp": run_timestamp,
            "report_date": pd.Timestamp(run_timestamp).date().isoformat(),
            "ref": ref,
            "history_rows": int(len(history)),
            "current_security_rows": int(len(current)),
            "eligible_rows": int(len(eligible)),
            "top30_rows": int(len(selected)),
            "candidate_fingerprint": fingerprint,
            "exact_duplicate_prior_run": duplicate_run,
            "included_in_event_history": not duplicate_run,
        })
        if duplicate_run:
            continue
        seen_fingerprints.add(fingerprint)
        selected["run_timestamp"] = pd.Timestamp(run_timestamp)
        selected["report_date"] = pd.Timestamp(run_timestamp).normalize()
        selected["selection_date"] = selected["date"]
        selected["pick_rule"] = "LEGACY_BUY_TOP30"
        selected["era"] = "legacy"
        selected["source_ref"] = ref
        event_frames.append(selected)

    events = pd.concat(event_frames, ignore_index=True, sort=False) if event_frames else pd.DataFrame()
    diagnostics_df = pd.DataFrame(diagnostics)
    final_history = snapshots[-1]
    return events, diagnostics_df, final_history


def dashboard_pick_events(dashboard: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    event_frames: list[pd.DataFrame] = []
    diagnostics: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()

    for report_date, group in dashboard.groupby("report_date", sort=True):
        selected = group[group["rank"].between(1, 100, inclusive="both")].copy()
        selected = selected.sort_values(["rank", "code"]).drop_duplicates("code", keep="first")
        fingerprint = candidate_fingerprint(selected, "rank")
        duplicate_run = fingerprint in seen_fingerprints
        explicit_dates = selected["explicit_price_date"].dropna()
        inferred_market_date = explicit_dates.max() if not explicit_dates.empty else report_date
        diagnostics.append({
            "run_timestamp": report_date.isoformat(),
            "report_date": report_date.date().isoformat(),
            "ref": "data/momentum_daily_ranking.csv",
            "history_rows": int(len(group)),
            "current_security_rows": int(group["code"].nunique()),
            "eligible_rows": int(len(group)),
            "top100_rows": int(len(selected)),
            "candidate_fingerprint": fingerprint,
            "exact_duplicate_prior_run": duplicate_run,
            "included_in_event_history": not duplicate_run,
            "inferred_market_date": inferred_market_date.date().isoformat(),
        })
        if duplicate_run:
            continue
        seen_fingerprints.add(fingerprint)
        selected["pick_rank"] = selected["rank"]
        selected["run_timestamp"] = report_date
        selected["selection_date"] = selected["explicit_price_date"].fillna(inferred_market_date)
        selected["pick_rule"] = "DASHBOARD_TOP100"
        selected["era"] = "dashboard"
        selected["source_ref"] = "data/momentum_daily_ranking.csv"
        event_frames.append(selected)

    events = pd.concat(event_frames, ignore_index=True, sort=False) if event_frames else pd.DataFrame()
    return events, pd.DataFrame(diagnostics)


def build_price_history(final_legacy: pd.DataFrame, dashboard: pd.DataFrame) -> pd.DataFrame:
    old = final_legacy[["date", "code", "name", "close"]].rename(columns={"date": "price_date"}).copy()
    new = dashboard[["report_date", "explicit_price_date", "code", "name", "close"]].copy()
    new["price_date"] = new["explicit_price_date"].fillna(new["report_date"])
    new = new[["price_date", "code", "name", "close"]]
    prices = pd.concat([old, new], ignore_index=True)
    prices["price_date"] = pd.to_datetime(prices["price_date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["price_date", "code", "close"])
    return prices.sort_values(["code", "price_date"]).drop_duplicates(["code", "price_date"], keep="last")


def enrich_events(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    latest_date = prices["price_date"].max()
    by_code = {code: g.sort_values("price_date") for code, g in prices.groupby("code")}
    rows: list[dict[str, Any]] = []
    events = events.sort_values(["run_timestamp", "pick_rule", "pick_rank", "code"])
    for _, event in events.iterrows():
        code = event["code"]
        selection_date = pd.Timestamp(event["selection_date"])
        selection_close = float(event["close"])
        history = by_code.get(code, pd.DataFrame())
        forward = history[history["price_date"] >= selection_date].copy() if not history.empty else pd.DataFrame()
        if forward.empty:
            latest_price_date = pd.NaT
            latest_close = current_return = max_return = min_return = next_return = None
            next_date = pd.NaT
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
            "run_timestamp": pd.Timestamp(event["run_timestamp"]).isoformat(),
            "report_date": pd.Timestamp(event["report_date"]).date().isoformat(),
            "selection_price_date": selection_date.date().isoformat(),
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
            "source_ref": event.get("source_ref", ""),
        }
        for col in [
            "sector33", "return_5d", "return_20d", "return_60d", "volume_ratio",
            "trading_value", "ma20_deviation", "ma60_deviation", "relative_strength_score",
            "relative_strength_rank", "relative_strength_grade", "relative_strength_lifecycle",
            "relative_strength_alert", "rank_change", "is_new_entry", "is_rising_fast",
            "top30_streak", "data_quality_grade", "reason", "ytd_high_streak", "ytd_high_count",
        ]:
            row[col] = event.get(col) if col in event.index else None
        rows.append(row)
    return pd.DataFrame(rows)


def build_unique(events: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    ordered = events.sort_values(["run_timestamp", "pick_rank", "code"])
    for code, group in ordered.groupby("code"):
        first = group.iloc[0]
        rules = sorted(set(group["pick_rule"].dropna().astype(str)))
        records.append({
            "code": code,
            "name": first.get("name", ""),
            "first_run_timestamp": first["run_timestamp"],
            "first_report_date": first["report_date"],
            "first_selection_price_date": first["selection_price_date"],
            "first_selection_close": first["selection_close"],
            "first_pick_rule": first["pick_rule"],
            "first_pick_rank": first["pick_rank"],
            "first_selection_score": first["selection_score"],
            "best_pick_rank": int(group["pick_rank"].min()),
            "pick_event_count": int(len(group)),
            "distinct_report_dates": int(group["report_date"].nunique()),
            "pick_rules": "|".join(rules),
            "latest_price_date": first.get("latest_price_date"),
            "latest_close": first.get("latest_close"),
            "return_from_first_pick": first.get("current_return"),
            "max_return_from_first_pick": first.get("max_forward_return"),
            "min_return_from_first_pick": first.get("min_forward_return"),
            "next_session_return_from_first_pick": first.get("next_session_return"),
            "observed_sessions_from_first_pick": first.get("observed_sessions"),
            "ever_top30": bool((group["pick_rank"] <= 30).any()),
            "ever_top10": bool((group["pick_rank"] <= 10).any()),
            "mature": bool(first.get("observed_sessions", 0) > 0),
        })
    return pd.DataFrame(records).sort_values(["first_run_timestamp", "first_pick_rank", "code"])


def dataframe_sha256(df: pd.DataFrame) -> str:
    payload = df.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard-path", default="data/momentum_daily_ranking.csv")
    parser.add_argument("--output-dir", default="output/full_pick_history")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "source"
    work_dir.mkdir(parents=True, exist_ok=True)

    legacy_events, legacy_runs, final_legacy = legacy_pick_events(work_dir)
    dashboard = load_dashboard(Path(args.dashboard_path))
    dashboard_events, dashboard_runs = dashboard_pick_events(dashboard)
    raw_events = pd.concat([legacy_events, dashboard_events], ignore_index=True, sort=False)
    prices = build_price_history(final_legacy, dashboard)
    enriched = enrich_events(raw_events, prices)
    unique = build_unique(enriched)
    run_diagnostics = pd.concat([legacy_runs.assign(era="legacy"), dashboard_runs.assign(era="dashboard")], ignore_index=True, sort=False)

    outputs = {
        "full_pick_events.csv": enriched,
        "full_pick_universe.csv": unique,
        "combined_price_snapshots.csv": prices,
        "run_diagnostics.csv": run_diagnostics,
    }
    for filename, frame in outputs.items():
        frame.to_csv(out_dir / filename, index=False)

    mature = unique[unique["mature"]].copy()
    summary = {
        "audit_version": "2026-07-21-full-pick-history-v2",
        "legacy_first_report_timestamp": legacy_runs.loc[legacy_runs["included_in_event_history"], "run_timestamp"].min(),
        "legacy_last_report_timestamp": legacy_runs.loc[legacy_runs["included_in_event_history"], "run_timestamp"].max(),
        "dashboard_first_report_date": dashboard_runs.loc[dashboard_runs["included_in_event_history"], "report_date"].min(),
        "dashboard_last_report_date": dashboard_runs.loc[dashboard_runs["included_in_event_history"], "report_date"].max(),
        "latest_available_price_date": prices["price_date"].max().date().isoformat(),
        "included_legacy_runs": int(legacy_runs["included_in_event_history"].sum()),
        "included_dashboard_runs": int(dashboard_runs["included_in_event_history"].sum()),
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
        "daido_metal_first_pick": unique.loc[unique["code"] == "7245"].to_dict("records"),
        "files": {
            filename: {"sha256": dataframe_sha256(frame), "rows": int(len(frame))}
            for filename, frame in outputs.items()
        },
        "scope": {
            "positive_picks": "each non-duplicate legacy production Top30 plus each non-duplicate dashboard report Top100",
            "legacy_reconstruction": "latest available security row in each run snapshot, then the historical score-descending head(30) rule",
            "dashboard_reconstruction": "rank 1..100 per report; exact duplicate reruns collapsed",
            "excluded": "sell candidates and non-selected scanned-universe rows",
            "research_only": True,
            "strategy_changed": False,
            "production_state_mutated": False,
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
