"""Momentum Chimpan: Japanese stock momentum dashboard.

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。
特定銘柄の売買を推奨するものではありません。
最終的な投資判断は利用者自身の責任で行ってください。
"""
from __future__ import annotations

import html
import logging
import os
import shutil
import signal
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd
import yfinance as yf
import yaml
from dotenv import load_dotenv

APP_VERSION = "2026-06-28-momentum-dashboard-v1"
JPX_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
DISCLAIMER = "本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。"
DAILY_RANKING_COLUMNS = [
    "date", "rank", "code", "name", "score", "return_20d", "volume_ratio", "ytd_high_flag",
    "ytd_high_streak", "ytd_high_count", "top30_streak", "rank_change", "new_entry",
    "rising_fast", "best_rank_update",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
STOP_REQUESTED = False


def request_stop(signum: int, frame: Any) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    logger.warning("Stop requested; current download will be interrupted or the scan will stop after it returns")


signal.signal(signal.SIGINT, request_stop)


def error_entry(code: str, name: str, error: str, stage: str, recoverable: bool = True) -> dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "code": code,
        "name": name,
        "error": error,
        "recoverable": recoverable,
    }


@dataclass
class Stock:
    code: str
    name: str
    market: str = ""


def load_config() -> dict[str, Any]:
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dirs(config: dict[str, Any]) -> None:
    paths = [
        "data/price_cache",
        Path(config["data"]["history_path"]).parent,
        Path(config["data"]["output_path"]).parent,
        Path(config["data"].get("ranking_history_path", "data/momentum_daily_ranking.csv")).parent,
        Path(config["data"].get("market_temperature_path", "data/market_temperature.csv")).parent,
    ]
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def normalize_code(code: Any) -> str:
    return str(code).strip().split(".")[0].zfill(4)


def market_matches(market_text: str, include_markets: set[str]) -> bool:
    """Match config market names against both English and JPX Japanese labels."""
    if not include_markets:
        return True
    aliases = {
        "Prime": ["Prime", "プライム"],
        "Standard": ["Standard", "スタンダード"],
        "Growth": ["Growth", "グロース"],
    }
    return any(any(alias in market_text for alias in aliases.get(m, [m])) for m in include_markets)


def load_universe(config: dict[str, Any]) -> tuple[list[Stock], list[dict[str, Any]], dict[str, int]]:
    errors: list[dict[str, Any]] = []
    stats = {"jpx_listed_count": 0, "universe_count": 0, "excluded_count": 0}
    cache = Path("data/jpx_list_cache.csv")
    try:
        logger.info("Downloading JPX listed issue list")
        df = pd.read_excel(JPX_LIST_URL)
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache, index=False)
    except Exception as exc:
        logger.warning("JPX list download failed: %s", exc)
        errors.append(error_entry("JPX", "listed issue list", str(exc), "load_universe", recoverable=cache.exists()))
        if cache.exists():
            df = pd.read_csv(cache)
        else:
            errors.append(error_entry("JPX", "listed issue list", "JPX list cache is missing", "load_universe", recoverable=False))
            return [], errors, stats

    code_col = next((c for c in df.columns if "コード" in str(c) or str(c).lower() == "code"), df.columns[0])
    name_col = next((c for c in df.columns if "銘柄名" in str(c) or "name" in str(c).lower()), df.columns[1])
    market_col = next((c for c in df.columns if "市場" in str(c) or "区分" in str(c)), None)
    type_col = next((c for c in df.columns if "規模" in str(c) or "商品" in str(c) or "33業種" in str(c)), None)
    include = set(config["market"].get("include_markets", []))
    excluded_words = ["ETF", "REIT", "不動産投信", "インフラ", "優先", "外国", "ETN"]
    stocks: list[Stock] = []
    valid_codes = 0
    for _, row in df.iterrows():
        code = normalize_code(row.get(code_col, ""))
        if not code.isdigit() or len(code) != 4:
            continue
        valid_codes += 1
        name = str(row.get(name_col, ""))
        market = str(row.get(market_col, "")) if market_col else ""
        type_text = " ".join(str(row.get(c, "")) for c in [market_col, type_col] if c)
        if not market_matches(market, include):
            continue
        if any(w.lower() in (name + type_text).lower() for w in excluded_words):
            continue
        stocks.append(Stock(code, name, market))
    stats["jpx_listed_count"] = valid_codes
    stats["universe_count"] = len(stocks)
    stats["excluded_count"] = max(valid_codes - len(stocks), 0)
    return stocks, errors, stats


