from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import historical_backfill as backfill


members = [
    backfill.UniverseMember("1001", "Alpha", "プライム（内国株式）", "電気機器"),
    backfill.UniverseMember("1002", "Beta", "プライム（内国株式）", "電気機器"),
    backfill.UniverseMember("2001", "Gamma", "スタンダード（内国株式）", "銀行業"),
    backfill.UniverseMember("3001", "Delta", "グロース（内国株式）", "情報・通信業"),
]
limited = backfill.stratified_limit(members, 3)
assert len(limited) == 3
assert len({member.sector33 for member in limited}) == 3

index = pd.bdate_range("2025-01-06", periods=100)
prices = {}
for member_index, member in enumerate(members):
    base = 100 + member_index * 10
    trend = np.linspace(0, 30 + member_index * 5, len(index))
    close = base + trend
    prices[member.code] = pd.DataFrame({
        "Date": index,
        "Open": close - 0.5,
        "High": close + 1.0,
        "Low": close - 1.0,
        "Close": close,
        "Volume": np.full(len(index), 2_000_000 + member_index * 100_000),
        "RawClose": close,
    })

config = {
    "market": {
        "include_markets": ["Prime", "Standard", "Growth"],
        "min_trading_value": 100_000_000,
    }
}
history, coverage = backfill.build_historical_ranking(
    members,
    prices,
    config,
    sample_every=5,
    minimum_coverage_ratio=0.70,
    top_limit=2,
)
assert not history.empty
assert history["date"].nunique() >= 5
assert not history.duplicated(["date", "code"]).any()
assert set(history["code"]) == {member.code for member in members}
assert history.groupby("date")["rank"].min().eq(1).all()
assert history.groupby("date")["is_top100"].sum().eq(2).all()
assert {"is_new_entry", "rank_change", "top30_streak", "score", "trading_value"}.issubset(history.columns)
assert not coverage.empty
assert coverage["ranked_count"].eq(4).all()

quality = backfill.data_quality_table(members, prices)
assert set(quality["status"]) == {"OK"}
assert quality["row_count"].eq(100).all()

single_member = [members[0]]
raw_index = pd.bdate_range("2026-01-05", periods=3)
raw = pd.DataFrame({
    "Open": [100.0, 102.0, 104.0],
    "High": [101.0, 103.0, 105.0],
    "Low": [99.0, 101.0, 103.0],
    "Close": [100.0, 102.0, 104.0],
    "Adj Close": [50.0, 51.0, 52.0],
    "Volume": [1000, 1100, 1200],
}, index=raw_index)
raw.index.name = "Date"
normalized = backfill.normalize_downloaded_prices(raw, single_member)
assert "1001" in normalized
normalized_frame = normalized["1001"]
assert np.isclose(normalized_frame.iloc[-1]["Close"], 52.0)
assert np.isclose(normalized_frame.iloc[-1]["RawClose"], 104.0)

with TemporaryDirectory() as temporary:
    result = backfill.write_outputs(
        history,
        coverage,
        quality,
        [],
        temporary,
        universe_count=4000,
        selected_count=4,
        start=date(2025, 1, 1),
        end=date(2026, 1, 1),
        sample_every=5,
        cache_hash="abc123",
    )
    for path in result["paths"].values():
        assert Path(path).exists(), path
    manifest = result["manifest"]
    assert manifest["research_only"] is True
    assert manifest["promotion_evidence_allowed"] is False
    assert manifest["production_state_mutation_allowed"] is False
    assert manifest["universe_bias"] == "CURRENT_LIST_ONLY_SURVIVORSHIP_AND_DELISTING_BIAS"
    assert manifest["ranking_date_count"] == history["date"].nunique()
    workbook = pd.ExcelFile(result["paths"]["excel"])
    assert {"Backfill Summary", "Coverage", "Data Quality", "Errors", "Ranking Sample"}.issubset(workbook.sheet_names)

print("historical backfill validation passed")
