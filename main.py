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

APP_VERSION = "2026-07-10-dashboard-regime-history-v6"
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
    ranked["is_rising_fast"] = ranked["is_top100"] & ~ranked["is_new_entry"] & (ranked["rank_change"].fillna(0) >= 20)
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


def fmt_pct_point(value: Any) -> str:
    """Format a decimal return difference as percentage points."""
    if value is None or pd.isna(value):
        return "-"
    number = float(value) * 100
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}pt"


def fmt_price(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.0f}円"


def fmt_trading_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    number = float(value)
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}億円"
    if number >= 10_000:
        return f"{number / 10_000:.0f}万円"
    return f"{number:,.0f}円"


def fmt_rank_change(value: Any) -> str:
    """Return only meaningful rank movement; hide no-history and unchanged rows."""
    if value is None or pd.isna(value):
        return ""
    number = int(float(value))
    if number > 0:
        return f"前回比 +{number}位"
    if number < 0:
        return f"前回比 {number}位"
    return ""


def compact_reason(reason: Any) -> str:
    """Keep qualitative conditions while removing numbers already shown above."""
    raw = str(reason or "").strip()
    if not raw:
        return ""
    items = [item.strip() for item in raw.replace("/", "、").split("、") if item.strip()]
    numeric_prefixes = ("連続更新", "20日騰落率", "出来高倍率")
    filtered = [item for item in items if not item.startswith(numeric_prefixes)]
    return " / ".join(filtered)


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


def score_breakdown_text(r: pd.Series) -> str:
    parts = [
        f"高値{fmt_int(r.get('score_ytd_high'))}",
        f"連続{fmt_int(r.get('score_ytd_streak'))}",
        f"20日{fmt_int(r.get('score_return_20d'))}",
        f"出来高{fmt_int(r.get('score_volume_ratio'))}",
        f"MA{fmt_int(r.get('score_ma'))}",
        f"売買{fmt_int(r.get('score_trading_value'))}",
    ]
    return " / ".join(parts)


def latest_price_date(df: pd.DataFrame) -> str:
    if df.empty or "price_date" not in df.columns:
        return "-"
    dates = pd.to_datetime(df["price_date"], errors="coerce").dropna()
    if dates.empty:
        return "-"
    return dates.max().date().isoformat()


def reading_summary(summary: dict[str, Any]) -> str:
    new_count = int(summary.get("新規ランクイン", 0) or 0)
    rising_count = int(summary.get("急上昇", 0) or 0)
    streak10 = int(summary.get("TOP30継続10日以上", 0) or 0)
    ytd_count = int(summary.get("年初来高値更新", 0) or 0)
    notes = []
    if new_count:
        notes.append(f"新規Top100入りが{new_count}件あり、資金流入の新しい候補を確認する日です。")
    if rising_count:
        notes.append(f"前回もTop100だった銘柄のうち、20位以上の急上昇が{rising_count}件あります。")
    if streak10:
        notes.append(f"TOP30を10日以上維持する銘柄が{streak10}件あり、継続トレンドが残っています。")
    if ytd_count >= 100:
        notes.append(f"年初来高値更新が{ytd_count}件あり、市場全体のモメンタムは強めです。")
    if not notes:
        notes.append("新規・急上昇よりも既存上位銘柄の継続確認が中心です。")
    return " ".join(notes)


def ranking_badges(r: pd.Series) -> list[str]:
    badges = []
    if bool(r.get("is_new_entry")):
        badges.append("NEW")
    if bool(r.get("is_rising_fast")):
        badges.append(f"急上昇 +{fmt_int(r.get('rank_change'))}")
    if bool(r.get("is_best_rank")):
        badges.append("最高順位")
    streak = int(r.get("top30_streak", r.get("top30_streak_days", 0)) or 0)
    if streak >= 3:
        badges.append(f"TOP30 {streak}日")
    return badges


