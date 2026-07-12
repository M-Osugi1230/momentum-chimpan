"""Prepare live Forward Evidence from signed eligible daily sessions only."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import evidence_provenance as provenance
import live_session_eligibility as eligibility

FILTER_VERSION = "2026-07-13-forward-eligible-history-v1"


def prepare_eligible_live_history(
    ranking_path: str,
    ledger_path: str,
    output_path: str,
    provenance_path: str,
    fingerprint_path: str | None = None,
) -> dict[str, Any]:
    source = Path(ranking_path)
    if source.as_posix() != provenance.ALLOWED_LIVE_SOURCE:
        raise ValueError(f"live evidence source must be {provenance.ALLOWED_LIVE_SOURCE}")
    if not source.is_file():
        raise FileNotFoundError(ranking_path)

    frame = pd.read_csv(source, dtype={"code": str, "date": str})
    if "strategy_fingerprint" not in frame.columns:
        frame["strategy_fingerprint"] = ""
    if "date" not in frame.columns:
        raise ValueError("live ranking history is missing date")

    current = provenance.current_strategy_fingerprint()
    if fingerprint_path:
        snapshot = provenance.load_json(fingerprint_path)
        snapshot_fingerprint = str(snapshot.get("strategy_fingerprint", "")).strip()
        if snapshot_fingerprint and snapshot_fingerprint != current:
            raise ValueError("stored strategy fingerprint does not match current code")

    ledger = eligibility.load_history(ledger_path)
    ledger_issues = eligibility.validate_history(ledger)
    if ledger_issues:
        raise ValueError("invalid live-session eligibility ledger: " + "; ".join(ledger_issues))
    eligible_ledger = ledger[
        ledger["eligible_for_forward_evidence"]
        & ledger["strategy_fingerprint"].eq(current)
        & ledger["report_date"].ne("")
        & ledger["ranking_date_sha256"].ne("")
    ].copy()

    approved_hashes: dict[str, set[str]] = {}
    approved_run_ids: dict[str, list[str]] = {}
    for _, row in eligible_ledger.iterrows():
        report_date = str(row["report_date"])
        approved_hashes.setdefault(report_date, set()).add(str(row["ranking_date_sha256"]))
        approved_run_ids.setdefault(report_date, []).append(str(row["source_run_id"]))

    stamped = frame[frame["strategy_fingerprint"].astype(str).eq(current)].copy()
    source_dates = sorted(frame["date"].dropna().astype(str).unique().tolist())
    stamped_dates = sorted(stamped["date"].dropna().astype(str).unique().tolist())
    verified_frames: list[pd.DataFrame] = []
    verified_dates: list[str] = []
    excluded_dates: list[dict[str, Any]] = []

    for report_date in stamped_dates:
        date_frame = stamped[stamped["date"].astype(str).eq(report_date)].copy()
        payload = eligibility.ranking_date_payload(date_frame, report_date)
        date_hash = eligibility.canonical_hash(payload) if payload["rows"] else ""
        allowed = approved_hashes.get(report_date, set())
        if date_hash and date_hash in allowed:
            verified_frames.append(date_frame)
            verified_dates.append(report_date)
        else:
            excluded_dates.append({
                "report_date": report_date,
                "ranking_date_sha256": date_hash,
                "reason": "NO_ELIGIBLE_LEDGER_ROW" if not allowed else "RANKING_DATE_SHA256_MISMATCH",
            })

    if verified_frames:
        eligible_frame = pd.concat(verified_frames, ignore_index=True)
        eligible_frame["date"] = eligible_frame["date"].astype(str)
        eligible_frame = eligible_frame.drop_duplicates(["date", "code"], keep="last")
        sort_columns = [column for column in ("date", "rank", "code") if column in eligible_frame.columns]
        eligible_frame = eligible_frame.sort_values(sort_columns).reset_index(drop=True)
    else:
        eligible_frame = frame.iloc[0:0].copy()

    provenance.atomic_write_csv(eligible_frame, output_path)
    payload = {
        "provenance_version": provenance.PROVENANCE_VERSION,
        "eligibility_filter_version": FILTER_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_origin": provenance.LIVE_ORIGIN,
        "promotion_evidence_allowed": True,
        "strategy_fingerprint": current,
        "source_path": ranking_path,
        "source_sha256": provenance.sha256_file(source),
        "filtered_history_path": output_path,
        "filtered_history_sha256": provenance.sha256_file(output_path),
        "source_row_count": len(frame),
        "eligible_row_count": len(eligible_frame),
        "source_date_count": len(source_dates),
        "strategy_stamped_date_count": len(stamped_dates),
        "eligible_date_count": len(verified_dates),
        "first_eligible_date": verified_dates[0] if verified_dates else "",
        "last_eligible_date": verified_dates[-1] if verified_dates else "",
        "eligibility_enforced": True,
        "eligibility_ledger_path": ledger_path,
        "eligibility_ledger_sha256": provenance.sha256_file(ledger_path),
        "eligibility_ledger_row_count": len(ledger),
        "eligible_ledger_row_count": len(eligible_ledger),
        "verified_date_count": len(verified_dates),
        "verified_dates": verified_dates,
        "verified_source_run_ids": {
            date: sorted(set(approved_run_ids.get(date, [])))
            for date in verified_dates
        },
        "excluded_unverified_date_count": len(excluded_dates),
        "excluded_unverified_dates": excluded_dates,
        "bias_flags": [],
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
        "research_only": True,
    }
    provenance.atomic_write_json(payload, provenance_path)
    return payload


def validate_manifest(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("provenance_version") != provenance.PROVENANCE_VERSION:
        issues.append("invalid provenance_version")
    if payload.get("eligibility_filter_version") != FILTER_VERSION:
        issues.append("invalid eligibility_filter_version")
    if payload.get("evidence_origin") != provenance.LIVE_ORIGIN:
        issues.append("invalid evidence_origin")
    if payload.get("promotion_evidence_allowed") is not True:
        issues.append("promotion_evidence_allowed must be true")
    if payload.get("eligibility_enforced") is not True:
        issues.append("eligibility_enforced must be true")
    if not eligibility.valid_sha256(payload.get("strategy_fingerprint")):
        issues.append("invalid strategy_fingerprint")
    if not eligibility.valid_sha256(payload.get("source_sha256")):
        issues.append("invalid source_sha256")
    if not eligibility.valid_sha256(payload.get("filtered_history_sha256")):
        issues.append("invalid filtered_history_sha256")
    if not eligibility.valid_sha256(payload.get("eligibility_ledger_sha256")):
        issues.append("invalid eligibility_ledger_sha256")
    if payload.get("automatic_weight_change") is not False:
        issues.append("automatic_weight_change must be false")
    if payload.get("automatic_strategy_change") is not False:
        issues.append("automatic_strategy_change must be false")
    if payload.get("production_state_mutations") != []:
        issues.append("production_state_mutations must be empty")
    if payload.get("research_only") is not True:
        issues.append("research_only must be true")
    verified_dates = payload.get("verified_dates")
    if not isinstance(verified_dates, list):
        issues.append("verified_dates must be a list")
    elif int(payload.get("verified_date_count", -1)) != len(verified_dates):
        issues.append("verified_date_count mismatch")
    excluded = payload.get("excluded_unverified_dates")
    if not isinstance(excluded, list):
        issues.append("excluded_unverified_dates must be a list")
    elif int(payload.get("excluded_unverified_date_count", -1)) != len(excluded):
        issues.append("excluded_unverified_date_count mismatch")
    return issues


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Filter live ranking history through signed eligibility")
    parser.add_argument("--ranking", default=provenance.ALLOWED_LIVE_SOURCE)
    parser.add_argument("--ledger", default=eligibility.DEFAULT_LEDGER)
    parser.add_argument("--output", default="output/replay/live_strategy_history.csv")
    parser.add_argument("--provenance", default="output/replay/evidence_provenance.json")
    parser.add_argument("--fingerprint", default="data/strategy_fingerprint.json")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    payload = prepare_eligible_live_history(
        ranking_path=args.ranking,
        ledger_path=args.ledger,
        output_path=args.output,
        provenance_path=args.provenance,
        fingerprint_path=args.fingerprint,
    )
    issues = validate_manifest(payload)
    print(json.dumps({"payload": payload, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
