from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evidence_provenance as provenance
import forward_eligible_history as forward_filter
import live_session_eligibility as eligibility

FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64


def ledger_record(report_date: str, date_hash: str, fingerprint: str = FINGERPRINT) -> dict:
    return {
        "eligibility_version": eligibility.ELIGIBILITY_VERSION,
        "source_run_id": f"run-{report_date}",
        "source_run_url": f"https://github.com/example/actions/runs/{report_date}",
        "upstream_conclusion": "success",
        "upstream_event": "schedule",
        "head_sha": "c" * 40,
        "created_at_utc": f"{report_date}T07:45:00Z",
        "updated_at_utc": f"{report_date}T08:10:00Z",
        "recorded_at_utc": f"{report_date}T08:10:00Z",
        "report_date": report_date,
        "strategy_fingerprint": fingerprint,
        "readiness_state": "PASS",
        "eligible_for_forward_evidence": True,
        "eligible_for_priority_outcome_ingestion": True,
        "artifact_fingerprint": "d" * 64,
        "readiness_fingerprint": "e" * 64,
        "readiness_status_sha256": "f" * 64,
        "ranking_date_row_count": 2,
        "ranking_date_sha256": date_hash,
        "critical_failure_count": 0,
        "review_warning_count": 0,
        "readiness_details": "",
        "research_only": True,
    }


def prepare_fixture(root: Path) -> tuple[Path, Path, pd.DataFrame]:
    ranking_path = root / "data" / "momentum_daily_ranking.csv"
    ranking_path.parent.mkdir(parents=True, exist_ok=True)
    ranking = pd.DataFrame([
        {
            "date": "2026-07-13",
            "rank": 1,
            "code": "1001",
            "close": 100.0,
            "score": 80.0,
            "strategy_fingerprint": FINGERPRINT,
        },
        {
            "date": "2026-07-13",
            "rank": 2,
            "code": "1002",
            "close": 110.0,
            "score": 75.0,
            "strategy_fingerprint": FINGERPRINT,
        },
        {
            "date": "2026-07-14",
            "rank": 1,
            "code": "1001",
            "close": 102.0,
            "score": 82.0,
            "strategy_fingerprint": FINGERPRINT,
        },
        {
            "date": "2026-07-15",
            "rank": 1,
            "code": "1003",
            "close": 90.0,
            "score": 78.0,
            "strategy_fingerprint": OTHER_FINGERPRINT,
        },
    ])
    ranking.to_csv(ranking_path, index=False)

    payload = eligibility.ranking_date_payload(ranking, "2026-07-13")
    date_hash = eligibility.canonical_hash(payload)
    ledger = eligibility.normalize_history(
        pd.DataFrame([ledger_record("2026-07-13", date_hash)])
    )
    ledger_path = root / eligibility.DEFAULT_LEDGER
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(ledger_path, index=False)
    return ranking_path, ledger_path, ranking


