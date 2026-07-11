"""Evaluate replay signals using tradable next-session prices.

Signals are known only after the report-date close. This research layer enters
at the next available adjusted open, applies explicit slippage and fees, exits
at later adjusted closes, and computes equal-weight universe/Top100/sector
benchmarks over the same dates. It is exploratory backfill evidence only.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import main
import replay
import research_scorecard

EXECUTION_VERSION = "2026-07-11-next-open-execution-v1"
DEFAULT_SIGNALS = "output/backfill/replay/replay_signals.csv"
DEFAULT_PRICES = "output/backfill/historical_price_panel.csv"
DEFAULT_RANKING = "output/backfill/historical_ranking.csv"
DEFAULT_PROVENANCE = "output/backfill/replay/evidence_provenance.json"
DEFAULT_OUTPUT_DIR = "output/backfill/execution"
DEFAULT_HORIZONS = (5, 10, 20)


def load_signals(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(target, dtype={"code": str})
    required = {"signal_date", "code", "sector33", "entry_close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"signals missing columns: {missing}")
    frame["code"] = frame["code"].map(main.normalize_code)
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce")
    frame["entry_close"] = pd.to_numeric(frame["entry_close"], errors="coerce")
    return frame.dropna(subset=["signal_date", "entry_close"]).sort_values(["signal_date", "code"]).reset_index(drop=True)


def load_price_panel(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(target, dtype={"code": str})
    required = {
        "date", "code", "sector33", "adjusted_open", "adjusted_high",
        "adjusted_low", "adjusted_close", "volume",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"price panel missing columns: {missing}")
    frame["code"] = frame["code"].map(main.normalize_code)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "code", "adjusted_open", "adjusted_close"])
    frame = frame[(frame["adjusted_open"] > 0) & (frame["adjusted_close"] > 0) & (frame["volume"] > 0)]
    return frame.drop_duplicates(["date", "code"], keep="last").sort_values(["code", "date"]).reset_index(drop=True)


def load_ranking(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"code": str})
    required = {"date", "code", "rank"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"ranking history missing columns: {missing}")
    frame["code"] = frame["code"].map(main.normalize_code)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["rank"] = pd.to_numeric(frame["rank"], errors="coerce")
    return frame.dropna(subset=["date", "rank"])


def price_lookup(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        code: group.sort_values("date").reset_index(drop=True)
        for code, group in panel.groupby("code", sort=False)
    }


def next_entry_index(frame: pd.DataFrame, signal_date: pd.Timestamp) -> int | None:
    dates = frame["date"].to_numpy(dtype="datetime64[ns]")
    index = int(np.searchsorted(dates, np.datetime64(signal_date), side="right"))
    return index if index < len(frame) else None


def pair_benchmark_returns(
    panel: pd.DataFrame,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
) -> pd.DataFrame:
    entry = panel[panel["date"] == entry_date][["code", "sector33", "adjusted_open"]].copy()
    exit_frame = panel[panel["date"] == exit_date][["code", "adjusted_close"]].copy()
    merged = entry.merge(exit_frame, on="code", how="inner")
    merged = merged[(merged["adjusted_open"] > 0) & merged["adjusted_close"].notna()].copy()
    merged["benchmark_return"] = merged["adjusted_close"] / merged["adjusted_open"] - 1
    return merged


def execution_status(gap_return: float) -> str:
    if gap_return >= 0.05:
        return "GAP_CHASE"
    if gap_return <= -0.05:
        return "GAP_DOWN"
    return "NORMAL"


def simulate_execution(
    signals: pd.DataFrame,
    panel: pd.DataFrame,
    ranking: pd.DataFrame,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    entry_slippage_bps: float = 5.0,
    exit_slippage_bps: float = 5.0,
    fees_bps: float = 20.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    prices = price_lookup(panel)
    top100_by_date = {
        date: set(group[pd.to_numeric(group["rank"], errors="coerce") <= 100]["code"])
        for date, group in ranking.groupby("date")
    }
    benchmark_cache: dict[tuple[pd.Timestamp, pd.Timestamp], pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []

    for _, signal in signals.iterrows():
        code = main.normalize_code(signal.get("code"))
        signal_date = pd.Timestamp(signal["signal_date"]).normalize()
        code_prices = prices.get(code)
        if code_prices is None or code_prices.empty:
            coverage_rows.append({"signal_date": signal_date.date().isoformat(), "code": code, "status": "NO_PRICE_HISTORY", "entry_date": "", "available_horizons": 0})
            continue
        entry_index = next_entry_index(code_prices, signal_date)
        if entry_index is None:
            coverage_rows.append({"signal_date": signal_date.date().isoformat(), "code": code, "status": "NO_NEXT_SESSION", "entry_date": "", "available_horizons": 0})
            continue
        entry_row = code_prices.iloc[entry_index]
        entry_date = pd.Timestamp(entry_row["date"]).normalize()
        entry_open = float(entry_row["adjusted_open"])
        signal_close = float(signal["entry_close"])
        gap_return = entry_open / signal_close - 1
        available_horizons = 0

        for horizon in horizons:
            exit_index = entry_index + int(horizon) - 1
            if exit_index >= len(code_prices):
                continue
            exit_row = code_prices.iloc[exit_index]
            exit_date = pd.Timestamp(exit_row["date"]).normalize()
            exit_close = float(exit_row["adjusted_close"])
            effective_entry = entry_open * (1 + entry_slippage_bps / 10_000)
            effective_exit = exit_close * (1 - exit_slippage_bps / 10_000)
            gross_return = exit_close / entry_open - 1
            net_return = effective_exit / effective_entry - 1 - fees_bps / 10_000
            close_based_return = exit_close / signal_close - 1
            shortfall = gross_return - close_based_return

            key = (entry_date, exit_date)
            if key not in benchmark_cache:
                benchmark_cache[key] = pair_benchmark_returns(panel, entry_date, exit_date)
            benchmark = benchmark_cache[key]
            universe_return = float(benchmark["benchmark_return"].mean()) if not benchmark.empty else np.nan
            signal_sector = main.normalize_sector33(signal.get("sector33"))
            sector_rows = benchmark[benchmark["sector33"].map(main.normalize_sector33) == signal_sector]
            sector_return = float(sector_rows["benchmark_return"].mean()) if not sector_rows.empty else np.nan
            top100_codes = top100_by_date.get(signal_date, set())
            top100_rows = benchmark[benchmark["code"].isin(top100_codes)]
            top100_return = float(top100_rows["benchmark_return"].mean()) if not top100_rows.empty else np.nan

            record = signal.to_dict()
            record.update({
                "signal_date": signal_date.date().isoformat(),
                "entry_price_date": entry_date.date().isoformat(),
                "exit_price_date": exit_date.date().isoformat(),
                "horizon_days": int(horizon),
                "signal_close": signal_close,
                "next_session_open": entry_open,
                "exit_close": exit_close,
                "entry_gap_return": gap_return,
                "execution_status": execution_status(gap_return),
                "entry_slippage_bps": entry_slippage_bps,
                "exit_slippage_bps": exit_slippage_bps,
                "fees_bps": fees_bps,
                "total_nominal_friction_bps": entry_slippage_bps + exit_slippage_bps + fees_bps,
                "close_based_forward_return": close_based_return,
                "next_open_gross_return": gross_return,
                "implementation_shortfall": shortfall,
                "forward_return": net_return,
                "universe_equal_weight_return": universe_return,
                "top100_equal_weight_return": top100_return,
                "sector_equal_weight_return": sector_return,
                "excess_vs_universe": net_return - universe_return if pd.notna(universe_return) else np.nan,
                "excess_vs_top100": net_return - top100_return if pd.notna(top100_return) else np.nan,
                "excess_vs_sector": net_return - sector_return if pd.notna(sector_return) else np.nan,
                "beat_universe": bool(net_return > universe_return) if pd.notna(universe_return) else None,
                "beat_top100": bool(net_return > top100_return) if pd.notna(top100_return) else None,
                "beat_sector": bool(net_return > sector_return) if pd.notna(sector_return) else None,
                "benchmark_member_count": len(benchmark),
                "execution_model": EXECUTION_VERSION,
            })
            rows.append(record)
            available_horizons += 1

        coverage_rows.append({
            "signal_date": signal_date.date().isoformat(),
            "code": code,
            "status": "EXECUTABLE" if available_horizons else "INSUFFICIENT_FUTURE_SESSIONS",
            "entry_date": entry_date.date().isoformat(),
            "entry_gap_return": gap_return,
            "execution_status": execution_status(gap_return),
            "available_horizons": available_horizons,
        })

    return pd.DataFrame(rows), pd.DataFrame(coverage_rows)


def execution_summary(outcomes: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    signal_count = len(coverage)
    executable = int((coverage.get("status", pd.Series(dtype=str)) == "EXECUTABLE").sum()) if not coverage.empty else 0
    for horizon, group in outcomes.groupby("horizon_days") if not outcomes.empty else []:
        rows.append({
            "horizon_days": int(horizon),
            "signal_count": signal_count,
            "executable_signal_count": executable,
            "outcome_count": len(group),
            "execution_coverage": executable / signal_count if signal_count else 0.0,
            "average_entry_gap": float(pd.to_numeric(group["entry_gap_return"], errors="coerce").mean()),
            "gap_chase_rate": float((group["execution_status"] == "GAP_CHASE").mean()),
            "average_close_based_return": float(pd.to_numeric(group["close_based_forward_return"], errors="coerce").mean()),
            "average_next_open_gross_return": float(pd.to_numeric(group["next_open_gross_return"], errors="coerce").mean()),
            "average_implementation_shortfall": float(pd.to_numeric(group["implementation_shortfall"], errors="coerce").mean()),
            "average_net_return": float(pd.to_numeric(group["forward_return"], errors="coerce").mean()),
            "average_excess_vs_universe": float(pd.to_numeric(group["excess_vs_universe"], errors="coerce").mean()),
            "beat_universe_rate": float(group["beat_universe"].dropna().astype(bool).mean()) if group["beat_universe"].notna().any() else None,
        })
    return pd.DataFrame(rows)


def write_outputs(
    outcomes: pd.DataFrame,
    coverage: pd.DataFrame,
    output_dir: str,
    provenance_path: str,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    summary = execution_summary(outcomes, coverage)
    concentration = research_scorecard.build_concentration(outcomes) if not outcomes.empty else pd.DataFrame()
    evidence = research_scorecard.build_evidence_scorecard(outcomes) if not outcomes.empty else pd.DataFrame()
    paths = {
        "outcomes": target / "execution_benchmarked_outcomes.csv",
        "coverage": target / "execution_coverage.csv",
        "summary": target / "execution_summary.csv",
        "evidence": target / "execution_evidence_scorecard.csv",
        "concentration": target / "execution_concentration.csv",
        "excel": target / "execution_realism.xlsx",
        "manifest": target / "execution_realism_manifest.json",
    }
    outcomes.to_csv(paths["outcomes"], index=False)
    coverage.to_csv(paths["coverage"], index=False)
    summary.to_csv(paths["summary"], index=False)
    evidence.to_csv(paths["evidence"], index=False)
    concentration.to_csv(paths["concentration"], index=False)
    manifest = {
        "execution_version": EXECUTION_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "strategy_fingerprint": provenance.get("strategy_fingerprint", ""),
        "signal_count": len(coverage),
        "executable_signal_count": int((coverage.get("status", pd.Series(dtype=str)) == "EXECUTABLE").sum()) if not coverage.empty else 0,
        "outcome_count": len(outcomes),
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "exit_model": "NTH_SESSION_ADJUSTED_CLOSE",
        "default_entry_slippage_bps": float(outcomes["entry_slippage_bps"].iloc[0]) if not outcomes.empty else 5.0,
        "default_exit_slippage_bps": float(outcomes["exit_slippage_bps"].iloc[0]) if not outcomes.empty else 5.0,
        "default_fees_bps": float(outcomes["fees_bps"].iloc[0]) if not outcomes.empty else 20.0,
        "lookahead_entry_allowed": False,
        "same_day_close_entry_allowed": False,
        "research_only": True,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Execution Summary", index=False)
        summary.to_excel(writer, sheet_name="Horizon Summary", index=False)
        outcomes.to_excel(writer, sheet_name="Execution Outcomes", index=False)
        coverage.to_excel(writer, sheet_name="Coverage", index=False)
        evidence.to_excel(writer, sheet_name="Evidence Scorecard", index=False)
        concentration.to_excel(writer, sheet_name="Concentration", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(max((len(str(cell.value or "")) for cell in column), default=8) + 2, 48)
    return {"manifest": manifest, "paths": {key: str(value) for key, value in paths.items()}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate replay signals at next-session executable prices")
    parser.add_argument("--signals", default=DEFAULT_SIGNALS)
    parser.add_argument("--prices", default=DEFAULT_PRICES)
    parser.add_argument("--ranking", default=DEFAULT_RANKING)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--entry-slippage-bps", type=float, default=5.0)
    parser.add_argument("--exit-slippage-bps", type=float, default=5.0)
    parser.add_argument("--fees-bps", type=float, default=20.0)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before = replay.live_state_hashes()
    signals = load_signals(args.signals)
    panel = load_price_panel(args.prices)
    ranking = load_ranking(args.ranking)
    outcomes, coverage = simulate_execution(
        signals,
        panel,
        ranking,
        DEFAULT_HORIZONS,
        args.entry_slippage_bps,
        args.exit_slippage_bps,
        args.fees_bps,
    )
    result = write_outputs(outcomes, coverage, args.output_dir, args.provenance)
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    result["manifest"]["production_state_mutations"] = mutations
    Path(result["paths"]["manifest"]).write_text(json.dumps(result["manifest"], ensure_ascii=False, indent=2), encoding="utf-8")
    if args.strict:
        if mutations:
            raise RuntimeError(f"production state mutated: {mutations}")
        if outcomes.empty:
            raise RuntimeError("execution simulation produced no outcomes")
        if (pd.to_datetime(outcomes["entry_price_date"]) <= pd.to_datetime(outcomes["signal_date"])).any():
            raise RuntimeError("same-day or pre-signal entries detected")
        if (pd.to_datetime(outcomes["exit_price_date"]) < pd.to_datetime(outcomes["entry_price_date"])).any():
            raise RuntimeError("exit precedes entry")
        coverage_ratio = float((coverage["status"] == "EXECUTABLE").mean()) if len(coverage) else 0.0
        if coverage_ratio < 0.50:
            raise RuntimeError(f"execution coverage below 50%: {coverage_ratio:.1%}")
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