def metric_highlight_specs() -> list[tuple[str, str, str]]:
    return [
        ("20日騰落率 上位5", "return_20d", "20日"),
        ("出来高倍率 上位5", "volume_ratio", "出来高"),
        ("売買代金 上位5", "trading_value", "売買代金"),
        ("YTD更新回数 上位5", "ytd_high_count", "YTD更新"),
        ("60日騰落率 上位5", "return_60d", "60日"),
    ]


def metric_top(df: pd.DataFrame, column: str, limit: int = 5) -> pd.DataFrame:
    if df.empty or column not in df.columns:
        return pd.DataFrame(columns=df.columns)
    work = df[df[column].notna()].copy()
    if work.empty:
        return work
    return work.sort_values([column, "score", "rank"], ascending=[False, False, True]).head(limit)


def format_metric_value(column: str, value: Any) -> str:
    if column in {"return_20d", "return_60d"}:
        return fmt_pct(value)
    if column == "volume_ratio":
        return f"{fmt_num(value)}倍"
    if column == "trading_value":
        return fmt_trading_value(value)
    if column == "ytd_high_count":
        return f"{fmt_int(value)}回"
    return fmt_num(value)


def metric_detail_parts(r: pd.Series, target_column: str) -> list[str]:
    """Return common highlight metrics without repeating the target metric."""
    parts: list[str] = []
    if target_column != "return_20d":
        parts.append(f"20日 {fmt_pct(r.get('return_20d'))}")
    if target_column != "volume_ratio":
        parts.append(f"出来高 {fmt_num(r.get('volume_ratio'))}倍")
    if target_column != "trading_value":
        parts.append(f"売買代金 {fmt_trading_value(r.get('trading_value'))}")
    return parts


def plain_metric_highlights(top100: pd.DataFrame) -> list[str]:
    lines = ["【指標別ハイライト】"]
    for title, column, label in metric_highlight_specs():
        ranked = metric_top(top100, column)
        if ranked.empty:
            continue
        lines.append(f"■ {title}")
        for _, r in ranked.iterrows():
            details = "｜".join(metric_detail_parts(r, column))
            suffix = f"｜{details}" if details else ""
            lines.append(
                f"#{int(r['rank'])} {r['code']} {r['name']}｜{int(r['score'])}点｜"
                f"{label} {format_metric_value(column, r.get(column))}{suffix}"
            )
        lines.append("")
    return lines


def html_metric_highlights(top100: pd.DataFrame) -> str:
    groups = []
    for title, column, label in metric_highlight_specs():
        ranked = metric_top(top100, column)
        if ranked.empty:
            continue
        rows = []
        for _, r in ranked.iterrows():
            details = " ・ ".join(metric_detail_parts(r, column))
            suffix = f" ・ {html_text(details)}" if details else ""
            rows.append(
                f'''<div style="border-top:1px solid #e5e7eb;padding:10px 0">
<div style="font-size:14px;font-weight:800;color:#0f172a">#{int(r["rank"])} {html_text(r["code"])} {html_text(r["name"])} <span style="color:{score_color(r["score"])}">{int(r["score"])}点</span></div>
<div style="font-size:12px;line-height:1.7;color:#475569">{html_text(label)} <b>{html_text(format_metric_value(column, r.get(column)))}</b>{suffix}</div>
</div>'''
            )
        groups.append(
            f'''<div style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:14px;margin-top:10px">
<div style="font-size:15px;font-weight:900;color:#111827">{html_text(title)}</div>{"".join(rows)}
</div>'''
        )
    if not groups:
        return ""
    return f'<h2 style="margin-top:22px">指標別ハイライト</h2>{"".join(groups)}'


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


