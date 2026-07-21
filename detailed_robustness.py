"""Run leave-one-sector, random-placebo, and fixed evidence scorecard checks."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import detailed_oos_analysis as core

VERSION = "2026-07-22-detailed-robustness-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ranking", required=True)
    parser.add_argument("--universe-outcomes", required=True)
    parser.add_argument("--selection-events", required=True)
    parser.add_argument("--method-summary", required=True)
    parser.add_argument("--rank-ic-summary", required=True)
    parser.add_argument("--protocol", default="research/detailed_oos_protocol.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol, _ = core.load_protocol(args.protocol)
    ranking = pd.read_csv(
        args.ranking,
        dtype={"code": str},
        usecols=lambda column: column in {"date", "code"},
        low_memory=False,
    )
    outcomes = pd.read_csv(
        args.universe_outcomes,
        dtype={"code": str},
        usecols=lambda column: column
        in {"signal_date", "code", "horizon_sessions", "net_return"},
        low_memory=False,
    )
    selection_columns = {
        "signal_date",
        "code",
        "name",
        "sector33",
        "method_rank",
        "method_score",
        "method",
        "horizon_sessions",
        "net_return",
        "market_excess_net",
        "sector_excess_net",
        "mfe",
        "mae",
    }
    selections = pd.read_csv(
        args.selection_events,
        dtype={"code": str},
        usecols=lambda column: column in selection_columns,
        low_memory=False,
    )
    summary = pd.read_csv(args.method_summary, low_memory=False)
    rank_ic = pd.read_csv(args.rank_ic_summary, low_memory=False)
    for frame in (ranking, outcomes, selections):
        frame["code"] = frame["code"].astype(str).str.split(".").str[0].str.zfill(4)
    ranking["date"] = pd.to_datetime(ranking["date"], errors="coerce").dt.normalize()
    outcomes["signal_date"] = pd.to_datetime(
        outcomes["signal_date"], errors="coerce"
    ).dt.normalize()
    selections["signal_date"] = pd.to_datetime(
        selections["signal_date"], errors="coerce"
    ).dt.normalize()
    selections["eligible"] = True
    selections["method_rank"] = pd.to_numeric(selections["method_rank"], errors="coerce")
    selections["method_score"] = pd.to_numeric(selections["method_score"], errors="coerce")
    selections["year"] = selections["signal_date"].dt.year.astype(int)
    selections["max_close_drawdown"] = np.nan
    available = tuple(
        sorted(int(value) for value in outcomes["horizon_sessions"].dropna().unique())
    )
    effective = core.Protocol(
        horizons=tuple(value for value in protocol.horizons if value in available),
        top_sizes=protocol.top_sizes,
        cost_bps=protocol.cost_bps,
        random_repetitions=protocol.random_repetitions,
        primary_horizons=tuple(
            value for value in protocol.primary_horizons if value in available
        ),
        primary_top_sizes=protocol.primary_top_sizes,
        minimum_years_positive=protocol.minimum_years_positive,
        minimum_rank_ic_positive_rate=protocol.minimum_rank_ic_positive_rate,
        minimum_leave_one_sector_positive_rate=protocol.minimum_leave_one_sector_positive_rate,
    )
    leave_sector = core.leave_one_sector_out(selections, effective.top_sizes)
    placebo = core.random_placebo(
        ranking,
        outcomes,
        summary,
        effective.top_sizes,
        effective.horizons,
        effective.random_repetitions,
    )
    scorecard = core.evidence_scorecard(
        summary,
        rank_ic,
        leave_sector,
        placebo,
        effective,
    )
    leave_sector.to_csv(output_dir / "leave_one_sector_out.csv", index=False)
    placebo.to_csv(output_dir / "random_placebo.csv", index=False)
    scorecard.to_csv(output_dir / "evidence_scorecard.csv", index=False)
    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "years": sorted(int(value) for value in selections["year"].unique()),
        "available_horizons": list(effective.horizons),
        "top_sizes": list(effective.top_sizes),
        "random_placebo_repetitions": effective.random_repetitions,
        "leave_one_sector_rows": len(leave_sector),
        "random_placebo_rows": len(placebo),
        "scorecard_rows": len(scorecard),
        "research_only": True,
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "ranking_sha256": core.sha256_file(args.ranking),
        "outcomes_sha256": core.sha256_file(args.universe_outcomes),
        "selection_events_sha256": core.sha256_file(args.selection_events),
        "method_summary_sha256": core.sha256_file(args.method_summary),
        "rank_ic_summary_sha256": core.sha256_file(args.rank_ic_summary),
        "protocol_sha256": core.sha256_file(args.protocol),
    }
    (output_dir / "robustness_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.strict:
        if leave_sector.empty or placebo.empty or scorecard.empty:
            raise RuntimeError("robustness output is empty")
        if set(scorecard["promotion_status"]) != {
            "RESEARCH_SUPPORT_ONLY_NON_PROMOTABLE"
        }:
            raise RuntimeError("invalid promotion status")
        if not placebo["one_sided_empirical_p"].between(0, 1).all():
            raise RuntimeError("invalid placebo p-values")
        if manifest["production_state_mutations"]:
            raise RuntimeError("production state mutated")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
