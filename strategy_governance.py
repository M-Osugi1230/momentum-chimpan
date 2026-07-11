"""Govern strategy changes and research experiment promotion.

Only strategy-relevant AST nodes and selected config sections are fingerprinted,
so operational and presentation changes do not automatically require an
experiment. Strategy changes must be registered and can never be promoted
without robust evidence plus explicit manual approval.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

import main

GOVERNANCE_VERSION = "2026-07-11-strategy-governance-v1"
DEFAULT_REGISTRY = "research/experiment_registry.yaml"
DEFAULT_ROBUSTNESS = "output/replay/replay_robustness_summary.csv"
DEFAULT_OUTPUT_DIR = "output/research"
STRATEGY_CONFIG_SECTIONS = ("market", "ranking", "signals")
STRATEGY_CONSTANT_PREFIXES = ("PAPER_",)
STRATEGY_FUNCTION_KEYWORDS = (
    "score",
    "priority",
    "expectancy",
    "regime",
    "sector_momentum",
    "sector_leader",
    "signal_governance",
    "adaptive_threshold",
    "paper_target",
    "paper_trade",
    "mark_paper",
    "risk_budget",
    "release_readiness",
)
NON_STRATEGY_FUNCTION_PREFIXES = ("plain_", "html_", "fmt_", "load_", "write_")
NON_STRATEGY_FUNCTION_SUFFIXES = ("_text", "_count", "_section", "_columns")
ALLOWED_STATUSES = {"production", "proposed", "running", "evidence_ready", "rejected", "promoted"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def strategy_function_selected(name: str) -> bool:
    if name.startswith(NON_STRATEGY_FUNCTION_PREFIXES) or name.endswith(NON_STRATEGY_FUNCTION_SUFFIXES):
        return False
    return any(keyword in name for keyword in STRATEGY_FUNCTION_KEYWORDS)


def extract_strategy_ast(source: str) -> dict[str, Any]:
    tree = ast.parse(source)
    constants: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            if any(name.startswith(STRATEGY_CONSTANT_PREFIXES) for name in names):
                constants.append({
                    "names": sorted(names),
                    "ast": ast.dump(node, annotate_fields=True, include_attributes=False),
                })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and strategy_function_selected(node.name):
            functions.append({
                "name": node.name,
                "ast": ast.dump(node, annotate_fields=True, include_attributes=False),
            })
    return {
        "constants": sorted(constants, key=lambda item: item["names"]),
        "functions": sorted(functions, key=lambda item: item["name"]),
    }


def load_strategy_config(path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    parsed = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return {section: parsed.get(section) for section in STRATEGY_CONFIG_SECTIONS if section in parsed}


def strategy_fingerprint(main_path: str = "main.py", config_path: str = "config.yaml") -> dict[str, Any]:
    source = Path(main_path).read_text(encoding="utf-8")
    payload = {
        "strategy_ast": extract_strategy_ast(source),
        "strategy_config": load_strategy_config(config_path),
    }
    serialized = canonical_json(payload)
    return {
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "selected_function_count": len(payload["strategy_ast"]["functions"]),
        "selected_constant_count": len(payload["strategy_ast"]["constants"]),
        "config_sections": sorted(payload["strategy_config"].keys()),
        "payload": payload,
    }


def load_registry(path: str = DEFAULT_REGISTRY) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"experiment registry not found: {path}")
    registry = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(registry, dict):
        raise ValueError("experiment registry must be a mapping")
    registry.setdefault("policy", {})
    registry.setdefault("experiments", [])
    return registry


def validate_registry(registry: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    experiments = registry.get("experiments", [])
    if not isinstance(experiments, list):
        return [{"severity": "FAIL", "experiment_id": "", "issue": "experiments must be a list"}]
    seen: set[str] = set()
    for experiment in experiments:
        experiment_id = str(experiment.get("experiment_id", "")).strip()
        if not experiment_id:
            issues.append({"severity": "FAIL", "experiment_id": "", "issue": "experiment_id is required"})
            continue
        if experiment_id in seen:
            issues.append({"severity": "FAIL", "experiment_id": experiment_id, "issue": "duplicate experiment_id"})
        seen.add(experiment_id)
        status = str(experiment.get("status", "")).strip()
        if status not in ALLOWED_STATUSES:
            issues.append({"severity": "FAIL", "experiment_id": experiment_id, "issue": f"invalid status: {status}"})
        for field in ("hypothesis", "strategy_fingerprint", "change_summary"):
            if not str(experiment.get(field, "")).strip():
                issues.append({"severity": "FAIL", "experiment_id": experiment_id, "issue": f"{field} is required"})
        scope = experiment.get("evidence_scope", {}) or {}
        if not all(key in scope for key in ("group_type", "group_value", "horizon_days")):
            issues.append({"severity": "FAIL", "experiment_id": experiment_id, "issue": "complete evidence_scope is required"})
    return issues


def load_robustness(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    return pd.read_csv(target)


def matching_evidence(experiment: dict[str, Any], robustness: pd.DataFrame) -> dict[str, Any]:
    if robustness is None or robustness.empty:
        return {}
    scope = experiment.get("evidence_scope", {}) or {}
    horizon = pd.to_numeric(pd.Series([scope.get("horizon_days")]), errors="coerce").iloc[0]
    if pd.isna(horizon):
        return {}
    subset = robustness[
        (robustness.get("group_type", pd.Series(dtype=str)).astype(str) == str(scope.get("group_type")))
        & (robustness.get("group_value", pd.Series(dtype=str)).astype(str) == str(scope.get("group_value")))
        & (pd.to_numeric(robustness.get("horizon_days", pd.Series(dtype=float)), errors="coerce") == int(horizon))
    ]
    return {} if subset.empty else subset.iloc[0].to_dict()


def approval_valid(experiment: dict[str, Any]) -> bool:
    approval = experiment.get("manual_approval", {}) or {}
    return bool(
        approval.get("approved") is True
        and str(approval.get("approved_by", "")).strip()
        and str(approval.get("approved_at", "")).strip()
    )


def evidence_eligibility(experiment: dict[str, Any], policy: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    count = int(pd.to_numeric(pd.Series([evidence.get("count")]), errors="coerce").fillna(0).iloc[0]) if evidence else 0
    q_value_raw = pd.to_numeric(pd.Series([evidence.get("fdr_q_value")]), errors="coerce").iloc[0] if evidence else float("nan")
    q_value = None if pd.isna(q_value_raw) else float(q_value_raw)
    status = str(evidence.get("robustness_status", "")) if evidence else ""
    early = pd.to_numeric(pd.Series([evidence.get("early_net_average_excess")]), errors="coerce").iloc[0] if evidence else float("nan")
    late = pd.to_numeric(pd.Series([evidence.get("late_net_average_excess")]), errors="coerce").iloc[0] if evidence else float("nan")
    leave_sector = pd.to_numeric(pd.Series([evidence.get("worst_leave_one_sector_excess")]), errors="coerce").iloc[0] if evidence else float("nan")
    criteria = {
        "minimum_outcome_count": count >= int(policy.get("minimum_outcome_count", 100)),
        "required_robustness_status": status == str(policy.get("required_robustness_status", "ROBUST")),
        "maximum_fdr_q_value": q_value is not None and q_value <= float(policy.get("maximum_fdr_q_value", 0.05)),
        "positive_early_period": (not policy.get("require_positive_early_period", True)) or (pd.notna(early) and float(early) > 0),
        "positive_late_period": (not policy.get("require_positive_late_period", True)) or (pd.notna(late) and float(late) > 0),
        "positive_leave_one_sector": (not policy.get("require_positive_leave_one_sector", True)) or (pd.notna(leave_sector) and float(leave_sector) > 0),
    }
    return {
        "eligible": all(criteria.values()),
        "criteria": criteria,
        "count": count,
        "fdr_q_value": q_value,
        "robustness_status": status,
    }


def audit_registry(
    registry: dict[str, Any],
    current_fingerprint: str,
    robustness: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    policy = registry.get("policy", {}) or {}
    audit_rows: list[dict[str, Any]] = []
    issue_rows = validate_registry(registry)
    for experiment in registry.get("experiments", []):
        experiment_id = str(experiment.get("experiment_id", ""))
        fingerprint = str(experiment.get("strategy_fingerprint", ""))
        resolved_fingerprint = current_fingerprint if fingerprint == "CURRENT" else fingerprint
        evidence = matching_evidence(experiment, robustness)
        eligibility = evidence_eligibility(experiment, policy, evidence)
        approved = approval_valid(experiment)
        status = str(experiment.get("status", ""))
        promotion_valid = status != "promoted" or (
            eligibility["eligible"]
            and (not policy.get("require_manual_approval", True) or approved)
            and resolved_fingerprint == current_fingerprint
        )
        if not promotion_valid:
            issue_rows.append({
                "severity": "FAIL",
                "experiment_id": experiment_id,
                "issue": "promoted experiment lacks matching fingerprint, robust evidence, or manual approval",
            })
        audit_rows.append({
            "experiment_id": experiment_id,
            "experiment_type": experiment.get("experiment_type"),
            "status": status,
            "registered_fingerprint": fingerprint,
            "resolved_fingerprint": resolved_fingerprint,
            "matches_current_strategy": resolved_fingerprint == current_fingerprint,
            "evidence_status": eligibility["robustness_status"],
            "evidence_count": eligibility["count"],
            "evidence_fdr_q_value": eligibility["fdr_q_value"],
            "evidence_eligible": eligibility["eligible"],
            "manual_approval_valid": approved,
            "promotion_valid": promotion_valid,
            "automatic_promotion": False,
        })
    return pd.DataFrame(audit_rows), pd.DataFrame(issue_rows)


def ci_strategy_change_check(
    base_main: str,
    current_main: str,
    base_config: str,
    current_config: str,
    registry: dict[str, Any],
) -> dict[str, Any]:
    base = strategy_fingerprint(base_main, base_config)
    current = strategy_fingerprint(current_main, current_config)
    changed = base["sha256"] != current["sha256"]
    matching = []
    if changed:
        for experiment in registry.get("experiments", []):
            if str(experiment.get("strategy_fingerprint")) == current["sha256"] and str(experiment.get("status")) in {
                "proposed", "running", "evidence_ready", "promoted"
            }:
                matching.append(str(experiment.get("experiment_id")))
    return {
        "base_fingerprint": base["sha256"],
        "current_fingerprint": current["sha256"],
        "strategy_changed": changed,
        "matching_experiments": matching,
        "passed": (not changed) or bool(matching),
    }


def write_snapshot(output_path: str, main_path: str = "main.py", config_path: str = "config.yaml") -> dict[str, Any]:
    fingerprint = strategy_fingerprint(main_path, config_path)
    result = {
        "governance_version": GOVERNANCE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "strategy_fingerprint": fingerprint["sha256"],
        "selected_function_count": fingerprint["selected_function_count"],
        "selected_constant_count": fingerprint["selected_constant_count"],
        "config_sections": fingerprint["config_sections"],
        "research_only": True,
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def write_audit(output_dir: str, registry_path: str, robustness_path: str) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    fingerprint = strategy_fingerprint()
    registry = load_registry(registry_path)
    robustness = load_robustness(robustness_path)
    audit, issues = audit_registry(registry, fingerprint["sha256"], robustness)
    manifest = {
        "governance_version": GOVERNANCE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy_fingerprint": fingerprint["sha256"],
        "experiment_count": len(audit),
        "issue_count": len(issues),
        "automatic_promotion": False,
        "research_only": True,
    }
    audit.to_csv(target / "strategy_experiment_audit.csv", index=False)
    issues.to_csv(target / "strategy_governance_issues.csv", index=False)
    (target / "strategy_governance_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with pd.ExcelWriter(target / "strategy_governance.xlsx", engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Governance Summary", index=False)
        audit.to_excel(writer, sheet_name="Experiment Audit", index=False)
        issues.to_excel(writer, sheet_name="Issues", index=False)
        pd.DataFrame([registry.get("policy", {})]).to_excel(writer, sheet_name="Promotion Policy", index=False)
    return {"manifest": manifest, "audit": audit, "issues": issues}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Govern strategy experiments")
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--output", default="data/strategy_fingerprint.json")
    snapshot.add_argument("--main", default="main.py")
    snapshot.add_argument("--config", default="config.yaml")

    audit = sub.add_parser("audit")
    audit.add_argument("--registry", default=DEFAULT_REGISTRY)
    audit.add_argument("--robustness", default=DEFAULT_ROBUSTNESS)
    audit.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    audit.add_argument("--strict", action="store_true")

    ci = sub.add_parser("ci-check")
    ci.add_argument("--base-main", required=True)
    ci.add_argument("--current-main", default="main.py")
    ci.add_argument("--base-config", required=True)
    ci.add_argument("--current-config", default="config.yaml")
    ci.add_argument("--registry", default=DEFAULT_REGISTRY)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "snapshot":
        result = write_snapshot(args.output, args.main, args.config)
    elif args.command == "audit":
        result = write_audit(args.output_dir, args.registry, args.robustness)
        if args.strict and not result["issues"].empty:
            raise RuntimeError("strategy governance audit failed")
        result = result["manifest"]
    else:
        result = ci_strategy_change_check(
            args.base_main,
            args.current_main,
            args.base_config,
            args.current_config,
            load_registry(args.registry),
        )
        if not result["passed"]:
            raise RuntimeError(
                "strategy fingerprint changed without a registered experiment matching the current fingerprint"
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
