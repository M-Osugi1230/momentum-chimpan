"""Decision-first daily email for the Momentum Chimpan research dashboard.

The full analytical detail lives in the web dashboard and workbook.  This module
keeps the mail intentionally small: freshness, market conclusion, the most
important caution, and three-to-five Daily Action List names.  It never changes
ranking, scores, priorities, paper execution, or production state.
"""
from __future__ import annotations

import html
import os
import re
from typing import Any
from urllib.parse import quote

import pandas as pd

DEFAULT_SITE_URL = "https://momentum-chimpan.osugimurata.chatgpt.site/"
DISCLAIMER = (
    "売買推奨ではなく、今日どの銘柄から詳しく調査するかを整理するための研究支援情報です。"
)
MAX_REASON_CHARS = 92
MAX_CHANGE_CHARS = 64
MAX_RISK_CHARS = 72


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


def compact_text(value: Any, limit: int) -> str:
    """Normalize generated prose and keep the mail scannable."""
    text = optional_text(value)
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = text.replace(" / ", "・").replace("｜", "・")
    text = re.sub(r"・+", "・", text).strip(" ・")
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rstrip(" ・、。")
    return f"{clipped}…"


def resolve_site_url(config: dict[str, Any] | None = None) -> str:
    configured = os.getenv("MOMENTUM_SITE_URL", "").strip()
    if not configured and isinstance(config, dict):
        site_config = config.get("site", {})
        if isinstance(site_config, dict):
            configured = optional_text(site_config.get("url"))
    url = configured or DEFAULT_SITE_URL
    return url if url.endswith("/") else f"{url}/"


def stock_url(site_url: str, code: Any) -> str:
    normalized = _normalize_code(code)
    return f"{site_url}?code={quote(normalized)}#ranking"


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
    bucket = work.get("research_bucket", work.get("action_priority", pd.Series(index=work.index, dtype=str)))
    if "daily_action_list" in work.columns:
        selected = work[work["daily_action_list"].fillna(False).astype(bool)].copy()
    else:
        selected = work[bucket.isin(["A", "B"])].copy()
    if selected.empty:
        selected = work[bucket.isin(["A", "B"])].copy()
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


def _regime_colors(regime: str) -> tuple[str, str, str]:
    if regime == "過熱警戒":
        return "#991b1b", "#fef2f2", "#fecaca"
    if regime == "強気":
        return "#166534", "#f0fdf4", "#bbf7d0"
    if regime == "やや強気":
        return "#1d4ed8", "#eff6ff", "#bfdbfe"
    if regime == "弱気":
        return "#7f1d1d", "#fff7ed", "#fed7aa"
    return "#854d0e", "#fefce8", "#fde68a"


def _transition_text(summary: dict[str, Any], regime: str) -> str:
    explicit = optional_text(summary.get("Market Regime転換"))
    transition_type = optional_text(summary.get("Market Regime転換種別"))
    score_delta = pd.to_numeric(pd.Series([summary.get("Market Regime Score前回比")]), errors="coerce").iloc[0]
    streak = integer(summary.get("Market Regime継続日数"), 1)
    if explicit and "履歴開始" not in explicit:
        suffix = f"・{transition_type}" if transition_type and transition_type != "維持" else ""
        delta = "" if pd.isna(score_delta) else f"・前回比 {int(score_delta):+d}点"
        return f"{explicit}{suffix}{delta}"
    delta = "" if pd.isna(score_delta) else f"・前回比 {int(score_delta):+d}点"
    return f"{regime}を{max(streak, 1)}営業日維持{delta}"


