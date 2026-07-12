from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCS = {
    "docs/PROJECT_CHARTER.md",
    "docs/ROADMAP.md",
    "docs/ARCHITECTURE.md",
    "docs/OPERATIONS_RUNBOOK.md",
    "docs/DATA_DICTIONARY.md",
    "docs/KPI_DICTIONARY.md",
}

PRODUCTION_STATE_PATHS = {
    "data/momentum_daily_ranking.csv",
    "data/market_temperature.csv",
    "data/sector_leader_signal_history.csv",
    "data/paper_portfolio.csv",
    "data/paper_trade_history.csv",
    "data/paper_equity_history.csv",
    "data/execution_audit.csv",
    "data/operations_heartbeat.json",
    "data/strategy_fingerprint.json",
    "data/state_snapshots",
    "data/jpx_list_cache.csv",
}


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


for relative_path in sorted(REQUIRED_DOCS):
    assert (ROOT / relative_path).is_file(), f"missing required document: {relative_path}"

readme = text("README.md")
for relative_path in sorted(REQUIRED_DOCS):
    assert relative_path in readme, f"README does not link {relative_path}"

assert "daily_runner.py" in readme, "README must name the production entrypoint"
assert "メール上位30件" in readme or "上位30件" in readme
assert "Momentum Top10" not in readme, "obsolete Top10 wording returned"
assert "自動売買" in readme
assert "automatic weight change: disabled" in readme
assert "HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE" in readme

config = yaml.safe_load(text("config.yaml"))
assert config["ranking"]["buy_candidate_limit"] == 100
assert config["ranking"]["email_top_n"] == 30
assert config["market"]["exclude_etf"] is True
assert config["market"]["exclude_reit"] is True

workflow_text = text(".github/workflows/daily.yml")
workflow = yaml.safe_load(workflow_text)
assert workflow["permissions"]["contents"] == "write"
assert "python daily_runner.py" in workflow_text
assert "python main.py" not in workflow_text
assert "MOMENTUM_MAX_SYMBOLS" in workflow_text
assert "operations.py heartbeat" in workflow_text
assert "evidence_provenance.py stamp-live" in workflow_text
assert "state_recovery.py seal" in workflow_text

for state_path in sorted(PRODUCTION_STATE_PATHS):
    assert state_path in workflow_text, f"daily persistence is missing documented state: {state_path}"

architecture = text("docs/ARCHITECTURE.md")
data_dictionary = text("docs/DATA_DICTIONARY.md")
runbook = text("docs/OPERATIONS_RUNBOOK.md")
for state_path in sorted(PRODUCTION_STATE_PATHS):
    assert state_path in architecture, f"architecture missing state path: {state_path}"
    assert state_path in data_dictionary, f"data dictionary missing state path: {state_path}"
assert "max_symbols=0" in runbook
assert "Do not force persistence" in runbook

catalog = yaml.safe_load(text("research/evidence_catalog.yaml"))
subject = catalog["subject"]
assert subject["current_production_weight_points"] == 15
assert subject["current_decision"] == "HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE"
assert subject["historical_consensus"] == "CONFLICTED_TIME_UNSTABLE"
assert subject["governing_study_id"] == "volume-component-forward-evidence-v1"
assert subject["promotion_evidence_allowed"] is False
assert subject["automatic_weight_change_allowed"] is False
assert subject["automatic_strategy_change_allowed"] is False
assert subject["manual_review_required"] is True

forward_status = json.loads(text("data/volume_component_forward_status.json"))
assert forward_status["study_id"] == "volume-component-forward-evidence-v1"
assert forward_status["eligible_signal_date_from"] == "2026-07-13"
assert forward_status["entry_model"] == "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"
assert forward_status["same_day_close_entry_allowed"] is False
assert forward_status["automatic_weight_change"] is False
assert forward_status["automatic_strategy_change"] is False
assert forward_status["manual_review_required"] is True
assert forward_status["production_state_mutations"] == []

roadmap = text("docs/ROADMAP.md")
charter = text("docs/PROJECT_CHARTER.md")
kpis = text("docs/KPI_DICTIONARY.md")
assert "New score components and weight optimization | 0%" in roadmap
assert "Current development policy" in charter
assert "Daily workflow success rate" in kpis
assert "Stale-price false acceptance" in kpis
assert "Automatic strategy or weight changes" in kpis

print("documentation contract validation passed")
