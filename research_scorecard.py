"""Post-hoc evidence scorecard for walk-forward replay outcomes.

The scorecard is research-only. It compares replay signals with equal-weight
universe, Top100, and same-sector benchmarks after signal generation. It never
changes production thresholds, live state, or paper positions.
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

SCORECARD_VERSION = "2026-07-11-replay-evidence-scorecard-v1"
DEFAULT_OUTCOMES = "output/replay/replay_outcomes.csv"
DEFAULT_HISTORY = "data/momentum_daily_ranking.csv"
DEFAULT_JPX_CACHE = "data/jpx_list_cache.csv"
DEFAULT_OUTPUT_DIR = "output/replay"

BENCHMARK_COLUMNS = [
    "universe_equal_weight_return",
    "top100_equal_weight_return",
    "sector_equal_weight_return",
    "excess_vs_universe",
    "excess_vs_top100",
    "excess_vs_sector",
    "beat_universe",
    "beat_top100",
    "beat_sector",
    "benchmark_member_count",
]


def _safe_float(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def load_outcomes(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"replay outcomes not found: {path}")
    outcomes = pd.read_csv(target, dtype={"code": str})
    required = {
        "signal_date", "entry_price_date", "exit_price_date", "code", "sector33",
        "horizon_days", "forward_return",
    }
    missing = sorted(required - set(outcomes.columns))
    if missing:
        raise ValueError(f"replay outcomes missing columns: {missing}")
    outcomes["code"] = outcomes["code"].map(main.normalize_code)
    outcomes["horizon_days"] = pd.to_numeric(outcomes["horizon_days"], errors="coerce")
    outcomes["forward_return"] = pd.to_numeric(outcomes["forward_return"], errors="coerce")
    outcomes = outcomes.dropna(subset=["horizon_days", "forward_return"]).copy()
    outcomes["horizon_days"] = outcomes["horizon_days"].astype(int)
    for column in ("signal_date", "entry_price_date", "exit_price_date"):
        outcomes[column] = pd.to_datetime(outcomes[column], errors="coerce").dt.date.astype("string")
    return outcomes


def _pair_returns(history: pd.DataFrame, entry_date: str, exit_date: str) -> pd.DataFrame:
    entry = history[history["date"].astype(str) == str(entry_date)][
        ["code", "close", "rank", "sector33"]
    ].copy()
    exit_frame = history[history["date"].astype(str) == str(exit_date)][["code", "close"]].copy()
    entry = entry.rename(columns={"close": "entry_benchmark_close", "rank": "entry_rank"})
    exit_frame = exit_frame.rename(columns={"close": "exit_benchmark_close"})
    merged = entry.merge(exit_frame, on="code", how="inner")
    merged["entry_benchmark_close"] = pd.to_numeric(merged["entry_benchmark_close"], errors="coerce")
    merged["exit_benchmark_close"] = pd.to_numeric(merged["exit_benchmark_close"], errors="coerce")
    merged = merged.dropna(subset=["entry_benchmark_close", "exit_benchmark_close"])
    merged = merged[merged["entry_benchmark_close"] > 0].copy()
    merged["benchmark_return"] = (
        merged["exit_benchmark_close"] / merged["entry_benchmark_close"] - 1
    )
    return merged


def attach_benchmarks(outcomes: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if outcomes.empty:
        return outcomes.assign(**{column: pd.Series(dtype=float) for column in BENCHMARK_COLUMNS})
    pair_cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    for _, outcome in outcomes.iterrows():
        entry_date = str(outcome["entry_price_date"])
        exit_date = str(outcome["exit_price_date"])
        key = (entry_date, exit_date)
        if key not in pair_cache:
            pair_cache[key] = _pair_returns(history, entry_date, exit_date)
        benchmark = pair_cache[key]
        universe_return = _safe_float(benchmark["benchmark_return"].mean()) if not benchmark.empty else None
        top100 = benchmark[pd.to_numeric(benchmark["entry_rank"], errors="coerce") <= 100]
        top100_return = _safe_float(top100["benchmark_return"].mean()) if not top100.empty else None
        sector_name = main.normalize_sector33(outcome.get("sector33"))
        sector = benchmark[benchmark["sector33"].map(main.normalize_sector33) == sector_name]
        sector_return = _safe_float(sector["benchmark_return"].mean()) if not sector.empty else None
        signal_return = float(outcome["forward_return"])

        record = outcome.to_dict()
        record.update({
            "universe_equal_weight_return": universe_return,
            "top100_equal_weight_return": top100_return,
            "sector_equal_weight_return": sector_return,
            "excess_vs_universe": None if universe_return is None else signal_return - universe_return,
            "excess_vs_top100": None if top100_return is None else signal_return - top100_return,
            "excess_vs_sector": None if sector_return is None else signal_return - sector_return,
            "beat_universe": None if universe_return is None else signal_return > universe_return,
            "beat_top100": None if top100_return is None else signal_return > top100_return,
            "beat_sector": None if sector_return is None else signal_return > sector_return,
            "benchmark_member_count": len(benchmark),
        })
        rows.append(record)
    return pd.DataFrame(rows)


def bootstrap_mean_ci(
    values: pd.Series,
    samples: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float | None, float | None]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(clean) < 5:
        return None, None
    generator = np.random.default_rng(seed)
    indices = generator.integers(0, len(clean), size=(samples, len(clean)))
    means = clean[indices].mean(axis=1)
    alpha = (1 - confidence) / 2
    return float(np.quantile(means, alpha)), float(np.quantile(means, 1 - alpha))


def evidence_grade(
    count: int,
    ci_low: float | None,
    beat_rate: float | None,
    concentration_flag: bool,
) -> str:
    if count < 10:
        return "INSUFFICIENT"
    if count < 30:
        return "EARLY"
    if ci_low is None or ci_low <= 0:
        return "INCONCLUSIVE"
    if concentration_flag:
        return "DEVELOPING"
    if count >= 100 and beat_rate is not None and beat_rate >= 0.55:
        return "STRONG"
    if count >= 50:
        return "PROMISING"
    return "DEVELOPING"


def build_concentration(enriched: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for horizon, subset in enriched.groupby("horizon_days"):
        count = len(subset)
        code_share = float(subset["code"].value_counts(normalize=True).max()) if count else 0.0
        sector_share = float(subset["sector33"].value_counts(normalize=True).max()) if count else 0.0
        date_share = float(subset["signal_date"].value_counts(normalize=True).max()) if count else 0.0
        rows.append({
            "horizon_days": int(horizon),
            "count": count,
            "unique_codes": int(subset["code"].nunique()),
            "unique_sectors": int(subset["sector33"].nunique()),
            "unique_signal_dates": int(subset["signal_date"].nunique()),
            "top_code_share": code_share,
            "top_sector_share": sector_share,
            "top_signal_date_share": date_share,
            "concentration_flag": max(code_share, sector_share, date_share) > 0.35,
        })
    return pd.DataFrame(rows)


def _scorecard_record(
    group_type: str,
    group_value: str,
    horizon: int,
    subset: pd.DataFrame,
    concentration_flag: bool,
) -> dict[str, Any]:
    excess = pd.to_numeric(subset["excess_vs_universe"], errors="coerce").dropna()
    returns = pd.to_numeric(subset["forward_return"], errors="coerce").dropna()
    beat = subset["beat_universe"].dropna().astype(bool)
    ci_low, ci_high = bootstrap_mean_ci(excess, seed=42 + int(horizon))
    beat_rate = float(beat.mean()) if len(beat) else None
    count = len(excess)
    return {
        "group_type": group_type,
        "group_value": group_value,
        "horizon_days": int(horizon),
        "count": count,
        "win_rate": float((returns > 0).mean()) if len(returns) else None,
        "average_return": float(returns.mean()) if len(returns) else None,
        "median_return": float(returns.median()) if len(returns) else None,
        "average_excess_vs_universe": float(excess.mean()) if len(excess) else None,
        "median_excess_vs_universe": float(excess.median()) if len(excess) else None,
        "beat_universe_rate": beat_rate,
        "excess_ci_low_95": ci_low,
        "excess_ci_high_95": ci_high,
        "concentration_flag": concentration_flag,
        "evidence_grade": evidence_grade(count, ci_low, beat_rate, concentration_flag),
    }


def build_evidence_scorecard(enriched: pd.DataFrame) -> pd.DataFrame:
    if enriched.empty:
        return pd.DataFrame()
    concentration = build_concentration(enriched).set_index("horizon_days")
    records: list[dict[str, Any]] = []
    groupings = [
        ("overall", None),
        ("priority", "sector_research_priority"),
        ("grade", "sector_leader_grade"),
        ("rotation", "sector_rotation"),
    ]
    for horizon, horizon_rows in enriched.groupby("horizon_days"):
        concentration_flag = bool(concentration.loc[int(horizon), "concentration_flag"])
        records.append(_scorecard_record("overall", "all", int(horizon), horizon_rows, concentration_flag))
        for group_type, column in groupings[1:]:
            if column not in horizon_rows.columns:
                continue
            for value, subset in horizon_rows.groupby(column, dropna=False):
                records.append(
                    _scorecard_record(group_type, str(value), int(horizon), subset, concentration_flag)
                )
    return pd.DataFrame(records)


def build_methodology() -> pd.DataFrame:
    return pd.DataFrame([
        {"item": "Signal timing", "detail": "Signals are generated by replay.py using only data available on or before each signal date."},
        {"item": "Benchmark timing", "detail": "Benchmarks are attached only after signals are fixed and use the same entry and exit report dates."},
        {"item": "Universe benchmark", "detail": "Equal-weight return of all codes present on both entry and exit dates."},
        {"item": "Top100 benchmark", "detail": "Equal-weight return of entry-date Momentum Top100 members present at exit."},
        {"item": "Sector benchmark", "detail": "Equal-weight return of entry-date same-sector members present at exit."},
        {"item": "Confidence interval", "detail": "Deterministic 2,000-sample bootstrap 95% interval of mean excess vs universe."},
        {"item": "Evidence grading", "detail": "Grades depend on sample size, confidence interval, beat rate, and concentration."},
        {"item": "Production use", "detail": "Research only. Results do not change live thresholds or paper positions automatically."},
    ])


def write_outputs(
    enriched: pd.DataFrame,
    scorecard: pd.DataFrame,
    concentration: pd.DataFrame,
    output_dir: str,
    source_hash: str,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "excel": target / "replay_evidence_scorecard.xlsx",
        "outcomes": target / "replay_benchmarked_outcomes.csv",
        "scorecard": target / "replay_evidence_scorecard.csv",
        "concentration": target / "replay_concentration.csv",
        "manifest": target / "replay_evidence_manifest.json",
    }
    enriched.to_csv(paths["outcomes"], index=False)
    scorecard.to_csv(paths["scorecard"], index=False)
    concentration.to_csv(paths["concentration"], index=False)
    benchmark_coverage = float(enriched["excess_vs_universe"].notna().mean()) if len(enriched) else 0.0
    manifest = {
        "scorecard_version": SCORECARD_VERSION,
        "replay_version": replay.REPLAY_VERSION,
        "production_app_version": main.APP_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_outcomes_sha256": source_hash,
        "outcome_count": len(enriched),
        "scorecard_row_count": len(scorecard),
        "benchmark_coverage": benchmark_coverage,
        "research_only": True,
        "automatic_threshold_changes": False,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Evidence Summary", index=False)
        enriched.to_excel(writer, sheet_name="Benchmarked Outcomes", index=False)
        scorecard.to_excel(writer, sheet_name="Evidence Scorecard", index=False)
        concentration.to_excel(writer, sheet_name="Concentration", index=False)
        build_methodology().to_excel(writer, sheet_name="Methodology", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"paths": {key: str(value) for key, value in paths.items()}, "manifest": manifest}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build benchmarked evidence from replay outcomes")
    parser.add_argument("--outcomes", default=DEFAULT_OUTCOMES)
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--jpx-cache", default=DEFAULT_JPX_CACHE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    outcomes = load_outcomes(args.outcomes)
    history = replay.prepare_history(args.history, args.jpx_cache)
    enriched = attach_benchmarks(outcomes, history)
    concentration = build_concentration(enriched)
    scorecard = build_evidence_scorecard(enriched)
    result = write_outputs(
        enriched,
        scorecard,
        concentration,
        args.output_dir,
        replay.sha256_file(args.outcomes),
    )
    if args.strict and len(enriched):
        coverage = result["manifest"]["benchmark_coverage"]
        if coverage < 0.90:
            raise RuntimeError(f"benchmark coverage below 90%: {coverage:.1%}")
        invalid_dates = (
            pd.to_datetime(enriched["exit_price_date"], errors="coerce")
            <= pd.to_datetime(enriched["entry_price_date"], errors="coerce")
        ).sum()
        if invalid_dates:
            raise RuntimeError(f"invalid benchmark date ordering: {invalid_dates}")
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