def _primary_caution(ctx: dict[str, Any]) -> str:
    candidates = ctx["candidates"]
    if ctx["data_stale"]:
        return "株価データが当日基準を満たしていません。銘柄評価より先に更新状況を確認してください。"
    if ctx["p0"] > 0:
        return f"P0アラートが{ctx['p0']}件あります。詳細確認までランキング利用を保留してください。"
    if ctx["p1"] > 0:
        return f"P1アラートが{ctx['p1']}件あります。運用詳細を確認してから利用してください。"
    if not candidates.empty:
        risks = [
            compact_text(row.get("risk_summary") or row.get("caution_reasons"), MAX_RISK_CHARS)
            for _, row in candidates.iterrows()
        ]
        risks = [risk for risk in risks if risk and risk not in {"特記事項なし", "過熱注意なし"}]
        if risks:
            return risks[0]
    if ctx["quality_c"] > 0 or ctx["quality_d"] > 0:
        return f"品質C/Dが{ctx['quality_c'] + ctx['quality_d']}件あります。候補ごとの品質表示を確認してください。"
    if ctx["overheat_count"] > 0:
        return f"Top100内に過熱判定が{ctx['overheat_count']}件あります。上昇率だけで追わないでください。"
    return "候補は調査順です。最新開示・チャート・流動性を確認してから判断してください。"


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
    regime = optional_text(summary.get("Market Regime")) or optional_text(temp.get("market_regime"))
    regime_score = integer(summary.get("Market Regime Score", temp.get("market_regime_score")), 0)
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

    price_date = optional_text(summary.get("株価データ日")) or optional_text(summary.get("最新株価日"))
    if not price_date and "price_date" in top100.columns and not top100.empty:
        values = pd.to_datetime(top100["price_date"], errors="coerce").dropna()
        if not values.empty:
            price_date = values.max().date().isoformat()
    report_date = optional_text(summary.get("実行日"))
    freshness = optional_text(summary.get("市場データ鮮度"))
    state_updated = optional_text(summary.get("状態更新実行"))
    data_stale = bool(
        (freshness and freshness.upper() not in {"FRESH", "PASS"})
        or (report_date and price_date and report_date != price_date)
        or state_updated.upper() == "NO"
    )

    evidence = snapshot or {}
    context = {
        "date": report_date,
        "price_date": price_date,
        "site_url": resolve_site_url(config),
        "regime": regime or "判定待ち",
        "regime_score": regime_score,
        "guidance": _market_guidance(regime),
        "transition": _transition_text(summary, regime or "判定待ち"),
        "candidates": candidates,
        "top100_count": len(top100),
        "new_count": len(new_entries) or integer(summary.get("新規ランクイン")),
        "rising_count": len(rising_fast) or integer(summary.get("急上昇")),
        "ytd_high_count": integer(summary.get("年初来高値更新"), integer(temp.get("ytd_high_count"))),
        "avg_score": number(temp.get("top100_avg_score")),
        "avg_return_20d": number(temp.get("top100_avg_return_20d")),
        "change_counts": change_counts,
        "quality_a": integer(summary.get("Data Quality A")),
        "quality_b": integer(summary.get("Data Quality B")),
        "quality_c": integer(summary.get("Data Quality C")),
        "quality_d": integer(summary.get("Data Quality D")),
        "fresh_ratio": number(summary.get("Data Quality現行日率", summary.get("当日株価比率"))),
        "freshness": freshness or ("STALE" if data_stale else "FRESH"),
        "data_stale": data_stale,
        "run_health": health or "UNKNOWN",
        "p0": p0,
        "p1": p1,
        "overheat_count": integer(summary.get("Top100 過熱銘柄数")),
        "forward_status": optional_text(summary.get("Forward Evidence", evidence.get("governing_study_status"))) or "ACCUMULATING",
        "production_weight": integer(summary.get("出来高倍率配点", evidence.get("production_weight_points", 15)), 15),
    }
    context["primary_caution"] = _primary_caution(context)
    context["attention_required"] = bool(data_stale or p0 > 0 or p1 > 0)
    return context


def _candidate_values(row: pd.Series) -> dict[str, Any]:
    bucket = optional_text(row.get("research_bucket")) or optional_text(row.get("action_priority"))
    return {
        "bucket": bucket,
        "code": _normalize_code(row.get("code")),
        "name": optional_text(row.get("name")),
        "action_score": number(row.get("action_score")),
        "reason": compact_text(row.get("why_today") or row.get("positive_reasons"), MAX_REASON_CHARS),
        "change": compact_text(row.get("what_changed"), MAX_CHANGE_CHARS),
        "risk": compact_text(row.get("risk_summary") or row.get("caution_reasons"), MAX_RISK_CHARS),
    }


def _candidate_plain(row: pd.Series, position: int, site_url: str) -> list[str]:
    value = _candidate_values(row)
    lines = [
        f"{position}. [{value['bucket']}] {value['code']} {value['name']}｜調査優先度 {value['action_score']:.1f}",
        f"   理由: {value['reason'] or '-'}",
    ]
    if value["change"]:
        lines.append(f"   変化: {value['change']}")
    lines.extend([
        f"   注意: {value['risk'] or '特記事項なし'}",
        f"   詳細: {stock_url(site_url, value['code'])}",
    ])
    return lines


