"""Ingest exact daily research decisions without refreshing market prices.

Ledger reconciliation may need to replay many historical Daily Momentum Report
artifacts. Fetching the same price histories after every artifact is wasteful and
can prevent the ledger commit from completing. This helper appends each exact
artifact's decisions and creates deterministic PENDING outcome placeholders.
The reconciliation workflow performs one normal ``priority_outcomes.py update``
after every source artifact has been ingested.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

import priority_outcomes as tracker


def ensure_pending_outcomes(
    decisions: pd.DataFrame,
    existing: pd.DataFrame,
    policy: dict[str, Any],
) -> pd.DataFrame:
    work = tracker.normalize_frame(
        existing,
        tracker.OUTCOME_COLUMNS,
        tracker.BOOL_OUTCOME_COLUMNS,
        tracker.NUMERIC_OUTCOME_COLUMNS,
    )
    existing_keys = {
        (str(row["decision_id"]), int(row["horizon_sessions"]))
        for _, row in work.iterrows()
    }
    rows = work.to_dict(orient="records")
    for _, decision in decisions.iterrows():
        for horizon in policy["execution_model"]["horizons_sessions"]:
            key = (str(decision["decision_id"]), int(horizon))
            if key in existing_keys:
                continue
            row = tracker.outcome_base(decision, int(horizon), policy)
            row["outcome_detail"] = "price refresh deferred until reconciliation finalization"
            rows.append(row)
            existing_keys.add(key)
    result = tracker.normalize_frame(
        pd.DataFrame(rows, columns=tracker.OUTCOME_COLUMNS),
        tracker.OUTCOME_COLUMNS,
        tracker.BOOL_OUTCOME_COLUMNS,
        tracker.NUMERIC_OUTCOME_COLUMNS,
    )
    if result.empty:
        return result
    result = result.drop_duplicates(
        ["decision_id", "horizon_sessions"], keep="last"
    )
    result["_date"] = pd.to_datetime(result["decision_date"], errors="coerce")
    result = result.sort_values(
        ["_date", "horizon_sessions", "code"], na_position="last"
    )
    return result.drop(columns="_date").reset_index(drop=True)


def ingest(
    artifact_root: str,
    source_run_id: str,
    source_run_url: str,
    recorded_at_utc: str,
    policy_path: str,
    decisions_path: str,
    outcomes_path: str,
    calibration_json_path: str,
    calibration_md_path: str,
) -> dict[str, Any]:
    policy = tracker.load_policy(policy_path)
    decisions = tracker.load_decisions(decisions_path)
    incoming = tracker.extract_decisions(
        artifact_root=artifact_root,
        source_run_id=source_run_id,
        source_run_url=source_run_url,
        recorded_at_utc=recorded_at_utc,
        policy=policy,
    )
    decisions = tracker.append_decisions(decisions, incoming)
    outcomes = ensure_pending_outcomes(
        decisions,
        tracker.load_outcomes(outcomes_path),
        policy,
    )
    history_errors = tracker.validate_histories(decisions, outcomes, policy)
    if history_errors:
        raise ValueError("; ".join(history_errors))
    calibration = tracker.build_calibration(decisions, outcomes, policy)
    calibration_errors = tracker.validate_calibration(calibration)
    if calibration_errors:
        raise ValueError("; ".join(calibration_errors))
    tracker.atomic_write_csv(decisions, decisions_path)
    tracker.atomic_write_csv(outcomes, outcomes_path)
    tracker.atomic_write_json(calibration, calibration_json_path)
    tracker.atomic_write_text(
        tracker.calibration_markdown(calibration), calibration_md_path
    )
    return {
        "source_run_id": source_run_id,
        "incoming_decision_count": int(len(incoming)),
        "decision_count": int(len(decisions)),
        "outcome_row_count": int(len(outcomes)),
        "complete_outcome_count": int(
            outcomes["outcome_status"].eq("COMPLETE").sum()
        ),
        "pending_outcome_count": int(
            outcomes["outcome_status"].eq("PENDING").sum()
        ),
        "price_refresh_performed": False,
        "production_state_mutations": [],
        "automatic_priority_rule_change": False,
        "research_only": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest exact daily priority decisions without price refresh"
    )
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--source-run-url", required=True)
    parser.add_argument("--recorded-at-utc", required=True)
    parser.add_argument("--policy", default=tracker.POLICY_PATH)
    parser.add_argument("--decisions", default=tracker.DEFAULT_DECISIONS)
    parser.add_argument("--outcomes", default=tracker.DEFAULT_OUTCOMES)
    parser.add_argument(
        "--calibration-json", default=tracker.DEFAULT_CALIBRATION_JSON
    )
    parser.add_argument("--calibration-md", default=tracker.DEFAULT_CALIBRATION_MD)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = ingest(
        artifact_root=args.artifact_root,
        source_run_id=args.source_run_id,
        source_run_url=args.source_run_url,
        recorded_at_utc=args.recorded_at_utc,
        policy_path=args.policy,
        decisions_path=args.decisions,
        outcomes_path=args.outcomes,
        calibration_json_path=args.calibration_json,
        calibration_md_path=args.calibration_md,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
