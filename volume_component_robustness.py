"""Cross-fold robustness study for the governed volume-ratio score component.

The study recreates multiple disjoint, sector-stratified current-JPX symbol
folds.  Within each fold it compares the governed baseline ranking against a
strict distribution-preserving removal of ``score_volume_ratio``.  All outputs
are research-only, use next-session executable prices, and can never mutate
production state or automatically change strategy weights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

import historical_backfill as backfill
import main
import portfolio_exit_lab as exit_lab
import portfolio_regime_attribution as attribution
import portfolio_research as portfolio
import replay
import score_component_ablation as ablation

ROBUSTNESS_VERSION = "2026-07-11-volume-component-cross-fold-v1"
BASELINE_VARIANT = "baseline"
TEST_VARIANT = "drop_volume_ratio"
DEFAULT_REGISTRY = "research/volume_component_robustness.yaml"
DEFAULT_CACHE = "data/jpx_list_cache.csv"
DEFAULT_CONFIG = "config.yaml"
DEFAULT_OUTPUT_DIR = "output/volume_component_robustness"


def sha256_text(values: list[str]) -> str:
    payload = "\n".join(values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_registry(path: str = DEFAULT_REGISTRY) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    registry = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(registry, dict) or "validation_gate" not in registry:
        raise ValueError("volume robustness registry is invalid")
    return registry


def sector_round_robin(members: list[backfill.UniverseMember]) -> list[backfill.UniverseMember]:
    groups: dict[str, list[backfill.UniverseMember]] = {}
    for member in sorted(members, key=lambda item: item.code):
        groups.setdefault(member.sector33 or "未分類", []).append(member)
    positions = {sector: 0 for sector in groups}
    sectors = sorted(groups)
    ordered: list[backfill.UniverseMember] = []
    while True:
        progressed = False
        for sector in sectors:
            position = positions[sector]
            if position < len(groups[sector]):
                ordered.append(groups[sector][position])
                positions[sector] += 1
                progressed = True
        if not progressed:
            break
    return ordered


def prepare_folds(
    cache_path: str,
    config_path: str,
    output_dir: str,
    fold_count: int,
    symbols_per_fold: int,
) -> dict[str, Any]:
    if fold_count < 2 or symbols_per_fold < 4:
        raise ValueError("fold_count must be >=2 and symbols_per_fold >=4")
    config = backfill.load_config(config_path)
    universe = backfill.load_current_universe(cache_path, config)
    required = fold_count * symbols_per_fold
    if len(universe) < required:
        raise ValueError(f"current universe has {len(universe)} symbols; {required} required")
    ordered = sector_round_robin(universe)[:required]
    folds = [ordered[index::fold_count] for index in range(fold_count)]
    if any(len(fold) != symbols_per_fold for fold in folds):
        raise RuntimeError("fold allocation did not produce equal fold sizes")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    fold_records: list[dict[str, Any]] = []
    all_codes: set[str] = set()
    for index, members in enumerate(folds, start=1):
        fold_id = f"fold_{index:02d}"
        fold_dir = root / fold_id
        fold_dir.mkdir(parents=True, exist_ok=True)
        codes = [member.code for member in members]
        overlap = sorted(all_codes & set(codes))
        if overlap:
            raise RuntimeError(f"fold overlap detected: {overlap[:5]}")
        all_codes.update(codes)
        cache = pd.DataFrame([
            {
                "コード": member.code,
                "銘柄名": member.name,
                "市場・商品区分": member.market,
                "33業種区分": member.sector33,
            }
            for member in members
        ])
        cache_path_out = fold_dir / "jpx_fold_cache.csv"
        cache.to_csv(cache_path_out, index=False)
        sector_counts = cache["33業種区分"].fillna("未分類").value_counts().sort_index().to_dict()
        manifest = {
            "robustness_version": ROBUSTNESS_VERSION,
            "fold_id": fold_id,
            "fold_index": index,
            "symbol_count": len(codes),
            "codes": codes,
            "codes_sha256": sha256_text(codes),
            "sector_counts": sector_counts,
            "source_cache_sha256": sha256_file(cache_path),
            "research_only": True,
            "promotion_evidence_allowed": False,
            "production_state_mutations": [],
        }
        manifest_path = fold_dir / "fold_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        fold_records.append({
            "fold_id": fold_id,
            "symbol_count": len(codes),
            "codes_sha256": manifest["codes_sha256"],
            "cache_path": str(cache_path_out),
            "manifest_path": str(manifest_path),
            "sector_count": len(sector_counts),
        })

    overlap_matrix: list[dict[str, Any]] = []
    for left_index, left in enumerate(folds):
        left_codes = {member.code for member in left}
        for right_index, right in enumerate(folds):
            right_codes = {member.code for member in right}
            overlap_matrix.append({
                "left_fold": f"fold_{left_index + 1:02d}",
                "right_fold": f"fold_{right_index + 1:02d}",
                "overlap_count": len(left_codes & right_codes) if left_index != right_index else len(left_codes),
            })
    pd.DataFrame(fold_records).to_csv(root / "fold_index.csv", index=False)
    pd.DataFrame(overlap_matrix).to_csv(root / "fold_overlap_matrix.csv", index=False)
    global_manifest = {
        "robustness_version": ROBUSTNESS_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fold_count": fold_count,
        "symbols_per_fold": symbols_per_fold,
        "selected_symbol_count": len(all_codes),
        "all_codes_sha256": sha256_text(sorted(all_codes)),
        "cross_fold_overlap_count": 0,
        "sector_stratified": True,
        "research_only": True,
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "production_state_mutations": [],
    }
    (root / "folds_manifest.json").write_text(
        json.dumps(global_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "fold_index": pd.DataFrame(fold_records),
        "overlap_matrix": pd.DataFrame(overlap_matrix),
        "manifest": global_manifest,
    }


def simulate_variant(
    history: pd.DataFrame,
    prices: pd.DataFrame,
    variant: str,
    periods: dict[str, tuple[pd.Timestamp, pd.Timestamp]] | None = None,
    top_limit: int = 100,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], pd.DataFrame, replay.ReplayResult]:
    variant_history = ablation.build_variant_history(history, variant, top_limit=top_limit)
    replay_result = replay.run_walk_forward_replay(variant_history, top_limit=top_limit)
    signals = replay_result.signals
    if signals is None or signals.empty:
        raise RuntimeError(f"{variant} replay produced no signals")
    aligned_prices = attribution.align_prices_to_signal_window(signals, prices)
    selected_periods = periods or exit_lab.period_ranges(signals)
    period_metrics, results = ablation.simulate_periods(signals, aligned_prices, variant, selected_periods)
    return period_metrics, results, variant_history, replay_result


def analyze_fold_frames(
    history: pd.DataFrame,
    prices: pd.DataFrame,
    fold_id: str,
    registry: dict[str, Any],
    top_limit: int = 100,
) -> dict[str, pd.DataFrame]:
    baseline_history = ablation.build_variant_history(history, BASELINE_VARIANT, top_limit=top_limit)
    baseline_replay = replay.run_walk_forward_replay(baseline_history, top_limit=top_limit)
    if baseline_replay.signals is None or baseline_replay.signals.empty:
        raise RuntimeError("baseline fold replay produced no signals")
    aligned_prices = attribution.align_prices_to_signal_window(baseline_replay.signals, prices)
    periods = exit_lab.period_ranges(baseline_replay.signals)
    baseline_metrics, baseline_results = ablation.simulate_periods(
        baseline_replay.signals, aligned_prices, BASELINE_VARIANT, periods
    )

    tested_history = ablation.build_variant_history(history, TEST_VARIANT, top_limit=top_limit)
    distribution = ablation.validate_distribution_preservation(baseline_history, tested_history)
    tested_replay = replay.run_walk_forward_replay(tested_history, top_limit=top_limit)
    if tested_replay.signals is None or tested_replay.signals.empty:
        raise RuntimeError("drop-volume fold replay produced no signals")
    tested_metrics, tested_results = ablation.simulate_periods(
        tested_replay.signals, aligned_prices, TEST_VARIANT, periods
    )

    metrics = pd.concat([baseline_metrics, tested_metrics], ignore_index=True)
    baseline_by_period = {
        period: metrics[(metrics["variant"] == BASELINE_VARIANT) & (metrics["period"] == period)].iloc[0]
        for period in ("full", "early", "late")
    }
    tested_by_period = {
        period: metrics[(metrics["variant"] == TEST_VARIANT) & (metrics["period"] == period)].iloc[0]
        for period in ("full", "early", "late")
    }
    test = ablation.paired_sign_flip(
        baseline_results["full"]["equity"], tested_results["full"]["equity"], seed=20260711
    )
    gate = registry["validation_gate"]
    adequate = (
        int(baseline_by_period["full"]["closed_trades"]) >= int(gate["minimum_full_trades_per_fold"])
        and int(tested_by_period["full"]["closed_trades"]) >= int(gate["minimum_full_trades_per_fold"])
        and int(baseline_by_period["early"]["closed_trades"]) >= int(gate["minimum_subperiod_trades_per_fold"])
        and int(baseline_by_period["late"]["closed_trades"]) >= int(gate["minimum_subperiod_trades_per_fold"])
        and int(tested_by_period["early"]["closed_trades"]) >= int(gate["minimum_subperiod_trades_per_fold"])
        and int(tested_by_period["late"]["closed_trades"]) >= int(gate["minimum_subperiod_trades_per_fold"])
    )
    full_delta = float(tested_by_period["full"]["excess_total_return"]) - float(
        baseline_by_period["full"]["excess_total_return"]
    )
    early_delta = float(tested_by_period["early"]["excess_total_return"]) - float(
        baseline_by_period["early"]["excess_total_return"]
    )
    late_delta = float(tested_by_period["late"]["excess_total_return"]) - float(
        baseline_by_period["late"]["excess_total_return"]
    )
    fold_status = "INSUFFICIENT"
    if adequate:
        fold_status = "REMOVAL_HURTS" if full_delta < 0 else "REMOVAL_IMPROVES_OR_NEUTRAL"
    summary = pd.DataFrame([{
        "fold_id": fold_id,
        "baseline_full_trades": int(baseline_by_period["full"]["closed_trades"]),
        "tested_full_trades": int(tested_by_period["full"]["closed_trades"]),
        "baseline_total_return": float(baseline_by_period["full"]["total_return"]),
        "tested_total_return": float(tested_by_period["full"]["total_return"]),
        "baseline_excess_return": float(baseline_by_period["full"]["excess_total_return"]),
        "tested_excess_return": float(tested_by_period["full"]["excess_total_return"]),
        "baseline_max_drawdown": float(baseline_by_period["full"]["max_drawdown"]),
        "tested_max_drawdown": float(tested_by_period["full"]["max_drawdown"]),
        "delta_excess_return": full_delta,
        "delta_max_drawdown": float(tested_by_period["full"]["max_drawdown"]) - float(
            baseline_by_period["full"]["max_drawdown"]
        ),
        "early_delta_excess": early_delta,
        "late_delta_excess": late_delta,
        "sample_adequate": adequate,
        "fold_status": fold_status,
        "distribution_preserved": bool(distribution["score_multiset_equal"].all()),
        "baseline_signal_count": len(baseline_replay.signals),
        "tested_signal_count": len(tested_replay.signals),
        "baseline_lookahead_violations": baseline_replay.manifest.get("lookahead_violations", 0),
        "tested_lookahead_violations": tested_replay.manifest.get("lookahead_violations", 0),
        **test,
    }])

    equity_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    for variant, results in ((BASELINE_VARIANT, baseline_results), (TEST_VARIANT, tested_results)):
        for period, result in results.items():
            equity = result["equity"].copy()
            if not equity.empty:
                equity["fold_id"] = fold_id
                equity["variant"] = variant
                equity["period"] = period
                equity_frames.append(equity)
            trades = result["trades"].copy()
            if not trades.empty:
                trades["fold_id"] = fold_id
                trades["variant"] = variant
                trades["period"] = period
                trade_frames.append(trades)
    return {
        "summary": summary,
        "period_metrics": metrics.assign(fold_id=fold_id),
        "distribution_audit": distribution.assign(fold_id=fold_id, variant=TEST_VARIANT),
        "equity": pd.concat(equity_frames, ignore_index=True) if equity_frames else pd.DataFrame(),
        "trades": pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame(),
    }


def write_fold_outputs(
    results: dict[str, pd.DataFrame],
    output_dir: str,
    provenance_path: str,
    fold_manifest_path: str,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    fold_manifest = json.loads(Path(fold_manifest_path).read_text(encoding="utf-8"))
    paths = {
        "summary": output / "volume_fold_summary.csv",
        "period_metrics": output / "volume_fold_period_metrics.csv",
        "distribution": output / "volume_fold_distribution_audit.csv",
        "equity": output / "volume_fold_equity.csv",
        "trades": output / "volume_fold_trades.csv",
        "manifest": output / "volume_fold_analysis_manifest.json",
    }
    mapping = {
        "summary": "summary",
        "period_metrics": "period_metrics",
        "distribution": "distribution_audit",
        "equity": "equity",
        "trades": "trades",
    }
    for path_key, result_key in mapping.items():
        results[result_key].to_csv(paths[path_key], index=False)
    manifest = {
        "robustness_version": ROBUSTNESS_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fold_id": fold_manifest["fold_id"],
        "fold_codes_sha256": fold_manifest["codes_sha256"],
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "daily_score_distribution_preserved": bool(results["distribution_audit"]["score_multiset_equal"].all()),
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"paths": {key: str(value) for key, value in paths.items()}, "manifest": manifest}


def aggregate_folds(fold_root: str, registry: dict[str, Any]) -> dict[str, pd.DataFrame | dict[str, Any]]:
    root = Path(fold_root)
    summary_paths = sorted(root.glob("fold_*/analysis/volume_fold_summary.csv"))
    equity_paths = sorted(root.glob("fold_*/analysis/volume_fold_equity.csv"))
    if not summary_paths or len(summary_paths) != len(equity_paths):
        raise RuntimeError("fold summaries/equities are missing or mismatched")
    summaries = pd.concat([pd.read_csv(path) for path in summary_paths], ignore_index=True)
    equities = pd.concat([pd.read_csv(path) for path in equity_paths], ignore_index=True)
    evaluable = summaries[summaries["sample_adequate"].fillna(False)].copy()
    gate = registry["validation_gate"]
    harm_fraction = float((evaluable["delta_excess_return"] < 0).mean()) if len(evaluable) else 0.0
    median_delta = float(evaluable["delta_excess_return"].median()) if len(evaluable) else None
    early_harm_fraction = float((evaluable["early_delta_excess"] <= 0).mean()) if len(evaluable) else 0.0
    late_harm_fraction = float((evaluable["late_delta_excess"] <= 0).mean()) if len(evaluable) else 0.0

    full_equity = equities[equities["period"].astype(str).eq("full")].copy()
    full_equity["date"] = pd.to_datetime(full_equity["date"], errors="coerce").dt.date.astype(str)
    daily = full_equity.pivot_table(
        index=["fold_id", "date"], columns="variant", values="daily_return", aggfunc="last"
    ).reset_index()
    daily = daily.dropna(subset=[BASELINE_VARIANT, TEST_VARIANT])
    aggregate_daily = daily.groupby("date", as_index=False)[[BASELINE_VARIANT, TEST_VARIANT]].mean()
    baseline_equity = aggregate_daily[["date", BASELINE_VARIANT]].rename(columns={BASELINE_VARIANT: "daily_return"})
    tested_equity = aggregate_daily[["date", TEST_VARIANT]].rename(columns={TEST_VARIANT: "daily_return"})
    aggregate_test = ablation.paired_sign_flip(baseline_equity, tested_equity, seed=20260712)

    robust = (
        len(evaluable) >= int(gate["minimum_evaluable_folds"])
        and harm_fraction >= float(gate["minimum_harm_direction_fraction"])
        and median_delta is not None
        and median_delta < 0
        and aggregate_test["two_sided_p_value"] is not None
        and aggregate_test["two_sided_p_value"] <= float(gate["maximum_two_sided_p_value"])
        and aggregate_test["ci_high"] is not None
        and aggregate_test["ci_high"] < 0
    )
    directional = (
        len(evaluable) >= int(gate["minimum_evaluable_folds"])
        and harm_fraction >= float(gate["minimum_harm_direction_fraction"])
        and median_delta is not None
        and median_delta < 0
    )
    status = "ROBUSTLY_SUPPORTED" if robust else "DIRECTIONALLY_SUPPORTED" if directional else "NOT_SUPPORTED"
    aggregate_summary = pd.DataFrame([{
        "fold_count": len(summaries),
        "evaluable_fold_count": len(evaluable),
        "harm_fold_count": int((evaluable["delta_excess_return"] < 0).sum()),
        "harm_direction_fraction": harm_fraction,
        "early_harm_fraction": early_harm_fraction,
        "late_harm_fraction": late_harm_fraction,
        "median_delta_excess_return": median_delta,
        "mean_delta_excess_return": float(evaluable["delta_excess_return"].mean()) if len(evaluable) else None,
        "median_delta_max_drawdown": float(evaluable["delta_max_drawdown"].median()) if len(evaluable) else None,
        **aggregate_test,
        "robustness_status": status,
        "automatic_weight_change_allowed": False,
    }])
    manifest = {
        "robustness_version": ROBUSTNESS_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fold_count": len(summaries),
        "evaluable_fold_count": len(evaluable),
        "robustness_status": status,
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
    }
    return {
        "fold_summary": summaries,
        "aggregate_daily": aggregate_daily,
        "aggregate_summary": aggregate_summary,
        "manifest": manifest,
    }


def write_aggregate_outputs(results: dict[str, Any], output_dir: str) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "fold_summary": output / "volume_cross_fold_summary.csv",
        "aggregate_daily": output / "volume_cross_fold_daily_returns.csv",
        "aggregate_summary": output / "volume_component_robustness_summary.csv",
        "excel": output / "volume_component_robustness.xlsx",
        "manifest": output / "volume_component_robustness_manifest.json",
    }
    results["fold_summary"].to_csv(paths["fold_summary"], index=False)
    results["aggregate_daily"].to_csv(paths["aggregate_daily"], index=False)
    results["aggregate_summary"].to_csv(paths["aggregate_summary"], index=False)
    paths["manifest"].write_text(json.dumps(results["manifest"], ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([results["manifest"]]).to_excel(writer, sheet_name="Manifest", index=False)
        results["aggregate_summary"].to_excel(writer, sheet_name="Robustness Summary", index=False)
        results["fold_summary"].to_excel(writer, sheet_name="Fold Results", index=False)
        results["aggregate_daily"].to_excel(writer, sheet_name="Aggregate Daily", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"paths": {key: str(value) for key, value in paths.items()}, "manifest": results["manifest"]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cross-fold volume component robustness study")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-folds")
    prepare.add_argument("--cache", default=DEFAULT_CACHE)
    prepare.add_argument("--config", default=DEFAULT_CONFIG)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--fold-count", type=int, required=True)
    prepare.add_argument("--symbols-per-fold", type=int, required=True)

    analyze = subparsers.add_parser("analyze-fold")
    analyze.add_argument("--history", required=True)
    analyze.add_argument("--prices", required=True)
    analyze.add_argument("--provenance", required=True)
    analyze.add_argument("--fold-manifest", required=True)
    analyze.add_argument("--registry", default=DEFAULT_REGISTRY)
    analyze.add_argument("--output-dir", required=True)
    analyze.add_argument("--top-limit", type=int, default=100)
    analyze.add_argument("--strict", action="store_true")

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--fold-root", required=True)
    aggregate.add_argument("--registry", default=DEFAULT_REGISTRY)
    aggregate.add_argument("--output-dir", required=True)
    aggregate.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before = replay.live_state_hashes()
    if args.command == "prepare-folds":
        result = prepare_folds(args.cache, args.config, args.output_dir, args.fold_count, args.symbols_per_fold)
        print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    elif args.command == "analyze-fold":
        registry = load_registry(args.registry)
        history = ablation.load_history(args.history)
        prices = portfolio.load_prices(args.prices)
        fold_manifest = json.loads(Path(args.fold_manifest).read_text(encoding="utf-8"))
        results = analyze_fold_frames(history, prices, fold_manifest["fold_id"], registry, top_limit=args.top_limit)
        output = write_fold_outputs(results, args.output_dir, args.provenance, args.fold_manifest)
        after = replay.live_state_hashes()
        mutations = [path for path in before if before[path] != after.get(path, "")]
        output["manifest"]["production_state_mutations"] = mutations
        Path(output["paths"]["manifest"]).write_text(
            json.dumps(output["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if args.strict:
            summary = results["summary"].iloc[0]
            if mutations:
                raise RuntimeError(f"production state mutated: {mutations}")
            if not bool(summary["distribution_preserved"]):
                raise RuntimeError("daily score distribution changed")
            if int(summary["baseline_lookahead_violations"]) or int(summary["tested_lookahead_violations"]):
                raise RuntimeError("lookahead violation detected")
        print(results["summary"].to_string(index=False))
    else:
        registry = load_registry(args.registry)
        results = aggregate_folds(args.fold_root, registry)
        output = write_aggregate_outputs(results, args.output_dir)
        after = replay.live_state_hashes()
        mutations = [path for path in before if before[path] != after.get(path, "")]
        output["manifest"]["production_state_mutations"] = mutations
        Path(output["paths"]["manifest"]).write_text(
            json.dumps(output["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if args.strict:
            if mutations:
                raise RuntimeError(f"production state mutated: {mutations}")
            if results["aggregate_summary"].empty:
                raise RuntimeError("aggregate robustness summary is empty")
        print(results["aggregate_summary"].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