def plain_ranking_section(title: str, df: pd.DataFrame, limit: int = 5, show_empty: bool = False) -> list[str]:
    if df.empty and not show_empty:
        return []
    lines = [f"【{title}】"]
    if df.empty:
        return lines + ["該当なし", ""]
    for _, r in df.head(limit).iterrows():
        badges = " / ".join(ranking_badges(r))
        rank_change = fmt_rank_change(r.get("rank_change"))
        first_meta = [f"終値 {fmt_price(r.get('close'))}"]
        if rank_change:
            first_meta.append(rank_change)
        first_meta.append(f"売買代金 {fmt_trading_value(r.get('trading_value'))}")
        reason = compact_reason(r.get("reason"))
        lines += [
            f"{int(r['rank'])}. {r['code']} {r['name']} {int(r['score'])}点" + (f" {badges}" if badges else ""),
            "   " + " / ".join(first_meta),
            f"   5日 {fmt_pct(r.get('return_5d'))} / 20日 {fmt_pct(r.get('return_20d'))} / 60日 {fmt_pct(r.get('return_60d'))} / 出来高 {fmt_num(r.get('volume_ratio'))}倍",
            f"   YTD更新 {fmt_int(r.get('ytd_high_count'))}回 / 連続 {fmt_int(r.get('ytd_high_streak'))}日 / スコア内訳 {score_breakdown_text(r)}",
        ]
        if reason:
            lines.append(f"   条件 {reason}")
        lines.append("")
    return lines


def html_ranking_cards(df: pd.DataFrame, limit: int = 5, show_empty: bool = False) -> str:
    if df.empty and not show_empty:
        return ""
    if df.empty:
        return '<div style="color:#64748b">該当なし</div>'
    items = []
    for _, r in df.head(limit).iterrows():
        badge_html = "".join(
            f'<span style="display:inline-block;margin:2px;padding:3px 8px;border-radius:999px;background:#e0f2fe;color:#075985;font-size:12px;font-weight:700">{html_text(b)}</span>'
            for b in ranking_badges(r)
        )
        rank_change = fmt_rank_change(r.get("rank_change"))
        rank_html = f" ・ {html_text(rank_change)}" if rank_change else ""
        reason = compact_reason(r.get("reason"))
        reason_html = f'<div style="font-size:13px;color:#64748b;margin-top:6px">{html_text(reason)}</div>' if reason else ""
        badges_block = f'<div style="clear:both;margin-top:8px">{badge_html}</div>' if badge_html else '<div style="clear:both"></div>'
        items.append(f'''<div style="border:1px solid #e5e7eb;border-radius:16px;padding:14px;margin:10px 0;background:#fff">
<div><b>#{int(r["rank"])} {html_text(r["code"])} {html_text(r["name"])}</b><span style="float:right;background:{score_color(r["score"])};color:#fff;border-radius:999px;padding:4px 10px;font-weight:800">{int(r["score"])}点</span></div>
{badges_block}
<div style="font-size:13px;color:#334155;line-height:1.7;margin-top:8px">終値 <b>{fmt_price(r.get("close"))}</b>{rank_html} ・ 売買代金 <b>{fmt_trading_value(r.get("trading_value"))}</b></div>
<div style="font-size:13px;color:#334155;line-height:1.7">5日 <b>{fmt_pct(r.get("return_5d"))}</b> ・ 20日 <b>{fmt_pct(r.get("return_20d"))}</b> ・ 60日 <b>{fmt_pct(r.get("return_60d"))}</b> ・ 出来高 <b>{fmt_num(r.get("volume_ratio"))}倍</b></div>
<div style="font-size:13px;color:#334155;line-height:1.7">YTD更新 <b>{fmt_int(r.get("ytd_high_count"))}回</b> ・ 連続 <b>{fmt_int(r.get("ytd_high_streak"))}日</b> ・ {html_text(score_breakdown_text(r))}</div>
{reason_html}
</div>''')
    return "".join(items)


def compact_rank_slice(top100: pd.DataFrame, start_rank: int, end_rank: int) -> pd.DataFrame:
    """Return a rank range for compact email display."""
    if top100.empty or "rank" not in top100.columns or start_rank > end_rank:
        return pd.DataFrame(columns=top100.columns)
    work = top100[(top100["rank"] >= start_rank) & (top100["rank"] <= end_rank)].copy()
    return work.sort_values("rank")


