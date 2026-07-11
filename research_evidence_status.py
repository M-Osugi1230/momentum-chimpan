"""Build a compact signed status from governed live execution evidence.

This file is safe to commit to the repository because it contains only derived
readiness metadata and input hashes. It never changes strategy parameters,
thresholds, paper positions, or production market state.
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

import evidence_provenance
import main

STATUS_VERSION = "2026-07-11-research-evidence-status-v1"
DEFAULT_OUTPUT = "data/research_evidence_status.json"
DEFAULT_ARTIFACT_DIR = "output/replay/evidence-status"
DEFAULT_REGISTRY = "research/experiment_registry.yaml"


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return payload


def load_registry(path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("experiment registry must be a mapping")
    return payload


def load_robustness(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return pd.DataFrame()
    frame = pd.read_csv(target)
    for column in (
        "horizon_days",
        "count",
        "fdr_q_value",
        "early_net_average_excess",
        "late_net_average_excess",
        "worst_leave_one_sector_excess",
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def select_governed_row(frame: pd.DataFrame, horizon_days: int = 10) -> dict[str, Any]:
    if frame.empty:
        return {}
    work = frame.copy()
    if "group_type" in work.columns:
        work = work[work["group_type"].astype(str) == "overall"]
    if "group_value" in work.columns:
        work = work[work["group_value"].astype(str) == "all"]
    if "horizon_days" in work.columns:
        exact = work[pd.to_numeric(work["horizon_days"], errors="coerce") == horizon_days]
        if not exact.empty:
            work = exact
    if work.empty:
        return {}
    if "count" in work.columns:
        work = work.sort_values("count", ascending=False)
    return work.iloc[0].to_dict()


def governance_issue_count(path: str) -> int:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return 0
    frame = pd.read_csv(target)
    if frame.empty:
        return 0
    if "severity" not in frame.columns:
        return len(frame)
    return int(frame["severity"].astype(str).str.upper().isin(["FAIL", "ERROR", "P0", "P1"]).sum())


def optional_float(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def optional_int(value: Any) -> int:
    converted = optional_float(value)
    return 0 if converted is None else int(converted)


def build_status(
    provenance_path: str,
    robustness_path: str,
    governance_issues_path: str,
    execution_manifest_path: str,
    registry_path: str = DEFAULT_REGISTRY,
    minimum_outcomes: int = 100,
    horizon_days: int = 10,
) -> dict[str, Any]:
    provenance = load_json(provenance_path)
    execution = load_json(execution_manifest_path)
    registry = load_registry(registry_path)
    robustness = load_robustness(robustness_path)
    row = select_governed_row(robustness, horizon_days)
    provenance_valid, provenance_detail = evidence_provenance.provenance_valid(provenance, registry)
    issue_count = governance_issue_count(governance_issues_path)
    outcome_count = optional_int(row.get("count", execution.get("outcome_count", 0)))
    robustness_status = str(row.get("robustness_status", "INSUFFICIENT") or "INSUFFICIENT")
    fdr_q_value = optional_float(row.get("fdr_q_value"))
    early_excess = optional_float(row.get("early_net_average_excess"))
    late_excess = optional_float(row.get("late_net_average_excess"))
    leave_one_sector = optional_float(row.get("worst_leave_one_sector_excess"))
    enough_outcomes = outcome_count >= minimum_outcomes
    robust = robustness_status == "ROBUST"
    manual_review_eligible = bool(
        provenance_valid
        and enough_outcomes
        and robust
        and issue_count == 0
    )
    if manual_review_eligible:
        readiness = "ELIGIBLE_FOR_MANUAL_REVIEW"
    elif outcome_count == 0:
        readiness = "NO_EXECUTION_EVIDENCE"
    elif not enough_outcomes:
        readiness = "ACCUMULATING"
    elif not provenance_valid:
        readiness = "PROVENANCE_BLOCKED"
    elif issue_count:
        readiness = "GOVERNANCE_BLOCKED"
    else:
        readiness = "FRAGILE"
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = {
        "status_version": STATUS_VERSION,
        "generated_at_utc": generated_at,
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "strategy_fingerprint": provenance.get("strategy_fingerprint", ""),
        "evidence_origin": provenance.get("evidence_origin", ""),
        "execution_origin": provenance.get("execution_origin", ""),
        "execution_model": provenance.get("execution_model", execution.get("entry_model", "")),
        "same_day_close_entry_allowed": provenance.get(
            "same_day_close_entry_allowed", execution.get("same_day_close_entry_allowed")
        ),
        "promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "provenance_valid": provenance_valid,
        "provenance_detail": provenance_detail,
        "governance_issue_count": issue_count,
        "governed_horizon_days": horizon_days,
        "minimum_outcomes": minimum_outcomes,
        "outcome_count": outcome_count,
        "enough_outcomes": enough_outcomes,
        "robustness_status": robustness_status,
        "fdr_q_value": fdr_q_value,
        "early_net_average_excess": early_excess,
        "late_net_average_excess": late_excess,
        "worst_leave_one_sector_excess": leave_one_sector,
        "manual_review_eligible": manual_review_eligible,
        "readiness": readiness,
        "automatic_strategy_change": False,
        "automatic_promotion": False,
        "manual_approval_required": True,
        "research_only": True,
        "inputs": {
            "provenance_path": provenance_path,
            "provenance_sha256": sha256_file(provenance_path),
            "robustness_path": robustness_path,
            "robustness_sha256": sha256_file(robustness_path),
            "governance_issues_path": governance_issues_path,
            "governance_issues_sha256": sha256_file(governance_issues_path),
            "execution_manifest_path": execution_manifest_path,
            "execution_manifest_sha256": sha256_file(execution_manifest_path),
            "registry_path": registry_path,
            "registry_sha256": sha256_file(registry_path),
        },
    }
    status_payload = json.dumps(status, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    status["status_sha256"] = hashlib.sha256(status_payload.encode("utf-8")).hexdigest()
    return status


def write_outputs(status: dict[str, Any], output_path: str, artifact_dir: str) -> dict[str, str]:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact = Path(artifact_dir)
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "research_evidence_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    flat = {key: value for key, value in status.items() if key != "inputs"}
    for key, value in status.get("inputs", {}).items():
        flat[f"input_{key}"] = value
    pd.DataFrame([flat]).to_csv(artifact / "research_evidence_status.csv", index=False)
    with pd.ExcelWriter(artifact / "research_evidence_status.xlsx", engine="openpyxl") as writer:
        pd.DataFrame([flat]).to_excel(writer, sheet_name="Evidence Status", index=False)
        pd.DataFrame([
            {"criterion": "provenance_valid", "passed": status["provenance_valid"]},
            {"criterion": "minimum_outcomes", "passed": status["enough_outcomes"]},
            {"criterion": "robustness_status", "passed": status["robustness_status"] == "ROBUST"},
            {"criterion": "governance_issue_count", "passed": status["governance_issue_count"] == 0},
            {"criterion": "manual_review_eligible", "passed": status["manual_review_eligible"]},
        ]).to_excel(writer, sheet_name="Readiness Criteria", index=False)
    return {
        "output": str(output),
        "json": str(artifact / "research_evidence_status.json"),
        "csv": str(artifact / "research_evidence_status.csv"),
        "excel": str(artifact / "research_evidence_status.xlsx"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build signed live execution evidence status")
    parser.add_argument("--provenance", default="output/replay/execution_evidence_provenance.json")
    parser.add_argument("--robustness", default="output/replay/execution/replay_robustness_summary.csv")
    parser.add_argument("--governance-issues", default="output/replay/execution/strategy_governance_issues.csv")
    parser.add_argument("--execution-manifest", default="output/replay/execution/execution_realism_manifest.json")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--minimum-outcomes", type=int, default=100)
    parser.add_argument("--horizon-days", type=int, default=10)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    status = build_status(
        args.provenance,
        args.robustness,
        args.governance_issues,
        args.execution_manifest,
        args.registry,
        args.minimum_outcomes,
        args.horizon_days,
    )
    paths = write_outputs(status, args.output, args.artifact_dir)
    print(json.dumps({"status": status, "paths": paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
