from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "daily-priority-outcomes.yml"

text = WORKFLOW_PATH.read_text(encoding="utf-8")
workflow = yaml.safe_load(text)

assert workflow["permissions"]["actions"] == "read"
assert workflow["permissions"]["contents"] == "read"

publish_steps = workflow["jobs"]["publish"]["steps"]
step_names = [step.get("name", "") for step in publish_steps]

download_index = step_names.index("Download exact upstream daily artifact")
readiness_index = step_names.index("Require exact live-session readiness before ingestion")
ingest_index = step_names.index("Ingest decisions and mature available outcomes")

assert download_index < readiness_index < ingest_index

readiness_step = publish_steps[readiness_index]
assert readiness_step["if"] == "github.event_name == 'workflow_run'"
readiness_script = readiness_step["run"]

required_fragments = (
    "live_session_readiness_with_recovery.py build",
    "daily_recovery_drill.py",
    "--artifact-root downloaded-daily-report",
    "--source-run-id",
    "--source-run-url",
    "--upstream-conclusion",
    "--upstream-event",
    "--head-sha",
    "--created-at-utc",
    "--updated-at-utc",
    "live_session_readiness.json",
    "live_session_readiness_with_recovery as readiness",
    "validate_readiness(payload)",
    "readiness_state",
    "eligible_for_priority_outcome_ingestion",
    "exact_recovery_drill",
    "exact recovery PASS is required",
    "production_state_unchanged",
    "readiness_fingerprint",
    "status_sha256",
)
for fragment in required_fragments:
    assert fragment in readiness_script, fragment

assert "readiness_state') == 'FAIL'" in readiness_script
assert "eligible_for_priority_outcome_ingestion') is not True" in readiness_script
assert "live_session_readiness.py build" not in readiness_script

publish_condition = workflow["jobs"]["publish"]["if"]
assert "github.event_name == 'schedule'" in publish_condition
assert "github.event_name == 'workflow_dispatch'" in publish_condition
assert "github.event_name == 'workflow_run'" in publish_condition

upload_step = next(step for step in publish_steps if step.get("name") == "Upload outcome diagnostics")
upload_paths = upload_step["with"]["path"]
assert "output/priority-outcomes-readiness/live_session_readiness.json" in upload_paths
assert "output/priority-outcomes-readiness/live_session_readiness.md" in upload_paths
assert "/tmp/priority-outcomes-readiness.log" in upload_paths

persist_step = next(step for step in publish_steps if step.get("name") == "Persist research outcome files only")
persist_script = persist_step["run"]
assert "research/priority_outcomes/daily_research_decisions.csv" in persist_script
assert "research/priority_outcomes/daily_research_outcomes.csv" in persist_script
assert "live_session_readiness.json" not in persist_script
assert "data/momentum_daily_ranking.csv" not in persist_script
assert "config.yaml" not in persist_script

assert ("EMAIL_" + "APP_PASSWORD") not in text
assert ("contents:" + " write") not in text.split("publish:", 1)[0]

print("recovery-aware priority outcome readiness gate validation passed")
