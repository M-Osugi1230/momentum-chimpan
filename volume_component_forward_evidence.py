"""Prospective evidence tracker for the governed volume-ratio score component.

Only fingerprint-stamped live ranking dates on or after the pre-registered cutoff
are eligible. The governed baseline is compared with a distribution-preserving
``drop_volume_ratio`` counterfactual. Both variants use next-session adjusted
opens, explicit execution friction, and identical forward horizons. Outputs are
research-only and can never mutate production state or change strategy weights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

import execution_realism
import live_execution_panel
import main
import replay
import score_component_ablation as ablation

FORWARD_VERSION = "2026-07-12-volume-component-forward-v1"
BASELINE_VARIANT = "baseline"
TEST_VARIANT = "drop_volume_ratio"
DEFAULT_REGISTRY = "research/volume_component_forward_evidence.yaml"
DEFAULT_HISTORY = "output/volume_component_forward/live_strategy_history.csv"
DEFAULT_PROVENANCE = "output/volume_component_forward/evidence_provenance.json"
DEFAULT_OUTPUT_DIR = "output/volume_component_forward/report"
DEFAULT_HORIZONS = (5, 10, 20)
TARGET_COLUMNS = ("forward_return", "excess_vs_universe", "excess_vs_sector")


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_number(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def safe_mean(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return None if values.empty else float(values.mean())


def safe_rate(series: pd.Series) -> float | None:
    values = series.dropna()
    return None if values.empty else float(values.astype(bool).mean())


def load_registry(path: str = DEFAULT_REGISTRY) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    required = {"study", "comparison", "evidence_gate", "governance"}
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError("volume component forward registry is invalid")
    return payload


def eligible_dates(history: pd.DataFrame, registration_date: str) -> list[str]:
    if history is None or history.empty or "date" not in history.columns:
        return []
    return sorted(
        date
        for date in history["date"].dropna().astype(str).unique().tolist()
        if date >= registration_date
    )


def _empty_replay_manifest() -> dict[str, Any]:
    return {
        "replay_date_count": 0,
        "signal_count": 0,
        "lookahead_violations": 0,
        "research_only": True,
    }


def build_variant_replays(
    history: pd.DataFrame,
    registry: dict[str, Any],
    top_limit: int = 100,
) -> dict[str, Any]:
    registration_date = str(registry["study"]["eligible_signal_date_from"])
    baseline_history = ablation.build_variant_history(
        history, BASELINE_VARIANT, top_limit=top_limit
    )
    tested_history = ablation.build_variant_history(
        history, TEST_VARIANT, top_limit=top_limit
    )
    distribution = ablation.validate_distribution_preservation(
        baseline_history, tested_history
    )
    distribution = distribution[
        distribution["date"].astype(str).ge(registration_date)
    ].reset_index(drop=True)

    variants: dict[str, dict[str, Any]] = {}
    for variant, variant_history in (
        (BASELINE_VARIANT, baseline_history),
        (TEST_VARIANT, tested_history),
    ):
        dates = eligible_dates(variant_history, registration_date)
        if not dates:
            variants[variant] = {
                "history": variant_history,
                "signals": pd.DataFrame(),
                "audit": pd.DataFrame(),
                "manifest": _empty_replay_manifest(),
            }
            continue
        result = replay.run_walk_forward_replay(
            variant_history,
            top_limit=top_limit,
            min_date=registration_date,
        )
        signals = result.signals.copy()
        if not signals.empty:
            signals["variant"] = variant
            signals["signal_date"] = pd.to_datetime(
                signals["signal_date"], errors="coerce"
            )
            signals = signals.dropna(subset=["signal_date", "code"])
        audit = result.audit.copy()
        if not audit.empty:
            audit["variant"] = variant
        variants[variant] = {
            "history": variant_history,
            "signals": signals,
            "audit": audit,
            "manifest": result.manifest,
        }

    signal_frames = [
        variants[name]["signals"]
        for name in (BASELINE_VARIANT, TEST_VARIANT)
        if not variants[name]["signals"].empty
    ]
    audit_frames = [
        variants[name]["audit"]
        for name in (BASELINE_VARIANT, TEST_VARIANT)
        if not variants[name]["audit"].empty
    ]
    return {
        "baseline_history": baseline_history,
        "tested_history": tested_history,
        "signals": (
            pd.concat(signal_frames, ignore_index=True)
            if signal_frames
            else pd.DataFrame()
        ),
        "audit": (
            pd.concat(audit_frames, ignore_index=True)
            if audit_frames
            else pd.DataFrame()
        ),
        "distribution_audit": distribution,
        "variant_manifests": {
            name: variants[name]["manifest"] for name in variants
        },
    }


def simulate_variant_outcomes(
    signals: pd.DataFrame,
    panel: pd.DataFrame,
    ranking: pd.DataFrame,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    outcome_frames: list[pd.DataFrame] = []
    coverage_frames: list[pd.DataFrame] = []
    if signals is None or signals.empty:
        return pd.DataFrame(), pd.DataFrame()

    ranking_frame = ranking.copy()
    ranking_frame["date"] = pd.to_datetime(ranking_frame["date"], errors="coerce")
    ranking_frame["rank"] = pd.to_numeric(ranking_frame["rank"], errors="coerce")
    ranking_frame["code"] = ranking_frame["code"].map(main.normalize_code)

    for variant in (BASELINE_VARIANT, TEST_VARIANT):
        variant_signals = signals[
            signals["variant"].astype(str).eq(variant)
        ].copy()
        if variant_signals.empty:
            continue
        variant_signals["signal_date"] = pd.to_datetime(
            variant_signals["signal_date"], errors="coerce"
        )
        variant_signals["entry_close"] = pd.to_numeric(
            variant_signals["entry_close"], errors="coerce"
        )
        variant_signals = variant_signals.dropna(
            subset=["signal_date", "entry_close", "code"]
        )
        outcomes, coverage = execution_realism.simulate_execution(
            variant_signals,
            panel,
            ranking_frame,
            horizons=horizons,
        )
        if not outcomes.empty:
            outcomes["variant"] = variant
            outcome_frames.append(outcomes)
        if not coverage.empty:
            coverage["variant"] = variant
            coverage_frames.append(coverage)

    return (
        pd.concat(outcome_frames, ignore_index=True)
        if outcome_frames
        else pd.DataFrame(),
        pd.concat(coverage_frames, ignore_index=True)
        if coverage_frames
        else pd.DataFrame(),
    )


def build_variant_metrics(outcomes: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "variant",
        "horizon_days",
        "outcome_count",
        "unique_codes",
        "unique_signal_dates",
        "average_forward_return",
        "median_forward_return",
        "win_rate",
        "average_excess_vs_universe",
        "beat_universe_rate",
        "average_excess_vs_sector",
        "beat_sector_rate",
    ]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for (variant, horizon), group in outcomes.groupby(
        ["variant", "horizon_days"]
    ):
        forward = pd.to_numeric(group["forward_return"], errors="coerce")
        rows.append({
            "variant": str(variant),
            "horizon_days": int(horizon),
            "outcome_count": int(len(group)),
            "unique_codes": int(group["code"].astype(str).nunique()),
            "unique_signal_dates": int(
                group["signal_date"].astype(str).nunique()
            ),
            "average_forward_return": safe_mean(forward),
            "median_forward_return": (
                None
                if forward.dropna().empty
                else float(forward.dropna().median())
            ),
            "win_rate": (
                float((forward.dropna() > 0).mean())
                if forward.notna().any()
                else None
            ),
            "average_excess_vs_universe": safe_mean(
                group["excess_vs_universe"]
            ),
            "beat_universe_rate": safe_rate(group["beat_universe"]),
            "average_excess_vs_sector": safe_mean(group["excess_vs_sector"]),
            "beat_sector_rate": safe_rate(group["beat_sector"]),
        })
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["horizon_days", "variant"]
    ).reset_index(drop=True)


def build_daily_pairs(outcomes: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "signal_date",
        "horizon_days",
        "target",
        "baseline",
        "tested",
        "delta",
    ]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    rows: list[pd.DataFrame] = []
    for target in TARGET_COLUMNS:
        grouped = outcomes.groupby(
            ["signal_date", "horizon_days", "variant"], as_index=False
        )[target].mean()
        pivot = grouped.pivot_table(
            index=["signal_date", "horizon_days"],
            columns="variant",
            values=target,
            aggfunc="last",
        ).reset_index()
        if (
            BASELINE_VARIANT not in pivot.columns
            or TEST_VARIANT not in pivot.columns
        ):
            continue
        pivot = pivot.dropna(
            subset=[BASELINE_VARIANT, TEST_VARIANT]
        ).copy()
        pivot["target"] = target
        pivot["baseline"] = pd.to_numeric(
            pivot[BASELINE_VARIANT], errors="coerce"
        )
        pivot["tested"] = pd.to_numeric(
            pivot[TEST_VARIANT], errors="coerce"
        )
        pivot["delta"] = pivot["tested"] - pivot["baseline"]
        rows.append(pivot[columns])
    return (
        pd.concat(rows, ignore_index=True)
        .sort_values(["horizon_days", "target", "signal_date"])
        .reset_index(drop=True)
        if rows
        else pd.DataFrame(columns=columns)
    )


def _subperiod_deltas(
    group: pd.DataFrame,
) -> tuple[float | None, float | None]:
    dates = sorted(group["signal_date"].astype(str).unique().tolist())
    if len(dates) < 2:
        return None, None
    midpoint = max(len(dates) // 2, 1)
    early_dates = set(dates[:midpoint])
    late_dates = set(dates[midpoint:])
    early = group[
        group["signal_date"].astype(str).isin(early_dates)
    ]["delta"]
    late = group[
        group["signal_date"].astype(str).isin(late_dates)
    ]["delta"]
    return safe_mean(early), safe_mean(late)


def build_statistical_summary(daily_pairs: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "horizon_days",
        "target",
        "paired_date_count",
        "mean_daily_difference",
        "median_daily_difference",
        "early_mean_difference",
        "late_mean_difference",
        "ci_low",
        "ci_high",
        "two_sided_p_value",
        "improvement_p_value",
        "harm_p_value",
    ]
    if daily_pairs is None or daily_pairs.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for (horizon, target), group in daily_pairs.groupby(
        ["horizon_days", "target"]
    ):
        base = group[["signal_date", "baseline"]].rename(
            columns={"signal_date": "date", "baseline": "daily_return"}
        )
        tested = group[["signal_date", "tested"]].rename(
            columns={"signal_date": "date", "tested": "daily_return"}
        )
        test = ablation.paired_sign_flip(
            base,
            tested,
            block_length=5,
            iterations=2000,
            seed=20260713 + int(horizon),
        )
        early, late = _subperiod_deltas(group)
        numeric_delta = pd.to_numeric(group["delta"], errors="coerce").dropna()
        rows.append({
            "horizon_days": int(horizon),
            "target": str(target),
            "paired_date_count": int(
                group["signal_date"].astype(str).nunique()
            ),
            "mean_daily_difference": safe_mean(group["delta"]),
            "median_daily_difference": (
                None if numeric_delta.empty else float(numeric_delta.median())
            ),
            "early_mean_difference": early,
            "late_mean_difference": late,
            **test,
        })
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["horizon_days", "target"]
    ).reset_index(drop=True)


def _metric_row(
    metrics: pd.DataFrame,
    variant: str,
    horizon: int,
) -> pd.Series | None:
    subset = metrics[
        metrics["variant"].astype(str).eq(variant)
        & pd.to_numeric(metrics["horizon_days"], errors="coerce").eq(horizon)
    ]
    return None if subset.empty else subset.iloc[0]


def _stat_row(
    stats: pd.DataFrame,
    horizon: int,
    target: str,
) -> pd.Series | None:
    subset = stats[
        pd.to_numeric(stats["horizon_days"], errors="coerce").eq(horizon)
        & stats["target"].astype(str).eq(target)
    ]
    return None if subset.empty else subset.iloc[0]


def evaluate_evidence_status(
    metrics: pd.DataFrame,
    stats: pd.DataFrame,
    registry: dict[str, Any],
) -> dict[str, Any]:
    gate = registry["evidence_gate"]
    target = str(gate.get("primary_target", "excess_vs_universe"))
    horizons = [
        int(value) for value in gate.get("required_horizons", [10, 20])
    ]
    minimum_outcomes = int(
        gate.get("minimum_outcomes_per_variant_per_horizon", 100)
    )
    minimum_dates = int(gate.get("minimum_paired_dates_per_horizon", 20))
    maximum_p = float(gate.get("maximum_two_sided_p_value", 0.05))

    adequacy: list[bool] = []
    direction: list[bool] = []
    robustness: list[bool] = []
    for horizon in horizons:
        baseline = _metric_row(metrics, BASELINE_VARIANT, horizon)
        tested = _metric_row(metrics, TEST_VARIANT, horizon)
        stat = _stat_row(stats, horizon, target)
        adequate = (
            baseline is not None
            and tested is not None
            and stat is not None
            and int(baseline["outcome_count"]) >= minimum_outcomes
            and int(tested["outcome_count"]) >= minimum_outcomes
            and int(stat["paired_date_count"]) >= minimum_dates
        )
        adequacy.append(bool(adequate))
        mean_difference = (
            None if stat is None else safe_number(stat["mean_daily_difference"])
        )
        early = (
            None if stat is None else safe_number(stat["early_mean_difference"])
        )
        late = (
            None if stat is None else safe_number(stat["late_mean_difference"])
        )
        p_value = (
            None if stat is None else safe_number(stat["two_sided_p_value"])
        )
        ci_high = None if stat is None else safe_number(stat["ci_high"])
        direction.append(bool(
            adequate
            and mean_difference is not None
            and mean_difference < 0
        ))
        robustness.append(bool(
            adequate
            and mean_difference is not None
            and mean_difference < 0
            and early is not None
            and early < 0
            and late is not None
            and late < 0
            and p_value is not None
            and p_value <= maximum_p
            and ci_high is not None
            and ci_high < 0
        ))

    sample_adequate = bool(adequacy and all(adequacy))
    if not sample_adequate:
        status = "ACCUMULATING"
    elif robustness and all(robustness):
        status = "ROBUSTLY_SUPPORTED"
    elif direction and all(direction):
        status = "DIRECTIONALLY_SUPPORTED"
    else:
        status = "NOT_SUPPORTED"
    return {
        "evidence_status": status,
        "sample_adequate": sample_adequate,
        "primary_target": target,
        "required_horizons": "|".join(str(value) for value in horizons),
        "minimum_outcomes_per_variant_per_horizon": minimum_outcomes,
        "minimum_paired_dates_per_horizon": minimum_dates,
        "automatic_weight_change_allowed": False,
        "promotion_evidence_allowed": False,
    }


def analyze_forward_outcomes(
    outcomes: pd.DataFrame,
    registry: dict[str, Any],
) -> dict[str, Any]:
    metrics = build_variant_metrics(outcomes)
    daily_pairs = build_daily_pairs(outcomes)
    stats = build_statistical_summary(daily_pairs)
    status = pd.DataFrame([
        evaluate_evidence_status(metrics, stats, registry)
    ])
    return {
        "variant_metrics": metrics,
        "daily_pairs": daily_pairs,
        "statistical_summary": stats,
        "evidence_status": status,
    }


def _manifest_frame(manifest: dict[str, Any]) -> pd.DataFrame:
    row: dict[str, Any] = {}
    for key, value in manifest.items():
        if isinstance(value, (dict, list, tuple, set)):
            row[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            row[key] = value
    return pd.DataFrame([row])


def write_outputs(
    output_dir: str,
    analysis: dict[str, Any],
    signals: pd.DataFrame,
    outcomes: pd.DataFrame,
    coverage: pd.DataFrame,
    distribution_audit: pd.DataFrame,
    replay_audit: pd.DataFrame,
    manifest: dict[str, Any],
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "signals": output / "volume_component_forward_signals.csv",
        "outcomes": output / "volume_component_forward_outcomes.csv",
        "coverage": output / "volume_component_forward_coverage.csv",
        "distribution": output
        / "volume_component_forward_distribution_audit.csv",
        "replay_audit": output / "volume_component_forward_replay_audit.csv",
        "variant_metrics": output
        / "volume_component_forward_variant_metrics.csv",
        "daily_pairs": output / "volume_component_forward_daily_pairs.csv",
        "statistics": output / "volume_component_forward_statistics.csv",
        "status": output / "volume_component_forward_status.csv",
        "excel": output / "volume_component_forward_evidence.xlsx",
        "manifest": output / "volume_component_forward_manifest.json",
    }
    frames = {
        "signals": signals,
        "outcomes": outcomes,
        "coverage": coverage,
        "distribution": distribution_audit,
        "replay_audit": replay_audit,
        "variant_metrics": analysis["variant_metrics"],
        "daily_pairs": analysis["daily_pairs"],
        "statistics": analysis["statistical_summary"],
        "status": analysis["evidence_status"],
    }
    for key, frame in frames.items():
        frame.to_csv(paths[key], index=False)
    paths["manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        _manifest_frame(manifest).to_excel(
            writer, sheet_name="Manifest", index=False
        )
        analysis["evidence_status"].to_excel(
            writer, sheet_name="Status", index=False
        )
        analysis["variant_metrics"].to_excel(
            writer, sheet_name="Variant Metrics", index=False
        )
        analysis["statistical_summary"].to_excel(
            writer, sheet_name="Statistics", index=False
        )
        analysis["daily_pairs"].to_excel(
            writer, sheet_name="Daily Pairs", index=False
        )
        signals.to_excel(writer, sheet_name="Signals", index=False)
        outcomes.to_excel(writer, sheet_name="Outcomes", index=False)
        coverage.to_excel(writer, sheet_name="Coverage", index=False)
        distribution_audit.to_excel(
            writer, sheet_name="Distribution Audit", index=False
        )
        replay_audit.to_excel(
            writer, sheet_name="Replay Audit", index=False
        )
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max(
                        (len(str(cell.value or "")) for cell in column),
                        default=8,
                    )
                    + 2,
                    48,
                )
    return {key: str(value) for key, value in paths.items()}


def run_forward_evidence(
    history_path: str,
    provenance_path: str,
    registry_path: str,
    output_dir: str,
    top_limit: int = 100,
    batch_size: int = 50,
) -> dict[str, Any]:
    before = replay.live_state_hashes()
    registry = load_registry(registry_path)
    history = ablation.load_history(history_path)
    replay_data = build_variant_replays(
        history, registry, top_limit=top_limit
    )
    signals = replay_data["signals"]

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    union_signals_path = output / "volume_component_forward_union_signals.csv"
    signals.to_csv(union_signals_path, index=False)
    panel_path = output / "volume_component_forward_price_panel.csv"
    panel_manifest_path = (
        output / "volume_component_forward_price_panel_manifest.json"
    )

    if signals.empty:
        panel = pd.DataFrame(columns=[
            "date",
            "code",
            "name",
            "sector33",
            "adjusted_open",
            "adjusted_high",
            "adjusted_low",
            "adjusted_close",
            "volume",
            "raw_close",
        ])
        panel.to_csv(panel_path, index=False)
        panel_manifest = {
            "panel_row_count": 0,
            "requested_symbol_count": 0,
            "downloaded_symbol_count": 0,
            "production_state_mutations": [],
        }
        panel_manifest_path.write_text(
            json.dumps(panel_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        panel_result = live_execution_panel.build_live_panel(
            str(union_signals_path),
            provenance_path,
            str(panel_path),
            str(panel_manifest_path),
            batch_size=batch_size,
            future_buffer_days=30,
        )
        panel = panel_result["panel"]
        panel_manifest = panel_result["manifest"]

    horizons = tuple(
        int(value)
        for value in registry["comparison"].get(
            "horizons", DEFAULT_HORIZONS
        )
    )
    outcomes, coverage = simulate_variant_outcomes(
        signals,
        panel,
        replay_data["baseline_history"],
        horizons=horizons,
    )
    analysis = analyze_forward_outcomes(outcomes, registry)
    source_provenance = json.loads(
        Path(provenance_path).read_text(encoding="utf-8")
    )
    status = analysis["evidence_status"].iloc[0].to_dict()
    after = replay.live_state_hashes()
    mutations = [
        path for path in before if before[path] != after.get(path, "")
    ]
    distribution_preserved = bool(
        replay_data["distribution_audit"].empty
        or replay_data["distribution_audit"]["score_multiset_equal"].all()
    )
    lookahead_violations = int(sum(
        int(payload.get("lookahead_violations", 0))
        for payload in replay_data["variant_manifests"].values()
    ))
    registration_date = str(
        registry["study"]["eligible_signal_date_from"]
    )
    signal_dates = (
        sorted(
            signals["signal_date"]
            .dt.date.astype(str)
            .unique()
            .tolist()
        )
        if not signals.empty
        else []
    )
    manifest = {
        "forward_version": FORWARD_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "study_id": registry["study"]["id"],
        "registered_at": registry["study"]["registered_at"],
        "eligible_signal_date_from": registration_date,
        "source_evidence_origin": source_provenance.get(
            "evidence_origin", ""
        ),
        "source_strategy_fingerprint": source_provenance.get(
            "strategy_fingerprint", ""
        ),
        "source_provenance_sha256": sha256_file(provenance_path),
        "source_history_sha256": sha256_file(history_path),
        "source_promotion_evidence_allowed": source_provenance.get(
            "promotion_evidence_allowed"
        )
        is True,
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "manual_review_required": True,
        "research_only": True,
        "comparison": f"{BASELINE_VARIANT}_vs_{TEST_VARIANT}",
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "horizons": "|".join(str(value) for value in horizons),
        "distribution_preserved": distribution_preserved,
        "lookahead_violations": lookahead_violations,
        "eligible_history_date_count": len(
            eligible_dates(history, registration_date)
        ),
        "first_signal_date": signal_dates[0] if signal_dates else "",
        "last_signal_date": signal_dates[-1] if signal_dates else "",
        "signal_row_count": int(len(signals)),
        "outcome_count": int(len(outcomes)),
        "panel_row_count": int(panel_manifest.get("panel_row_count", 0)),
        "evidence_status": status["evidence_status"],
        "sample_adequate": bool(status["sample_adequate"]),
        "production_state_mutations": mutations,
    }
    paths = write_outputs(
        output_dir,
        analysis,
        signals,
        outcomes,
        coverage,
        replay_data["distribution_audit"],
        replay_data["audit"],
        manifest,
    )
    return {
        "manifest": manifest,
        "paths": paths,
        "analysis": analysis,
        "signals": signals,
        "outcomes": outcomes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prospective volume-component evidence tracker"
    )
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-limit", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    result = run_forward_evidence(
        args.history,
        args.provenance,
        args.registry,
        args.output_dir,
        top_limit=args.top_limit,
        batch_size=args.batch_size,
    )
    manifest = result["manifest"]
    if args.strict:
        if manifest["production_state_mutations"]:
            raise RuntimeError(
                f"production state mutated: "
                f"{manifest['production_state_mutations']}"
            )
        if not manifest["distribution_preserved"]:
            raise RuntimeError("daily score distribution changed")
        if int(manifest["lookahead_violations"]):
            raise RuntimeError("lookahead violation detected")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(result["analysis"]["evidence_status"].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
