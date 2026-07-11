from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import historical_backfill as backfill
import portfolio_holdout_validation as holdout


registry = holdout.load_registry("research/portfolio_holdout_hypotheses.yaml")
discovery_codes = holdout.normalize_codes(registry["discovery_design"]["codes"])
assert len(discovery_codes) == 72
assert holdout.codes_sha256(discovery_codes) == registry["discovery_design"]["codes_sha256"]
assert len(registry["hypotheses"]) == 4

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    # Build a cache with discovery codes and a separate multi-sector holdout.
    rows = []
    for index, code in enumerate(discovery_codes[:8]):
        rows.append({
            "コード": code,
            "銘柄名": f"Discovery {code}",
            "市場・商品区分": "プライム（内国株式）",
            "33業種区分": "電気機器",
        })
    holdout_codes = [f"8{index:03d}" for index in range(1, 17)]
    sectors = ["電気機器", "銀行業", "空運業", "パルプ・紙"]
    for index, code in enumerate(holdout_codes):
        rows.append({
            "コード": code,
            "銘柄名": f"Holdout {code}",
            "市場・商品区分": "プライム（内国株式）",
            "33業種区分": sectors[index % len(sectors)],
        })
    cache = root / "jpx.csv"
    pd.DataFrame(rows).to_csv(cache, index=False)
    config = root / "config.yaml"
    config.write_text(yaml.safe_dump({
        "market": {"include_markets": ["Prime"], "min_trading_value": 100_000_000}
    }, allow_unicode=True), encoding="utf-8")
    selected, manifest = holdout.prepare_holdout_universe(
        registry, str(cache), str(config), max_symbols=8
    )
    assert len(selected) == 8
    assert manifest["discovery_holdout_overlap_count"] == 0
    assert not set(selected["コード"]) & set(discovery_codes)
    output_cache = root / "holdout.csv"
    output_manifest = root / "holdout_manifest.json"
    holdout.write_holdout_universe(selected, manifest, str(output_cache), str(output_manifest))
    assert output_cache.exists()
    assert output_manifest.exists()
    stored_manifest = json.loads(output_manifest.read_text(encoding="utf-8"))
    assert stored_manifest["selected_holdout_symbol_count"] == 8
    assert stored_manifest["output_cache_sha256"]


members: list[backfill.UniverseMember] = []
sectors = ("電気機器", "銀行業", "空運業", "パルプ・紙")
for sector_index, sector in enumerate(sectors):
    for stock_index in range(4):
        code = f"8{sector_index + 1}{stock_index + 1:02d}"
        members.append(backfill.UniverseMember(code, f"Stock {code}", "Prime", sector))

index = pd.bdate_range("2024-01-04", periods=320)
price_frames: dict[str, pd.DataFrame] = {}
for member_index, member in enumerate(members):
    base = 80.0 + member_index * 3.0
    sector = member.sector33
    slope = 0.42 - member_index * 0.02
    if sector == "空運業":
        slope -= 0.22
    if sector == "パルプ・紙":
        slope -= 0.16
    wave = np.sin(np.arange(len(index)) / (7 + member_index % 4)) * 2.5
    # Some late accelerations create 急加速 observations with mixed outcomes.
    acceleration = np.where(
        (np.arange(len(index)) > 180) & (member_index % 3 == 0),
        (np.arange(len(index)) - 180) * 0.10,
        0.0,
    )
    close = np.maximum(base + np.arange(len(index)) * slope + wave + acceleration, 10.0)
    volume = np.full(len(index), 8_000_000 + member_index * 200_000)
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

synthetic_registry = json.loads(json.dumps(registry))
synthetic_registry["holdout_design"].update({
    "minimum_baseline_trades": 8,
    "minimum_variant_trades": 5,
    "minimum_subperiod_trades": 2,
    "block_bootstrap_length": 3,
    "bootstrap_iterations": 200,
    "maximum_fdr_q_value": 0.10,
})
results = holdout.evaluate_holdout(synthetic_registry, signals, history, prices)
summary = results["hypothesis_summary"]
assert len(summary) == 4
assert set(summary["hypothesis_id"]) == {row["id"] for row in registry["hypotheses"]}
assert set(summary["validation_status"]).issubset({
    "VALIDATED", "DIRECTIONALLY_SUPPORTED", "REJECTED", "INSUFFICIENT",
})
assert summary["automatic_activation_allowed"].eq(False).all()
assert "fdr_q_value" in summary.columns
assert "early_delta_excess" in summary.columns
assert "late_delta_excess" in summary.columns
assert set(results["period_metrics"]["period"]) == {"full", "early", "late"}
assert len(results["period_metrics"]) == 15  # baseline plus four hypotheses, each over three periods
assert results["context_coverage"].iloc[0]["relative_strength_score_coverage"] == 1.0
assert results["universe_coverage"].iloc[0]["unique_signal_codes"] > 0

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    provenance = root / "provenance.json"
    provenance.write_text(json.dumps({
        "evidence_origin": "HISTORICAL_CURRENT_UNIVERSE_BACKFILL",
        "promotion_evidence_allowed": False,
    }), encoding="utf-8")
    universe_manifest = root / "universe.json"
    universe_manifest.write_text(json.dumps({
        "discovery_codes_sha256": registry["discovery_design"]["codes_sha256"],
        "selected_holdout_codes_sha256": "synthetic-holdout",
        "discovery_holdout_overlap_count": 0,
    }), encoding="utf-8")
    output = holdout.write_evaluation_outputs(
        synthetic_registry,
        results,
        str(provenance),
        str(universe_manifest),
        str(root / "evaluation"),
    )
    for path in output["paths"].values():
        assert Path(path).exists(), path
    manifest = output["manifest"]
    assert manifest["promotion_evidence_allowed"] is False
    assert manifest["automatic_hypothesis_activation"] is False
    assert manifest["automatic_strategy_change"] is False
    assert manifest["discovery_holdout_overlap_count"] == 0
    workbook = pd.ExcelFile(output["paths"]["excel"])
    assert {
        "Manifest", "Hypothesis Summary", "Period Metrics", "Baseline",
        "Trades", "Equity", "Context Coverage", "Universe Coverage",
    }.issubset(workbook.sheet_names)

# BH adjustment must be monotonic in sorted p-values.
q_values = holdout.bh_q_values([0.001, 0.02, 0.04, None])
assert q_values[0] <= q_values[1] <= q_values[2]
assert q_values[3] is None

print("portfolio disjoint holdout validation passed")
