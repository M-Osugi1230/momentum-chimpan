"""Operational controls for the Momentum Chimpan daily workflow.

This module does not generate investment signals or place orders. It records a
workflow heartbeat, validates persisted state, applies bounded snapshot
retention, and sends failure notifications when email credentials are present.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pandas as pd

import main

OPERATIONS_VERSION = "2026-07-11-operations-v1"
DEFAULT_REPORT = "output/daily_report.xlsx"
DEFAULT_HEARTBEAT = "data/operations_heartbeat.json"
DEFAULT_MAINTENANCE_REPORT = "output/state_maintenance.json"
DEFAULT_NOTIFICATION_LOG = "output/ops_notification.txt"

STATE_SPECS: dict[str, set[str]] = {
    "data/momentum_daily_ranking.csv": {"date", "rank", "code", "close", "score"},
    "data/market_temperature.csv": {"date"},
    "data/paper_portfolio.csv": {"position_id", "status", "code", "current_price", "market_value"},
    "data/paper_trade_history.csv": {"position_id", "code", "exit_date", "realized_pnl"},
    "data/paper_equity_history.csv": {"date", "equity", "drawdown"},
    "data/execution_audit.csv": {"run_id", "date", "app_version", "release_status"},
}
OPTIONAL_STATE_PATHS = {
    "data/sector_leader_signal_history.csv",
    "data/execution_audit.csv",
}


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def read_report_summary(report_path: str) -> dict[str, Any]:
    target = Path(report_path)
    if not target.exists():
        raise FileNotFoundError(f"report not found: {report_path}")
    frame = pd.read_excel(target, sheet_name="Summary")
    if frame.empty:
        raise ValueError("Summary sheet is empty")
    return {str(key): _json_safe(value) for key, value in frame.iloc[0].to_dict().items()}


def build_heartbeat(
    summary: dict[str, Any],
    run_id: str = "",
    run_url: str = "",
    event_name: str = "",
) -> dict[str, Any]:
    freshness = str(summary.get("市場データ鮮度") or "UNKNOWN")
    state_update = str(summary.get("状態更新実行") or "NO").upper()
    return {
        "operations_version": OPERATIONS_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workflow_run_id": run_id,
        "workflow_run_url": run_url,
        "workflow_event": event_name,
        "workflow_status": "SUCCESS",
        "app_version": summary.get("アプリ版") or main.APP_VERSION,
        "report_date": summary.get("実行日"),
        "market_data_freshness": freshness,
        "latest_price_date": summary.get("最新株価日") or summary.get("株価データ日"),
        "current_day_price_ratio": summary.get("当日株価比率"),
        "state_update_executed": state_update == "YES",
        "run_health": summary.get("Run Health"),
        "release_status": summary.get("リリース判定"),
        "p0_alerts": summary.get("運用P0アラート"),
        "p1_alerts": summary.get("運用P1アラート"),
        "execution_mode": summary.get("実行モード") or main.EXECUTION_MODE,
        "research_only": True,
    }


def write_heartbeat(
    report_path: str,
    output_path: str,
    run_id: str = "",
    run_url: str = "",
    event_name: str = "",
) -> dict[str, Any]:
    heartbeat = build_heartbeat(read_report_summary(report_path), run_id, run_url, event_name)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(heartbeat, ensure_ascii=False, indent=2), encoding="utf-8")
    return heartbeat


def load_heartbeat(path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def validate_csv(path: str, required_columns: set[str], required: bool) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {
            "path": path,
            "status": "FAIL" if required else "WARN",
            "row_count": None,
            "missing_columns": sorted(required_columns),
            "detail": "file missing",
        }
    try:
        frame = pd.read_csv(target)
    except Exception as exc:
        return {
            "path": path,
            "status": "FAIL",
            "row_count": None,
            "missing_columns": [],
            "detail": f"unreadable: {exc}",
        }
    missing = sorted(required_columns - set(frame.columns))
    return {
        "path": path,
        "status": "FAIL" if missing else "PASS",
        "row_count": len(frame),
        "missing_columns": missing,
        "detail": "schema valid" if not missing else "required columns missing",
    }


def validate_state_files(state_update_executed: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path, columns in STATE_SPECS.items():
        required = state_update_executed and path not in OPTIONAL_STATE_PATHS
        results.append(validate_csv(path, columns, required))

    sector_path = Path("data/sector_leader_signal_history.csv")
    if sector_path.exists():
        try:
            sector = pd.read_csv(sector_path)
            has_code = "code" in sector.columns
            has_date = "signal_date" in sector.columns or "date" in sector.columns
            results.append({
                "path": str(sector_path),
                "status": "PASS" if has_code and has_date else "FAIL",
                "row_count": len(sector),
                "missing_columns": [] if has_code and has_date else ["code", "signal_date|date"],
                "detail": "schema valid" if has_code and has_date else "required identity columns missing",
            })
        except Exception as exc:
            results.append({
                "path": str(sector_path),
                "status": "FAIL",
                "row_count": None,
                "missing_columns": [],
                "detail": f"unreadable: {exc}",
            })
    elif state_update_executed:
        results.append({
            "path": str(sector_path),
            "status": "WARN",
            "row_count": None,
            "missing_columns": [],
            "detail": "no sector signals generated yet",
        })
    return results


def _snapshot_dates(root: Path) -> list[tuple[date, Path]]:
    rows: list[tuple[date, Path]] = []
    if not root.exists():
        return rows
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            snapshot_date = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        rows.append((snapshot_date, child))
    return sorted(rows)


def retention_plan(
    snapshots: list[tuple[date, Path]],
    daily_days: int = 30,
    monthly_months: int = 12,
) -> tuple[set[Path], set[Path]]:
    if not snapshots:
        return set(), set()
    latest = max(snapshot_date for snapshot_date, _ in snapshots)
    daily_cutoff = latest - timedelta(days=max(daily_days - 1, 0))
    keep: set[Path] = {
        path for snapshot_date, path in snapshots if snapshot_date >= daily_cutoff
    }

    older = [(snapshot_date, path) for snapshot_date, path in snapshots if snapshot_date < daily_cutoff]
    month_groups: dict[str, list[tuple[date, Path]]] = {}
    for snapshot_date, path in older:
        month_groups.setdefault(snapshot_date.strftime("%Y-%m"), []).append((snapshot_date, path))
    month_keys = sorted(month_groups, reverse=True)[:monthly_months]
    for month in month_keys:
        keep.add(max(month_groups[month], key=lambda item: item[0])[1])
    all_paths = {path for _, path in snapshots}
    return keep, all_paths - keep


def maintain_snapshots(
    root_path: str,
    state_update_executed: bool,
    daily_days: int = 30,
    monthly_months: int = 12,
) -> dict[str, Any]:
    root = Path(root_path)
    snapshots = _snapshot_dates(root)
    keep, remove = retention_plan(snapshots, daily_days, monthly_months)
    removed: list[str] = []
    if state_update_executed:
        for path in sorted(remove):
            for item in sorted(path.rglob("*"), reverse=True):
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    item.rmdir()
            path.rmdir()
            removed.append(str(path))
    return {
        "snapshot_root": str(root),
        "state_update_executed": state_update_executed,
        "daily_days": daily_days,
        "monthly_months": monthly_months,
        "snapshot_count_before": len(snapshots),
        "snapshot_count_kept": len(keep) if state_update_executed else len(snapshots),
        "snapshot_count_removed": len(removed),
        "removed_paths": removed,
    }


def run_maintenance(
    heartbeat_path: str,
    report_path: str,
    snapshot_root: str = "data/state_snapshots",
    daily_days: int = 30,
    monthly_months: int = 12,
) -> dict[str, Any]:
    heartbeat = load_heartbeat(heartbeat_path)
    state_update_executed = bool(heartbeat.get("state_update_executed"))
    validation = validate_state_files(state_update_executed)
    retention = maintain_snapshots(snapshot_root, state_update_executed, daily_days, monthly_months)
    failures = [row for row in validation if row["status"] == "FAIL"]
    result = {
        "operations_version": OPERATIONS_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "state_update_executed": state_update_executed,
        "validation_status": "FAIL" if failures else "PASS",
        "validation_failures": len(failures),
        "validation": validation,
        "retention": retention,
    }
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        raise RuntimeError(f"state schema validation failed: {[row['path'] for row in failures]}")
    return result


def _tail_log(path: str, max_lines: int = 80, max_chars: int = 12000) -> str:
    target = Path(path)
    if not target.exists():
        return "log file not found"
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    text = "\n".join(lines)
    return text[-max_chars:]


def notify_failure(
    stage: str,
    log_path: str,
    output_path: str,
    run_url: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    sender = os.getenv("EMAIL_FROM", "").strip()
    recipient = os.getenv("EMAIL_TO", "").strip()
    password = os.getenv("EMAIL_APP_PASSWORD", "").strip()
    excerpt = _tail_log(log_path)
    subject = f"[Momentum Chimpan][P0] Daily workflow failed: {stage}"
    body = (
        f"Momentum Chimpan daily workflow failed.\n\n"
        f"Stage: {stage}\nRun ID: {run_id}\nRun URL: {run_url}\n"
        f"Execution mode: {main.EXECUTION_MODE}\n\n"
        f"Log tail:\n{excerpt}\n"
    )
    status = "SKIPPED_NO_CREDENTIALS"
    error = ""
    if sender and recipient and password:
        try:
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = sender
            message["To"] = recipient
            message.set_content(body)
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
                smtp.login(sender, password)
                smtp.send_message(message)
            status = "SENT"
        except Exception as exc:
            status = "FAILED"
            error = str(exc)
    result = {
        "status": status,
        "stage": stage,
        "run_id": run_id,
        "run_url": run_url,
        "subject": subject,
        "error": error,
        "body": body,
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Momentum Chimpan operational controls")
    subparsers = parser.add_subparsers(dest="command", required=True)

    heartbeat = subparsers.add_parser("heartbeat")
    heartbeat.add_argument("--report", default=DEFAULT_REPORT)
    heartbeat.add_argument("--output", default=DEFAULT_HEARTBEAT)
    heartbeat.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID", ""))
    heartbeat.add_argument("--run-url", default="")
    heartbeat.add_argument("--event-name", default=os.getenv("GITHUB_EVENT_NAME", ""))

    maintain = subparsers.add_parser("maintain")
    maintain.add_argument("--heartbeat", default=DEFAULT_HEARTBEAT)
    maintain.add_argument("--report", default=DEFAULT_MAINTENANCE_REPORT)
    maintain.add_argument("--snapshot-root", default="data/state_snapshots")
    maintain.add_argument("--daily-days", type=int, default=30)
    maintain.add_argument("--monthly-months", type=int, default=12)

    notify = subparsers.add_parser("notify")
    notify.add_argument("--stage", required=True)
    notify.add_argument("--log", default="output/run.log")
    notify.add_argument("--output", default=DEFAULT_NOTIFICATION_LOG)
    notify.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID", ""))
    notify.add_argument("--run-url", default="")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "heartbeat":
        result = write_heartbeat(args.report, args.output, args.run_id, args.run_url, args.event_name)
    elif args.command == "maintain":
        result = run_maintenance(
            args.heartbeat, args.report, args.snapshot_root, args.daily_days, args.monthly_months
        )
    else:
        result = notify_failure(args.stage, args.log, args.output, args.run_url, args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
