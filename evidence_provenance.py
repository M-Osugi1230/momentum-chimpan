"""Seal research evidence provenance and prevent invalid promotion.

Live-forward evidence is eligible only when every ranking row is stamped with
the current governed strategy fingerprint. Historical current-universe backfill
is always exploratory and can never authorize a promoted experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import main
import strategy_governance

PROVENANCE_VERSION = "2026-07-11-evidence-provenance-v1"
LIVE_ORIGIN = "LIVE_FORWARD_RANKING_HISTORY"
BACKFILL_ORIGIN = "HISTORICAL_CURRENT_UNIVERSE_BACKFILL"
EXECUTION_ORIGIN = "LIVE_FORWARD_NEXT_OPEN_EXECUTION"
REQUIRED_EXECUTION_MODEL = "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"
ALLOWED_LIVE_SOURCE = "data/momentum_daily_ranking.csv"
PROVENANCE_AUDIT_COLUMNS = [
    "experiment_id",
    "status",
    "evidence_origin",
    "promotion_evidence_allowed",
    "strategy_fingerprint_matches",
    "provenance_valid",
    "promotion_valid_after_provenance",
]
PROVENANCE_ISSUE_COLUMNS = ["severity", "experiment_id", "issue"]


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_csv(frame: pd.DataFrame, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(target)


def atomic_write_json(payload: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def current_strategy_fingerprint() -> str:
    return strategy_governance.strategy_fingerprint()["sha256"]


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(str(path))
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON manifest must be an object: {path}")
    return payload


def report_state_update(report_path: str) -> tuple[str, bool, str]:
    frame = pd.read_excel(report_path, sheet_name="Summary")
    if frame.empty:
        raise ValueError("Summary sheet is empty")
    row = frame.iloc[0]
    report_date = str(row.get("実行日", "")).strip()
    state_update = str(row.get("状態更新実行", "NO")).strip().upper() == "YES"
    app_version = str(row.get("アプリ版", main.APP_VERSION)).strip()
    return report_date, state_update, app_version


def stamp_live_ranking_history(
    ranking_path: str,
    report_path: str,
    fingerprint_path: str,
    audit_path: str,
    snapshot_root: str = "data/state_snapshots",
) -> dict[str, Any]:
    fingerprint_manifest = load_json(fingerprint_path)
    fingerprint = str(fingerprint_manifest.get("strategy_fingerprint", "")).strip()
    if not fingerprint:
        raise ValueError("strategy fingerprint is empty")
    current = current_strategy_fingerprint()
    if fingerprint != current:
        raise ValueError("strategy fingerprint snapshot does not match current code")

    report_date, state_update, app_version = report_state_update(report_path)

    def verify_or_stamp(path: Path, required: bool) -> dict[str, Any]:
        if not path.exists():
            if required:
                raise FileNotFoundError(str(path))
            return {
                "row_count": 0,
                "already_stamped": 0,
                "changed": False,
                "sha256": "",
            }
        frame = pd.read_csv(path, dtype={"code": str})
        if "date" not in frame.columns:
            raise ValueError(f"ranking state is missing date column: {path}")
        missing_columns = [
            column
            for column in (
                "strategy_fingerprint",
                "strategy_app_version",
                "strategy_stamp_source",
            )
            if column not in frame.columns
        ]
        for column in missing_columns:
            frame[column] = ""
        mask = frame["date"].astype(str) == report_date
        row_count = int(mask.sum())
        if required and row_count == 0:
            raise ValueError(f"no ranking rows found for report date {report_date}: {path}")
        existing = frame.loc[mask, "strategy_fingerprint"].fillna("").astype(str).str.strip()
        mismatch = existing.ne("") & existing.ne(fingerprint)
        if bool(mismatch.any()):
            raise ValueError(f"ranking rows already contain a different strategy fingerprint: {path}")
        already_stamped = int(existing.eq(fingerprint).sum())
        blank_rows = existing.eq("")
        changed = bool(missing_columns or blank_rows.any())
        if row_count:
            frame.loc[mask, "strategy_fingerprint"] = fingerprint
            frame.loc[mask, "strategy_app_version"] = app_version
            frame.loc[mask, "strategy_stamp_source"] = "DAILY_GOVERNED_WORKFLOW"
        if changed:
            atomic_write_csv(frame, path)
        return {
            "row_count": row_count,
            "already_stamped": already_stamped,
            "changed": changed,
            "sha256": sha256_file(path),
        }

    ranking_result = {
        "row_count": 0,
        "already_stamped": 0,
        "changed": False,
        "sha256": sha256_file(ranking_path),
    }
    snapshot_result = {
        "row_count": 0,
        "already_stamped": 0,
        "changed": False,
        "sha256": "",
    }
    snapshot_path = Path(snapshot_root) / report_date / "ranking_history.csv"
    if state_update:
        ranking_result = verify_or_stamp(Path(ranking_path), required=True)
        snapshot_result = verify_or_stamp(snapshot_path, required=True)
        if ranking_result["row_count"] != snapshot_result["row_count"]:
            raise ValueError("ranking history and state snapshot have different report-date row counts")

    audit = {
        "provenance_version": PROVENANCE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ranking_path": ranking_path,
        "snapshot_path": str(snapshot_path),
        "report_date": report_date,
        "state_update_executed": state_update,
        "strategy_fingerprint": fingerprint,
        "strategy_app_version": app_version,
        "stamped_rows": ranking_result["row_count"],
        "already_stamped_rows": ranking_result["already_stamped"],
        "ranking_changed_by_verifier": ranking_result["changed"],
        "ranking_sha256": ranking_result["sha256"],
        "snapshot_verified": bool(state_update),
        "snapshot_stamped_rows": snapshot_result["row_count"],
        "snapshot_already_stamped_rows": snapshot_result["already_stamped"],
        "snapshot_changed_by_verifier": snapshot_result["changed"],
        "snapshot_sha256": snapshot_result["sha256"],
        "research_only": True,
    }
    atomic_write_json(audit, audit_path)
    return audit


def prepare_live_history(
    ranking_path: str,
    output_path: str,
    provenance_path: str,
    fingerprint_path: str | None = None,
) -> dict[str, Any]:
    source = Path(ranking_path)
    if source.as_posix() != ALLOWED_LIVE_SOURCE:
        raise ValueError(f"live evidence source must be {ALLOWED_LIVE_SOURCE}")
    if not source.exists():
        raise FileNotFoundError(ranking_path)
    frame = pd.read_csv(source, dtype={"code": str})
    if "strategy_fingerprint" not in frame.columns:
        frame["strategy_fingerprint"] = ""
    current = current_strategy_fingerprint()
    if fingerprint_path:
        snapshot = load_json(fingerprint_path)
        snapshot_fingerprint = str(snapshot.get("strategy_fingerprint", "")).strip()
        if snapshot_fingerprint and snapshot_fingerprint != current:
            raise ValueError("stored strategy fingerprint does not match current code")
    eligible = frame[frame["strategy_fingerprint"].astype(str) == current].copy()
    if not eligible.empty:
        eligible["date"] = eligible["date"].astype(str)
        eligible = eligible.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "rank"])
    atomic_write_csv(eligible, output_path)
    dates = sorted(eligible.get("date", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
    source_dates = sorted(frame.get("date", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
    payload = {
        "provenance_version": PROVENANCE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_origin": LIVE_ORIGIN,
        "promotion_evidence_allowed": True,
        "strategy_fingerprint": current,
        "source_path": ranking_path,
        "source_sha256": sha256_file(source),
        "filtered_history_path": output_path,
        "filtered_history_sha256": sha256_file(output_path),
        "source_row_count": len(frame),
        "eligible_row_count": len(eligible),
        "source_date_count": len(source_dates),
        "eligible_date_count": len(dates),
        "first_eligible_date": dates[0] if dates else "",
        "last_eligible_date": dates[-1] if dates else "",
        "bias_flags": [],
        "research_only": True,
    }
    atomic_write_json(payload, provenance_path)
    return payload


def seal_derived_backfill(source_manifest_path: str, provenance_path: str) -> dict[str, Any]:
    source = load_json(source_manifest_path)
    if source.get("universe_bias") != "CURRENT_LIST_ONLY_SURVIVORSHIP_AND_DELISTING_BIAS":
        raise ValueError("derived backfill manifest is missing the required survivorship-bias declaration")
    if source.get("promotion_evidence_allowed") is not False:
        raise ValueError("derived backfill manifest must explicitly prohibit promotion evidence")
    payload = {
        "provenance_version": PROVENANCE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_origin": BACKFILL_ORIGIN,
        "promotion_evidence_allowed": False,
        "strategy_fingerprint": current_strategy_fingerprint(),
        "source_manifest_path": source_manifest_path,
        "source_manifest_sha256": sha256_file(source_manifest_path),
        "source_history_sha256": source.get("jpx_cache_sha256", ""),
        "eligible_date_count": int(source.get("ranking_date_count", 0) or 0),
        "bias_flags": [
            "SURVIVORSHIP_BIAS",
            "DELISTING_BIAS",
            "CURRENT_CONSTITUENTS_ONLY",
        ],
        "research_only": True,
    }
    atomic_write_json(payload, provenance_path)
    return payload




def _finite_number(value: Any) -> bool:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return pd.notna(converted) and float(converted) >= 0


def seal_execution_evidence(
    source_provenance_path: str,
    execution_manifest_path: str,
    provenance_path: str,
) -> dict[str, Any]:
    source = load_json(source_provenance_path)
    execution = load_json(execution_manifest_path)
    current = current_strategy_fingerprint()
    source_fingerprint = str(source.get("strategy_fingerprint", ""))
    execution_fingerprint = str(execution.get("strategy_fingerprint", source_fingerprint))
    execution_model = str(execution.get("entry_model", ""))
    same_day_allowed = execution.get("same_day_close_entry_allowed")
    outcome_count = int(pd.to_numeric(pd.Series([execution.get("outcome_count")]), errors="coerce").fillna(0).iloc[0])
    cost_fields = {
        "entry_slippage_bps": execution.get("default_entry_slippage_bps"),
        "exit_slippage_bps": execution.get("default_exit_slippage_bps"),
        "fees_bps": execution.get("default_fees_bps"),
    }
    controls_valid = bool(
        execution_model == REQUIRED_EXECUTION_MODEL
        and same_day_allowed is False
        and all(_finite_number(value) for value in cost_fields.values())
        and outcome_count > 0
    )
    promotion_allowed = bool(
        source.get("promotion_evidence_allowed") is True
        and execution.get("promotion_evidence_allowed") is True
        and source_fingerprint == current
        and execution_fingerprint == current
        and controls_valid
    )
    payload = {
        "provenance_version": PROVENANCE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_origin": source.get("evidence_origin", ""),
        "execution_origin": EXECUTION_ORIGIN,
        "execution_evidence": True,
        "promotion_evidence_allowed": promotion_allowed,
        "strategy_fingerprint": source_fingerprint,
        "source_path": source.get("source_path", ""),
        "source_provenance_path": source_provenance_path,
        "source_provenance_sha256": sha256_file(source_provenance_path),
        "execution_manifest_path": execution_manifest_path,
        "execution_manifest_sha256": sha256_file(execution_manifest_path),
        "execution_model": execution_model,
        "same_day_close_entry_allowed": same_day_allowed,
        "entry_slippage_bps": cost_fields["entry_slippage_bps"],
        "exit_slippage_bps": cost_fields["exit_slippage_bps"],
        "fees_bps": cost_fields["fees_bps"],
        "execution_outcome_count": outcome_count,
        "execution_controls_valid": controls_valid,
        "bias_flags": source.get("bias_flags", []),
        "research_only": True,
    }
    atomic_write_json(payload, provenance_path)
    return payload


def provenance_valid(provenance: dict[str, Any], registry: dict[str, Any]) -> tuple[bool, str]:
    policy = registry.get("policy", {}) or {}
    allowed_origins = set(policy.get("allowed_promotion_evidence_origins", [LIVE_ORIGIN]))
    origin = str(provenance.get("evidence_origin", ""))
    if provenance.get("promotion_evidence_allowed") is not True:
        return False, "evidence manifest explicitly prohibits promotion"
    if origin not in allowed_origins:
        return False, f"evidence origin is not allowed for promotion: {origin}"
    if str(provenance.get("strategy_fingerprint", "")) != current_strategy_fingerprint():
        return False, "evidence strategy fingerprint does not match current code"
    if origin == LIVE_ORIGIN and str(provenance.get("source_path", "")) != ALLOWED_LIVE_SOURCE:
        return False, "live evidence source path is not the governed ranking history"
    required_execution_model = str(policy.get("required_promotion_execution_model", "")).strip()
    if required_execution_model:
        if provenance.get("execution_evidence") is not True:
            return False, "promotion evidence is not sealed execution evidence"
        if str(provenance.get("execution_model", "")) != required_execution_model:
            return False, "execution model does not satisfy promotion policy"
        if provenance.get("same_day_close_entry_allowed") is not False:
            return False, "same-day close entry is not allowed for promotion"
        for field in ("entry_slippage_bps", "exit_slippage_bps", "fees_bps"):
            if not _finite_number(provenance.get(field)):
                return False, f"execution cost control is missing or invalid: {field}"
        if int(pd.to_numeric(pd.Series([provenance.get("execution_outcome_count")]), errors="coerce").fillna(0).iloc[0]) <= 0:
            return False, "execution evidence has no outcomes"
    return True, "promotion provenance is valid"


def governance_audit_with_provenance(
    output_dir: str,
    registry_path: str,
    robustness_path: str,
    provenance_path: str,
) -> dict[str, Any]:
    provenance = load_json(provenance_path)
    registry = strategy_governance.load_registry(registry_path)
    base_result = strategy_governance.write_audit(output_dir, registry_path, robustness_path)
    audit = base_result["audit"].copy()
    issues = base_result["issues"].copy()
    if issues.empty:
        issues = pd.DataFrame(columns=PROVENANCE_ISSUE_COLUMNS)
    else:
        for column in PROVENANCE_ISSUE_COLUMNS:
            if column not in issues.columns:
                issues[column] = ""
        issues = issues[PROVENANCE_ISSUE_COLUMNS]

    valid, detail = provenance_valid(provenance, registry)
    provenance_rows: list[dict[str, Any]] = []
    for experiment in registry.get("experiments", []):
        experiment_id = str(experiment.get("experiment_id", ""))
        status = str(experiment.get("status", ""))
        is_promoted = status == "promoted"
        audit_row = audit[audit.get("experiment_id", pd.Series(dtype=str)).astype(str) == experiment_id]
        base_promotion_valid = bool(audit_row.iloc[0].get("promotion_valid")) if not audit_row.empty else False
        promotion_valid = base_promotion_valid and (valid or not is_promoted)
        provenance_rows.append({
            "experiment_id": experiment_id,
            "status": status,
            "evidence_origin": provenance.get("evidence_origin", ""),
            "promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
            "strategy_fingerprint_matches": str(provenance.get("strategy_fingerprint", "")) == current_strategy_fingerprint(),
            "provenance_valid": valid,
            "promotion_valid_after_provenance": promotion_valid,
        })
        if is_promoted and not valid:
            issues = pd.concat([issues, pd.DataFrame([{
                "severity": "FAIL",
                "experiment_id": experiment_id,
                "issue": f"promotion blocked by evidence provenance: {detail}",
            }])], ignore_index=True)

    provenance_audit = pd.DataFrame(provenance_rows, columns=PROVENANCE_AUDIT_COLUMNS)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    provenance_audit.to_csv(target / "evidence_provenance_audit.csv", index=False)
    issues.to_csv(target / "strategy_governance_issues.csv", index=False)
    manifest = {
        "provenance_version": PROVENANCE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_origin": provenance.get("evidence_origin", ""),
        "promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "provenance_valid": valid,
        "provenance_detail": detail,
        "strategy_fingerprint": provenance.get("strategy_fingerprint", ""),
        "experiment_count": len(provenance_audit),
        "issue_count": len(issues),
        "automatic_promotion": False,
        "research_only": True,
    }
    atomic_write_json(manifest, target / "evidence_provenance_audit.json")
    with pd.ExcelWriter(target / "evidence_provenance_audit.xlsx", engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Provenance Summary", index=False)
        provenance_audit.to_excel(writer, sheet_name="Experiment Provenance", index=False)
        issues.to_excel(writer, sheet_name="Issues", index=False)
    return {
        "manifest": manifest,
        "audit": provenance_audit,
        "issues": issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seal and audit evidence provenance")
    sub = parser.add_subparsers(dest="command", required=True)

    stamp = sub.add_parser("stamp-live")
    stamp.add_argument("--ranking", default=ALLOWED_LIVE_SOURCE)
    stamp.add_argument("--report", default="output/daily_report.xlsx")
    stamp.add_argument("--fingerprint", default="data/strategy_fingerprint.json")
    stamp.add_argument("--audit", default="output/evidence_stamp_audit.json")
    stamp.add_argument("--snapshot-root", default="data/state_snapshots")

    prepare = sub.add_parser("prepare-live")
    prepare.add_argument("--ranking", default=ALLOWED_LIVE_SOURCE)
    prepare.add_argument("--output", default="output/replay/live_strategy_history.csv")
    prepare.add_argument("--provenance", default="output/replay/evidence_provenance.json")
    prepare.add_argument("--fingerprint", default="data/strategy_fingerprint.json")

    derived = sub.add_parser("seal-derived")
    derived.add_argument("--source-manifest", required=True)
    derived.add_argument("--provenance", required=True)

    execution = sub.add_parser("seal-execution")
    execution.add_argument("--source-provenance", required=True)
    execution.add_argument("--execution-manifest", required=True)
    execution.add_argument("--provenance", required=True)

    audit = sub.add_parser("governance-audit")
    audit.add_argument("--output-dir", required=True)
    audit.add_argument("--registry", default=strategy_governance.DEFAULT_REGISTRY)
    audit.add_argument("--robustness", required=True)
    audit.add_argument("--provenance", required=True)
    audit.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "stamp-live":
        result = stamp_live_ranking_history(
            args.ranking, args.report, args.fingerprint, args.audit, args.snapshot_root
        )
    elif args.command == "prepare-live":
        result = prepare_live_history(args.ranking, args.output, args.provenance, args.fingerprint)
    elif args.command == "seal-derived":
        result = seal_derived_backfill(args.source_manifest, args.provenance)
    elif args.command == "seal-execution":
        result = seal_execution_evidence(
            args.source_provenance, args.execution_manifest, args.provenance
        )
    else:
        result = governance_audit_with_provenance(
            args.output_dir, args.registry, args.robustness, args.provenance
        )
        if args.strict and not result["issues"].empty:
            raise RuntimeError("evidence provenance governance audit failed")
        result = result["manifest"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
