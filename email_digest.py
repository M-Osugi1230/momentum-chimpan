"""Concise daily email for the Momentum Chimpan research dashboard.

The full analytical detail lives in the generated web dashboard and workbook.
This module intentionally limits email content to the market conclusion, the
small Daily Action List, important cautions, and a stable dashboard link.
It never changes ranking, scores, priorities, paper execution, or state.
"""
from __future__ import annotations

import html
import os
from typing import Any

import pandas as pd

DEFAULT_SITE_URL = "https://m-osugi1230.github.io/momentum-chimpan/"
DISCLAIMER = (
    "売買推奨ではなく、今日どの銘柄から詳しく調査するかを整理するための研究支援情報です。"
)


def optional_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "nat"} else text


def number(value: Any, default: float = 0.0) -> float:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return default if pd.isna(converted) else float(converted)


def integer(value: Any, default: int = 0) -> int:
    return int(round(number(value, float(default))))


def percent(value: Any, digits: int = 1) -> str:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(converted):
        return "-"
    return f"{float(converted) * 100:.{digits}f}%"


def signed(value: Any, digits: int = 1, suffix: str = "") -> str:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(converted):
        return "-"
    value_float = float(converted)
    prefix = "+" if value_float > 0 else ""
    return f"{prefix}{value_float:.{digits}f}{suffix}"


def escape(value: Any) -> str:
    return html.escape(optional_text(value))


def resolve_site_url(config: dict[str, Any] | None = None) -> str:
    configured = os.getenv("MOMENTUM_SITE_URL", "").strip()
    if not configured and isinstance(config, dict):
        site_config = config.get("site", {})
        if isinstance(site_config, dict):
            configured = optional_text(site_config.get("url"))
    url = configured or DEFAULT_SITE_URL
    return url if url.endswith("/") else f"{url}/"


def _argument(
    args: tuple[Any, ...], kwargs: dict[str, Any], index: int, name: str, default: Any
) -> Any:
    if len(args) > index:
        return args[index]
    return kwargs.get(name, default)