def compact_signal_text(r: pd.Series) -> str:
    signals: list[str] = []
    if row_flag(r, "is_new_entry"):
        signals.append("NEW")
    if row_flag(r, "is_rising_fast"):
        signals.append(f"急上昇 +{fmt_int(r.get('rank_change'))}")
    elif fmt_rank_change(r.get("rank_change")):
        signals.append(fmt_rank_change(r.get("rank_change")))
    if row_flag(r, "is_best_rank"):
        signals.append("最高順位")
    streak = int(row_number(r, "top30_streak", row_number(r, "top30_streak_days")))
    if streak >= 3:
        signals.append(f"TOP30 {streak}日")
    return " / ".join(signals)


def plain_compact_ranking_section(title: str, df: pd.DataFrame) -> list[str]:
    if df.empty:
        return []
    lines = [f"【{title}】"]
    for _, r in df.iterrows():
        signal = compact_signal_text(r)
        suffix = f"｜{signal}" if signal else ""
        lines.append(
            f"#{int(r['rank'])} {r['code']} {r['name']}｜{int(r['score'])}点｜"
            f"5日 {fmt_pct(r.get('return_5d'))}｜20日 {fmt_pct(r.get('return_20d'))}｜"
            f"出来高 {fmt_num(r.get('volume_ratio'))}倍｜売買代金 {fmt_trading_value(r.get('trading_value'))}{suffix}"
        )
    lines.append("")
    return lines


