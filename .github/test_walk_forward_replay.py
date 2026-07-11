from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import replay


history = replay.prepare_history(
    "data/momentum_daily_ranking.csv",
    "data/jpx_list_cache.csv",
)
assert not history.empty
assert history["date"].nunique() >= 2
assert history["sector33"].astype(str).str.strip().ne("").mean() >= 0.90

before = replay.live_state_hashes()
result = replay.run_walk_forward_replay(
    history,
    top_limit=100,
    max_dates=min(4, history["date"].nunique()),
    source_hash=replay.sha256_file("data/momentum_daily_ranking.csv"),
)
after = replay.live_state_hashes()

assert before == after, "replay mutated live state"
assert result.manifest["research_only"] is True
assert result.manifest["live_state_mutation_allowed"] is False
assert result.manifest["lookahead_violations"] == 0
assert not result.audit.empty
assert set(result.audit["status"]) == {"PASS"}
prior_dates = pd.to_datetime(result.audit["prior_input_max_date"], errors="coerce")
signal_dates = pd.to_datetime(result.audit["signal_date"], errors="coerce")
assert (prior_dates.isna() | (prior_dates < signal_dates)).all()
assert (result.audit["future_rows_available_to_signal_generation"] == 0).all()
assert (result.coverage["sector_coverage_ratio"] >= 0.90).all()

with TemporaryDirectory() as temporary:
    paths = replay.write_replay_outputs(result, temporary)
    for path in paths.values():
        assert Path(path).exists(), path
    workbook = pd.ExcelFile(paths["excel"])
    required_sheets = {
        "Replay Summary",
        "Signals",
        "Outcomes",
        "Performance",
        "No Lookahead Audit",
        "Coverage",
    }
    assert required_sheets.issubset(workbook.sheet_names)
    manifest_text = Path(paths["manifest"]).read_text(encoding="utf-8")
    assert '"lookahead_violations": 0' in manifest_text

print("walk-forward replay validation passed")
