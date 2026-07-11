"""Seal Momentum Chimpan state snapshots and verify isolated recovery.

This module never restores files into the production data directory. It seals
existing daily state snapshots with SHA-256 metadata, identifies the newest
valid restore point, copies it into an isolated recovery workspace, and verifies
that every restored file is readable and byte-identical to the sealed source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import main

RECOVERY_VERSION = "2026-07-11-state-recovery-v1"
SNAPSHOT_SCHEMA_VERSION = "2.0"
DEFAULT_SNAPSHOT_ROOT = "data/state_snapshots"
DEFAULT_DRILL_OUTPUT = "output/recovery"

STATE_FILES: dict[str, str] = {
    "ranking_history": "data/momentum_daily_ranking.csv",
    "market_temperature": "data/market_temperature.csv",
    "sector_leader_signals": "data/sector_leader_signal_history.csv",
    "paper_portfolio": "data/paper_portfolio.csv",
    "paper_trade_history": "data/paper_trade_history.csv",
    "paper_equity_history": "data/paper_equity_history.csv",
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


def atomic_write_json(payload: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def csv_shape(path: str | Path) -> tuple[int | None, int | None]:
    target = Path(path)
    if not target.exists():
        return None, None
    if target.stat().st_size == 0:
        return 0, 0
    try:
        frame = pd.read_csv(target)
    except Exception:
        return None, None
    return len(frame), len(frame.columns)


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(str(path))
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return payload


def report_state(report_path: str) -> tuple[str, bool, str]:
    frame = pd.read_excel(report_path, sheet_name="Summary")
    if frame.empty:
        raise ValueError("Summary sheet is empty")
    row = frame.iloc[0]
    report_date = str(row.get("実行日", "")).strip()
    state_update = str(row.get("状態更新実行", "NO")).strip().upper() == "YES"
    app_version = str(row.get("アプリ版", main.APP_VERSION)).strip()
    if not report_date:
        raise ValueError("report date is empty")
    return report_date, state_update, app_version


def snapshot_file(snapshot_dir: Path, state_name: str) -> Path:
    return snapshot_dir / f"{state_name}.csv"


def inventory_snapshot(snapshot_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state_name, production_path in STATE_FILES.items():
        path = snapshot_file(snapshot_dir, state_name)
        exists = path.exists() and path.is_file()
        row_count, column_count = csv_shape(path)
        if not exists:
            status = "MISSING"
        elif row_count is None:
            status = "UNREADABLE"
        elif path.stat().st_size == 0:
            status = "EMPTY"
        else:
            status = "OK"
        rows.append({
            "state_name": state_name,
            "production_path": production_path,
            "snapshot_path": str(path),
            "exists": exists,
            "size_bytes": path.stat().st_size if exists else 0,
            "row_count": row_count,
            "column_count": column_count,
            "sha256": sha256_file(path),
            "status": status,
        })
    return rows


def verify_ranking_fingerprint(
    ranking_path: Path,
    snapshot_date: str,
    strategy_fingerprint: str,
) -> tuple[bool, int, str]:
    if not ranking_path.exists():
        return False, 0, "ranking snapshot is missing"
    try:
        frame = pd.read_csv(ranking_path, dtype={"code": str})
    except Exception as exc:
        return False, 0, f"ranking snapshot is unreadable: {exc}"
    required = {"date", "strategy_fingerprint"}
    if not required.issubset(frame.columns):
        return False, 0, "ranking snapshot is missing strategy stamp columns"
    rows = frame[frame["date"].astype(str) == snapshot_date]
    if rows.empty:
        return False, 0, "ranking snapshot has no rows for snapshot date"
    fingerprints = rows["strategy_fingerprint"].fillna("").astype(str).str.strip()
    if not fingerprints.eq(strategy_fingerprint).all():
        return False, len(rows), "ranking snapshot contains a mismatched strategy fingerprint"
    return True, len(rows), "strategy fingerprint verified"


def seal_snapshot(
    report_path: str,
    fingerprint_path: str,
    snapshot_root: str = DEFAULT_SNAPSHOT_ROOT,
    audit_path: str = "output/recovery_snapshot_audit.json",
) -> dict[str, Any]:
    report_date, state_update, app_version = report_state(report_path)
    fingerprint_manifest = load_json(fingerprint_path)
    strategy_fingerprint = str(fingerprint_manifest.get("strategy_fingerprint", "")).strip()
    if not strategy_fingerprint:
        raise ValueError("strategy fingerprint is empty")
    snapshot_dir = Path(snapshot_root) / report_date
    manifest_path = snapshot_dir / "snapshot_manifest.json"

    if not state_update:
        audit = {
            "recovery_version": RECOVERY_VERSION,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "snapshot_date": report_date,
            "state_update_executed": False,
            "status": "SKIPPED_NO_STATE_UPDATE",
            "snapshot_manifest": str(manifest_path),
            "research_only": True,
        }
        atomic_write_json(audit, audit_path)
        return audit

    if not snapshot_dir.exists():
        raise FileNotFoundError(str(snapshot_dir))
    files = inventory_snapshot(snapshot_dir)
    complete_files = all(row["status"] == "OK" for row in files)
    ranking_ok, ranking_rows, ranking_detail = verify_ranking_fingerprint(
        snapshot_file(snapshot_dir, "ranking_history"),
        report_date,
        strategy_fingerprint,
    )
    complete = bool(complete_files and ranking_ok)
    manifest = {
        "recovery_version": RECOVERY_VERSION,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "sealed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot_date": report_date,
        "app_version": app_version,
        "execution_mode": main.EXECUTION_MODE,
        "strategy_fingerprint": strategy_fingerprint,
        "state_file_count": len(files),
        "valid_state_file_count": sum(row["status"] == "OK" for row in files),
        "ranking_rows_for_snapshot_date": ranking_rows,
        "ranking_fingerprint_verified": ranking_ok,
        "ranking_fingerprint_detail": ranking_detail,
        "complete": complete,
        "manual_restore_only": True,
        "automatic_production_restore": False,
        "files": files,
    }
    atomic_write_json(manifest, manifest_path)
    audit = {
        "recovery_version": RECOVERY_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot_date": report_date,
        "state_update_executed": True,
        "status": "SEALED" if complete else "INVALID",
        "snapshot_manifest": str(manifest_path),
        "snapshot_manifest_sha256": sha256_file(manifest_path),
        "complete": complete,
        "research_only": True,
    }
    atomic_write_json(audit, audit_path)
    if not complete:
        raise RuntimeError(f"state snapshot is incomplete or invalid: {manifest_path}")
    return audit


def validate_snapshot(snapshot_dir: Path) -> tuple[bool, dict[str, Any], list[str]]:
    manifest_path = snapshot_dir / "snapshot_manifest.json"
    issues: list[str] = []
    if not manifest_path.exists():
        return False, {}, ["snapshot manifest is missing"]
    try:
        manifest = load_json(manifest_path)
    except Exception as exc:
        return False, {}, [f"snapshot manifest is unreadable: {exc}"]
    if manifest.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION:
        issues.append("snapshot schema version mismatch")
    if manifest.get("complete") is not True:
        issues.append("snapshot manifest is not marked complete")
    if manifest.get("automatic_production_restore") is not False:
        issues.append("snapshot permits automatic production restore")
    expected_names = set(STATE_FILES)
    manifest_rows = manifest.get("files", [])
    row_by_name = {
        str(row.get("state_name", "")): row
        for row in manifest_rows
        if isinstance(row, dict)
    }
    if set(row_by_name) != expected_names:
        issues.append("snapshot manifest state file set mismatch")
    for state_name in sorted(expected_names):
        path = snapshot_file(snapshot_dir, state_name)
        row = row_by_name.get(state_name, {})
        if not path.exists():
            issues.append(f"missing snapshot file: {state_name}")
            continue
        if sha256_file(path) != str(row.get("sha256", "")):
            issues.append(f"snapshot checksum mismatch: {state_name}")
        rows, columns = csv_shape(path)
        if rows is None or columns is None:
            issues.append(f"snapshot file unreadable: {state_name}")
        if rows != row.get("row_count") or columns != row.get("column_count"):
            issues.append(f"snapshot shape mismatch: {state_name}")
    fingerprint = str(manifest.get("strategy_fingerprint", ""))
    date_value = str(manifest.get("snapshot_date", ""))
    ranking_ok, _, detail = verify_ranking_fingerprint(
        snapshot_file(snapshot_dir, "ranking_history"), date_value, fingerprint
    )
    if not ranking_ok:
        issues.append(detail)
    return not issues, manifest, issues


def snapshot_catalog(snapshot_root: str) -> pd.DataFrame:
    root = Path(snapshot_root)
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return pd.DataFrame(columns=[
            "snapshot_date", "snapshot_path", "valid", "issue_count",
            "issues", "strategy_fingerprint", "app_version", "manifest_sha256",
        ])
    for directory in sorted((path for path in root.iterdir() if path.is_dir()), reverse=True):
        valid, manifest, issues = validate_snapshot(directory)
        rows.append({
            "snapshot_date": directory.name,
            "snapshot_path": str(directory),
            "valid": valid,
            "issue_count": len(issues),
            "issues": " | ".join(issues),
            "strategy_fingerprint": manifest.get("strategy_fingerprint", ""),
            "app_version": manifest.get("app_version", ""),
            "manifest_sha256": sha256_file(directory / "snapshot_manifest.json"),
        })
    return pd.DataFrame(rows)


def latest_valid_snapshot(snapshot_root: str) -> tuple[Path | None, pd.DataFrame]:
    catalog = snapshot_catalog(snapshot_root)
    if catalog.empty:
        return None, catalog
    valid = catalog[catalog["valid"] == True].sort_values("snapshot_date", ascending=False)
    if valid.empty:
        return None, catalog
    return Path(valid.iloc[0]["snapshot_path"]), catalog


def build_recovery_plan(snapshot_dir: Path, production_root: str = ".") -> pd.DataFrame:
    root = Path(production_root)
    rows: list[dict[str, Any]] = []
    for state_name, production_relative in STATE_FILES.items():
        source = snapshot_file(snapshot_dir, state_name)
        destination = root / production_relative
        source_hash = sha256_file(source)
        destination_hash = sha256_file(destination)
        rows.append({
            "state_name": state_name,
            "snapshot_path": str(source),
            "production_path": str(destination),
            "snapshot_sha256": source_hash,
            "production_sha256": destination_hash,
            "production_exists": destination.exists(),
            "action": "NO_CHANGE" if source_hash and source_hash == destination_hash else "WOULD_RESTORE",
        })
    return pd.DataFrame(rows)


def restore_to_sandbox(snapshot_dir: Path, destination_root: str) -> pd.DataFrame:
    destination = Path(destination_root)
    destination.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for state_name, production_relative in STATE_FILES.items():
        source = snapshot_file(snapshot_dir, state_name)
        target = destination / production_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_hash = sha256_file(source)
        target_hash = sha256_file(target)
        row_count, column_count = csv_shape(target)
        verified = bool(source_hash and source_hash == target_hash and row_count is not None)
        rows.append({
            "state_name": state_name,
            "source_path": str(source),
            "restored_path": str(target),
            "source_sha256": source_hash,
            "restored_sha256": target_hash,
            "row_count": row_count,
            "column_count": column_count,
            "verified": verified,
        })
    return pd.DataFrame(rows)


def run_recovery_drill(
    snapshot_root: str = DEFAULT_SNAPSHOT_ROOT,
    output_dir: str = DEFAULT_DRILL_OUTPUT,
    production_root: str = ".",
    strict: bool = False,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    selected, catalog = latest_valid_snapshot(snapshot_root)
    catalog.to_csv(output / "snapshot_catalog.csv", index=False)
    if selected is None:
        manifest = {
            "recovery_version": RECOVERY_VERSION,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "NO_VALID_SNAPSHOT",
            "selected_snapshot": "",
            "catalog_count": len(catalog),
            "valid_snapshot_count": int(catalog.get("valid", pd.Series(dtype=bool)).fillna(False).sum()) if not catalog.empty else 0,
            "production_state_mutated": False,
            "automatic_production_restore": False,
            "research_only": True,
        }
        atomic_write_json(manifest, output / "recovery_drill_manifest.json")
        if strict:
            raise RuntimeError("no valid state snapshot is available")
        return manifest

    plan = build_recovery_plan(selected, production_root)
    restored = restore_to_sandbox(selected, str(output / "sandbox"))
    plan.to_csv(output / "recovery_plan.csv", index=False)
    restored.to_csv(output / "restore_verification.csv", index=False)
    all_verified = bool(not restored.empty and restored["verified"].all())
    manifest = {
        "recovery_version": RECOVERY_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "PASS" if all_verified else "FAIL",
        "selected_snapshot": str(selected),
        "selected_snapshot_date": selected.name,
        "catalog_count": len(catalog),
        "valid_snapshot_count": int(catalog["valid"].fillna(False).sum()) if not catalog.empty else 0,
        "state_file_count": len(restored),
        "verified_state_file_count": int(restored["verified"].sum()) if not restored.empty else 0,
        "would_restore_count": int((plan["action"] == "WOULD_RESTORE").sum()) if not plan.empty else 0,
        "production_state_mutated": False,
        "sandbox_root": str(output / "sandbox"),
        "automatic_production_restore": False,
        "manual_restore_only": True,
        "research_only": True,
    }
    atomic_write_json(manifest, output / "recovery_drill_manifest.json")
    with pd.ExcelWriter(output / "recovery_drill.xlsx", engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Recovery Summary", index=False)
        catalog.to_excel(writer, sheet_name="Snapshot Catalog", index=False)
        plan.to_excel(writer, sheet_name="Recovery Plan", index=False)
        restored.to_excel(writer, sheet_name="Restore Verification", index=False)
    if strict and not all_verified:
        raise RuntimeError("sandbox recovery verification failed")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seal and drill state snapshot recovery")
    sub = parser.add_subparsers(dest="command", required=True)

    seal = sub.add_parser("seal")
    seal.add_argument("--report", default="output/daily_report.xlsx")
    seal.add_argument("--fingerprint", default="data/strategy_fingerprint.json")
    seal.add_argument("--snapshot-root", default=DEFAULT_SNAPSHOT_ROOT)
    seal.add_argument("--audit", default="output/recovery_snapshot_audit.json")

    drill = sub.add_parser("drill")
    drill.add_argument("--snapshot-root", default=DEFAULT_SNAPSHOT_ROOT)
    drill.add_argument("--output-dir", default=DEFAULT_DRILL_OUTPUT)
    drill.add_argument("--production-root", default=".")
    drill.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "seal":
        result = seal_snapshot(
            args.report,
            args.fingerprint,
            args.snapshot_root,
            args.audit,
        )
    else:
        result = run_recovery_drill(
            args.snapshot_root,
            args.output_dir,
            args.production_root,
            args.strict,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
