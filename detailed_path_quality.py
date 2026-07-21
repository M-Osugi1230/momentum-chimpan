"""Compute freshness-safe selected-signal price-path quality in an isolated process."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import detailed_oos_analysis as core

VERSION = "2026-07-22-detailed-path-quality-v2"


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
    panel = pd.read_csv(args.prices, dtype={"code": str}, low_memory=False)
    panel["code"] = panel["code"].astype(str).str.split(".").str[0].str.zfill(4)
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.normalize()
    for column in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume"):
        if column in panel.columns:
            panel[column] = pd.to_numeric(panel[column], errors="coerce")
    panel = panel.dropna(
        subset=["date", "code", "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"]
    )
    panel = panel[
        panel[["adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"]].gt(0).all(axis=1)
    ]
    methods = core.top_method_candidates(ranking, 100)
    detail, summary = core.path_quality(
        methods,
        core.price_lookup(panel),
        args.max_horizon,
    )
    detail.to_csv(output_dir / "path_quality_detail.csv", index=False)
    summary.to_csv(output_dir / "path_quality_summary.csv", index=False)
    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "max_horizon": args.max_horizon,
        "selected_signal_rows": len(methods),
        "path_detail_rows": len(detail),
        "path_summary_rows": len(summary),
        "path_quality_rules": {
            "max_entry_gap_days": core.MAX_ENTRY_GAP_DAYS,
            "max_session_gap_days": core.MAX_SESSION_GAP_DAYS,
            "max_adjacent_price_multiplier": core.MAX_ADJACENT_PRICE_MULTIPLIER,
            "require_positive_volume_for_all_path_sessions": True,
        },
        "research_only": True,
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "ranking_sha256": core.sha256_file(args.enriched_ranking),
        "prices_sha256": core.sha256_file(args.prices),
    }
    (output_dir / "path_quality_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.strict:
        if detail.empty or summary.empty:
            raise RuntimeError("path quality output is empty")
        allowed = {"UP_5_FIRST", "DOWN_5_FIRST", "BOTH_SAME_SESSION", "NEITHER"}
        if not detail["first_touch_5pct"].isin(allowed).all():
            raise RuntimeError("invalid first-touch state")
        if not detail["path_data_quality"].eq("OK").all():
            raise RuntimeError("non-fresh price path entered output")
        if detail["entry_gap_days"].gt(core.MAX_ENTRY_GAP_DAYS).any():
            raise RuntimeError("stale path entry detected")
        if detail["max_session_gap_days"].gt(core.MAX_SESSION_GAP_DAYS).any():
            raise RuntimeError("discontinuous path detected")
        if manifest["production_state_mutations"]:
            raise RuntimeError("production state mutated")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
