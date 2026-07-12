"""Build and validate a compact signed status for volume-component forward evidence.

Raw signals, prices, and outcomes remain in GitHub Actions artifacts. Only derived
progress metadata, statistics, provenance hashes, and immutable governance flags
are eligible for repository persistence. This status can inform a human-facing
dashboard but can never change score weights or production strategy.
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

STATUS_VERSION = "2026-07-12-volume-component-forward-status-v1"
STUDY_ID = "volume-component-forward-evidence-v1"
DEFAULT_OUTPUT = "data/volume_component_forward_status.json"
DEFAULT_REGISTRY = "research/volume_component_forward_evidence.yaml"
REQUIRED_HORIZONS = (10, 20)
ALLOWED_EVIDENCE_STATUSES = {
    "ACCUMULATING",
    "DIRECTIONALLY_SUPPORTED",
    "ROBUSTLY_SUPPORTED",
    "NOT_SUPPORTED",
}


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {target}")
    return payload


def load_csv(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    if target.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(target)


def load_registry(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("forward evidence registry must be a mapping")
    return payload


def optional_float(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def optional_int(value: Any) -> int:
    converted = optional_float(value)
    return 0 if converted is None else int(converted)


def optional_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def metric_row(metrics: pd.DataFrame, variant: str, horizon: int) -> dict[str, Any]:
    if metrics.empty:
        return {}
    subset = metrics[
        metrics["variant"].astype(str).eq(variant)
        & pd.to_numeric(metrics["horizon_days"], errors="coerce").eq(horizon)
    ]
    return {} if subset.empty else subset.iloc[0].to_dict()


def statistic_row(
    statistics: pd.DataFrame,
    horizon: int,
    target: str,
) -> dict[str, Any]:
    if statistics.empty:
        return {}
    subset = statistics[
        pd.to_numeric(statistics["horizon_days"], errors="coerce").eq(horizon)
        & statistics["target"].astype(str).eq(target)
    ]
    return {} if subset.empty else subset.iloc[0].to_dict()


def progress_record(
    metrics: pd.DataFrame,
    statistics: pd.DataFrame,
    horizon: int,
    target: str,
    minimum_outcomes: int,
    minimum_paired_dates: int,
) -> dict[str, Any]:
    baseline = metric_row(metrics, "baseline", horizon)
    tested = metric_row(metrics, "drop_volume_ratio", horizon)
    stat = statistic_row(statistics, horizon, target)
    baseline_count = optional_int(baseline.get("outcome_count"))
    tested_count = optional_int(tested.get("outcome_count"))
    paired_dates = optional_int(stat.get("paired_date_count"))
    minimum_variant_count = min(baseline_count, tested_count)
    return {
        "horizon_days": horizon,
        "baseline_outcome_count": baseline_count,
        "tested_outcome_count": tested_count,
        "minimum_variant_outcome_count": minimum_variant_count,
        "required_outcomes_per_variant": minimum_outcomes,
        "paired_date_count": paired_dates,
        "required_paired_dates": minimum_paired_dates,
        "outcome_progress_ratio": (
            min(minimum_variant_count / minimum_outcomes, 1.0)
            if minimum_outcomes > 0
            else 1.0
        ),
        "paired_date_progress_ratio": (
            min(paired_dates / minimum_paired_dates, 1.0)
            if minimum_paired_dates > 0
            else 1.0
        ),
        "sample_adequate": bool(
            baseline_count >= minimum_outcomes
            and tested_count >= minimum_outcomes
            and paired_dates >= minimum_paired_dates
        ),
        "mean_daily_difference": optional_float(stat.get("mean_daily_difference")),
        "early_mean_difference": optional_float(stat.get("early_mean_difference")),
        "late_mean_difference": optional_float(stat.get("late_mean_difference")),
        "ci_low": optional_float(stat.get("ci_low")),
        "ci_high": optional_float(stat.get("ci_high")),
        "two_sided_p_value": optional_float(stat.get("two_sided_p_value")),
        "harm_p_value": optional_float(stat.get("harm_p_value")),
    }


def build_status(
    manifest_path: str,
    evidence_status_path: str,
    variant_metrics_path: str,
    statistics_path: str,
    provenance_path: str,
    registry_path: str = DEFAULT_REGISTRY,
    source_run_id: str = "",
) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    provenance = load_json(provenance_path)
    registry = load_registry(registry_path)
    status_frame = load_csv(evidence_status_path)
    metrics = load_csv(variant_metrics_path)
    statistics = load_csv(statistics_path)

    if status_frame.empty or len(status_frame) != 1:
        raise ValueError("forward evidence status CSV must contain exactly one row")
    status_row = status_frame.iloc[0].to_dict()
    study = registry.get("study") or {}
    comparison = registry.get("comparison") or {}
    gate = registry.get("evidence_gate") or {}
    governance = registry.get("governance") or {}

    if study.get("id") != STUDY_ID:
        raise ValueError(f"unexpected study id: {study.get('id')!r}")
    if manifest.get("study_id") != STUDY_ID:
        raise ValueError("forward manifest study id does not match registry")
    if manifest.get("entry_model") != "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN":
        raise ValueError("forward evidence entry model is not executable")
    if manifest.get("same_day_close_entry_allowed") is not False:
        raise ValueError("same-day close entry must remain prohibited")
    if manifest.get("promotion_evidence_allowed") is not False:
        raise ValueError("promotion evidence must remain disabled")
    if manifest.get("automatic_weight_change") is not False:
        raise ValueError("automatic weight changes must remain disabled")
    if manifest.get("automatic_strategy_change") is not False:
        raise ValueError("automatic strategy changes must remain disabled")
    if manifest.get("production_state_mutations") not in ([], None):
        raise ValueError("forward evidence mutated production state")
    if int(manifest.get("lookahead_violations", 0) or 0):
        raise ValueError("forward evidence contains lookahead violations")
    if manifest.get("distribution_preserved") is not True:
        raise ValueError("daily score distribution was not preserved")
    if governance.get("promotion_evidence_allowed") is not False:
        raise ValueError("registry promotion evidence must remain disabled")
    if governance.get("automatic_weight_change") is not False:
        raise ValueError("registry automatic weight change must remain disabled")
    if governance.get("automatic_strategy_change") is not False:
        raise ValueError("registry automatic strategy change must remain disabled")
    if comparison.get("entry_model") != "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN":
        raise ValueError("registry entry model changed")

    evidence_status = str(status_row.get("evidence_status", "ACCUMULATING"))
    if evidence_status not in ALLOWED_EVIDENCE_STATUSES:
        raise ValueError(f"unknown forward evidence status: {evidence_status}")
    sample_adequate = optional_bool(status_row.get("sample_adequate", False))
    primary_target = str(
        status_row.get("primary_target")
        or gate.get("primary_target")
        or "excess_vs_universe"
    )
    minimum_outcomes = optional_int(
        status_row.get(
            "minimum_outcomes_per_variant_per_horizon",
            gate.get("minimum_outcomes_per_variant_per_horizon", 100),
        )
    )
    minimum_paired_dates = optional_int(
        status_row.get(
            "minimum_paired_dates_per_horizon",
            gate.get("minimum_paired_dates_per_horizon", 20),
        )
    )

    horizons = {
        str(horizon): progress_record(
            metrics,
            statistics,
            horizon,
            primary_target,
            minimum_outcomes,
            minimum_paired_dates,
        )
        for horizon in REQUIRED_HORIZONS
    }
    all_horizons_adequate = all(
        record["sample_adequate"] for record in horizons.values()
    )
    if sample_adequate != all_horizons_adequate:
        raise ValueError("sample adequacy disagrees with per-horizon progress")
    if evidence_status != "ACCUMULATING" and not sample_adequate:
        raise ValueError("non-accumulating status requires adequate samples")

    generated_at = str(
        manifest.get("generated_at_utc")
        or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    substantive = {
        "status_version": STATUS_VERSION,
        "study_id": STUDY_ID,
        "eligible_signal_date_from": study.get("eligible_signal_date_from"),
        "strategy_fingerprint": provenance.get(
            "strategy_fingerprint",
            manifest.get("source_strategy_fingerprint", ""),
        ),
        "evidence_origin": "LIVE_FORWARD_RANKING_HISTORY",
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "evidence_status": evidence_status,
        "sample_adequate": sample_adequate,
        "primary_target": primary_target,
        "horizons": horizons,
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
        "source_run_id": str(source_run_id or ""),
        "source_hashes": {
            "manifest_sha256": sha256_file(manifest_path),
            "evidence_status_sha256": sha256_file(evidence_status_path),
            "variant_metrics_sha256": sha256_file(variant_metrics_path),
            "statistics_sha256": sha256_file(statistics_path),
            "provenance_sha256": sha256_file(provenance_path),
            "registry_sha256": sha256_file(registry_path),
        },
    }
    payload = {
        **substantive,
        "generated_at_utc": generated_at,
        "evidence_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def build_initial_status() -> dict[str, Any]:
    minimum_outcomes = 100
    minimum_paired_dates = 20
    horizons = {
        str(horizon): {
            "horizon_days": horizon,
            "baseline_outcome_count": 0,
            "tested_outcome_count": 0,
            "minimum_variant_outcome_count": 0,
            "required_outcomes_per_variant": minimum_outcomes,
            "paired_date_count": 0,
            "required_paired_dates": minimum_paired_dates,
            "outcome_progress_ratio": 0.0,
            "paired_date_progress_ratio": 0.0,
            "sample_adequate": False,
            "mean_daily_difference": None,
            "early_mean_difference": None,
            "late_mean_difference": None,
            "ci_low": None,
            "ci_high": None,
            "two_sided_p_value": None,
            "harm_p_value": None,
        }
        for horizon in REQUIRED_HORIZONS
    }
    substantive = {
        "status_version": STATUS_VERSION,
        "study_id": STUDY_ID,
        "eligible_signal_date_from": "2026-07-13",
        "strategy_fingerprint": "",
        "evidence_origin": "LIVE_FORWARD_RANKING_HISTORY",
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "evidence_status": "ACCUMULATING",
        "sample_adequate": False,
        "primary_target": "excess_vs_universe",
        "horizons": horizons,
        "promotion_evidence_allowed": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
        "source_run_id": "INITIAL_PRE_REGISTRATION_STATUS",
        "source_hashes": {},
    }
    payload = {
        **substantive,
        "generated_at_utc": "2026-07-12T00:00:00+00:00",
        "evidence_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def validate_status(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("status_version") != STATUS_VERSION:
        errors.append("invalid status_version")
    if payload.get("study_id") != STUDY_ID:
        errors.append("invalid study_id")
    if payload.get("eligible_signal_date_from") != "2026-07-13":
        errors.append("invalid prospective cutoff")
    if payload.get("evidence_origin") != "LIVE_FORWARD_RANKING_HISTORY":
        errors.append("invalid evidence_origin")
    if payload.get("entry_model") != "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN":
        errors.append("invalid entry_model")
    if payload.get("same_day_close_entry_allowed") is not False:
        errors.append("same-day close entry must be false")
    for key in (
        "promotion_evidence_allowed",
        "automatic_weight_change",
        "automatic_strategy_change",
    ):
        if payload.get(key) is not False:
            errors.append(f"{key} must be false")
    if payload.get("manual_review_required") is not True:
        errors.append("manual_review_required must be true")
    if payload.get("research_only") is not True:
        errors.append("research_only must be true")
    if payload.get("production_state_mutations") != []:
        errors.append("production_state_mutations must be empty")

    evidence_status = payload.get("evidence_status")
    if evidence_status not in ALLOWED_EVIDENCE_STATUSES:
        errors.append("invalid evidence_status")
    horizons = payload.get("horizons")
    if not isinstance(horizons, dict) or set(horizons) != {"10", "20"}:
        errors.append("horizons must contain exactly 10 and 20")
        horizons = {}
    adequate_flags: list[bool] = []
    for key in ("10", "20"):
        record = horizons.get(key)
        if not isinstance(record, dict):
            continue
        for count_key in (
            "baseline_outcome_count",
            "tested_outcome_count",
            "minimum_variant_outcome_count",
            "required_outcomes_per_variant",
            "paired_date_count",
            "required_paired_dates",
        ):
            value = record.get(count_key)
            if not isinstance(value, int) or value < 0:
                errors.append(f"horizons.{key}.{count_key} must be nonnegative integer")
        for ratio_key in ("outcome_progress_ratio", "paired_date_progress_ratio"):
            value = record.get(ratio_key)
            if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                errors.append(f"horizons.{key}.{ratio_key} must be within 0..1")
        adequate_flags.append(record.get("sample_adequate") is True)
    expected_adequate = bool(adequate_flags and all(adequate_flags))
    if payload.get("sample_adequate") is not expected_adequate:
        errors.append("top-level sample_adequate disagrees with horizons")
    if evidence_status != "ACCUMULATING" and not expected_adequate:
        errors.append("non-accumulating status requires adequate samples")

    status_copy = dict(payload)
    supplied_status_hash = status_copy.pop("status_sha256", "")
    if supplied_status_hash != canonical_hash(status_copy):
        errors.append("status_sha256 mismatch")
    substantive = dict(status_copy)
    substantive.pop("generated_at_utc", None)
    supplied_evidence_fingerprint = substantive.pop("evidence_fingerprint", "")
    if supplied_evidence_fingerprint != canonical_hash(substantive):
        errors.append("evidence_fingerprint mismatch")
    return errors


def write_status(payload: dict[str, Any], output_path: str | Path) -> None:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or validate signed volume-component forward status"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--manifest", required=True)
    build.add_argument("--evidence-status", required=True)
    build.add_argument("--variant-metrics", required=True)
    build.add_argument("--statistics", required=True)
    build.add_argument("--provenance", required=True)
    build.add_argument("--registry", default=DEFAULT_REGISTRY)
    build.add_argument("--source-run-id", default="")
    build.add_argument("--output", default=DEFAULT_OUTPUT)

    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--output", default=DEFAULT_OUTPUT)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--status", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "build":
        payload = build_status(
            args.manifest,
            args.evidence_status,
            args.variant_metrics,
            args.statistics,
            args.provenance,
            args.registry,
            args.source_run_id,
        )
        write_status(payload, args.output)
    elif args.command == "initialize":
        payload = build_initial_status()
        write_status(payload, args.output)
    else:
        payload = load_json(args.status)

    errors = validate_status(payload)
    if errors:
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "evidence_status": payload["evidence_status"],
                "evidence_fingerprint": payload["evidence_fingerprint"],
                "horizons": payload["horizons"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
