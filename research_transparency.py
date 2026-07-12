"""Read-only research transparency helpers for the daily dashboard.

This module converts the canonical research evidence catalog into a compact
human-facing status panel. It never changes rankings, score weights, thresholds,
strategy fingerprints, or production state. Missing or invalid catalog data falls
back to a conservative HOLD/UNKNOWN presentation instead of failing the report.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

import research_evidence_catalog as evidence_catalog

DEFAULT_CATALOG = "research/evidence_catalog.yaml"
SHEET_NAME = "Research Evidence"


def _fallback(error: str = "") -> dict[str, Any]:
    return {
        "catalog_health": "WARN",
        "catalog_error": error,
        "subject_id": "volume_ratio_score_component",
        "subject_label": "出来高倍率スコア構成要素",
        "production_weight_points": 15,
        "decision": evidence_catalog.HOLD_DECISION,
        "historical_consensus": "UNKNOWN",
        "research_status": "UNKNOWN",
        "governing_study_id": "volume-component-forward-evidence-v1",
        "governing_study_status": "UNKNOWN",
        "next_decision_trigger": "FORWARD_EVIDENCE_GATE_COMPLETION",
        "automatic_weight_change_allowed": False,
        "automatic_strategy_change_allowed": False,
        "promotion_evidence_allowed": False,
        "manual_review_required": True,
        "decision_reason": "研究台帳を確認できないため、現行15点を維持して手動確認します。",
        "studies": [],
    }


def load_snapshot(
    catalog_path: str | Path = DEFAULT_CATALOG,
    repository_root: str | Path = ".",
) -> dict[str, Any]:
    try:
        catalog = evidence_catalog.load_catalog(catalog_path)
        errors = evidence_catalog.validate_catalog(catalog, repository_root)
        if errors:
            return _fallback(" / ".join(errors[:5]))
        subject = catalog["subject"]
        studies = list(catalog.get("studies") or [])
        governing = next(
            (
                study
                for study in studies
                if study.get("id") == subject.get("governing_study_id")
            ),
            {},
        )
        return {
            "catalog_health": "PASS",
            "catalog_error": "",
            "subject_id": subject.get("id", ""),
            "subject_label": subject.get("label", ""),
            "production_weight_points": int(
                subject.get("current_production_weight_points", 15)
            ),
            "decision": subject.get("current_decision", evidence_catalog.HOLD_DECISION),
            "historical_consensus": subject.get("historical_consensus", "UNKNOWN"),
            "research_status": subject.get("current_research_status", "UNKNOWN"),
            "governing_study_id": subject.get("governing_study_id", ""),
            "governing_study_status": governing.get("status", "UNKNOWN"),
            "next_decision_trigger": subject.get("next_decision_trigger", ""),
            "automatic_weight_change_allowed": bool(
                subject.get("automatic_weight_change_allowed", False)
            ),
            "automatic_strategy_change_allowed": bool(
                subject.get("automatic_strategy_change_allowed", False)
            ),
            "promotion_evidence_allowed": bool(
                subject.get("promotion_evidence_allowed", False)
            ),
            "manual_review_required": bool(
                subject.get("manual_review_required", True)
            ),
            "decision_reason": subject.get("decision_reason", ""),
            "studies": studies,
        }
    except Exception as exc:
        return _fallback(str(exc))


def summary_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "研究台帳状態": snapshot.get("catalog_health", "WARN"),
        "研究対象": snapshot.get("subject_label", "出来高倍率スコア構成要素"),
        "出来高倍率配点": snapshot.get("production_weight_points", 15),
        "研究判断": snapshot.get("decision", evidence_catalog.HOLD_DECISION),
        "歴史エビデンス": snapshot.get("historical_consensus", "UNKNOWN"),
        "研究ステータス": snapshot.get("research_status", "UNKNOWN"),
        "Forward Evidence": snapshot.get("governing_study_status", "UNKNOWN"),
        "次回判断条件": snapshot.get("next_decision_trigger", ""),
        "自動配点変更": "DISABLED",
        "自動戦略変更": "DISABLED",
        "研究手動レビュー": "REQUIRED",
    }


def plain_section(snapshot: dict[str, Any]) -> list[str]:
    health = snapshot.get("catalog_health", "WARN")
    lines = [
        "【研究エビデンスの現在地】",
        (
            f"対象: {snapshot.get('subject_label', '出来高倍率スコア構成要素')} / "
            f"現行配点 {snapshot.get('production_weight_points', 15)}点（据え置き）"
        ),
        (
            f"判断: {snapshot.get('decision', evidence_catalog.HOLD_DECISION)} / "
            f"研究状態: {snapshot.get('research_status', 'UNKNOWN')}"
        ),
        (
            f"歴史検証: {snapshot.get('historical_consensus', 'UNKNOWN')} / "
            f"Forward Evidence: {snapshot.get('governing_study_status', 'UNKNOWN')}"
        ),
        "自動配点変更・自動戦略変更は無効です。次の判断はforward evidence完了後の手動レビューです。",
    ]
    if health != "PASS":
        lines.append(
            "注意: 研究台帳を完全検証できないため、安全側で現行配点を維持しています。"
        )
    lines.append("")
    return lines


def html_section(snapshot: dict[str, Any]) -> str:
    health = snapshot.get("catalog_health", "WARN")
    border = "#7c3aed" if health == "PASS" else "#b45309"
    background = "#faf5ff" if health == "PASS" else "#fffbeb"
    label = "UNRESOLVED / FORWARD ACCUMULATING"
    if health != "PASS":
        label = "SAFE HOLD / CATALOG WARN"
    esc = lambda value: html.escape(str(value or ""))
    warning = ""
    if health != "PASS":
        warning = (
            '<div style="font-size:11px;color:#b45309;margin-top:6px">'
            "研究台帳を完全検証できないため、安全側で現行配点を維持しています。</div>"
        )
    return f'''<div style="background:{background};border:2px solid {border};border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:12px;font-weight:900;color:{border}">RESEARCH EVIDENCE</div>
<div style="font-size:19px;font-weight:900;color:#3b0764;margin-top:2px">{esc(snapshot.get("subject_label", "出来高倍率スコア構成要素"))} <span style="float:right;font-size:12px;color:{border}">{esc(label)}</span></div>
<div style="clear:both;font-size:13px;color:#334155;margin-top:8px">現行配点 <b>{int(snapshot.get("production_weight_points", 15))}点のまま維持</b> ・ 自動配点変更なし ・ 自動戦略変更なし</div>
<div style="font-size:12px;line-height:1.8;color:#475569;margin-top:5px">歴史検証 <b>{esc(snapshot.get("historical_consensus", "UNKNOWN"))}</b> ・ Forward Evidence <b>{esc(snapshot.get("governing_study_status", "UNKNOWN"))}</b><br>次の判断はforward evidence完了後の手動レビューです。</div>{warning}</div>'''


def evidence_rows(snapshot: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = [
        {
            "section": "Current Decision",
            "study_id": snapshot.get("subject_id", ""),
            "label": snapshot.get("subject_label", ""),
            "evidence_class": "GOVERNED_DECISION",
            "status": snapshot.get("research_status", "UNKNOWN"),
            "decision": snapshot.get("decision", evidence_catalog.HOLD_DECISION),
            "weight_points": snapshot.get("production_weight_points", 15),
            "source_pr": None,
            "delta_excess_return": None,
            "two_sided_p_value": None,
            "interpretation": snapshot.get("decision_reason", ""),
        }
    ]
    for study in snapshot.get("studies", []):
        rows.append(
            {
                "section": "Study",
                "study_id": study.get("id", ""),
                "label": study.get("label", ""),
                "evidence_class": study.get("evidence_class", ""),
                "status": study.get("status", ""),
                "decision": "",
                "weight_points": None,
                "source_pr": study.get("source_pr"),
                "delta_excess_return": study.get("primary_delta_excess_return"),
                "two_sided_p_value": study.get("two_sided_p_value"),
                "interpretation": study.get("interpretation", ""),
            }
        )
    return pd.DataFrame(rows)


def patch_workbook(path: str | Path, snapshot: dict[str, Any]) -> None:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    workbook = load_workbook(target)
    if SHEET_NAME in workbook.sheetnames:
        del workbook[SHEET_NAME]
    sheet = workbook.create_sheet(SHEET_NAME, 1)
    columns = list(evidence_rows(snapshot).columns)
    for column_index, value in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=column_index, value=value)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="6D28D9")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    frame = evidence_rows(snapshot)
    for row_index, row in enumerate(frame.itertuples(index=False), start=2):
        for column_index, value in enumerate(row, start=1):
            sheet.cell(row=row_index, column=column_index, value=value)
    sheet.freeze_panes = "A2"
    widths = {
        "A": 18,
        "B": 38,
        "C": 34,
        "D": 34,
        "E": 28,
        "F": 44,
        "G": 14,
        "H": 12,
        "I": 20,
        "J": 20,
        "K": 80,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    workbook.save(target)