def html_compact_ranking_section(title: str, df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    rows = []
    for _, r in df.iterrows():
        signal = compact_signal_text(r)
        signal_html = (
            f'<div style="font-size:11px;color:#0369a1;margin-top:3px;font-weight:700">{html_text(signal)}</div>'
            if signal else ""
        )
        rows.append(
            f"""<div style="border-top:1px solid #e5e7eb;padding:9px 0">
<div style="font-size:13px;font-weight:800;color:#0f172a">#{int(r["rank"])} {html_text(r["code"])} {html_text(r["name"])} <span style="float:right;color:{score_color(r["score"])}">{int(r["score"])}点</span></div>
<div style="clear:both;font-size:11px;line-height:1.7;color:#475569">5日 {fmt_pct(r.get("return_5d"))} ・ 20日 {fmt_pct(r.get("return_20d"))} ・ 出来高 {fmt_num(r.get("volume_ratio"))}倍 ・ 売買代金 {fmt_trading_value(r.get("trading_value"))}</div>
{signal_html}
</div>"""
        )
    return f"""<h2 style="margin-top:22px">{html_text(title)}</h2>
<div style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:6px 14px">{"".join(rows)}</div>"""


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


def optional_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text_value = str(value).strip()
    return "" if text_value.lower() in {"", "nan", "none"} else text_value


def latest_previous_regime(history: pd.DataFrame, today: str) -> dict[str, Any]:
    if history is None or history.empty or "date" not in history.columns or "market_regime" not in history.columns:
        return {}
    work = history.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date_sort"])
    work = work[work["date"].astype(str) != str(today)]
    work["regime_text"] = work["market_regime"].map(optional_text)
    work = work[work["regime_text"] != ""].sort_values("date_sort")
    if work.empty:
        return {}
    row = work.iloc[-1]
    score_value = pd.to_numeric(pd.Series([row.get("market_regime_score")]), errors="coerce").iloc[0]
    return {
        "date": str(row.get("date", "")),
        "label": optional_text(row.get("market_regime")),
        "score": None if pd.isna(score_value) else int(float(score_value)),
    }


def market_regime_transition_type(previous_label: str, current_label: str) -> str:
    if not previous_label:
        return "履歴開始"
    if previous_label == current_label:
        return "維持"
    if current_label == "過熱警戒":
        return "警戒強化"
    if previous_label == "過熱警戒" and current_label != "過熱警戒":
        return "過熱緩和"
    order = {"弱気": 0, "中立": 1, "やや強気": 2, "強気": 3}
    previous_rank = order.get(previous_label)
    current_rank = order.get(current_label)
    if previous_rank is None or current_rank is None:
        return "転換"
    if current_rank > previous_rank:
        return "改善"
    if current_rank < previous_rank:
        return "悪化"
    return "転換"


def market_regime_streak(history: pd.DataFrame, today: str, current_label: str) -> int:
    if history is None or history.empty or "date" not in history.columns or "market_regime" not in history.columns:
        return 1
    work = history.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date_sort"])
    work = work[work["date"].astype(str) != str(today)].sort_values("date_sort", ascending=False)
    streak = 1
    for _, row in work.iterrows():
        label = optional_text(row.get("market_regime"))
        if not label:
            continue
        if label != current_label:
            break
        streak += 1
    return streak


def attach_market_regime_history(today: str, temperature: pd.DataFrame, regime: dict[str, Any], history: pd.DataFrame) -> pd.DataFrame:
    current = temperature.copy()
    previous = latest_previous_regime(history, today)
    previous_label = previous.get("label", "")
    previous_score = previous.get("score")
    current_label = regime["label"]
    current_score = int(regime["score"])
    changed = bool(previous_label and previous_label != current_label)
    transition_type = market_regime_transition_type(previous_label, current_label)
    transition = f"{previous_label} → {current_label}" if previous_label else f"履歴開始 → {current_label}"
    score_delta = None if previous_score is None else current_score - int(previous_score)
    streak = market_regime_streak(history, today, current_label)

    current["market_regime"] = current_label
    current["market_regime_base"] = regime.get("base_label", current_label)
    current["market_regime_score"] = current_score
    current["market_regime_ma20_ratio"] = regime.get("ma20_ratio", 0.0)
    current["market_regime_ma60_ratio"] = regime.get("ma60_ratio", 0.0)
    current["market_regime_overheat_count"] = regime.get("overheat_count", 0)
    current["market_regime_overheat_ratio"] = regime.get("overheat_ratio", 0.0)
    current["previous_market_regime"] = previous_label
    current["previous_market_regime_score"] = previous_score
    current["previous_market_regime_date"] = previous.get("date", "")
    current["regime_changed"] = changed
    current["regime_transition"] = transition
    current["regime_transition_type"] = transition_type
    current["regime_score_delta"] = score_delta
    current["regime_streak"] = streak
    return current


def enrich_regime_from_temperature(regime: dict[str, Any], temperature: pd.DataFrame) -> dict[str, Any]:
    enriched = dict(regime)
    if temperature is None or temperature.empty:
        return enriched
    row = temperature.iloc[0]
    previous_score_value = pd.to_numeric(pd.Series([row.get("previous_market_regime_score")]), errors="coerce").iloc[0]
    score_delta_value = pd.to_numeric(pd.Series([row.get("regime_score_delta")]), errors="coerce").iloc[0]
    streak_value = pd.to_numeric(pd.Series([row.get("regime_streak")]), errors="coerce").iloc[0]
    enriched.update({
        "previous_label": optional_text(row.get("previous_market_regime")),
        "previous_score": None if pd.isna(previous_score_value) else int(float(previous_score_value)),
        "previous_date": optional_text(row.get("previous_market_regime_date")),
        "changed": str(row.get("regime_changed", "")).lower() in {"true", "1"} if not isinstance(row.get("regime_changed"), bool) else bool(row.get("regime_changed")),
        "transition": optional_text(row.get("regime_transition")),
        "transition_type": optional_text(row.get("regime_transition_type")),
        "score_delta": None if pd.isna(score_delta_value) else int(float(score_delta_value)),
        "streak": 1 if pd.isna(streak_value) else int(float(streak_value)),
    })
    return enriched


def regime_history_text(regime: dict[str, Any]) -> str:
    previous_label = regime.get("previous_label", "")
    score_delta = regime.get("score_delta")
    delta_text = "" if score_delta is None else f" / スコア前回比 {score_delta:+d}点"
    if not previous_label:
        return f"履歴: 本日から判定履歴を開始（{regime['label']}）"
    if regime.get("changed"):
        return f"転換: {regime.get('transition', '')}（{regime.get('transition_type', '転換')}）{delta_text}"
    return f"継続: {regime['label']}を{regime.get('streak', 1)}営業日維持{delta_text}"


def plain_market_regime(regime: dict[str, Any]) -> list[str]:
    return [
        "【Market Regime】",
        f"判定: {regime['label']} / 市場環境スコア {regime['score']}点",
        regime_history_text(regime),
        f"20日線上 {regime['ma20_ratio']:.1%} / 60日線上 {regime['ma60_ratio']:.1%} / 過熱銘柄 {regime['overheat_count']}件 ({regime['overheat_ratio']:.1%})",
        f"方針: {regime['guidance']}",
        "",
    ]


def html_market_regime(regime: dict[str, Any]) -> str:
    transition_color = "#b91c1c" if regime.get("transition_type") in {"悪化", "警戒強化"} else "#15803d" if regime.get("transition_type") in {"改善", "過熱緩和"} else "#475569"
    return f"""<div style="background:{regime['background']};border:2px solid {regime['color']};border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:12px;font-weight:800;color:{regime['color']}">MARKET REGIME</div>
<div style="font-size:24px;font-weight:900;color:{regime['color']};margin-top:2px">{html_text(regime['label'])} <span style="font-size:16px">{regime['score']}点</span></div>
<div style="font-size:12px;font-weight:800;color:{transition_color};margin-top:6px">{html_text(regime_history_text(regime))}</div>
<div style="font-size:12px;line-height:1.8;color:#334155;margin-top:8px">20日線上 <b>{regime['ma20_ratio']:.1%}</b> ・ 60日線上 <b>{regime['ma60_ratio']:.1%}</b> ・ 過熱銘柄 <b>{regime['overheat_count']}件 ({regime['overheat_ratio']:.1%})</b></div>
<div style="font-size:13px;line-height:1.8;color:#334155;margin-top:6px"><b>本日の方針:</b> {html_text(regime['guidance'])}</div>
</div>"""


def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:
    top_n = cfg["ranking"]["email_top_n"]
    temp = {} if temperature.empty else temperature.iloc[0].to_dict()
    long_streak = top30_streak[top30_streak["top30_streak"] >= 10].copy() if not top30_streak.empty and "top30_streak" in top30_streak.columns else pd.DataFrame()
    price_date = latest_price_date(top100)
    priority = select_priority_candidates(top100, 10)
    compact_ranked = compact_rank_slice(top100, top_n + 1, 30)
    regime = enrich_regime_from_temperature(calculate_market_regime(top100, temperature), temperature)
    lines = [
        "本日のモメンタム・ダッシュボードです。",
        "",
        "【まず見るポイント】",
        f"レポート日: {summary.get('実行日', '-')}",
        f"株価データ日: {price_date}",
        f"買い候補TOP100: {len(top100)}件",
        f"今日の重点候補: {len(priority)}件",
        f"新規ランクイン: {summary.get('新規ランクイン', 0)}件",
        f"急上昇: {summary.get('急上昇', 0)}件",
        f"TOP30継続10日以上: {summary.get('TOP30継続10日以上', 0)}件",
        f"年初来高値更新: {summary.get('年初来高値更新', 0)}件",
        f"取得失敗: {summary.get('取得失敗', 0)}件",
        "",
        "【今日の読み方】",
        reading_summary(summary),
        "",
        "【Market Temperature】",
        f"YTD高値 {fmt_int(temp.get('ytd_high_count'))} ({fmt_delta(temp.get('delta_ytd_high_count'), 0)}) / Top100平均スコア {fmt_num(temp.get('top100_avg_score'), 2)} ({fmt_delta(temp.get('delta_top100_avg_score'), 2)})",
        f"Top100平均20日騰落率 {fmt_pct(temp.get('top100_avg_return_20d'))} (前回比 {fmt_pct_point(temp.get('delta_top100_avg_return_20d'))}) / Top100平均出来高倍率 {fmt_num(temp.get('top100_avg_volume_ratio'), 2)} ({fmt_delta(temp.get('delta_top100_avg_volume_ratio'), 2)})",
        "",
    ]
    lines += plain_market_regime(regime)
    lines += plain_priority_section(priority)
    lines += plain_metric_highlights(top100)
    lines += plain_ranking_section("年初来高値更新ランキング 上位10件", ytd_high_ranking, 10)
    lines += plain_ranking_section("新規ランクイン 上位10件", new_entries, 10)
    lines += plain_ranking_section("急上昇 上位10件", rising_fast, 10)
    lines += plain_ranking_section("TOP30継続10日以上 上位10件", long_streak, 10)
    lines += plain_ranking_section(f"Momentum Top{top_n}（詳細）", top100, top_n, show_empty=True)
    lines += plain_compact_ranking_section(f"Momentum {top_n + 1}-30（コンパクト）", compact_ranked)
    lines += ["【詳細】GitHub Actions artifact の daily_report.xlsx を確認してください。", "", DISCLAIMER]
    return "\n".join(lines)


def metric_card(label: str, value: str, color: str = "#111827") -> str:
    return f'<td style="width:50%;padding:6px"><div style="border:1px solid #e5e7eb;border-radius:14px;padding:12px;background:#fff"><div style="font-size:12px;color:#64748b">{html_text(label)}</div><div style="font-size:22px;font-weight:800;color:{color}">{html_text(value)}</div></div></td>'


def html_section(title: str, df: pd.DataFrame, limit: int, show_empty: bool = False) -> str:
    cards = html_ranking_cards(df, limit, show_empty=show_empty)
    if not cards:
        return ""
    return f"<h2>{html_text(title)}</h2>{cards}"


def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:
    top_n = cfg["ranking"]["email_top_n"]
    temp = {} if temperature.empty else temperature.iloc[0].to_dict()
    long_streak = top30_streak[top30_streak["top30_streak"] >= 10].copy() if not top30_streak.empty and "top30_streak" in top30_streak.columns else pd.DataFrame()
    price_date = latest_price_date(top100)
    priority = select_priority_candidates(top100, 10)
    compact_ranked = compact_rank_slice(top100, top_n + 1, 30)
    regime = enrich_regime_from_temperature(calculate_market_regime(top100, temperature), temperature)
    cards = [
        metric_card("買い候補TOP100", f"{len(top100)}件", "#111827"),
        metric_card("新規ランクイン", f"{summary.get('新規ランクイン', 0)}件", "#16a34a"),
        metric_card("急上昇", f"{summary.get('急上昇', 0)}件", "#ea580c"),
        metric_card("TOP30継続10日以上", f"{summary.get('TOP30継続10日以上', 0)}件", "#7c3aed"),
        metric_card("年初来高値更新", f"{summary.get('年初来高値更新', 0)}件", "#2563eb"),
        metric_card("取得失敗", f"{summary.get('取得失敗', 0)}件", "#dc2626" if summary.get('取得失敗', 0) else "#16a34a"),
    ]
    sections = "".join([
        html_market_regime(regime),
        html_priority_section(priority),
        html_metric_highlights(top100),
        html_section("年初来高値更新ランキング 上位10件", ytd_high_ranking, 10),
        html_section("新規ランクイン 上位10件", new_entries, 10),
        html_section("急上昇 上位10件", rising_fast, 10),
        html_section("TOP30継続10日以上 上位10件", long_streak, 10),
        html_section(f"Momentum Top{top_n}（詳細）", top100, top_n, show_empty=True),
        html_compact_ranking_section(f"Momentum {top_n + 1}-30（コンパクト）", compact_ranked),
    ])
    return f'''<!doctype html><html><body style="margin:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans','Yu Gothic',Meiryo,Arial,sans-serif;color:#111827"><div style="max-width:720px;margin:0 auto;padding:16px"><div style="background:#0f172a;color:#fff;border-radius:20px;padding:20px"><div style="font-size:13px;color:#cbd5e1">モメンタムチンパン ダッシュボード</div><div style="font-size:24px;font-weight:900">{html_text(summary.get('実行日', ''))}</div><div style="margin-top:8px;color:#e2e8f0">株価データ日: {html_text(price_date)} / 売買指示ではなく、モメンタム確認用の自動スクリーニングです。</div></div><table width="100%" style="margin-top:12px;border-collapse:collapse"><tr>{cards[0]}{cards[1]}</tr><tr>{cards[2]}{cards[3]}</tr><tr>{cards[4]}{cards[5]}</tr></table><div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>今日の読み方</b><div style="font-size:13px;line-height:1.8;color:#334155">{html_text(reading_summary(summary))}</div></div><div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>Market Temperature</b><div style="font-size:13px;line-height:1.8;color:#334155">YTD高値 {fmt_int(temp.get('ytd_high_count'))} ({fmt_delta(temp.get('delta_ytd_high_count'), 0)}) / Top100平均スコア {fmt_num(temp.get('top100_avg_score'), 2)} ({fmt_delta(temp.get('delta_top100_avg_score'), 2)})<br>Top100平均20日騰落率 {fmt_pct(temp.get('top100_avg_return_20d'))}（前回比 {fmt_pct_point(temp.get('delta_top100_avg_return_20d'))}） / Top100平均出来高倍率 {fmt_num(temp.get('top100_avg_volume_ratio'), 2)} ({fmt_delta(temp.get('delta_top100_avg_volume_ratio'), 2)})</div></div>{sections}<div style="font-size:12px;color:#64748b;line-height:1.7;margin-top:16px">詳細はGitHub Actions artifactのdaily_report.xlsxを確認してください。<br>{html_text(DISCLAIMER)}</div></div></body></html>'''


def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> None:
    load_dotenv()
    sender, to, pw = os.getenv("EMAIL_FROM"), os.getenv("EMAIL_TO"), os.getenv("EMAIL_APP_PASSWORD")
    if not sender or not to or not pw:
        logger.info("Email secrets are not set; skip email")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"【モメンタムチンパン】{summary['実行日']} 引け後レポート"
    msg["From"], msg["To"] = sender, to
    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, cfg), "plain", "utf-8"))
    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, cfg), "html", "utf-8"))
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
    regime = calculate_market_regime(top100, temperature)
    temperature = attach_market_regime_history(today, temperature, regime, old_temp)
    regime = enrich_regime_from_temperature(regime, temperature)
    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)

    elapsed = round(perf_counter() - started_at, 1)
    limited_mode = max_symbols > 0 and max_symbols < full_universe_count
    universe_df = pd.DataFrame([{"code": st.code, "name": st.name, "market": st.market, "scan_mode": "verification_limited" if limited_mode else "full"} for st in stocks])
    summary = {
        "実行日": today,
        "アプリ版": APP_VERSION,
        "レポート形式": "dashboard_regime_history_v6",
        "株価データ日": latest_price_date(top100),
        "JPX上場銘柄数": universe_stats.get("jpx_listed_count", 0),
        "通常株ユニバース数": full_universe_count,
        "除外銘柄数": universe_stats.get("excluded_count", 0),
        "実スキャン対象銘柄数": len(stocks),
        "取得成功": success,
        "取得失敗": len(errors),
        "年初来高値更新": int(all_ranked.get("ytd_high_flag", pd.Series(dtype=bool)).fillna(False).sum()) if not all_ranked.empty else 0,
        "Momentum Top100": len(top100),
        "Market Regime": regime["label"],
        "Market Regime Score": regime["score"],
        "前回Market Regime": regime.get("previous_label", ""),
        "Market Regime転換": regime.get("transition", ""),
        "Market Regime転換種別": regime.get("transition_type", ""),
        "Market Regime転換有無": regime.get("changed", False),
        "Market Regime継続日数": regime.get("streak", 1),
        "Market Regime Score前回比": regime.get("score_delta"),
        "Top100 20日線上比率": regime["ma20_ratio"],
        "Top100 60日線上比率": regime["ma60_ratio"],
        "Top100 過熱銘柄数": regime["overheat_count"],
        "Top100 過熱銘柄比率": regime["overheat_ratio"],
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
        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, cfg)
    except Exception as exc:
        logger.exception("Email sending failed: %s", exc)


if __name__ == "__main__":
    main()
