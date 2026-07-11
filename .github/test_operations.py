from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import operations


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    report = root / "daily_report.xlsx"
    heartbeat_path = root / "heartbeat.json"
    summary = pd.DataFrame([{
        "実行日": "2026-07-10",
        "アプリ版": "test-version",
        "市場データ鮮度": "FRESH",
        "最新株価日": "2026-07-10",
        "当日株価比率": 0.99,
        "状態更新実行": "YES",
        "Run Health": "PASS",
        "リリース判定": "PAPER_VALIDATION",
        "運用P0アラート": 0,
        "運用P1アラート": 0,
        "実行モード": "RESEARCH_AND_PAPER_ONLY",
    }])
    with pd.ExcelWriter(report, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)

    heartbeat = operations.write_heartbeat(
        str(report), str(heartbeat_path), "123", "https://example.test/run/123", "workflow_dispatch"
    )
    assert heartbeat["workflow_status"] == "SUCCESS"
    assert heartbeat["state_update_executed"] is True
    assert heartbeat["market_data_freshness"] == "FRESH"
    assert json.loads(heartbeat_path.read_text(encoding="utf-8"))["workflow_run_id"] == "123"

    valid_csv = root / "valid.csv"
    pd.DataFrame([{"date": "2026-07-10", "code": "7203"}]).to_csv(valid_csv, index=False)
    valid = operations.validate_csv(str(valid_csv), {"date", "code"}, required=True)
    invalid = operations.validate_csv(str(valid_csv), {"date", "rank"}, required=True)
    assert valid["status"] == "PASS"
    assert invalid["status"] == "FAIL"
    assert invalid["missing_columns"] == ["rank"]

    snapshots = root / "snapshots"
    start = date(2026, 4, 1)
    for offset in range(100):
        path = snapshots / (start + timedelta(days=offset)).isoformat()
        path.mkdir(parents=True, exist_ok=True)
        (path / "state.csv").write_text("date,value\n2026-01-01,1\n", encoding="utf-8")

    maintenance = operations.maintain_snapshots(
        str(snapshots), state_update_executed=True, daily_days=30, monthly_months=12
    )
    remaining = [path for path in snapshots.iterdir() if path.is_dir()]
    assert maintenance["snapshot_count_removed"] > 0
    assert len(remaining) <= 42
    assert len(remaining) >= 30

    dry_run_root = root / "dry_run_snapshots"
    for offset in range(40):
        path = dry_run_root / (start + timedelta(days=offset)).isoformat()
        path.mkdir(parents=True, exist_ok=True)
    dry_run = operations.maintain_snapshots(
        str(dry_run_root), state_update_executed=False, daily_days=30, monthly_months=12
    )
    assert dry_run["snapshot_count_removed"] == 0
    assert len(list(dry_run_root.iterdir())) == 40

    log_path = root / "run.log"
    log_path.write_text("line1\nline2\nfailure detail\n", encoding="utf-8")
    notification_path = root / "notification.json"
    for key in ("EMAIL_FROM", "EMAIL_TO", "EMAIL_APP_PASSWORD"):
        os.environ.pop(key, None)
    notification = operations.notify_failure(
        "report", str(log_path), str(notification_path), "https://example.test/run/123", "123"
    )
    assert notification["status"] == "SKIPPED_NO_CREDENTIALS"
    assert "failure detail" in notification["body"]
    assert notification_path.exists()

print("operational controls validation passed")
