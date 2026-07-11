from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import data_source_canary as canary
import historical_backfill


members = [
    historical_backfill.UniverseMember("1001", "A1", "Prime", "電気機器"),
    historical_backfill.UniverseMember("1002", "A2", "Prime", "電気機器"),
    historical_backfill.UniverseMember("2001", "B1", "Prime", "銀行業"),
    historical_backfill.UniverseMember("2002", "B2", "Prime", "銀行業"),
    historical_backfill.UniverseMember("3001", "C1", "Prime", "機械"),
]
sample = canary.stratified_sample(members, 3)
assert [member.code for member in sample] == ["2001", "1001", "3001"]
assert len({member.sector33 for member in sample}) == 3
assert len(canary.stratified_sample(members, 99)) == len(members)


def valid_frame(latest: str = "2026-07-10") -> pd.DataFrame:
    dates = pd.bdate_range(end=latest, periods=30)
    return pd.DataFrame({
        "Date": dates,
        "Open": [100 + index for index in range(len(dates))],
        "High": [102 + index for index in range(len(dates))],
        "Low": [99 + index for index in range(len(dates))],
        "Close": [101 + index for index in range(len(dates))],
        "Volume": [1_000_000] * len(dates),
        "RawClose": [101 + index for index in range(len(dates))],
    })


today = date(2026, 7, 11)
healthy = canary.inspect_symbol(members[0], valid_frame(), today, 20, 7)
assert healthy["status"] == "PASS"
assert healthy["row_count"] == 30
assert healthy["duplicate_dates"] == 0
assert healthy["ohlc_violations"] == 0

broken = valid_frame()
broken.loc[0, "High"] = broken.loc[0, "Low"] - 1
broken.loc[1, "Close"] = 0
broken.loc[2, "Volume"] = -1
broken = pd.concat([broken, broken.iloc[[3]]], ignore_index=True)
bad = canary.inspect_symbol(members[0], broken, today, 20, 7)
assert bad["status"] == "FAIL"
assert bad["duplicate_dates"] == 1
assert bad["ohlc_violations"] >= 1
assert bad["nonpositive_close_rows"] >= 1
assert bad["negative_volume_rows"] >= 1

stale = canary.inspect_symbol(members[0], valid_frame("2026-06-20"), today, 20, 7)
assert stale["status"] == "FAIL"
assert stale["age_days"] > 7

missing = canary.inspect_symbol(members[0], None, today, 20, 7)
assert missing["status"] == "FAIL"

batch_prices = {member.code: valid_frame() for member in members[:3]}
original_download = canary.historical_backfill.download_price_history
canary.historical_backfill.download_price_history = lambda selected, start, end, batch_size=1: (
    {selected[0].code: batch_prices[selected[0].code].copy()},
    [],
)
try:
    comparisons = canary.compare_batch_single(
        members[:3], batch_prices, date(2026, 3, 1), date(2026, 7, 12), 3, 0.005
    )
finally:
    canary.historical_backfill.download_price_history = original_download
assert len(comparisons) == 3
assert set(comparisons["status"]) == {"PASS"}

mismatched_prices = {code: frame.copy() for code, frame in batch_prices.items()}
single = mismatched_prices["1001"].copy()
single.loc[single.index[-1], "Close"] *= 1.10
canary.historical_backfill.download_price_history = lambda selected, start, end, batch_size=1: (
    {selected[0].code: single if selected[0].code == "1001" else mismatched_prices[selected[0].code]},
    [],
)
try:
    mismatch = canary.compare_batch_single(
        members[:1], batch_prices, date(2026, 3, 1), date(2026, 7, 12), 1, 0.005
    )
finally:
    canary.historical_backfill.download_price_history = original_download
assert mismatch.iloc[0]["status"] == "FAIL"

status, detail = canary.overall_status(
    pd.DataFrame([healthy, healthy]),
    comparisons.head(1),
    0,
)
assert status == "PASS"
assert "coverage 100.0%" in detail
status, _ = canary.overall_status(pd.DataFrame([healthy, bad]), comparisons.head(1), 0)
assert status == "FAIL"
status, _ = canary.overall_status(pd.DataFrame([healthy, healthy]), mismatch, 0)
assert status == "FAIL"

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    output = root / "canary"
    selected = members[:3]
    synthetic = {member.code: valid_frame() for member in selected}

    original_config = canary.historical_backfill.load_config
    original_universe = canary.historical_backfill.load_current_universe
    original_download = canary.historical_backfill.download_price_history
    original_hashes = canary.replay.live_state_hashes
    canary.historical_backfill.load_config = lambda path: {"market": {}}
    canary.historical_backfill.load_current_universe = lambda cache, config: selected
    canary.historical_backfill.download_price_history = lambda selected_members, start, end, batch_size=12: (
        {member.code: synthetic[member.code].copy() for member in selected_members},
        [],
    )
    canary.replay.live_state_hashes = lambda: {"data/state.csv": "unchanged"}
    try:
        # Pin the canary's perceived date by making the synthetic latest date
        # close enough to the actual run date through a temporary wrapper.
        original_inspect = canary.inspect_symbol
        canary.inspect_symbol = lambda member, frame, run_today, minimum_rows, maximum_age_days: original_inspect(
            member, frame, date(2026, 7, 11), minimum_rows, maximum_age_days
        )
        result = canary.run_canary(
            "cache.csv",
            "config.yaml",
            str(output),
            sample_size=3,
            compare_count=2,
            minimum_rows=20,
            maximum_age_days=7,
        )
    finally:
        canary.inspect_symbol = original_inspect
        canary.historical_backfill.load_config = original_config
        canary.historical_backfill.load_current_universe = original_universe
        canary.historical_backfill.download_price_history = original_download
        canary.replay.live_state_hashes = original_hashes

    manifest = result["manifest"]
    assert manifest["status"] == "PASS"
    assert manifest["sample_size"] == 3
    assert manifest["sample_sector_count"] == 2
    assert manifest["production_state_mutations"] == []
    assert manifest["research_only"] is True
    for name in (
        "symbol_checks.csv",
        "batch_single_comparison.csv",
        "download_errors.csv",
        "canary_manifest.json",
        "data_source_canary.xlsx",
    ):
        assert (output / name).exists(), name
    workbook = pd.ExcelFile(output / "data_source_canary.xlsx")
    assert {"Canary Summary", "Symbol Checks", "Batch Single", "Download Errors"}.issubset(workbook.sheet_names)

print("external data source canary validation passed")
