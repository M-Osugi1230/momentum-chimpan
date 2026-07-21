#!/usr/bin/env python3
"""Run the historical pick exporter with normalized timestamps and rerun handling."""
from __future__ import annotations

from typing import Any

import pandas as pd

import export_full_pick_history as exporter

_original_enrich_events = exporter.enrich_events


def _dashboard_pick_events_prefer_explicit_market_date(
    dashboard: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    event_frames: list[pd.DataFrame] = []
    diagnostics: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()

    # Descending order means an exact duplicate weekend rerun with an explicit price date
    # is retained instead of the earlier synthetic bootstrap row.
    grouped = list(dashboard.groupby("report_date", sort=True))
    for report_date, group in reversed(grouped):
        selected = group[group["rank"].between(1, 100, inclusive="both")].copy()
        selected = selected.sort_values(["rank", "code"]).drop_duplicates("code", keep="first")
        fingerprint = exporter.candidate_fingerprint(selected, "rank")
        duplicate_run = fingerprint in seen_fingerprints
        explicit_dates = selected["explicit_price_date"].dropna()
        if not explicit_dates.empty:
            inferred_market_date = explicit_dates.max()
        elif report_date.weekday() >= 5:
            inferred_market_date = report_date - pd.offsets.BDay(1)
        else:
            inferred_market_date = report_date
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
    if not events.empty:
        events = events.sort_values(["report_date", "pick_rank", "code"]).reset_index(drop=True)
    diagnostics_df = pd.DataFrame(diagnostics).sort_values("run_timestamp").reset_index(drop=True)
    return events, diagnostics_df


def _enrich_events_with_normalized_timestamps(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    normalized = events.copy()
    normalized["run_timestamp"] = pd.to_datetime(
        normalized["run_timestamp"], errors="coerce", utc=True
    ).dt.tz_localize(None)
    normalized["report_date"] = pd.to_datetime(
        normalized["report_date"], errors="coerce", utc=True
    ).dt.tz_localize(None)
    normalized["selection_date"] = pd.to_datetime(
        normalized["selection_date"], errors="coerce", utc=True
    ).dt.tz_localize(None)
    return _original_enrich_events(normalized, prices)


exporter.dashboard_pick_events = _dashboard_pick_events_prefer_explicit_market_date
exporter.enrich_events = _enrich_events_with_normalized_timestamps

if __name__ == "__main__":
    exporter.main()
