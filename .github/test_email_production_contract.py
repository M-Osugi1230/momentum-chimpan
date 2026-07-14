from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import daily_runner
import daily_research_focus
import data_quality
import email_delivery
import email_digest
import research_transparency


class Logger:
    def info(self, *args, **kwargs):
        return None


captured: dict[str, object] = {}


def original_excel(path, summary, *args, **kwargs):
    captured["excel_summary"] = dict(summary)
    return "ok"


def identity(frame):
    return frame.copy()


fake_main = SimpleNamespace(
    excel_report=original_excel,
    build_plain_email=lambda *args, **kwargs: "legacy plain",
    build_html_email=lambda *args, **kwargs: "legacy html",
    send_email=lambda *args, **kwargs: None,
    attach_strategy_provenance=identity,
    load_config=lambda: {"market": {"min_trading_value": 100_000_000}},
    load_dotenv=lambda: None,
    logger=Logger(),
    os=os,
    smtplib=SimpleNamespace(),
    MIMEMultipart=object,
    MIMEText=object,
)

originals = {
    "dq_apply": data_quality.apply_priority_gate,
    "dq_replace": data_quality.replace_frame_in_place,
    "dq_summary": data_quality.summary_fields,
    "dq_patch": data_quality.patch_workbook,
    "focus_attach": daily_research_focus.attach_daily_focus,
    "focus_summary": daily_research_focus.summary_fields,
    "focus_patch": daily_research_focus.patch_workbook,
    "transparency_summary": research_transparency.summary_fields,
    "transparency_patch": research_transparency.patch_workbook,
}

focused = pd.DataFrame([{
    "code": "7453",
    "name": "良品計画",
    "research_bucket": "B",
    "daily_action_list": True,
    "daily_action_rank": 1,
    "action_score": 72,
    "why_today": "相対強度S / 出来高4.1倍 / 売買代金50億円以上 / 初動",
    "what_changed": "Top100新規ランクイン / 前回比+2409位",
    "risk_summary": "20日線乖離17.1%",
    "lifecycle_status": "初登場",
    "is_new_entry": True,
}])

data_quality.apply_priority_gate = lambda action, top100: action.copy()
data_quality.replace_frame_in_place = lambda target, source: None
data_quality.summary_fields = lambda top100, priority: {
    "Data Quality A": 68,
    "Data Quality B": 0,
    "Data Quality C": 32,
    "Data Quality D": 0,
    "Data Quality現行日率": 0.99,
}
data_quality.patch_workbook = lambda *args, **kwargs: None
daily_research_focus.attach_daily_focus = lambda priority, top100: focused.copy()
daily_research_focus.summary_fields = lambda priority: {"Daily Action List": 1}
daily_research_focus.patch_workbook = lambda *args, **kwargs: None
research_transparency.summary_fields = lambda snapshot: {
    "Forward Evidence": "ACCUMULATING",
    "出来高倍率配点": 15,
}
research_transparency.patch_workbook = lambda *args, **kwargs: None

try:
    daily_runner._PATCHED = False
    daily_runner.install_patches(
        snapshot={"governing_study_status": "ACCUMULATING", "production_weight_points": 15},
        main_module=fake_main,
    )

    summary = {
        "実行日": "2026-07-13",
        "株価データ日": "2026-07-13",
        "市場データ鮮度": "FRESH",
        "状態更新実行": "YES",
        "Market Regime": "強気",
        "Market Regime Score": 93,
        "Run Health": "PASS",
        "運用P0アラート": 0,
        "運用P1アラート": 0,
        "当日株価比率": 0.99,
    }
    top100 = pd.DataFrame([{"code": "7453", "price_date": "2026-07-13"}])
    excel_args = [top100] + [pd.DataFrame() for _ in range(28)] + [focused.copy()]
    result = fake_main.excel_report("unused.xlsx", summary, *excel_args)
    assert result == "ok"
    assert summary["Data Quality A"] == 68
    assert summary["Data Quality C"] == 32
    assert summary["Daily Action List"] == 1
    assert summary["Forward Evidence"] == "ACCUMULATING"
    assert summary["出来高倍率配点"] == 15
    assert captured["excel_summary"] == summary

    mail_args = [summary, top100, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()]
    while len(mail_args) <= 24:
        mail_args.append(pd.DataFrame())
    mail_args[8] = pd.DataFrame([{
        "top100_avg_score": 63.14,
        "top100_avg_return_20d": 0.1556,
    }])
    mail_args[23] = {"table": pd.DataFrame(), "action_priority": focused}
    mail_args[24] = {"site": {"url": "https://example.test/"}}
    plain = fake_main.build_plain_email(*mail_args)
    assert "品質 A 68 / B 0 / C 32 / D 0" in plain
    assert "【今日の調査候補】1件" in plain
    assert "出来高4.1倍" in plain

    subject_text = email_digest.subject(summary, focused)
    receipt = email_delivery.build_receipt(
        status="SKIPPED_SECRETS_MISSING",
        summary=summary,
        sender="",
        recipient_text="",
        started_at_utc="2026-07-13T08:00:00+00:00",
        completed_at_utc="2026-07-13T08:00:01+00:00",
        subject_text=subject_text,
    )
    assert email_delivery.validate_receipt(receipt) == []
    assert receipt["subject_source"] == "explicit"
    assert receipt["subject_sha256"] == hashlib.sha256(subject_text.lower().encode("utf-8")).hexdigest()
finally:
    data_quality.apply_priority_gate = originals["dq_apply"]
    data_quality.replace_frame_in_place = originals["dq_replace"]
    data_quality.summary_fields = originals["dq_summary"]
    data_quality.patch_workbook = originals["dq_patch"]
    daily_research_focus.attach_daily_focus = originals["focus_attach"]
    daily_research_focus.summary_fields = originals["focus_summary"]
    daily_research_focus.patch_workbook = originals["focus_patch"]
    research_transparency.summary_fields = originals["transparency_summary"]
    research_transparency.patch_workbook = originals["transparency_patch"]
    daily_runner._PATCHED = False

print("production email summary handoff validation passed")
