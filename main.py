"""Momentum Chimpan: Japanese stock momentum screener.

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
from time import perf_counter
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
import yaml
from dotenv import load_dotenv

APP_VERSION = "2026-07-05-dashboard-full-history-v1"
JPX_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
DISCLAIMER = "本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。"

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
    for p in [
        "data/price_cache",
        Path(config["data"]["ranking_history_path"]).parent,
        Path(config["data"]["market_temperature_path"]).parent,
        Path(config["data"]["output_path"]).parent,
    ]:
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
        "price_date": pd.to_datetime(df["Date"].iloc[-1]).date().isoformat(),
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
        "prev_close": float(close.iloc[-2]) if len(close) >= 2 else None,
        "recent_high": float(high.tail(60).max()),
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


def ranking_history_columns() -> list[str]:
    return [
        "date", "rank", "code", "name", "close", "score", "reason", "score_ytd_high", "score_ytd_streak",
        "score_return_20d", "score_volume_ratio", "score_ma", "score_trading_value", "ytd_high_flag",
        "ytd_high_streak", "ytd_high_count", "return_5d", "return_20d", "return_60d", "volume_ratio",
        "trading_value", "ma20", "ma60", "above_ma20", "above_ma60", "price_date", "is_top100",
        "is_new_entry", "rank_change", "is_rising_fast", "is_best_rank", "top30_streak", "top30_streak_days",
    ]


def load_ranking_history(path: str) -> pd.DataFrame:
    if Path(path).exists():
        return pd.read_csv(path, dtype={"code": str})
    return pd.DataFrame(columns=ranking_history_columns())


def enrich_ranking_features(all_ranked: pd.DataFrame, history: pd.DataFrame, today: str, top_limit: int) -> pd.DataFrame:
    if all_ranked.empty:
        return all_ranked
    ranked = all_ranked.copy()
    for col in ["rank", "date"]:
        if col in ranked.columns:
            ranked = ranked.drop(columns=[col])
    ranked.insert(0, "rank", range(1, len(ranked) + 1))
    ranked.insert(0, "date", today)
    ranked["code"] = ranked["code"].map(normalize_code)
    ranked["is_top100"] = ranked["rank"] <= top_limit

    prior = history[history["date"] != today].copy() if not history.empty and "date" in history.columns else history.copy()
    latest_rank = {}
    previous_top100: set[str] = set()
    previous_top30_streak = {}
    previous_top30_codes: set[str] = set()
    best_rank = {}

    if not prior.empty:
        prior["code"] = prior["code"].map(normalize_code)
        prior["date_sort"] = pd.to_datetime(prior["date"], errors="coerce")
        prior = prior.dropna(subset=["date_sort", "code"])
    if not prior.empty:
        previous_date = prior["date_sort"].max()
        previous = prior[prior["date_sort"] == previous_date].copy()
        previous_top100 = set(previous[previous["rank"] <= top_limit]["code"])
        previous_top30 = previous[previous["rank"] <= 30].copy()
        previous_top30_codes = set(previous_top30["code"])
        latest_rank = dict(zip(previous["code"], previous["rank"]))
        streak_col = "top30_streak" if "top30_streak" in previous_top30.columns else "top30_streak_days"
        previous_top30_streak = dict(zip(previous_top30["code"], previous_top30.get(streak_col, pd.Series(1, index=previous_top30.index)).fillna(1)))
        best_rank = prior.groupby("code")["rank"].min().to_dict()

    ranked["is_new_entry"] = ranked["is_top100"] & ~ranked["code"].isin(previous_top100)
    ranked["rank_change"] = ranked.apply(lambda r: (int(latest_rank.get(r["code"])) - int(r["rank"])) if r["code"] in latest_rank else None, axis=1)
    ranked["is_rising_fast"] = ranked["is_top100"] & (ranked["rank_change"].fillna(0) >= 20)
    ranked["is_best_rank"] = ranked.apply(lambda r: True if r["code"] not in best_rank else int(r["rank"]) < int(best_rank[r["code"]]), axis=1)
    ranked["top30_streak"] = ranked.apply(
        lambda r: (int(previous_top30_streak.get(r["code"], 0)) + 1) if int(r["rank"]) <= 30 and r["code"] in previous_top30_codes else (1 if int(r["rank"]) <= 30 else 0),
        axis=1,
    )
    ranked["top30_streak_days"] = ranked["top30_streak"]
    return ranked


def write_ranking_history(all_ranked: pd.DataFrame, path: str) -> None:
    old = load_ranking_history(path)
    frames = [df for df in (old, all_ranked) if not df.empty]
    out = pd.concat(frames, ignore_index=True) if frames else all_ranked
    if not out.empty:
        out["code"] = out["code"].map(normalize_code)
        out = out.drop_duplicates(["code", "date"], keep="last").sort_values(["date", "rank"])
        cols = [c for c in ranking_history_columns() if c in out.columns] + [c for c in out.columns if c not in ranking_history_columns()]
        out = out[cols]
    out.to_csv(path, index=False)


def market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "date", "ytd_high_count", "top100_avg_score", "top100_avg_return_20d", "top100_avg_volume_ratio",
        "delta_ytd_high_count", "delta_top100_avg_score", "delta_top100_avg_return_20d", "delta_top100_avg_volume_ratio",
    ]
    current = {
        "date": today,
        "ytd_high_count": int(all_ranked.get("ytd_high_flag", pd.Series(dtype=bool)).fillna(False).sum()) if not all_ranked.empty else 0,
        "top100_avg_score": round(float(top100["score"].mean()), 4) if not top100.empty else 0.0,
        "top100_avg_return_20d": round(float(top100["return_20d"].dropna().mean()), 6) if not top100.empty and top100["return_20d"].notna().any() else 0.0,
        "top100_avg_volume_ratio": round(float(top100["volume_ratio"].dropna().mean()), 4) if not top100.empty and top100["volume_ratio"].notna().any() else 0.0,
    }

    prior = previous_temperature.copy() if previous_temperature is not None else pd.DataFrame(columns=cols)
    if not prior.empty and "date" in prior.columns:
        prior = prior[prior["date"] != today].copy()
        prior["date_sort"] = pd.to_datetime(prior["date"], errors="coerce")
        prior = prior.dropna(subset=["date_sort"]).sort_values("date_sort")
    if prior.empty:
        deltas = {
            "delta_ytd_high_count": 0,
            "delta_top100_avg_score": 0.0,
            "delta_top100_avg_return_20d": 0.0,
            "delta_top100_avg_volume_ratio": 0.0,
        }
    else:
        prev = prior.iloc[-1]
        deltas = {
            "delta_ytd_high_count": current["ytd_high_count"] - int(float(prev.get("ytd_high_count", 0) or 0)),
            "delta_top100_avg_score": round(current["top100_avg_score"] - float(prev.get("top100_avg_score", 0) or 0), 4),
            "delta_top100_avg_return_20d": round(current["top100_avg_return_20d"] - float(prev.get("top100_avg_return_20d", 0) or 0), 6),
            "delta_top100_avg_volume_ratio": round(current["top100_avg_volume_ratio"] - float(prev.get("top100_avg_volume_ratio", 0) or 0), 4),
        }
    return pd.DataFrame([{**current, **deltas}], columns=cols)


def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame([summary]).to_excel(w, sheet_name="Summary", index=False)
        top100.to_excel(w, sheet_name="Momentum Top100", index=False)
        new_entries.to_excel(w, sheet_name="New Entries", index=False)
        rising_fast.to_excel(w, sheet_name="Rising Fast", index=False)
        top30_streak.to_excel(w, sheet_name="Top30 Streak", index=False)
        ytd_high_ranking.to_excel(w, sheet_name="YTD High Ranking", index=False)
        temperature.to_excel(w, sheet_name="Market Temperature", index=False)
        universe.to_excel(w, sheet_name="Scanned Universe", index=False)
        pd.DataFrame(errors).to_excel(w, sheet_name="Errors", index=False)
        for ws in w.book.worksheets:
            ws.freeze_panes = "A2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 40)


def backup_error_artifacts(errors: list[dict[str, Any]], cfg: dict[str, Any], report_path: str) -> None:
    if not errors:
        return
    backup_root = Path(cfg["data"].get("error_backup_dir", "data/error_backups"))
    backup_dir = backup_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(errors).to_csv(backup_dir / "errors.csv", index=False)
    for src in [report_path, cfg["data"].get("ranking_history_path"), "data/jpx_list_cache.csv"]:
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


def fmt_delta(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    number = float(value)
    sign = "+" if number > 0 else ""
    if digits == 0:
        return f"{sign}{int(number):,}"
    return f"{sign}{number:,.{digits}f}"


def compact_reason(reason: Any) -> str:
    return str(reason or "条件該当なし").replace("、", " / ")


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


def ranking_badges(r: pd.Series) -> list[str]:
    badges = []
    if bool(r.get("is_new_entry")):
        badges.append("NEW")
    if bool(r.get("is_rising_fast")):
        badges.append(f"急上昇 +{fmt_int(r.get('rank_change'))}")
    if bool(r.get("is_best_rank")):
        badges.append("最高順位")
    if int(r.get("top30_streak", r.get("top30_streak_days", 0)) or 0):
        badges.append(f"TOP30 {int(r.get('top30_streak', r.get('top30_streak_days')))}日")
    return badges


def plain_ranking_section(title: str, df: pd.DataFrame, limit: int = 5) -> list[str]:
    lines = [f"【{title}】"]
    if df.empty:
        return lines + ["該当なし", ""]
    for _, r in df.head(limit).iterrows():
        badges = " / ".join(ranking_badges(r))
        lines += [
            f"{int(r['rank'])}. {r['code']} {r['name']} {int(r['score'])}点" + (f" {badges}" if badges else ""),
            f"   20日 {fmt_pct(r.get('return_20d'))} / 出来高 {fmt_num(r.get('volume_ratio'))}倍 / 連続 {int(r.get('ytd_high_streak', 0))}日",
            f"   理由 {compact_reason(r.get('reason'))}",
            "",
        ]
    return lines


def html_ranking_cards(df: pd.DataFrame, limit: int = 5) -> str:
    if df.empty:
        return '<div style="color:#64748b">該当なし</div>'
    items = []
    for _, r in df.head(limit).iterrows():
        badge_html = "".join(f'<span style="display:inline-block;margin:2px;padding:3px 8px;border-radius:999px;background:#e0f2fe;color:#075985;font-size:12px;font-weight:700">{html_text(b)}</span>' for b in ranking_badges(r))
        items.append(f'<div style="border:1px solid #e5e7eb;border-radius:16px;padding:14px;margin:10px 0;background:#fff"><div><b>#{int(r["rank"])} {html_text(r["code"])} {html_text(r["name"])}</b><span style="float:right;background:{score_color(r["score"])};color:#fff;border-radius:999px;padding:4px 10px;font-weight:800">{int(r["score"])}点</span></div><div style="clear:both;margin-top:8px">{badge_html}</div><div style="font-size:13px;color:#334155;line-height:1.7;margin-top:8px">20日 <b>{fmt_pct(r.get("return_20d"))}</b> ・ 出来高 <b>{fmt_num(r.get("volume_ratio"))}倍</b> ・ 連続 <b>{int(r.get("ytd_high_streak", 0))}日</b></div><div style="font-size:13px;color:#64748b;margin-top:6px">{html_text(compact_reason(r.get("reason")))}</div></div>')
    return "".join(items)


def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:
    top_n = cfg["ranking"]["email_top_n"]
    temp = {} if temperature.empty else temperature.iloc[0].to_dict()
    lines = [
        "本日のモメンタム・ダッシュボードです。",
        "",
        "【まず見るポイント】",
        f"買い候補TOP100: {len(top100)}件",
        f"新規ランクイン: {summary.get('新規ランクイン', 0)}件",
        f"急上昇: {summary.get('急上昇', 0)}件",
        f"TOP30継続10日以上: {summary.get('TOP30継続10日以上', 0)}件",
        f"年初来高値更新: {summary.get('年初来高値更新', 0)}件",
        f"取得失敗: {summary.get('取得失敗', 0)}件",
        "※売買指示ではありません。確認対象の抽出結果です。",
        "",
        "【Market Temperature】",
        f"YTD高値 {fmt_int(temp.get('ytd_high_count'))} ({fmt_delta(temp.get('delta_ytd_high_count'), 0)}) / Top100平均スコア {fmt_num(temp.get('top100_avg_score'), 2)} ({fmt_delta(temp.get('delta_top100_avg_score'), 2)})",
        f"Top100平均20日騰落率 {fmt_pct(temp.get('top100_avg_return_20d'))} ({fmt_delta(temp.get('delta_top100_avg_return_20d'), 4)}) / Top100平均出来高倍率 {fmt_num(temp.get('top100_avg_volume_ratio'), 2)} ({fmt_delta(temp.get('delta_top100_avg_volume_ratio'), 2)})",
        "",
    ]
    lines += plain_ranking_section(f"Momentum Top{top_n}", top100, top_n)
    lines += plain_ranking_section("新規ランクイン 上位5件", new_entries, 5)
    lines += plain_ranking_section("急上昇 上位5件", rising_fast, 5)
    lines += plain_ranking_section("TOP30継続ランキング 上位5件", top30_streak, 5)
    lines += ["【詳細】GitHub Actions artifact の daily_report.xlsx を確認してください。", "", DISCLAIMER]
    return "\n".join(lines)


def metric_card(label: str, value: str, color: str = "#111827") -> str:
    return f'<td style="width:50%;padding:6px"><div style="border:1px solid #e5e7eb;border-radius:14px;padding:12px;background:#fff"><div style="font-size:12px;color:#64748b">{html_text(label)}</div><div style="font-size:22px;font-weight:800;color:{color}">{html_text(value)}</div></div></td>'


def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:
    top_n = cfg["ranking"]["email_top_n"]
    temp = {} if temperature.empty else temperature.iloc[0].to_dict()
    cards = [
        metric_card("買い候補TOP100", f"{len(top100)}件", "#111827"),
        metric_card("新規ランクイン", f"{summary.get('新規ランクイン', 0)}件", "#16a34a"),
        metric_card("急上昇", f"{summary.get('急上昇', 0)}件", "#ea580c"),
        metric_card("TOP30継続10日以上", f"{summary.get('TOP30継続10日以上', 0)}件", "#7c3aed"),
        metric_card("年初来高値更新", f"{summary.get('年初来高値更新', 0)}件", "#2563eb"),
        metric_card("取得失敗", f"{summary.get('取得失敗', 0)}件", "#dc2626" if summary.get('取得失敗', 0) else "#16a34a"),
    ]
    return f'''<!doctype html><html><body style="margin:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans','Yu Gothic',Meiryo,Arial,sans-serif;color:#111827"><div style="max-width:720px;margin:0 auto;padding:16px"><div style="background:#0f172a;color:#fff;border-radius:20px;padding:20px"><div style="font-size:13px;color:#cbd5e1">モメンタムチンパン ダッシュボード</div><div style="font-size:24px;font-weight:900">{html_text(summary.get('実行日', ''))}</div><div style="margin-top:8px;color:#e2e8f0">売買指示ではなく、モメンタム確認用の自動スクリーニングです。</div></div><table width="100%" style="margin-top:12px;border-collapse:collapse"><tr>{cards[0]}{cards[1]}</tr><tr>{cards[2]}{cards[3]}</tr><tr>{cards[4]}{cards[5]}</tr></table><div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>Market Temperature</b><div style="font-size:13px;line-height:1.8;color:#334155">YTD高値 {fmt_int(temp.get('ytd_high_count'))} ({fmt_delta(temp.get('delta_ytd_high_count'), 0)}) / Top100平均スコア {fmt_num(temp.get('top100_avg_score'), 2)} ({fmt_delta(temp.get('delta_top100_avg_score'), 2)})<br>Top100平均20日騰落率 {fmt_pct(temp.get('top100_avg_return_20d'))} ({fmt_delta(temp.get('delta_top100_avg_return_20d'), 4)}) / Top100平均出来高倍率 {fmt_num(temp.get('top100_avg_volume_ratio'), 2)} ({fmt_delta(temp.get('delta_top100_avg_volume_ratio'), 2)})</div></div><h2>Momentum Top{top_n}</h2>{html_ranking_cards(top100, top_n)}<h2>新規ランクイン 上位5件</h2>{html_ranking_cards(new_entries, 5)}<h2>急上昇 上位5件</h2>{html_ranking_cards(rising_fast, 5)}<h2>TOP30継続ランキング 上位5件</h2>{html_ranking_cards(top30_streak, 5)}<div style="font-size:12px;color:#64748b;line-height:1.7;margin-top:16px">{html_text(DISCLAIMER)}</div></div></body></html>'''


def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> None:
    load_dotenv()
    sender, to, pw = os.getenv("EMAIL_FROM"), os.getenv("EMAIL_TO"), os.getenv("EMAIL_APP_PASSWORD")
    if not sender or not to or not pw:
        logger.info("Email secrets are not set; skip email")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"【モメンタムチンパン】{summary['実行日']} 引け後レポート"
    msg["From"], msg["To"] = sender, to
    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, temperature, cfg), "plain", "utf-8"))
    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, temperature, cfg), "html", "utf-8"))
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

    rows = []
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
            if m["price_date"] != today:
                logger.info("%s latest price date is %s (not today %s)", st.code, m["price_date"], today)
            sc, reason, score_breakdown = score(m, cfg["market"]["min_trading_value"])
            success += 1
            row = {"code": st.code, "name": st.name, "score": sc, "reason": reason, **score_breakdown, **m}
            if m["close"] >= cfg["market"].get("min_price", 0):
                rows.append(row)
        except Exception as exc:
            if STOP_REQUESTED:
                logger.warning("Scan interrupted while processing %s; stopping", st.code)
                errors.append(error_entry(st.code, st.name, f"interrupted: {exc}", "fetch_price", recoverable=True))
                break
            logger.exception("Failed processing %s", st.code)
            errors.append(error_entry(st.code, st.name, str(exc), "fetch_price", recoverable=True))

    all_df = pd.DataFrame(rows)
    top_limit = int(cfg["ranking"]["buy_candidate_limit"])
    if not all_df.empty:
        base_all = all_df.sort_values(["score", "return_20d", "volume_ratio"], ascending=[False, False, False]).copy()
    else:
        base_all = pd.DataFrame(columns=ranking_history_columns())
    history = load_ranking_history(cfg["data"]["ranking_history_path"])
    all_ranked = enrich_ranking_features(base_all, history, today, top_limit) if not base_all.empty else pd.DataFrame(columns=ranking_history_columns())
    if not all_ranked.empty:
        cols = [c for c in ranking_history_columns() if c in all_ranked.columns] + [c for c in all_ranked.columns if c not in ranking_history_columns()]
        all_ranked = all_ranked[cols]
    write_ranking_history(all_ranked, cfg["data"]["ranking_history_path"])

    top100 = all_ranked[all_ranked["rank"] <= top_limit].copy() if not all_ranked.empty else pd.DataFrame(columns=ranking_history_columns())
    new_entries = top100[top100["is_new_entry"] == True].copy() if not top100.empty else top100.copy()
    rising_fast = top100[top100["is_rising_fast"] == True].copy() if not top100.empty else top100.copy()
    top30_streak = top100[top100["top30_streak"] > 0].sort_values(["top30_streak", "rank"], ascending=[False, True]).copy() if not top100.empty else top100.copy()
    top30_streak_10 = top100[top100["top30_streak"] >= 10].copy() if not top100.empty else top100.copy()
    ytd_high_ranking = all_ranked[all_ranked["ytd_high_flag"] == True].sort_values(["ytd_high_streak", "ytd_high_count", "score"], ascending=[False, False, False]).copy() if not all_ranked.empty else all_ranked.copy()

    temp_path = cfg["data"]["market_temperature_path"]
    old_temp = pd.read_csv(temp_path) if Path(temp_path).exists() else pd.DataFrame()
    temperature = market_temperature(today, all_ranked, top100, old_temp)
    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)

    elapsed = round(perf_counter() - started_at, 1)
    limited_mode = max_symbols > 0 and max_symbols < full_universe_count
    universe_df = pd.DataFrame([{"code": st.code, "name": st.name, "market": st.market, "scan_mode": "verification_limited" if limited_mode else "full"} for st in stocks])
    summary = {
        "実行日": today,
        "アプリ版": APP_VERSION,
        "レポート形式": "dashboard_full_history_v1",
        "JPX上場銘柄数": universe_stats.get("jpx_listed_count", 0),
        "通常株ユニバース数": full_universe_count,
        "除外銘柄数": universe_stats.get("excluded_count", 0),
        "実スキャン対象銘柄数": len(stocks),
        "取得成功": success,
        "取得失敗": len(errors),
        "年初来高値更新": int(all_ranked.get("ytd_high_flag", pd.Series(dtype=bool)).fillna(False).sum()) if not all_ranked.empty else 0,
        "Momentum Top100": len(top100),
        "新規ランクイン": len(new_entries),
        "急上昇": len(rising_fast),
        "過去最高順位更新": int(top100.get("is_best_rank", pd.Series(dtype=bool)).fillna(False).sum()) if not top100.empty else 0,
        "TOP30継続銘柄": len(top30_streak),
        "TOP30継続10日以上": len(top30_streak_10),
        "検証モード": "YES" if limited_mode else "NO",
        "銘柄数制限": max_symbols if max_symbols > 0 else "なし",
        "処理時間秒": elapsed,
        "注意事項": DISCLAIMER,
    }
    excel_report(cfg["data"]["output_path"], summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, errors, universe_df)
    backup_error_artifacts(errors, cfg, cfg["data"]["output_path"])
    try:
        send_email(summary, top100, new_entries, rising_fast, top30_streak, temperature, cfg)
    except Exception as exc:
        logger.exception("Email sending failed: %s", exc)


if __name__ == "__main__":
    main()
