from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import state_recovery


fingerprint = "recovery-test-fingerprint"


def create_snapshot(root: Path, snapshot_date: str, value: int) -> Path:
    directory = root / snapshot_date
    directory.mkdir(parents=True, exist_ok=True)
    for state_name in state_recovery.STATE_FILES:
        if state_name == "ranking_history":
            frame = pd.DataFrame([{
                "date": snapshot_date,
                "rank": 1,
                "code": "1001",
                "score": value,
                "strategy_fingerprint": fingerprint,
                "strategy_app_version": "test-app",
                "strategy_stamp_source": "TEST_GOVERNED_WORKFLOW",
            }])
        else:
            frame = pd.DataFrame([{"date": snapshot_date, "value": value}])
        frame.to_csv(directory / f"{state_name}.csv", index=False)
    return directory


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    snapshot_root = root / "snapshots"
    production_root = root / "production"
    output_root = root / "output"
    fingerprint_path = root / "fingerprint.json"
    fingerprint_path.write_text(json.dumps({
        "strategy_fingerprint": fingerprint,
    }), encoding="utf-8")

    older = create_snapshot(snapshot_root, "2026-07-10", 10)
    newer = create_snapshot(snapshot_root, "2026-07-13", 20)

    for snapshot_date, directory in [("2026-07-10", older), ("2026-07-13", newer)]:
        report = root / f"report-{snapshot_date}.xlsx"
        with pd.ExcelWriter(report, engine="openpyxl") as writer:
            pd.DataFrame([{
                "実行日": snapshot_date,
                "状態更新実行": "YES",
                "アプリ版": "test-app",
            }]).to_excel(writer, sheet_name="Summary", index=False)
        audit = state_recovery.seal_snapshot(
            str(report),
            str(fingerprint_path),
            str(snapshot_root),
            str(root / f"audit-{snapshot_date}.json"),
        )
        assert audit["status"] == "SEALED"
        assert audit["complete"] is True
        manifest = json.loads((directory / "snapshot_manifest.json").read_text(encoding="utf-8"))
        assert manifest["snapshot_schema_version"] == state_recovery.SNAPSHOT_SCHEMA_VERSION
        assert manifest["strategy_fingerprint"] == fingerprint
        assert manifest["state_file_count"] == len(state_recovery.STATE_FILES)
        assert manifest["valid_state_file_count"] == len(state_recovery.STATE_FILES)
        assert manifest["ranking_fingerprint_verified"] is True
        assert manifest["automatic_production_restore"] is False

    valid, manifest, issues = state_recovery.validate_snapshot(newer)
    assert valid is True
    assert not issues
    assert manifest["snapshot_date"] == "2026-07-13"

    # Current production contains different data so every state would be restored.
    for state_name, production_relative in state_recovery.STATE_FILES.items():
        target = production_root / production_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"date": "2026-07-14", "value": 999}]).to_csv(target, index=False)

    selected, catalog = state_recovery.latest_valid_snapshot(str(snapshot_root))
    assert selected == newer
    assert len(catalog) == 2
    assert catalog["valid"].all()

    plan = state_recovery.build_recovery_plan(newer, str(production_root))
    assert len(plan) == len(state_recovery.STATE_FILES)
    assert set(plan["action"]) == {"WOULD_RESTORE"}

    restored = state_recovery.restore_to_sandbox(newer, str(output_root / "sandbox-only"))
    assert len(restored) == len(state_recovery.STATE_FILES)
    assert restored["verified"].all()
    for production_relative in state_recovery.STATE_FILES.values():
        assert (output_root / "sandbox-only" / production_relative).exists()

    drill = state_recovery.run_recovery_drill(
        str(snapshot_root),
        str(output_root / "drill"),
        str(production_root),
        strict=True,
    )
    assert drill["status"] == "PASS"
    assert drill["selected_snapshot_date"] == "2026-07-13"
    assert drill["verified_state_file_count"] == len(state_recovery.STATE_FILES)
    assert drill["production_state_mutated"] is False
    assert drill["automatic_production_restore"] is False
    assert (output_root / "drill" / "recovery_drill.xlsx").exists()

    # Corrupt the newer snapshot. Discovery must fall back to the older sealed point.
    with (newer / "market_temperature.csv").open("a", encoding="utf-8") as handle:
        handle.write("corruption\n")
    valid, _, issues = state_recovery.validate_snapshot(newer)
    assert valid is False
    assert any("checksum mismatch" in issue or "shape mismatch" in issue for issue in issues)
    selected, catalog = state_recovery.latest_valid_snapshot(str(snapshot_root))
    assert selected == older
    assert int((catalog["valid"] == True).sum()) == 1

    # A stale/holiday report does not create or seal a new snapshot directory.
    stale_report = root / "stale-report.xlsx"
    with pd.ExcelWriter(stale_report, engine="openpyxl") as writer:
        pd.DataFrame([{
            "実行日": "2026-07-14",
            "状態更新実行": "NO",
            "アプリ版": "test-app",
        }]).to_excel(writer, sheet_name="Summary", index=False)
    stale_audit = state_recovery.seal_snapshot(
        str(stale_report),
        str(fingerprint_path),
        str(snapshot_root),
        str(root / "stale-audit.json"),
    )
    assert stale_audit["status"] == "SKIPPED_NO_STATE_UPDATE"
    assert not (snapshot_root / "2026-07-14").exists()

    empty_drill = state_recovery.run_recovery_drill(
        str(root / "missing-snapshots"),
        str(output_root / "empty-drill"),
        str(production_root),
        strict=False,
    )
    assert empty_drill["status"] == "NO_VALID_SNAPSHOT"
    assert empty_drill["production_state_mutated"] is False

print("sealed state recovery validation passed")
