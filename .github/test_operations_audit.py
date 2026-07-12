from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import operations_audit as audit


FINGERPRINT = "a" * 64


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_success_artifact(root: Path, report_date: str = "2026-07-13") -> Path:
    artifact = root / "momentum-operations-123"
    output = artifact / "output"
    data = artifact / "data"
    output.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Summary"
    headers = [
        "実行日",
        "アプリ版",
        "実行モード",
        "通常株ユニバース数",
        "実スキャン対象銘柄数",
        "取得成功数",
        "取得失敗数",
        "市場データ鮮度",
        "最新株価日",
        "Run Health",
        "リリース判定",
    ]
    values = [
        report_date,
        "test-app",
        "RESEARCH_AND_PAPER_ONLY",
        3800,
        100,
        99,
        1,
        "FRESH",
        report_date,
        "PASS",
        "RESEARCH",
    ]
    sheet.append(headers)
    sheet.append(values)
    workbook.save(output / "daily_report.xlsx")

    write_json(data / "operations_heartbeat.json", {
        "workflow_run_id": "123",
        "workflow_status": "SUCCESS",
        "app_version": "test-app",
        "report_date": report_date,
        "market_data_freshness": "FRESH",
        "latest_price_date": report_date,
        "current_day_price_ratio": 0.99,
        "state_update_executed": True,
        "run_health": "PASS",
        "release_status": "RESEARCH",
        "p0_alerts": 0,
        "p1_alerts": 0,
        "execution_mode": "RESEARCH_AND_PAPER_ONLY",
        "research_only": True,
    })
    write_json(data / "strategy_fingerprint.json", {
        "strategy_fingerprint": FINGERPRINT,
    })
    write_json(output / "evidence_stamp_audit.json", {
        "report_date": report_date,
        "state_update_executed": True,
        "strategy_fingerprint": FINGERPRINT,
        "stamped_rows": 2,
        "snapshot_verified": True,
        "research_only": True,
    })
    write_json(output / "recovery_snapshot_audit.json", {
        "snapshot_date": report_date,
        "state_update_executed": True,
        "status": "SEALED",
        "complete": True,
        "research_only": True,
    })
    write_json(output / "state_maintenance.json", {
        "state_update_executed": True,
        "validation_status": "PASS",
        "validation_failures": 0,
    })

    pd.DataFrame([
        {"date": report_date, "rank": 1, "code": "1111", "score": 90, "strategy_fingerprint": FINGERPRINT},
        {"date": report_date, "rank": 2, "code": "2222", "score": 80, "strategy_fingerprint": FINGERPRINT},
    ]).to_csv(data / "momentum_daily_ranking.csv", index=False)
    pd.DataFrame([
        {"date": report_date, "ytd_high_count": 10},
    ]).to_csv(data / "market_temperature.csv", index=False)
    (output / "run.log").write_text("success\n", encoding="utf-8")
    return artifact


committed_history = audit.load_history(ROOT / audit.DEFAULT_HISTORY)
committed_status = json.loads((ROOT / audit.DEFAULT_STATUS).read_text(encoding="utf-8"))
assert committed_history.empty
assert audit.validate_status(committed_status) == []
assert committed_status["audit_state"] == "ACCUMULATING"
assert committed_status["market_session_count"] == 0
assert committed_status["automatic_strategy_change"] is False
assert committed_status["automatic_weight_change"] is False

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    artifact = build_success_artifact(root)
    record = audit.build_record(
        artifact,
        workflow_run_id="123",
        workflow_run_url="https://example.test/runs/123",
        upstream_conclusion="success",
        upstream_event="schedule",
        head_sha="b" * 40,
        head_branch="main",
        created_at_utc="2026-07-13T07:45:00Z",
        updated_at_utc="2026-07-13T08:00:00Z",
    )
    assert record["audit_status"] == "PASS", record["audit_failures"]
    assert record["report_date"] == "2026-07-13"
    assert record["retrieval_coverage"] == 0.99
    assert record["ranking_duplicate_count"] == 0
    assert record["market_temperature_duplicate_count"] == 0
    assert record["ranking_fingerprint_matches"] is True
    assert record["recovery_complete"] is True
    assert record["maintenance_status"] == "PASS"
    assert record["full_state_update"] is True

    history = audit.append_record(audit.empty_history(), record)
    assert len(history) == 1
    history = audit.append_record(history, record)
    assert len(history) == 1, "same workflow run must be idempotent"

    status = audit.build_status(history)
    assert audit.validate_status(status) == []
    assert status["audit_state"] == "ACCUMULATING"
    assert status["market_session_count"] == 1
    assert status["remaining_market_sessions"] == 9

    ten = audit.empty_history()
    for day in range(1, 11):
        row = copy.deepcopy(record)
        row["workflow_run_id"] = str(1000 + day)
        row["report_date"] = f"2026-07-{day + 12:02d}"
        row["intended_date_jst"] = row["report_date"]
        row["created_at_utc"] = f"2026-07-{day + 12:02d}T07:45:00Z"
        row["updated_at_utc"] = f"2026-07-{day + 12:02d}T08:00:00Z"
        ten = audit.append_record(ten, row)
    passed = audit.build_status(ten)
    assert audit.validate_status(passed) == []
    assert passed["audit_state"] == "PASS"
    assert passed["market_session_count"] == 10
    assert passed["minimum_retrieval_coverage"] == 0.99

    weak = ten.copy()
    weak.loc[weak.index[-1], "retrieval_coverage"] = 0.97
    review = audit.build_status(weak)
    assert review["audit_state"] == "REVIEW_REQUIRED"

    tampered = copy.deepcopy(passed)
    tampered["automatic_weight_change"] = True
    errors = audit.validate_status(tampered)
    assert any("automatic_weight_change must be false" in error for error in errors)
    assert any("status_sha256 mismatch" in error for error in errors)

    empty_artifact = root / "empty"
    empty_artifact.mkdir()
    failure = audit.build_record(
        empty_artifact,
        workflow_run_id="999",
        workflow_run_url="https://example.test/runs/999",
        upstream_conclusion="failure",
        upstream_event="schedule",
        head_sha="c" * 40,
        head_branch="main",
        created_at_utc="2026-07-14T07:45:00Z",
        updated_at_utc="2026-07-14T07:46:00Z",
    )
    assert failure["audit_status"] == "FAIL"
    assert "upstream conclusion=failure" in failure["audit_failures"]
    assert "daily report missing" in failure["audit_failures"]
    assert failure["full_state_update"] is False

workflow_path = ROOT / ".github" / "workflows" / "daily-operations-audit.yml"
workflow_text = workflow_path.read_text(encoding="utf-8")
assert "Daily Momentum Report" in workflow_text
assert "actions: read" in workflow_text
assert "contents: write" in workflow_text
assert "research/operations/daily_production_audit.csv" in workflow_text
assert "research/operations/daily_production_audit_status.json" in workflow_text
assert "git add --" in workflow_text
assert "data/momentum_daily_ranking.csv" not in workflow_text.split("git add --", 1)[1]
assert "config.yaml" not in workflow_text.split("git add --", 1)[1]
assert ("EMAIL_" + "APP_PASSWORD") not in workflow_text

print("daily production audit validation passed")
