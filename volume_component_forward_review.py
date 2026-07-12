"""Build a manual review packet for the volume-ratio score component.

The packet combines the canonical evidence catalog with the signed prospective
status. It is informational only: it cannot change score weights, strategy
configuration, production state, or trading behavior. A final human decision is
required even when the forward evidence gate has completed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import evidence_provenance
import research_evidence_catalog as catalog_module
import volume_component_forward_status as status_module

REVIEW_VERSION = "2026-07-12-volume-component-forward-review-v1"
DEFAULT_STATUS = "data/volume_component_forward_status.json"
DEFAULT_CATALOG = "research/evidence_catalog.yaml"
DEFAULT_OUTPUT_DIR = "output/volume-component-forward-review"
READY_STATUS = "READY_FOR_HUMAN_WEIGHT_REVIEW"
NOT_READY_STATUS = "NOT_READY"


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence_interpretation(evidence_status: str) -> str:
    mapping = {
        "ACCUMULATING": "INSUFFICIENT_FORWARD_EVIDENCE",
        "DIRECTIONALLY_SUPPORTED": "DIRECTIONAL_COMPONENT_SUPPORT_ONLY",
        "ROBUSTLY_SUPPORTED": "ROBUST_COMPONENT_CONTRIBUTION_SUPPORT",
        "NOT_SUPPORTED": "COMPONENT_CONTRIBUTION_NOT_SUPPORTED",
    }
    return mapping.get(evidence_status, "UNKNOWN")


def horizon_summary(status: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("10", "20"):
        record = dict((status.get("horizons") or {}).get(key) or {})
        rows.append({
            "horizon_days": int(key),
            "baseline_outcome_count": int(
                record.get("baseline_outcome_count", 0) or 0
            ),
            "tested_outcome_count": int(
                record.get("tested_outcome_count", 0) or 0
            ),
            "required_outcomes_per_variant": int(
                record.get("required_outcomes_per_variant", 100) or 100
            ),
            "paired_date_count": int(
                record.get("paired_date_count", 0) or 0
            ),
            "required_paired_dates": int(
                record.get("required_paired_dates", 20) or 20
            ),
            "sample_adequate": record.get("sample_adequate") is True,
            "mean_daily_difference": record.get("mean_daily_difference"),
            "early_mean_difference": record.get("early_mean_difference"),
            "late_mean_difference": record.get("late_mean_difference"),
            "ci_low": record.get("ci_low"),
            "ci_high": record.get("ci_high"),
            "two_sided_p_value": record.get("two_sided_p_value"),
            "harm_p_value": record.get("harm_p_value"),
        })
    return rows


def study_chronology(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for study in catalog.get("studies") or []:
        rows.append({
            "study_id": study.get("id", ""),
            "label": study.get("label", ""),
            "evidence_class": study.get("evidence_class", ""),
            "source_pr": study.get("source_pr"),
            "status": study.get("status", ""),
            "universe_size": study.get("universe_size"),
            "fold_count": study.get("fold_count"),
            "primary_delta_excess_return": study.get(
                "primary_delta_excess_return"
            ),
            "two_sided_p_value": study.get("two_sided_p_value"),
            "interpretation": study.get("interpretation", ""),
        })
    return rows


def criterion(
    name: str,
    passed: bool,
    actual: Any,
    required: Any,
    blocking: bool = True,
) -> dict[str, Any]:
    return {
        "criterion": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
        "blocking": bool(blocking),
    }


def build_review_packet(
    status_path: str = DEFAULT_STATUS,
    catalog_path: str = DEFAULT_CATALOG,
    repository_root: str = ".",
    current_fingerprint: str | None = None,
) -> dict[str, Any]:
    signed_status = status_module.load_json(status_path)
    status_errors = status_module.validate_status(signed_status)
    catalog = catalog_module.load_catalog(catalog_path)
    catalog_errors = catalog_module.validate_catalog(
        catalog, repository_root
    )

    subject = dict(catalog.get("subject") or {})
    governing_id = str(subject.get("governing_study_id") or "")
    evidence_status = str(
        signed_status.get("evidence_status") or "ACCUMULATING"
    )
    horizons = horizon_summary(signed_status)
    all_horizons_adequate = bool(
        horizons and all(row["sample_adequate"] for row in horizons)
    )
    finalized = evidence_status != "ACCUMULATING"
    resolved_fingerprint = (
        str(current_fingerprint)
        if current_fingerprint is not None
        else evidence_provenance.current_strategy_fingerprint()
    )
    signed_fingerprint = str(
        signed_status.get("strategy_fingerprint") or ""
    )
    fingerprint_consistent = bool(
        resolved_fingerprint
        and signed_fingerprint
        and resolved_fingerprint == signed_fingerprint
    )
    catalog_governed = bool(
        governing_id == status_module.STUDY_ID
        and subject.get("current_production_weight_points") == 15
        and subject.get("current_decision")
        == catalog_module.HOLD_DECISION
        and subject.get("historical_consensus")
        == catalog_module.CONFLICTED_CONSENSUS
    )
    prospective_valid = bool(
        signed_status.get("eligible_signal_date_from") == "2026-07-13"
        and signed_status.get("evidence_origin")
        == "LIVE_FORWARD_RANKING_HISTORY"
        and signed_status.get("entry_model")
        == "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"
        and signed_status.get("same_day_close_entry_allowed") is False
    )
    governance_locked = bool(
        signed_status.get("promotion_evidence_allowed") is False
        and signed_status.get("automatic_weight_change") is False
        and signed_status.get("automatic_strategy_change") is False
        and signed_status.get("manual_review_required") is True
        and signed_status.get("research_only") is True
        and signed_status.get("production_state_mutations") == []
        and subject.get("promotion_evidence_allowed") is False
        and subject.get("automatic_weight_change_allowed") is False
        and subject.get("automatic_strategy_change_allowed") is False
        and subject.get("manual_review_required") is True
    )

    criteria = [
        criterion(
            "signed_forward_status_integrity",
            not status_errors,
            "PASS" if not status_errors else " / ".join(status_errors),
            "valid SHA-256 envelope and governed fields",
        ),
        criterion(
            "canonical_evidence_catalog_integrity",
            not catalog_errors,
            "PASS" if not catalog_errors else " / ".join(catalog_errors),
            "valid canonical catalog and referenced result files",
        ),
        criterion(
            "governing_study_alignment",
            catalog_governed,
            governing_id,
            status_module.STUDY_ID,
        ),
        criterion(
            "prospective_execution_provenance",
            prospective_valid,
            (
                f"origin={signed_status.get('evidence_origin')} "
                f"entry={signed_status.get('entry_model')} "
                f"cutoff={signed_status.get('eligible_signal_date_from')}"
            ),
            "live forward history from 2026-07-13 and next-session open",
        ),
        criterion(
            "required_horizon_samples",
            all_horizons_adequate,
            "; ".join(
                f"{row['horizon_days']}d="
                f"{min(row['baseline_outcome_count'], row['tested_outcome_count'])}/"
                f"{row['required_outcomes_per_variant']} outcomes, "
                f"{row['paired_date_count']}/{row['required_paired_dates']} dates"
                for row in horizons
            ),
            "10d and 20d samples adequate",
        ),
        criterion(
            "forward_evidence_finalized",
            finalized,
            evidence_status,
            "DIRECTIONALLY_SUPPORTED, ROBUSTLY_SUPPORTED, or NOT_SUPPORTED",
        ),
        criterion(
            "strategy_fingerprint_consistency",
            fingerprint_consistent,
            (
                f"current={resolved_fingerprint or 'MISSING'} "
                f"evidence={signed_fingerprint or 'MISSING'}"
            ),
            "current strategy fingerprint equals signed evidence fingerprint",
        ),
        criterion(
            "historical_conflict_acknowledged",
            subject.get("historical_consensus")
            == catalog_module.CONFLICTED_CONSENSUS,
            subject.get("historical_consensus", "MISSING"),
            catalog_module.CONFLICTED_CONSENSUS,
        ),
        criterion(
            "automatic_changes_locked",
            governance_locked,
            (
                f"weight={signed_status.get('automatic_weight_change')} "
                f"strategy={signed_status.get('automatic_strategy_change')}"
            ),
            "all automatic changes disabled; manual review required",
        ),
    ]
    ready = all(
        item["passed"]
        for item in criteria
        if item.get("blocking") is True
    )
    packet_status = READY_STATUS if ready else NOT_READY_STATUS

    core = {
        "review_version": REVIEW_VERSION,
        "status": packet_status,
        "subject_id": subject.get("id", ""),
        "subject_label": subject.get("label", ""),
        "current_weight_points": int(
            subject.get("current_production_weight_points", 15)
        ),
        "current_governed_decision": subject.get(
            "current_decision", catalog_module.HOLD_DECISION
        ),
        "historical_consensus": subject.get(
            "historical_consensus", "UNKNOWN"
        ),
        "governing_study_id": governing_id,
        "evidence_status": evidence_status,
        "evidence_interpretation": evidence_interpretation(
            evidence_status
        ),
        "strategy_fingerprint": resolved_fingerprint,
        "signed_status_strategy_fingerprint": signed_fingerprint,
        "signed_status_sha256": sha256_file(status_path),
        "signed_status_envelope_sha256": signed_status.get(
            "status_sha256", ""
        ),
        "signed_evidence_fingerprint": signed_status.get(
            "evidence_fingerprint", ""
        ),
        "evidence_catalog_sha256": sha256_file(catalog_path),
        "criteria": criteria,
        "horizons": horizons,
        "study_chronology": study_chronology(catalog),
        "allowed_human_decisions": [
            "KEEP_15_POINTS",
            "CONTINUE_ACCUMULATING",
            "REGISTER_NEW_WEIGHT_EXPERIMENT",
            "REJECT_WEIGHT_CHANGE",
        ],
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "automatic_approval": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
    }
    packet = {
        **core,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "packet_fingerprint": canonical_hash(core),
    }
    packet["packet_sha256"] = canonical_hash(packet)
    return packet


def packet_markdown(packet: dict[str, Any]) -> str:
    lines = [
        "# Volume-Ratio Component Forward Evidence Review",
        "",
        f"- Status: **{packet['status']}**",
        f"- Current production weight: **{packet['current_weight_points']} points**",
        f"- Evidence status: `{packet['evidence_status']}`",
        f"- Interpretation: `{packet['evidence_interpretation']}`",
        f"- Historical consensus: `{packet['historical_consensus']}`",
        f"- Strategy fingerprint: `{packet['strategy_fingerprint']}`",
        f"- Signed status SHA-256: `{packet['signed_status_sha256']}`",
        f"- Packet SHA-256: `{packet['packet_sha256']}`",
        "",
        "## Readiness criteria",
        "",
        "| Criterion | Passed | Actual | Required |",
        "|---|---:|---|---|",
    ]
    for item in packet["criteria"]:
        actual = str(item["actual"]).replace("|", "/")
        required = str(item["required"]).replace("|", "/")
        lines.append(
            f"| {item['criterion']} | "
            f"{'YES' if item['passed'] else 'NO'} | "
            f"{actual} | {required} |"
        )
    lines.extend([
        "",
        "## Forward horizons",
        "",
        "| Horizon | Baseline | Removal | Paired dates | Mean delta | CI | p-value | Adequate |",
        "|---:|---:|---:|---:|---:|---|---:|---:|",
    ])
    for row in packet["horizons"]:
        mean_delta = row.get("mean_daily_difference")
        ci_low = row.get("ci_low")
        ci_high = row.get("ci_high")
        p_value = row.get("two_sided_p_value")
        lines.append(
            f"| {row['horizon_days']} | "
            f"{row['baseline_outcome_count']} | "
            f"{row['tested_outcome_count']} | "
            f"{row['paired_date_count']} | "
            f"{mean_delta if mean_delta is not None else '—'} | "
            f"{ci_low if ci_low is not None else '—'} to "
            f"{ci_high if ci_high is not None else '—'} | "
            f"{p_value if p_value is not None else '—'} | "
            f"{'YES' if row['sample_adequate'] else 'NO'} |"
        )
    lines.extend([
        "",
        "## Human decision boundary",
        "",
        "This packet does not change the 15-point production weight. It does not approve a new weight, remove the component, or activate a strategy. Any weight change requires a separately registered experiment and an explicit human decision tied to this exact packet and signed status.",
        "",
    ])
    return "\n".join(lines)


def write_packet(
    packet: dict[str, Any],
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "volume_component_forward_review.json"
    markdown_path = output / "volume_component_forward_review.md"
    excel_path = output / "volume_component_forward_review.xlsx"
    json_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        packet_markdown(packet), encoding="utf-8"
    )
    summary = pd.DataFrame([{
        "status": packet["status"],
        "current_weight_points": packet["current_weight_points"],
        "evidence_status": packet["evidence_status"],
        "evidence_interpretation": packet["evidence_interpretation"],
        "historical_consensus": packet["historical_consensus"],
        "strategy_fingerprint": packet["strategy_fingerprint"],
        "signed_status_sha256": packet["signed_status_sha256"],
        "packet_sha256": packet["packet_sha256"],
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "automatic_approval": False,
        "manual_review_required": True,
    }])
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Review Summary", index=False)
        pd.DataFrame(packet["criteria"]).to_excel(
            writer, sheet_name="Readiness Criteria", index=False
        )
        pd.DataFrame(packet["horizons"]).to_excel(
            writer, sheet_name="Forward Horizons", index=False
        )
        pd.DataFrame(packet["study_chronology"]).to_excel(
            writer, sheet_name="Evidence Chronology", index=False
        )
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[
                    column[0].column_letter
                ].width = min(
                    max(
                        (len(str(cell.value or "")) for cell in column),
                        default=8,
                    )
                    + 2,
                    64,
                )
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "excel": str(excel_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build manual volume-component forward review packet"
    )
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--current-fingerprint", default=None)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    packet = build_review_packet(
        args.status,
        args.catalog,
        args.repository_root,
        args.current_fingerprint,
    )
    paths = write_packet(packet, args.output_dir)
    print(
        json.dumps(
            {
                "status": packet["status"],
                "evidence_status": packet["evidence_status"],
                "packet_sha256": packet["packet_sha256"],
                "paths": paths,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
