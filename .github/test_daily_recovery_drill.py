from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import daily_recovery_drill as daily_drill
import state_recovery

FINGERPRINT = "a" * 64


def create_snapshot(root: Path, snapshot_date: str) -> Path:
    directory = root / snapshot_date
    directory.mkdir(parents=True, exist_ok=True)
    for index, (state_name, _production_path) in enumerate(
        state_recovery.STATE_FILES.items(), start=1
    ):
        if state_name == "ranking_history":
            frame = pd.DataFrame([{
                "date": snapshot_date,
                "rank": 1,
                "code": "1001",
                "score": 80.0,
                "strategy_fingerprint": FINGERPRINT,
                "strategy_app_version": "test-app",
                "strategy_stamp_source": "TEST_GOVERNED_WORKFLOW",
            }])
        else:
            frame = pd.DataFrame([{"date": snapshot_date, "value": index}])
        frame.to_csv(directory / f"{state_name}.csv", index=False)
    return directory


def create_report(path: Path, report_date: str, state_update: bool) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([{
            "実行日": report_date,
            "状態更新実行": "YES" if state_update else "NO",
            "アプリ版": "test-app",
        }]).to_excel(writer, sheet_name="Summary", index=False)


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot_root = root / "snapshots"
        production_root = root / "production"
        output_root = root / "output"
        fingerprint_path = root / "fingerprint.json"
        fingerprint_path.write_text(
            json.dumps({"strategy_fingerprint": FINGERPRINT}), encoding="utf-8"
        )

        snapshot_date = "2026-07-13"
        snapshot_dir = create_snapshot(snapshot_root, snapshot_date)
        report = root / "report.xlsx"
        audit_path = root / "recovery_snapshot_audit.json"
        create_report(report, snapshot_date, True)
        audit = state_recovery.seal_snapshot(
            str(report),
            str(fingerprint_path),
            str(snapshot_root),
            str(audit_path),
        )
        assert audit["status"] == "SEALED"

        for index, (_state_name, relative_path) in enumerate(
            state_recovery.STATE_FILES.items(), start=1
        ):
            target = production_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([{"date": "2026-07-14", "value": index * 100}]).to_csv(
                target, index=False
            )
        before = daily_drill.production_hashes(str(production_root))

        drill = daily_drill.run_exact_drill(
            audit_path=str(audit_path),
            snapshot_root=str(snapshot_root),
            output_dir=str(output_root / "pass"),
            production_root=str(production_root),
        )
        assert daily_drill.validate_manifest(drill) == []
        assert drill["status"] == "PASS"
        assert drill["operational_gate_passed"] is True
        assert drill["evidence_eligible"] is True
        assert drill["expected_snapshot_date"] == snapshot_date
        assert drill["selected_snapshot_date"] == snapshot_date
        assert drill["expected_manifest_sha256"] == drill["selected_manifest_sha256"]
        assert drill["expected_snapshot_match"] is True
        assert drill["verified_state_file_count"] == len(state_recovery.STATE_FILES)
        assert drill["production_state_unchanged"] is True
        assert drill["production_state_mutated"] is False
        assert drill["automatic_production_restore"] is False
        assert daily_drill.production_hashes(str(production_root)) == before
        assert (output_root / "pass" / "recovery_drill_manifest.json").is_file()
        assert (output_root / "pass" / "recovery_drill.xlsx").is_file()
        assert (output_root / "pass" / "recovery_snapshot_catalog.csv").is_file()
        assert (output_root / "pass" / "recovery_plan.csv").is_file()
        assert (
            output_root / "pass" / "recovery_restore_verification.csv"
        ).is_file()
        for relative_path in state_recovery.STATE_FILES.values():
            assert (output_root / "pass" / "sandbox" / relative_path).is_file()

        wrong_audit = dict(audit)
        wrong_audit["snapshot_manifest_sha256"] = "0a" * 32
        wrong_audit_path = root / "wrong-audit.json"
        wrong_audit_path.write_text(json.dumps(wrong_audit), encoding="utf-8")
        failed = daily_drill.run_exact_drill(
            audit_path=str(wrong_audit_path),
            snapshot_root=str(snapshot_root),
            output_dir=str(output_root / "fail"),
            production_root=str(production_root),
        )
        assert failed["status"] == "FAIL"
        assert failed["operational_gate_passed"] is False
        assert failed["production_state_mutated"] is False
        assert any("manifest SHA-256" in issue for issue in failed["issues"])
        assert daily_drill.validate_manifest(failed) == []

        stale_report = root / "stale.xlsx"
        stale_audit_path = root / "stale-audit.json"
        create_report(stale_report, "2026-07-14", False)
        stale_audit = state_recovery.seal_snapshot(
            str(stale_report),
            str(fingerprint_path),
            str(snapshot_root),
            str(stale_audit_path),
        )
        assert stale_audit["status"] == "SKIPPED_NO_STATE_UPDATE"
        skipped = daily_drill.run_exact_drill(
            audit_path=str(stale_audit_path),
            snapshot_root=str(snapshot_root),
            output_dir=str(output_root / "skip"),
            production_root=str(production_root),
        )
        assert skipped["status"] == "SKIPPED_NO_STATE_UPDATE"
        assert skipped["operational_gate_passed"] is True
        assert skipped["evidence_eligible"] is False
        assert skipped["state_update_executed"] is False
        assert daily_drill.validate_manifest(skipped) == []

        tampered = copy.deepcopy(drill)
        tampered["verified_state_file_count"] = 0
        issues = daily_drill.validate_manifest(tampered)
        assert any("verified state file count" in issue for issue in issues)
        assert any("status_sha256 mismatch" in issue for issue in issues)

    workflow_path = ROOT / ".github" / "workflows" / "daily.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    steps = workflow["jobs"]["report"]["steps"]
    step_indexes = {
        step["id"]: index
        for index, step in enumerate(steps)
        if step.get("id")
    }
    seal_index = step_indexes["recovery"]
    drill_index = step_indexes["recovery_drill"]
    maintenance_index = step_indexes["maintenance"]
    persist_index = step_indexes["persist"]
    assert seal_index < drill_index < maintenance_index < persist_index

    drill_step = steps[drill_index]
    assert drill_step["id"] == "recovery_drill"
    assert drill_step["continue-on-error"] is True
    script = drill_step["run"]
    assert "python daily_recovery_drill.py run" in script
    assert "--audit output/recovery_snapshot_audit.json" in script
    assert "--snapshot-root data/state_snapshots" in script
    assert "--output-dir output/recovery" in script
    assert "--production-root ." in script
    assert "--strict" in script

    maintenance = steps[maintenance_index]
    assert "steps.recovery_drill.outcome == 'success'" in maintenance["if"]
    persist = steps[persist_index]
    assert "steps.recovery_drill.outcome == 'success'" in persist["if"]
    artifact = next(
        step
        for step in steps
        if step.get("uses") == "actions/upload-artifact@v4"
        and "output/recovery" in step.get("with", {}).get("path", "")
    )
    assert "output/recovery/**" in artifact["with"]["path"]

    print("daily exact recovery drill validation passed")


if __name__ == "__main__":
    main()
