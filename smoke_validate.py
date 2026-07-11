"""Validate a limited-symbol end-to-end Momentum Chimpan run.

The smoke run never persists production state and never sends email. It checks
that external-data scanning, report generation, heartbeat creation, and strategy
fingerprinting complete coherently in one ephemeral GitHub Actions workspace.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import main

SMOKE_VERSION = "2026-07-11-e2e-smoke-v1"
REQUIRED_SHEETS = {
    "Summary",
    "Momentum Top100",
    "Sector Momentum",
    "Run Health",
    "Release Readiness",
    "Operational Alerts",
    "Errors",
}


def json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def load_json(path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    return json.loads(target.read_text(encoding="utf-8"))


def validate_smoke_run(
    report_path: str,
    heartbeat_path: str,
    fingerprint_path: str,
    max_symbols: int,
) -> dict[str, Any]:
    report = Path(report_path)
    if not report.exists() or report.stat().st_size == 0:
        raise RuntimeError("daily report was not generated")
    workbook = pd.ExcelFile(report)
    missing_sheets = sorted(REQUIRED_SHEETS - set(workbook.sheet_names))
    if missing_sheets:
        raise RuntimeError(f"daily report missing sheets: {missing_sheets}")

    summary_frame = pd.read_excel(report, sheet_name="Summary")
    if summary_frame.empty:
        raise RuntimeError("Summary sheet is empty")
    summary = {str(key): json_safe(value) for key, value in summary_frame.iloc[0].to_dict().items()}
    heartbeat = load_json(heartbeat_path)
    fingerprint = load_json(fingerprint_path)

    scan_count = int(pd.to_numeric(pd.Series([summary.get("実スキャン対象銘柄数")]), errors="coerce").fillna(0).iloc[0])
    if scan_count <= 0 or scan_count > max_symbols:
        raise RuntimeError(f"limited scan count outside expected range: {scan_count}")
    if str(summary.get("アプリ版")) != main.APP_VERSION:
        raise RuntimeError("report app version does not match imported application")
    if str(summary.get("実行モード")) != main.EXECUTION_MODE:
        raise RuntimeError("report execution mode is not research-only")
    if heartbeat.get("workflow_status") != "SUCCESS":
        raise RuntimeError("operational heartbeat does not report success")
    if heartbeat.get("execution_mode") != main.EXECUTION_MODE:
        raise RuntimeError("heartbeat execution mode mismatch")
    if heartbeat.get("research_only") is not True:
        raise RuntimeError("heartbeat research_only flag missing")
    if fingerprint.get("execution_mode") != main.EXECUTION_MODE:
        raise RuntimeError("strategy fingerprint execution mode mismatch")
    if fingerprint.get("research_only") is not True:
        raise RuntimeError("strategy fingerprint research_only flag missing")
    if not str(fingerprint.get("strategy_fingerprint", "")):
        raise RuntimeError("strategy fingerprint is empty")

    freshness = str(summary.get("市場データ鮮度") or "UNKNOWN")
    if freshness not in {"FRESH", "PARTIAL", "STALE", "EMPTY"}:
        raise RuntimeError(f"unexpected market data freshness: {freshness}")
    state_update = str(summary.get("状態更新実行") or "NO")
    if freshness != "FRESH" and state_update != "NO":
        raise RuntimeError("stale/partial/empty smoke run attempted state mutation")

    errors_frame = pd.read_excel(report, sheet_name="Errors")
    result = {
        "smoke_version": SMOKE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "scan_count": scan_count,
        "max_symbols": max_symbols,
        "market_data_freshness": freshness,
        "state_update_executed_in_process": state_update == "YES",
        "workflow_persistence_enabled": False,
        "email_enabled": False,
        "report_size_bytes": report.stat().st_size,
        "sheet_count": len(workbook.sheet_names),
        "error_row_count": len(errors_frame),
        "heartbeat_status": heartbeat.get("workflow_status"),
        "strategy_fingerprint": fingerprint.get("strategy_fingerprint"),
        "passed": True,
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate limited-symbol E2E smoke output")
    parser.add_argument("--report", default="output/daily_report.xlsx")
    parser.add_argument("--heartbeat", default="output/smoke/operations_heartbeat.json")
    parser.add_argument("--fingerprint", default="output/smoke/strategy_fingerprint.json")
    parser.add_argument("--max-symbols", type=int, default=5)
    parser.add_argument("--output", default="output/smoke/smoke_manifest.json")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    result = validate_smoke_run(args.report, args.heartbeat, args.fingerprint, args.max_symbols)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
