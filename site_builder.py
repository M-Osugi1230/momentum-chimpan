"""Build and validate the static Momentum Chimpan research dashboard.

The site is a presentation layer generated from the exact daily workbook and
persisted research history. It is intentionally static, dependency-free, and
read-only. It never changes scores, ranking, priorities, paper execution, or
production state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

SITE_VERSION = "2026-07-13-rich-dashboard-v1"
DEFAULT_OUTPUT = "output/site"
REQUIRED_FILES = (
    "index.html",
    "404.html",
    "assets/styles.css",
    "assets/app.js",
    "assets/data.js",
    "downloads/daily_report.xlsx",
    "site_manifest.json",
    ".nojekyll",
)
FORBIDDEN_TEXT = (
    "EMAIL_APP_PASSWORD",
    "EMAIL_FROM",
    "EMAIL_TO",
    "smtp.gmail.com",
    "@icloud.com",
    "@gmail.com",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_scalar(value: Any) -> Any:
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
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 10)
    if isinstance(value, (int, bool, str)):
        return value
    return str(value)


def frame_records(frame: pd.DataFrame, columns: list[str] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    work = frame.copy()
    if columns is not None:
        for column in columns:
            if column not in work.columns:
                work[column] = None
        work = work[columns]
    if limit is not None:
        work = work.head(limit)
    return [
        {str(key): clean_scalar(value) for key, value in row.items()}
        for row in work.to_dict(orient="records")
    ]


def read_sheet(workbook: Path, name: str) -> pd.DataFrame:
    try:
        return pd.read_excel(workbook, sheet_name=name)
    except (ValueError, FileNotFoundError):
        return pd.DataFrame()


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().split(".")[0]
    return text.zfill(4) if text.isdigit() else text


def latest_history(
    path: str | Path | None,
    current_codes: set[str],
    maximum_dates: int = 40,
) -> dict[str, list[dict[str, Any]]]:
    target = Path(path) if path else Path()
    if not target.is_file() or not current_codes:
        return {}
    try:
        frame = pd.read_csv(target, dtype={"code": str, "date": str}, low_memory=False)
    except Exception:
        return {}
    required = {"date", "code"}
    if frame.empty or not required.issubset(frame.columns):
        return {}
    frame["code"] = frame["code"].map(normalize_code)
    frame = frame[frame["code"].isin(current_codes)].copy()
    frame["date_sort"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date_sort"])
    dates = sorted(frame["date_sort"].dt.date.unique())[-maximum_dates:]
    frame = frame[frame["date_sort"].dt.date.isin(dates)]
    columns = [
        column
        for column in (
            "date",
            "rank",
            "score",
            "return_5d",
            "return_20d",
            "volume_ratio",
            "relative_strength_score",
            "data_quality_grade",
        )
        if column in frame.columns
    ]
    result: dict[str, list[dict[str, Any]]] = {}
    for code, group in frame.groupby("code"):
        group = group.sort_values("date_sort")
        result[code] = frame_records(group, columns)
    return result


def temperature_history(path: str | Path | None, maximum_dates: int = 90) -> list[dict[str, Any]]:
    target = Path(path) if path else Path()
    if not target.is_file():
        return []
    try:
        frame = pd.read_csv(target, dtype={"date": str})
    except Exception:
        return []
    if frame.empty or "date" not in frame.columns:
        return []
    frame["date_sort"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date_sort"]).sort_values("date_sort").tail(maximum_dates)
    columns = [
        column
        for column in (
            "date",
            "ytd_high_count",
            "top100_avg_score",
            "top100_avg_return_20d",
            "top100_avg_volume_ratio",
            "market_regime",
            "market_regime_score",
            "market_regime_ma20_ratio",
            "market_regime_ma60_ratio",
            "market_regime_overheat_ratio",
        )
        if column in frame.columns
    ]
    return frame_records(frame, columns)


def summary_dict(frame: pd.DataFrame) -> dict[str, Any]:
    if frame is None or frame.empty:
        return {}
    return {str(key): clean_scalar(value) for key, value in frame.iloc[0].to_dict().items()}


def join_priorities(top100: pd.DataFrame, action: pd.DataFrame) -> pd.DataFrame:
    if top100.empty:
        return top100.copy()
    result = top100.copy()
    result["code"] = result["code"].map(normalize_code)
    if action.empty or "code" not in action.columns:
        return result
    priority = action.copy()
    priority["code"] = priority["code"].map(normalize_code)
    columns = [
        column
        for column in (
            "code",
            "research_bucket",
            "daily_action_list",
            "daily_action_rank",
            "action_priority",
            "action_score",
            "why_today",
            "what_changed",
            "risk_summary",
            "next_research_questions",
            "focus_adjustment_reason",
            "expectancy_score",
            "expectancy_confidence",
            "lifecycle_status",
        )
        if column in priority.columns
    ]
    priority = priority[columns].drop_duplicates("code", keep="first")
    overlapping = [column for column in columns if column != "code" and column in result.columns]
    result = result.drop(columns=overlapping, errors="ignore")
    return result.merge(priority, on="code", how="left")


def build_payload(
    workbook_path: str | Path,
    ranking_history_path: str | Path | None = None,
    market_temperature_path: str | Path | None = None,
    site_url: str = "",
) -> dict[str, Any]:
    workbook = Path(workbook_path)
    if not workbook.is_file():
        raise FileNotFoundError(str(workbook))

    sheets = {
        name: read_sheet(workbook, name)
        for name in (
            "Summary",
            "Action Priority",
            "Momentum Top100",
            "New Entries",
            "Rising Fast",
            "Priority Changes",
            "Priority Lifecycle",
            "Relative Strength",
            "RS Lifecycle",
            "Sector Momentum",
            "Sector Rotation",
            "Sector Leaders",
            "Data Quality",
            "Paper Portfolio",
            "Paper Trade Plan",
            "Paper Performance",
            "Signal Performance",
            "Research Evidence",
            "Release Readiness",
            "Operational Alerts",
            "Run Health",
            "Risk Budget",
            "Market Temperature",
        )
    }
    summary = summary_dict(sheets["Summary"])
    action = sheets["Action Priority"].copy()
    if not action.empty and "code" in action.columns:
        action["code"] = action["code"].map(normalize_code)
    top100 = join_priorities(sheets["Momentum Top100"], action)
    codes = set(top100.get("code", pd.Series(dtype=str)).astype(str))

    action_columns = [
        "code", "name", "sector33", "research_bucket", "daily_action_list",
        "daily_action_rank", "action_priority", "action_score", "momentum_rank",
        "momentum_score", "relative_strength_grade", "relative_strength_rank",
        "data_quality_grade", "data_quality_score", "lifecycle_status",
        "expectancy_score", "expectancy_confidence", "return_5d", "return_20d",
        "market_relative_20d", "sector_relative_20d", "volume_ratio", "trading_value",
        "rank_change", "is_new_entry", "is_rising_fast", "is_best_rank",
        "why_today", "what_changed", "risk_summary", "next_research_questions",
        "focus_adjustment_reason", "data_quality_reason_codes", "data_quality_warnings",
    ]
    top_columns = [
        "rank", "code", "name", "sector33", "close", "score", "return_5d",
        "return_20d", "return_60d", "volume_ratio", "trading_value", "ytd_high_flag",
        "ytd_high_streak", "ytd_high_count", "above_ma20", "above_ma60",
        "ma20_deviation", "ma60_deviation", "is_new_entry", "rank_change",
        "is_rising_fast", "is_best_rank", "top30_streak", "relative_strength_score",
        "relative_strength_rank", "relative_strength_grade", "dual_outperformer",
        "relative_strength_lifecycle", "relative_strength_alert", "market_relative_20d",
        "sector_relative_20d", "data_quality_grade", "data_quality_score",
        "data_quality_reason_codes", "data_quality_warnings", "research_bucket",
        "daily_action_list", "daily_action_rank", "action_priority", "action_score",
        "why_today", "what_changed", "risk_summary", "next_research_questions",
        "expectancy_score", "expectancy_confidence", "lifecycle_status",
    ]
    priority_change_columns = [
        "status", "code", "name", "current_rank", "previous_rank", "current_score",
        "previous_score", "current_labels", "previous_labels", "label_changed",
        "exit_reason", "priority_streak_days", "priority_total_days",
        "priority_lifecycle_status", "expectancy_score", "expectancy_confidence",
        "return_20d", "volume_ratio", "trading_value",
    ]
    sector_columns = [
        "sector_rank", "sector33", "sector_momentum_score", "sector_strength",
        "stock_count", "top100_count", "top30_count", "top100_ratio", "avg_score",
        "avg_return_20d", "avg_return_60d", "avg_volume_ratio", "above_ma20_ratio",
        "above_ma60_ratio", "ytd_high_count", "representative_stocks",
        "previous_sector_rank", "sector_rank_change", "sector_score_delta",
        "sector_rotation", "sector_rotation_reason",
    ]
    leader_columns = [
        "overall_leader_rank", "sector33", "sector_rank", "sector_rotation", "code",
        "name", "momentum_rank", "momentum_score", "sector_leader_score",
        "sector_leader_grade", "sector_research_priority", "action_priority",
        "action_score", "expectancy_score", "expectancy_confidence", "return_20d",
        "relative_strength_score", "relative_strength_grade", "volume_ratio",
        "trading_value", "leader_reasons", "leader_cautions",
    ]
    relative_columns = [
        "relative_strength_rank", "rank", "code", "name", "sector33", "score",
        "relative_strength_score", "relative_strength_grade", "dual_outperformer",
        "return_20d", "market_relative_20d", "sector_relative_20d", "return_60d",
        "market_relative_60d", "sector_relative_60d", "relative_strength_reason",
        "trading_value", "volume_ratio",
    ]
    lifecycle_columns = [
        "relative_strength_lifecycle", "relative_strength_alert",
        "relative_strength_trajectory_score", "relative_strength_rank", "rank", "code",
        "name", "sector33", "score", "relative_strength_score",
        "relative_strength_grade", "relative_strength_score_delta",
        "relative_strength_rank_change", "relative_strength_direction",
        "relative_strength_strong_streak", "dual_outperformer_streak",
        "relative_strength_new_high", "relative_strength_lifecycle_reason",
        "trading_value", "volume_ratio",
    ]
    paper_columns = [
        "status", "code", "name", "sector33", "entry_date", "entry_price", "quantity",
        "cost_basis", "current_price", "market_value", "stop_price", "target_price",
        "holding_days", "sector_research_priority", "sector_leader_score",
        "sector_rotation", "unrealized_pnl", "unrealized_return",
    ]

    payload: dict[str, Any] = {
        "site_version": SITE_VERSION,
        "generated_from_workbook_sha256": sha256_file(workbook),
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "site_url": site_url,
        "research_only": True,
        "automatic_score_change": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "summary": summary,
        "actions": frame_records(action, action_columns),
        "top100": frame_records(top100, top_columns),
        "new_entries": frame_records(sheets["New Entries"], top_columns, 100),
        "rising_fast": frame_records(sheets["Rising Fast"], top_columns, 100),
        "priority_changes": frame_records(sheets["Priority Changes"], priority_change_columns),
        "priority_lifecycle": frame_records(sheets["Priority Lifecycle"]),
        "relative_strength": frame_records(sheets["Relative Strength"], relative_columns, 100),
        "relative_strength_lifecycle": frame_records(sheets["RS Lifecycle"], lifecycle_columns, 100),
        "sectors": frame_records(sheets["Sector Momentum"], sector_columns, 33),
        "sector_rotation": frame_records(sheets["Sector Rotation"], None, 33),
        "sector_leaders": frame_records(sheets["Sector Leaders"], leader_columns, 100),
        "paper_portfolio": frame_records(sheets["Paper Portfolio"], paper_columns),
        "paper_plan": frame_records(sheets["Paper Trade Plan"]),
        "paper_performance": frame_records(sheets["Paper Performance"]),
        "signal_performance": frame_records(sheets["Signal Performance"]),
        "research_evidence": frame_records(sheets["Research Evidence"]),
        "release_readiness": frame_records(sheets["Release Readiness"]),
        "operational_alerts": frame_records(sheets["Operational Alerts"]),
        "run_health": frame_records(sheets["Run Health"]),
        "risk_budget": frame_records(sheets["Risk Budget"]),
        "market_temperature": frame_records(sheets["Market Temperature"]),
        "ranking_history": latest_history(ranking_history_path, codes),
        "temperature_history": temperature_history(market_temperature_path),
    }
    payload["payload_sha256"] = canonical_hash(payload)
    return payload


from site_template import INDEX_HTML
from site_styles import STYLES_CSS
from site_script import APP_JS


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def build_site(
    workbook_path: str | Path,
    output_dir: str | Path = DEFAULT_OUTPUT,
    ranking_history_path: str | Path | None = None,
    market_temperature_path: str | Path | None = None,
    site_url: str = "",
) -> dict[str, Any]:
    workbook = Path(workbook_path)
    output = Path(output_dir)
    if output.exists():
        shutil.rmtree(output)
    (output / "assets").mkdir(parents=True, exist_ok=True)
    (output / "downloads").mkdir(parents=True, exist_ok=True)

    payload = build_payload(
        workbook,
        ranking_history_path=ranking_history_path,
        market_temperature_path=market_temperature_path,
        site_url=site_url,
    )
    atomic_write(output / "index.html", INDEX_HTML)
    atomic_write(output / "404.html", INDEX_HTML)
    atomic_write(output / "assets" / "styles.css", STYLES_CSS)
    atomic_write(output / "assets" / "app.js", APP_JS)
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    atomic_write(output / "assets" / "data.js", f"window.MOMENTUM_DASHBOARD={data_json};\n")
    shutil.copy2(workbook, output / "downloads" / "daily_report.xlsx")
    atomic_write(output / ".nojekyll", "")

    files: list[dict[str, Any]] = []
    for path in sorted(p for p in output.rglob("*") if p.is_file() and p.name != "site_manifest.json"):
        files.append({
            "path": path.relative_to(output).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    manifest_core = {
        "site_version": SITE_VERSION,
        "report_date": payload.get("summary", {}).get("実行日", ""),
        "workbook_sha256": payload["generated_from_workbook_sha256"],
        "payload_sha256": payload["payload_sha256"],
        "file_count": len(files),
        "files": files,
        "research_only": True,
        "production_state_mutations": [],
    }
    manifest = {
        **manifest_core,
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "manifest_fingerprint": canonical_hash(manifest_core),
    }
    manifest["status_sha256"] = canonical_hash(manifest)
    atomic_write(output / "site_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    validation = validate_site(output)
    if not validation["passed"]:
        raise ValueError("invalid generated site: " + "; ".join(validation["issues"]))
    return {"payload": payload, "manifest": manifest, "validation": validation}


def validate_site(output_dir: str | Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    output = Path(output_dir)
    issues: list[str] = []
    for relative in REQUIRED_FILES:
        path = output / relative
        if not path.is_file():
            issues.append(f"missing required file: {relative}")
    manifest_path = output / "site_manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as error:
            issues.append(f"invalid site manifest: {error}")
    if manifest:
        supplied_status = manifest.get("status_sha256", "")
        unsigned = dict(manifest)
        unsigned.pop("status_sha256", None)
        if supplied_status != canonical_hash(unsigned):
            issues.append("site manifest status_sha256 mismatch")
        core = dict(unsigned)
        generated_at = core.pop("generated_at_utc", None)
        supplied_fingerprint = core.pop("manifest_fingerprint", "")
        if supplied_fingerprint != canonical_hash(core):
            issues.append("site manifest fingerprint mismatch")
        if generated_at is None:
            issues.append("site manifest generated_at_utc is required")
        if manifest.get("research_only") is not True:
            issues.append("site must remain research_only")
        if manifest.get("production_state_mutations") != []:
            issues.append("site cannot declare production state mutations")
        listed = {entry.get("path"): entry for entry in manifest.get("files", []) if isinstance(entry, dict)}
        for relative, entry in listed.items():
            path = output / str(relative)
            if not path.is_file():
                issues.append(f"manifest file missing: {relative}")
                continue
            if entry.get("sha256") != sha256_file(path):
                issues.append(f"manifest hash mismatch: {relative}")
    data_path = output / "assets" / "data.js"
    if data_path.is_file():
        data_text = data_path.read_text(encoding="utf-8")
        if not data_text.startswith("window.MOMENTUM_DASHBOARD="):
            issues.append("data.js does not expose the expected dashboard payload")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in data_text:
                issues.append(f"forbidden private marker in data.js: {forbidden}")
    for relative in ("index.html", "assets/app.js", "assets/styles.css"):
        path = output / relative
        if path.is_file() and path.stat().st_size < 500:
            issues.append(f"site asset is unexpectedly small: {relative}")
    return {"passed": not issues, "issues": sorted(set(issues)), "manifest": manifest}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or validate Momentum Chimpan static site")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--workbook", required=True)
    build.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    build.add_argument("--ranking-history", default="data/momentum_daily_ranking.csv")
    build.add_argument("--market-temperature", default="data/market_temperature.csv")
    build.add_argument("--site-url", default="")
    validate = commands.add_parser("validate")
    validate.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "build":
        result = build_site(
            workbook_path=args.workbook,
            output_dir=args.output_dir,
            ranking_history_path=args.ranking_history,
            market_temperature_path=args.market_temperature,
            site_url=args.site_url,
        )
        print(json.dumps({
            "passed": True,
            "report_date": result["manifest"].get("report_date"),
            "file_count": result["manifest"].get("file_count"),
            "payload_sha256": result["manifest"].get("payload_sha256"),
        }, ensure_ascii=False, indent=2))
        return 0
    result = validate_site(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
