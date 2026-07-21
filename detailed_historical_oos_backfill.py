"""Freshness-safe wrapper for the detailed historical OOS backfill.

The generic historical backfill is intentionally permissive because it is a research utility.
This detailed study applies stricter point-in-time data quality rules before ranking:

- the evaluation date must have positive traded volume;
- only positive-volume observations are used as trading sessions;
- a calendar gap greater than 21 days starts a new listing/trading segment;
- at least the normal minimum history must exist inside the current segment.

These rules prevent stale pre-IPO carry-forwards, reused ticker histories, and long-suspension
segments from entering a ranking. The wrapper remains read-only and non-promotable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import historical_backfill
import historical_oos_backfill as base
import main
import relative_strength_lifecycle as rs_lifecycle

VERSION = "2026-07-22-detailed-oos-freshness-backfill-v2"
MAX_HISTORY_GAP_DAYS = 21

_FRESHNESS_AUDIT: list[dict[str, Any]] = []


def current_active_segment(
    frame: pd.DataFrame,
    evaluation_date: pd.Timestamp,
    lookback_rows: int,
) -> tuple[pd.DataFrame, str]:
    available = frame[frame["Date"] <= evaluation_date].copy()
    if available.empty:
        return available, "NO_HISTORY"
    volume = pd.to_numeric(available["Volume"], errors="coerce").fillna(0.0)
    active = available[volume.gt(0)].copy()
    if active.empty:
        return active, "NO_POSITIVE_VOLUME_HISTORY"
    if pd.Timestamp(active["Date"].iloc[-1]).normalize() != pd.Timestamp(evaluation_date).normalize():
        return active.iloc[0:0], "NO_ACTIVE_TRADE_ON_EVALUATION_DATE"
    gaps = pd.to_datetime(active["Date"], errors="coerce").diff().dt.days
    breaks = np.flatnonzero(gaps.gt(MAX_HISTORY_GAP_DAYS).fillna(False).to_numpy())
    if len(breaks):
        active = active.iloc[int(breaks[-1]) :].copy()
    active = active.tail(int(lookback_rows)).copy()
    if len(active) < historical_backfill.MIN_HISTORY_ROWS:
        return active.iloc[0:0], "INSUFFICIENT_CONTINUOUS_ACTIVE_HISTORY"
    return active, "OK"


def build_rankings_fresh(
    members: list[historical_backfill.UniverseMember],
    prices: dict[str, pd.DataFrame],
    config: dict[str, Any],
    evaluation_start,
    evaluation_end,
    sample_every: int,
    minimum_coverage_ratio: float,
    top_limit: int,
    lookback_rows: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metadata = {member.code: member for member in members}
    dates = base.evaluation_dates(
        prices,
        evaluation_start,
        evaluation_end,
        sample_every,
        minimum_coverage_ratio,
    )
    historical = pd.DataFrame(columns=main.ranking_history_columns())
    coverage_rows: list[dict[str, Any]] = []
    min_trading_value = int((config.get("market") or {}).get("min_trading_value", 100_000_000))

    for evaluation_date in dates:
        rows: list[dict[str, Any]] = []
        counters: dict[str, int] = {}
        for code, frame in prices.items():
            available, status = current_active_segment(frame, evaluation_date, lookback_rows)
            counters[status] = counters.get(status, 0) + 1
            if status != "OK":
                continue
            try:
                metrics = main.metrics(
                    available[["Date", "Open", "High", "Low", "Close", "Volume"]]
                )
                current_volume = float(pd.to_numeric(available["Volume"], errors="coerce").iloc[-1])
                raw_close = float(pd.to_numeric(available["RawClose"], errors="coerce").iloc[-1])
                metrics["trading_value"] = raw_close * current_volume
                if not np.isfinite(metrics["trading_value"]) or metrics["trading_value"] <= 0:
                    counters["NON_POSITIVE_CURRENT_TRADING_VALUE"] = counters.get(
                        "NON_POSITIVE_CURRENT_TRADING_VALUE", 0
                    ) + 1
                    continue
                score, reason, breakdown = main.score(metrics, min_trading_value)
                member = metadata[code]
                rows.append(
                    {
                        "code": code,
                        "name": member.name,
                        "market": member.market,
                        "sector33": member.sector33,
                        "score": score,
                        "reason": reason,
                        "history_segment_start": pd.Timestamp(available["Date"].iloc[0]).date().isoformat(),
                        "history_segment_rows": len(available),
                        "history_freshness_status": "OK",
                        **breakdown,
                        **metrics,
                    }
                )
            except Exception:
                counters["SCORING_ERROR"] = counters.get("SCORING_ERROR", 0) + 1
                continue
        base_frame = pd.DataFrame(rows)
        if base_frame.empty:
            continue
        base_frame = base_frame.sort_values(
            ["score", "return_20d", "volume_ratio"],
            ascending=[False, False, False],
            na_position="last",
        )
        day = evaluation_date.date().isoformat()
        ranked = main.enrich_ranking_features(base_frame, historical, day, top_limit)
        ranked = main.attach_relative_strength(ranked)
        ranked = rs_lifecycle.attach(ranked, historical, day)
        columns = [column for column in main.ranking_history_columns() if column in ranked.columns]
        columns += [column for column in ranked.columns if column not in columns]
        ranked = ranked[columns]
        historical = pd.concat([historical, ranked], ignore_index=True)
        coverage_rows.append(
            {
                "date": day,
                "available_symbol_count": len(base_frame),
                "ranked_count": len(ranked),
                "top_count": int(
                    (pd.to_numeric(ranked["rank"], errors="coerce") <= top_limit).sum()
                ),
                "sector_count": int(
                    ranked["sector33"].fillna("").astype(str).str.strip().replace("nan", "").nunique()
                ),
                "minimum_coverage_ratio": minimum_coverage_ratio,
                **{f"freshness_{key.lower()}_count": value for key, value in counters.items()},
            }
        )
        for key, value in counters.items():
            _FRESHNESS_AUDIT.append(
                {"date": day, "status": key, "symbol_count": int(value)}
            )
    if not historical.empty:
        historical["code"] = historical["code"].map(main.normalize_code)
        historical = (
            historical.drop_duplicates(["date", "code"], keep="last")
            .sort_values(["date", "rank"])
            .reset_index(drop=True)
        )
    return historical, pd.DataFrame(coverage_rows)


def main_cli() -> int:
    args = base.parse_args()
    base.build_rankings = build_rankings_fresh
    result = base.main_cli()
    output_dir = Path(args.output_dir)
    audit = pd.DataFrame(_FRESHNESS_AUDIT)
    audit.to_csv(output_dir / "backfill_freshness_audit.csv", index=False)
    manifest_path = output_dir / "backfill_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "detailed_freshness_wrapper_version": VERSION,
            "ranking_session_filter": "POSITIVE_VOLUME_ON_EVALUATION_DATE",
            "history_segment_rule": f"RESET_AFTER_CALENDAR_GAP_GT_{MAX_HISTORY_GAP_DAYS}_DAYS",
            "minimum_history_within_active_segment": historical_backfill.MIN_HISTORY_ROWS,
            "freshness_audit_rows": len(audit),
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        history = pd.read_csv(output_dir / "historical_ranking.csv", low_memory=False)
        if "history_freshness_status" not in history.columns:
            raise RuntimeError("freshness status missing from ranking")
        if not history["history_freshness_status"].eq("OK").all():
            raise RuntimeError("non-fresh row entered detailed ranking")
        if pd.to_numeric(history["trading_value"], errors="coerce").le(0).any():
            raise RuntimeError("non-positive trading value entered detailed ranking")
    return result


if __name__ == "__main__":
    raise SystemExit(main_cli())
