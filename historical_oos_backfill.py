"""Build explicit-period, research-only historical ranking snapshots.

This module is intentionally isolated from production state. It downloads adjusted OHLCV
with a warm-up period, evaluates rankings only inside a requested historical interval, and
keeps future price rows solely for subsequent outcome measurement.

The default universe is the current JPX listed-issue cache. Therefore the output has
survivorship, delisting, and historical membership bias and is permanently non-promotable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import historical_backfill
import historical_price_panel
import main
import relative_strength_lifecycle as rs_lifecycle
import replay

VERSION = "2026-07-21-explicit-period-oos-backfill-v1"
DEFAULT_OUTPUT_DIR = "output/oos-2025"


def stable_stratified_limit(
    members: list[historical_backfill.UniverseMember], max_symbols: int
) -> list[historical_backfill.UniverseMember]:
    """Select a deterministic sector-balanced sample without low-code ordering bias."""
    if max_symbols <= 0 or max_symbols >= len(members):
        return sorted(members, key=lambda item: item.code)
    groups: dict[str, list[historical_backfill.UniverseMember]] = {}
    for member in members:
        groups.setdefault(member.sector33 or "未分類", []).append(member)
    for sector in groups:
        groups[sector] = sorted(
            groups[sector],
            key=lambda item: hashlib.sha256(
                f"historical-oos-sample|{item.code}".encode("utf-8")
            ).hexdigest(),
        )
    selected: list[historical_backfill.UniverseMember] = []
    positions = {sector: 0 for sector in groups}
    sectors = sorted(groups)
    while len(selected) < max_symbols:
        progressed = False
        for sector in sectors:
            position = positions[sector]
            if position < len(groups[sector]):
                selected.append(groups[sector][position])
                positions[sector] += 1
                progressed = True
                if len(selected) >= max_symbols:
                    break
        if not progressed:
            break
    return sorted(selected, key=lambda item: item.code)


def evaluation_dates(
    prices: dict[str, pd.DataFrame],
    evaluation_start: date,
    evaluation_end: date,
    sample_every: int,
    minimum_coverage_ratio: float,
) -> list[pd.Timestamp]:
    dates = historical_backfill.eligible_evaluation_dates(
        prices,
        sample_every=1,
        minimum_coverage_ratio=minimum_coverage_ratio,
    )
    bounded = [
        value
        for value in dates
        if evaluation_start <= value.date() <= evaluation_end
    ]
    step = max(int(sample_every), 1)
    sampled = bounded[::step]
    if bounded and bounded[-1] not in sampled:
        sampled.append(bounded[-1])
    return sampled


def build_rankings(
    members: list[historical_backfill.UniverseMember],
    prices: dict[str, pd.DataFrame],
    config: dict[str, Any],
    evaluation_start: date,
    evaluation_end: date,
    sample_every: int,
    minimum_coverage_ratio: float,
    top_limit: int,
    lookback_rows: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metadata = {member.code: member for member in members}
    dates = evaluation_dates(
        prices,
        evaluation_start,
        evaluation_end,
        sample_every,
        minimum_coverage_ratio,
    )
    historical = pd.DataFrame(columns=main.ranking_history_columns())
    coverage_rows: list[dict[str, Any]] = []
    min_trading_value = int(
        (config.get("market") or {}).get("min_trading_value", 100_000_000)
    )

    for evaluation_date in dates:
        rows: list[dict[str, Any]] = []
        for code, frame in prices.items():
            available = frame[frame["Date"] <= evaluation_date].tail(lookback_rows)
            if len(available) < historical_backfill.MIN_HISTORY_ROWS:
                continue
            try:
                metrics = main.metrics(
                    available[["Date", "Open", "High", "Low", "Close", "Volume"]]
                )
                metrics["trading_value"] = float(available["RawClose"].iloc[-1]) * float(
                    available["Volume"].iloc[-1]
                )
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
                        **breakdown,
                        **metrics,
                    }
                )
            except Exception:
                continue
        base = pd.DataFrame(rows)
        if base.empty:
            continue
        base = base.sort_values(
            ["score", "return_20d", "volume_ratio"],
            ascending=[False, False, False],
            na_position="last",
        )
        day = evaluation_date.date().isoformat()
        ranked = main.enrich_ranking_features(base, historical, day, top_limit)
        ranked = main.attach_relative_strength(ranked)
        ranked = rs_lifecycle.attach(ranked, historical, day)
        columns = [column for column in main.ranking_history_columns() if column in ranked.columns]
        columns += [column for column in ranked.columns if column not in columns]
        ranked = ranked[columns]
        historical = pd.concat([historical, ranked], ignore_index=True)
        coverage_rows.append(
            {
                "date": day,
                "available_symbol_count": len(base),
                "ranked_count": len(ranked),
                "top_count": int(
                    (pd.to_numeric(ranked["rank"], errors="coerce") <= top_limit).sum()
                ),
                "sector_count": int(
                    ranked["sector33"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .replace("nan", "")
                    .nunique()
                ),
                "minimum_coverage_ratio": minimum_coverage_ratio,
            }
        )
    if not historical.empty:
        historical["code"] = historical["code"].map(main.normalize_code)
        historical = (
            historical.drop_duplicates(["date", "code"], keep="last")
            .sort_values(["date", "rank"])
            .reset_index(drop=True)
        )
    return historical, pd.DataFrame(coverage_rows)


def parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {label}: {value}; expected YYYY-MM-DD") from exc


def write_outputs(
    output_dir: Path,
    history: pd.DataFrame,
    panel: pd.DataFrame,
    coverage: pd.DataFrame,
    quality: pd.DataFrame,
    errors: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    history.to_csv(output_dir / "historical_ranking.csv", index=False)
    panel.to_csv(output_dir / "historical_price_panel.csv", index=False)
    coverage.to_csv(output_dir / "backfill_coverage.csv", index=False)
    quality.to_csv(output_dir / "backfill_data_quality.csv", index=False)
    pd.DataFrame(errors).to_csv(output_dir / "backfill_errors.csv", index=False)
    (output_dir / "backfill_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build explicit-period isolated historical ranking snapshots"
    )
    parser.add_argument("--cache", default="data/jpx_list_cache.csv")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--download-start", default="2023-10-01")
    parser.add_argument("--download-end", default="2026-02-15")
    parser.add_argument("--evaluation-start", default="2025-01-01")
    parser.add_argument("--evaluation-end", default="2025-12-31")
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--sample-every", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--minimum-coverage-ratio", type=float, default=0.70)
    parser.add_argument("--top-limit", type=int, default=100)
    parser.add_argument("--lookback-rows", type=int, default=260)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    download_start = parse_date(args.download_start, "download-start")
    download_end = parse_date(args.download_end, "download-end")
    evaluation_start = parse_date(args.evaluation_start, "evaluation-start")
    evaluation_end = parse_date(args.evaluation_end, "evaluation-end")
    if not download_start < evaluation_start <= evaluation_end < download_end:
        raise ValueError(
            "expected download_start < evaluation_start <= evaluation_end < download_end"
        )

    before_state = replay.live_state_hashes()
    config = historical_backfill.load_config(args.config)
    full_universe = historical_backfill.load_current_universe(args.cache, config)
    selected = stable_stratified_limit(full_universe, args.max_symbols)
    prices, errors = historical_backfill.download_price_history(
        selected,
        download_start,
        download_end,
        batch_size=args.batch_size,
    )
    quality = historical_backfill.data_quality_table(selected, prices)
    history, coverage = build_rankings(
        selected,
        prices,
        config,
        evaluation_start,
        evaluation_end,
        args.sample_every,
        args.minimum_coverage_ratio,
        args.top_limit,
        args.lookback_rows,
    )
    panel = historical_price_panel.flatten_price_panel(selected, prices)
    after_state = replay.live_state_hashes()
    mutations = [
        path for path in before_state if before_state[path] != after_state.get(path, "")
    ]

    output_dir = Path(args.output_dir)
    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "download_start": download_start.isoformat(),
        "download_end": download_end.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
        "sample_every_trading_days": args.sample_every,
        "lookback_rows": args.lookback_rows,
        "top_limit": args.top_limit,
        "current_jpx_universe_count": len(full_universe),
        "selected_universe_count": len(selected),
        "selection_method": (
            "ALL_CURRENT_LISTED_ISSUES"
            if args.max_symbols <= 0 or args.max_symbols >= len(full_universe)
            else "DETERMINISTIC_SECTOR_BALANCED_HASH_SAMPLE"
        ),
        "downloaded_symbol_count": int((quality["row_count"] > 0).sum())
        if not quality.empty
        else 0,
        "sufficient_history_symbol_count": int((quality["status"] == "OK").sum())
        if not quality.empty
        else 0,
        "ranking_date_count": int(history["date"].nunique()) if not history.empty else 0,
        "ranking_row_count": len(history),
        "price_panel_row_count": len(panel),
        "first_ranking_date": str(history["date"].min()) if not history.empty else "",
        "last_ranking_date": str(history["date"].max()) if not history.empty else "",
        "first_panel_date": str(panel["date"].min()) if not panel.empty else "",
        "last_panel_date": str(panel["date"].max()) if not panel.empty else "",
        "download_error_count": len(errors),
        "price_adjustment": "YFINANCE_ADJUSTED_OHLC_WITH_RAW_CLOSE_VOLUME_FOR_TRADING_VALUE",
        "universe_bias": "CURRENT_LIST_ONLY_SURVIVORSHIP_DELISTING_AND_MEMBERSHIP_BIAS",
        "temporal_isolation": "RANKING_INPUTS_FILTERED_TO_DATE_LE_SIGNAL_DATE",
        "design_period_relationship": "2025_PRECEDES_2026_HEALTHY_V1_V2_DESIGN",
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "production_state_mutations": mutations,
        "research_only": True,
        "jpx_cache_sha256": historical_backfill.sha256_file(args.cache),
    }
    write_outputs(output_dir, history, panel, coverage, quality, errors, manifest)

    if args.strict:
        if mutations:
            raise RuntimeError(f"production state mutated: {mutations}")
        if history.empty or history["date"].nunique() < 20:
            raise RuntimeError("historical ranking produced fewer than 20 evaluation dates")
        if history.duplicated(["date", "code"]).any():
            raise RuntimeError("duplicate date/code rows in historical ranking")
        if panel.empty:
            raise RuntimeError("historical price panel is empty")
        sufficient_ratio = (
            float((quality["status"] == "OK").mean()) if len(quality) else 0.0
        )
        if sufficient_ratio < 0.50:
            raise RuntimeError(
                f"less than 50% of selected symbols have sufficient history: {sufficient_ratio:.1%}"
            )
        ranking_dates = pd.to_datetime(history["date"], errors="coerce")
        if ranking_dates.min().date() < evaluation_start or ranking_dates.max().date() > evaluation_end:
            raise RuntimeError("ranking dates escaped the evaluation interval")
        if pd.to_datetime(panel["date"], errors="coerce").max().date() <= evaluation_end:
            raise RuntimeError("price panel lacks post-evaluation outcome rows")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
