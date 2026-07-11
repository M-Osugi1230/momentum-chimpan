from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
import os
import sys

import pandas as pd

spec = importlib.util.spec_from_file_location("momentum_main", "main.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)

assert module.APP_VERSION == "2026-07-11-dashboard-market-freshness-v17"

today = "2026-07-13"
fresh = pd.DataFrame({"price_date": [today] * 100})
partial = pd.DataFrame({"price_date": [today] * 90 + ["2026-07-10"] * 10})
stale = pd.DataFrame({"price_date": ["2026-07-10"] * 100})

fresh_result = module.evaluate_market_data_freshness(today, fresh)
assert fresh_result["status"] == "FRESH"
assert fresh_result["state_update_allowed"] is True
assert fresh_result["fresh_ratio"] == 1.0

partial_result = module.evaluate_market_data_freshness(today, partial)
assert partial_result["status"] == "PARTIAL"
assert partial_result["state_update_allowed"] is False
assert partial_result["fresh_count"] == 90

stale_result = module.evaluate_market_data_freshness(today, stale)
assert stale_result["status"] == "STALE"
assert stale_result["state_update_allowed"] is False
assert stale_result["latest_price_date"] == "2026-07-10"

empty_result = module.evaluate_market_data_freshness(today, pd.DataFrame())
assert empty_result["status"] == "EMPTY"
assert empty_result["state_update_allowed"] is False

base_health = pd.DataFrame([
    {"check_name": "overall", "status": "PASS", "actual": "PASS", "expected": "PASS", "detail": "ok"},
    {"check_name": "scan_coverage", "status": "PASS", "actual": 1.0, "expected": ">=95%", "detail": "ok"},
])
health_fresh = module.attach_market_data_freshness_health(base_health, fresh_result)
health_stale = module.attach_market_data_freshness_health(base_health, stale_result)
assert module.run_health_overall(health_fresh) == "PASS"
assert module.run_health_overall(health_stale) == "FAIL"
assert health_stale.iloc[0]["check_name"] == "overall"
assert "market_data_current_day" in set(health_stale["check_name"])

original_cwd = Path.cwd()
with TemporaryDirectory() as tmpdir:
    os.chdir(tmpdir)
    try:
        snapshot = module.load_existing_paper_state({"label": "中立"}, health_stale)
        assert snapshot["portfolio"].empty
        assert snapshot["trade_history"].empty
        assert snapshot["plan"].empty
        assert len(snapshot["performance"]) == 1
        assert not Path("data").exists(), "read-only stale path must not create state files"
    finally:
        os.chdir(original_cwd)

source = Path("main.py").read_text(encoding="utf-8")
assert 'if state_update_allowed:\n        write_ranking_history' in source
assert 'performance_history = combined_ranking_history(history, all_ranked, today) if state_update_allowed else history.copy()' in source
assert 'paper_result = load_existing_paper_state(regime, run_health)' in source
assert 'state_snapshots = pd.DataFrame(columns=STATE_SNAPSHOT_COLUMNS)' in source
assert '状態更新実行' in source

print("market data freshness validation passed")
