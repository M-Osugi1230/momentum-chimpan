from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import historical_backfill as backfill
import portfolio_exit_lab as lab
import portfolio_research as portfolio


assert portfolio.PORTFOLIO_RESEARCH_VERSION == "2026-07-11-execution-portfolio-v3-exit-policy"
assert len(lab.EXPERIMENTS) == 28
assert {row.entry_cohort for row in lab.EXPERIMENTS} == {"baseline", "relative_strength_a_s"}

position = {
    "code": "1001",
    "fixed_stop": 92.0,
    "target_price": 115.0,
    "highest_close": 100.0,
    "holding_sessions": 4,
}
price_row = {
    "adjusted_open": 100.0,
    "adjusted_high": 104.0,
    "adjusted_low": 96.0,
    "adjusted_close": 101.0,
}
hold5 = portfolio.ExitPolicy("hold5", 0.08, 0.15, 0.10, 5, True)
assert portfolio.resolve_exit(position, price_row, False, {"1001"}, hold5) == ("TIME_EXIT", 101.0)
no_signal = portfolio.ExitPolicy("no_signal", 0.08, 0.15, 0.10, 20, False)
assert portfolio.resolve_exit(position, price_row, True, set(), no_signal) is None
signal_exit = portfolio.ExitPolicy("signal", 0.08, 0.15, 0.10, 20, True)
assert portfolio.resolve_exit(position, price_row, True, set(), signal_exit) == ("SIGNAL_EXIT", 101.0)

# Hold eligibility must be independent of entry eligibility. A held stock exits
# when the hold flag becomes false even if the row remains on the report date.
dates = pd.bdate_range("2026-01-05", periods=12)
prices_calendar = pd.DataFrame([
    {
        "date": day,
        "code": "1001",
        "sector33": "電気機器",
        "adjusted_open": 100.0,
        "adjusted_high": 101.0,
        "adjusted_low": 99.0,
        "adjusted_close": 100.0,
        "raw_trading_value": 1_000_000_000.0,
    }
    for day in dates
])
signals_calendar = pd.DataFrame([
    {
        "signal_date": dates[0], "code": "1001", "name": "Hold Gate",
        "sector33": "電気機器", "entry_close": 100.0,
        "sector_research_priority": "最優先", "sector_leader_score": 90,
        "portfolio_eligible": True, "portfolio_hold_eligible": True,
    },
    {
        "signal_date": dates[3], "code": "1001", "name": "Hold Gate",
        "sector33": "電気機器", "entry_close": 100.0,
        "sector_research_priority": "最優先", "sector_leader_score": 90,
        "portfolio_eligible": True, "portfolio_hold_eligible": False,
    },
])
calendar_result = portfolio.simulate_scenario(
    signals_calendar,
    prices_calendar,
    portfolio.PortfolioScenario("hold-gate", None, 0.0, 0.01),
    exit_policy=BASELINE_EXIT if (BASELINE_EXIT := portfolio.ExitPolicy("baseline", 0.08, 0.15, 0.10, 20, True)) else portfolio.DEFAULT_EXIT_POLICY,
)
assert len(calendar_result["trades"]) == 1
assert calendar_result["trades"].iloc[0]["exit_reason"] == "SIGNAL_EXIT"
assert calendar_result["trades"].iloc[0]["exit_date"] == dates[3].date().isoformat()

members = []
for sector_index, sector in enumerate(("電気機器", "銀行業", "機械", "情報・通信業")):
    for stock_index in range(4):
        code = f"{sector_index + 1}{stock_index + 1:03d}"
        members.append(backfill.UniverseMember(code, f"Stock {code}", "Prime", sector))

index = pd.bdate_range("2024-01-04", periods=330)
price_frames: dict[str, pd.DataFrame] = {}
for member_index, member in enumerate(members):
    base = 80.0 + member_index * 4.0
    slope = 0.42 - member_index * 0.035
    wave = np.sin(np.arange(len(index)) / (8 + member_index % 5)) * (2.5 + member_index % 3)
    regime_shift = np.where(np.arange(len(index)) > 190, (member_index % 4 - 1.5) * 0.10 * (np.arange(len(index)) - 190), 0.0)
    close = np.maximum(base + np.arange(len(index)) * slope + wave + regime_shift, 12.0)
    volume = np.full(len(index), 8_000_000 + member_index * 250_000)
    price_frames[member.code] = pd.DataFrame({
        "Date": index,
        "Open": close * (0.998 + (member_index % 3) * 0.001),
        "High": close * 1.025,
        "Low": close * 0.975,
        "Close": close,
        "Volume": volume,
        "RawClose": close,
    })

