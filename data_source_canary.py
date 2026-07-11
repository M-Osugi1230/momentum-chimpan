"""Preflight canary for JPX universe metadata and yfinance price history.

The canary samples current-listed Japanese equities across sectors, validates
normalized daily OHLCV invariants, price freshness, adjustment factors, and a
small batch-vs-single download consistency set. It writes diagnostics only and
never mutates production market or paper state.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import historical_backfill
import main
import replay

CANARY_VERSION = "2026-07-11-data-source-canary-v1"
DEFAULT_OUTPUT_DIR = "output/data-source-canary"
DEFAULT_CACHE = "data/jpx_list_cache.csv"
DEFAULT_CONFIG = "config.yaml"


def stratified_sample(
    members: list[historical_backfill.UniverseMember],
    sample_size: int,
) -> list[historical_backfill.UniverseMember]:
    if sample_size <= 0 or sample_size >= len(members):
        return sorted(members, key=lambda member: (member.sector33, member.code))
    by_sector: dict[str, list[historical_backfill.UniverseMember]] = {}
    for member in sorted(members, key=lambda item: (item.sector33, item.code)):
        sector = main.normalize_sector33(member.sector33) or "UNKNOWN"
        by_sector.setdefault(sector, []).append(member)
    sectors = sorted(by_sector)
    selected: list[historical_backfill.UniverseMember] = []
    offset = 0
    while len(selected) < sample_size:
        added = False
        for sector in sectors:
            group = by_sector[sector]
            if offset < len(group):
                selected.append(group[offset])
                added = True
                if len(selected) >= sample_size:
                    break
        if not added:
            break
        offset += 1
    return selected


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def inspect_symbol(
    member: historical_backfill.UniverseMember,
    frame: pd.DataFrame | None,
    today: date,
    minimum_rows: int,
    maximum_age_days: int,
) -> dict[str, Any]:
    base = {
        "code": member.code,
        "name": member.name,
        "sector33": main.normalize_sector33(member.sector33),
        "row_count": 0,
        "first_date": "",
        "latest_date": "",
        "age_days": None,
        "duplicate_dates": 0,
        "missing_ohlcv_rows": 0,
        "ohlc_violations": 0,
        "nonpositive_close_rows": 0,
        "negative_volume_rows": 0,
        "invalid_adjustment_rows": 0,
        "adjustment_ratio_changes": 0,
        "status": "FAIL",
        "detail": "price history is missing",
    }
    if frame is None or frame.empty:
        return base
    required = {"Date", "Open", "High", "Low", "Close", "Volume", "RawClose"}
    missing_columns = sorted(required - set(frame.columns))
    if missing_columns:
        return {**base, "detail": f"missing normalized columns: {missing_columns}"}
    work = frame.copy()
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume", "RawClose"):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=["Date"]).sort_values("Date")
    if work.empty:
        return {**base, "detail": "all dates are invalid"}
    duplicate_dates = int(work["Date"].duplicated().sum())
    missing_ohlcv = int(work[["Open", "High", "Low", "Close", "Volume"]].isna().any(axis=1).sum())
    high = work["High"]
    low = work["Low"]
    open_price = work["Open"]
    close = work["Close"]
    ohlc_violations = int(
        (
            (high < open_price)
            | (high < close)
            | (low > open_price)
            | (low > close)
            | (high < low)
        ).fillna(True).sum()
    )
    nonpositive_close = int((close <= 0).fillna(True).sum())
    negative_volume = int((work["Volume"] < 0).fillna(True).sum())
    ratio = work["RawClose"] / close.replace(0, np.nan)
    invalid_adjustment = int((~np.isfinite(ratio) | (ratio <= 0)).sum())
    valid_ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
    ratio_changes = int((valid_ratio.pct_change().abs() > 0.01).sum()) if len(valid_ratio) else 0
    latest = pd.Timestamp(work["Date"].max()).date()
    first = pd.Timestamp(work["Date"].min()).date()
    age_days = (today - latest).days
    severe = bool(
        len(work) < minimum_rows
        or age_days > maximum_age_days
        or duplicate_dates
        or missing_ohlcv
        or ohlc_violations
        or nonpositive_close
        or negative_volume
        or invalid_adjustment
    )
    warning = bool(age_days > 3 or ratio_changes > 5)
    status = "FAIL" if severe else "WARN" if warning else "PASS"
    details: list[str] = []
    if len(work) < minimum_rows:
        details.append(f"rows {len(work)} < {minimum_rows}")
    if age_days > maximum_age_days:
        details.append(f"latest price is {age_days} days old")
    if duplicate_dates:
        details.append(f"duplicate dates {duplicate_dates}")
    if missing_ohlcv:
        details.append(f"missing OHLCV rows {missing_ohlcv}")
    if ohlc_violations:
        details.append(f"OHLC violations {ohlc_violations}")
    if nonpositive_close:
        details.append(f"nonpositive close rows {nonpositive_close}")
    if negative_volume:
        details.append(f"negative volume rows {negative_volume}")
    if invalid_adjustment:
        details.append(f"invalid adjustment rows {invalid_adjustment}")
    if ratio_changes > 5:
        details.append(f"frequent adjustment changes {ratio_changes}")
    return {
        **base,
        "row_count": len(work),
        "first_date": first.isoformat(),
        "latest_date": latest.isoformat(),
        "age_days": age_days,
        "duplicate_dates": duplicate_dates,
        "missing_ohlcv_rows": missing_ohlcv,
        "ohlc_violations": ohlc_violations,
        "nonpositive_close_rows": nonpositive_close,
        "negative_volume_rows": negative_volume,
        "invalid_adjustment_rows": invalid_adjustment,
        "adjustment_ratio_changes": ratio_changes,
        "status": status,
        "detail": " | ".join(details) if details else "normalized OHLCV checks passed",
    }


def latest_close(frame: pd.DataFrame | None) -> tuple[str, float | None]:
    if frame is None or frame.empty or not {"Date", "Close"}.issubset(frame.columns):
        return "", None
    work = frame.copy()
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
    work["Close"] = pd.to_numeric(work["Close"], errors="coerce")
    work = work.dropna(subset=["Date", "Close"]).sort_values("Date")
    if work.empty:
        return "", None
    row = work.iloc[-1]
    return pd.Timestamp(row["Date"]).date().isoformat(), float(row["Close"])


def compare_batch_single(
    members: list[historical_backfill.UniverseMember],
    batch_prices: dict[str, pd.DataFrame],
    start: date,
    end: date,
    compare_count: int,
    relative_tolerance: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for member in members[: max(compare_count, 0)]:
        single_prices, errors = historical_backfill.download_price_history(
            [member], start, end, batch_size=1
        )
        batch_date, batch_close = latest_close(batch_prices.get(member.code))
        single_date, single_close = latest_close(single_prices.get(member.code))
        relative_difference = None
        if batch_close is not None and single_close is not None and single_close != 0:
            relative_difference = abs(batch_close - single_close) / abs(single_close)
        passed = bool(
            not errors
            and batch_date
            and batch_date == single_date
            and relative_difference is not None
            and relative_difference <= relative_tolerance
        )
        rows.append({
            "code": member.code,
            "name": member.name,
            "batch_latest_date": batch_date,
            "single_latest_date": single_date,
            "batch_latest_close": batch_close,
            "single_latest_close": single_close,
            "relative_difference": relative_difference,
            "tolerance": relative_tolerance,
            "download_error_count": len(errors),
            "status": "PASS" if passed else "FAIL",
        })
    return pd.DataFrame(rows)


def overall_status(
    details: pd.DataFrame,
    comparisons: pd.DataFrame,
    download_error_count: int,
) -> tuple[str, str]:
    if details.empty:
        return "FAIL", "no symbol checks were produced"
    pass_count = int((details["status"] == "PASS").sum())
    warn_count = int((details["status"] == "WARN").sum())
    fail_count = int((details["status"] == "FAIL").sum())
    coverage = (pass_count + warn_count) / len(details)
    comparison_failures = int((comparisons.get("status", pd.Series(dtype=str)) == "FAIL").sum()) if not comparisons.empty else 0
    if coverage < 0.80 or fail_count > max(1, math.floor(len(details) * 0.20)) or comparison_failures:
        status = "FAIL"
    elif coverage < 0.95 or warn_count or download_error_count:
        status = "WARN"
    else:
        status = "PASS"
    detail = (
        f"PASS {pass_count} / WARN {warn_count} / FAIL {fail_count} / "
        f"coverage {coverage:.1%} / batch-single failures {comparison_failures} / "
        f"download errors {download_error_count}"
    )
    return status, detail


def run_canary(
    cache_path: str = DEFAULT_CACHE,
    config_path: str = DEFAULT_CONFIG,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    sample_size: int = 12,
    lookback_days: int = 120,
    batch_size: int = 12,
    compare_count: int = 3,
    minimum_rows: int = 20,
    maximum_age_days: int = 7,
    relative_tolerance: float = 0.005,
) -> dict[str, Any]:
    before = replay.live_state_hashes()
    config = historical_backfill.load_config(config_path)
    universe = historical_backfill.load_current_universe(cache_path, config)
    selected = stratified_sample(universe, sample_size)
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=max(lookback_days, 30))
    end = today + timedelta(days=2)
    prices, errors = historical_backfill.download_price_history(
        selected, start, end, batch_size=max(batch_size, 1)
    )
    detail_rows = [
        inspect_symbol(member, prices.get(member.code), today, minimum_rows, maximum_age_days)
        for member in selected
    ]
    details = pd.DataFrame(detail_rows)
    comparisons = compare_batch_single(
        selected,
        prices,
        start,
        end,
        compare_count,
        relative_tolerance,
    )
    status, status_detail = overall_status(details, comparisons, len(errors))
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    details.to_csv(output / "symbol_checks.csv", index=False)
    comparisons.to_csv(output / "batch_single_comparison.csv", index=False)
    pd.DataFrame(errors).to_csv(output / "download_errors.csv", index=False)
    manifest = {
        "canary_version": CANARY_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "status": status,
        "status_detail": status_detail,
        "universe_count": len(universe),
        "sample_size": len(selected),
        "sample_sector_count": len({main.normalize_sector33(member.sector33) for member in selected}),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "minimum_rows": minimum_rows,
        "maximum_age_days": maximum_age_days,
        "batch_single_compare_count": len(comparisons),
        "batch_single_relative_tolerance": relative_tolerance,
        "download_error_count": len(errors),
        "production_state_mutations": mutations,
        "research_only": True,
    }
    (output / "canary_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with pd.ExcelWriter(output / "data_source_canary.xlsx", engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Canary Summary", index=False)
        details.to_excel(writer, sheet_name="Symbol Checks", index=False)
        comparisons.to_excel(writer, sheet_name="Batch Single", index=False)
        pd.DataFrame(errors).to_excel(writer, sheet_name="Download Errors", index=False)
    return {
        "manifest": manifest,
        "details": details,
        "comparisons": comparisons,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the external market data canary")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--sample-size", type=int, default=12)
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--compare-count", type=int, default=3)
    parser.add_argument("--minimum-rows", type=int, default=20)
    parser.add_argument("--maximum-age-days", type=int, default=7)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    result = run_canary(
        args.cache,
        args.config,
        args.output_dir,
        args.sample_size,
        args.lookback_days,
        args.batch_size,
        args.compare_count,
        args.minimum_rows,
        args.maximum_age_days,
    )
    manifest = result["manifest"]
    if args.strict:
        if manifest["production_state_mutations"]:
            raise RuntimeError(f"production state mutated: {manifest['production_state_mutations']}")
        if manifest["status"] == "FAIL":
            raise RuntimeError(manifest["status_detail"])
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
