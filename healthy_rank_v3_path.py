"""Freshness-safe price-path comparison for Healthy Rank v3 holdout."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import detailed_oos_analysis as core
import healthy_rank_v3

VERSION = "2026-07-22-healthy-rank-v3-path-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enriched-ranking", required=True)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-horizon", type=int, default=60)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ranking = pd.read_csv(args.enriched_ranking, dtype={"code": str}, low_memory=False)
    ranking["code"] = ranking["code"].astype(str).str.split(".").str[0].str.zfill(4)
    ranking["date"] = pd.to_datetime(ranking["date"], errors="coerce").dt.normalize()
    ranked = healthy_rank_v3.attach(ranking)

    frames: list[pd.DataFrame] = []
    for method, rank_column, score_column in (
        ("production", "rank", "score"),
        ("healthy_v1", "healthy_rank", "healthy_selection_score"),
        ("healthy_v3", "healthy_v3_rank", "healthy_v3_selection_score"),
    ):
        columns = [
            column
            for column in ["date", "code", "name", "sector33", rank_column, score_column]
            if column in ranked
        ]
        frame = ranked[columns].copy()
        frame[rank_column] = pd.to_numeric(frame[rank_column], errors="coerce")
        frame = frame[frame[rank_column].notna() & frame[rank_column].le(100)]
        frame = frame.rename(
            columns={
                "date": "signal_date",
                rank_column: "method_rank",
                score_column: "method_score",
            }
        )
        frame["method"] = method
        frame["eligible"] = True
        frames.append(frame)
    methods = pd.concat(frames, ignore_index=True, sort=False)

    panel = pd.read_csv(args.prices, dtype={"code": str}, low_memory=False)
    panel["code"] = panel["code"].astype(str).str.split(".").str[0].str.zfill(4)
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.normalize()
    for column in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume"):
        if column in panel:
            panel[column] = pd.to_numeric(panel[column], errors="coerce")
    panel = panel.dropna(
        subset=["date", "code", "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"]
    )
    panel = panel[
        panel[["adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"]]
        .gt(0)
        .all(axis=1)
    ]

    detail, summary = core.path_quality(
        methods,
        core.price_lookup(panel),
        args.max_horizon,
    )
    detail.to_csv(output_dir / "path_detail.csv", index=False)
    summary.to_csv(output_dir / "path_summary.csv", index=False)
    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_version": healthy_rank_v3.VERSION,
        "max_horizon": args.max_horizon,
        "candidate_rows": len(methods),
        "path_detail_rows": len(detail),
        "path_summary_rows": len(summary),
        "methods": sorted(detail["method"].unique().tolist()) if len(detail) else [],
        "years": sorted(int(value) for value in detail["year"].unique()) if len(detail) else [],
        "research_only": True,
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "ranking_sha256": core.sha256_file(args.enriched_ranking),
        "prices_sha256": core.sha256_file(args.prices),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        if detail.empty or summary.empty:
            raise RuntimeError("v3 path output is empty")
        if set(manifest["years"]) != {2018, 2019, 2020, 2021}:
            raise RuntimeError("holdout years missing from path output")
        if set(manifest["methods"]) != {"production", "healthy_v1", "healthy_v3"}:
            raise RuntimeError("path method set mismatch")
        if not detail["path_data_quality"].eq("OK").all():
            raise RuntimeError("non-fresh path entered output")
        if manifest["production_state_mutations"]:
            raise RuntimeError("production state mutated")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