history, _ = backfill.build_historical_ranking(
    members,
    price_frames,
    {"market": {"min_trading_value": 100_000_000}},
    sample_every=5,
    minimum_coverage_ratio=0.70,
    top_limit=12,
)
assert history["date"].nunique() > 20

signal_rows = []
for report_date, day in history.groupby("date"):
    for _, row in day.sort_values("rank").head(10).iterrows():
        signal_rows.append({
            "signal_date": report_date,
            "code": row["code"],
            "name": row["name"],
            "sector33": row["sector33"],
            "entry_close": row["close"],
            "sector_research_priority": "最優先" if int(row["rank"]) <= 3 else "優先",
            "sector_leader_score": 100 - int(row["rank"]),
            "sector_rotation": "加速" if int(row["rank"]) <= 4 else "改善",
            "relative_strength_score": row["relative_strength_score"],
            "relative_strength_grade": row["relative_strength_grade"],
        })
signals = pd.DataFrame(signal_rows)
signals["signal_date"] = pd.to_datetime(signals["signal_date"])

price_rows = []
for member in members:
    for _, row in price_frames[member.code].iterrows():
        price_rows.append({
            "date": row["Date"],
            "code": member.code,
            "sector33": member.sector33,
            "adjusted_open": row["Open"],
            "adjusted_high": row["High"],
            "adjusted_low": row["Low"],
            "adjusted_close": row["Close"],
            "raw_trading_value": row["RawClose"] * row["Volume"],
        })
prices = pd.DataFrame(price_rows)

results = lab.run_exit_lab(signals, history, prices)
assert set(results["summary"]["experiment"]) == {row.name for row in lab.EXPERIMENTS}
assert set(results["period_metrics"]["period"]) == {"full", "early", "late"}
assert len(results["period_metrics"]) == len(lab.EXPERIMENTS) * 3
assert results["context_coverage"].iloc[0]["relative_strength_score_coverage"] == 1.0
assert results["context_coverage"].iloc[0]["lifecycle_coverage"] == 1.0
assert not results["equity"].empty
assert "delta_excess_vs_cohort_baseline" in results["summary"].columns
assert "early_total_return" in results["summary"].columns
assert "late_total_return" in results["summary"].columns
assert set(results["summary"]["evidence_status"]).issubset({
    "BASELINE", "INSUFFICIENT", "ROBUST_OUTPERFORMANCE",
    "ROBUST_IMPROVEMENT_ONLY", "FULL_PERIOD_IMPROVEMENT_ONLY", "NOT_IMPROVED",
})
for cohort in ("baseline", "relative_strength_a_s"):
    baseline = results["summary"][
        (results["summary"]["entry_cohort"] == cohort)
        & (results["summary"]["exit_policy"] == "baseline")
        & (~results["summary"]["deterioration_guard"].astype(bool))
    ]
    assert len(baseline) == 1
    assert baseline.iloc[0]["evidence_status"] == "BASELINE"

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    provenance = root / "provenance.json"
    provenance.write_text(json.dumps({
        "evidence_origin": "HISTORICAL_CURRENT_UNIVERSE_BACKFILL",
        "promotion_evidence_allowed": False,
    }), encoding="utf-8")
    output = lab.write_outputs(results, str(provenance), str(root / "exit_lab"))
    for path in output["paths"].values():
        assert Path(path).exists(), path
    manifest = output["manifest"]
    assert manifest["promotion_evidence_allowed"] is False
    assert manifest["automatic_exit_activation"] is False
    assert manifest["automatic_strategy_change"] is False
    assert manifest["same_day_close_entry_allowed"] is False
    workbook = pd.ExcelFile(output["paths"]["excel"])
    assert {
        "Lab Summary", "Exit Summary", "Period Metrics", "Trades", "Equity",
        "Skipped Entries", "Experiment Audit", "Context Coverage",
    }.issubset(workbook.sheet_names)

print("portfolio exit lab validation passed")
