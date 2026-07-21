"""Run Healthy v1 gate ablations and simple baseline comparisons in isolation."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import detailed_oos_analysis as core

VERSION = "2026-07-22-detailed-ablation-baseline-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enriched-ranking", required=True)
    parser.add_argument("--universe-outcomes", required=True)
    parser.add_argument("--protocol", default="research/detailed_oos_protocol.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol, _ = core.load_protocol(args.protocol)
    ranking_columns = {
        "date",
        "code",
        "name",
        "sector33",
        "healthy_exclusion_reasons",
        "healthy_selection_score",
        "return_5d",
        "return_20d",
        "healthy_relative_strength_score",
        "ytd_high_streak",
        "volume_ratio",
        "ma20_deviation",
    }
    outcome_columns = {
        "signal_date",
        "code",
        "horizon_sessions",
        "net_return",
        "market_excess_net",
        "mae",
        "mfe",
    }
    ranking = pd.read_csv(
        args.enriched_ranking,
        dtype={"code": str},
        low_memory=False,
        usecols=lambda column: column in ranking_columns,
    )
    outcomes = pd.read_csv(
        args.universe_outcomes,
        dtype={"code": str},
        low_memory=False,
        usecols=lambda column: column in outcome_columns,
    )
    ranking["code"] = ranking["code"].astype(str).str.split(".").str[0].str.zfill(4)
    outcomes["code"] = outcomes["code"].astype(str).str.split(".").str[0].str.zfill(4)
    ranking["date"] = pd.to_datetime(ranking["date"], errors="coerce").dt.normalize()
    outcomes["signal_date"] = pd.to_datetime(
        outcomes["signal_date"], errors="coerce"
    ).dt.normalize()
    available = tuple(
        sorted(int(value) for value in outcomes["horizon_sessions"].dropna().unique())
    )
    horizons = tuple(value for value in protocol.horizons if value in available)
    ablations = core.ablation_summary(
        ranking,
        outcomes,
        protocol.top_sizes,
        horizons,
    )
    baselines = core.summarize_candidate_methods(
        core.baseline_candidates(ranking),
        outcomes,
        protocol.top_sizes,
    )
    ablations.to_csv(output_dir / "healthy_v1_ablation_summary.csv", index=False)
    baselines.to_csv(output_dir / "simple_baseline_summary.csv", index=False)
    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "years": sorted(int(value) for value in ranking["date"].dt.year.dropna().unique()),
        "available_horizons": list(horizons),
        "top_sizes": list(protocol.top_sizes),
        "ablation_rows": len(ablations),
        "baseline_rows": len(baselines),
        "ablation_variants": sorted(
            ablations["ablation_variant"].dropna().unique().tolist()
        )
        if len(ablations)
        else [],
        "baseline_methods": sorted(baselines["method"].dropna().unique().tolist())
        if len(baselines)
        else [],
        "research_only": True,
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "ranking_sha256": core.sha256_file(args.enriched_ranking),
        "outcomes_sha256": core.sha256_file(args.universe_outcomes),
        "protocol_sha256": core.sha256_file(args.protocol),
    }
    (output_dir / "ablation_baseline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.strict:
        if ablations.empty or baselines.empty:
            raise RuntimeError("ablation or baseline output is empty")
        if "ORIGINAL_V1" not in set(ablations["ablation_variant"]):
            raise RuntimeError("original v1 missing from ablation")
        required_baselines = {
            "baseline_return_5d",
            "baseline_return_20d",
            "baseline_simple_balanced",
        }
        if not required_baselines.issubset(set(baselines["method"])):
            raise RuntimeError("required baselines missing")
        if manifest["production_state_mutations"]:
            raise RuntimeError("production state mutated")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
