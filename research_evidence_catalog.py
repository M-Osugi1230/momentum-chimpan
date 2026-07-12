"""Validate and render the governed research evidence catalog.

The catalog prevents a historical result from being interpreted in isolation when
later, higher-precedence evidence conflicts with it. It is deliberately separate
from production strategy configuration and can never activate a weight change.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml

CATALOG_VERSION = "2026-07-12-research-evidence-catalog-v1"
DEFAULT_CATALOG = "research/evidence_catalog.yaml"
DEFAULT_MARKDOWN = "research/evidence_catalog.md"

ALLOWED_EVIDENCE_CLASSES = {
    "PROSPECTIVE_LIVE",
    "EXPANDED_DISJOINT_HISTORICAL",
    "DISJOINT_CROSS_FOLD_HISTORICAL",
    "SINGLE_HOLDOUT_HISTORICAL",
}
ALLOWED_COMPLETION_STATES = {"COMPLETE", "ACCUMULATING"}
ALLOWED_STATUSES = {
    "REMOVAL_HURTS_VALIDATED",
    "DIRECTIONALLY_SUPPORTED",
    "ROBUSTLY_SUPPORTED",
    "NOT_SUPPORTED",
    "ACCUMULATING",
}
HOLD_DECISION = "HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE"
CONFLICTED_CONSENSUS = "CONFLICTED_TIME_UNSTABLE"


def load_catalog(path: str | Path = DEFAULT_CATALOG) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("evidence catalog root must be a mapping")
    return payload


def parse_date(value: Any, field: str, errors: list[str]) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        errors.append(f"{field} must be an ISO date: {value!r}")
        return None


def require_false(mapping: dict[str, Any], key: str, scope: str, errors: list[str]) -> None:
    if mapping.get(key) is not False:
        errors.append(f"{scope}.{key} must be false")


def _safe_path(root: Path, raw_path: str, scope: str, errors: list[str]) -> Path | None:
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        errors.append(f"{scope} contains an unsafe result path: {raw_path}")
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        errors.append(f"{scope} escapes repository root: {raw_path}")
        return None
    return resolved


def validate_catalog(catalog: dict[str, Any], repository_root: str | Path = ".") -> list[str]:
    errors: list[str] = []
    root = Path(repository_root)

    if catalog.get("catalog_version") != CATALOG_VERSION:
        errors.append(
            f"catalog_version must be {CATALOG_VERSION!r}, got {catalog.get('catalog_version')!r}"
        )
    updated_at = parse_date(catalog.get("updated_at"), "updated_at", errors)

    subject = catalog.get("subject")
    if not isinstance(subject, dict):
        errors.append("subject must be a mapping")
        subject = {}
    if subject.get("id") != "volume_ratio_score_component":
        errors.append("subject.id must be volume_ratio_score_component")
    if subject.get("current_production_weight_points") != 15:
        errors.append("subject.current_production_weight_points must remain 15")
    if subject.get("current_decision") != HOLD_DECISION:
        errors.append(f"subject.current_decision must be {HOLD_DECISION}")
    if subject.get("historical_consensus") != CONFLICTED_CONSENSUS:
        errors.append(f"subject.historical_consensus must be {CONFLICTED_CONSENSUS}")
    if subject.get("current_research_status") != "UNRESOLVED":
        errors.append("subject.current_research_status must be UNRESOLVED")
    if subject.get("next_decision_trigger") != "FORWARD_EVIDENCE_GATE_COMPLETION":
        errors.append("subject.next_decision_trigger must be FORWARD_EVIDENCE_GATE_COMPLETION")
    require_false(subject, "promotion_evidence_allowed", "subject", errors)
    require_false(subject, "automatic_weight_change_allowed", "subject", errors)
    require_false(subject, "automatic_strategy_change_allowed", "subject", errors)
    if subject.get("manual_review_required") is not True:
        errors.append("subject.manual_review_required must be true")

    precedence = catalog.get("precedence")
    if not isinstance(precedence, list) or not precedence:
        errors.append("precedence must be a non-empty list")
        precedence = []
    if len(precedence) != len(set(precedence)):
        errors.append("precedence contains duplicate evidence classes")
    if set(precedence) != ALLOWED_EVIDENCE_CLASSES:
        errors.append(
            "precedence must contain each governed evidence class exactly once"
        )
    if precedence and precedence[0] != "PROSPECTIVE_LIVE":
        errors.append("PROSPECTIVE_LIVE must have highest evidence precedence")

    decision_rules = catalog.get("decision_rules")
    if not isinstance(decision_rules, dict):
        errors.append("decision_rules must be a mapping")
        decision_rules = {}
    supportive = set(decision_rules.get("supportive_statuses") or [])
    non_supportive = set(decision_rules.get("non_supportive_statuses") or [])
    unresolved = set(decision_rules.get("unresolved_statuses") or [])
    if not supportive or not supportive.issubset(ALLOWED_STATUSES):
        errors.append("decision_rules.supportive_statuses are invalid")
    if non_supportive != {"NOT_SUPPORTED"}:
        errors.append("decision_rules.non_supportive_statuses must contain NOT_SUPPORTED")
    if unresolved != {"ACCUMULATING"}:
        errors.append("decision_rules.unresolved_statuses must contain ACCUMULATING")
    if decision_rules.get("when_historical_conflict_exists") != HOLD_DECISION:
        errors.append("historical conflict must force the hold decision")
    if decision_rules.get("when_forward_is_accumulating") != HOLD_DECISION:
        errors.append("accumulating forward evidence must force the hold decision")
    for key in (
        "require_forward_gate_before_weight_research",
        "forbid_recent_window_cherry_picking",
        "forbid_automatic_activation",
    ):
        if decision_rules.get(key) is not True:
            errors.append(f"decision_rules.{key} must be true")

    studies = catalog.get("studies")
    if not isinstance(studies, list) or not studies:
        errors.append("studies must be a non-empty list")
        studies = []

    study_ids: set[str] = set()
    study_by_id: dict[str, dict[str, Any]] = {}
    historical_supportive = False
    historical_non_supportive = False
    latest_catalog_date: date | None = None

    for index, raw_study in enumerate(studies):
        scope = f"studies[{index}]"
        if not isinstance(raw_study, dict):
            errors.append(f"{scope} must be a mapping")
            continue
        study = raw_study
        study_id = str(study.get("id") or "")
        if not study_id:
            errors.append(f"{scope}.id is required")
            continue
        if study_id in study_ids:
            errors.append(f"duplicate study id: {study_id}")
        study_ids.add(study_id)
        study_by_id[study_id] = study

        evidence_class = study.get("evidence_class")
        if evidence_class not in ALLOWED_EVIDENCE_CLASSES:
            errors.append(f"{scope}.evidence_class is invalid: {evidence_class!r}")
        if evidence_class not in precedence:
            errors.append(f"{scope}.evidence_class is missing from precedence")

        source_pr = study.get("source_pr")
        if not isinstance(source_pr, int) or source_pr <= 0:
            errors.append(f"{scope}.source_pr must be a positive integer")

        registered_at = parse_date(study.get("registered_at"), f"{scope}.registered_at", errors)
        completed_at = parse_date(study.get("completed_at"), f"{scope}.completed_at", errors)
        eligible_from = parse_date(
            study.get("eligible_signal_date_from"),
            f"{scope}.eligible_signal_date_from",
            errors,
        )
        for candidate in (registered_at, completed_at, eligible_from):
            if candidate is not None and (latest_catalog_date is None or candidate > latest_catalog_date):
                latest_catalog_date = candidate
        if registered_at and completed_at and completed_at < registered_at:
            errors.append(f"{scope}.completed_at cannot precede registered_at")

        completion_state = study.get("completion_state")
        status = study.get("status")
        if completion_state not in ALLOWED_COMPLETION_STATES:
            errors.append(f"{scope}.completion_state is invalid: {completion_state!r}")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{scope}.status is invalid: {status!r}")
        if completion_state == "COMPLETE":
            if completed_at is None:
                errors.append(f"{scope}.completed_at is required for a complete study")
            if status == "ACCUMULATING":
                errors.append(f"{scope} cannot be complete with ACCUMULATING status")
            for metric in (
                "primary_delta_excess_return",
                "two_sided_p_value",
                "confidence_interval_low",
                "confidence_interval_high",
            ):
                if study.get(metric) is None:
                    errors.append(f"{scope}.{metric} is required for a complete study")
        if completion_state == "ACCUMULATING":
            if completed_at is not None:
                errors.append(f"{scope}.completed_at must be null while accumulating")
            if status != "ACCUMULATING":
                errors.append(f"{scope}.status must be ACCUMULATING while accumulating")

        if evidence_class == "PROSPECTIVE_LIVE":
            if eligible_from is None:
                errors.append(f"{scope}.eligible_signal_date_from is required")
            elif registered_at is not None and eligible_from <= registered_at:
                errors.append(
                    f"{scope}.eligible_signal_date_from must be after registration"
                )
            if study.get("evidence_origin") != "LIVE_FORWARD_RANKING_HISTORY":
                errors.append(f"{scope}.evidence_origin must be LIVE_FORWARD_RANKING_HISTORY")
        elif eligible_from is not None:
            errors.append(f"{scope}.eligible_signal_date_from is only valid for prospective evidence")

        require_false(study, "promotion_evidence_allowed", scope, errors)
        require_false(study, "automatic_weight_change_allowed", scope, errors)

        result_files = study.get("result_files")
        if not isinstance(result_files, list):
            errors.append(f"{scope}.result_files must be a list")
            result_files = []
        for file_index, raw_path in enumerate(result_files):
            if not isinstance(raw_path, str) or not raw_path:
                errors.append(f"{scope}.result_files[{file_index}] must be a path string")
                continue
            resolved = _safe_path(root, raw_path, scope, errors)
            if resolved is not None and not resolved.is_file():
                errors.append(f"{scope} references a missing result file: {raw_path}")

        artifact_hash = study.get("source_artifact_sha256")
        if artifact_hash is not None:
            if not isinstance(artifact_hash, str) or len(artifact_hash) != 64:
                errors.append(f"{scope}.source_artifact_sha256 must be a 64-character hash")
            else:
                try:
                    int(artifact_hash, 16)
                except ValueError:
                    errors.append(f"{scope}.source_artifact_sha256 must be hexadecimal")

        if evidence_class != "PROSPECTIVE_LIVE" and completion_state == "COMPLETE":
            if status in supportive:
                historical_supportive = True
            if status in non_supportive:
                historical_non_supportive = True

    governing_id = subject.get("governing_study_id")
    governing = study_by_id.get(str(governing_id))
    if governing is None:
        errors.append("subject.governing_study_id must reference an existing study")
    else:
        if governing.get("evidence_class") != "PROSPECTIVE_LIVE":
            errors.append("the governing study must be PROSPECTIVE_LIVE")
        if governing.get("status") == "ACCUMULATING" and subject.get("current_decision") != HOLD_DECISION:
            errors.append("accumulating governing evidence must keep the production weight unchanged")

    if historical_supportive and historical_non_supportive:
        if subject.get("historical_consensus") != CONFLICTED_CONSENSUS:
            errors.append("conflicting historical evidence must be labeled CONFLICTED_TIME_UNSTABLE")
        if subject.get("current_decision") != HOLD_DECISION:
            errors.append("conflicting historical evidence must block a weight change")

    if updated_at and latest_catalog_date and updated_at < latest_catalog_date:
        errors.append("updated_at cannot precede the latest study date")

    return errors


def _format_percent(value: Any, *, points: bool = False) -> str:
    if value is None:
        return "—"
    numeric = float(value)
    suffix = "pt" if points else "%"
    return f"{numeric * 100:+.2f}{suffix}"


def _format_p(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def render_markdown(catalog: dict[str, Any]) -> str:
    subject = catalog["subject"]
    precedence = catalog["precedence"]
    studies = catalog["studies"]

    lines = [
        "# Research Evidence Catalog",
        "",
        f"- Catalog version: `{catalog['catalog_version']}`",
        f"- Updated: `{catalog['updated_at']}`",
        f"- Subject: **{subject['label']}**",
        "",
        "## Current governed decision",
        "",
        "| Item | Current state |",
        "|---|---|",
        f"| Production weight | {subject['current_production_weight_points']} points |",
        f"| Decision | `{subject['current_decision']}` |",
        f"| Historical consensus | `{subject['historical_consensus']}` |",
        f"| Research status | `{subject['current_research_status']}` |",
        f"| Governing study | `{subject['governing_study_id']}` |",
        f"| Next trigger | `{subject['next_decision_trigger']}` |",
        "| Automatic weight change | **Forbidden** |",
        "| Automatic strategy change | **Forbidden** |",
        "",
        f"> {subject['decision_reason']}",
        "",
        "## Evidence precedence",
        "",
    ]
    for index, item in enumerate(precedence, start=1):
        lines.append(f"{index}. `{item}`")

    lines.extend([
        "",
        "## Study chronology",
        "",
        "| Study | Class | PR | Status | Universe / folds | Delta excess | p-value | CI |",
        "|---|---|---:|---|---|---:|---:|---|",
    ])
    for study in studies:
        universe = study.get("universe_size")
        folds = study.get("fold_count")
        sample = "—" if universe is None else f"{universe} symbols"
        if folds is not None:
            sample += f" / {folds} fold" + ("s" if int(folds) != 1 else "")
        ci_low = _format_percent(study.get("confidence_interval_low"))
        ci_high = _format_percent(study.get("confidence_interval_high"))
        ci = "—" if ci_low == "—" and ci_high == "—" else f"{ci_low} to {ci_high}"
        lines.append(
            "| "
            + " | ".join([
                f"`{study['id']}`",
                f"`{study['evidence_class']}`",
                str(study["source_pr"]),
                f"`{study['status']}`",
                sample,
                _format_percent(study.get("primary_delta_excess_return"), points=True),
                _format_p(study.get("two_sided_p_value")),
                ci,
            ])
            + " |"
        )

    lines.extend([
        "",
        "## Interpretation",
        "",
    ])
    for study in studies:
        lines.append(f"- **{study['label']}**: {study['interpretation']}")

    lines.extend([
        "",
        "## Decision guardrails",
        "",
        "- Historical evidence is conflicting and time-unstable.",
        "- The current 15-point weight remains unchanged while prospective evidence accumulates.",
        "- A favorable recent subperiod cannot be selected after observing the results.",
        "- Historical results cannot independently authorize a promotion or weight change.",
        "- Any future change requires the prospective evidence gate and manual review.",
        "",
        "## Machine-readable source",
        "",
        "The canonical source is [`research/evidence_catalog.yaml`](evidence_catalog.yaml).",
        "",
    ])
    return "\n".join(lines)


def write_markdown(catalog: dict[str, Any], output_path: str | Path) -> None:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(catalog), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate governed research evidence")
    parser.add_argument("command", choices=("validate", "render"))
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--output", default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    catalog = load_catalog(args.catalog)
    errors = validate_catalog(catalog, args.repository_root)
    if errors:
        if args.json:
            print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        else:
            for error in errors:
                print(f"ERROR: {error}")
        return 1
    if args.command == "render":
        write_markdown(catalog, args.output)
    if args.json:
        print(json.dumps({"valid": True, "study_count": len(catalog["studies"])}, ensure_ascii=False))
    else:
        print(f"research evidence catalog valid: {len(catalog['studies'])} studies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
