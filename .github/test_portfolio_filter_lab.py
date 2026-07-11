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
import portfolio_filter_lab as lab
import portfolio_research as portfolio


# Verify that an ineligible row remains on the report calendar and exits a
# previously held position rather than silently deleting the report date.
flat_dates = pd.bdate_range("2026-01-05", periods=10)
flat_prices = pd.DataFrame([
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
    for day in flat_dates
])
calendar_signals = pd.DataFrame([
    {
        "signal_date": flat_dates[0], "code": "1001", "name": "Calendar",
        "sector33": "電気機器", "entry_close": 100.0,
        "sector_research_priority": "最優先", "sector_leader_score": 90,
        "portfolio_eligible": True,
    },
    {
        "signal_date": flat_dates[3], "code": "1001", "name": "Calendar",
        "sector33": "電気機器", "entry_close": 100.0,
        "sector_research_priority": "最優先", "sector_leader_score": 90,
        "portfolio_eligible": False,
    },
])
calendar_result = portfolio.simulate_scenario(
    calendar_signals,
    flat_prices,
    portfolio.PortfolioScenario("calendar", None, 0.0, 0.01),
)
assert len(calendar_result["trades"]) == 1
calendar_trade = calendar_result["trades"].iloc[0]
assert calendar_trade["exit_reason"] == "SIGNAL_EXIT"
assert calendar_trade["exit_date"] == flat_dates[3].date().isoformat()


members = [
    backfill.UniverseMember("1001", "Alpha", "Prime", "電気機器"),
    backfill.UniverseMember("1002", "Beta", "Prime", "電気機器"),
    backfill.UniverseMember("2001", "Gamma", "Prime", "銀行業"),
    backfill.UniverseMember("2002", "Delta", "Prime", "銀行業"),
    backfill.UniverseMember("3001", "Epsilon", "Standard", "機械"),
    backfill.UniverseMember("3002", "Zeta", "Standard", "機械"),
    backfill.UniverseMember("4001", "Eta", "Growth", "情報・通信業"),
    backfill.UniverseMember("4002", "Theta", "Growth", "情報・通信業"),
]
index = pd.bdate_range("2025-01-06", periods=145)
price_frames: dict[str, pd.DataFrame] = {}
for member_index, member in enumerate(members):
    base = 100 + member_index * 6
    slope = [0.70, 0.48, 0.36, 0.24, 0.10, -0.02, -0.10, -0.18][member_index]
    trend = np.arange(len(index), dtype=float) * slope
    if member.code == "3002":
        trend += np.where(np.arange(len(index)) >= 100, (np.arange(len(index)) - 99) * 0.65, 0.0)
    close = np.maximum(base + trend, 15.0)
    volume = np.full(len(index), 5_000_000 + member_index * 250_000)
    price_frames[member.code] = pd.DataFrame({
        "Date": index,
        "Open": close * 0.999,
        "High": close * 1.018,
        "Low": close * 0.982,
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
    top_limit=4,
)
assert history["relative_strength_score"].notna().all()

signal_rows = []
for report_date, day in history.groupby("date"):
    for _, row in day.sort_values("rank").head(4).iterrows():
        signal_rows.append({
            "signal_date": report_date,
            "code": row["code"],
            "name": row["name"],
            "sector33": row["sector33"],
            "entry_close": row["close"],
            "sector_research_priority": "最優先" if int(row["rank"]) == 1 else "優先",
            "sector_leader_score": 95 - int(row["rank"]) * 4,
            "sector_leader_grade": "A",
            "sector_rotation": "加速" if int(row["rank"]) <= 2 else "改善",
            "action_priority": "A" if int(row["rank"]) == 1 else "B" if int(row["rank"]) == 2 else "C",
            "action_score": 90 - int(row["rank"]) * 5,
            "relative_strength_score": row["relative_strength_score"],
            "relative_strength_grade": row["relative_strength_grade"],
        })
signals = pd.DataFrame(signal_rows)
signals["signal_date"] = pd.to_datetime(signals["signal_date"])

price_rows = []
for member in members:
    frame = price_frames[member.code]
    for _, row in frame.iterrows():
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

enriched, coverage = lab.attach_filter_context(signals, history)
assert coverage.iloc[0]["relative_strength_score_coverage"] == 1.0
assert coverage.iloc[0]["lifecycle_coverage"] == 1.0
assert coverage.iloc[0]["market_regime_coverage"] == 1.0
assert enriched["relative_strength_lifecycle"].fillna("").ne("").all()
assert enriched["market_regime"].fillna("").ne("").all()

quality_rule = next(rule for rule in lab.RULES if rule.name == "quality_liquid")
flagged, audit = lab.apply_rule(enriched, quality_rule)
assert flagged["portfolio_eligible"].dtype == bool
assert not audit.empty
assert int(flagged["portfolio_eligible"].sum()) < len(flagged)

results = lab.run_filter_rules(signals, history, prices)
assert set(results["metrics"]["filter_rule"]) == {rule.name for rule in lab.RULES}
assert not results["equity"].empty
assert "delta_excess_total_return_vs_baseline" in results["metrics"].columns
assert "improvement_status" in results["metrics"].columns
baseline = results["metrics"][results["metrics"]["filter_rule"] == "baseline"].iloc[0]
assert baseline["improvement_status"] == "BASELINE"
assert int(baseline["eligible_signal_count"]) == len(signals)
assert (results["metrics"]["eligible_signal_count"] <= len(signals)).all()
assert not results["eligibility_audit"].empty

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    provenance = root / "provenance.json"
    provenance.write_text(json.dumps({
        "evidence_origin": "HISTORICAL_CURRENT_UNIVERSE_BACKFILL",
        "promotion_evidence_allowed": False,
    }), encoding="utf-8")
    output = lab.write_outputs(results, str(provenance), str(root / "lab"))
    for path in output["paths"].values():
        assert Path(path).exists(), path
    manifest = output["manifest"]
    assert manifest["promotion_evidence_allowed"] is False
    assert manifest["automatic_strategy_change"] is False
    assert manifest["automatic_filter_activation"] is False
    assert manifest["same_day_close_entry_allowed"] is False
    workbook = pd.ExcelFile(output["paths"]["excel"])
    assert {
        "Lab Summary", "Filter Metrics", "Trades", "Equity",
        "Skipped Entries", "Eligibility Audit", "Context Coverage",
    }.issubset(workbook.sheet_names)

print("portfolio filter lab validation passed")
