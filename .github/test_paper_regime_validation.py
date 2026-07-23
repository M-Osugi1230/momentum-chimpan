from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import paper_regime_validation as validation


policy = validation.load_policy(ROOT / validation.POLICY_PATH)
validation.validate_policy(policy)
regime_targets, health_multipliers = validation.target_maps(policy)
assert regime_targets == {
    "強気": 0.80,
    "やや強気": 0.65,
    "中立": 0.45,
    "弱気": 0.20,
    "過熱警戒": 0.30,
}
assert health_multipliers == {"PASS": 1.0, "WARN": 0.5, "FAIL": 0.0}
assert policy["operational_health"]["FAIL"]["new_entries_allowed"] is False
assert policy["governance"]["production_state_mutations"] == []
assert policy["governance"]["automatic_paper_rule_change"] is False
assert policy["governance"]["live_orders"] is False

# Keep the registered validation assumptions aligned with the live paper policy.
main_source = (ROOT / "main.py").read_text(encoding="utf-8")
for fragment in (
    '"強気": 0.80',
    '"やや強気": 0.65',
    '"中立": 0.45',
    '"弱気": 0.20',
    '"過熱警戒": 0.30',
    'if health == "FAIL"',
    'if health == "WARN"',
    'return round(base * 0.50, 4)',
):
    assert fragment in main_source, fragment

regimes = ["強気", "やや強気", "中立", "弱気", "過熱警戒"]
health = ["PASS", "WARN", "PASS", "PASS", "FAIL"]
dates = pd.date_range("2026-01-05", periods=5, freq="B")
equity = pd.DataFrame({
    "date": dates.date.astype(str),
    "equity": [10_000_000, 10_050_000, 10_020_000, 9_980_000, 9_970_000],
    "exposure_ratio": [0.80, 0.325, 0.45, 0.20, 0.00],
    "drawdown": [0.0, 0.0, -0.003, -0.007, -0.008],
    "open_positions": [8, 4, 5, 2, 0],
    "closed_trades": [0, 1, 2, 3, 4],
    "win_rate": [None, 1.0, 0.5, 1 / 3, 0.5],
})
market = pd.DataFrame({"date": dates.date.astype(str), "market_regime": regimes})
execution = pd.DataFrame({"date": dates.date.astype(str), "run_health": health})
trades = pd.DataFrame({
    "position_id": [f"p{i}" for i in range(5)],
    "code": [f"100{i}" for i in range(5)],
    "name": [f"Stock {i}" for i in range(5)],
    "sector33": ["電気機器", "銀行業", "小売業", "機械", "情報・通信業"],
    "entry_date": dates.date.astype(str),
    "exit_date": dates.date.astype(str),
    "exit_reason": ["TAKE_PROFIT", "STOP_LOSS", "TIME_EXIT", "SIGNAL_EXIT", "TRAILING_STOP"],
    "realized_pnl": [10_000, -5_000, 2_000, -1_000, 3_000],
    "realized_return": [0.02, -0.01, 0.004, -0.002, 0.006],
})

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    equity.to_csv(root / "equity.csv", index=False)
    trades.to_csv(root / "trades.csv", index=False)
    execution.to_csv(root / "execution.csv", index=False)
    market.to_csv(root / "market.csv", index=False)
    output = root / "output"
    status = validation.build(
        equity_path=root / "equity.csv",
        trades_path=root / "trades.csv",
        execution_path=root / "execution.csv",
        market_path=root / "market.csv",
        policy_path=ROOT / validation.POLICY_PATH,
        output_dir=output,
    )
    issues = validation.validate_output(output)
    assert issues == [], issues
    assert status["validation_status"] == "ACCUMULATING"
    assert status["missing_market_regimes"] == []
    assert status["all_market_regimes_mature"] is False
    summary = pd.read_csv(output / "paper_regime_summary.csv").set_index("market_regime")
    assert set(summary.index) == set(regimes)
    daily = pd.read_csv(output / "paper_regime_daily.csv").set_index("market_regime")
    assert abs(daily.loc["強気", "target_exposure"] - 0.80) < 1e-12
    assert abs(daily.loc["やや強気", "target_exposure"] - 0.325) < 1e-12
    assert abs(daily.loc["中立", "target_exposure"] - 0.45) < 1e-12
    assert abs(daily.loc["弱気", "target_exposure"] - 0.20) < 1e-12
    assert abs(daily.loc["過熱警戒", "target_exposure"] - 0.0) < 1e-12
    payload = json.loads((output / "paper_regime_validation.json").read_text(encoding="utf-8"))
    assert payload["research_only"] is True
    assert payload["automatic_paper_rule_change"] is False
    assert payload["production_state_mutations"] == []

# Empty live histories must still retain every registered regime as MISSING.
with TemporaryDirectory() as temporary:
    root = Path(temporary)
    for name in ("equity.csv", "trades.csv", "execution.csv", "market.csv"):
        Path(root / name).write_text("", encoding="utf-8")
    output = root / "empty-output"
    status = validation.build(
        equity_path=root / "equity.csv",
        trades_path=root / "trades.csv",
        execution_path=root / "execution.csv",
        market_path=root / "market.csv",
        policy_path=ROOT / validation.POLICY_PATH,
        output_dir=output,
    )
    assert set(status["missing_market_regimes"]) == set(regimes)
    assert validation.validate_output(output) == []

print("paper portfolio all-regime validation passed")
