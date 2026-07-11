"""Capture immutable current-listed JPX universe snapshots for future PIT research.

Snapshots begin when this feature is deployed; they do not retroactively remove
survivorship bias from older backfills. A new snapshot is written on the first
full, fresh run of a month or whenever membership/metadata changes. Existing
snapshot files are immutable and production strategy rules are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import historical_backfill
import main

ARCHIVE_VERSION = "2026-07-11-universe-archive-v1"
DEFAULT_ROOT = "data/universe_snapshots"
DEFAULT_CATALOG = "data/universe_snapshot_catalog.csv"
CATALOG_COLUMNS = [
    "snapshot_date",
    "snapshot_path",
    "archive_version",
    "app_version",
    "universe_count",
    "universe_sha256",
    "source_cache_sha256",
    "previous_snapshot_date",
    "additions",
    "removals",
    "sector_changes",
    "name_changes",
    "capture_reason",
    "created_at_utc",
]


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_universe(members: list[historical_backfill.UniverseMember]) -> pd.DataFrame:
    rows = [
        {
            "code": main.normalize_code(member.code),
            "name": str(member.name or "").strip(),
            "market": str(member.market or "").strip(),
            "sector33": main.normalize_sector33(member.sector33),
        }
        for member in members
        if main.normalize_code(member.code)
    ]
    frame = pd.DataFrame(rows, columns=["code", "name", "market", "sector33"])
    if frame.empty:
        return frame
    frame = frame.drop_duplicates("code", keep="last").sort_values("code").reset_index(drop=True)
    return frame


def canonical_bytes(frame: pd.DataFrame) -> bytes:
    if frame.empty:
        return b"code,name,market,sector33\n"
    return frame[["code", "name", "market", "sector33"]].to_csv(index=False, lineterminator="\n").encode("utf-8")


def universe_sha256(frame: pd.DataFrame) -> str:
    return hashlib.sha256(canonical_bytes(frame)).hexdigest()


def atomic_write_csv(frame: pd.DataFrame, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    frame.to_csv(temporary, index=False, lineterminator="\n")
    temporary.replace(target)


def atomic_write_json(payload: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def report_context(report_path: str) -> dict[str, Any]:
    frame = pd.read_excel(report_path, sheet_name="Summary")
    if frame.empty:
        raise ValueError("Summary sheet is empty")
    row = frame.iloc[0]
    report_date = str(row.get("実行日", "")).strip()
    if not report_date:
        raise ValueError("report date is empty")
    state_update = str(row.get("状態更新実行", "NO")).strip().upper() == "YES"
    scanned = int(pd.to_numeric(pd.Series([row.get("実スキャン対象銘柄数")]), errors="coerce").fillna(0).iloc[0])
    universe = int(pd.to_numeric(pd.Series([row.get("通常株ユニバース数")]), errors="coerce").fillna(0).iloc[0])
    return {
        "report_date": report_date,
        "state_update": state_update,
        "app_version": str(row.get("アプリ版", main.APP_VERSION)).strip(),
        "scanned_count": scanned,
        "universe_count": universe,
        "full_run": bool(universe > 0 and scanned == universe),
    }


def load_catalog(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return pd.DataFrame(columns=CATALOG_COLUMNS)
    frame = pd.read_csv(target, dtype={"snapshot_date": str})
    for column in CATALOG_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[CATALOG_COLUMNS].drop_duplicates("snapshot_date", keep="last").sort_values("snapshot_date")


def load_snapshot(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(str(path))
    frame = pd.read_csv(target, dtype={"code": str})
    required = {"code", "name", "market", "sector33"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"universe snapshot missing columns: {missing}")
    frame["code"] = frame["code"].map(main.normalize_code)
    frame["name"] = frame["name"].fillna("").astype(str).str.strip()
    frame["market"] = frame["market"].fillna("").astype(str).str.strip()
    frame["sector33"] = frame["sector33"].map(main.normalize_sector33)
    if frame["code"].duplicated().any():
        raise ValueError(f"universe snapshot has duplicate codes: {path}")
    return frame.sort_values("code").reset_index(drop=True)


def latest_catalog_row(catalog: pd.DataFrame, before_or_on: str | None = None) -> pd.Series | None:
    if catalog.empty:
        return None
    work = catalog.copy()
    work["date_value"] = pd.to_datetime(work["snapshot_date"], errors="coerce")
    work = work.dropna(subset=["date_value"])
    if before_or_on:
        limit = pd.Timestamp(before_or_on)
        work = work[work["date_value"] <= limit]
    if work.empty:
        return None
    return work.sort_values("date_value").iloc[-1]


def compare_universes(previous: pd.DataFrame, current: pd.DataFrame) -> dict[str, int]:
    previous_index = previous.set_index("code") if not previous.empty else pd.DataFrame()
    current_index = current.set_index("code") if not current.empty else pd.DataFrame()
    previous_codes = set(previous["code"]) if not previous.empty else set()
    current_codes = set(current["code"]) if not current.empty else set()
    common = previous_codes & current_codes
    sector_changes = sum(
        str(previous_index.loc[code, "sector33"]) != str(current_index.loc[code, "sector33"])
        for code in common
    )
    name_changes = sum(
        str(previous_index.loc[code, "name"]) != str(current_index.loc[code, "name"])
        for code in common
    )
    return {
        "additions": len(current_codes - previous_codes),
        "removals": len(previous_codes - current_codes),
        "sector_changes": int(sector_changes),
        "name_changes": int(name_changes),
    }


def capture_reason(
    report_date: str,
    current_hash: str,
    previous_row: pd.Series | None,
) -> str:
    if previous_row is None:
        return "INITIAL"
    previous_date = str(previous_row.get("snapshot_date", ""))
    previous_hash = str(previous_row.get("universe_sha256", ""))
    if pd.Timestamp(previous_date).to_period("M") != pd.Timestamp(report_date).to_period("M"):
        return "NEW_MONTH"
    if previous_hash != current_hash:
        return "UNIVERSE_CHANGED"
    return "UNCHANGED"


def capture_snapshot(
    report_path: str,
    cache_path: str,
    config_path: str,
    snapshot_root: str = DEFAULT_ROOT,
    catalog_path: str = DEFAULT_CATALOG,
    audit_path: str = "output/universe_archive_audit.json",
) -> dict[str, Any]:
    context = report_context(report_path)
    audit: dict[str, Any] = {
        "archive_version": ARCHIVE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **context,
        "captured": False,
        "capture_reason": "",
        "snapshot_path": "",
        "research_only": True,
    }
    if not context["state_update"]:
        audit["capture_reason"] = "SKIPPED_NO_STATE_UPDATE"
        atomic_write_json(audit, audit_path)
        return audit
    if not context["full_run"]:
        audit["capture_reason"] = "SKIPPED_LIMITED_RUN"
        atomic_write_json(audit, audit_path)
        return audit

    config = historical_backfill.load_config(config_path)
    members = historical_backfill.load_current_universe(cache_path, config)
    universe = canonical_universe(members)
    if universe.empty:
        raise RuntimeError("current universe is empty")
    if len(universe) != context["universe_count"]:
        raise RuntimeError(
            f"universe count mismatch: report={context['universe_count']} cache={len(universe)}"
        )
    current_hash = universe_sha256(universe)
    catalog = load_catalog(catalog_path)
    previous_row = latest_catalog_row(catalog, context["report_date"])
    reason = capture_reason(context["report_date"], current_hash, previous_row)
    audit.update({
        "capture_reason": reason,
        "universe_count": len(universe),
        "universe_sha256": current_hash,
        "source_cache_sha256": sha256_file(cache_path),
    })
    if reason == "UNCHANGED":
        audit["previous_snapshot_date"] = str(previous_row.get("snapshot_date", "")) if previous_row is not None else ""
        audit["snapshot_path"] = str(previous_row.get("snapshot_path", "")) if previous_row is not None else ""
        atomic_write_json(audit, audit_path)
        return audit

    root = Path(snapshot_root)
    root.mkdir(parents=True, exist_ok=True)
    snapshot_path = root / f"{context['report_date']}.csv"
    if snapshot_path.exists():
        existing = load_snapshot(snapshot_path)
        if universe_sha256(existing) != current_hash:
            raise RuntimeError(f"immutable snapshot conflict: {snapshot_path}")
    else:
        atomic_write_csv(universe, snapshot_path)
    if sha256_file(snapshot_path) != current_hash:
        # The canonical file bytes are intentionally identical to canonical_bytes.
        raise RuntimeError(f"snapshot hash mismatch after write: {snapshot_path}")

    previous_snapshot = pd.DataFrame(columns=universe.columns)
    previous_date = ""
    if previous_row is not None:
        previous_date = str(previous_row.get("snapshot_date", ""))
        previous_path = str(previous_row.get("snapshot_path", ""))
        if previous_path:
            previous_snapshot = load_snapshot(previous_path)
    changes = compare_universes(previous_snapshot, universe)
    row = {
        "snapshot_date": context["report_date"],
        "snapshot_path": str(snapshot_path),
        "archive_version": ARCHIVE_VERSION,
        "app_version": context["app_version"],
        "universe_count": len(universe),
        "universe_sha256": current_hash,
        "source_cache_sha256": sha256_file(cache_path),
        "previous_snapshot_date": previous_date,
        **changes,
        "capture_reason": reason,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    catalog = pd.concat([catalog, pd.DataFrame([row])], ignore_index=True)
    catalog = catalog.drop_duplicates("snapshot_date", keep="last").sort_values("snapshot_date")
    atomic_write_csv(catalog[CATALOG_COLUMNS], catalog_path)
    audit.update({
        "captured": True,
        "snapshot_path": str(snapshot_path),
        "previous_snapshot_date": previous_date,
        **changes,
    })
    atomic_write_json(audit, audit_path)
    return audit


def lookup_snapshot(
    as_of_date: str,
    catalog_path: str = DEFAULT_CATALOG,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    catalog = load_catalog(catalog_path)
    row = latest_catalog_row(catalog, as_of_date)
    if row is None:
        return pd.DataFrame(columns=["code", "name", "market", "sector33"]), {
            "as_of_date": as_of_date,
            "status": "NO_SNAPSHOT",
            "snapshot_date": "",
            "snapshot_path": "",
        }
    snapshot_path = str(row.get("snapshot_path", ""))
    frame = load_snapshot(snapshot_path)
    expected_hash = str(row.get("universe_sha256", ""))
    actual_hash = universe_sha256(frame)
    if actual_hash != expected_hash or sha256_file(snapshot_path) != expected_hash:
        raise RuntimeError(f"universe snapshot checksum mismatch: {snapshot_path}")
    return frame, {
        "as_of_date": as_of_date,
        "status": "OK",
        "snapshot_date": str(row.get("snapshot_date", "")),
        "snapshot_path": snapshot_path,
        "universe_count": len(frame),
        "universe_sha256": actual_hash,
    }


def validate_archive(catalog_path: str = DEFAULT_CATALOG) -> pd.DataFrame:
    catalog = load_catalog(catalog_path)
    rows: list[dict[str, Any]] = []
    previous_date: pd.Timestamp | None = None
    for _, row in catalog.iterrows():
        snapshot_date = pd.to_datetime(row.get("snapshot_date"), errors="coerce")
        path = str(row.get("snapshot_path", ""))
        status = "PASS"
        detail = ""
        try:
            frame = load_snapshot(path)
            expected = str(row.get("universe_sha256", ""))
            actual = universe_sha256(frame)
            if expected != actual or sha256_file(path) != expected:
                raise RuntimeError("checksum mismatch")
            if int(row.get("universe_count", -1)) != len(frame):
                raise RuntimeError("universe count mismatch")
            if previous_date is not None and snapshot_date <= previous_date:
                raise RuntimeError("snapshot dates are not strictly increasing")
        except Exception as exc:
            status = "FAIL"
            detail = str(exc)
        rows.append({
            "snapshot_date": str(row.get("snapshot_date", "")),
            "snapshot_path": path,
            "status": status,
            "detail": detail or "immutable snapshot verified",
        })
        if pd.notna(snapshot_date):
            previous_date = snapshot_date
    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture and query point-in-time universe snapshots")
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture")
    capture.add_argument("--report", default="output/daily_report.xlsx")
    capture.add_argument("--cache", default="data/jpx_list_cache.csv")
    capture.add_argument("--config", default="config.yaml")
    capture.add_argument("--snapshot-root", default=DEFAULT_ROOT)
    capture.add_argument("--catalog", default=DEFAULT_CATALOG)
    capture.add_argument("--audit", default="output/universe_archive_audit.json")

    lookup = sub.add_parser("lookup")
    lookup.add_argument("--as-of-date", required=True)
    lookup.add_argument("--catalog", default=DEFAULT_CATALOG)
    lookup.add_argument("--output", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--catalog", default=DEFAULT_CATALOG)
    validate.add_argument("--output", default="output/universe_archive_validation.csv")
    validate.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "capture":
        result = capture_snapshot(
            args.report,
            args.cache,
            args.config,
            args.snapshot_root,
            args.catalog,
            args.audit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "lookup":
        frame, metadata = lookup_snapshot(args.as_of_date, args.catalog)
        atomic_write_csv(frame, args.output)
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    validation = validate_archive(args.catalog)
    atomic_write_csv(validation, args.output)
    if args.strict and (validation.get("status", pd.Series(dtype=str)) == "FAIL").any():
        raise RuntimeError("universe archive validation failed")
    print(validation.to_json(orient="records", force_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
