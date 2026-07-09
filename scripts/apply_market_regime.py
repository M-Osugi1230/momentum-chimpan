from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match, found {count}: {old[:120]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-10-dashboard-top30-compact-v4"',
    'APP_VERSION = "2026-07-10-dashboard-market-regime-v5"',
)

helper_code = r'''

def series_ratio(df: pd.DataFrame, column: str) -> float:
    if df.empty or column not in df.columns:
        return 0.0
    values = df[column].fillna(False).astype(bool)
    return float(values.mean()) if len(values) else 0.0


def calculate_market_regime(top100: pd.DataFrame, temperature: pd.DataFrame) -> dict[str, Any]:
    """Classify the market environment from breadth, momentum, volume and heat."""
    temp = {} if temperature.empty else temperature.iloc[0].to_dict()
    avg_score = float(temp.get("top100_avg_score", 0) or 0)
    avg_return_20d = float(temp.get("top100_avg_return_20d", 0) or 0)
    avg_volume_ratio = float(temp.get("top100_avg_volume_ratio", 0) or 0)
    ytd_high_count = int(float(temp.get("ytd_high_count", 0) or 0))
    ma20_ratio = series_ratio(top100, "above_ma20")
    ma60_ratio = series_ratio(top100, "above_ma60")

    if top100.empty:
        overheat_count = 0
        overheat_ratio = 0.0
    else:
        return20 = pd.to_numeric(top100.get("return_20d", pd.Series(index=top100.index, dtype=float)), errors="coerce").fillna(0)
        ma20_deviation = pd.to_numeric(top100.get("ma20_deviation", pd.Series(index=top100.index, dtype=float)), errors="coerce").fillna(0)
        volume_ratio = pd.to_numeric(top100.get("volume_ratio", pd.Series(index=top100.index, dtype=float)), errors="coerce").fillna(0)
        overheat_mask = (return20 >= 0.50) | (ma20_deviation >= 0.25) | (volume_ratio >= 8.0)
        overheat_count = int(overheat_mask.sum())
        overheat_ratio = float(overheat_mask.mean())

    score = 0
    score += 25 if avg_score >= 70 else 18 if avg_score >= 60 else 10 if avg_score >= 50 else 3
    score += 20 if avg_return_20d >= 0.15 else 14 if avg_return_20d >= 0.05 else 8 if avg_return_20d >= 0 else 0
    score += 15 if avg_volume_ratio >= 2.0 else 10 if avg_volume_ratio >= 1.5 else 5 if avg_volume_ratio >= 1.0 else 0
    score += 15 if ma20_ratio >= 0.80 else 10 if ma20_ratio >= 0.65 else 5 if ma20_ratio >= 0.50 else 0
    score += 15 if ma60_ratio >= 0.80 else 10 if ma60_ratio >= 0.65 else 5 if ma60_ratio >= 0.50 else 0
    score += 10 if ytd_high_count >= 100 else 6 if ytd_high_count >= 50 else 3 if ytd_high_count >= 20 else 0
    score = min(int(score), 100)

    if score >= 75:
        base_label = "強気"
    elif score >= 60:
        base_label = "やや強気"
    elif score >= 45:
        base_label = "中立"
    else:
        base_label = "弱気"

    overheated = score >= 60 and (overheat_ratio >= 0.20 or (avg_return_20d >= 0.25 and overheat_ratio >= 0.15))
    label = "過熱警戒" if overheated else base_label

    if label == "過熱警戒":
        guidance = "上昇基調は強い一方、飛びつきを避け、押し目・出来高減速・20日線乖離を確認してください。"
        color = "#b91c1c"
        background = "#fef2f2"
    elif label == "強気":
        guidance = "重点候補と継続銘柄を優先し、出来高と流動性を確認しながら順張り候補を精査する局面です。"
        color = "#15803d"
        background = "#f0fdf4"
    elif label == "やや強気":
        guidance = "初動・加速候補を中心に選別し、複数シグナルが重なる銘柄を優先してください。"
        color = "#2563eb"
        background = "#eff6ff"
    elif label == "中立":
        guidance = "ランキング変化を観察し、単独シグナルではなく複数条件が重なる銘柄に絞る局面です。"
        color = "#a16207"
        background = "#fefce8"
    else:
        guidance = "新規候補を絞り、流動性・損切り水準・移動平均線の回復を重視する局面です。"
        color = "#475569"
        background = "#f8fafc"

    return {
        "label": label,
        "base_label": base_label,
        "score": score,
        "guidance": guidance,
        "color": color,
        "background": background,
        "ma20_ratio": ma20_ratio,
        "ma60_ratio": ma60_ratio,
        "overheat_count": overheat_count,
        "overheat_ratio": overheat_ratio,
        "avg_score": avg_score,
        "avg_return_20d": avg_return_20d,
        "avg_volume_ratio": avg_volume_ratio,
        "ytd_high_count": ytd_high_count,
    }


def plain_market_regime(regime: dict[str, Any]) -> list[str]:
    return [
        "【Market Regime】",
        f"判定: {regime['label']} / 市場環境スコア {regime['score']}点",
        f"20日線上 {regime['ma20_ratio']:.1%} / 60日線上 {regime['ma60_ratio']:.1%} / 過熱銘柄 {regime['overheat_count']}件 ({regime['overheat_ratio']:.1%})",
        f"方針: {regime['guidance']}",
        "",
    ]


def html_market_regime(regime: dict[str, Any]) -> str:
    return f"""<div style="background:{regime['background']};border:2px solid {regime['color']};border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:12px;font-weight:800;color:{regime['color']}">MARKET REGIME</div>
<div style="font-size:24px;font-weight:900;color:{regime['color']};margin-top:2px">{html_text(regime['label'])} <span style="font-size:16px">{regime['score']}点</span></div>
<div style="font-size:12px;line-height:1.8;color:#334155;margin-top:8px">20日線上 <b>{regime['ma20_ratio']:.1%}</b> ・ 60日線上 <b>{regime['ma60_ratio']:.1%}</b> ・ 過熱銘柄 <b>{regime['overheat_count']}件 ({regime['overheat_ratio']:.1%})</b></div>
<div style="font-size:13px;line-height:1.8;color:#334155;margin-top:6px"><b>本日の方針:</b> {html_text(regime['guidance'])}</div>
</div>"""
'''

