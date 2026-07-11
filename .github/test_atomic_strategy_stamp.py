from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import evidence_provenance
import main


fingerprint = "abc123-governed-fingerprint"
frame = pd.DataFrame([
    {"date": "2026-07-13", "rank": 1, "code": "1001", "score": 80},
    {"date": "2026-07-13", "rank": 2, "code": "1002", "score": 75},
])

old_fingerprint = os.environ.get("MOMENTUM_STRATEGY_FINGERPRINT")
old_source = os.environ.get("MOMENTUM_STRATEGY_STAMP_SOURCE")
os.environ["MOMENTUM_STRATEGY_FINGERPRINT"] = fingerprint
os.environ["MOMENTUM_STRATEGY_STAMP_SOURCE"] = "TEST_GOVERNED_WORKFLOW"
try:
    stamped = main.attach_strategy_provenance(frame)
finally:
    if old_fingerprint is None:
        os.environ.pop("MOMENTUM_STRATEGY_FINGERPRINT", None)
    else:
        os.environ["MOMENTUM_STRATEGY_FINGERPRINT"] = old_fingerprint
    if old_source is None:
        os.environ.pop("MOMENTUM_STRATEGY_STAMP_SOURCE", None)
    else:
        os.environ["MOMENTUM_STRATEGY_STAMP_SOURCE"] = old_source

assert set(stamped["strategy_fingerprint"]) == {fingerprint}
assert set(stamped["strategy_app_version"]) == {main.APP_VERSION}
assert set(stamped["strategy_stamp_source"]) == {"TEST_GOVERNED_WORKFLOW"}
assert "strategy_fingerprint" not in frame.columns

unstamped = main.attach_strategy_provenance(frame)
assert "strategy_fingerprint" not in unstamped.columns

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    ranking_path = root / "momentum_daily_ranking.csv"
    report_path = root / "daily_report.xlsx"
    fingerprint_path = root / "strategy_fingerprint.json"
    audit_path = root / "evidence_stamp_audit.json"
    snapshot_root = root / "state_snapshots"
    snapshot_path = snapshot_root / "2026-07-13" / "ranking_history.csv"
    snapshot_path.parent.mkdir(parents=True)

    stamped.to_csv(ranking_path, index=False)
    stamped.to_csv(snapshot_path, index=False)
    before_ranking_sha = evidence_provenance.sha256_file(ranking_path)
    before_snapshot_sha = evidence_provenance.sha256_file(snapshot_path)
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        pd.DataFrame([{
            "実行日": "2026-07-13",
            "状態更新実行": "YES",
            "アプリ版": main.APP_VERSION,
        }]).to_excel(writer, sheet_name="Summary", index=False)
    fingerprint_path.write_text(json.dumps({
        "strategy_fingerprint": fingerprint,
    }), encoding="utf-8")

    original_current = evidence_provenance.current_strategy_fingerprint
    evidence_provenance.current_strategy_fingerprint = lambda: fingerprint
    try:
        audit = evidence_provenance.stamp_live_ranking_history(
            str(ranking_path),
            str(report_path),
            str(fingerprint_path),
            str(audit_path),
            str(snapshot_root),
        )
    finally:
        evidence_provenance.current_strategy_fingerprint = original_current

    assert audit["stamped_rows"] == 2
    assert audit["already_stamped_rows"] == 2
    assert audit["snapshot_verified"] is True
    assert audit["snapshot_stamped_rows"] == 2
    assert evidence_provenance.sha256_file(ranking_path) == before_ranking_sha
    assert evidence_provenance.sha256_file(snapshot_path) == before_snapshot_sha
    assert audit_path.exists()

    mismatched = stamped.copy()
    mismatched.loc[0, "strategy_fingerprint"] = "different"
    mismatched.to_csv(ranking_path, index=False)
    evidence_provenance.current_strategy_fingerprint = lambda: fingerprint
    try:
        try:
            evidence_provenance.stamp_live_ranking_history(
                str(ranking_path),
                str(report_path),
                str(fingerprint_path),
                str(audit_path),
                str(snapshot_root),
            )
            raise AssertionError("mismatched existing fingerprint should fail")
        except ValueError as exc:
            assert "different strategy fingerprint" in str(exc)
    finally:
        evidence_provenance.current_strategy_fingerprint = original_current

    stale_report = root / "stale_report.xlsx"
    with pd.ExcelWriter(stale_report, engine="openpyxl") as writer:
        pd.DataFrame([{
            "実行日": "2026-07-14",
            "状態更新実行": "NO",
            "アプリ版": main.APP_VERSION,
        }]).to_excel(writer, sheet_name="Summary", index=False)
    before = evidence_provenance.sha256_file(ranking_path)
    evidence_provenance.current_strategy_fingerprint = lambda: fingerprint
    try:
        stale_audit = evidence_provenance.stamp_live_ranking_history(
            str(ranking_path),
            str(stale_report),
            str(fingerprint_path),
            str(root / "stale_audit.json"),
            str(snapshot_root),
        )
    finally:
        evidence_provenance.current_strategy_fingerprint = original_current
    assert stale_audit["state_update_executed"] is False
    assert stale_audit["stamped_rows"] == 0
    assert stale_audit["snapshot_verified"] is False
    assert evidence_provenance.sha256_file(ranking_path) == before

print("atomic strategy stamp validation passed")
