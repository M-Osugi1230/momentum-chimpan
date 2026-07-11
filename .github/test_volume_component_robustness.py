from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import historical_backfill as backfill
import volume_component_robustness as robustness


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    cache_path = root / "jpx.csv"
    config_path = root / "config.yaml"
    registry_path = root / "registry.yaml"

    sectors = ("電気機器", "銀行業", "機械", "情報・通信業", "化学", "小売業")
    rows = []
    for sector_index, sector in enumerate(sectors):
        for stock_index in range(6):
            code = f"{sector_index + 1}{stock_index + 1:03d}"
            rows.append({
                "コード": code,
                "銘柄名": f"Stock {code}",
                "市場・商品区分": "Prime",
                "33業種区分": sector,
            })
    pd.DataFrame(rows).to_csv(cache_path, index=False)
    config_path.write_text(yaml.safe_dump({
        "market": {"include_markets": ["Prime"], "min_trading_value": 100_000_000}
    }, allow_unicode=True), encoding="utf-8")
    registry = {
        "validation_gate": {
            "minimum_evaluable_folds": 3,
            "minimum_full_trades_per_fold": 1,
            "minimum_subperiod_trades_per_fold": 1,
            "minimum_harm_direction_fraction": 0.75,
            "maximum_two_sided_p_value": 0.05,
            "require_aggregate_ci_high_below_zero": True,
            "require_median_delta_excess_below_zero": True,
        },
        "governance": {
            "promotion_evidence_allowed": False,
            "automatic_weight_change": False,
        },
    }
    registry_path.write_text(yaml.safe_dump(registry, allow_unicode=True), encoding="utf-8")

    fold_root = root / "folds"
    prepared = robustness.prepare_folds(
        str(cache_path), str(config_path), str(fold_root), fold_count=3, symbols_per_fold=8
    )
    assert prepared["manifest"]["fold_count"] == 3
    assert prepared["manifest"]["selected_symbol_count"] == 24
    assert prepared["manifest"]["cross_fold_overlap_count"] == 0
    assert len(prepared["fold_index"]) == 3
    assert prepared["fold_index"]["symbol_count"].eq(8).all()
    overlap = prepared["overlap_matrix"]
    assert overlap.loc[overlap["left_fold"] != overlap["right_fold"], "overlap_count"].eq(0).all()
    all_fold_codes: set[str] = set()
    for fold_id in prepared["fold_index"]["fold_id"]:
        manifest = json.loads((fold_root / fold_id / "fold_manifest.json").read_text(encoding="utf-8"))
        assert manifest["symbol_count"] == 8
        assert len(manifest["codes_sha256"]) == 64
        assert not (all_fold_codes & set(manifest["codes"]))
        all_fold_codes.update(manifest["codes"])

    # Run one small end-to-end fold analysis on deterministic synthetic prices.
    members = [
        backfill.UniverseMember(f"9{index + 1:03d}", f"Synthetic {index}", "Prime", sectors[index % len(sectors)])
        for index in range(18)
    ]
    dates = pd.bdate_range("2024-01-04", periods=280)
    price_frames: dict[str, pd.DataFrame] = {}
    for member_index, member in enumerate(members):
        base = 70.0 + member_index * 3.0
        trend = 0.16 + (member_index % 5) * 0.025
        wave = np.sin(np.arange(len(dates)) / (7 + member_index % 4)) * (1.8 + member_index % 3)
        close = np.maximum(base + np.arange(len(dates)) * trend + wave, 10.0)
        volume = np.full(len(dates), 4_000_000 + member_index * 120_000, dtype=float)
        if member_index % 3 == 0:
            volume[150:] *= 2.2
        price_frames[member.code] = pd.DataFrame({
            "Date": dates,
            "Open": close * 0.999,
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
        top_limit=15,
    )
    assert not history.empty
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
    fold_results = robustness.analyze_fold_frames(history, prices, "fold_test", registry, top_limit=15)
    assert len(fold_results["summary"]) == 1
    fold_summary = fold_results["summary"].iloc[0]
    assert bool(fold_summary["distribution_preserved"])
    assert int(fold_summary["baseline_lookahead_violations"]) == 0
    assert int(fold_summary["tested_lookahead_violations"]) == 0
    assert {"two_sided_p_value", "improvement_p_value", "harm_p_value"}.issubset(
        fold_results["summary"].columns
    )
    assert set(fold_results["period_metrics"]["variant"]) == {
        robustness.BASELINE_VARIANT,
        robustness.TEST_VARIANT,
    }
    assert set(fold_results["period_metrics"]["period"]) == {"full", "early", "late"}
    assert fold_results["distribution_audit"]["score_multiset_equal"].all()

    provenance_path = root / "provenance.json"
    provenance_path.write_text(json.dumps({
        "evidence_origin": "HISTORICAL_CURRENT_UNIVERSE_BACKFILL",
        "promotion_evidence_allowed": False,
    }), encoding="utf-8")
    fold_manifest_path = root / "fold_manifest.json"
    fold_manifest_path.write_text(json.dumps({
        "fold_id": "fold_test",
        "codes_sha256": "a" * 64,
    }), encoding="utf-8")
    fold_output = robustness.write_fold_outputs(
        fold_results, str(root / "fold_output"), str(provenance_path), str(fold_manifest_path)
    )
    assert fold_output["manifest"]["daily_score_distribution_preserved"] is True
    assert fold_output["manifest"]["promotion_evidence_allowed"] is False
    assert fold_output["manifest"]["automatic_weight_change"] is False
    for path in fold_output["paths"].values():
        assert Path(path).exists(), path

    # Validate aggregate inference with deliberately harmful tested returns.
    aggregate_root = root / "aggregate_folds"
    aggregate_dates = pd.bdate_range("2025-01-06", periods=120)
    for fold_index in range(3):
        analysis = aggregate_root / f"fold_{fold_index + 1:02d}" / "analysis"
        analysis.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{
            "fold_id": f"fold_{fold_index + 1:02d}",
            "sample_adequate": True,
            "delta_excess_return": -0.04 - fold_index * 0.005,
            "delta_max_drawdown": -0.01,
            "early_delta_excess": -0.01,
            "late_delta_excess": -0.02,
        }]).to_csv(analysis / "volume_fold_summary.csv", index=False)
        equity_rows = []
        for date_index, date in enumerate(aggregate_dates):
            baseline_return = 0.001 + np.sin(date_index / 9) * 0.0002
            tested_return = baseline_return - 0.0015 - fold_index * 0.00005
            equity_rows.extend([
                {
                    "date": date,
                    "daily_return": baseline_return,
                    "fold_id": f"fold_{fold_index + 1:02d}",
                    "variant": robustness.BASELINE_VARIANT,
                    "period": "full",
                },
                {
                    "date": date,
                    "daily_return": tested_return,
                    "fold_id": f"fold_{fold_index + 1:02d}",
                    "variant": robustness.TEST_VARIANT,
                    "period": "full",
                },
            ])
        pd.DataFrame(equity_rows).to_csv(analysis / "volume_fold_equity.csv", index=False)

    aggregate = robustness.aggregate_folds(str(aggregate_root), registry)
    aggregate_summary = aggregate["aggregate_summary"].iloc[0]
    assert aggregate_summary["evaluable_fold_count"] == 3
    assert aggregate_summary["harm_fold_count"] == 3
    assert aggregate_summary["harm_direction_fraction"] == 1.0
    assert aggregate_summary["median_delta_excess_return"] < 0
    assert aggregate_summary["ci_high"] < 0
    assert aggregate_summary["two_sided_p_value"] <= 0.05
    assert aggregate_summary["robustness_status"] == "ROBUSTLY_SUPPORTED"
    assert not bool(aggregate_summary["automatic_weight_change_allowed"])

    aggregate_output = robustness.write_aggregate_outputs(aggregate, str(root / "aggregate_output"))
    assert aggregate_output["manifest"]["promotion_evidence_allowed"] is False
    assert aggregate_output["manifest"]["automatic_weight_change"] is False
    assert aggregate_output["manifest"]["production_state_mutations"] == []
    workbook = pd.ExcelFile(aggregate_output["paths"]["excel"])
    assert {"Manifest", "Robustness Summary", "Fold Results", "Aggregate Daily"}.issubset(
        workbook.sheet_names
    )

print("volume component robustness validation passed")
