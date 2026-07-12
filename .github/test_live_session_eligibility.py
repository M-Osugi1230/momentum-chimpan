from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import live_session_eligibility as eligibility


def valid_record(run_id: str = "100", report_date: str = "2026-07-13") -> dict:
    return {
        "eligibility_version": eligibility.ELIGIBILITY_VERSION,
        "source_run_id": run_id,
        "source_run_url": f"https://github.com/example/actions/runs/{run_id}",
        "upstream_conclusion": "success",
        "upstream_event": "schedule",
        "head_sha": "a" * 40,
        "created_at_utc": "2026-07-13T07:45:00Z",
        "updated_at_utc": "2026-07-13T08:10:00Z",
        "recorded_at_utc": "2026-07-13T08:10:00Z",
        "report_date": report_date,
        "strategy_fingerprint": "b" * 64,
        "readiness_state": "PASS",
        "eligible_for_forward_evidence": True,
        "eligible_for_priority_outcome_ingestion": True,
        "artifact_fingerprint": "c" * 64,
        "readiness_fingerprint": "d" * 64,
        "readiness_status_sha256": "e" * 64,
        "ranking_date_row_count": 2,
        "ranking_date_sha256": "f" * 64,
        "critical_failure_count": 0,
        "review_warning_count": 0,
        "readiness_details": "",
        "research_only": True,
    }


def failed_record(run_id: str = "101") -> dict:
    record = valid_record(run_id)
    record.update({
        "report_date": "",
        "strategy_fingerprint": "",
        "readiness_state": "FAIL",
        "eligible_for_forward_evidence": False,
        "eligible_for_priority_outcome_ingestion": False,
        "ranking_date_row_count": 0,
        "ranking_date_sha256": "",
        "critical_failure_count": 2,
        "readiness_details": "required_artifacts: missing",
    })
    return record


def main() -> None:
    frame_a = pd.DataFrame([
        {"date": "2026-07-13", "code": "1002", "rank": 2, "score": 70.0},
        {"date": "2026-07-13", "code": "1001", "rank": 1, "score": 80.0},
        {"date": "2026-07-14", "code": "1001", "rank": 1, "score": 81.0},
    ])
    frame_b = frame_a.iloc[[1, 2, 0]].copy()
    payload_a = eligibility.ranking_date_payload(frame_a, "2026-07-13")
    payload_b = eligibility.ranking_date_payload(frame_b, "2026-07-13")
    assert payload_a == payload_b
    assert len(payload_a["rows"]) == 2
    assert eligibility.canonical_hash(payload_a) == eligibility.canonical_hash(payload_b)

    empty_status = eligibility.build_status(eligibility.empty_history())
    assert empty_status["ledger_state"] == "EMPTY"
    assert empty_status["run_count"] == 0
    assert eligibility.validate_status(empty_status) == []

    history = eligibility.empty_history()
    history = eligibility.append_record(history, valid_record())
    assert eligibility.validate_history(history) == []
    assert len(history) == 1

    replacement = valid_record()
    replacement["review_warning_count"] = 1
    replacement["readiness_state"] = "REVIEW_REQUIRED"
    history = eligibility.append_record(history, replacement)
    assert len(history) == 1
    assert int(history.iloc[0]["review_warning_count"]) == 1
    assert history.iloc[0]["readiness_state"] == "REVIEW_REQUIRED"

    history = eligibility.append_record(history, failed_record())
    assert len(history) == 2
    assert eligibility.validate_history(history) == []

    invalid = history.copy()
    invalid.loc[invalid["source_run_id"].eq("100"), "ranking_date_sha256"] = ""
    issues = eligibility.validate_history(invalid)
    assert any("invalid ranking_date_sha256" in issue for issue in issues)

    invalid_state = history.copy()
    invalid_state.loc[invalid_state["source_run_id"].eq("100"), "readiness_state"] = "FAIL"
    issues = eligibility.validate_history(invalid_state)
    assert any("eligible run has invalid readiness_state" in issue for issue in issues)

    status = eligibility.build_status(history)
    assert eligibility.validate_status(status) == []
    assert status["run_count"] == 2
    assert status["eligible_forward_run_count"] == 1
    assert status["eligible_forward_date_count"] == 1
    assert status["failed_readiness_run_count"] == 1
    assert status["automatic_strategy_change"] is False
    assert status["production_state_mutations"] == []

    tampered = copy.deepcopy(status)
    tampered["eligible_forward_run_count"] = 9
    assert any("status_sha256 mismatch" in issue for issue in eligibility.validate_status(tampered))

    with TemporaryDirectory() as directory:
        root = Path(directory)
        ranking_path = root / "ranking.csv"
        frame_a.to_csv(ranking_path, index=False)
        count, digest = eligibility.ranking_date_fingerprint(ranking_path, "2026-07-13")
        assert count == 2
        assert digest == eligibility.canonical_hash(payload_a)

        ledger_path = root / "ledger.csv"
        status_path = root / "status.json"
        history.to_csv(ledger_path, index=False)
        status_path.write_text(json.dumps(status), encoding="utf-8")
        result = eligibility.validate_committed(str(ledger_path), str(status_path))
        assert result["passed"] is True

    committed = eligibility.validate_committed(
        str(ROOT / eligibility.DEFAULT_LEDGER),
        str(ROOT / eligibility.DEFAULT_STATUS),
    )
    assert committed["passed"] is True
    assert committed["status"]["ledger_state"] == "EMPTY"

    workflow_path = ROOT / ".github" / "workflows" / "live-session-eligibility-ledger.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    assert workflow["permissions"]["actions"] == "read"
    assert workflow["permissions"]["contents"] == "read"
    assert "actions/download-artifact@v4" in workflow_text
    assert "run-id: ${{ github.event.workflow_run.id }}" in workflow_text
    assert "live_session_eligibility.py update" in workflow_text
    assert "--source-run-id" in workflow_text
    assert "--upstream-conclusion" in workflow_text
    assert "research/evidence/live_session_eligibility.csv" in workflow_text
    assert "research/evidence/live_session_eligibility_status.json" in workflow_text
    assert ("EMAIL_" + "APP_PASSWORD") not in workflow_text

    persistence = next(
        step for step in workflow["jobs"]["publish"]["steps"]
        if step.get("name") == "Persist eligibility research files only"
    )["run"]
    assert "git add --" in persistence
    assert "research/evidence/live_session_eligibility.csv" in persistence
    assert "research/evidence/live_session_eligibility_status.json" in persistence
    for forbidden in (
        "main.py",
        "config.yaml",
        "data/momentum_daily_ranking.csv",
        "paper_portfolio.csv",
        "research/priority_outcomes/daily_research_decisions.csv",
    ):
        assert forbidden not in persistence

    print("live-session eligibility validation passed")


if __name__ == "__main__":
    main()
