from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import main
import relative_strength_evidence as evidence
import replay


def synthetic_history() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-06", periods=45)
    codes = [f"{1000 + index:04d}" for index in range(40)]
    sectors = ["電気機器", "機械", "情報・通信業", "化学", "サービス業", "卸売業"]
    rows = []
    for index, code in enumerate(codes):
        strength = index / (len(codes) - 1)
        drift = -0.0015 + strength * 0.0045
        sector = sectors[index % len(sectors)]
        for day_index, date in enumerate(dates):
            deterministic_noise = 1 + 0.002 * np.sin((day_index + index) / 5)
            close = 100 * ((1 + drift) ** day_index) * deterministic_noise
            relative_input = -0.25 + strength * 0.55 + 0.003 * np.sin(day_index / 4)
            rows.append({
                "date": date.date().isoformat(),
                "price_date": date.date().isoformat(),
                "rank": len(codes) - index,
                "code": code,
                "name": f"銘柄{code}",
                "sector33": sector,
                "close": close,
                "score": 50 + strength * 45,
                "return_5d": relative_input * 0.25,
                "return_20d": relative_input,
                "return_60d": relative_input * 1.4,
                "volume_ratio": 1 + strength * 3,
                "trading_value": 100_000_000 + strength * 5_000_000_000,
                "ytd_high_flag": strength >= 0.65,
                "above_ma20": strength >= 0.40,
                "above_ma60": strength >= 0.35,
                "is_top100": True,
                "is_new_entry": False,
                "is_rising_fast": False,
                "is_best_rank": False,
            })
    return pd.DataFrame(rows)


history = synthetic_history()
signals = evidence.build_signal_panel(history, top_limit=100)
assert not signals.empty
assert set(signals["relative_strength_grade"].dropna()) >= {"S", "A", "B", "C"}
assert "D10" in set(signals["relative_strength_decile"])
assert "D1" in set(signals["relative_strength_decile"])
assert signals.groupby("signal_date").size().min() == 40

outcomes = evidence.build_forward_outcomes(signals, history)
assert not outcomes.empty
assert set(outcomes["horizon_days"].unique()) == {5, 10, 20}
assert outcomes["market_excess_return"].notna().mean() >= 0.99
assert outcomes["sector_excess_return"].notna().mean() >= 0.99
assert (pd.to_datetime(outcomes["exit_price_date"]) > pd.to_datetime(outcomes["entry_price_date"])).all()

performance = evidence.build_bucket_performance(outcomes)
assert not performance.empty
ten_day_deciles = performance[
    (performance["group_type"] == "decile")
    & (performance["horizon_days"] == 10)
]
d10 = ten_day_deciles[ten_day_deciles["group_value"] == "D10"].iloc[0]
d1 = ten_day_deciles[ten_day_deciles["group_value"] == "D1"].iloc[0]
assert d10["average_market_excess"] > d1["average_market_excess"]
assert d10["average_sector_excess"] > d1["average_sector_excess"]

daily_ic = evidence.build_daily_information_coefficients(outcomes)
ic_summary = evidence.build_ic_summary(daily_ic)
market_ic_10 = ic_summary[
    (ic_summary["target"] == "market_excess")
    & (ic_summary["horizon_days"] == 10)
].iloc[0]
sector_ic_10 = ic_summary[
    (ic_summary["target"] == "sector_excess")
    & (ic_summary["horizon_days"] == 10)
].iloc[0]
assert market_ic_10["mean_ic"] > 0
assert sector_ic_10["mean_ic"] > 0

monotonicity = evidence.build_monotonicity(performance)
ten_day_monotonicity = monotonicity[
    monotonicity["horizon_days"] == 10
].iloc[0]
assert ten_day_monotonicity["d10_minus_d1_market_excess"] > 0
assert ten_day_monotonicity["d10_minus_d1_sector_excess"] > 0

stability = evidence.build_rank_stability(signals)
assert len(stability) == history["date"].nunique() - 1
assert stability["score_spearman"].dropna().mean() > 0.95
assert stability["top_n_retention_rate"].dropna().mean() > 0.90

robustness = evidence.build_robustness(outcomes)
assert not robustness.empty
dual_ten = robustness[
    (robustness["group_type"] == "dual_outperformer")
    & (robustness["group_value"] == "True")
    & (robustness["horizon_days"] == 10)
]
assert not dual_ten.empty
assert dual_ten.iloc[0]["net_average_market_excess"] > 0
assert dual_ten.iloc[0]["net_average_sector_excess"] > 0

outputs, readiness_summary = evidence.run_evidence_lab(
    history,
    registry_path="research/experiment_registry.yaml",
    top_limit=100,
)
assert outputs["signals"].shape[0] == signals.shape[0]
assert readiness_summary["experiment_id"] == evidence.EXPERIMENT_ID
assert readiness_summary["automatic_promotion"] is False
assert readiness_summary["production_change_authorized"] is False

before = replay.live_state_hashes()
with tempfile.TemporaryDirectory() as temporary_directory:
    result = evidence.write_outputs(
        outputs,
        readiness_summary,
        temporary_directory,
        "synthetic-history.csv",
        history,
        before,
        after_hashes=replay.live_state_hashes(),
    )
    manifest = result["manifest"]
    assert manifest["research_only"] is True
    assert manifest["automatic_promotion"] is False
    assert manifest["production_change_authorized"] is False
    assert manifest["live_state_unchanged"] is True
    assert manifest["benchmark_coverage"] >= 0.99

    excel_path = Path(result["paths"]["excel"])
    assert excel_path.exists()
    workbook = pd.ExcelFile(excel_path)
    expected_sheets = {
        "Evidence Summary", "Signals", "Outcomes", "Bucket Performance",
        "Daily IC", "IC Summary", "Monotonicity", "Rank Stability",
        "Robustness", "Experiment Readiness", "Methodology",
    }
    assert expected_sheets.issubset(set(workbook.sheet_names))

    manifest_path = Path(result["paths"]["manifest"])
    loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert loaded_manifest["evidence_version"] == evidence.EVIDENCE_VERSION

with tempfile.TemporaryDirectory() as temporary_directory:
    missing = Path(temporary_directory) / "missing.csv"
    assert evidence.load_history(str(missing), "missing-jpx.csv").empty

assert evidence.parse_horizons("20,5,10,10") == (5, 10, 20)
print("relative strength evidence tests passed")
