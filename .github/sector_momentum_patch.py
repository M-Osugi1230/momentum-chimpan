from pathlib import Path
import textwrap

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-11-dashboard-action-priority-v11"',
    'APP_VERSION = "2026-07-11-dashboard-sector-momentum-v12"',
    "app version",
)

replace_once(
    '''@dataclass
class Stock:
    code: str
    name: str
    market: str = ""
''',
    '''@dataclass
class Stock:
    code: str
    name: str
    market: str = ""
    sector33: str = ""
''',
    "stock sector field",
)

replace_once(
    '''def normalize_code(code: Any) -> str:
    return str(code).strip().split(".")[0].zfill(4)
''',
    '''def normalize_code(code: Any) -> str:
    return str(code).strip().split(".")[0].zfill(4)


def normalize_sector33(value: Any) -> str:
    """Normalize the JPX 33-sector name while preserving valid labels such as その他製品."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    result = str(value).strip()
    return "" if result.lower() in {"", "nan", "none", "-"} else result
''',
    "sector normalizer",
)

replace_once(
    '''    market_col = next((c for c in df.columns if "市場" in str(c) or "区分" in str(c)), None)
    type_col = next((c for c in df.columns if "規模" in str(c) or "商品" in str(c) or "33業種" in str(c)), None)
''',
    '''    market_col = next((c for c in df.columns if "市場" in str(c) or "区分" in str(c)), None)
    sector_col = next((c for c in df.columns if "33業種区分" in str(c)), None)
    if sector_col is None:
        sector_col = next((c for c in df.columns if "33業種" in str(c) and "コード" not in str(c)), None)
    type_col = next((c for c in df.columns if "規模" in str(c) or "商品" in str(c) or "33業種" in str(c)), None)
''',
    "sector source column",
)

replace_once(
    '''        market = str(row.get(market_col, "")) if market_col else ""
        type_text = " ".join(str(row.get(c, "")) for c in [market_col, type_col] if c)
''',
    '''        market = str(row.get(market_col, "")) if market_col else ""
        sector33 = normalize_sector33(row.get(sector_col, "")) if sector_col else ""
        type_text = " ".join(str(row.get(c, "")) for c in [market_col, type_col, sector_col] if c)
''',
    "sector extraction",
)

replace_once(
    '        stocks.append(Stock(code, name, market))',
    '        stocks.append(Stock(code, name, market, sector33))',
    "stock constructor",
)

replace_once(
    '        "date", "rank", "code", "name", "close", "score", "reason",',
    '        "date", "rank", "code", "name", "sector33", "close", "score", "reason",',
    "history sector column",
)