def _frame(value: Any) -> pd.DataFrame:
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _priority_changes(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_code(value: Any) -> str:
    text = optional_text(value).split(".")[0]
    return text.zfill(4) if text.isdigit() else text


def _daily_candidates(focus: pd.DataFrame, limit: int = 5) -> pd.DataFrame:
    if focus is None or focus.empty:
        return pd.DataFrame()
    work = focus.copy()
    if "daily_action_list" in work.columns:
        selected = work[work["daily_action_list"].fillna(False).astype(bool)].copy()
    else:
        selected = work[
            work.get("research_bucket", work.get("action_priority", "")).isin(["A", "B"])
        ].copy()
    if selected.empty:
        selected = work[
            work.get("research_bucket", work.get("action_priority", "")).isin(["A", "B"])
        ].copy()
    if selected.empty:
        return selected
    selected["_priority"] = selected.get(
        "research_bucket", selected.get("action_priority", "")
    ).map({"A": 0, "B": 1}).fillna(9)
    selected["_rank"] = pd.to_numeric(
        selected.get("daily_action_rank", selected.get("momentum_rank")), errors="coerce"
    ).fillna(9999)
    selected["_score"] = pd.to_numeric(
        selected.get("action_score", pd.Series(index=selected.index, dtype=float)),
        errors="coerce",
    ).fillna(0)
    return selected.sort_values(
        ["_priority", "_rank", "_score"], ascending=[True, True, False]
    ).head(limit)


def _market_guidance(regime: str) -> str:
    if regime == "過熱警戒":
        return "上昇は強い一方、飛びつかず、乖離と出来高減速を優先確認。"
    if regime == "強気":
        return "初動・再浮上と流動性の高い銘柄を優先して深掘り。"
    if regime == "やや強気":
        return "複数の強さが重なる候補に絞り、継続性を確認。"
    if regime == "弱気":
        return "新規候補を厳選し、流動性と下振れリスクを優先確認。"
    return "単独シグナルではなく、複数条件が重なる候補を優先。"


def _context(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    daily_focus: pd.DataFrame | None,
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _argument(args, kwargs, 0, "summary", {})
    summary = summary if isinstance(summary, dict) else {}
    top100 = _frame(_argument(args, kwargs, 1, "top100", pd.DataFrame()))
    new_entries = _frame(_argument(args, kwargs, 4, "new_entries", pd.DataFrame()))
    rising_fast = _frame(_argument(args, kwargs, 5, "rising_fast", pd.DataFrame()))
    temperature = _frame(_argument(args, kwargs, 8, "temperature", pd.DataFrame()))
    run_health = _frame(_argument(args, kwargs, 15, "run_health", pd.DataFrame()))
    operational_alerts = _frame(
        _argument(args, kwargs, 21, "operational_alerts", pd.DataFrame())
    )
    priority_changes = _priority_changes(
        _argument(args, kwargs, 23, "priority_changes", {})
    )
    config = _argument(args, kwargs, 24, "cfg", {})
    config = config if isinstance(config, dict) else {}

    focus = _frame(daily_focus)
    if focus.empty:
        focus = _frame(priority_changes.get("action_priority"))
    candidates = _daily_candidates(focus)

    temp = {} if temperature.empty else temperature.iloc[0].to_dict()
    regime = optional_text(summary.get("Market Regime")) or optional_text(
        temp.get("market_regime")
    )
    regime_score = integer(
        summary.get("Market Regime Score", temp.get("market_regime_score")), 0
    )
    change_counts = {
        label: int((priority_changes.get("table", pd.DataFrame()).get("status", pd.Series(dtype=str)) == label).sum())
        if isinstance(priority_changes.get("table"), pd.DataFrame)
        else 0
        for label in ("新規", "継続", "脱落")
    }
    if not any(change_counts.values()):
        change_counts = {
            "新規": integer(summary.get("重点候補新規")),
            "継続": integer(summary.get("重点候補継続")),
            "脱落": integer(summary.get("重点候補脱落")),
        }

    p0 = integer(summary.get("運用P0アラート"))
    p1 = integer(summary.get("運用P1アラート"))
    if not operational_alerts.empty and "severity" in operational_alerts.columns:
        p0 = int(operational_alerts["severity"].eq("P0").sum())
        p1 = int(operational_alerts["severity"].eq("P1").sum())
    health = optional_text(summary.get("Run Health"))
    if not health and not run_health.empty:
        overall = run_health[run_health.get("check_name", "").eq("overall")]
        health = optional_text(overall.iloc[0].get("status")) if not overall.empty else ""

    price_date = optional_text(summary.get("株価データ日"))
    if not price_date and "price_date" in top100.columns and not top100.empty:
        values = pd.to_datetime(top100["price_date"], errors="coerce").dropna()
        if not values.empty:
            price_date = values.max().date().isoformat()

    evidence = snapshot or {}
    return {
        "date": optional_text(summary.get("実行日")),
        "price_date": price_date,
        "site_url": resolve_site_url(config),
        "regime": regime or "判定待ち",
        "regime_score": regime_score,
        "guidance": _market_guidance(regime),
        "candidates": candidates,
        "top100_count": len(top100),
        "new_count": len(new_entries) or integer(summary.get("新規ランクイン")),
        "rising_count": len(rising_fast) or integer(summary.get("急上昇")),
        "ytd_high_count": integer(summary.get("年初来高値更新"), integer(temp.get("ytd_high_count"))),
        "avg_score": number(temp.get("top100_avg_score")),
        "avg_return_20d": number(temp.get("top100_avg_return_20d")),
        "change_counts": change_counts,
        "quality_a": integer(summary.get("Data Quality A")),
        "quality_c": integer(summary.get("Data Quality C")),
        "fresh_ratio": number(summary.get("Data Quality現行日率", summary.get("当日株価比率"))),
        "run_health": health or "UNKNOWN",
        "p0": p0,
        "p1": p1,
        "forward_status": optional_text(
            summary.get("Forward Evidence", evidence.get("governing_study_status"))
        ) or "ACCUMULATING",
        "production_weight": integer(
            summary.get("出来高倍率配点", evidence.get("production_weight_points", 15)), 15
        ),
    }


def _candidate_plain(row: pd.Series, position: int) -> list[str]:
    bucket = optional_text(row.get("research_bucket")) or optional_text(
        row.get("action_priority")
    )
    code = _normalize_code(row.get("code"))
    name = optional_text(row.get("name"))
    action_score = number(row.get("action_score"))
    reason = optional_text(row.get("why_today")) or optional_text(
        row.get("positive_reasons")
    )
    change = optional_text(row.get("what_changed"))
    risk = optional_text(row.get("risk_summary")) or optional_text(
        row.get("caution_reasons")
    )
    return [
        f"{position}. [{bucket}] {code} {name}｜{action_score:.1f}点",
        f"   理由: {reason or '-'}",
        *([f"   変化: {change}"] if change else []),
        f"   注意: {risk or '特記事項なし'}",
    ]


def build_plain(
    *args: Any,
    daily_focus: pd.DataFrame | None = None,
    snapshot: dict[str, Any] | None = None,
    **kwargs: Any,
) -> str:
    ctx = _context(args, kwargs, daily_focus, snapshot)
    candidates = ctx["candidates"]
    lines = [
        f"【モメンタムチンパン】{ctx['date']} 引け後ダイジェスト",
        f"株価データ日: {ctx['price_date']}",
        "",
        "【今日の結論】",
        f"市場: {ctx['regime']} {ctx['regime_score']}点｜{ctx['guidance']}",
        f"Top100平均スコア {ctx['avg_score']:.2f}｜平均20日騰落率 {percent(ctx['avg_return_20d'])}｜年初来高値 {ctx['ytd_high_count']}件",
        "",
        f"【今日の調査候補】{len(candidates)}件",
    ]
    if candidates.empty:
        lines.append("A/B候補はありません。無理に候補を増やさず、Watchの改善を待ちます。")
    else:
        for position, (_, row) in enumerate(candidates.iterrows(), 1):
            lines.extend(_candidate_plain(row, position))
            lines.append("")
    change = ctx["change_counts"]
    lines += [
        "【重要な変化と品質】",
        f"重点候補: 新規 {change['新規']} / 継続 {change['継続']} / 脱落 {change['脱落']}｜Top100新規 {ctx['new_count']}｜急上昇 {ctx['rising_count']}",
        f"Data Quality: A {ctx['quality_a']} / C {ctx['quality_c']}｜当日データ率 {percent(ctx['fresh_ratio'])}",
        f"運用: {ctx['run_health']}｜P0 {ctx['p0']} / P1 {ctx['p1']}｜Forward Evidence {ctx['forward_status']}｜出来高倍率配点 {ctx['production_weight']}点据え置き",
        "",
        f"全ランキング・業種・相対強度・品質・ペーパー検証: {ctx['site_url']}",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def _candidate_card(row: pd.Series, position: int) -> str:
    bucket = optional_text(row.get("research_bucket")) or optional_text(
        row.get("action_priority")
    )
    code = _normalize_code(row.get("code"))
    name = optional_text(row.get("name"))
    action_score = number(row.get("action_score"))
    reason = optional_text(row.get("why_today")) or optional_text(
        row.get("positive_reasons")
    )
    change = optional_text(row.get("what_changed"))
    risk = optional_text(row.get("risk_summary")) or optional_text(
        row.get("caution_reasons")
    )
    accent = "#15803d" if bucket == "A" else "#2563eb"
    background = "#f0fdf4" if bucket == "A" else "#eff6ff"
    change_html = (
        f'<div style="font-size:12px;color:#475569;margin-top:5px"><b>変化:</b> {escape(change)}</div>'
        if change
        else ""
    )
    return f'''<div style="background:{background};border:1px solid {accent};border-radius:14px;padding:13px;margin-top:10px">
<div style="font-size:15px;font-weight:900;color:#0f172a">{position}. [{escape(bucket)}] {escape(code)} {escape(name)} <span style="float:right;color:{accent}">{action_score:.1f}点</span></div>
<div style="clear:both;font-size:12px;line-height:1.65;color:#334155;margin-top:6px"><b>理由:</b> {escape(reason or '-')}</div>
{change_html}
<div style="font-size:12px;line-height:1.65;color:#9a3412;margin-top:5px"><b>注意:</b> {escape(risk or '特記事項なし')}</div>
</div>'''


def build_html(
    *args: Any,
    daily_focus: pd.DataFrame | None = None,
    snapshot: dict[str, Any] | None = None,
    **kwargs: Any,
) -> str:
    ctx = _context(args, kwargs, daily_focus, snapshot)
    candidates = ctx["candidates"]
    candidate_html = "".join(
        _candidate_card(row, position)
        for position, (_, row) in enumerate(candidates.iterrows(), 1)
    )
    if not candidate_html:
        candidate_html = '<div style="font-size:13px;color:#64748b;margin-top:10px">A/B候補はありません。Watchの改善を待ちます。</div>'
    change = ctx["change_counts"]
    health_color = "#15803d" if ctx["run_health"] == "PASS" and ctx["p0"] == 0 else "#b45309"
    return f'''<!doctype html><html lang="ja"><body style="margin:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans','Yu Gothic',Meiryo,Arial,sans-serif;color:#0f172a">
<div style="max-width:680px;margin:0 auto;padding:16px">
<div style="background:linear-gradient(135deg,#0f172a,#1e3a8a);color:#fff;border-radius:20px;padding:20px">
<div style="font-size:12px;color:#bfdbfe;font-weight:800;letter-spacing:.08em">MOMENTUM CHIMPAN</div>
<div style="font-size:24px;font-weight:900;margin-top:3px">{escape(ctx['date'])} 引け後ダイジェスト</div>
<div style="font-size:12px;color:#dbeafe;margin-top:7px">株価データ日 {escape(ctx['price_date'])}｜詳しい情報はWebダッシュボードに集約</div>
</div>
<div style="background:#fff;border-radius:18px;padding:16px;margin-top:12px;border:1px solid #e2e8f0">
<div style="font-size:12px;font-weight:900;color:#64748b">今日の結論</div>
<div style="font-size:23px;font-weight:900;color:#15803d;margin-top:3px">{escape(ctx['regime'])} <span style="font-size:15px">{ctx['regime_score']}点</span></div>
<div style="font-size:13px;color:#334155;line-height:1.7;margin-top:5px">{escape(ctx['guidance'])}</div>
<div style="font-size:12px;color:#475569;line-height:1.8;margin-top:8px">平均スコア <b>{ctx['avg_score']:.2f}</b> ・ 平均20日 <b>{percent(ctx['avg_return_20d'])}</b> ・ 年初来高値 <b>{ctx['ytd_high_count']}件</b></div>
</div>
<div style="background:#fff;border-radius:18px;padding:16px;margin-top:12px;border:1px solid #e2e8f0">
<div style="font-size:18px;font-weight:900">今日の調査候補 <span style="font-size:13px;color:#64748b">{len(candidates)}件</span></div>
{candidate_html}
</div>
<div style="background:#fff;border-radius:18px;padding:16px;margin-top:12px;border:1px solid #e2e8f0">
<div style="font-size:16px;font-weight:900">重要な変化と品質</div>
<div style="font-size:12px;line-height:1.9;color:#334155;margin-top:7px">重点候補 新規 <b>{change['新規']}</b> / 継続 <b>{change['継続']}</b> / 脱落 <b>{change['脱落']}</b><br>Top100新規 <b>{ctx['new_count']}</b> ・ 急上昇 <b>{ctx['rising_count']}</b><br>Data Quality A <b>{ctx['quality_a']}</b> / C <b>{ctx['quality_c']}</b> ・ 当日データ率 <b>{percent(ctx['fresh_ratio'])}</b><br><span style="color:{health_color}">運用 <b>{escape(ctx['run_health'])}</b> ・ P0 {ctx['p0']} / P1 {ctx['p1']}</span></div>
</div>
<a href="{escape(ctx['site_url'])}" style="display:block;text-align:center;background:#2563eb;color:#fff;text-decoration:none;border-radius:14px;padding:14px 18px;font-weight:900;margin-top:14px">全情報をWebダッシュボードで見る</a>
<div style="font-size:11px;color:#64748b;line-height:1.7;margin:14px 4px 0">Forward Evidence {escape(ctx['forward_status'])}。出来高倍率配点は{ctx['production_weight']}点のままです。<br>{escape(DISCLAIMER)}</div>
</div></body></html>'''
