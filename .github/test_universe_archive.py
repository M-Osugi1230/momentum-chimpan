from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import historical_backfill
import universe_archive


def members(version: int = 1):
    base = [
        historical_backfill.UniverseMember("1001", "Alpha", "Prime", "電気機器"),
        historical_backfill.UniverseMember("2001", "Beta", "Prime", "銀行業"),
        historical_backfill.UniverseMember("3001", "Gamma", "Standard", "機械"),
    ]
    if version >= 2:
        base[1] = historical_backfill.UniverseMember("2001", "Beta New", "Prime", "保険業")
        base.append(historical_backfill.UniverseMember("4001", "Delta", "Growth", "情報・通信業"))
    return base


def write_report(path: Path, report_date: str, state_update: str, scanned: int, universe: int):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([{
            "実行日": report_date,
            "状態更新実行": state_update,
            "アプリ版": "test-app",
            "実スキャン対象銘柄数": scanned,
            "通常株ユニバース数": universe,
        }]).to_excel(writer, sheet_name="Summary", index=False)


canonical = universe_archive.canonical_universe(members())
assert list(canonical.columns) == ["code", "name", "market", "sector33"]
assert list(canonical["code"]) == ["1001", "2001", "3001"]
assert len(universe_archive.universe_sha256(canonical)) == 64
comparison = universe_archive.compare_universes(
    canonical,
    universe_archive.canonical_universe(members(2)),
)
assert comparison == {
    "additions": 1,
    "removals": 0,
    "sector_changes": 1,
    "name_changes": 1,
}

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    snapshot_root = root / "snapshots"
    catalog_path = root / "catalog.csv"
    cache_path = root / "jpx.csv"
    config_path = root / "config.yaml"
    audit_path = root / "audit.json"
    cache_path.write_text("cache\n", encoding="utf-8")
    config_path.write_text("market: {}\n", encoding="utf-8")

    current_members = members()
    original_config = universe_archive.historical_backfill.load_config
    original_universe = universe_archive.historical_backfill.load_current_universe
    universe_archive.historical_backfill.load_config = lambda path: {"market": {}}
    universe_archive.historical_backfill.load_current_universe = lambda cache, config: current_members
    try:
        report1 = root / "report1.xlsx"
        write_report(report1, "2026-07-13", "YES", 3, 3)
        initial = universe_archive.capture_snapshot(
            str(report1), str(cache_path), str(config_path),
            str(snapshot_root), str(catalog_path), str(audit_path),
        )
        assert initial["captured"] is True
        assert initial["capture_reason"] == "INITIAL"
        snapshot1 = snapshot_root / "2026-07-13.csv"
        assert snapshot1.exists()
        catalog = universe_archive.load_catalog(str(catalog_path))
        assert len(catalog) == 1
        assert catalog.iloc[0]["additions"] == 3
        assert universe_archive.sha256_file(snapshot1) == initial["universe_sha256"]

        report2 = root / "report2.xlsx"
        write_report(report2, "2026-07-14", "YES", 3, 3)
        unchanged = universe_archive.capture_snapshot(
            str(report2), str(cache_path), str(config_path),
            str(snapshot_root), str(catalog_path), str(audit_path),
        )
        assert unchanged["captured"] is False
        assert unchanged["capture_reason"] == "UNCHANGED"
        assert not (snapshot_root / "2026-07-14.csv").exists()
        assert len(universe_archive.load_catalog(str(catalog_path))) == 1

        current_members = members(2)
        report3 = root / "report3.xlsx"
        write_report(report3, "2026-07-20", "YES", 4, 4)
        changed = universe_archive.capture_snapshot(
            str(report3), str(cache_path), str(config_path),
            str(snapshot_root), str(catalog_path), str(audit_path),
        )
        assert changed["captured"] is True
        assert changed["capture_reason"] == "UNIVERSE_CHANGED"
        assert changed["additions"] == 1
        assert changed["sector_changes"] == 1
        assert changed["name_changes"] == 1
        snapshot3 = snapshot_root / "2026-07-20.csv"
        assert snapshot3.exists()

        report4 = root / "report4.xlsx"
        write_report(report4, "2026-08-03", "YES", 4, 4)
        monthly = universe_archive.capture_snapshot(
            str(report4), str(cache_path), str(config_path),
            str(snapshot_root), str(catalog_path), str(audit_path),
        )
        assert monthly["captured"] is True
        assert monthly["capture_reason"] == "NEW_MONTH"
        assert (snapshot_root / "2026-08-03.csv").exists()
        assert len(universe_archive.load_catalog(str(catalog_path))) == 3

        frame, metadata = universe_archive.lookup_snapshot("2026-07-15", str(catalog_path))
        assert metadata["status"] == "OK"
        assert metadata["snapshot_date"] == "2026-07-13"
        assert len(frame) == 3
        frame, metadata = universe_archive.lookup_snapshot("2026-07-25", str(catalog_path))
        assert metadata["snapshot_date"] == "2026-07-20"
        assert len(frame) == 4
        frame, metadata = universe_archive.lookup_snapshot("2026-01-01", str(catalog_path))
        assert metadata["status"] == "NO_SNAPSHOT"
        assert frame.empty

        validation = universe_archive.validate_archive(str(catalog_path))
        assert len(validation) == 3
        assert set(validation["status"]) == {"PASS"}

        stale_report = root / "stale.xlsx"
        write_report(stale_report, "2026-08-04", "NO", 4, 4)
        stale = universe_archive.capture_snapshot(
            str(stale_report), str(cache_path), str(config_path),
            str(snapshot_root), str(catalog_path), str(audit_path),
        )
        assert stale["capture_reason"] == "SKIPPED_NO_STATE_UPDATE"
        assert not (snapshot_root / "2026-08-04.csv").exists()

        limited_report = root / "limited.xlsx"
        write_report(limited_report, "2026-08-05", "YES", 2, 4)
        limited = universe_archive.capture_snapshot(
            str(limited_report), str(cache_path), str(config_path),
            str(snapshot_root), str(catalog_path), str(audit_path),
        )
        assert limited["capture_reason"] == "SKIPPED_LIMITED_RUN"
        assert not (snapshot_root / "2026-08-05.csv").exists()

        # Existing snapshot files are immutable.
        conflict_report = root / "conflict.xlsx"
        write_report(conflict_report, "2026-08-03", "YES", 3, 3)
        current_members = members()
        try:
            universe_archive.capture_snapshot(
                str(conflict_report), str(cache_path), str(config_path),
                str(snapshot_root), str(catalog_path), str(audit_path),
            )
            raise AssertionError("immutable snapshot conflict should fail")
        except RuntimeError as exc:
            assert "immutable snapshot conflict" in str(exc)

        # Tampering is detected by archive validation and lookup.
        with snapshot3.open("a", encoding="utf-8") as handle:
            handle.write("9999,Tampered,Prime,機械\n")
        validation = universe_archive.validate_archive(str(catalog_path))
        failed = validation[validation["snapshot_date"] == "2026-07-20"].iloc[0]
        assert failed["status"] == "FAIL"
        try:
            universe_archive.lookup_snapshot("2026-07-25", str(catalog_path))
            raise AssertionError("tampered lookup should fail")
        except RuntimeError as exc:
            assert "checksum mismatch" in str(exc)
    finally:
        universe_archive.historical_backfill.load_config = original_config
        universe_archive.historical_backfill.load_current_universe = original_universe

print("point-in-time universe archive validation passed")