replace_once(
    "\n\ndef build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:",
    helper_code + "\n\ndef build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:",
)

replace_once(
    '    compact_ranked = compact_rank_slice(top100, top_n + 1, 30)\n    lines = [',
    '    compact_ranked = compact_rank_slice(top100, top_n + 1, 30)\n    regime = calculate_market_regime(top100, temperature)\n    lines = [',
)
replace_once(
    '    lines += plain_priority_section(priority)\n',
    '    lines += plain_market_regime(regime)\n    lines += plain_priority_section(priority)\n',
)

old_html_marker = '    compact_ranked = compact_rank_slice(top100, top_n + 1, 30)\n    cards = ['
if text.count(old_html_marker) != 1:
    raise RuntimeError(f"Expected one HTML compact marker, found {text.count(old_html_marker)}")
text = text.replace(
    old_html_marker,
    '    compact_ranked = compact_rank_slice(top100, top_n + 1, 30)\n    regime = calculate_market_regime(top100, temperature)\n    cards = [',
    1,
)
replace_once(
    '        html_priority_section(priority),\n',
    '        html_market_regime(regime),\n        html_priority_section(priority),\n',
)

replace_once(
    '    temperature = market_temperature(today, all_ranked, top100, old_temp)\n',
    '    temperature = market_temperature(today, all_ranked, top100, old_temp)\n    regime = calculate_market_regime(top100, temperature)\n',
)
replace_once(
    '        "レポート形式": "dashboard_top30_compact_v4",',
    '        "レポート形式": "dashboard_market_regime_v5",',
)
replace_once(
    '        "Momentum Top100": len(top100),\n',
    '        "Momentum Top100": len(top100),\n        "Market Regime": regime["label"],\n        "Market Regime Score": regime["score"],\n        "Top100 20日線上比率": regime["ma20_ratio"],\n        "Top100 60日線上比率": regime["ma60_ratio"],\n        "Top100 過熱銘柄数": regime["overheat_count"],\n        "Top100 過熱銘柄比率": regime["overheat_ratio"],\n',
)

path.write_text(text, encoding="utf-8")
print("Applied Market Regime update")