def main() -> None:
    original_fingerprint = provenance.current_strategy_fingerprint
    original_allowed_source = provenance.ALLOWED_LIVE_SOURCE
    original_cwd = Path.cwd()
    try:
        provenance.current_strategy_fingerprint = lambda: FINGERPRINT
        provenance.ALLOWED_LIVE_SOURCE = "data/momentum_daily_ranking.csv"

        with TemporaryDirectory() as directory:
            root = Path(directory)
            ranking_path, ledger_path, ranking = prepare_fixture(root)
            os.chdir(root)

            output_path = root / "output" / "live_strategy_history.csv"
            manifest_path = root / "output" / "evidence_provenance.json"
            payload = forward_filter.prepare_eligible_live_history(
                ranking_path="data/momentum_daily_ranking.csv",
                ledger_path=eligibility.DEFAULT_LEDGER,
                output_path=str(output_path),
                provenance_path=str(manifest_path),
                fingerprint_path=None,
            )
            assert forward_filter.validate_manifest(payload) == []
            filtered = pd.read_csv(output_path, dtype={"code": str, "date": str})
            assert len(filtered) == 2
            assert set(filtered["date"]) == {"2026-07-13"}
            assert payload["eligibility_enforced"] is True
            assert payload["verified_dates"] == ["2026-07-13"]
            assert payload["verified_date_count"] == 1
            assert payload["excluded_unverified_date_count"] == 1
            assert payload["excluded_unverified_dates"][0]["report_date"] == "2026-07-14"
            assert payload["excluded_unverified_dates"][0]["reason"] == "NO_ELIGIBLE_LEDGER_ROW"
            assert payload["automatic_weight_change"] is False
            assert payload["production_state_mutations"] == []

            mismatched = eligibility.load_history(ledger_path)
            mismatched.loc[:, "ranking_date_sha256"] = "0" * 64
            mismatched.to_csv(ledger_path, index=False)
            mismatch_output = root / "output" / "mismatch.csv"
            mismatch_manifest = root / "output" / "mismatch.json"
            mismatch_payload = forward_filter.prepare_eligible_live_history(
                ranking_path="data/momentum_daily_ranking.csv",
                ledger_path=eligibility.DEFAULT_LEDGER,
                output_path=str(mismatch_output),
                provenance_path=str(mismatch_manifest),
                fingerprint_path=None,
            )
            assert pd.read_csv(mismatch_output).empty
            reasons = {
                row["report_date"]: row["reason"]
                for row in mismatch_payload["excluded_unverified_dates"]
            }
            assert reasons["2026-07-13"] == "RANKING_DATE_SHA256_MISMATCH"
            assert reasons["2026-07-14"] == "NO_ELIGIBLE_LEDGER_ROW"

            wrong_fingerprint_payload = eligibility.ranking_date_payload(
                ranking[ranking["date"].eq("2026-07-13")], "2026-07-13"
            )
            wrong = eligibility.normalize_history(pd.DataFrame([
                ledger_record(
                    "2026-07-13",
                    eligibility.canonical_hash(wrong_fingerprint_payload),
                    fingerprint=OTHER_FINGERPRINT,
                )
            ]))
            wrong.to_csv(ledger_path, index=False)
            wrong_output = root / "output" / "wrong.csv"
            wrong_manifest = root / "output" / "wrong.json"
            wrong_payload = forward_filter.prepare_eligible_live_history(
                ranking_path="data/momentum_daily_ranking.csv",
                ledger_path=eligibility.DEFAULT_LEDGER,
                output_path=str(wrong_output),
                provenance_path=str(wrong_manifest),
                fingerprint_path=None,
            )
            assert pd.read_csv(wrong_output).empty
            assert wrong_payload["eligible_ledger_row_count"] == 0
            assert wrong_payload["verified_date_count"] == 0

            invalid = eligibility.normalize_history(pd.DataFrame([
                ledger_record("2026-07-13", "")
            ]))
            invalid.to_csv(ledger_path, index=False)
            try:
                forward_filter.prepare_eligible_live_history(
                    ranking_path="data/momentum_daily_ranking.csv",
                    ledger_path=eligibility.DEFAULT_LEDGER,
                    output_path=str(root / "output" / "invalid.csv"),
                    provenance_path=str(root / "output" / "invalid.json"),
                    fingerprint_path=None,
                )
                raise AssertionError("invalid ledger was accepted")
            except ValueError as error:
                assert "invalid live-session eligibility ledger" in str(error)
    finally:
        os.chdir(original_cwd)
        provenance.current_strategy_fingerprint = original_fingerprint
        provenance.ALLOWED_LIVE_SOURCE = original_allowed_source

    workflow_path = ROOT / ".github" / "workflows" / "volume-component-forward-evidence.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    assert workflow["permissions"]["contents"] == "read"
    assert "python live_session_eligibility.py validate" in workflow_text
    assert "python forward_eligible_history.py" in workflow_text
    assert "--ledger research/evidence/live_session_eligibility.csv" in workflow_text
    assert "python evidence_provenance.py prepare-live" not in workflow_text
    assert ("git" + " push") not in workflow_text
    assert ("EMAIL_" + "APP_PASSWORD") not in workflow_text

    print("forward eligible history validation passed")


if __name__ == "__main__":
    main()
