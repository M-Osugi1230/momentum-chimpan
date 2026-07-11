from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import release_review


fingerprint = "a" * 64

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    evidence_path = root / "evidence.json"
    runtime_path = root / "runtime.json"
    heartbeat_path = root / "heartbeat.json"
    fingerprint_path = root / "fingerprint.json"
    equity_path = root / "equity.csv"
    trades_path = root / "trades.csv"

    evidence_path.write_text(json.dumps({
        "manual_review_eligible": True,
        "readiness": "ELIGIBLE_FOR_MANUAL_REVIEW",
        "strategy_fingerprint": fingerprint,
        "execution_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "outcome_count": 120,
        "robustness_status": "ROBUST",
    }), encoding="utf-8")
    runtime_path.write_text(json.dumps({
        "strategy_fingerprint": fingerprint,
        "dependency_lock_present": True,
        "required_packages_present": True,
        "execution_mode": "RESEARCH_AND_PAPER_ONLY",
        "environment_status": "STABLE",
    }), encoding="utf-8")
    heartbeat_path.write_text(json.dumps({
        "status": "PASS",
        "run_id": "123",
    }), encoding="utf-8")
    fingerprint_path.write_text(json.dumps({
        "strategy_fingerprint": fingerprint,
    }), encoding="utf-8")
    pd.DataFrame([{
        "date": "2026-07-10",
        "equity": 10_500_000,
        "drawdown": -0.05,
        "realized_pnl": 400_000,
        "unrealized_pnl": 100_000,
        "open_positions": 3,
        "win_rate": 0.60,
    }]).to_csv(equity_path, index=False)
    pd.DataFrame([
        {"realized_pnl": 10_000 if index < 12 else -5_000}
        for index in range(20)
    ]).to_csv(trades_path, index=False)

    original_fingerprint = release_review.evidence_provenance.current_strategy_fingerprint
    release_review.evidence_provenance.current_strategy_fingerprint = lambda: fingerprint
    try:
        packet = release_review.build_review_packet(
            str(evidence_path), str(runtime_path), str(heartbeat_path),
            str(fingerprint_path), str(equity_path), str(trades_path),
        )
    finally:
        release_review.evidence_provenance.current_strategy_fingerprint = original_fingerprint

    assert packet["status"] == "READY_FOR_HUMAN_REVIEW"
    assert all(item["passed"] for item in packet["criteria"])
    assert packet["strategy_fingerprint"] == fingerprint
    assert len(packet["evidence_status_sha256"]) == 64
    assert len(packet["packet_sha256"]) == 64
    assert packet["automatic_strategy_change"] is False
    assert packet["automatic_approval"] is False
    assert packet["manual_approval_required"] is True
    assert packet["paper"]["closed_trades"] == 20
    assert packet["paper"]["win_rate"] == 0.60

    paths = release_review.write_packet(packet, str(root / "packet"))
    for path in paths.values():
        assert Path(path).exists(), path
    markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "READY_FOR_HUMAN_REVIEW" in markdown
    assert "does not approve or activate" in markdown
    workbook = pd.ExcelFile(paths["excel"])
    assert {
        "Review Summary", "Readiness Criteria", "Paper Validation",
        "Evidence Status", "Runtime",
    }.issubset(workbook.sheet_names)

    # Any missing critical input blocks the packet.
    runtime_path.write_text(json.dumps({
        "strategy_fingerprint": fingerprint,
        "dependency_lock_present": False,
        "required_packages_present": True,
        "execution_mode": "RESEARCH_AND_PAPER_ONLY",
    }), encoding="utf-8")
    release_review.evidence_provenance.current_strategy_fingerprint = lambda: fingerprint
    try:
        blocked = release_review.build_review_packet(
            str(evidence_path), str(runtime_path), str(heartbeat_path),
            str(fingerprint_path), str(equity_path), str(trades_path),
        )
    finally:
        release_review.evidence_provenance.current_strategy_fingerprint = original_fingerprint
    assert blocked["status"] == "NOT_READY"
    runtime_criterion = next(
        item for item in blocked["criteria"]
        if item["criterion"] == "locked_runtime_environment"
    )
    assert runtime_criterion["passed"] is False

    # Fingerprint mismatch also blocks review.
    runtime_path.write_text(json.dumps({
        "strategy_fingerprint": "b" * 64,
        "dependency_lock_present": True,
        "required_packages_present": True,
        "execution_mode": "RESEARCH_AND_PAPER_ONLY",
    }), encoding="utf-8")
    release_review.evidence_provenance.current_strategy_fingerprint = lambda: fingerprint
    try:
        mismatch = release_review.build_review_packet(
            str(evidence_path), str(runtime_path), str(heartbeat_path),
            str(fingerprint_path), str(equity_path), str(trades_path),
        )
    finally:
        release_review.evidence_provenance.current_strategy_fingerprint = original_fingerprint
    assert mismatch["status"] == "NOT_READY"
    fingerprint_criterion = next(
        item for item in mismatch["criteria"]
        if item["criterion"] == "strategy_fingerprint_consistency"
    )
    assert fingerprint_criterion["passed"] is False

    approvals_path = root / "approvals.yaml"
    approvals_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "policy": {"automatic_activation": False},
        "approvals": [{
            "approval_id": "approval-001",
            "decision": "APPROVE",
            "strategy_fingerprint": fingerprint,
            "evidence_status_sha256": "c" * 64,
            "review_packet_sha256": "d" * 64,
            "reviewer": "human-reviewer",
            "approved_at_utc": "2026-07-11T00:00:00Z",
            "scope": "MANUAL_REVIEW_ONLY",
            "notes": "Reviewed evidence and risks.",
        }],
    }, sort_keys=False), encoding="utf-8")
    approvals = release_review.validate_approvals(str(approvals_path))
    assert len(approvals) == 1
    assert approvals.iloc[0]["decision"] == "APPROVE"
    assert approvals.iloc[0]["scope"] == "MANUAL_REVIEW_ONLY"

    invalid = yaml.safe_load(approvals_path.read_text(encoding="utf-8"))
    invalid["policy"]["automatic_activation"] = True
    approvals_path.write_text(yaml.safe_dump(invalid), encoding="utf-8")
    try:
        release_review.validate_approvals(str(approvals_path))
        raise AssertionError("automatic activation should be rejected")
    except ValueError as exc:
        assert "automatic activation" in str(exc)

    invalid["policy"]["automatic_activation"] = False
    invalid["approvals"] = invalid["approvals"] * 2
    approvals_path.write_text(yaml.safe_dump(invalid), encoding="utf-8")
    try:
        release_review.validate_approvals(str(approvals_path))
        raise AssertionError("duplicate approval ids should be rejected")
    except ValueError as exc:
        assert "unique" in str(exc)

print("manual release review validation passed")
