from __future__ import annotations

from datetime import date
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


members = [
    backfill.UniverseMember("1001", "Alpha", "Prime", "電気機器"),
    backfill.UniverseMember("1002", "Beta", "Prime", "電気機器"),
    backfill.UniverseMember("2001", "Gamma", "Prime", "銀行業"),
    backfill.UniverseMember("2002", "Delta", "Prime", "銀行業"),
    backfill.UniverseMember("3001", "Epsilon", "Standard", "機械"),
    backfill.UniverseMember("3002", "Zeta", "Standard", "機械"),
]

index = pd.bdate_range("2025-01-06", periods=150)
prices: dict[str, pd.DataFrame] = {}
for member_index, member in enumerate(members):
    base = 100.0 + member_index * 5.0
    # Deliberately create a cross-section with persistent leaders, laggards and
    # a late acceleration so lifecycle states are based only on prior snapshots.
    slope = [0.70, 0.45, 0.30, 0.12, -0.02, -0.18][member_index]
    trend = np.arange(len(index), dtype=float) * slope
    if member.code == "2002":
        trend += np.where(np.arange(len(index)) >= 105, (np.arange(len(index)) - 104) * 0.75, 0.0)
    close = np.maximum(base + trend, 10.0)
    volume = np.full(len(index), 2_000_000 + member_index * 150_000)
    prices[member.code] = pd.DataFrame({
        "Date": index,
        "Open": close * 0.998,
        "High": close * 1.012,
        "Low": close * 0.988,
        "Close": close,
        "Volume": volume,
        "RawClose": close,
    })

config = {"market": {"min_trading_value": 100_000_000}}
history, coverage = backfill.build_historical_ranking(
    members,
    prices,
    config,
    sample_every=5,
    minimum_coverage_ratio=0.70,
    top_limit=4,
)

assert not history.empty
assert history["date"].nunique() >= 10
assert not history.duplicated(["date", "code"]).any()
assert pd.to_numeric(history["relative_strength_score"], errors="coerce").notna().all()
assert pd.to_numeric(history["relative_strength_rank"], errors="coerce").notna().all()
assert history.groupby("date")["relative_strength_rank"].min().eq(1).all()
assert history["relative_strength_grade"].fillna("").ne("").all()
assert history["relative_strength_grade"].nunique() >= 3
assert history["relative_strength_lifecycle"].fillna("").ne("").all()
assert history["relative_strength_alert"].fillna("").ne("").all()
assert pd.to_numeric(history["relative_strength_trajectory_score"], errors="coerce").between(0, 100).all()

first_date = sorted(history["date"].unique())[0]
first = history[history["date"] == first_date]
assert set(first["relative_strength_lifecycle"]) == {"初登場"}

later = history[history["date"] != first_date]
assert later["previous_relative_strength_date"].fillna("").ne("").all()
assert later["relative_strength_score_delta"].notna().all()
assert later["relative_strength_rank_change"].notna().all()
assert later["relative_strength_lifecycle"].isin({
    "急加速", "再浮上", "加速", "主導継続", "主導", "継続",
    "失速警戒", "崩れ", "低位",
}).all()
assert later["relative_strength_lifecycle"].isin({"主導継続", "主導", "加速", "急加速", "再浮上"}).any()
assert history["dual_outperformer"].fillna(False).astype(bool).any()
assert not coverage.empty

with TemporaryDirectory() as temporary:
    quality = backfill.data_quality_table(members, prices)
    output = backfill.write_outputs(
        history,
        coverage,
        quality,
        [],
        temporary,
        universe_count=len(members),
        selected_count=len(members),
        start=date(2025, 1, 1),
        end=date(2026, 1, 1),
        sample_every=5,
        cache_hash="synthetic",
    )
    manifest = output["manifest"]
    assert manifest["backfill_version"] == "2026-07-11-historical-relative-strength-v2"
    assert manifest["relative_strength_enabled"] is True
    assert manifest["relative_strength_non_null_ratio"] == 1.0
    assert manifest["relative_strength_grade_count"] >= 3
    assert manifest["relative_strength_lifecycle_enabled"] is True
    assert manifest["relative_strength_lifecycle_non_null_ratio"] == 1.0
    assert manifest["promotion_evidence_allowed"] is False
    stored = json.loads(Path(output["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert stored["relative_strength_non_null_ratio"] == 1.0

print("historical relative strength validation passed")
