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
import portfolio_regime_attribution as attribution


members: list[backfill.UniverseMember] = []
sectors = ("電気機器", "銀行業", "機械", "情報・通信業")
for sector_index, sector in enumerate(sectors):
    for stock_index in range(4):
        code = f"{sector_index + 1}{stock_index + 1:03d}"
        members.append(backfill.UniverseMember(code, f"Stock {code}", "Prime", sector))

index = pd.bdate_range("2024-01-04", periods=300)
price_frames: dict[str, pd.DataFrame] = {}
for member_index, member in enumerate(members):
    base = 80.0 + member_index * 4.0
    slope = 0.38 - member_index * 0.03
    wave = np.sin(np.arange(len(index)) / (7 + member_index % 5)) * (2.0 + member_index % 4)
    # The second half changes leadership to force different regimes and sectors.
    shift = np.where(
        np.arange(len(index)) > 165,
        (member_index % 4 - 1.5) * 0.12 * (np.arange(len(index)) - 165),
        0.0,
    )
    close = np.maximum(base + np.arange(len(index)) * slope + wave + shift, 12.0)
    volume = np.full(len(index), 7_000_000 + member_index * 220_000)
    price_frames[member.code] = pd.DataFrame({
        "Date": index,
        "Open": close * (0.997 + (member_index % 3) * 0.001),
        "High": close * 1.024,
        "Low": close * 0.976,
        "Close": close,
        "Volume": volume,
        "RawClose": close,
    })

history, coverage = backfill.build_historical_ranking(
    members,
    price_frames,
    {"market": {"min_trading_value": 100_000_000}},
    sample_every=5,
    minimum_coverage_ratio=0.70,
    top_limit=12,
)
assert not history.empty
assert history["date"].nunique() > 20
assert not coverage.empty

signal_rows: list[dict[str, object]] = []
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

price_rows: list[dict[str, object]] = []
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

results = attribution.run_attribution(signals, history, prices)
assert not results["baseline_metrics"].empty
assert not results["trades"].empty
assert not results["trade_attribution"].empty
assert not results["daily_equity"].empty
assert not results["daily_regime_attribution"].empty
assert not results["quarterly_stability"].empty
assert not results["rolling_stability"].empty
assert not results["counterfactuals"].empty
assert results["context_coverage"].iloc[0]["relative_strength_score_coverage"] == 1.0
assert results["context_coverage"].iloc[0]["market_regime_coverage"] == 1.0

trade_dimensions = set(results["trade_attribution"]["dimension"])
assert {"overall", "market_regime", "sector33", "relative_strength_lifecycle", "exit_reason"}.issubset(trade_dimensions)
assert results["trade_attribution"]["trade_count"].ge(0).all()
assert results["daily_regime_attribution"]["session_count"].gt(0).all()
assert results["quarterly_stability"]["session_count"].gt(0).all()
assert results["rolling_stability"]["session_count"].eq(attribution.ROLLING_SESSIONS).all()

counterfactuals = results["counterfactuals"]
assert "baseline" in set(counterfactuals["counterfactual"])
assert "risk_on_entries" in set(counterfactuals["counterfactual"])
assert "non_weak_entries" in set(counterfactuals["counterfactual"])
assert counterfactuals["counterfactual"].str.startswith("exclude_sector_").any()
assert counterfactuals["counterfactual"].str.startswith("exclude_regime_").any()
assert set(counterfactuals["diagnostic_status"]).issubset({
    "BASELINE", "RETURN_AND_DD_IMPROVED", "NO_CLEAR_IMPROVEMENT",
})
assert (counterfactuals["eligible_signal_count"] <= counterfactuals["total_signal_count"]).all()

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    provenance = root / "provenance.json"
    provenance.write_text(json.dumps({
        "evidence_origin": "HISTORICAL_CURRENT_UNIVERSE_BACKFILL",
        "promotion_evidence_allowed": False,
    }), encoding="utf-8")
    output = attribution.write_outputs(results, str(provenance), str(root / "attribution"))
    for path in output["paths"].values():
        assert Path(path).exists(), path
    manifest = output["manifest"]
    assert manifest["promotion_evidence_allowed"] is False
    assert manifest["automatic_regime_filter_activation"] is False
    assert manifest["automatic_sector_exclusion"] is False
    assert manifest["automatic_strategy_change"] is False
    assert manifest["same_day_close_entry_allowed"] is False
    workbook = pd.ExcelFile(output["paths"]["excel"])
    assert {
        "Manifest", "Summary", "Trades", "Trade Attribution", "Daily Regime",
        "Quarterly", "Rolling 63D", "Counterfactuals", "Context Coverage",
    }.issubset(workbook.sheet_names)

print("portfolio regime attribution validation passed")
