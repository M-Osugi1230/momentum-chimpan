"""Pre-registered, disjoint-symbol holdout validation for portfolio hypotheses.

Discovery codes from the regime-attribution study are sealed in a registry and
excluded before selecting a new sector-stratified JPX universe. The baseline and
all hypotheses use the same execution-aware portfolio engine. No hypothesis is
activated automatically and historical current-universe evidence remains
permanently non-promotable.
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
import portfolio_filter_lab as filter_lab
import portfolio_regime_attribution as attribution
import portfolio_research as portfolio
import replay

HOLDOUT_VERSION = "2026-07-11-portfolio-hypothesis-holdout-v1"
DEFAULT_REGISTRY = "research/portfolio_holdout_hypotheses.yaml"
DEFAULT_CACHE = "data/jpx_list_cache.csv"
DEFAULT_CONFIG = "config.yaml"
DEFAULT_OUTPUT_DIR = "output/portfolio_holdout"

BASELINE_SCENARIO = portfolio.PortfolioScenario("holdout_baseline", None, 0.0, 0.01)
BASELINE_EXIT_POLICY = portfolio.ExitPolicy("baseline", 0.08, 0.15, 0.10, 20, True)


def normalize_codes(values: list[Any]) -> list[str]:
    return sorted({main.normalize_code(value) for value in values if main.normalize_code(value).isdigit()})


def codes_sha256(codes: list[str]) -> str:
    payload = "".join(f"{code}\n" for code in normalize_codes(codes))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_registry(path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    registry = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    discovery = registry.get("discovery_design") or {}
    codes = normalize_codes(discovery.get("codes") or [])
    expected_hash = str(discovery.get("codes_sha256") or "")
    if not codes or len(codes) != int(discovery.get("symbol_count") or 0):
        raise ValueError("discovery code registry count mismatch")
    if codes_sha256(codes) != expected_hash:
        raise ValueError("discovery code registry hash mismatch")
    hypotheses = registry.get("hypotheses") or []
    if not hypotheses or len({str(row.get("id")) for row in hypotheses}) != len(hypotheses):
        raise ValueError("holdout hypotheses must have unique non-empty IDs")
    governance = registry.get("governance") or {}
    required_false = (
        "promotion_evidence_allowed",
        "automatic_strategy_change",
        "automatic_hypothesis_activation",
        "production_state_mutation_allowed",
    )
    if any(governance.get(key) is not False for key in required_false):
        raise ValueError("holdout governance must disable promotion, activation and mutation")
    if governance.get("research_only") is not True or governance.get("manual_review_required") is not True:
        raise ValueError("holdout governance must be research-only and manually reviewed")
    return registry


def prepare_holdout_universe(
    registry: dict[str, Any],
    cache_path: str,
    config_path: str,
    max_symbols: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    config = backfill.load_config(config_path)
    full_members = backfill.load_current_universe(cache_path, config)
    discovery_codes = set(normalize_codes((registry.get("discovery_design") or {}).get("codes") or []))
    remaining = [member for member in full_members if member.code not in discovery_codes]
    selected = backfill.stratified_limit(remaining, max_symbols)
    selected_codes = [member.code for member in selected]
    overlap = sorted(discovery_codes & set(selected_codes))
    if overlap:
        raise RuntimeError(f"holdout overlaps discovery universe: {overlap}")
    frame = pd.DataFrame([
        {
            "コード": member.code,
            "銘柄名": member.name,
            "市場・商品区分": member.market,
            "33業種区分": member.sector33,
        }
        for member in selected
    ])
    manifest = {
        "holdout_version": HOLDOUT_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_cache_sha256": sha256_file(cache_path),
        "discovery_codes_sha256": codes_sha256(list(discovery_codes)),
        "discovery_symbol_count": len(discovery_codes),
        "eligible_after_exclusion_count": len(remaining),
        "selected_holdout_symbol_count": len(selected_codes),
        "selected_holdout_codes_sha256": codes_sha256(selected_codes),
        "discovery_holdout_overlap_count": len(overlap),
        "selection": "SECTOR_STRATIFIED_CURRENT_JPX_EXCLUDING_DISCOVERY_CODES",
        "survivorship_bias": "CURRENT_LIST_ONLY_SURVIVORSHIP_AND_DELISTING_BIAS",
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "automatic_hypothesis_activation": False,
        "production_state_mutations": [],
        "selected_codes": selected_codes,
    }
    return frame, manifest


def write_holdout_universe(
    frame: pd.DataFrame,
    manifest: dict[str, Any],
    output_cache: str,
    output_manifest: str,
) -> None:
    cache_target = Path(output_cache)
    manifest_target = Path(output_manifest)
    cache_target.parent.mkdir(parents=True, exist_ok=True)
    manifest_target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(cache_target, index=False)
    written_codes = normalize_codes(pd.read_csv(cache_target, dtype=str)["コード"].tolist())
    if codes_sha256(written_codes) != manifest["selected_holdout_codes_sha256"]:
        raise RuntimeError("written holdout cache hash mismatch")
    manifest["output_cache_sha256"] = sha256_file(cache_target)
    manifest_target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_signals(signals: pd.DataFrame, history: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    enriched, coverage = filter_lab.attach_filter_context(signals, history)
    enriched["sector33"] = enriched.get("sector33", pd.Series(index=enriched.index, dtype=str)).fillna("").map(main.normalize_sector33)
    enriched["relative_strength_lifecycle"] = enriched.get(
        "relative_strength_lifecycle", pd.Series(index=enriched.index, dtype=str)
    ).fillna("").astype(str)
    return enriched, coverage


def hypothesis_mask(signals: pd.DataFrame, hypothesis: dict[str, Any]) -> pd.Series:
    sectors = {main.normalize_sector33(value) for value in hypothesis.get("exclude_sectors") or []}
    lifecycles = {str(value).strip() for value in hypothesis.get("exclude_lifecycles") or []}
    mask = pd.Series(True, index=signals.index, dtype=bool)
    if sectors:
        mask &= ~signals["sector33"].isin(sectors)
    if lifecycles:
        mask &= ~signals["relative_strength_lifecycle"].isin(lifecycles)
    return mask


def simulate_variant(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    name: str,
    eligible: pd.Series,
) -> dict[str, Any]:
    prepared = signals.copy()
    prepared["portfolio_eligible"] = eligible.fillna(False).astype(bool)
    # Discovery hypotheses are entry exclusions only. They do not force a sale
    # after entry and therefore cannot benefit from an unregistered exit rule.
    prepared["portfolio_hold_eligible"] = True
    scenario = portfolio.PortfolioScenario(name, None, 0.0, 0.01)
    return portfolio.simulate_scenario(
        prepared,
        prices,
        scenario,
        exit_policy=BASELINE_EXIT_POLICY,
    )


def aligned_daily_difference(baseline: pd.DataFrame, variant: pd.DataFrame) -> pd.Series:
    left = baseline[["date", "daily_return"]].copy().rename(columns={"daily_return": "baseline_return"})
    right = variant[["date", "daily_return"]].copy().rename(columns={"daily_return": "variant_return"})
    merged = left.merge(right, on="date", how="inner")
    return (
        pd.to_numeric(merged["variant_return"], errors="coerce").fillna(0.0)
        - pd.to_numeric(merged["baseline_return"], errors="coerce").fillna(0.0)
    )


def moving_block_bootstrap(
    differences: pd.Series,
    block_length: int,
    iterations: int,
    seed: int,
) -> dict[str, float | None]:
    values = pd.to_numeric(differences, errors="coerce").dropna().to_numpy(dtype=float)
    if len(values) < max(block_length * 2, 10):
        return {"mean_daily_difference": None, "ci_low": None, "ci_high": None, "one_sided_p_value": None}
    observed = float(values.mean())
    rng = np.random.default_rng(seed)
    block_length = max(1, min(int(block_length), len(values)))
    start_count = max(len(values) - block_length + 1, 1)
    boot_means = np.empty(iterations, dtype=float)
    blocks_needed = int(np.ceil(len(values) / block_length))
    for index in range(iterations):
        starts = rng.integers(0, start_count, size=blocks_needed)
        sample = np.concatenate([values[start : start + block_length] for start in starts])[: len(values)]
        boot_means[index] = float(sample.mean())
    centered = boot_means - observed
    one_sided_p = float((1 + np.sum(centered >= observed)) / (iterations + 1))
    return {
        "mean_daily_difference": observed,
        "ci_low": float(np.quantile(boot_means, 0.025)),
        "ci_high": float(np.quantile(boot_means, 0.975)),
        "one_sided_p_value": one_sided_p,
    }


def bh_q_values(p_values: list[float | None]) -> list[float | None]:
    valid = [(index, float(value)) for index, value in enumerate(p_values) if value is not None and np.isfinite(value)]
    result: list[float | None] = [None] * len(p_values)
    if not valid:
        return result
    ordered = sorted(valid, key=lambda item: item[1])
    total = len(ordered)
    adjusted = [0.0] * total
    running = 1.0
    for reverse_index in range(total - 1, -1, -1):
        _, p_value = ordered[reverse_index]
        rank = reverse_index + 1
        running = min(running, p_value * total / rank)
        adjusted[reverse_index] = min(running, 1.0)
    for (original_index, _), q_value in zip(ordered, adjusted):
        result[original_index] = float(q_value)
    return result


def period_metrics(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    name: str,
    eligible: pd.Series,
    periods: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    for period_name, (start, end) in periods.items():
        period_signals, period_prices = exit_lab.slice_period(
            signals.assign(portfolio_eligible=eligible.values),
            prices,
            start,
            end,
            BASELINE_EXIT_POLICY.maximum_holding_sessions,
        )
        period_eligible = period_signals.get("portfolio_eligible", pd.Series(False, index=period_signals.index)).fillna(False).astype(bool)
        result = simulate_variant(period_signals, period_prices, f"{name}_{period_name}", period_eligible)
        metrics = dict(result["metrics"])
        metrics.update({
            "variant": name,
            "period": period_name,
            "period_start": start.date().isoformat(),
            "period_end_signal": end.date().isoformat(),
            "eligible_signal_count": int(period_eligible.sum()),
            "signal_count": len(period_signals),
        })
        rows.append(metrics)
        results[period_name] = result
    return pd.DataFrame(rows), results


def evaluate_holdout(
    registry: dict[str, Any],
    signals: pd.DataFrame,
    history: pd.DataFrame,
    prices: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    enriched, coverage = prepare_signals(signals, history)
    aligned_prices = attribution.align_prices_to_signal_window(enriched, prices)
    periods = exit_lab.period_ranges(enriched)
    baseline_mask = pd.Series(True, index=enriched.index, dtype=bool)
    baseline_periods, baseline_results = period_metrics(
        enriched, aligned_prices, "BASELINE", baseline_mask, periods
    )
    baseline_full = baseline_results["full"]
    baseline_metrics = baseline_periods[baseline_periods["period"] == "full"].iloc[0]

    design = registry.get("holdout_design") or {}
    minimum_baseline_trades = int(design.get("minimum_baseline_trades") or 30)
    minimum_variant_trades = int(design.get("minimum_variant_trades") or 20)
    minimum_subperiod_trades = int(design.get("minimum_subperiod_trades") or 6)
    block_length = int(design.get("block_bootstrap_length") or 5)
    iterations = int(design.get("bootstrap_iterations") or 2000)
    maximum_q = float(design.get("maximum_fdr_q_value") or 0.05)

    hypothesis_rows: list[dict[str, Any]] = []
    all_period_rows: list[pd.DataFrame] = [baseline_periods]
    trade_frames: list[pd.DataFrame] = []
    equity_frames: list[pd.DataFrame] = []
    p_values: list[float | None] = []

    for index, hypothesis in enumerate(registry.get("hypotheses") or []):
        hypothesis_id = str(hypothesis.get("id"))
        eligible = hypothesis_mask(enriched, hypothesis)
        periods_frame, results_by_period = period_metrics(
            enriched, aligned_prices, hypothesis_id, eligible, periods
        )
        all_period_rows.append(periods_frame)
        full_metrics = periods_frame[periods_frame["period"] == "full"].iloc[0]
        early_metrics = periods_frame[periods_frame["period"] == "early"].iloc[0]
        late_metrics = periods_frame[periods_frame["period"] == "late"].iloc[0]
        daily_test = moving_block_bootstrap(
            aligned_daily_difference(baseline_full["equity"], results_by_period["full"]["equity"]),
            block_length,
            iterations,
            seed=20260711 + index,
        )
        p_values.append(daily_test["one_sided_p_value"])
        baseline_excess = float(baseline_metrics["excess_total_return"])
        baseline_dd = float(baseline_metrics["max_drawdown"])
        early_baseline = baseline_periods[baseline_periods["period"] == "early"].iloc[0]
        late_baseline = baseline_periods[baseline_periods["period"] == "late"].iloc[0]
        row = {
            "hypothesis_id": hypothesis_id,
            "description": str(hypothesis.get("description") or ""),
            "exclude_sectors": ",".join(str(value) for value in hypothesis.get("exclude_sectors") or []),
            "exclude_lifecycles": ",".join(str(value) for value in hypothesis.get("exclude_lifecycles") or []),
            "eligible_signal_count": int(eligible.sum()),
            "total_signal_count": len(enriched),
            "eligible_signal_ratio": float(eligible.mean()) if len(enriched) else 0.0,
            "baseline_closed_trades": int(baseline_metrics["closed_trades"]),
            "variant_closed_trades": int(full_metrics["closed_trades"]),
            "early_variant_closed_trades": int(early_metrics["closed_trades"]),
            "late_variant_closed_trades": int(late_metrics["closed_trades"]),
            "baseline_total_return": float(baseline_metrics["total_return"]),
            "variant_total_return": float(full_metrics["total_return"]),
            "baseline_excess_total_return": baseline_excess,
            "variant_excess_total_return": float(full_metrics["excess_total_return"]),
            "delta_excess_total_return": float(full_metrics["excess_total_return"]) - baseline_excess,
            "baseline_max_drawdown": baseline_dd,
            "variant_max_drawdown": float(full_metrics["max_drawdown"]),
            "delta_max_drawdown": float(full_metrics["max_drawdown"]) - baseline_dd,
            "early_delta_excess": float(early_metrics["excess_total_return"]) - float(early_baseline["excess_total_return"]),
            "late_delta_excess": float(late_metrics["excess_total_return"]) - float(late_baseline["excess_total_return"]),
            "variant_win_rate": full_metrics.get("win_rate"),
            "variant_profit_factor": full_metrics.get("profit_factor"),
            **daily_test,
        }
        hypothesis_rows.append(row)
        for period_name, result in results_by_period.items():
            for key, frames in (("trades", trade_frames), ("equity", equity_frames)):
                frame = result[key].copy()
                if not frame.empty:
                    frame["hypothesis_id"] = hypothesis_id
                    frame["period"] = period_name
                    frames.append(frame)

    q_values = bh_q_values(p_values)
    for row, q_value in zip(hypothesis_rows, q_values):
        row["fdr_q_value"] = q_value
        sufficient = (
            row["baseline_closed_trades"] >= minimum_baseline_trades
            and row["variant_closed_trades"] >= minimum_variant_trades
            and row["early_variant_closed_trades"] >= minimum_subperiod_trades
            and row["late_variant_closed_trades"] >= minimum_subperiod_trades
        )
        directional = (
            row["delta_excess_total_return"] > 0
            and row["delta_max_drawdown"] >= 0
            and row["early_delta_excess"] >= 0
            and row["late_delta_excess"] >= 0
        )
        statistically_supported = (
            q_value is not None
            and q_value <= maximum_q
            and row["ci_low"] is not None
            and row["ci_low"] > 0
        )
        row["sample_adequate"] = sufficient
        row["directionally_supported"] = directional
        row["statistically_supported"] = statistically_supported
        if not sufficient:
            row["validation_status"] = "INSUFFICIENT"
        elif directional and statistically_supported:
            row["validation_status"] = "VALIDATED"
        elif directional:
            row["validation_status"] = "DIRECTIONALLY_SUPPORTED"
        else:
            row["validation_status"] = "REJECTED"
        row["automatic_activation_allowed"] = False

    hypothesis_summary = pd.DataFrame(hypothesis_rows)
    status_order = {"VALIDATED": 0, "DIRECTIONALLY_SUPPORTED": 1, "REJECTED": 2, "INSUFFICIENT": 3}
    hypothesis_summary["_status_order"] = hypothesis_summary["validation_status"].map(status_order).fillna(9)
    hypothesis_summary = hypothesis_summary.sort_values(
        ["_status_order", "delta_excess_total_return", "delta_max_drawdown"],
        ascending=[True, False, False],
    ).drop(columns="_status_order").reset_index(drop=True)

    universe_coverage = pd.DataFrame([{
        "signal_count": len(enriched),
        "unique_signal_codes": int(enriched["code"].nunique()),
        "first_signal_date": pd.to_datetime(enriched["signal_date"]).min().date().isoformat(),
        "last_signal_date": pd.to_datetime(enriched["signal_date"]).max().date().isoformat(),
        "relative_strength_score_coverage": float(enriched["relative_strength_score"].notna().mean()),
        "lifecycle_coverage": float(enriched["relative_strength_lifecycle"].fillna("").ne("").mean()),
    }])
    return {
        "hypothesis_summary": hypothesis_summary,
        "period_metrics": pd.concat(all_period_rows, ignore_index=True),
        "trades": pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame(),
        "equity": pd.concat(equity_frames, ignore_index=True) if equity_frames else pd.DataFrame(),
        "context_coverage": coverage,
        "universe_coverage": universe_coverage,
        "baseline_metrics": pd.DataFrame([baseline_metrics.to_dict()]),
    }


def write_evaluation_outputs(
    registry: dict[str, Any],
    results: dict[str, pd.DataFrame],
    provenance_path: str,
    holdout_universe_manifest_path: str,
    output_dir: str,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    universe_manifest = json.loads(Path(holdout_universe_manifest_path).read_text(encoding="utf-8"))
    paths = {
        "summary": output / "portfolio_holdout_hypothesis_summary.csv",
        "period_metrics": output / "portfolio_holdout_period_metrics.csv",
        "trades": output / "portfolio_holdout_trades.csv",
        "equity": output / "portfolio_holdout_equity.csv",
        "context": output / "portfolio_holdout_context_coverage.csv",
        "universe": output / "portfolio_holdout_universe_coverage.csv",
        "baseline": output / "portfolio_holdout_baseline_metrics.csv",
        "excel": output / "portfolio_holdout_validation.xlsx",
        "manifest": output / "portfolio_holdout_validation_manifest.json",
    }
    mapping = {
        "summary": "hypothesis_summary", "period_metrics": "period_metrics",
        "trades": "trades", "equity": "equity", "context": "context_coverage",
        "universe": "universe_coverage", "baseline": "baseline_metrics",
    }
    for path_key, result_key in mapping.items():
        results[result_key].to_csv(paths[path_key], index=False)
    manifest = {
        "holdout_version": HOLDOUT_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "registry_sha256": sha256_file(DEFAULT_REGISTRY),
        "production_app_version": main.APP_VERSION,
        "portfolio_engine_version": portfolio.PORTFOLIO_RESEARCH_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "discovery_codes_sha256": universe_manifest.get("discovery_codes_sha256"),
        "holdout_codes_sha256": universe_manifest.get("selected_holdout_codes_sha256"),
        "discovery_holdout_overlap_count": universe_manifest.get("discovery_holdout_overlap_count"),
        "hypothesis_count": len(registry.get("hypotheses") or []),
        "validated_hypothesis_count": int((results["hypothesis_summary"]["validation_status"] == "VALIDATED").sum()),
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "automatic_hypothesis_activation": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "current_universe_bias": True,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Manifest", index=False)
        results["hypothesis_summary"].to_excel(writer, sheet_name="Hypothesis Summary", index=False)
        results["period_metrics"].to_excel(writer, sheet_name="Period Metrics", index=False)
        results["baseline_metrics"].to_excel(writer, sheet_name="Baseline", index=False)
        results["trades"].to_excel(writer, sheet_name="Trades", index=False)
        results["equity"].to_excel(writer, sheet_name="Equity", index=False)
        results["context_coverage"].to_excel(writer, sheet_name="Context Coverage", index=False)
        results["universe_coverage"].to_excel(writer, sheet_name="Universe Coverage", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"manifest": manifest, "paths": {key: str(value) for key, value in paths.items()}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate pre-registered portfolio hypotheses on a disjoint-symbol holdout")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-universe")
    prepare.add_argument("--registry", default=DEFAULT_REGISTRY)
    prepare.add_argument("--cache", default=DEFAULT_CACHE)
    prepare.add_argument("--config", default=DEFAULT_CONFIG)
    prepare.add_argument("--max-symbols", type=int, default=72)
    prepare.add_argument("--output-cache", required=True)
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--strict", action="store_true")

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--registry", default=DEFAULT_REGISTRY)
    evaluate.add_argument("--signals", required=True)
    evaluate.add_argument("--history", required=True)
    evaluate.add_argument("--prices", required=True)
    evaluate.add_argument("--provenance", required=True)
    evaluate.add_argument("--holdout-universe-manifest", required=True)
    evaluate.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    evaluate.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before = replay.live_state_hashes()
    registry = load_registry(args.registry)
    if args.command == "prepare-universe":
        frame, manifest = prepare_holdout_universe(
            registry, args.cache, args.config, args.max_symbols
        )
        write_holdout_universe(frame, manifest, args.output_cache, args.manifest)
        if args.strict:
            if frame.empty or len(frame) != args.max_symbols:
                raise RuntimeError(f"holdout universe size mismatch: {len(frame)}")
            if manifest["discovery_holdout_overlap_count"] != 0:
                raise RuntimeError("discovery and holdout universes overlap")
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        signals = portfolio.load_signals(args.signals)
        history = filter_lab.load_history(args.history)
        prices = portfolio.load_prices(args.prices)
        results = evaluate_holdout(registry, signals, history, prices)
        output = write_evaluation_outputs(
            registry, results, args.provenance,
            args.holdout_universe_manifest, args.output_dir,
        )
        after = replay.live_state_hashes()
        mutations = [path for path in before if before[path] != after.get(path, "")]
        output["manifest"]["production_state_mutations"] = mutations
        Path(output["paths"]["manifest"]).write_text(
            json.dumps(output["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if args.strict:
            if mutations:
                raise RuntimeError(f"production state mutated: {mutations}")
            if len(results["hypothesis_summary"]) != len(registry.get("hypotheses") or []):
                raise RuntimeError("one or more registered hypotheses are missing")
            if float(results["context_coverage"].iloc[0]["relative_strength_score_coverage"]) < 0.99:
                raise RuntimeError("relative strength context coverage below 99%")
            if results["equity"].empty:
                raise RuntimeError("holdout evaluation produced no equity curves")
        print(results["hypothesis_summary"].to_string(index=False))
        print(json.dumps(output["manifest"], ensure_ascii=False, indent=2))
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    if mutations:
        raise RuntimeError(f"production state mutated: {mutations}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
