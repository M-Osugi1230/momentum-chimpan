#!/usr/bin/env python3
"""Run the historical pick exporter with one consistent UTC-naive timeline."""
from __future__ import annotations

import pandas as pd

import export_full_pick_history as exporter

_original_enrich_events = exporter.enrich_events


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


exporter.enrich_events = _enrich_events_with_normalized_timestamps

if __name__ == "__main__":
    exporter.main()
