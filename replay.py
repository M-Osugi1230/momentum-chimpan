"""Walk-forward replay for Momentum Chimpan research signals.

This module replays stored daily ranking history without mutating live production state.
Signal generation for each replay date only receives data dated on or before that date.
Future observations are used only after signal generation to evaluate outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import main

REPLAY_VERSION = "2026-07-11-walk-forward-replay-v1"
DEFAULT_HISTORY_PATH = "data/momentum_daily_ranking.csv"
DEFAULT_JPX_CACHE_PATH = "data/jpx_list_cache.csv"
DEFAULT_OUTPUT_DIR = "output/replay"

LIVE_STATE_PATHS = [
    "data/momentum_daily_ranking.csv",
    "data/market_temperature.csv",
    "data/sector_leader_signal_history.csv",
    "data/paper_portfolio.csv",
    "data/paper_trade_history.csv",
    "data/paper_equity_history.csv",
    "data/execution_audit.csv",
]

BOOLEAN_COLUMNS = [
    "ytd_high_flag",
    "above_ma20",
    "above_ma60",
    "is_top100",
    "is_new_entry",
    "is_rising_fast",
    "is_best_rank",
]


@dataclass(frozen=True)
class ReplayResult:
    signals: pd.DataFrame
    outcomes: pd.DataFrame
    performance: pd.DataFrame
    audit: pd.DataFrame
    coverage: pd.DataFrame
    manifest: dict[str, Any]


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def live_state_hashes(paths: list[str] | None = None) -> dict[str, str]:
    return {path: sha256_file(path) for path in (paths or LIVE_STATE_PATHS)}


def _coerce_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    normalized = series.astype(str).str.strip().str.lower()
    return normalized.isin({"true", "1", "yes", "y", "t"})


def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        for column in columns:
            if candidate in str(column):
                return column
    return None


def load_sector_map(path: str) -> dict[str, str]:
    target = Path(path)
    if not target.exists():
        return {}
    frame = pd.read_csv(target, dtype=str)
    columns = [str(column) for column in frame.columns]
    code_column = _find_column(columns, ["コード", "code"])
    sector_column = _find_column(columns, ["33業種区分", "33業種"])
    if not code_column or not sector_column:
        return {}
    result: dict[str, str] = {}
    for _, row in frame.iterrows():
        code = main.normalize_code(row.get(code_column, ""))
        sector = main.normalize_sector33(row.get(sector_column, ""))
        if code.isdigit() and len(code) == 4 and sector:
            result[code] = sector
    return result


def prepare_history(history_path: str, jpx_cache_path: str) -> pd.DataFrame:
    target = Path(history_path)
    if not target.exists():
        raise FileNotFoundError(f"ranking history not found: {history_path}")
    history = pd.read_csv(target, dtype={"code": str})
    required = {"date", "rank", "code", "close", "score"}
    missing = sorted(required - set(history.columns))
    if missing:
        raise ValueError(f"ranking history missing required columns: {missing}")

    parsed_dates = pd.to_datetime(history["date"], errors="coerce")
    history = history[parsed_dates.notna()].copy()
    history["date"] = parsed_dates[parsed_dates.notna()].dt.date.astype(str)
    history["code"] = history["code"].map(main.normalize_code)
    history["rank"] = pd.to_numeric(history["rank"], errors="coerce")
    history["close"] = pd.to_numeric(history["close"], errors="coerce")
    history["score"] = pd.to_numeric(history["score"], errors="coerce")
    history = history.dropna(subset=["rank", "close", "score"])

    sector_map = load_sector_map(jpx_cache_path)
    if "sector33" not in history.columns:
        history["sector33"] = ""
    history["sector33"] = history["sector33"].map(main.normalize_sector33)
    missing_sector = history["sector33"].eq("")
    history.loc[missing_sector, "sector33"] = (
        history.loc[missing_sector, "code"].map(sector_map).fillna("")
    )

    for column in BOOLEAN_COLUMNS:
        history[column] = _coerce_bool(history[column]) if column in history.columns else False

    numeric_candidates = [
        "return_5d", "return_20d", "return_60d", "volume_ratio", "trading_value",
        "ma20", "ma60", "ma20_deviation", "ma60_deviation", "rank_change",
        "top30_streak", "top30_streak_days", "ytd_high_streak", "ytd_high_count",
    ]
    for column in numeric_candidates:
        if column in history.columns:
            history[column] = pd.to_numeric(history[column], errors="coerce")

    return (
        history.sort_values(["date", "rank", "code"])
        .drop_duplicates(["date", "code"], keep="last")
        .reset_index(drop=True)
    )


def replay_date_range(
    history: pd.DataFrame,
    min_date: str | None = None,
    max_date: str | None = None,
    max_dates: int | None = None,
) -> list[str]:
    dates = sorted(history["date"].dropna().astype(str).unique().tolist())
    if min_date:
        dates = [date for date in dates if date >= min_date]
    if max_date:
        dates = [date for date in dates if date <= max_date]
    if max_dates and max_dates > 0:
        dates = dates[-max_dates:]
    return dates


def _empty_performance() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "scope_type", "scope_value", "horizon_days", "count", "wins",
        "win_rate", "average_return", "median_return",
    ])


def run_walk_forward_replay(
    history: pd.DataFrame,
    top_limit: int = 100,
    min_date: str | None = None,
    max_date: str | None = None,
    max_dates: int | None = None,
    source_hash: str = "",
) -> ReplayResult:
    dates = replay_date_range(history, min_date, max_date, max_dates)
    if not dates:
        raise ValueError("no replay dates available")

    signal_frames: list[pd.DataFrame] = []
    audit_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    temperature_history = pd.DataFrame()

    for replay_date in dates:
        day_all = history[history["date"].astype(str) == replay_date].copy()
        prior_history = history[history["date"].astype(str) < replay_date].copy()
        day_top = day_all[pd.to_numeric(day_all["rank"], errors="coerce") <= top_limit].copy()

        sector_momentum = main.attach_sector_rotation(
            main.calculate_sector_momentum(day_all, prior_history, replay_date, top_limit)
        )
        priority_changes = main.compare_priority_candidates(
            day_top, prior_history, replay_date, top_limit
        )
        priority_changes = main.attach_priority_candidate_lifecycle(
            priority_changes, prior_history, day_top, replay_date, top_limit
        )
        performance_history = main.combined_ranking_history(
            prior_history, day_all, replay_date
        )
        priority_performance = main.calculate_priority_performance(
            performance_history, top_limit
        )
        signal_performance = main.build_signal_performance_summary(priority_performance)
        priority_changes = main.attach_priority_expectancy(
            priority_changes, signal_performance
        )

        temperature = main.market_temperature(
            replay_date, day_all, day_top, temperature_history
        )
        regime = main.calculate_market_regime(day_top, temperature)
        temperature = main.attach_market_regime_history(
            replay_date, temperature, regime, temperature_history
        )
        regime = main.enrich_regime_from_temperature(regime, temperature)
        priority_changes = main.attach_action_priority(priority_changes, regime)
        action_priority = priority_changes.get("action_priority", pd.DataFrame())
        sector_leaders = main.build_sector_leaders(
            day_all, sector_momentum, action_priority
        )
        signals = main.current_sector_signal_snapshot(replay_date, sector_leaders)
        if signals is not None and not signals.empty:
            signals = signals.copy()
            signals["replay_version"] = REPLAY_VERSION
            signals["signal_input_max_date"] = replay_date
            signals["lookahead_safe"] = True
            signal_frames.append(signals)

        temperature_history = (
            pd.concat([temperature_history, temperature], ignore_index=True)
            .drop_duplicates(["date"], keep="last")
            .sort_values("date")
        )

        max_prior_date = str(prior_history["date"].max()) if not prior_history.empty else ""
        violations = int(bool(max_prior_date and max_prior_date >= replay_date))
        audit_rows.append({
            "signal_date": replay_date,
            "input_max_date": replay_date,
            "prior_input_max_date": max_prior_date,
            "future_rows_available_to_signal_generation": 0,
            "lookahead_violations": violations,
            "signal_count": 0 if signals is None else len(signals),
            "sector_count": len(sector_momentum),
            "top_count": len(day_top),
            "status": "PASS" if violations == 0 else "FAIL",
        })
        price_dates = pd.to_datetime(day_all.get("price_date"), errors="coerce")
        coverage_rows.append({
            "date": replay_date,
            "universe_rows": len(day_all),
            "top_rows": len(day_top),
            "sector_rows": len(sector_momentum),
            "leader_rows": len(sector_leaders),
            "sector_coverage_ratio": (
                float(day_all["sector33"].astype(str).str.strip().ne("").mean())
                if len(day_all) else 0.0
            ),
            "latest_price_date": (
                str(price_dates.max().date()) if price_dates.notna().any() else ""
            ),
        })

    signals_all = pd.concat(signal_frames, ignore_index=True) if signal_frames else pd.DataFrame()
    outcomes = (
        main.calculate_sector_leader_outcomes(signals_all, history)
        if not signals_all.empty else pd.DataFrame()
    )
    performance = (
        main.build_sector_leader_performance_summary(outcomes)
        if outcomes is not None and not outcomes.empty else _empty_performance()
    )
    audit = pd.DataFrame(audit_rows)
    coverage = pd.DataFrame(coverage_rows)
    lookahead_violations = int(
        pd.to_numeric(audit.get("lookahead_violations", pd.Series(dtype=int)), errors="coerce")
        .fillna(0).sum()
    )
    manifest = {
        "replay_version": REPLAY_VERSION,
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_history_sha256": source_hash,
        "first_replay_date": dates[0],
        "last_replay_date": dates[-1],
        "replay_date_count": len(dates),
        "source_row_count": len(history),
        "signal_count": len(signals_all),
        "outcome_count": len(outcomes),
        "performance_row_count": len(performance),
        "lookahead_violations": lookahead_violations,
        "live_state_mutation_allowed": False,
        "research_only": True,
    }
    return ReplayResult(signals_all, outcomes, performance, audit, coverage, manifest)


def write_replay_outputs(result: ReplayResult, output_dir: str) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "excel": str(target / "walk_forward_replay.xlsx"),
        "signals": str(target / "replay_signals.csv"),
        "outcomes": str(target / "replay_outcomes.csv"),
        "performance": str(target / "replay_performance.csv"),
        "audit": str(target / "replay_audit.csv"),
        "coverage": str(target / "replay_coverage.csv"),
        "manifest": str(target / "replay_manifest.json"),
    }
    result.signals.to_csv(paths["signals"], index=False)
    result.outcomes.to_csv(paths["outcomes"], index=False)
    result.performance.to_csv(paths["performance"], index=False)
    result.audit.to_csv(paths["audit"], index=False)
    result.coverage.to_csv(paths["coverage"], index=False)
    Path(paths["manifest"]).write_text(
        json.dumps(result.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = pd.DataFrame([{
        "再生バージョン": result.manifest["replay_version"],
        "本番アプリ版": result.manifest["production_app_version"],
        "再生開始日": result.manifest["first_replay_date"],
        "再生終了日": result.manifest["last_replay_date"],
        "再生日数": result.manifest["replay_date_count"],
        "シグナル件数": result.manifest["signal_count"],
        "結果件数": result.manifest["outcome_count"],
        "先読み違反": result.manifest["lookahead_violations"],
        "ライブstate更新": "禁止",
        "用途": "研究・検証のみ",
    }])
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Replay Summary", index=False)
        result.signals.to_excel(writer, sheet_name="Signals", index=False)
        result.outcomes.to_excel(writer, sheet_name="Outcomes", index=False)
        result.performance.to_excel(writer, sheet_name="Performance", index=False)
        result.audit.to_excel(writer, sheet_name="No Lookahead Audit", index=False)
        result.coverage.to_excel(writer, sheet_name="Coverage", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                values = [len(str(cell.value or "")) for cell in column]
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max(values, default=8) + 2, 42
                )
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay stored Momentum Chimpan history without mutating live state."
    )
    parser.add_argument("--history", default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--jpx-cache", default=DEFAULT_JPX_CACHE_PATH)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-date")
    parser.add_argument("--max-date")
    parser.add_argument("--max-dates", type=int)
    parser.add_argument("--top-limit", type=int, default=100)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before_hashes = live_state_hashes()
    history_hash = sha256_file(args.history)
    history = prepare_history(args.history, args.jpx_cache)
    result = run_walk_forward_replay(
        history,
        top_limit=args.top_limit,
        min_date=args.min_date,
        max_date=args.max_date,
        max_dates=args.max_dates,
        source_hash=history_hash,
    )
    paths = write_replay_outputs(result, args.output_dir)
    after_hashes = live_state_hashes()
    mutated = [
        path for path in before_hashes
        if before_hashes[path] != after_hashes.get(path, "")
    ]
    result.manifest["live_state_mutations"] = mutated
    result.manifest["live_state_unchanged"] = not mutated
    Path(paths["manifest"]).write_text(
        json.dumps(result.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        if result.manifest["lookahead_violations"]:
            raise RuntimeError("look-ahead audit failed")
        if mutated:
            raise RuntimeError(f"live state mutated during replay: {mutated}")
    print(json.dumps(result.manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