def fetch_price(code: str, lookback_days: int, timeout_seconds: int = 20) -> pd.DataFrame:
    ticker = f"{code}.T"
    start = datetime.utcnow().date() - timedelta(days=int(lookback_days * 1.8))
    df = yf.download(ticker, start=start.isoformat(), progress=False, auto_adjust=False, threads=False, timeout=timeout_seconds)
    if df.empty:
        raise ValueError("yfinance returned empty data")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.title).reset_index()
    return df[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna().tail(lookback_days)


def metrics(df: pd.DataFrame) -> dict[str, Any]:
    close, high, vol = df["Close"], df["High"], df["Volume"]
    last_close, last_high, last_vol = float(close.iloc[-1]), float(high.iloc[-1]), float(vol.iloc[-1])
    ytd = df[pd.to_datetime(df["Date"]).dt.year == pd.to_datetime(df["Date"].iloc[-1]).year]
    prev_ytd_high = float(ytd["High"].iloc[:-1].max()) if len(ytd) > 1 else float("nan")
    ytd_flag = bool(pd.isna(prev_ytd_high) or last_high >= prev_ytd_high or last_close >= prev_ytd_high)
    cum = 0
    streak = 0
    running = -1.0
    flags = []
    for h, c in zip(ytd["High"], ytd["Close"]):
        f = float(h) >= running or float(c) >= running
        flags.append(f)
        if f:
            cum += 1
        running = max(running, float(h), float(c))
    for f in reversed(flags):
        if f:
            streak += 1
        else:
            break
    ma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else None
    avg20vol = float(vol.iloc[-21:-1].mean()) if len(vol) >= 21 else float(vol.tail(20).mean())

    def ret(n: int) -> float | None:
        return float(close.iloc[-1] / close.iloc[-n - 1] - 1) if len(close) > n and close.iloc[-n - 1] else None

    return {
        "close": last_close,
        "high": last_high,
        "volume": last_vol,
        "date": pd.to_datetime(df["Date"].iloc[-1]).date().isoformat(),
        "ytd_high_flag": ytd_flag,
        "ytd_high_streak": streak,
        "ytd_high_count": cum,
        "return_5d": ret(5),
        "return_20d": ret(20),
        "return_60d": ret(60),
        "ma20": ma20,
        "ma60": ma60,
        "ma20_deviation": (last_close / ma20 - 1) if ma20 else None,
        "ma60_deviation": (last_close / ma60 - 1) if ma60 else None,
        "volume_ratio": (last_vol / avg20vol) if avg20vol else None,
        "trading_value": last_close * last_vol,
        "above_ma20": bool(ma20 and last_close > ma20),
        "above_ma60": bool(ma60 and last_close > ma60),
    }


def score(m: dict[str, Any], min_trading_value: int) -> tuple[int, str, dict[str, int]]:
    reasons = []
    breakdown = {
        "score_ytd_high": 0,
        "score_ytd_streak": 0,
        "score_return_20d": 0,
        "score_volume_ratio": 0,
        "score_ma": 0,
        "score_trading_value": 0,
    }
    if m["ytd_high_flag"]:
        breakdown["score_ytd_high"] = 30
        reasons.append("年初来高値更新")
    st = m["ytd_high_streak"]
    breakdown["score_ytd_streak"] = 20 if st >= 8 else 16 if st >= 5 else 12 if st >= 3 else 8 if st >= 2 else 5 if st >= 1 else 0
    if breakdown["score_ytd_streak"]:
        reasons.append(f"連続更新{st}日")
    r20 = m.get("return_20d") or 0
    breakdown["score_return_20d"] = 20 if r20 >= .30 else 15 if r20 >= .20 else 10 if r20 >= .10 else 5 if r20 >= .05 else 0
    if breakdown["score_return_20d"]:
        reasons.append(f"20日騰落率{r20:.1%}")
    vr = m.get("volume_ratio") or 0
    breakdown["score_volume_ratio"] = 15 if vr >= 3 else 10 if vr >= 2 else 5 if vr >= 1.5 else 0
    if breakdown["score_volume_ratio"]:
        reasons.append(f"出来高倍率{vr:.1f}倍")
    if m.get("above_ma20"):
        breakdown["score_ma"] += 5
        reasons.append("20日線上")
    if m.get("above_ma60"):
        breakdown["score_ma"] += 5
        reasons.append("60日線上")
    if m.get("trading_value", 0) >= min_trading_value:
        breakdown["score_trading_value"] = 5
        reasons.append("売買代金1億円以上")
    return min(sum(breakdown.values()), 100), "、".join(reasons), breakdown


def write_history(rows: list[dict[str, Any]], path: str) -> None:
    cols = ["code", "name", "date", "ytd_high_flag", "ytd_high_streak", "ytd_high_count", "close", "high", "volume", "score"]
    new = pd.DataFrame(rows, columns=cols)
    old = pd.read_csv(path, dtype={"code": str}) if Path(path).exists() else pd.DataFrame(columns=cols)
    frames = [df for df in (old, new) if not df.empty]
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    out = out.drop_duplicates(["code", "date"], keep="last")
    out.to_csv(path, index=False)


def latest_past_date(history: pd.DataFrame, today: str) -> str | None:
    if history.empty or "date" not in history.columns:
        return None
    dates = sorted(str(d) for d in history["date"].dropna().unique() if str(d) < today)
    return dates[-1] if dates else None


def calculate_top30_streak(combined: pd.DataFrame, today: str) -> dict[str, int]:
    if combined.empty:
        return {}
    df = combined.copy()
    df["date"] = df["date"].astype(str)
    df["rank"] = pd.to_numeric(df["rank"], errors="coerce")
    dates_desc = sorted(df["date"].dropna().unique(), reverse=True)
    result: dict[str, int] = {}
    todays_codes = set(df.loc[df["date"] == today, "code"].astype(str))
    for code in todays_codes:
        streak = 0
        code_df = df[df["code"].astype(str) == code].set_index("date")
        for d in dates_desc:
            if d not in code_df.index:
                break
            ranks = code_df.loc[[d], "rank"]
            best_rank = pd.to_numeric(ranks, errors="coerce").min()
            if pd.notna(best_rank) and int(best_rank) <= 30:
                streak += 1
            else:
                break
        result[code] = streak
    return result


def enrich_ranking(today_df: pd.DataFrame, history: pd.DataFrame, today: str) -> tuple[pd.DataFrame, str | None]:
    cols = DAILY_RANKING_COLUMNS
    if today_df.empty:
        return pd.DataFrame(columns=cols), latest_past_date(history, today)
    ranked = today_df.sort_values(["score", "return_20d", "volume_ratio"], ascending=[False, False, False]).copy()
    ranked.insert(0, "rank", range(1, len(ranked) + 1))
    ranked["date"] = today
    prev_date = latest_past_date(history, today)
    prev_map: dict[str, int] = {}
    if prev_date:
        prev = history[history["date"].astype(str) == prev_date].copy()
        prev["rank"] = pd.to_numeric(prev.get("rank"), errors="coerce")
        prev_map = prev.dropna(subset=["rank"]).set_index("code")["rank"].astype(int).to_dict()
    best_map: dict[str, int] = {}
    if not history.empty and "rank" in history.columns:
        past = history[history["date"].astype(str) < today].copy()
        past["rank"] = pd.to_numeric(past.get("rank"), errors="coerce")
        if not past.empty:
            best_map = past.dropna(subset=["rank"]).groupby("code")["rank"].min().astype(int).to_dict()
    ranked["previous_rank"] = ranked["code"].map(prev_map)
    ranked["rank_change"] = ranked.apply(lambda r: int(r["previous_rank"] - r["rank"]) if pd.notna(r.get("previous_rank")) else None, axis=1)
    ranked["new_entry"] = ranked.apply(lambda r: int(r["rank"]) <= 100 and (pd.isna(r.get("previous_rank")) or int(r["previous_rank"]) > 100), axis=1)
    ranked["rising_fast"] = ranked["rank_change"].fillna(0).astype(int) >= 20
    ranked["previous_best_rank"] = ranked["code"].map(best_map)
    ranked["best_rank_update"] = ranked.apply(lambda r: pd.isna(r.get("previous_best_rank")) or int(r["rank"]) < int(r["previous_best_rank"]), axis=1)
    ranking_for_history = ranked[["date", "rank", "code", "name", "score", "return_20d", "volume_ratio", "ytd_high_flag", "ytd_high_streak", "ytd_high_count", "rank_change", "new_entry", "rising_fast", "best_rank_update"]].copy()
    combined = pd.concat([history, ranking_for_history], ignore_index=True) if not history.empty else ranking_for_history.copy()
    combined = combined.drop_duplicates(["code", "date"], keep="last")
    streak_map = calculate_top30_streak(combined, today)
    ranked["top30_streak"] = ranked["code"].map(streak_map).fillna(0).astype(int)
    return ranked, prev_date


def write_daily_ranking(ranked: pd.DataFrame, path: str) -> pd.DataFrame:
    cols = DAILY_RANKING_COLUMNS
    new = ranked[cols].copy() if not ranked.empty else pd.DataFrame(columns=cols)
    old = pd.read_csv(path, dtype={"code": str}) if Path(path).exists() else pd.DataFrame(columns=cols)
    frames = [df for df in (old, new) if not df.empty]
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    out = out.drop_duplicates(["code", "date"], keep="last")
    out.to_csv(path, index=False)
    return out


def write_market_temperature(today: str, all_df: pd.DataFrame, top100: pd.DataFrame, path: str) -> pd.DataFrame:
    cols = ["date", "ytd_high_count", "top100_avg_score", "top100_avg_return_20d", "top100_avg_volume_ratio", "ytd_high_count_change", "top100_avg_score_change", "top100_avg_return_20d_change", "top100_avg_volume_ratio_change"]
    old = pd.read_csv(path) if Path(path).exists() else pd.DataFrame(columns=cols)
    past = old[old["date"].astype(str) < today].copy() if not old.empty and "date" in old.columns else pd.DataFrame(columns=cols)
    prev = past.sort_values("date").iloc[-1].to_dict() if not past.empty else {}
    row = {
        "date": today,
        "ytd_high_count": int(all_df.get("ytd_high_flag", pd.Series(dtype=bool)).sum()) if not all_df.empty else 0,
        "top100_avg_score": float(top100["score"].mean()) if not top100.empty else None,
        "top100_avg_return_20d": float(top100["return_20d"].mean()) if not top100.empty else None,
        "top100_avg_volume_ratio": float(top100["volume_ratio"].mean()) if not top100.empty else None,
    }
    for key in ["ytd_high_count", "top100_avg_score", "top100_avg_return_20d", "top100_avg_volume_ratio"]:
        row[f"{key}_change"] = row[key] - prev[key] if prev and pd.notna(prev.get(key)) and pd.notna(row[key]) else None
    new = pd.DataFrame([row], columns=cols)
    out = pd.concat([old, new], ignore_index=True) if not old.empty else new
    out = out.drop_duplicates(["date"], keep="last").sort_values("date")
    out.to_csv(path, index=False)
    return out


def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, rank: pd.DataFrame, market_temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame([summary]).to_excel(w, sheet_name="Summary", index=False)
        top100.to_excel(w, sheet_name="Momentum Top100", index=False)
        new_entries.to_excel(w, sheet_name="New Entries", index=False)
        rising_fast.to_excel(w, sheet_name="Rising Fast", index=False)
        top30_streak.to_excel(w, sheet_name="Top30 Streak", index=False)
        rank.to_excel(w, sheet_name="YTD High Ranking", index=False)
        market_temperature.to_excel(w, sheet_name="Market Temperature", index=False)
        universe.to_excel(w, sheet_name="Scanned Universe", index=False)
        pd.DataFrame(errors).to_excel(w, sheet_name="Errors", index=False)
        for ws in w.book.worksheets:
            ws.freeze_panes = "A2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 42)


def backup_error_artifacts(errors: list[dict[str, Any]], cfg: dict[str, Any], report_path: str) -> None:
    if not errors:
        return
    backup_root = Path(cfg["data"].get("error_backup_dir", "data/error_backups"))
    backup_dir = backup_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(errors).to_csv(backup_dir / "errors.csv", index=False)
    for src in [report_path, cfg["data"].get("history_path"), cfg["data"].get("ranking_history_path"), cfg["data"].get("market_temperature_path"), "data/jpx_list_cache.csv"]:
        if src and Path(src).exists():
            shutil.copy2(src, backup_dir / Path(src).name)
    logger.warning("Backed up error artifacts to %s", backup_dir)


def fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.1%}"


