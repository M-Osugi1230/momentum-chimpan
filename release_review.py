"""Build a manual strategy-release review packet from governed evidence.

The packet is informational and cannot change strategy parameters. Approval
records are human-authored audit entries tied to an exact strategy fingerprint
and evidence-status hash; automatic activation is permanently disabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

import evidence_provenance
import main

REVIEW_VERSION = "2026-07-11-manual-release-review-v1"
DEFAULT_OUTPUT_DIR = "output/release-review"
DEFAULT_APPROVALS = "research/strategy_approvals.yaml"
APPROVAL_SCHEMA_VERSION = 1


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_csv(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(target)
    except Exception:
        return pd.DataFrame()


def optional_float(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def heartbeat_success(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    candidates = [
        payload.get("status"),
        payload.get("overall_status"),
        payload.get("run_status"),
        payload.get("run_health"),
    ]
    accepted = {"PASS", "SUCCESS", "HEALTHY", "OK"}
    return any(str(value or "").upper() in accepted for value in candidates)


def latest_paper_metrics(
    equity_history: pd.DataFrame,
    trade_history: pd.DataFrame,
) -> dict[str, Any]:
    if equity_history.empty:
        return {
            "equity": None,
            "drawdown": None,
            "closed_trades": len(trade_history),
            "win_rate": None,
            "realized_pnl": None,
        }
    row = equity_history.iloc[-1]
    realized = pd.to_numeric(
        trade_history.get("realized_pnl", pd.Series(dtype=float)), errors="coerce"
    ).dropna()
    wins = int((realized > 0).sum()) if len(realized) else 0
    closed = len(trade_history)
    return {
        "date": str(row.get("date", "")),
        "equity": optional_float(row.get("equity")),
        "drawdown": optional_float(row.get("drawdown")),
        "closed_trades": closed,
        "win_rate": wins / closed if closed else optional_float(row.get("win_rate")),
        "realized_pnl": optional_float(row.get("realized_pnl")),
        "unrealized_pnl": optional_float(row.get("unrealized_pnl")),
        "open_positions": optional_float(row.get("open_positions")),
    }


def build_review_packet(
    evidence_status_path: str = "data/research_evidence_status.json",
    runtime_provenance_path: str = "data/runtime_provenance.json",
    heartbeat_path: str = "data/operations_heartbeat.json",
    fingerprint_path: str = "data/strategy_fingerprint.json",
    paper_equity_path: str = "data/paper_equity_history.csv",
    paper_trade_path: str = "data/paper_trade_history.csv",
) -> dict[str, Any]:
    evidence = load_json(evidence_status_path)
    runtime = load_json(runtime_provenance_path)
    heartbeat = load_json(heartbeat_path)
    fingerprint_manifest = load_json(fingerprint_path)
    current_fingerprint = evidence_provenance.current_strategy_fingerprint()
    stored_fingerprint = str(fingerprint_manifest.get("strategy_fingerprint", ""))
    evidence_fingerprint = str(evidence.get("strategy_fingerprint", ""))
    runtime_fingerprint = str(runtime.get("strategy_fingerprint", ""))
    paper = latest_paper_metrics(load_csv(paper_equity_path), load_csv(paper_trade_path))

    evidence_ready = evidence.get("manual_review_eligible") is True
    fingerprint_consistent = bool(
        current_fingerprint
        and stored_fingerprint == current_fingerprint
        and evidence_fingerprint == current_fingerprint
        and runtime_fingerprint == current_fingerprint
    )
    runtime_ready = bool(
        runtime
        and runtime.get("dependency_lock_present") is True
        and runtime.get("required_packages_present") is True
        and runtime.get("execution_mode") == main.EXECUTION_MODE
    )
    operations_ready = heartbeat_success(heartbeat)
    paper_ready = bool(
        paper.get("equity") is not None
        and float(paper["equity"]) > main.PAPER_INITIAL_CAPITAL
        and int(paper.get("closed_trades", 0)) >= 20
        and paper.get("win_rate") is not None
        and float(paper["win_rate"]) >= 0.50
        and paper.get("drawdown") is not None
        and float(paper["drawdown"]) >= -0.10
    )
    criteria = [
        {
            "criterion": "signed_live_execution_evidence",
            "passed": evidence_ready,
            "actual": evidence.get("readiness", "MISSING"),
            "required": "ELIGIBLE_FOR_MANUAL_REVIEW",
            "blocking": True,
        },
        {
            "criterion": "strategy_fingerprint_consistency",
            "passed": fingerprint_consistent,
            "actual": current_fingerprint,
            "required": "current=evidence=runtime=daily snapshot",
            "blocking": True,
        },
        {
            "criterion": "locked_runtime_environment",
            "passed": runtime_ready,
            "actual": runtime.get("environment_status", "MISSING"),
            "required": "lock and required packages present",
            "blocking": True,
        },
        {
            "criterion": "latest_operational_heartbeat",
            "passed": operations_ready,
            "actual": heartbeat.get("status", heartbeat.get("overall_status", "MISSING")),
            "required": "PASS/SUCCESS/HEALTHY/OK",
            "blocking": True,
        },
        {
            "criterion": "paper_validation",
            "passed": paper_ready,
            "actual": (
                f"equity={paper.get('equity')} trades={paper.get('closed_trades')} "
                f"win_rate={paper.get('win_rate')} drawdown={paper.get('drawdown')}"
            ),
            "required": "positive return, 20 trades, win rate>=50%, DD>=-10%",
            "blocking": True,
        },
    ]
    all_passed = all(item["passed"] for item in criteria if item["blocking"])
    status = "READY_FOR_HUMAN_REVIEW" if all_passed else "NOT_READY"
    packet = {
        "review_version": REVIEW_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "execution_mode": main.EXECUTION_MODE,
        "strategy_fingerprint": current_fingerprint,
        "evidence_status_sha256": sha256_file(evidence_status_path),
        "runtime_provenance_sha256": sha256_file(runtime_provenance_path),
        "heartbeat_sha256": sha256_file(heartbeat_path),
        "strategy_fingerprint_manifest_sha256": sha256_file(fingerprint_path),
        "paper_equity_sha256": sha256_file(paper_equity_path),
        "paper_trade_sha256": sha256_file(paper_trade_path),
        "criteria": criteria,
        "evidence": evidence,
        "runtime": runtime,
        "heartbeat": heartbeat,
        "paper": paper,
        "automatic_strategy_change": False,
        "automatic_approval": False,
        "manual_approval_required": True,
        "research_only": True,
    }
    canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    packet["packet_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return packet


def packet_markdown(packet: dict[str, Any]) -> str:
    lines = [
        "# Momentum Chimpan Strategy Release Review",
        "",
        f"- Status: **{packet['status']}**",
        f"- Strategy fingerprint: `{packet['strategy_fingerprint']}`",
        f"- Evidence status SHA-256: `{packet['evidence_status_sha256']}`",
        f"- Packet SHA-256: `{packet['packet_sha256']}`",
        f"- Generated: {packet['generated_at_utc']}",
        "",
        "## Readiness criteria",
        "",
        "| Criterion | Passed | Actual | Required |",
        "|---|---:|---|---|",
    ]
    for item in packet["criteria"]:
        lines.append(
            f"| {item['criterion']} | {'YES' if item['passed'] else 'NO'} | "
            f"{str(item['actual']).replace('|', '/')} | {str(item['required']).replace('|', '/')} |"
        )
    lines.extend([
        "",
        "## Approval rule",
        "",
        "This packet does not approve or activate a strategy. A human-authored approval record must reference the exact strategy fingerprint and evidence-status SHA-256. Automatic activation remains disabled.",
        "",
    ])
    return "\n".join(lines)


def write_packet(packet: dict[str, Any], output_dir: str = DEFAULT_OUTPUT_DIR) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "release_review_packet.json"
    markdown_path = output / "release_review_packet.md"
    excel_path = output / "release_review_packet.xlsx"
    json_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(packet_markdown(packet), encoding="utf-8")
    criteria = pd.DataFrame(packet["criteria"])
    summary = pd.DataFrame([{
        "status": packet["status"],
        "strategy_fingerprint": packet["strategy_fingerprint"],
        "evidence_status_sha256": packet["evidence_status_sha256"],
        "packet_sha256": packet["packet_sha256"],
        "generated_at_utc": packet["generated_at_utc"],
        "automatic_strategy_change": False,
        "automatic_approval": False,
        "manual_approval_required": True,
    }])
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Review Summary", index=False)
        criteria.to_excel(writer, sheet_name="Readiness Criteria", index=False)
        pd.DataFrame([packet["paper"]]).to_excel(writer, sheet_name="Paper Validation", index=False)
        pd.DataFrame([packet["evidence"]]).to_excel(writer, sheet_name="Evidence Status", index=False)
        pd.DataFrame([packet["runtime"]]).to_excel(writer, sheet_name="Runtime", index=False)
    return {"json": str(json_path), "markdown": str(markdown_path), "excel": str(excel_path)}


def load_approvals(path: str = DEFAULT_APPROVALS) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("approval registry must be a mapping")
    return payload


def validate_approvals(path: str = DEFAULT_APPROVALS) -> pd.DataFrame:
    payload = load_approvals(path)
    if int(payload.get("schema_version", 0)) != APPROVAL_SCHEMA_VERSION:
        raise ValueError("approval schema version mismatch")
    policy = payload.get("policy", {})
    if policy.get("automatic_activation") is not False:
        raise ValueError("automatic activation must remain false")
    approvals = payload.get("approvals", [])
    if not isinstance(approvals, list):
        raise ValueError("approvals must be a list")
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    required = {
        "approval_id",
        "decision",
        "strategy_fingerprint",
        "evidence_status_sha256",
        "review_packet_sha256",
        "reviewer",
        "approved_at_utc",
        "scope",
    }
    for entry in approvals:
        if not isinstance(entry, dict):
            raise ValueError("approval entries must be mappings")
        missing = sorted(required - set(entry))
        if missing:
            raise ValueError(f"approval entry missing fields: {missing}")
        approval_id = str(entry["approval_id"]).strip()
        if not approval_id or approval_id in ids:
            raise ValueError("approval ids must be non-empty and unique")
        ids.add(approval_id)
        decision = str(entry["decision"]).upper()
        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError("decision must be APPROVE or REJECT")
        if str(entry["scope"]) != "MANUAL_REVIEW_ONLY":
            raise ValueError("approval scope must be MANUAL_REVIEW_ONLY")
        if len(str(entry["strategy_fingerprint"])) != 64:
            raise ValueError("strategy fingerprint must be a SHA-256 hex string")
        if len(str(entry["evidence_status_sha256"])) != 64:
            raise ValueError("evidence status hash must be SHA-256")
        if len(str(entry["review_packet_sha256"])) != 64:
            raise ValueError("review packet hash must be SHA-256")
        approved_at = pd.to_datetime(entry["approved_at_utc"], utc=True, errors="coerce")
        if pd.isna(approved_at):
            raise ValueError("approved_at_utc must be a valid timestamp")
        if not str(entry["reviewer"]).strip():
            raise ValueError("reviewer is required")
        rows.append({**entry, "decision": decision, "approved_at_utc": approved_at.isoformat()})
    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or validate manual release review artifacts")
    sub = parser.add_subparsers(dest="command", required=True)

    packet = sub.add_parser("packet")
    packet.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    packet.add_argument("--evidence-status", default="data/research_evidence_status.json")
    packet.add_argument("--runtime-provenance", default="data/runtime_provenance.json")
    packet.add_argument("--heartbeat", default="data/operations_heartbeat.json")
    packet.add_argument("--fingerprint", default="data/strategy_fingerprint.json")
    packet.add_argument("--paper-equity", default="data/paper_equity_history.csv")
    packet.add_argument("--paper-trades", default="data/paper_trade_history.csv")

    approvals = sub.add_parser("validate-approvals")
    approvals.add_argument("--approvals", default=DEFAULT_APPROVALS)
    approvals.add_argument("--output", default="output/release-review/approval_validation.csv")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "packet":
        packet = build_review_packet(
            args.evidence_status,
            args.runtime_provenance,
            args.heartbeat,
            args.fingerprint,
            args.paper_equity,
            args.paper_trades,
        )
        paths = write_packet(packet, args.output_dir)
        print(json.dumps({"packet": packet, "paths": paths}, ensure_ascii=False, indent=2))
        return 0
    validation = validate_approvals(args.approvals)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    validation.to_csv(target, index=False)
    print(validation.to_json(orient="records", force_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