def build_plain(
    *args: Any,
    daily_focus: pd.DataFrame | None = None,
    snapshot: dict[str, Any] | None = None,
    **kwargs: Any,
) -> str:
    ctx = _context(args, kwargs, daily_focus, snapshot)
    candidates = ctx["candidates"]
    alert = "【要確認】" if ctx["attention_required"] else "【本日の要点】"
    lines = [
        f"【モメンタムチンパン】{ctx['date']} 引け後ダイジェスト",
        f"株価データ日 {ctx['price_date']}｜鮮度 {ctx['freshness']}｜約90秒",
        "",
        alert,
        f"市場: {ctx['regime']} {ctx['regime_score']}点｜{ctx['transition']}",
        f"方針: {ctx['guidance']}",
        f"最大の注意: {ctx['primary_caution']}",
        "",
        f"【今日の調査候補】{len(candidates)}件",
    ]
    if candidates.empty:
        lines.append("A/B候補はありません。無理に候補を増やさず、Watchの改善を待ちます。")
    else:
        for position, (_, row) in enumerate(candidates.iterrows(), 1):
            lines.extend(_candidate_plain(row, position, ctx["site_url"]))
            lines.append("")
    change = ctx["change_counts"]
    lines += [
        "【市場と品質】",
        f"重点候補 新規 {change['新規']} / 継続 {change['継続']} / 脱落 {change['脱落']}｜Top100新規 {ctx['new_count']}｜急上昇 {ctx['rising_count']}",
        f"Top100平均 {ctx['avg_score']:.2f}点｜平均20日 {percent(ctx['avg_return_20d'])}｜年初来高値 {ctx['ytd_high_count']}件",
        f"品質 A {ctx['quality_a']} / B {ctx['quality_b']} / C {ctx['quality_c']} / D {ctx['quality_d']}｜当日データ率 {percent(ctx['fresh_ratio'])}",
        f"運用 {ctx['run_health']}｜P0 {ctx['p0']} / P1 {ctx['p1']}｜Forward Evidence {ctx['forward_status']}",
        "",
        f"全ランキング・業種・相対強度・検証: {ctx['site_url']}",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def _candidate_card(row: pd.Series, position: int, site_url: str) -> str:
    value = _candidate_values(row)
    accent = "#15803d" if value["bucket"] == "A" else "#2563eb"
    background = "#f0fdf4" if value["bucket"] == "A" else "#eff6ff"
    change_html = (
        f'<div style="font-size:12px;color:#475569;margin-top:5px"><b>変化:</b> {escape(value["change"])}</div>'
        if value["change"]
        else ""
    )
    return f'''<div style="background:{background};border:1px solid {accent};border-radius:14px;padding:13px;margin-top:10px">
<div style="font-size:15px;font-weight:900;color:#0f172a">{position}. [{escape(value['bucket'])}] {escape(value['code'])} {escape(value['name'])} <span style="float:right;color:{accent}">{value['action_score']:.1f}</span></div>
<div style="clear:both;font-size:12px;line-height:1.65;color:#334155;margin-top:6px"><b>理由:</b> {escape(value['reason'] or '-')}</div>
{change_html}
<div style="font-size:12px;line-height:1.65;color:#9a3412;margin-top:5px"><b>注意:</b> {escape(value['risk'] or '特記事項なし')}</div>
<a href="{escape(stock_url(site_url, value['code']))}" style="display:inline-block;margin-top:9px;color:{accent};font-size:12px;font-weight:900;text-decoration:none">銘柄詳細を見る →</a>
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
        _candidate_card(row, position, ctx["site_url"])
        for position, (_, row) in enumerate(candidates.iterrows(), 1)
    )
    if not candidate_html:
        candidate_html = '<div style="font-size:13px;color:#64748b;margin-top:10px">A/B候補はありません。Watchの改善を待ちます。</div>'
    change = ctx["change_counts"]
    health_color = "#15803d" if ctx["run_health"] == "PASS" and ctx["p0"] == 0 and ctx["p1"] == 0 else "#b45309"
    regime_color, regime_background, regime_border = _regime_colors(ctx["regime"])
    alert_color = "#991b1b" if ctx["attention_required"] else "#9a3412"
    alert_background = "#fef2f2" if ctx["attention_required"] else "#fff7ed"
    alert_border = "#fecaca" if ctx["attention_required"] else "#fed7aa"
    return f'''<!doctype html><html lang="ja"><body style="margin:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans','Yu Gothic',Meiryo,Arial,sans-serif;color:#0f172a">
<div style="display:none;max-height:0;overflow:hidden">{escape(ctx['regime'])} {ctx['regime_score']}点。調査候補{len(candidates)}件。最大の注意: {escape(ctx['primary_caution'])}</div>
<div style="max-width:680px;margin:0 auto;padding:16px">
<div style="background:linear-gradient(135deg,#0f172a,#1e3a8a);color:#fff;border-radius:20px;padding:20px">
<div style="font-size:12px;color:#bfdbfe;font-weight:800;letter-spacing:.08em">MOMENTUM CHIMPAN ・ 90 SEC</div>
<div style="font-size:24px;font-weight:900;margin-top:3px">{escape(ctx['date'])} 引け後ダイジェスト</div>
<div style="font-size:12px;color:#dbeafe;margin-top:7px">株価データ日 {escape(ctx['price_date'])} ・ 鮮度 {escape(ctx['freshness'])}</div>
</div>
<div style="background:{regime_background};border-radius:18px;padding:16px;margin-top:12px;border:1px solid {regime_border}">
<div style="font-size:12px;font-weight:900;color:{regime_color}">今日の結論</div>
<div style="font-size:23px;font-weight:900;color:{regime_color};margin-top:3px">{escape(ctx['regime'])} <span style="font-size:15px">{ctx['regime_score']}点</span></div>
<div style="font-size:12px;font-weight:800;color:#475569;margin-top:5px">{escape(ctx['transition'])}</div>
<div style="font-size:13px;color:#334155;line-height:1.7;margin-top:5px">{escape(ctx['guidance'])}</div>
<div style="font-size:12px;color:#475569;line-height:1.8;margin-top:8px">平均スコア <b>{ctx['avg_score']:.2f}</b> ・ 平均20日 <b>{percent(ctx['avg_return_20d'])}</b> ・ 年初来高値 <b>{ctx['ytd_high_count']}件</b></div>
</div>
<div style="background:{alert_background};border:1px solid {alert_border};border-radius:14px;padding:13px;margin-top:10px;color:{alert_color};font-size:13px;line-height:1.65"><b>最大の注意</b><br>{escape(ctx['primary_caution'])}</div>
<div style="background:#fff;border-radius:18px;padding:16px;margin-top:12px;border:1px solid #e2e8f0">
<div style="font-size:18px;font-weight:900">今日の調査候補 <span style="font-size:13px;color:#64748b">{len(candidates)}件</span></div>
{candidate_html}
</div>
<div style="background:#fff;border-radius:18px;padding:16px;margin-top:12px;border:1px solid #e2e8f0">
<div style="font-size:16px;font-weight:900">市場と品質</div>
<div style="font-size:12px;line-height:1.9;color:#334155;margin-top:7px">重点候補 新規 <b>{change['新規']}</b> / 継続 <b>{change['継続']}</b> / 脱落 <b>{change['脱落']}</b><br>Top100新規 <b>{ctx['new_count']}</b> ・ 急上昇 <b>{ctx['rising_count']}</b><br>品質 A <b>{ctx['quality_a']}</b> / B <b>{ctx['quality_b']}</b> / C <b>{ctx['quality_c']}</b> / D <b>{ctx['quality_d']}</b> ・ 当日データ率 <b>{percent(ctx['fresh_ratio'])}</b><br><span style="color:{health_color}">運用 <b>{escape(ctx['run_health'])}</b> ・ P0 {ctx['p0']} / P1 {ctx['p1']}</span></div>
</div>
<a href="{escape(ctx['site_url'])}" style="display:block;text-align:center;background:#2563eb;color:#fff;text-decoration:none;border-radius:14px;padding:14px 18px;font-weight:900;margin-top:14px">全情報をWebダッシュボードで見る</a>
<div style="font-size:11px;color:#64748b;line-height:1.7;margin:14px 4px 0">Forward Evidence {escape(ctx['forward_status'])}。出来高倍率配点は{ctx['production_weight']}点のままです。<br>{escape(DISCLAIMER)}</div>
</div></body></html>'''
