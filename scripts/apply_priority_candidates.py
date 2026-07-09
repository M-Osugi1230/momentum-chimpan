from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match, found {count}: {old[:100]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-09-dashboard-signal-quality-v2"',
    'APP_VERSION = "2026-07-09-dashboard-priority-candidates-v3"',
)

helper_code = r'''

def row_number(r: pd.Series, key: str, default: float = 0.0) -> float:
    value = r.get(key)
    if value is None or pd.isna(value):
        return default
    return float(value)


def row_flag(r: pd.Series, key: str) -> bool:
    value = r.get(key)
    if value is None or pd.isna(value):
        return False
    return bool(value)


def priority_candidate_labels(r: pd.Series) -> list[str]:
    """Classify a Top100 stock using transparent, existing momentum signals."""
    labels: list[str] = []
    score_value = row_number(r, "score")
    volume_ratio = row_number(r, "volume_ratio")
    trading_value = row_number(r, "trading_value")
    top30_days = row_number(r, "top30_streak", row_number(r, "top30_streak_days"))

    if (
        row_flag(r, "is_new_entry")
        and row_flag(r, "ytd_high_flag")
        and volume_ratio >= 1.5
        and trading_value >= 100_000_000
        and row_flag(r, "above_ma20")
        and row_flag(r, "above_ma60")
        and score_value >= 60
    ):
        labels.append("初動")
    if (
        row_flag(r, "is_rising_fast")
        and volume_ratio >= 1.5
        and trading_value >= 100_000_000
        and score_value >= 60
    ):
        labels.append("加速")
    if top30_days >= 3 and row_flag(r, "ytd_high_flag") and score_value >= 60:
        labels.append("継続")
    if trading_value >= 5_000_000_000 and volume_ratio >= 1.5 and score_value >= 60:
        labels.append("大型資金")

    if (
        row_number(r, "return_20d") >= 0.50
        or row_number(r, "ma20_deviation") >= 0.25
        or volume_ratio >= 8.0
    ):
        labels.append("過熱注意")
    return labels


def select_priority_candidates(top100: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    """Select multi-signal candidates without changing the underlying ranking score."""
    if top100.empty:
        return top100.copy()
    work = top100.copy()
    work["priority_labels"] = work.apply(priority_candidate_labels, axis=1)
    work["priority_signal_count"] = work["priority_labels"].map(
        lambda labels: len([label for label in labels if label != "過熱注意"])
    )
    work = work[work["priority_signal_count"] > 0].copy()
    if work.empty:
        return work
    return work.sort_values(
        ["priority_signal_count", "score", "trading_value", "rank"],
        ascending=[False, False, False, True],
    ).head(limit)


def plain_priority_section(priority: pd.DataFrame) -> list[str]:
    if priority.empty:
        return []
    lines = [
        "【今日の重点候補】",
        "複数のモメンタム条件が重なった銘柄です。過熱注意は買い推奨ではなく、値動き確認の注意タグです。",
    ]
    for _, r in priority.iterrows():
        tags = " / ".join(r.get("priority_labels", []))
        rank_change = fmt_rank_change(r.get("rank_change"))
        movement = f" / {rank_change}" if rank_change else ""
        lines += [
            f"#{int(r['rank'])} {r['code']} {r['name']}｜{int(r['score'])}点｜{tags}",
            f"   20日 {fmt_pct(r.get('return_20d'))} / 出来高 {fmt_num(r.get('volume_ratio'))}倍 / 売買代金 {fmt_trading_value(r.get('trading_value'))}{movement}",
            "",
        ]
    return lines


def html_priority_section(priority: pd.DataFrame) -> str:
    if priority.empty:
        return ""
    items = []
    for _, r in priority.iterrows():
        tag_html = []
        for label in r.get("priority_labels", []):
            if label == "過熱注意":
                background, color = "#fee2e2", "#991b1b"
            elif label == "大型資金":
                background, color = "#ede9fe", "#5b21b6"
            elif label == "継続":
                background, color = "#dcfce7", "#166534"
            elif label == "加速":
                background, color = "#ffedd5", "#9a3412"
            else:
                background, color = "#dbeafe", "#1d4ed8"
            tag_html.append(
                f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:3px 8px;border-radius:999px;background:{background};color:{color};font-size:12px;font-weight:800">{html_text(label)}</span>'
            )
        rank_change = fmt_rank_change(r.get("rank_change"))
        movement = f" ・ {html_text(rank_change)}" if rank_change else ""
        items.append(
            f"""<div style="border-top:1px solid #e5e7eb;padding:11px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(r["rank"])} {html_text(r["code"])} {html_text(r["name"])} <span style="color:{score_color(r["score"])}">{int(r["score"])}点</span></div>
<div style="margin:5px 0">{"".join(tag_html)}</div>
<div style="font-size:12px;line-height:1.7;color:#475569">20日 {fmt_pct(r.get("return_20d"))} ・ 出来高 {fmt_num(r.get("volume_ratio"))}倍 ・ 売買代金 {fmt_trading_value(r.get("trading_value"))}{movement}</div>
</div>"""
        )
    return f"""<div style="background:#fff;border:2px solid #0f172a;border-radius:18px;padding:16px;margin-top:18px">
<div style="font-size:18px;font-weight:900;color:#0f172a">今日の重点候補</div>
<div style="font-size:12px;line-height:1.7;color:#64748b;margin-top:4px">複数のモメンタム条件が重なった銘柄です。過熱注意は売買指示ではなく、値動き確認の注意タグです。</div>
{"".join(items)}
</div>"""
'''

replace_once(
    "\n\ndef plain_ranking_section(title: str, df: pd.DataFrame, limit: int = 5, show_empty: bool = False) -> list[str]:",
    helper_code + "\n\ndef plain_ranking_section(title: str, df: pd.DataFrame, limit: int = 5, show_empty: bool = False) -> list[str]:",
)

replace_once(
    '    price_date = latest_price_date(top100)\n    lines = [',
    '    price_date = latest_price_date(top100)\n    priority = select_priority_candidates(top100, 10)\n    lines = [',
)
replace_once(
    '        f"買い候補TOP100: {len(top100)}件",\n',
    '        f"買い候補TOP100: {len(top100)}件",\n        f"今日の重点候補: {len(priority)}件",\n',
)
replace_once(
    '    lines += plain_metric_highlights(top100)\n',
    '    lines += plain_priority_section(priority)\n    lines += plain_metric_highlights(top100)\n',
)

old_html_start = '    price_date = latest_price_date(top100)\n    cards = ['
if text.count(old_html_start) != 1:
    raise RuntimeError(f"Expected one HTML price-date marker, found {text.count(old_html_start)}")
text = text.replace(
    old_html_start,
    '    price_date = latest_price_date(top100)\n    priority = select_priority_candidates(top100, 10)\n    cards = [',
    1,
)
replace_once(
    '    sections = "".join([\n        html_metric_highlights(top100),',
    '    sections = "".join([\n        html_priority_section(priority),\n        html_metric_highlights(top100),',
)
replace_once(
    '        "レポート形式": "dashboard_signal_quality_v2",',
    '        "レポート形式": "dashboard_priority_candidates_v3",',
)

path.write_text(text, encoding="utf-8")
print("Applied priority candidate email update")