sector_code = textwrap.dedent(r'''

SECTOR_MOMENTUM_COLUMNS = [
    "date", "sector_rank", "sector33", "sector_momentum_score", "sector_strength",
    "stock_count", "top100_count", "top30_count", "top100_ratio", "avg_score", "median_score",
    "avg_return_20d", "median_return_20d", "avg_return_60d", "avg_volume_ratio",
    "above_ma20_ratio", "above_ma60_ratio", "ytd_high_count", "ytd_high_ratio",
    "representative_stocks", "previous_date", "previous_sector_rank", "previous_sector_score",
    "sector_rank_change", "sector_score_delta",
]


def numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if df.empty or column not in df.columns:
        return pd.Series(index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce")


def sector_flag_ratio(df: pd.DataFrame, column: str) -> float:
    if df.empty or column not in df.columns:
        return 0.0
    values = df[column].map(
        lambda value: str(value).strip().lower() in {"true", "1", "yes", "y"}
        if isinstance(value, str) else bool(value) if value is not None and not pd.isna(value) else False
    )
    return float(values.mean()) if len(values) else 0.0


def sector_metric_mean(df: pd.DataFrame, column: str) -> float:
    values = numeric_series(df, column).dropna()
    return float(values.mean()) if not values.empty else 0.0


def sector_metric_median(df: pd.DataFrame, column: str) -> float:
    values = numeric_series(df, column).dropna()
    return float(values.median()) if not values.empty else 0.0


def sector_momentum_score_values(
    avg_score: float,
    median_return_20d: float,
    above_ma20_ratio: float,
    above_ma60_ratio: float,
    top100_ratio: float,
    ytd_high_ratio: float,
) -> float:
    """Blend sector strength and breadth without changing any stock-level score."""
    score = 0.0
    score += 25 if avg_score >= 55 else 20 if avg_score >= 45 else 14 if avg_score >= 35 else 8 if avg_score >= 25 else 3
    score += 25 if median_return_20d >= 0.15 else 20 if median_return_20d >= 0.08 else 14 if median_return_20d >= 0.03 else 8 if median_return_20d >= 0 else 0
    score += 20 if above_ma20_ratio >= 0.75 else 15 if above_ma20_ratio >= 0.60 else 10 if above_ma20_ratio >= 0.45 else 5 if above_ma20_ratio >= 0.30 else 0
    score += 15 if above_ma60_ratio >= 0.75 else 11 if above_ma60_ratio >= 0.60 else 7 if above_ma60_ratio >= 0.45 else 3 if above_ma60_ratio >= 0.30 else 0
    score += 10 if top100_ratio >= 0.20 else 7 if top100_ratio >= 0.10 else 4 if top100_ratio >= 0.05 else 2 if top100_ratio > 0 else 0
    score += 5 if ytd_high_ratio >= 0.20 else 3 if ytd_high_ratio >= 0.10 else 1 if ytd_high_ratio >= 0.05 else 0
    return round(min(score, 100.0), 1)


def sector_strength_label(score: float) -> str:
    if score >= 75:
        return "強い"
    if score >= 60:
        return "やや強い"
    if score >= 45:
        return "中立"
    return "弱い"


def build_sector_momentum_snapshot(rows: pd.DataFrame, top_limit: int, report_date: str = "") -> pd.DataFrame:
    base_columns = SECTOR_MOMENTUM_COLUMNS[:20]
    if rows is None or rows.empty or "sector33" not in rows.columns:
        return pd.DataFrame(columns=base_columns)
    work = rows.copy()
    work["sector33"] = work["sector33"].map(normalize_sector33)
    work = work[work["sector33"] != ""].copy()
    if work.empty:
        return pd.DataFrame(columns=base_columns)
    work["rank"] = numeric_series(work, "rank")
    work["score"] = numeric_series(work, "score")
    work["is_sector_top100"] = work["rank"] <= top_limit
    work["is_sector_top30"] = work["rank"] <= 30

    records: list[dict[str, Any]] = []
    for sector33, group in work.groupby("sector33", sort=True):
        stock_count = int(len(group))
        top100_count = int(group["is_sector_top100"].sum())
        top30_count = int(group["is_sector_top30"].sum())
        top100_ratio = top100_count / stock_count if stock_count else 0.0
        avg_score = sector_metric_mean(group, "score")
        median_score = sector_metric_median(group, "score")
        avg_return_20d = sector_metric_mean(group, "return_20d")
        median_return_20d = sector_metric_median(group, "return_20d")
        avg_return_60d = sector_metric_mean(group, "return_60d")
        avg_volume_ratio = sector_metric_mean(group, "volume_ratio")
        above_ma20_ratio = sector_flag_ratio(group, "above_ma20")
        above_ma60_ratio = sector_flag_ratio(group, "above_ma60")
        ytd_high_ratio = sector_flag_ratio(group, "ytd_high_flag")
        ytd_high_count = int(round(ytd_high_ratio * stock_count))
        momentum_score = sector_momentum_score_values(
            avg_score, median_return_20d, above_ma20_ratio, above_ma60_ratio,
            top100_ratio, ytd_high_ratio,
        )
        representatives = group.sort_values(["rank", "score"], ascending=[True, False], na_position="last").head(3)
        representative_stocks = " / ".join(
            f"{normalize_code(row.get('code'))} {row.get('name', '')} (#{int(row.get('rank'))})"
            for _, row in representatives.iterrows() if row.get("rank") is not None and not pd.isna(row.get("rank"))
        )
        records.append({
            "date": report_date,
            "sector33": sector33,
            "sector_momentum_score": momentum_score,
            "sector_strength": sector_strength_label(momentum_score),
            "stock_count": stock_count,
            "top100_count": top100_count,
            "top30_count": top30_count,
            "top100_ratio": top100_ratio,
            "avg_score": round(avg_score, 2),
            "median_score": round(median_score, 2),
            "avg_return_20d": avg_return_20d,
            "median_return_20d": median_return_20d,
            "avg_return_60d": avg_return_60d,
            "avg_volume_ratio": round(avg_volume_ratio, 3),
            "above_ma20_ratio": above_ma20_ratio,
            "above_ma60_ratio": above_ma60_ratio,
            "ytd_high_count": ytd_high_count,
            "ytd_high_ratio": ytd_high_ratio,
            "representative_stocks": representative_stocks,
        })
    snapshot = pd.DataFrame(records)
    if snapshot.empty:
        return pd.DataFrame(columns=base_columns)
    snapshot = snapshot.sort_values(
        ["sector_momentum_score", "median_return_20d", "above_ma20_ratio", "top100_count", "sector33"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    snapshot.insert(1, "sector_rank", range(1, len(snapshot) + 1))
    return snapshot[[column for column in base_columns if column in snapshot.columns]]


def calculate_sector_momentum(all_ranked: pd.DataFrame, history: pd.DataFrame, today: str, top_limit: int) -> pd.DataFrame:
    current = build_sector_momentum_snapshot(all_ranked, top_limit, today)
    if current.empty:
        return pd.DataFrame(columns=SECTOR_MOMENTUM_COLUMNS)

    previous = pd.DataFrame()
    previous_date = ""
    if history is not None and not history.empty and {"date", "sector33"}.issubset(history.columns):
        prior = history.copy()
        prior["date_sort"] = pd.to_datetime(prior["date"], errors="coerce")
        prior["sector33"] = prior["sector33"].map(normalize_sector33)
        prior = prior.dropna(subset=["date_sort"])
        prior = prior[(prior["date"].astype(str) != str(today)) & (prior["sector33"] != "")]
        if not prior.empty:
            previous_date_value = prior["date_sort"].max()
            previous_date = previous_date_value.date().isoformat()
            previous_rows = prior[prior["date_sort"] == previous_date_value].copy()
            previous = build_sector_momentum_snapshot(previous_rows, top_limit, previous_date)

    current["previous_date"] = previous_date
    if previous.empty:
        current["previous_sector_rank"] = None
        current["previous_sector_score"] = None
        current["sector_rank_change"] = None
        current["sector_score_delta"] = None
    else:
        previous_index = previous.set_index("sector33")
        current["previous_sector_rank"] = current["sector33"].map(previous_index["sector_rank"])
        current["previous_sector_score"] = current["sector33"].map(previous_index["sector_momentum_score"])
        current["sector_rank_change"] = current["previous_sector_rank"] - current["sector_rank"]
        current["sector_score_delta"] = current["sector_momentum_score"] - current["previous_sector_score"]
    return current[[column for column in SECTOR_MOMENTUM_COLUMNS if column in current.columns]]


def sector_rank_change_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    number = int(float(value))
    if number > 0:
        return f"前回比 +{number}位"
    if number < 0:
        return f"前回比 {number}位"
    return "前回同順位"


def plain_sector_momentum_section(sector_momentum: pd.DataFrame, limit: int = 5) -> list[str]:
    if sector_momentum is None or sector_momentum.empty:
        return ["【業種別モメンタム（33業種）】", "業種分類を取得できなかったため、今回は集計対象外です。", ""]
    lines = [
        "【業種別モメンタム（33業種）】",
        "個別銘柄の順位は変えず、業種内の広がり・騰落率・移動平均線・Top100比率を比較しています。",
    ]
    for _, row in sector_momentum.head(limit).iterrows():
        movement = sector_rank_change_text(row.get("sector_rank_change"))
        movement_text = f" / {movement}" if movement else ""
        lines.append(
            f"#{int(row['sector_rank'])} {row['sector33']}｜{float(row['sector_momentum_score']):.1f}点 {row['sector_strength']}｜"
            f"Top100 {int(row['top100_count'])}/{int(row['stock_count'])}銘柄｜平均20日 {fmt_pct(row.get('avg_return_20d'))}｜"
            f"20日線上 {fmt_pct(row.get('above_ma20_ratio'))}{movement_text}"
        )
        if str(row.get("representative_stocks", "")).strip():
            lines.append(f"   上位銘柄: {row['representative_stocks']}")
    lines.append("")
    return lines


def html_sector_momentum_section(sector_momentum: pd.DataFrame, limit: int = 5) -> str:
    if sector_momentum is None or sector_momentum.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>業種別モメンタム（33業種）</b><div style="font-size:12px;color:#64748b;margin-top:5px">業種分類を取得できなかったため、今回は集計対象外です。</div></div>'
    colors = {
        "強い": ("#dcfce7", "#166534"),
        "やや強い": ("#dbeafe", "#1d4ed8"),
        "中立": ("#fef3c7", "#92400e"),
        "弱い": ("#f1f5f9", "#475569"),
    }
    items = []
    for _, row in sector_momentum.head(limit).iterrows():
        background, color = colors.get(str(row.get("sector_strength", "")), ("#f1f5f9", "#475569"))
        movement = sector_rank_change_text(row.get("sector_rank_change"))
        movement_html = f" ・ {html_text(movement)}" if movement else ""
        representatives = str(row.get("representative_stocks", "")).strip()
        representatives_html = f'<div style="font-size:10px;color:#64748b;margin-top:3px">上位銘柄: {html_text(representatives)}</div>' if representatives else ""
        items.append(
            f'<div style="border-top:1px solid #e5e7eb;padding:10px 0">'
            f'<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(row["sector_rank"])} {html_text(row["sector33"])} '
            f'<span style="display:inline-block;padding:2px 7px;border-radius:999px;background:{background};color:{color};font-size:11px">{float(row["sector_momentum_score"]):.1f}点 {html_text(row["sector_strength"])}</span></div>'
            f'<div style="font-size:11px;line-height:1.8;color:#475569">Top100 {int(row["top100_count"])}/{int(row["stock_count"])}銘柄 ・ 平均20日 {fmt_pct(row.get("avg_return_20d"))} ・ 20日線上 {fmt_pct(row.get("above_ma20_ratio"))}{movement_html}</div>'
            f'{representatives_html}</div>'
        )
    return (
        '<div style="background:#fff;border:2px solid #0f766e;border-radius:18px;padding:16px;margin-top:14px">'
        '<div style="font-size:18px;font-weight:900;color:#115e59">業種別モメンタム（33業種）</div>'
        '<div style="font-size:12px;line-height:1.7;color:#64748b;margin-top:4px">個別銘柄の順位は変えず、業種内の広がりと継続性を比較しています。</div>'
        + "".join(items) + '</div>'
    )
''')

