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
import score_component_ablation as ablation


members: list[backfill.UniverseMember] = []
sectors = ("電気機器", "銀行業", "機械", "情報・通信業")
for sector_index, sector in enumerate(sectors):
    for stock_index in range(4):
        code = f"9{sector_index + 1}{stock_index + 1:02d}"
        members.append(backfill.UniverseMember(code, f"Stock {code}", "Prime", sector))

index = pd.bdate_range("2024-01-04", periods=300)
price_frames: dict[str, pd.DataFrame] = {}
for member_index, member in enumerate(members):
    base = 70.0 + member_index * 4.0
    slope = 0.38 - member_index * 0.025
    wave = np.sin(np.arange(len(index)) / (6 + member_index % 5)) * (2.0 + member_index % 4)
    # Component characteristics differ across stocks and over time.
    close = np.maximum(base + np.arange(len(index)) * slope + wave, 12.0)
    if member_index % 4 == 0:
        close += np.where(np.arange(len(index)) > 170, (np.arange(len(index)) - 170) * 0.13, 0.0)
    volume = np.full(len(index), 7_000_000 + member_index * 180_000, dtype=float)
    if member_index % 3 == 0:
        volume[170:] *= 1.8
    price_frames[member.code] = pd.DataFrame({
        "Date": index,
        "Open": close * 0.998,
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
assert not history.empty
assert history["date"].nunique() > 20
assert set(item["column"] for item in ablation.COMPONENTS.values()).issubset(history.columns)

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

baseline_history = ablation.build_variant_history(history, "baseline", top_limit=12)
merged_baseline = history[["date", "code", "rank"]].merge(
    baseline_history[["date", "code", "rank"]],
    on=["date", "code"],
    suffixes=("_source", "_rebuilt"),
)
assert (merged_baseline["rank_source"] == merged_baseline["rank_rebuilt"]).all()

for variant in ablation.VARIANTS:
    variant_history = ablation.build_variant_history(history, variant, top_limit=12)
    audit = ablation.validate_distribution_preservation(baseline_history, variant_history)
    assert audit["score_multiset_equal"].all(), variant
    assert (audit["baseline_rows"] == audit["variant_rows"]).all()
    if variant != "baseline":
        diagnostics = ablation.rank_diagnostics(baseline_history, variant_history)
        assert diagnostics["mean_daily_rank_spearman"] is not None
        assert -1.0 <= diagnostics["mean_daily_rank_spearman"] <= 1.0

results = ablation.run_ablation(history, prices, top_limit=12)
assert set(results["summary"]["variant"]) == set(ablation.VARIANTS)
assert len(results["summary"]) == 7
assert len(results["period_metrics"]) == 21
assert set(results["period_metrics"]["period"]) == {"full", "early", "late"}
assert results["distribution_audit"]["score_multiset_equal"].all()
assert results["rank_diagnostics"]["distribution_preserved_all_dates"].all()
assert results["variant_signal_counts"]["lookahead_violations"].fillna(0).eq(0).all()
assert not results["equity"].empty
assert {"two_sided_p_value", "improvement_p_value", "harm_p_value"}.issubset(results["summary"].columns)
assert set(results["summary"]["ablation_status"]).issubset({
    "BASELINE", "REMOVAL_IMPROVES_VALIDATED", "REMOVAL_IMPROVES_DIRECTIONAL",
    "REMOVAL_HURTS_VALIDATED", "REMOVAL_HURTS_DIRECTIONAL", "MIXED", "INSUFFICIENT",
})
baseline = results["summary"][results["summary"]["variant"] == "baseline"]
assert len(baseline) == 1
assert baseline.iloc[0]["ablation_status"] == "BASELINE"
assert results["summary"][results["summary"]["variant"] != "baseline"]["automatic_weight_change_allowed"].eq(False).all()

# A pure reorder must preserve daily score histograms while changing at least
# one rank for at least one drop variant.
changed = []
for variant in ablation.VARIANTS[1:]:
    variant_history = ablation.build_variant_history(history, variant, top_limit=12)
    merged = baseline_history[["date", "code", "rank"]].merge(
        variant_history[["date", "code", "rank"]],
        on=["date", "code"], suffixes=("_baseline", "_variant"),
    )
    changed.append(bool((merged["rank_baseline"] != merged["rank_variant"]).any()))
assert any(changed)

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    history_path = root / "history.csv"
    history.to_csv(history_path, index=False)
    provenance = root / "provenance.json"
    provenance.write_text(json.dumps({
        "evidence_origin": "HISTORICAL_CURRENT_UNIVERSE_BACKFILL",
        "promotion_evidence_allowed": False,
    }), encoding="utf-8")
    output = ablation.write_outputs(results, str(provenance), str(history_path), str(root / "ablation"))
    for path in output["paths"].values():
        assert Path(path).exists(), path
    manifest = output["manifest"]
    assert manifest["daily_score_distribution_preserved"] is True
    assert manifest["automatic_weight_change"] is False
    assert manifest["automatic_component_removal"] is False
    assert manifest["promotion_evidence_allowed"] is False
    assert manifest["same_day_close_entry_allowed"] is False
    workbook = pd.ExcelFile(output["paths"]["excel"])
    assert {
        "Manifest", "Ablation Summary", "Period Metrics", "Rank Diagnostics",
        "Distribution Audit", "Signal Counts", "Trades", "Equity",
    }.issubset(workbook.sheet_names)

q_values = ablation.bh_q_values([0.001, 0.01, 0.04, None])
assert q_values[0] <= q_values[1] <= q_values[2]
assert q_values[3] is None

print("score component ablation validation passed")