def fmt_num(value: Any, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def fmt_int(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{int(float(value)):,}"


def fmt_seconds(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    seconds = float(value)
    if seconds >= 60:
        return f"{seconds / 60:.1f}分"
    return f"{seconds:.1f}秒"


def score_breakdown_items(r: pd.Series) -> list[tuple[str, int, int]]:
    return [
        ("年初来", int(r.get("score_ytd_high", 0)), 30),
        ("連続", int(r.get("score_ytd_streak", 0)), 20),
        ("20日", int(r.get("score_return_20d", 0)), 20),
        ("出来高", int(r.get("score_volume_ratio", 0)), 15),
        ("MA", int(r.get("score_ma", 0)), 10),
        ("代金", int(r.get("score_trading_value", 0)), 5),
    ]


def score_breakdown_text(r: pd.Series) -> str:
    return " / ".join(f"{label}{points}/{max_points}" for label, points, max_points in score_breakdown_items(r))


def compact_reason(reason: Any) -> str:
    text = str(reason or "条件該当なし")
    return text.replace("、", " / ")


def html_text(value: Any) -> str:
    return html.escape(str(value or ""))


def score_color(score: Any) -> str:
    value = int(score or 0)
    if value >= 85:
        return "#dc2626"
    if value >= 75:
        return "#ea580c"
    if value >= 60:
        return "#2563eb"
    return "#475569"


def compact_stock_line(r: pd.Series) -> str:
    return f"{int(r['rank'])}. {r['code']} {r['name']} {int(r['score'])}点 / 20日 {fmt_pct(r.get('return_20d'))} / 出来高 {fmt_num(r.get('volume_ratio'))}倍 / 連続 {int(r.get('ytd_high_streak', 0))}日"


def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, cfg: dict[str, Any]) -> str:
    top_n = cfg["ranking"]["email_top_n"]
    errors_count = int(summary.get("取得失敗", 0) or 0)
    ytd_count = int(summary.get("年初来高値更新", 0) or 0)
    success_count = int(summary.get("取得成功", 0) or 0)
    target_count = int(summary.get("実スキャン対象銘柄数", summary.get("通常株ユニバース数", 0)) or 0)
    success_rate = success_count / target_count if target_count else 0
    top = None if top100.empty else top100.iloc[0]
    lines = [
        "本日の日本株モメンタムダッシュボードです。",
        "",
        "【まず見るポイント】",
        f"買い候補TOP100：{summary.get('買い候補TOP100', 0)}件" + (f" / 最高：{top['code']} {top['name']} {int(top['score'])}点" if top is not None else ""),
        f"新規ランクイン：{summary.get('新規ランクイン', 0)}件 / 急上昇：{summary.get('急上昇', 0)}件 / TOP30継続10日以上：{summary.get('TOP30継続10日以上', 0)}件 / 年初来高値更新：{ytd_count}件",
        "※売買指示ではありません。確認対象の抽出結果です。",
        "",
        "【実行状況】",
        f"{summary.get('実行日', '')} / 対象 {target_count:,}銘柄 / 成功率 {success_rate:.1%} / 処理 {fmt_seconds(summary.get('処理時間秒'))}",
        f"比較対象日 {summary.get('比較対象日', 'なし')} / 検証 {summary.get('検証モード', '')} / 取得失敗 {errors_count}件",
        "",
        f"【モメンタムTOP{top_n}】",
    ]
    if top100.empty:
        lines.append("該当なし")
    for _, r in top100.head(top_n).iterrows():
        lines += [compact_stock_line(r), f"   内訳 {score_breakdown_text(r)}", f"   理由 {compact_reason(r.get('reason'))}", ""]
    sections = [("新規ランクイン 上位5件", new_entries), ("急上昇 上位5件", rising_fast), ("TOP30継続 上位5件", top30_streak)]
    for title, df in sections:
        lines += [f"【{title}】"]
        if df.empty:
            lines.append("該当なし")
        for _, r in df.head(5).iterrows():
            extra = f" / 変化 {fmt_int(r.get('rank_change'))}" if title.startswith("急上昇") else f" / 継続 {fmt_int(r.get('top30_streak'))}日" if title.startswith("TOP30") else ""
            lines.append(compact_stock_line(r) + extra)
        lines.append("")
    lines += [
        "【エラー】" + (f"取得失敗 {errors_count}件。Errorsシートを確認してください。" if errors_count else "取得失敗なし。"),
        "【詳細】GitHub Actions artifact の daily_report.xlsx を確認してください。",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def stock_cards(df: pd.DataFrame, limit: int, include_change: bool = False, include_streak: bool = False) -> str:
    if df.empty:
        return '<div style="color:#64748b;">該当なし</div>'
    cards = []
    for _, r in df.head(limit).iterrows():
        chips = "".join(
            f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:4px 8px;border-radius:999px;background:#f1f5f9;color:#334155;font-size:12px;">{html_text(label)} {points}/{max_points}</span>'
            for label, points, max_points in score_breakdown_items(r)
        )
        extra = ""
        if include_change and pd.notna(r.get("rank_change")):
            extra += f" ・ 順位上昇 <b>{fmt_int(r.get('rank_change'))}</b>"
        if include_streak:
            extra += f" ・ TOP30継続 <b>{fmt_int(r.get('top30_streak'))}日</b>"
        cards.append(f"""
        <div style="border:1px solid #e5e7eb;border-radius:16px;padding:14px;margin:10px 0;background:#ffffff;">
          <div style="display:block;margin-bottom:8px;">
            <span style="font-size:13px;color:#64748b;font-weight:700;">#{int(r['rank'])}</span>
            <span style="font-size:18px;font-weight:800;color:#111827;"> {html_text(r['code'])} {html_text(r['name'])}</span>
            <span style="float:right;background:{score_color(r['score'])};color:#ffffff;border-radius:999px;padding:4px 10px;font-size:14px;font-weight:800;">{int(r['score'])}点</span>
          </div>
          <div style="clear:both;font-size:13px;color:#334155;line-height:1.7;">
            20日 <b>{fmt_pct(r.get('return_20d'))}</b> ・ 出来高 <b>{fmt_num(r.get('volume_ratio'))}倍</b> ・ 連続 <b>{int(r.get('ytd_high_streak', 0))}日</b>{extra}
          </div>
          <div style="margin-top:10px;font-size:12px;color:#64748b;font-weight:800;">スコア内訳</div>
          <div style="margin-top:4px;line-height:1.6;">{chips}</div>
          <div style="margin-top:10px;font-size:12px;color:#64748b;font-weight:800;">理由</div>
          <div style="margin-top:4px;font-size:13px;color:#475569;line-height:1.6;">{html_text(compact_reason(r.get('reason')))}</div>
        </div>
        """)
    return "".join(cards)


def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, cfg: dict[str, Any]) -> str:
    top_n = cfg["ranking"]["email_top_n"]
    errors_count = int(summary.get("取得失敗", 0) or 0)
    ytd_count = int(summary.get("年初来高値更新", 0) or 0)
    success_count = int(summary.get("取得成功", 0) or 0)
    target_count = int(summary.get("実スキャン対象銘柄数", summary.get("通常株ユニバース数", 0)) or 0)
    success_rate = success_count / target_count if target_count else 0
    top = None if top100.empty else top100.iloc[0]

    def metric_card(label: str, value: str, color: str = "#111827") -> str:
        return f"""
        <td style="width:50%;padding:6px;vertical-align:top;">
          <div style="border:1px solid #e5e7eb;border-radius:14px;padding:12px;background:#ffffff;">
            <div style="font-size:12px;color:#64748b;line-height:1.3;">{html_text(label)}</div>
            <div style="font-size:22px;font-weight:800;color:{color};line-height:1.25;">{html_text(value)}</div>
          </div>
        </td>
        """

    top_text = "買い候補はありません"
    if top is not None:
        top_text = f"最高 {top['code']} {top['name']} {int(top['score'])}点"

    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans','Yu Gothic',Meiryo,Arial,sans-serif;color:#111827;">
    <div style="max-width:640px;margin:0 auto;padding:16px;">
      <div style="background:#0f172a;color:#ffffff;border-radius:20px;padding:20px;margin-bottom:14px;">
        <div style="font-size:13px;color:#cbd5e1;margin-bottom:6px;">日本株モメンタムダッシュボード</div>
        <div style="font-size:24px;font-weight:900;line-height:1.25;">{html_text(summary.get('実行日', ''))}</div>
        <div style="font-size:14px;line-height:1.6;color:#e2e8f0;margin-top:10px;">{html_text(top_text)}</div>
      </div>

      <div style="background:#ffffff;border-radius:18px;padding:16px;margin-bottom:14px;border:1px solid #e5e7eb;">
        <div style="font-size:18px;font-weight:900;margin-bottom:10px;">まず見るポイント</div>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
          <tr>{metric_card('買い候補TOP100', f"{summary.get('買い候補TOP100', 0)}件", '#dc2626')}{metric_card('新規ランクイン', f"{summary.get('新規ランクイン', 0)}件", '#2563eb')}</tr>
          <tr>{metric_card('急上昇', f"{summary.get('急上昇', 0)}件", '#ea580c')}{metric_card('TOP30継続10日以上', f"{summary.get('TOP30継続10日以上', 0)}件", '#16a34a')}</tr>
          <tr>{metric_card('年初来高値更新', f"{ytd_count}件", '#7c3aed')}{metric_card('取得失敗', f"{errors_count}件", '#dc2626' if errors_count else '#16a34a')}</tr>
        </table>
        <div style="font-size:12px;color:#64748b;line-height:1.6;margin-top:8px;">売買指示ではありません。日本株モメンタムの強弱と変化を確認するためのダッシュボードです。</div>
      </div>

      <div style="background:#ffffff;border-radius:18px;padding:16px;margin-bottom:14px;border:1px solid #e5e7eb;">
        <div style="font-size:18px;font-weight:900;margin-bottom:10px;">実行状況</div>
        <div style="font-size:14px;line-height:1.9;color:#334155;">
          対象 <b>{target_count:,}</b>銘柄 / 取得成功 <b>{success_count:,}</b> / 成功率 <b>{success_rate:.1%}</b><br>
          比較対象日 <b>{html_text(summary.get('比較対象日', 'なし'))}</b> / 検証 <b>{html_text(summary.get('検証モード', ''))}</b> / 処理 <b>{fmt_seconds(summary.get('処理時間秒'))}</b>
        </div>
      </div>

      <div style="background:#eff6ff;border-radius:18px;padding:16px;margin-bottom:14px;border:1px solid #bfdbfe;">
        <div style="font-size:18px;font-weight:900;margin-bottom:8px;color:#1e3a8a;">スコアの見方</div>
        <div style="font-size:13px;line-height:1.7;color:#1e40af;">100点満点：年初来30 / 連続20 / 20日20 / 出来高15 / MA10 / 代金5。点数が高いほど、直近の値動き・出来高・トレンド条件がそろっています。</div>
      </div>

      <div style="font-size:20px;font-weight:900;margin:18px 0 8px;">モメンタムTOP{top_n}</div>
      {stock_cards(top100, top_n)}

      <div style="font-size:20px;font-weight:900;margin:18px 0 8px;">新規ランクイン 上位5件</div>
      {stock_cards(new_entries, 5)}

      <div style="font-size:20px;font-weight:900;margin:18px 0 8px;">急上昇 上位5件</div>
      {stock_cards(rising_fast, 5, include_change=True)}

      <div style="font-size:20px;font-weight:900;margin:18px 0 8px;">TOP30継続 上位5件</div>
      {stock_cards(top30_streak, 5, include_streak=True)}

      <div style="background:#ffffff;border-radius:18px;padding:16px;margin-bottom:14px;border:1px solid #e5e7eb;">
        <div style="font-size:18px;font-weight:900;margin-bottom:8px;">エラー・詳細</div>
        <div style="font-size:14px;line-height:1.8;color:#334155;">取得失敗：<b>{errors_count}件</b><br>{'Errorsシートと data/error_backups を確認してください。' if errors_count else '本日の取得失敗はありません。'}<br>詳細は GitHub Actions artifact の <b>daily_report.xlsx</b> を確認してください。</div>
      </div>

      <div style="font-size:12px;line-height:1.7;color:#64748b;padding:8px 2px 20px;">{html_text(DISCLAIMER)}</div>
    </div>
  </body>
</html>"""


def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, cfg: dict[str, Any]) -> None:
    load_dotenv()
    sender, to, pw = os.getenv("EMAIL_FROM"), os.getenv("EMAIL_TO"), os.getenv("EMAIL_APP_PASSWORD")
    if not sender or not to or not pw:
        logger.info("Email secrets are not set; skip email")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"【モメンタムチンパン】{summary['実行日']} 日本株モメンタムダッシュボード"
    msg["From"], msg["To"] = sender, to
    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, cfg), "plain", "utf-8"))
    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, cfg), "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, pw)
        smtp.send_message(msg)


def main() -> None:
    started_at = perf_counter()
    cfg = load_config()
    ensure_dirs(cfg)
    stocks, errors, universe_stats = load_universe(cfg)
    full_universe_count = len(stocks)
    max_symbols = int(os.getenv("MOMENTUM_MAX_SYMBOLS", "0") or "0")
    if max_symbols > 0:
        logger.warning("VERIFICATION MODE: limiting universe from %s to first %s symbols", full_universe_count, max_symbols)
        stocks = stocks[:max_symbols]
    rows: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    success = 0
    today = datetime.now().date().isoformat()
    timeout_seconds = int(cfg["data"].get("request_timeout_seconds", 20))
    progress_interval = int(cfg["data"].get("progress_log_interval", 100))
    for idx, st in enumerate(stocks, start=1):
        if STOP_REQUESTED:
            logger.warning("Scan interrupted before processing %s; stopping", st.code)
            break
        if progress_interval > 0 and (idx == 1 or idx % progress_interval == 0 or idx == len(stocks)):
            logger.info("Progress: %s/%s symbols processed", idx, len(stocks))
        try:
            df = fetch_price(st.code, cfg["data"]["lookback_days"], timeout_seconds)
            m = metrics(df)
            if m["date"] != today:
                logger.info("%s latest price date is %s (not today %s)", st.code, m["date"], today)
            sc, reason, score_breakdown = score(m, cfg["market"]["min_trading_value"])
            success += 1
            row = {"code": st.code, "name": st.name, "score": sc, "reason": reason, **score_breakdown, **m}
            if m["close"] >= cfg["market"].get("min_price", 0):
                rows.append(row)
            history_rows.append({k: row.get(k) for k in ["code", "name", "date", "ytd_high_flag", "ytd_high_streak", "ytd_high_count", "close", "high", "volume", "score"]})
        except Exception as exc:
            if STOP_REQUESTED:
                logger.warning("Scan interrupted while processing %s; stopping", st.code)
                errors.append(error_entry(st.code, st.name, f"interrupted: {exc}", "fetch_price", recoverable=True))
                break
            logger.exception("Failed processing %s", st.code)
            errors.append(error_entry(st.code, st.name, str(exc), "fetch_price", recoverable=True))

    all_df = pd.DataFrame(rows)
    ranking_history_path = cfg["data"].get("ranking_history_path", "data/momentum_daily_ranking.csv")
    existing_ranking = pd.read_csv(ranking_history_path, dtype={"code": str}) if Path(ranking_history_path).exists() else pd.DataFrame(columns=DAILY_RANKING_COLUMNS)
    ranked, prev_run_date = enrich_ranking(all_df, existing_ranking, today)
    write_history(history_rows, cfg["data"]["history_path"])
    write_daily_ranking(ranked, ranking_history_path)

    top100_cols = ["rank", "code", "name", "close", "score", "rank_change", "new_entry", "rising_fast", "best_rank_update", "top30_streak", "reason", "score_ytd_high", "score_ytd_streak", "score_return_20d", "score_volume_ratio", "score_ma", "score_trading_value", "ytd_high_flag", "ytd_high_streak", "ytd_high_count", "return_5d", "return_20d", "return_60d", "volume_ratio", "trading_value", "ma20", "ma60", "above_ma20", "above_ma60"]
    top100 = ranked.head(cfg["ranking"]["buy_candidate_limit"])[top100_cols].copy() if not ranked.empty else pd.DataFrame(columns=top100_cols)
    new_entries = top100[top100.get("new_entry", pd.Series(dtype=bool)).fillna(False)].copy() if not top100.empty else pd.DataFrame(columns=top100_cols)
    rising_fast = top100[top100.get("rising_fast", pd.Series(dtype=bool)).fillna(False)].sort_values(["rank_change", "score"], ascending=[False, False]).copy() if not top100.empty else pd.DataFrame(columns=top100_cols)
    top30_streak_df = top100[top100.get("top30_streak", pd.Series(dtype=int)).fillna(0).astype(int) > 0].sort_values(["top30_streak", "rank"], ascending=[False, True]).copy() if not top100.empty else pd.DataFrame(columns=top100_cols)
    rank_cols = ["code", "name", "close", "ytd_high_streak", "ytd_high_count", "score", "return_20d", "volume_ratio", "rank"]
    ytd_ranking = ranked.sort_values(["ytd_high_streak", "ytd_high_count", "score"], ascending=False)[rank_cols] if not ranked.empty else pd.DataFrame(columns=rank_cols)
    market_temperature = write_market_temperature(today, all_df, top100, cfg["data"].get("market_temperature_path", "data/market_temperature.csv"))

    elapsed = round(perf_counter() - started_at, 1)
    limited_mode = max_symbols > 0 and max_symbols < full_universe_count
    universe_df = pd.DataFrame([{"code": st.code, "name": st.name, "market": st.market, "scan_mode": "verification_limited" if limited_mode else "full"} for st in stocks])
    summary = {
        "実行日": today,
        "アプリ版": APP_VERSION,
        "レポート形式": "momentum_dashboard_v1",
        "JPX上場銘柄数": universe_stats.get("jpx_listed_count", 0),
        "通常株ユニバース数": full_universe_count,
        "除外銘柄数": universe_stats.get("excluded_count", 0),
        "実スキャン対象銘柄数": len(stocks),
        "取得成功": success,
        "取得失敗": len(errors),
        "年初来高値更新": int(all_df.get("ytd_high_flag", pd.Series(dtype=bool)).sum()) if not all_df.empty else 0,
        "買い候補TOP100": len(top100),
        "新規ランクイン": len(new_entries),
        "急上昇": len(rising_fast),
        "TOP30継続10日以上": int((top100.get("top30_streak", pd.Series(dtype=int)).fillna(0).astype(int) >= 10).sum()) if not top100.empty else 0,
        "比較対象日": prev_run_date or "なし",
        "検証モード": "YES" if limited_mode else "NO",
        "銘柄数制限": max_symbols if max_symbols > 0 else "なし",
        "処理時間秒": elapsed,
        "注意事項": DISCLAIMER,
    }
    excel_report(cfg["data"]["output_path"], summary, top100, new_entries, rising_fast, top30_streak_df, ytd_ranking, market_temperature, errors, universe_df)
    backup_error_artifacts(errors, cfg, cfg["data"]["output_path"])
    try:
        send_email(summary, top100, new_entries, rising_fast, top30_streak_df, cfg)
    except Exception as exc:
        logger.exception("Email sending failed: %s", exc)


if __name__ == "__main__":
    main()