replace_once(
    '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
    sector_code + '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
    "sector functions",
)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame,',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum: pd.DataFrame, new_entries: pd.DataFrame,',
    "excel signature",
)
replace_once(
    '        top100.to_excel(w, sheet_name="Momentum Top100", index=False)\n',
    '        top100.to_excel(w, sheet_name="Momentum Top100", index=False)\n        sector_momentum.to_excel(w, sheet_name="Sector Momentum", index=False)\n',
    "excel sector sheet",
)

replace_once(
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    "plain email signature",
)
replace_once(
    '    lines += plain_market_regime(regime)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
    '    lines += plain_market_regime(regime)\n    lines += plain_sector_momentum_section(sector_momentum)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
    "plain sector section",
)

replace_once(
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    "html email signature",
)
replace_once(
    '        html_market_regime(regime),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
    '        html_market_regime(regime),\n        html_sector_momentum_section(sector_momentum),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
    "html sector section",
)

replace_once(
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
    "send email signature",
)
replace_once(
    'build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, priority_changes, cfg)',
    'build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, priority_changes, cfg)',
    "plain email call",
)
replace_once(
    'build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, priority_changes, cfg)',
    'build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, priority_changes, cfg)',
    "html email call",
)

replace_once(
    '            row = {"code": st.code, "name": st.name, "score": sc, "reason": reason, **score_breakdown, **m}',
    '            row = {"code": st.code, "name": st.name, "sector33": st.sector33, "score": sc, "reason": reason, **score_breakdown, **m}',
    "scan row sector",
)
replace_once(
    '    ytd_high_ranking = all_ranked[all_ranked["ytd_high_flag"] == True].sort_values(["ytd_high_streak", "ytd_high_count", "score"], ascending=[False, False, False]).copy() if not all_ranked.empty else all_ranked.copy()\n    priority_changes = compare_priority_candidates(top100, history, today, top_limit)',
    '    ytd_high_ranking = all_ranked[all_ranked["ytd_high_flag"] == True].sort_values(["ytd_high_streak", "ytd_high_count", "score"], ascending=[False, False, False]).copy() if not all_ranked.empty else all_ranked.copy()\n    sector_momentum = calculate_sector_momentum(all_ranked, history, today, top_limit)\n    priority_changes = compare_priority_candidates(top100, history, today, top_limit)',
    "sector calculation",
)
replace_once(
    '    universe_df = pd.DataFrame([{"code": st.code, "name": st.name, "market": st.market, "scan_mode": "verification_limited" if limited_mode else "full"} for st in stocks])',
    '    universe_df = pd.DataFrame([{"code": st.code, "name": st.name, "market": st.market, "sector33": st.sector33, "scan_mode": "verification_limited" if limited_mode else "full"} for st in stocks])',
    "universe sector",
)
replace_once(
    '        "レポート形式": "dashboard_action_priority_v11",',
    '        "レポート形式": "dashboard_sector_momentum_v12",',
    "report format",
)
replace_once(
    '        "Momentum Top100": len(top100),\n        "重点候補数":',
    '        "Momentum Top100": len(top100),\n        "業種集計数": len(sector_momentum),\n        "強い業種数": int((sector_momentum.get("sector_strength", pd.Series(dtype=str)) == "強い").sum()) if not sector_momentum.empty else 0,\n        "やや強い業種数": int((sector_momentum.get("sector_strength", pd.Series(dtype=str)) == "やや強い").sum()) if not sector_momentum.empty else 0,\n        "最上位業種": str(sector_momentum.iloc[0]["sector33"]) if not sector_momentum.empty else "",\n        "最上位業種スコア": float(sector_momentum.iloc[0]["sector_momentum_score"]) if not sector_momentum.empty else None,\n        "重点候補数":',
    "sector summary",
)
replace_once(
    'excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, new_entries,',
    'excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, sector_momentum, new_entries,',
    "excel call",
)
replace_once(
    'send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, priority_changes, cfg)',
    'send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, priority_changes, cfg)',
    "send email call",
)

path.write_text(text, encoding="utf-8")
print("Applied sector momentum implementation")
