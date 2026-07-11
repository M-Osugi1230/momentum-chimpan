"""Momentum Chimpan: Japanese stock momentum screener.

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。
特定銘柄の売買を推奨するものではありません。
最終的な投資判断は利用者自身の責任で行ってください。
"""
from __future__ import annotations

import hashlib
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
import relative_strength_lifecycle as rs_lifecycle
import yfinance as yf
import yaml
from dotenv import load_dotenv

APP_VERSION = "2026-07-11-dashboard-relative-strength-lifecycle-v19"
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
    sector33: str = ""


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
    sector_col = next((c for c in df.columns if "33業種区分" in str(c)), None)
    if sector_col is None:
        sector_col = next((c for c in df.columns if "33業種" in str(c) and "コード" not in str(c)), None)
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
        sector33 = normalize_sector33(row.get(sector_col, "")) if sector_col else ""
        type_text = " ".join(str(row.get(c, "")) for c in [market_col, type_col, sector_col] if c)
        if not market_matches(market, include):
            continue
        if any(w.lower() in (name + type_text).lower() for w in excluded_words):
            continue
        stocks.append(Stock(code, name, market, sector33))
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


RELATIVE_STRENGTH_COLUMNS = [
    "market_median_return_20d", "market_median_return_60d",
    "sector_median_return_20d", "sector_median_return_60d",
    "market_relative_20d", "market_relative_60d",
    "sector_relative_20d", "sector_relative_60d",
    "relative_strength_score", "relative_strength_rank", "relative_strength_grade",
    "dual_outperformer", "relative_strength_reason",
]


def relative_percentile(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() == 0:
        return pd.Series(0.5, index=values.index, dtype=float)
    return numeric.rank(method="average", pct=True).fillna(0.5)


def attach_relative_strength(frame: pd.DataFrame) -> pd.DataFrame:
    "Measure stock strength versus the scanned market and its JPX 33-sector peers."
    if frame is None or frame.empty:
        result = frame.copy() if frame is not None else pd.DataFrame()
        for column in RELATIVE_STRENGTH_COLUMNS:
            if column not in result.columns:
                result[column] = pd.Series(dtype="object")
        return result

    result = frame.copy()
    result["sector33"] = result.get("sector33", pd.Series(index=result.index, dtype=str)).map(normalize_sector33)
    for horizon in (20, 60):
        source = f"return_{horizon}d"
        result[source] = pd.to_numeric(result.get(source, pd.Series(index=result.index, dtype=float)), errors="coerce")
        market_median = result[source].median()
        result[f"market_median_return_{horizon}d"] = market_median
        sector_median = result.groupby("sector33", dropna=False)[source].transform("median")
        blank_sector = result["sector33"].eq("")
        sector_median = sector_median.where(~blank_sector, market_median).fillna(market_median)
        result[f"sector_median_return_{horizon}d"] = sector_median
        result[f"market_relative_{horizon}d"] = result[source] - market_median
        result[f"sector_relative_{horizon}d"] = result[source] - sector_median

    components = {
        "market_relative_20d": 0.30,
        "market_relative_60d": 0.25,
        "sector_relative_20d": 0.25,
        "sector_relative_60d": 0.20,
    }
    score = pd.Series(0.0, index=result.index, dtype=float)
    for column, weight in components.items():
        score += relative_percentile(result[column]) * weight * 100.0
    result["relative_strength_score"] = score.round(1).clip(lower=0.0, upper=100.0)
    result["relative_strength_rank"] = result["relative_strength_score"].rank(method="min", ascending=False).astype("Int64")
    result["relative_strength_grade"] = result["relative_strength_score"].map(
        lambda value: "S" if value >= 85 else "A" if value >= 70 else "B" if value >= 55 else "C"
    )
    result["dual_outperformer"] = (
        result["market_relative_20d"].gt(0)
        & result["market_relative_60d"].gt(0)
        & result["sector_relative_20d"].gt(0)
        & result["sector_relative_60d"].gt(0)
    )

    def reason(row: pd.Series) -> str:
        parts: list[str] = []
        if row_number(row, "market_relative_20d") > 0:
            parts.append("20日で市場超過")
        if row_number(row, "sector_relative_20d") > 0:
            parts.append("20日で同業超過")
        if row_number(row, "market_relative_60d") > 0:
            parts.append("60日で市場超過")
        if row_number(row, "sector_relative_60d") > 0:
            parts.append("60日で同業超過")
        return " / ".join(parts) if parts else "市場・同業比で劣後"

    result["relative_strength_reason"] = result.apply(reason, axis=1)
    return result


def build_relative_strength_table(top100: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "relative_strength_rank", "rank", "code", "name", "sector33", "score",
        "relative_strength_score", "relative_strength_grade", "dual_outperformer",
        "return_20d", "market_median_return_20d", "market_relative_20d",
        "sector_median_return_20d", "sector_relative_20d",
        "return_60d", "market_median_return_60d", "market_relative_60d",
        "sector_median_return_60d", "sector_relative_60d", "relative_strength_reason",
        "trading_value", "volume_ratio",
    ]
    if top100 is None or top100.empty:
        return pd.DataFrame(columns=columns)
    result = top100.copy().sort_values(
        ["relative_strength_score", "score", "rank"], ascending=[False, False, True]
    )
    return result[[column for column in columns if column in result.columns]].reset_index(drop=True)


def plain_relative_strength_section(relative_strength: pd.DataFrame, limit: int = 10) -> list[str]:
    if relative_strength is None or relative_strength.empty:
        return ["【市場・業種相対強度】", "相対強度を算出できませんでした。", ""]
    dual_count = int(relative_strength.get("dual_outperformer", pd.Series(dtype=bool)).fillna(False).sum())
    lines = [
        "【市場・業種相対強度】",
        "全スキャン銘柄の中央値を市場基準、JPX33業種内中央値を同業基準として比較します。",
        f"市場・同業の20日/60日をすべて上回る銘柄: {dual_count}件",
    ]
    for _, row in relative_strength.head(limit).iterrows():
        lines.append(
            f"#{int(row_number(row, 'relative_strength_rank'))} {row['code']} {row['name']}｜"
            f"相対{row_number(row, 'relative_strength_score'):.1f}点 {optional_text(row.get('relative_strength_grade'))}｜"
            f"市場20日 {fmt_pct(row.get('market_relative_20d'))}｜同業20日 {fmt_pct(row.get('sector_relative_20d'))}｜"
            f"市場60日 {fmt_pct(row.get('market_relative_60d'))}｜同業60日 {fmt_pct(row.get('sector_relative_60d'))}"
        )
    lines.append("")
    return lines


def html_relative_strength_section(relative_strength: pd.DataFrame, limit: int = 10) -> str:
    if relative_strength is None or relative_strength.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>市場・業種相対強度</b><div style="font-size:12px;color:#64748b;margin-top:5px">相対強度を算出できませんでした。</div></div>'
    dual_count = int(relative_strength.get("dual_outperformer", pd.Series(dtype=bool)).fillna(False).sum())
    items = []
    for _, row in relative_strength.head(limit).iterrows():
        grade = optional_text(row.get("relative_strength_grade"))
        color = "#15803d" if grade in {"S", "A"} else "#1d4ed8" if grade == "B" else "#64748b"
        items.append(f'''<div style="border-top:1px solid #e5e7eb;padding:9px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(row_number(row, "relative_strength_rank"))} {html_text(row["code"])} {html_text(row["name"])} <span style="float:right;color:{color}">{row_number(row, "relative_strength_score"):.1f}点 {html_text(grade)}</span></div>
<div style="clear:both;font-size:11px;color:#475569">市場20日 <b>{fmt_pct(row.get("market_relative_20d"))}</b> ・ 同業20日 <b>{fmt_pct(row.get("sector_relative_20d"))}</b> ・ 市場60日 {fmt_pct(row.get("market_relative_60d"))} ・ 同業60日 {fmt_pct(row.get("sector_relative_60d"))}</div>
</div>''')
    return f'''<div style="background:#fff;border:2px solid #0369a1;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#075985">市場・業種相対強度</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">市場中央値とJPX33業種内中央値の双方を比較します。20日/60日の全条件超過は {dual_count}件です。</div>{"".join(items)}</div>'''


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



STRATEGY_FINGERPRINT_ENV = "MOMENTUM_STRATEGY_FINGERPRINT"
STRATEGY_STAMP_SOURCE_ENV = "MOMENTUM_STRATEGY_STAMP_SOURCE"


def attach_strategy_provenance(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach the precomputed governed strategy fingerprint before persistence.

    The fingerprint is generated by strategy_governance.py before main.py runs.
    When the environment variable is absent (local or isolated smoke execution),
    the frame is returned unchanged apart from the defensive copy.
    """
    work = frame.copy()
    fingerprint = os.environ.get(STRATEGY_FINGERPRINT_ENV, "").strip()
    if work.empty or not fingerprint:
        return work
    source = os.environ.get(
        STRATEGY_STAMP_SOURCE_ENV, "DAILY_GOVERNED_WORKFLOW"
    ).strip() or "DAILY_GOVERNED_WORKFLOW"
    work["strategy_fingerprint"] = fingerprint
    work["strategy_app_version"] = APP_VERSION
    work["strategy_stamp_source"] = source
    return work


def ranking_history_columns() -> list[str]:
    return [
        "date", "rank", "code", "name", "sector33", "close", "score", "reason", "score_ytd_high", "score_ytd_streak",
        "score_return_20d", "score_volume_ratio", "score_ma", "score_trading_value", "ytd_high_flag",
        "ytd_high_streak", "ytd_high_count", "return_5d", "return_20d", "return_60d",
        "market_median_return_20d", "market_median_return_60d", "sector_median_return_20d", "sector_median_return_60d",
        "market_relative_20d", "market_relative_60d", "sector_relative_20d", "sector_relative_60d",
        "relative_strength_score", "relative_strength_rank", "relative_strength_grade", "dual_outperformer", "relative_strength_reason",
        *rs_lifecycle.LIFECYCLE_COLUMNS,
        "volume_ratio",
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


SECTOR_ROTATION_ORDER = {
    "加速": 0,
    "主導": 1,
    "改善": 2,
    "履歴開始": 3,
    "減速": 4,
    "底上げ": 5,
    "低迷": 6,
}

SECTOR_LEADER_COLUMNS = [
    "overall_leader_rank", "sector_leader_rank", "sector33", "sector_rank",
    "sector_momentum_score", "sector_strength", "sector_rotation", "sector_score_delta",
    "code", "name", "close", "price_date", "momentum_rank", "momentum_score", "sector_leader_score", "sector_leader_grade",
    "sector_research_priority", "action_priority", "action_score", "expectancy_score",
    "expectancy_confidence", "return_20d", "return_60d",
    "market_relative_20d", "market_relative_60d", "sector_relative_20d", "sector_relative_60d",
    "relative_strength_score", "relative_strength_rank", "relative_strength_grade", "dual_outperformer",
    "volume_ratio", "trading_value",
    "ma20_deviation", "leader_reasons", "leader_cautions",
]


def sector_rotation_values(row: pd.Series) -> dict[str, Any]:
    score = row_number(row, "sector_momentum_score")
    delta_value = row.get("sector_score_delta")
    rank_change_value = row.get("sector_rank_change")
    has_history = delta_value is not None and not pd.isna(delta_value)
    delta = 0.0 if not has_history else float(delta_value)
    rank_change = 0 if rank_change_value is None or pd.isna(rank_change_value) else int(float(rank_change_value))

    if not has_history:
        state = "履歴開始"
    elif score >= 60 and (delta >= 3 or rank_change >= 3):
        state = "加速"
    elif score >= 60 and delta > -3 and rank_change > -3:
        state = "主導"
    elif score >= 45 and (delta >= 3 or rank_change >= 3):
        state = "改善"
    elif score >= 45 and (delta <= -3 or rank_change <= -3):
        state = "減速"
    elif score < 45 and (delta >= 3 or rank_change >= 3):
        state = "底上げ"
    else:
        state = "低迷"

    base = min(max(score, 0.0), 100.0)
    rotation_score = base
    rotation_score += min(max(delta, -15.0), 15.0) * 1.3
    rotation_score += min(max(rank_change, -10), 10) * 1.2
    rotation_score = round(min(max(rotation_score, 0.0), 100.0), 1)

    if state == "加速":
        reason = "業種スコアまたは順位が上向き、かつ業種の絶対強度も高い"
    elif state == "主導":
        reason = "高い業種強度を維持"
    elif state == "改善":
        reason = "中立圏から順位またはスコアが改善"
    elif state == "減速":
        reason = "業種強度は残るが順位またはスコアが悪化"
    elif state == "底上げ":
        reason = "弱い水準から改善の兆し"
    elif state == "履歴開始":
        reason = "比較履歴を開始"
    else:
        reason = "業種強度と改善度がともに低い"

    return {
        "sector_rotation": state,
        "sector_rotation_score": rotation_score,
        "sector_rotation_reason": reason,
    }


def attach_sector_rotation(sector_momentum: pd.DataFrame) -> pd.DataFrame:
    if sector_momentum is None or sector_momentum.empty:
        columns = list(SECTOR_MOMENTUM_COLUMNS) + ["sector_rotation", "sector_rotation_score", "sector_rotation_reason"]
        return pd.DataFrame(columns=columns)
    result = sector_momentum.copy()
    rotation = result.apply(lambda row: pd.Series(sector_rotation_values(row)), axis=1)
    for column in rotation.columns:
        result[column] = rotation[column].values
    return result.sort_values("sector_rank").reset_index(drop=True)


def build_sector_rotation_table(sector_momentum: pd.DataFrame, sector_leaders: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "sector_rank", "sector33", "sector_momentum_score", "sector_strength", "sector_rotation",
        "sector_rotation_score", "sector_rotation_reason", "previous_sector_rank", "sector_rank_change",
        "previous_sector_score", "sector_score_delta", "top100_count", "top30_count", "above_ma20_ratio",
        "above_ma60_ratio", "top_sector_leader", "top_sector_leader_score",
    ]
    if sector_momentum is None or sector_momentum.empty:
        return pd.DataFrame(columns=columns)
    result = sector_momentum.copy()
    if sector_leaders is not None and not sector_leaders.empty:
        first = sector_leaders.sort_values(["sector33", "sector_leader_rank"]).drop_duplicates("sector33")
        first = first.assign(
            top_sector_leader=first["code"].astype(str) + " " + first["name"].astype(str),
            top_sector_leader_score=first["sector_leader_score"],
        )[["sector33", "top_sector_leader", "top_sector_leader_score"]]
        result = result.merge(first, on="sector33", how="left")
    else:
        result["top_sector_leader"] = ""
        result["top_sector_leader_score"] = None
    result["sector_rotation_order"] = result["sector_rotation"].map(SECTOR_ROTATION_ORDER).fillna(99)
    result = result.sort_values(
        ["sector_rotation_order", "sector_rotation_score", "sector_rank"],
        ascending=[True, False, True],
    ).drop(columns=["sector_rotation_order"])
    return result[[column for column in columns if column in result.columns]]


def leader_action_priority_points(value: Any) -> int:
    return {"A": 10, "B": 6, "C": 2, "見送り": -8}.get(optional_text(value), 0)


def sector_leader_values(row: pd.Series) -> dict[str, Any]:
    momentum_score = row_number(row, "score")
    momentum_rank = int(row_number(row, "rank", 999.0))
    sector_score = row_number(row, "sector_momentum_score")
    rotation = optional_text(row.get("sector_rotation"))
    trading_value = row_number(row, "trading_value")
    volume_ratio = row_number(row, "volume_ratio")
    return_20d = row_number(row, "return_20d")
    ma20_deviation = row_number(row, "ma20_deviation")
    action_priority = optional_text(row.get("action_priority"))
    expectancy_score = row_number(row, "expectancy_score", 50.0)
    confidence = optional_text(row.get("expectancy_confidence")) or "蓄積中"
    relative_strength_score = row_number(row, "relative_strength_score", 50.0)
    relative_strength_grade = optional_text(row.get("relative_strength_grade")) or "C"

    reasons: list[str] = []
    cautions: list[str] = []
    score = momentum_score * 0.38 + sector_score * 0.27

    if relative_strength_score >= 80:
        score += 8
        reasons.append(f"相対強度{relative_strength_score:.1f}点・{relative_strength_grade}")
    elif relative_strength_score >= 65:
        score += 5
        reasons.append(f"相対強度{relative_strength_score:.1f}点")
    elif relative_strength_score >= 50:
        score += 2
    elif relative_strength_score < 35:
        score -= 5
        cautions.append("市場・同業比で相対劣後")

    if momentum_rank <= 10:
        score += 12
        reasons.append("Momentum上位10位")
    elif momentum_rank <= 30:
        score += 9
        reasons.append("Momentum上位30位")
    elif momentum_rank <= 60:
        score += 6
    elif momentum_rank <= 100:
        score += 3

    rotation_points = {"加速": 10, "主導": 7, "改善": 6, "履歴開始": 2, "減速": -3, "底上げ": 1, "低迷": -6}
    score += rotation_points.get(rotation, 0)
    if rotation in {"加速", "主導", "改善"}:
        reasons.append(f"業種{rotation}")
    elif rotation in {"減速", "低迷"}:
        cautions.append(f"業種{rotation}")

    score += leader_action_priority_points(action_priority)
    if action_priority in {"A", "B"}:
        reasons.append(f"既存調査優先度{action_priority}")
    elif action_priority == "見送り":
        cautions.append("既存調査優先度は見送り")

    if expectancy_score >= 70 and confidence in {"高", "中"}:
        score += 6
        reasons.append(f"期待値{expectancy_score:.1f}点・信頼度{confidence}")
    elif expectancy_score < 50:
        score -= 3
        cautions.append("期待値50点未満")

    if trading_value >= 5_000_000_000:
        score += 7
        reasons.append("売買代金50億円以上")
    elif trading_value >= 1_000_000_000:
        score += 5
        reasons.append("売買代金10億円以上")
    elif trading_value >= 300_000_000:
        score += 3
    elif trading_value < 100_000_000:
        score -= 15
        cautions.append("売買代金1億円未満")

    if volume_ratio >= 3:
        score += 6
        reasons.append(f"出来高{volume_ratio:.1f}倍")
    elif volume_ratio >= 2:
        score += 4
    elif volume_ratio < 1:
        cautions.append("出来高倍率1倍未満")

    overheat = ma20_deviation >= 0.25 or return_20d >= 0.50
    if overheat:
        score -= 12
        cautions.append("過熱水準")
    elif ma20_deviation >= 0.18:
        score -= 5
        cautions.append(f"20日線乖離{ma20_deviation:.1%}")

    score = round(min(max(score, 0.0), 100.0), 1)
    if score >= 84 and trading_value >= 300_000_000 and not overheat and rotation in {"加速", "主導"}:
        priority = "最優先"
    elif score >= 72 and trading_value >= 100_000_000 and not overheat:
        priority = "優先"
    elif score >= 58 and trading_value >= 100_000_000:
        priority = "監視"
    else:
        priority = "見送り"

    grade = "S" if score >= 88 else "A" if score >= 78 else "B" if score >= 68 else "C"
    return {
        "sector_leader_score": score,
        "sector_leader_grade": grade,
        "sector_research_priority": priority,
        "leader_reasons": " / ".join(dict.fromkeys(reasons)),
        "leader_cautions": " / ".join(dict.fromkeys(cautions)),
    }


def build_sector_leaders(all_ranked: pd.DataFrame, sector_momentum: pd.DataFrame, action_priority: pd.DataFrame, limit_per_sector: int = 3) -> pd.DataFrame:
    columns = list(SECTOR_LEADER_COLUMNS)
    if all_ranked is None or all_ranked.empty or sector_momentum is None or sector_momentum.empty:
        return pd.DataFrame(columns=columns)
    sector_cols = [
        "sector33", "sector_rank", "sector_momentum_score", "sector_strength", "sector_rotation", "sector_score_delta",
    ]
    candidates = all_ranked.copy()
    candidates["sector33"] = candidates["sector33"].map(normalize_sector33)
    candidates = candidates[(candidates["sector33"] != "") & (numeric_series(candidates, "rank") <= 100)].copy()
    candidates = candidates.merge(sector_momentum[sector_cols].drop_duplicates("sector33"), on="sector33", how="left")

    if action_priority is not None and not action_priority.empty:
        action_cols = [
            "code", "action_priority", "action_score", "expectancy_score", "expectancy_confidence",
            "expectancy_evidence_count", "positive_reasons", "caution_reasons",
        ]
        available = [column for column in action_cols if column in action_priority.columns]
        candidates = candidates.merge(action_priority[available].drop_duplicates("code"), on="code", how="left")

    scored = candidates.apply(lambda row: pd.Series(sector_leader_values(row)), axis=1)
    for column in scored.columns:
        candidates[column] = scored[column].values
    candidates = candidates[numeric_series(candidates, "trading_value") >= 50_000_000].copy()
    candidates = candidates.sort_values(
        ["sector33", "sector_leader_score", "rank"],
        ascending=[True, False, True],
    )
    candidates["sector_leader_rank"] = candidates.groupby("sector33").cumcount() + 1
    candidates = candidates[candidates["sector_leader_rank"] <= limit_per_sector].copy()
    candidates = candidates.sort_values(
        ["sector_leader_score", "sector_momentum_score", "rank"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    candidates.insert(0, "overall_leader_rank", range(1, len(candidates) + 1))
    candidates = candidates.rename(columns={"rank": "momentum_rank", "score": "momentum_score"})
    return candidates[[column for column in columns if column in candidates.columns]]


def sector_research_priority_count(leaders: pd.DataFrame, priority: str) -> int:
    if leaders is None or leaders.empty or "sector_research_priority" not in leaders.columns:
        return 0
    return int((leaders["sector_research_priority"] == priority).sum())


def plain_sector_rotation_section(sector_rotation: pd.DataFrame, limit: int = 8) -> list[str]:
    if sector_rotation is None or sector_rotation.empty:
        return ["【業種ローテーション】", "比較可能な業種履歴がありません。", ""]
    lines = [
        "【業種ローテーション】",
        "業種の絶対強度と前回からのスコア・順位変化を組み合わせています。",
    ]
    for _, row in sector_rotation.head(limit).iterrows():
        delta = row.get("sector_score_delta")
        delta_text = "履歴開始" if delta is None or pd.isna(delta) else f"スコア差 {float(delta):+.1f}"
        rank_text = sector_rank_change_text(row.get("sector_rank_change"))
        lines.append(
            f"#{int(row['sector_rank'])} {row['sector33']}｜{row['sector_rotation']}｜"
            f"業種{float(row['sector_momentum_score']):.1f}点｜{delta_text}"
            + (f"｜{rank_text}" if rank_text else "")
        )
        leader = optional_text(row.get("top_sector_leader"))
        if leader:
            lines.append(f"   リーダー: {leader} / {row_number(row, 'top_sector_leader_score'):.1f}点")
    lines.append("")
    return lines


def plain_sector_leaders_section(leaders: pd.DataFrame, limit: int = 10) -> list[str]:
    if leaders is None or leaders.empty:
        return ["【業種リーダー候補】", "該当候補はありません。", ""]
    counts = {value: sector_research_priority_count(leaders, value) for value in ["最優先", "優先", "監視", "見送り"]}
    lines = [
        "【業種リーダー候補】",
        "売買推奨ではなく、強い・改善中の業種内で優先的に調査する銘柄です。",
        f"最優先 {counts['最優先']}件 / 優先 {counts['優先']}件 / 監視 {counts['監視']}件 / 見送り {counts['見送り']}件",
    ]
    subset = leaders[leaders["sector_research_priority"].isin(["最優先", "優先", "監視"])].head(limit)
    for _, row in subset.iterrows():
        lines.extend([
            f"#{int(row['overall_leader_rank'])} {row['code']} {row['name']}｜{row['sector33']} #{int(row['sector_rank'])} {row['sector_rotation']}",
            f"   業種リーダー {row_number(row, 'sector_leader_score'):.1f}点 / 調査 {row['sector_research_priority']} / Momentum #{int(row_number(row, 'momentum_rank'))}",
            f"   理由：{optional_text(row.get('leader_reasons')) or '-'}",
            f"   注意：{optional_text(row.get('leader_cautions')) or '特記事項なし'}",
            "",
        ])
    return lines


def html_sector_rotation_section(sector_rotation: pd.DataFrame, limit: int = 8) -> str:
    if sector_rotation is None or sector_rotation.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>業種ローテーション</b><div style="font-size:12px;color:#64748b;margin-top:5px">比較可能な業種履歴がありません。</div></div>'
    colors = {"加速": "#15803d", "主導": "#1d4ed8", "改善": "#0f766e", "減速": "#b45309", "底上げ": "#7c3aed", "低迷": "#64748b", "履歴開始": "#475569"}
    items = []
    for _, row in sector_rotation.head(limit).iterrows():
        state = optional_text(row.get("sector_rotation"))
        color = colors.get(state, "#475569")
        delta = row.get("sector_score_delta")
        delta_text = "履歴開始" if delta is None or pd.isna(delta) else f"スコア差 {float(delta):+.1f}"
        leader = optional_text(row.get("top_sector_leader"))
        leader_html = f'<div style="font-size:10px;color:#64748b;margin-top:3px">リーダー: {html_text(leader)} / {row_number(row, "top_sector_leader_score"):.1f}点</div>' if leader else ""
        items.append(f'''<div style="border-top:1px solid #e5e7eb;padding:9px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(row["sector_rank"])} {html_text(row["sector33"])} <span style="float:right;color:{color}">{html_text(state)}</span></div>
<div style="clear:both;font-size:11px;color:#475569">業種 {row_number(row, "sector_momentum_score"):.1f}点 ・ {html_text(delta_text)} ・ {html_text(sector_rank_change_text(row.get("sector_rank_change")))}</div>
{leader_html}</div>''')
    return f'''<div style="background:#fff;border:2px solid #0f766e;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#115e59">業種ローテーション</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">絶対強度と前回からの変化を組み合わせています。</div>
{"".join(items)}</div>'''


def html_sector_leaders_section(leaders: pd.DataFrame, limit: int = 10) -> str:
    if leaders is None or leaders.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>業種リーダー候補</b><div style="font-size:12px;color:#64748b;margin-top:5px">該当候補はありません。</div></div>'
    priority_colors = {"最優先": "#14532d", "優先": "#1d4ed8", "監視": "#a16207", "見送り": "#64748b"}
    subset = leaders[leaders["sector_research_priority"].isin(["最優先", "優先", "監視"])].head(limit)
    items = []
    for _, row in subset.iterrows():
        priority = optional_text(row.get("sector_research_priority"))
        color = priority_colors.get(priority, "#475569")
        caution = optional_text(row.get("leader_cautions")) or "特記事項なし"
        items.append(f'''<div style="border-top:1px solid #e5e7eb;padding:10px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(row["overall_leader_rank"])} {html_text(row["code"])} {html_text(row["name"])} <span style="float:right;color:{color}">{html_text(priority)} / {row_number(row, "sector_leader_score"):.1f}点</span></div>
<div style="clear:both;font-size:11px;color:#475569">{html_text(row["sector33"])} #{int(row["sector_rank"])} {html_text(row["sector_rotation"])} ・ Momentum #{int(row_number(row, "momentum_rank"))}</div>
<div style="font-size:11px;color:#15803d;font-weight:800;margin-top:3px">理由：{html_text(optional_text(row.get("leader_reasons")) or "-")}</div>
<div style="font-size:11px;color:#b45309;margin-top:3px">注意：{html_text(caution)}</div>
</div>''')
    counts = {value: sector_research_priority_count(leaders, value) for value in ["最優先", "優先", "監視", "見送り"]}
    return f'''<div style="background:#fff;border:2px solid #334155;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#0f172a">業種リーダー候補</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">強い・改善中の業種内で優先的に調査する銘柄です。売買推奨ではありません。</div>
<div style="font-size:13px;font-weight:800;color:#334155;margin-top:8px">最優先 {counts['最優先']}件 ・ 優先 {counts['優先']}件 ・ 監視 {counts['監視']}件 ・ 見送り {counts['見送り']}件</div>
{"".join(items)}</div>'''


SECTOR_SIGNAL_HISTORY_COLUMNS = [
    "signal_date", "entry_price_date", "code", "name", "sector33", "entry_close",
    "sector_research_priority", "sector_leader_score", "sector_leader_grade",
    "sector_rotation", "sector_momentum_score", "momentum_rank", "momentum_score",
    "action_priority", "action_score", "expectancy_score", "expectancy_confidence",
    "relative_strength_score", "relative_strength_grade", "market_relative_20d", "sector_relative_20d",
]

SECTOR_OUTCOME_COLUMNS = [
    "signal_date", "entry_price_date", "exit_price_date", "code", "name", "sector33",
    "sector_research_priority", "sector_leader_grade", "sector_rotation",
    "sector_leader_score", "horizon_days", "entry_close", "exit_close",
    "forward_return", "win", "market_benchmark_return", "sector_benchmark_return",
    "market_excess_return", "sector_excess_return", "market_outperformance", "sector_outperformance",
    "market_peer_count", "sector_peer_count", "calendar_days",
]


def load_sector_signal_history(path: str) -> pd.DataFrame:
    history_path = Path(path)
    if not history_path.exists():
        return pd.DataFrame(columns=SECTOR_SIGNAL_HISTORY_COLUMNS)
    try:
        history = pd.read_csv(history_path)
    except Exception as exc:
        logger.warning("Sector leader signal history could not be read: %s", exc)
        return pd.DataFrame(columns=SECTOR_SIGNAL_HISTORY_COLUMNS)
    if "code" in history.columns:
        history["code"] = history["code"].map(normalize_code)
    for column in SECTOR_SIGNAL_HISTORY_COLUMNS:
        if column not in history.columns:
            history[column] = None
    return history[SECTOR_SIGNAL_HISTORY_COLUMNS]


def current_sector_signal_snapshot(today: str, sector_leaders: pd.DataFrame) -> pd.DataFrame:
    if sector_leaders is None or sector_leaders.empty:
        return pd.DataFrame(columns=SECTOR_SIGNAL_HISTORY_COLUMNS)
    rows = []
    for _, row in sector_leaders.iterrows():
        rows.append({
            "signal_date": today,
            "entry_price_date": optional_text(row.get("price_date")) or today,
            "code": normalize_code(row.get("code")),
            "name": optional_text(row.get("name")),
            "sector33": optional_text(row.get("sector33")),
            "entry_close": row_number(row, "close"),
            "sector_research_priority": optional_text(row.get("sector_research_priority")),
            "sector_leader_score": row_number(row, "sector_leader_score"),
            "sector_leader_grade": optional_text(row.get("sector_leader_grade")),
            "sector_rotation": optional_text(row.get("sector_rotation")),
            "sector_momentum_score": row_number(row, "sector_momentum_score"),
            "momentum_rank": int(row_number(row, "momentum_rank", 999)),
            "momentum_score": row_number(row, "momentum_score"),
            "action_priority": optional_text(row.get("action_priority")),
            "action_score": row_number(row, "action_score"),
            "expectancy_score": row_number(row, "expectancy_score", 50),
            "expectancy_confidence": optional_text(row.get("expectancy_confidence")) or "蓄積中",
            "relative_strength_score": row_number(row, "relative_strength_score", 50),
            "relative_strength_grade": optional_text(row.get("relative_strength_grade")) or "C",
            "market_relative_20d": optional_number(row.get("market_relative_20d")),
            "sector_relative_20d": optional_number(row.get("sector_relative_20d")),
        })
    return pd.DataFrame(rows, columns=SECTOR_SIGNAL_HISTORY_COLUMNS)


def update_sector_signal_history(path: str, current: pd.DataFrame) -> pd.DataFrame:
    old = load_sector_signal_history(path)
    frames = [frame for frame in (old, current) if frame is not None and not frame.empty]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=SECTOR_SIGNAL_HISTORY_COLUMNS)
    if not combined.empty:
        combined["code"] = combined["code"].map(normalize_code)
        combined = combined.drop_duplicates(["signal_date", "code"], keep="last")
        combined = combined.sort_values(["signal_date", "sector_leader_score", "code"], ascending=[True, False, True])
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(history_path, index=False)
    return combined[SECTOR_SIGNAL_HISTORY_COLUMNS]



def peer_forward_benchmarks(
    prices: pd.DataFrame,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    sector33: str,
    cache: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    entry_key = entry_date.date().isoformat()
    exit_key = exit_date.date().isoformat()
    sector_key = normalize_sector33(sector33)
    cache_key = (entry_key, exit_key, sector_key)
    if cache_key in cache:
        return cache[cache_key]

    entry_rows = prices[prices["date_sort"] == entry_date.normalize()][["code", "close", "sector33"]].rename(columns={"close": "entry_peer_close"})
    exit_rows = prices[prices["date_sort"] == exit_date.normalize()][["code", "close"]].rename(columns={"close": "exit_peer_close"})
    paired = entry_rows.merge(exit_rows, on="code", how="inner")
    paired = paired[(paired["entry_peer_close"] > 0) & paired["exit_peer_close"].notna()].copy()
    paired["peer_forward_return"] = paired["exit_peer_close"] / paired["entry_peer_close"] - 1

    market_return = float(paired["peer_forward_return"].median()) if len(paired) >= 10 else None
    sector_rows = paired[paired["sector33"].map(normalize_sector33) == sector_key] if sector_key else pd.DataFrame()
    sector_return = float(sector_rows["peer_forward_return"].median()) if len(sector_rows) >= 2 else None
    result = {
        "market_benchmark_return": market_return,
        "sector_benchmark_return": sector_return,
        "market_peer_count": int(len(paired)),
        "sector_peer_count": int(len(sector_rows)),
    }
    cache[cache_key] = result
    return result


def calculate_sector_leader_outcomes(signal_history: pd.DataFrame, price_history: pd.DataFrame, horizons: tuple[int, ...] = (5, 10, 20)) -> pd.DataFrame:
    if signal_history is None or signal_history.empty or price_history is None or price_history.empty:
        return pd.DataFrame(columns=SECTOR_OUTCOME_COLUMNS)
    required = {"date", "code", "close"}
    if not required.issubset(price_history.columns):
        return pd.DataFrame(columns=SECTOR_OUTCOME_COLUMNS)
    price_columns = ["date", "code", "close"] + (["sector33"] if "sector33" in price_history.columns else [])
    prices = price_history[price_columns].copy()
    if "sector33" not in prices.columns:
        prices["sector33"] = ""
    prices["sector33"] = prices["sector33"].map(normalize_sector33)
    prices["code"] = prices["code"].map(normalize_code)
    prices["date_sort"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["date_sort", "close"]).drop_duplicates(["code", "date_sort"], keep="last")
    price_groups = {code: group.sort_values("date_sort") for code, group in prices.groupby("code")}
    benchmark_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    outcomes: list[dict[str, Any]] = []
    for _, signal_row in signal_history.iterrows():
        code = normalize_code(signal_row.get("code"))
        if code not in price_groups:
            continue
        entry_date = pd.to_datetime(signal_row.get("entry_price_date") or signal_row.get("signal_date"), errors="coerce")
        entry_close = pd.to_numeric(pd.Series([signal_row.get("entry_close")]), errors="coerce").iloc[0]
        if pd.isna(entry_date) or pd.isna(entry_close) or float(entry_close) <= 0:
            continue
        future = price_groups[code][price_groups[code]["date_sort"] > entry_date]
        for horizon in horizons:
            if len(future) < horizon:
                continue
            exit_row = future.iloc[horizon - 1]
            exit_close = float(exit_row["close"])
            forward_return = exit_close / float(entry_close) - 1
            benchmark = peer_forward_benchmarks(
                prices, entry_date.normalize(), exit_row["date_sort"].normalize(),
                optional_text(signal_row.get("sector33")), benchmark_cache,
            )
            market_benchmark_return = benchmark["market_benchmark_return"]
            sector_benchmark_return = benchmark["sector_benchmark_return"]
            market_excess_return = None if market_benchmark_return is None else forward_return - market_benchmark_return
            sector_excess_return = None if sector_benchmark_return is None else forward_return - sector_benchmark_return
            outcomes.append({
                "signal_date": signal_row.get("signal_date"),
                "entry_price_date": entry_date.date().isoformat(),
                "exit_price_date": exit_row["date_sort"].date().isoformat(),
                "code": code,
                "name": signal_row.get("name"),
                "sector33": signal_row.get("sector33"),
                "sector_research_priority": signal_row.get("sector_research_priority"),
                "sector_leader_grade": signal_row.get("sector_leader_grade"),
                "sector_rotation": signal_row.get("sector_rotation"),
                "sector_leader_score": signal_row.get("sector_leader_score"),
                "horizon_days": horizon,
                "entry_close": float(entry_close),
                "exit_close": exit_close,
                "forward_return": forward_return,
                "win": bool(forward_return > 0),
                "market_benchmark_return": market_benchmark_return,
                "sector_benchmark_return": sector_benchmark_return,
                "market_excess_return": market_excess_return,
                "sector_excess_return": sector_excess_return,
                "market_outperformance": None if market_excess_return is None else bool(market_excess_return > 0),
                "sector_outperformance": None if sector_excess_return is None else bool(sector_excess_return > 0),
                "market_peer_count": benchmark["market_peer_count"],
                "sector_peer_count": benchmark["sector_peer_count"],
                "calendar_days": int((exit_row["date_sort"] - entry_date).days),
            })
    return pd.DataFrame(outcomes, columns=SECTOR_OUTCOME_COLUMNS)


def sector_performance_record(group_type: str, group_value: str, horizon: int, subset: pd.DataFrame) -> dict[str, Any]:
    returns = pd.to_numeric(subset.get("forward_return", pd.Series(dtype=float)), errors="coerce").dropna()
    wins = subset.get("win", pd.Series(dtype=bool)).fillna(False).astype(bool)
    market_excess = pd.to_numeric(subset.get("market_excess_return", pd.Series(dtype=float)), errors="coerce").dropna()
    sector_excess = pd.to_numeric(subset.get("sector_excess_return", pd.Series(dtype=float)), errors="coerce").dropna()
    market_flags = subset.get("market_outperformance", pd.Series(index=subset.index, dtype=object)).dropna().astype(bool)
    sector_flags = subset.get("sector_outperformance", pd.Series(index=subset.index, dtype=object)).dropna().astype(bool)
    return {
        "group_type": group_type,
        "group_value": group_value,
        "horizon_days": horizon,
        "count": int(len(returns)),
        "win_rate": float(wins.mean()) if len(wins) else None,
        "average_return": float(returns.mean()) if not returns.empty else None,
        "median_return": float(returns.median()) if not returns.empty else None,
        "best_return": float(returns.max()) if not returns.empty else None,
        "worst_return": float(returns.min()) if not returns.empty else None,
        "average_market_excess_return": float(market_excess.mean()) if not market_excess.empty else None,
        "market_outperformance_rate": float(market_flags.mean()) if len(market_flags) else None,
        "average_sector_excess_return": float(sector_excess.mean()) if not sector_excess.empty else None,
        "sector_outperformance_rate": float(sector_flags.mean()) if len(sector_flags) else None,
        "average_leader_score": float(pd.to_numeric(subset.get("sector_leader_score", pd.Series(dtype=float)), errors="coerce").mean()) if len(subset) else None,
    }


def build_sector_leader_performance_summary(outcomes: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "group_type", "group_value", "horizon_days", "count", "win_rate",
        "average_return", "median_return", "best_return", "worst_return",
        "average_market_excess_return", "market_outperformance_rate",
        "average_sector_excess_return", "sector_outperformance_rate", "average_leader_score",
    ]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    records: list[dict[str, Any]] = []
    for horizon, horizon_rows in outcomes.groupby("horizon_days"):
        records.append(sector_performance_record("overall", "ALL", int(horizon), horizon_rows))
        for group_type, column in [
            ("priority", "sector_research_priority"),
            ("rotation", "sector_rotation"),
            ("grade", "sector_leader_grade"),
            ("sector", "sector33"),
        ]:
            for value, subset in horizon_rows.groupby(column, dropna=False):
                value_text = optional_text(value) or "未分類"
                records.append(sector_performance_record(group_type, value_text, int(horizon), subset))
    result = pd.DataFrame(records, columns=columns)
    return result.sort_values(["horizon_days", "group_type", "count", "group_value"], ascending=[True, True, False, True])


def performance_overall_stats(summary: pd.DataFrame, horizon: int) -> dict[str, Any]:
    if summary is None or summary.empty:
        return {}
    rows = summary[(summary["group_type"] == "overall") & (summary["horizon_days"] == horizon)]
    return {} if rows.empty else rows.iloc[0].to_dict()


def build_signal_governance(outcomes: pd.DataFrame, recent_limit: int = 20) -> pd.DataFrame:
    columns = [
        "scope_type", "scope_value", "horizon_days", "evidence_count", "recent_count",
        "baseline_average_return", "recent_average_return", "return_delta",
        "baseline_win_rate", "recent_win_rate", "win_rate_delta",
        "status", "health_score", "recommendation",
    ]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    scopes: list[tuple[str, str, pd.DataFrame]] = [("overall", "ALL", outcomes)]
    for scope_type, column, allowed in [
        ("priority", "sector_research_priority", ["最優先", "優先", "監視"]),
        ("rotation", "sector_rotation", ["加速", "主導", "改善", "減速"]),
    ]:
        for value in allowed:
            subset = outcomes[outcomes.get(column, pd.Series(index=outcomes.index, dtype=str)) == value]
            if not subset.empty:
                scopes.append((scope_type, value, subset))
    records: list[dict[str, Any]] = []
    for scope_type, scope_value, scope_rows in scopes:
        for horizon in (5, 10, 20):
            subset = scope_rows[scope_rows["horizon_days"] == horizon].copy()
            subset["signal_sort"] = pd.to_datetime(subset["signal_date"], errors="coerce")
            subset = subset.sort_values("signal_sort")
            count = len(subset)
            if count == 0:
                continue
            recent = subset.tail(min(recent_limit, count))
            baseline = subset.iloc[:-len(recent)] if count > len(recent) else subset
            baseline_return = float(pd.to_numeric(baseline["forward_return"], errors="coerce").mean())
            recent_return = float(pd.to_numeric(recent["forward_return"], errors="coerce").mean())
            baseline_win = float(baseline["win"].fillna(False).astype(bool).mean())
            recent_win = float(recent["win"].fillna(False).astype(bool).mean())
            return_delta = recent_return - baseline_return
            win_delta = recent_win - baseline_win
            if count < 8:
                status = "実績蓄積中"
                recommendation = "判定変更を行わず、実績を蓄積"
                health_score = 50
            elif (recent_return < 0 <= baseline_return) or return_delta <= -0.03 or win_delta <= -0.15:
                status = "劣化警戒"
                recommendation = "閾値を厳格化し、対象範囲を縮小"
                health_score = max(0, int(50 + return_delta * 500 + win_delta * 100))
            elif return_delta >= 0.03 and win_delta >= 0.10:
                status = "改善"
                recommendation = "十分な実績があれば対象範囲の拡張を検討"
                health_score = min(100, int(65 + return_delta * 300 + win_delta * 80))
            else:
                status = "安定"
                recommendation = "現行閾値を維持"
                health_score = min(100, max(0, int(60 + recent_return * 250 + (recent_win - 0.5) * 60)))
            records.append({
                "scope_type": scope_type,
                "scope_value": scope_value,
                "horizon_days": horizon,
                "evidence_count": count,
                "recent_count": len(recent),
                "baseline_average_return": baseline_return,
                "recent_average_return": recent_return,
                "return_delta": return_delta,
                "baseline_win_rate": baseline_win,
                "recent_win_rate": recent_win,
                "win_rate_delta": win_delta,
                "status": status,
                "health_score": health_score,
                "recommendation": recommendation,
            })
    result = pd.DataFrame(records, columns=columns)
    status_order = {"劣化警戒": 0, "実績蓄積中": 1, "安定": 2, "改善": 3}
    result["status_order"] = result["status"].map(status_order).fillna(9)
    return result.sort_values(["status_order", "scope_type", "horizon_days", "scope_value"]).drop(columns=["status_order"])


def build_adaptive_threshold_recommendations(governance: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "mode", "threshold_name", "current_value", "recommended_value", "change",
        "evidence_count", "governance_status", "reason", "activation_condition",
    ]
    current = {"最優先": 84, "優先": 72, "監視": 58}
    overall = pd.DataFrame()
    if governance is not None and not governance.empty:
        overall = governance[(governance["scope_type"] == "overall") & (governance["horizon_days"] == 10)]
        if overall.empty:
            overall = governance[(governance["scope_type"] == "overall") & (governance["horizon_days"] == 5)]
    status = "実績蓄積中" if overall.empty else optional_text(overall.iloc[0].get("status"))
    evidence = 0 if overall.empty else int(row_number(overall.iloc[0], "evidence_count"))
    recent_return = None if overall.empty else overall.iloc[0].get("recent_average_return")
    recent_win = None if overall.empty else overall.iloc[0].get("recent_win_rate")
    if status == "劣化警戒":
        adjustments = {"最優先": 4, "優先": 4, "監視": 3}
        reason = "直近実績の劣化を検知したため、候補抽出を厳格化"
    elif status == "改善" and evidence >= 30 and recent_return is not None and recent_win is not None and float(recent_return) > 0.03 and float(recent_win) >= 0.60:
        adjustments = {"最優先": -2, "優先": -2, "監視": -1}
        reason = "十分な実績を伴う改善を確認したため、限定的な対象拡張を提案"
    else:
        adjustments = {"最優先": 0, "優先": 0, "監視": 0}
        reason = "現行閾値を維持し、追加実績を蓄積"
    records = []
    for name, value in current.items():
        recommended = value + adjustments[name]
        records.append({
            "mode": "shadow_only",
            "threshold_name": name,
            "current_value": value,
            "recommended_value": recommended,
            "change": recommended - value,
            "evidence_count": evidence,
            "governance_status": status,
            "reason": reason,
            "activation_condition": "30件以上の実績、再現テスト合格、手動レビュー後にのみ本番反映",
        })
    return pd.DataFrame(records, columns=columns)


def run_health_overall(run_health: pd.DataFrame) -> str:
    if run_health is None or run_health.empty:
        return "UNKNOWN"
    overall = run_health[run_health["check_name"] == "overall"]
    return "UNKNOWN" if overall.empty else optional_text(overall.iloc[0].get("status"))


def build_run_health(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, sector_momentum: pd.DataFrame, sector_leaders: pd.DataFrame, errors: list[dict[str, Any]], scan_target: int, success: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(name: str, status: str, actual: Any, expected: str, detail: str) -> None:
        rows.append({"check_name": name, "status": status, "actual": actual, "expected": expected, "detail": detail})

    coverage = success / scan_target if scan_target else 0.0
    add("scan_coverage", "PASS" if coverage >= 0.95 else "WARN" if coverage >= 0.85 else "FAIL", coverage, ">=95%", "取得成功率")
    duplicate_codes = int(all_ranked["code"].duplicated().sum()) if all_ranked is not None and not all_ranked.empty and "code" in all_ranked.columns else 0
    add("duplicate_codes", "PASS" if duplicate_codes == 0 else "FAIL", duplicate_codes, "0", "同一実行内の重複コード")
    missing_sector_ratio = float((all_ranked.get("sector33", pd.Series(index=all_ranked.index, dtype=str)).fillna("").astype(str).str.strip() == "").mean()) if all_ranked is not None and not all_ranked.empty else 1.0
    add("missing_sector_ratio", "PASS" if missing_sector_ratio <= 0.05 else "WARN" if missing_sector_ratio <= 0.15 else "FAIL", missing_sector_ratio, "<=5%", "33業種分類の欠損率")
    latest = pd.to_datetime(all_ranked.get("price_date", pd.Series(dtype=str)), errors="coerce").max() if all_ranked is not None and not all_ranked.empty else pd.NaT
    age_days = None if pd.isna(latest) else int((pd.to_datetime(today) - latest.normalize()).days)
    stale_status = "FAIL" if age_days is None or age_days > 5 else "WARN" if age_days > 3 else "PASS"
    add("price_freshness", stale_status, age_days, "<=3 calendar days", "最新株価データからの経過日数")
    expected_top100 = min(100, len(all_ranked)) if all_ranked is not None else 0
    top100_count = len(top100) if top100 is not None else 0
    add("top100_count", "PASS" if top100_count == expected_top100 else "WARN", top100_count, str(expected_top100), "Momentum Top100件数")
    sector_count = len(sector_momentum) if sector_momentum is not None else 0
    add("sector_coverage", "PASS" if sector_count >= 25 else "WARN" if sector_count >= 15 else "FAIL", sector_count, ">=25", "集計できた33業種数")
    leader_count = len(sector_leaders) if sector_leaders is not None else 0
    add("sector_leader_count", "PASS" if leader_count >= 5 else "WARN" if leader_count > 0 else "FAIL", leader_count, ">=5", "業種リーダー候補数")
    error_rate = len(errors) / scan_target if scan_target else 1.0
    add("error_rate", "PASS" if error_rate <= 0.05 else "WARN" if error_rate <= 0.15 else "FAIL", error_rate, "<=5%", "取得失敗率")
    invalid_scores = 0
    if all_ranked is not None and not all_ranked.empty and "score" in all_ranked.columns:
        scores = pd.to_numeric(all_ranked["score"], errors="coerce")
        invalid_scores = int(((scores < 0) | (scores > 100) | scores.isna()).sum())
    add("score_bounds", "PASS" if invalid_scores == 0 else "FAIL", invalid_scores, "0", "Momentumスコアの範囲外・欠損")
    statuses = [row["status"] for row in rows]
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    rows.insert(0, {"check_name": "overall", "status": overall, "actual": overall, "expected": "PASS", "detail": f"PASS {statuses.count('PASS')} / WARN {statuses.count('WARN')} / FAIL {statuses.count('FAIL')}"})
    return pd.DataFrame(rows, columns=["check_name", "status", "actual", "expected", "detail"])


def plain_governance_section(performance: pd.DataFrame, governance: pd.DataFrame, thresholds: pd.DataFrame, run_health: pd.DataFrame) -> list[str]:
    lines = ["【実績検証・運用品質】", f"Run Health: {run_health_overall(run_health)}"]
    for horizon in (5, 10, 20):
        stats = performance_overall_stats(performance, horizon)
        if stats:
            lines.append(f"業種リーダー {horizon}日実績: {int(stats.get('count', 0))}件 / 勝率 {fmt_pct(stats.get('win_rate'))} / 平均 {fmt_pct(stats.get('average_return'))}")
    alerts = governance[governance["status"] == "劣化警戒"] if governance is not None and not governance.empty else pd.DataFrame()
    lines.append(f"劣化警戒: {len(alerts)}件")
    for _, row in alerts.head(3).iterrows():
        lines.append(f"  {row['scope_type']} {row['scope_value']} {int(row['horizon_days'])}日 / 直近 {fmt_pct(row.get('recent_average_return'))} / {row['recommendation']}")
    if thresholds is not None and not thresholds.empty:
        first = thresholds.iloc[0]
        change_text = ", ".join(f"{row['threshold_name']} {int(row['current_value'])}→{int(row['recommended_value'])}" for _, row in thresholds.iterrows())
        lines.append(f"閾値提案（shadow only）: {change_text} / {first['reason']}")
    warnings = run_health[run_health["status"].isin(["WARN", "FAIL"])] if run_health is not None and not run_health.empty else pd.DataFrame()
    for _, row in warnings.head(4).iterrows():
        lines.append(f"  品質 {row['status']}: {row['check_name']} / 実績 {row['actual']} / 基準 {row['expected']}")
    lines.append("")
    return lines


def html_governance_section(performance: pd.DataFrame, governance: pd.DataFrame, thresholds: pd.DataFrame, run_health: pd.DataFrame) -> str:
    overall = run_health_overall(run_health)
    health_color = "#15803d" if overall == "PASS" else "#b45309" if overall == "WARN" else "#b91c1c"
    metrics = []
    for horizon in (5, 10, 20):
        stats = performance_overall_stats(performance, horizon)
        if stats:
            metrics.append(f'<div style="font-size:12px;color:#334155">{horizon}日: <b>{int(stats.get("count", 0))}件</b> ・ 勝率 <b>{fmt_pct(stats.get("win_rate"))}</b> ・ 平均 <b>{fmt_pct(stats.get("average_return"))}</b></div>')
    alerts = governance[governance["status"] == "劣化警戒"] if governance is not None and not governance.empty else pd.DataFrame()
    alert_html = "".join(f'<div style="font-size:11px;color:#b91c1c;margin-top:4px">{html_text(row["scope_type"])} {html_text(row["scope_value"])} {int(row["horizon_days"])}日 ・ 直近 {fmt_pct(row.get("recent_average_return"))} ・ {html_text(row["recommendation"])}</div>' for _, row in alerts.head(3).iterrows())
    threshold_html = ""
    if thresholds is not None and not thresholds.empty:
        threshold_text = " / ".join(f'{row["threshold_name"]} {int(row["current_value"])}→{int(row["recommended_value"])}' for _, row in thresholds.iterrows())
        threshold_html = f'<div style="font-size:11px;color:#475569;margin-top:8px"><b>閾値提案（shadow only）:</b> {html_text(threshold_text)}</div>'
    warnings = run_health[run_health["status"].isin(["WARN", "FAIL"])] if run_health is not None and not run_health.empty else pd.DataFrame()
    warning_html = "".join(f'<div style="font-size:11px;color:#b45309;margin-top:3px">品質 {html_text(row["status"])}: {html_text(row["check_name"])} ・ 実績 {html_text(row["actual"])} ・ 基準 {html_text(row["expected"])}</div>' for _, row in warnings.head(4).iterrows())
    return f'''<div style="background:#fff;border:2px solid {health_color};border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#0f172a">実績検証・運用品質 <span style="float:right;color:{health_color}">{html_text(overall)}</span></div>
<div style="clear:both;font-size:12px;color:#64748b;margin:5px 0">業種リーダーの実績、シグナル劣化、閾値提案、データ品質を監視します。</div>
{"".join(metrics)}<div style="font-size:12px;font-weight:800;color:#334155;margin-top:7px">劣化警戒 {len(alerts)}件</div>{alert_html}{threshold_html}{warning_html}</div>'''


PAPER_INITIAL_CAPITAL = 10_000_000.0
PAPER_MAX_POSITIONS = 10
PAPER_MAX_POSITION_WEIGHT = 0.12
PAPER_MAX_SECTOR_WEIGHT = 0.25
PAPER_RISK_PER_TRADE = 0.01
PAPER_LOT_SIZE = 100
PAPER_MAX_HOLDING_DAYS = 20

PAPER_POSITION_COLUMNS = [
    "position_id", "status", "code", "name", "sector33", "entry_date", "entry_price",
    "quantity", "cost_basis", "current_price", "market_value", "highest_close",
    "stop_price", "target_price", "trailing_stop_pct", "holding_days",
    "sector_research_priority", "sector_leader_score", "sector_rotation",
    "unrealized_pnl", "unrealized_return",
]

PAPER_TRADE_HISTORY_COLUMNS = PAPER_POSITION_COLUMNS + [
    "exit_date", "exit_price", "exit_reason", "realized_pnl", "realized_return",
]

PAPER_PLAN_COLUMNS = [
    "plan_date", "action", "code", "name", "sector33", "entry_reference_price",
    "quantity", "planned_value", "portfolio_weight", "stop_price", "target_price",
    "risk_per_share", "planned_risk", "sector_research_priority", "sector_leader_score",
    "sector_rotation", "reason", "blocked_reason",
]

PAPER_EQUITY_COLUMNS = [
    "date", "initial_capital", "cash", "invested_cost", "market_value", "equity",
    "realized_pnl", "unrealized_pnl", "exposure_ratio", "peak_equity", "drawdown",
    "open_positions", "closed_trades", "win_rate",
]

RISK_BUDGET_COLUMNS = [
    "budget_type", "label", "current_value", "limit_value", "utilization", "status", "detail",
]


def atomic_write_csv(frame: pd.DataFrame, path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(target)


def load_csv_with_columns(path: str, columns: list[str]) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        return pd.DataFrame(columns=columns)
    try:
        frame = pd.read_csv(target)
    except Exception as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    if "code" in frame.columns:
        frame["code"] = frame["code"].map(normalize_code)
    return frame[columns]


def paper_target_exposure(regime_label: str, health_status: str) -> float:
    base = {
        "強気": 0.80,
        "やや強気": 0.65,
        "中立": 0.45,
        "弱気": 0.20,
        "過熱警戒": 0.30,
    }.get(optional_text(regime_label), 0.35)
    health = optional_text(health_status)
    if health == "FAIL":
        return 0.0
    if health == "WARN":
        return round(base * 0.50, 4)
    return base


def business_holding_days(entry_date: Any, current_date: Any) -> int:
    entry = pd.to_datetime(entry_date, errors="coerce")
    current = pd.to_datetime(current_date, errors="coerce")
    if pd.isna(entry) or pd.isna(current) or current <= entry:
        return 0
    return max(len(pd.bdate_range(entry.normalize(), current.normalize())) - 1, 0)


def current_price_lookup(all_ranked: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if all_ranked is None or all_ranked.empty or "code" not in all_ranked.columns:
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for _, row in all_ranked.iterrows():
        lookup[normalize_code(row.get("code"))] = row.to_dict()
    return lookup


def mark_paper_positions(
    today: str,
    portfolio: pd.DataFrame,
    all_ranked: pd.DataFrame,
    eligible_codes: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if portfolio is None or portfolio.empty:
        return pd.DataFrame(columns=PAPER_POSITION_COLUMNS), pd.DataFrame(columns=PAPER_TRADE_HISTORY_COLUMNS)
    prices = current_price_lookup(all_ranked)
    active_rows: list[dict[str, Any]] = []
    closed_rows: list[dict[str, Any]] = []
    for _, source in portfolio.iterrows():
        row = source.to_dict()
        code = normalize_code(row.get("code"))
        price_data = prices.get(code, {})
        current_price = row_number(pd.Series(price_data), "close", row_number(source, "current_price", row_number(source, "entry_price")))
        entry_price = row_number(source, "entry_price")
        quantity = int(row_number(source, "quantity"))
        highest_close = max(row_number(source, "highest_close", entry_price), current_price)
        holding_days = business_holding_days(source.get("entry_date"), today)
        stop_price = row_number(source, "stop_price", entry_price * 0.92)
        target_price = row_number(source, "target_price", entry_price * 1.16)
        trailing_stop_pct = row_number(source, "trailing_stop_pct", 0.10)
        trailing_price = highest_close * (1 - trailing_stop_pct)
        exit_reason = ""
        if current_price <= stop_price:
            exit_reason = "STOP_LOSS"
        elif current_price >= target_price:
            exit_reason = "TAKE_PROFIT"
        elif holding_days >= 5 and current_price <= trailing_price:
            exit_reason = "TRAILING_STOP"
        elif holding_days >= PAPER_MAX_HOLDING_DAYS:
            exit_reason = "TIME_EXIT"
        elif holding_days >= 5 and code not in eligible_codes:
            exit_reason = "SIGNAL_EXIT"
        market_value = current_price * quantity
        cost_basis = entry_price * quantity
        unrealized_pnl = market_value - cost_basis
        common = {
            **row,
            "code": code,
            "status": "OPEN" if not exit_reason else "CLOSED",
            "current_price": current_price,
            "market_value": market_value,
            "highest_close": highest_close,
            "holding_days": holding_days,
            "unrealized_pnl": unrealized_pnl if not exit_reason else 0.0,
            "unrealized_return": current_price / entry_price - 1 if entry_price else None,
        }
        if exit_reason:
            realized_pnl = (current_price - entry_price) * quantity
            closed_rows.append({
                **common,
                "exit_date": today,
                "exit_price": current_price,
                "exit_reason": exit_reason,
                "realized_pnl": realized_pnl,
                "realized_return": current_price / entry_price - 1 if entry_price else None,
            })
        else:
            active_rows.append(common)
    active = pd.DataFrame(active_rows)
    closed = pd.DataFrame(closed_rows)
    for column in PAPER_POSITION_COLUMNS:
        if column not in active.columns:
            active[column] = None
    for column in PAPER_TRADE_HISTORY_COLUMNS:
        if column not in closed.columns:
            closed[column] = None
    return active[PAPER_POSITION_COLUMNS], closed[PAPER_TRADE_HISTORY_COLUMNS]


def paper_portfolio_totals(
    portfolio: pd.DataFrame,
    trade_history: pd.DataFrame,
    initial_capital: float = PAPER_INITIAL_CAPITAL,
) -> dict[str, float]:
    realized = float(pd.to_numeric(trade_history.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if trade_history is not None and not trade_history.empty else 0.0
    cost = float(pd.to_numeric(portfolio.get("cost_basis", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if portfolio is not None and not portfolio.empty else 0.0
    market_value = float(pd.to_numeric(portfolio.get("market_value", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if portfolio is not None and not portfolio.empty else 0.0
    unrealized = market_value - cost
    cash = initial_capital + realized - cost
    equity = cash + market_value
    return {
        "initial_capital": initial_capital,
        "realized_pnl": realized,
        "invested_cost": cost,
        "market_value": market_value,
        "unrealized_pnl": unrealized,
        "cash": cash,
        "equity": equity,
        "exposure_ratio": market_value / equity if equity > 0 else 0.0,
    }


def build_paper_trade_plan(
    today: str,
    sector_leaders: pd.DataFrame,
    portfolio: pd.DataFrame,
    trade_history: pd.DataFrame,
    regime: dict[str, Any],
    run_health: pd.DataFrame,
    blocked_codes: set[str] | None = None,
    initial_capital: float = PAPER_INITIAL_CAPITAL,
) -> pd.DataFrame:
    if sector_leaders is None or sector_leaders.empty:
        return pd.DataFrame(columns=PAPER_PLAN_COLUMNS)
    health_status = run_health_overall(run_health)
    target_exposure = paper_target_exposure(optional_text(regime.get("label")), health_status)
    if target_exposure <= 0:
        return pd.DataFrame(columns=PAPER_PLAN_COLUMNS)
    totals = paper_portfolio_totals(portfolio, trade_history, initial_capital)
    equity = totals["equity"]
    target_market_value = max(equity * target_exposure, 0.0)
    available_value = max(target_market_value - totals["market_value"], 0.0)
    slots = max(PAPER_MAX_POSITIONS - len(portfolio), 0)
    if slots <= 0 or available_value <= 0:
        return pd.DataFrame(columns=PAPER_PLAN_COLUMNS)
    existing_codes = set(portfolio.get("code", pd.Series(dtype=str)).map(normalize_code)) if portfolio is not None and not portfolio.empty else set()
    blocked = {normalize_code(code) for code in (blocked_codes or set())}
    sector_used: dict[str, float] = {}
    if portfolio is not None and not portfolio.empty:
        for sector, group in portfolio.groupby("sector33"):
            sector_used[optional_text(sector)] = float(pd.to_numeric(group["market_value"], errors="coerce").fillna(0).sum())
    candidates = sector_leaders[sector_leaders["sector_research_priority"].isin(["最優先", "優先"])].copy()
    candidates = candidates.sort_values(["sector_research_priority", "sector_leader_score", "momentum_rank"], ascending=[True, False, True])
    rows: list[dict[str, Any]] = []
    planned_total = 0.0
    for _, candidate in candidates.iterrows():
        if len(rows) >= slots:
            break
        code = normalize_code(candidate.get("code"))
        if code in existing_codes or code in blocked:
            continue
        entry = row_number(candidate, "close")
        if entry <= 0:
            continue
        sector = optional_text(candidate.get("sector33")) or "未分類"
        max_position_value = equity * PAPER_MAX_POSITION_WEIGHT
        sector_remaining = max(equity * PAPER_MAX_SECTOR_WEIGHT - sector_used.get(sector, 0.0), 0.0)
        remaining_target = max(available_value - planned_total, 0.0)
        allocation_cap = min(max_position_value, sector_remaining, remaining_target)
        if allocation_cap < entry * PAPER_LOT_SIZE:
            continue
        stop_pct = 0.07 if optional_text(regime.get("label")) in {"強気", "やや強気"} else 0.08
        if optional_text(regime.get("label")) == "過熱警戒":
            stop_pct = 0.06
        stop_price = round(entry * (1 - stop_pct), 2)
        risk_per_share = entry - stop_price
        risk_budget = equity * PAPER_RISK_PER_TRADE
        quantity_by_value = int(allocation_cap // (entry * PAPER_LOT_SIZE)) * PAPER_LOT_SIZE
        quantity_by_risk = int(risk_budget // (risk_per_share * PAPER_LOT_SIZE)) * PAPER_LOT_SIZE if risk_per_share > 0 else 0
        quantity = min(quantity_by_value, quantity_by_risk)
        if quantity < PAPER_LOT_SIZE:
            continue
        planned_value = entry * quantity
        planned_risk = risk_per_share * quantity
        target_price = round(entry + risk_per_share * 2.0, 2)
        rows.append({
            "plan_date": today,
            "action": "PAPER_OPEN",
            "code": code,
            "name": optional_text(candidate.get("name")),
            "sector33": sector,
            "entry_reference_price": entry,
            "quantity": quantity,
            "planned_value": planned_value,
            "portfolio_weight": planned_value / equity if equity > 0 else 0.0,
            "stop_price": stop_price,
            "target_price": target_price,
            "risk_per_share": risk_per_share,
            "planned_risk": planned_risk,
            "sector_research_priority": optional_text(candidate.get("sector_research_priority")),
            "sector_leader_score": row_number(candidate, "sector_leader_score"),
            "sector_rotation": optional_text(candidate.get("sector_rotation")),
            "reason": f"業種{optional_text(candidate.get('sector_rotation'))} / リーダー{row_number(candidate, 'sector_leader_score'):.1f}点 / Run Health {health_status}",
            "blocked_reason": "",
        })
        planned_total += planned_value
        sector_used[sector] = sector_used.get(sector, 0.0) + planned_value
    return pd.DataFrame(rows, columns=PAPER_PLAN_COLUMNS)


def apply_paper_trade_plan(today: str, portfolio: pd.DataFrame, plan: pd.DataFrame) -> pd.DataFrame:
    active = portfolio.copy() if portfolio is not None else pd.DataFrame(columns=PAPER_POSITION_COLUMNS)
    if plan is None or plan.empty:
        return active[PAPER_POSITION_COLUMNS]
    new_rows: list[dict[str, Any]] = []
    for _, row in plan.iterrows():
        entry = row_number(row, "entry_reference_price")
        quantity = int(row_number(row, "quantity"))
        code = normalize_code(row.get("code"))
        new_rows.append({
            "position_id": f"{today}-{code}",
            "status": "OPEN",
            "code": code,
            "name": optional_text(row.get("name")),
            "sector33": optional_text(row.get("sector33")),
            "entry_date": today,
            "entry_price": entry,
            "quantity": quantity,
            "cost_basis": entry * quantity,
            "current_price": entry,
            "market_value": entry * quantity,
            "highest_close": entry,
            "stop_price": row_number(row, "stop_price"),
            "target_price": row_number(row, "target_price"),
            "trailing_stop_pct": 0.10,
            "holding_days": 0,
            "sector_research_priority": optional_text(row.get("sector_research_priority")),
            "sector_leader_score": row_number(row, "sector_leader_score"),
            "sector_rotation": optional_text(row.get("sector_rotation")),
            "unrealized_pnl": 0.0,
            "unrealized_return": 0.0,
        })
    combined = pd.concat([active, pd.DataFrame(new_rows)], ignore_index=True)
    combined = combined.drop_duplicates("position_id", keep="last")
    return combined[PAPER_POSITION_COLUMNS]


def append_paper_trade_history(history: pd.DataFrame, closed: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (history, closed) if frame is not None and not frame.empty]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PAPER_TRADE_HISTORY_COLUMNS)
    if not combined.empty:
        combined = combined.drop_duplicates("position_id", keep="last").sort_values(["exit_date", "position_id"])
    for column in PAPER_TRADE_HISTORY_COLUMNS:
        if column not in combined.columns:
            combined[column] = None
    return combined[PAPER_TRADE_HISTORY_COLUMNS]


def build_risk_budget(
    portfolio: pd.DataFrame,
    totals: dict[str, float],
    regime: dict[str, Any],
    run_health: pd.DataFrame,
) -> pd.DataFrame:
    health = run_health_overall(run_health)
    target_exposure = paper_target_exposure(optional_text(regime.get("label")), health)
    rows: list[dict[str, Any]] = []

    def add(kind: str, label: str, current: float, limit: float, detail: str) -> None:
        utilization = current / limit if limit > 0 else 0.0
        status = "PASS" if current <= limit + 1e-9 else "FAIL"
        rows.append({
            "budget_type": kind,
            "label": label,
            "current_value": current,
            "limit_value": limit,
            "utilization": utilization,
            "status": status,
            "detail": detail,
        })

    equity = totals.get("equity", PAPER_INITIAL_CAPITAL)
    add("portfolio", "投資比率", totals.get("exposure_ratio", 0.0), target_exposure, f"Market Regime {optional_text(regime.get('label'))} / Run Health {health}")
    add("portfolio", "保有銘柄数", float(len(portfolio)), float(PAPER_MAX_POSITIONS), "最大10銘柄")
    if portfolio is not None and not portfolio.empty and equity > 0:
        for sector, group in portfolio.groupby("sector33"):
            sector_value = float(pd.to_numeric(group["market_value"], errors="coerce").fillna(0).sum())
            add("sector", optional_text(sector) or "未分類", sector_value / equity, PAPER_MAX_SECTOR_WEIGHT, "1業種25%上限")
        for _, row in portfolio.iterrows():
            add("position", normalize_code(row.get("code")), row_number(row, "market_value") / equity, PAPER_MAX_POSITION_WEIGHT, "1銘柄12%上限")
    return pd.DataFrame(rows, columns=RISK_BUDGET_COLUMNS)


def update_paper_equity_history(
    path: str,
    today: str,
    totals: dict[str, float],
    portfolio: pd.DataFrame,
    trade_history: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    old = load_csv_with_columns(path, PAPER_EQUITY_COLUMNS)
    closed_count = len(trade_history) if trade_history is not None else 0
    wins = int((pd.to_numeric(trade_history.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0) > 0).sum()) if trade_history is not None and not trade_history.empty else 0
    win_rate = wins / closed_count if closed_count else None
    prior_peak = float(pd.to_numeric(old.get("equity", pd.Series(dtype=float)), errors="coerce").max()) if not old.empty else totals["equity"]
    peak = max(prior_peak, totals["equity"])
    drawdown = totals["equity"] / peak - 1 if peak > 0 else 0.0
    current = pd.DataFrame([{
        "date": today,
        **totals,
        "peak_equity": peak,
        "drawdown": drawdown,
        "open_positions": len(portfolio),
        "closed_trades": closed_count,
        "win_rate": win_rate,
    }], columns=PAPER_EQUITY_COLUMNS)
    combined = pd.concat([old, current], ignore_index=True).drop_duplicates("date", keep="last").sort_values("date")
    atomic_write_csv(combined, path)
    return combined, current.iloc[0].to_dict()


def run_paper_portfolio(
    today: str,
    all_ranked: pd.DataFrame,
    sector_leaders: pd.DataFrame,
    regime: dict[str, Any],
    run_health: pd.DataFrame,
    portfolio_path: str = "data/paper_portfolio.csv",
    trade_history_path: str = "data/paper_trade_history.csv",
    equity_history_path: str = "data/paper_equity_history.csv",
    initial_capital: float = PAPER_INITIAL_CAPITAL,
) -> dict[str, Any]:
    portfolio = load_csv_with_columns(portfolio_path, PAPER_POSITION_COLUMNS)
    trade_history = load_csv_with_columns(trade_history_path, PAPER_TRADE_HISTORY_COLUMNS)
    eligible_codes = set(
        sector_leaders[sector_leaders["sector_research_priority"].isin(["最優先", "優先"])]["code"].map(normalize_code)
    ) if sector_leaders is not None and not sector_leaders.empty else set()
    marked, closed_today = mark_paper_positions(today, portfolio, all_ranked, eligible_codes)
    trade_history = append_paper_trade_history(trade_history, closed_today)
    blocked_codes = set(closed_today.get("code", pd.Series(dtype=str)).map(normalize_code)) if not closed_today.empty else set()
    plan = build_paper_trade_plan(today, sector_leaders, marked, trade_history, regime, run_health, blocked_codes, initial_capital)
    portfolio = apply_paper_trade_plan(today, marked, plan)
    totals = paper_portfolio_totals(portfolio, trade_history, initial_capital)
    risk_budget = build_risk_budget(portfolio, totals, regime, run_health)
    equity_history, performance = update_paper_equity_history(equity_history_path, today, totals, portfolio, trade_history)
    atomic_write_csv(portfolio, portfolio_path)
    atomic_write_csv(trade_history, trade_history_path)
    return {
        "portfolio": portfolio,
        "plan": plan,
        "trade_history": trade_history,
        "risk_budget": risk_budget,
        "equity_history": equity_history,
        "performance": pd.DataFrame([performance]),
        "closed_today": closed_today,
    }


def plain_paper_portfolio_section(
    portfolio: pd.DataFrame,
    plan: pd.DataFrame,
    performance: pd.DataFrame,
    risk_budget: pd.DataFrame,
) -> list[str]:
    perf = {} if performance is None or performance.empty else performance.iloc[0].to_dict()
    lines = [
        "【ペーパーポートフォリオ】",
        "実注文は行いません。終値ベースの仮想検証で、売買推奨ではありません。",
        f"資産 {fmt_num(perf.get('equity'), 0)}円 / 現金 {fmt_num(perf.get('cash'), 0)}円 / 投資比率 {fmt_pct(perf.get('exposure_ratio'))}",
        f"実現損益 {fmt_num(perf.get('realized_pnl'), 0)}円 / 含み損益 {fmt_num(perf.get('unrealized_pnl'), 0)}円 / DD {fmt_pct(perf.get('drawdown'))}",
        f"保有 {len(portfolio) if portfolio is not None else 0}件 / 本日の新規計画 {len(plan) if plan is not None else 0}件",
    ]
    if plan is not None and not plan.empty:
        for _, row in plan.head(5).iterrows():
            lines.append(f"  OPEN {row['code']} {row['name']} / {int(row['quantity'])}株 / {fmt_price(row['entry_reference_price'])} / 損切 {fmt_price(row['stop_price'])} / 目標 {fmt_price(row['target_price'])}")
    failures = risk_budget[risk_budget["status"] == "FAIL"] if risk_budget is not None and not risk_budget.empty else pd.DataFrame()
    for _, row in failures.head(3).iterrows():
        lines.append(f"  リスク超過: {row['label']} {fmt_pct(row['current_value'])} > {fmt_pct(row['limit_value'])}")
    lines.append("")
    return lines


def html_paper_portfolio_section(
    portfolio: pd.DataFrame,
    plan: pd.DataFrame,
    performance: pd.DataFrame,
    risk_budget: pd.DataFrame,
) -> str:
    perf = {} if performance is None or performance.empty else performance.iloc[0].to_dict()
    failures = risk_budget[risk_budget["status"] == "FAIL"] if risk_budget is not None and not risk_budget.empty else pd.DataFrame()
    plan_items = "".join(
        f'<div style="border-top:1px solid #e5e7eb;padding:8px 0;font-size:11px;color:#334155"><b>OPEN {html_text(row["code"])} {html_text(row["name"])}</b> ・ {int(row["quantity"])}株 ・ {fmt_price(row["entry_reference_price"])} ・ 損切 {fmt_price(row["stop_price"])} ・ 目標 {fmt_price(row["target_price"])}</div>'
        for _, row in (plan.head(5).iterrows() if plan is not None and not plan.empty else [])
    )
    fail_html = "".join(
        f'<div style="font-size:11px;color:#b91c1c;margin-top:3px">リスク超過: {html_text(row["label"])} {fmt_pct(row["current_value"])} &gt; {fmt_pct(row["limit_value"])}</div>'
        for _, row in failures.head(3).iterrows()
    )
    return f'''<div style="background:#fff;border:2px solid #7c3aed;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#581c87">ペーパーポートフォリオ</div>
<div style="font-size:11px;color:#64748b;margin-top:4px">実注文は行わない終値ベースの仮想検証です。売買推奨ではありません。</div>
<div style="font-size:13px;color:#334155;margin-top:8px">資産 <b>{fmt_num(perf.get('equity'), 0)}円</b> ・ 現金 <b>{fmt_num(perf.get('cash'), 0)}円</b> ・ 投資比率 <b>{fmt_pct(perf.get('exposure_ratio'))}</b></div>
<div style="font-size:12px;color:#475569">実現損益 {fmt_num(perf.get('realized_pnl'), 0)}円 ・ 含み損益 {fmt_num(perf.get('unrealized_pnl'), 0)}円 ・ DD {fmt_pct(perf.get('drawdown'))}</div>
<div style="font-size:12px;font-weight:800;color:#334155;margin-top:6px">保有 {len(portfolio) if portfolio is not None else 0}件 ・ 本日の新規計画 {len(plan) if plan is not None else 0}件</div>{plan_items}{fail_html}</div>'''


def evaluate_market_data_freshness(
    today: str,
    all_ranked: pd.DataFrame,
    minimum_fresh_ratio: float = 0.95,
) -> dict[str, Any]:
    total_count = len(all_ranked) if all_ranked is not None else 0
    if all_ranked is None or all_ranked.empty or "price_date" not in all_ranked.columns:
        return {
            "status": "EMPTY",
            "latest_price_date": "",
            "fresh_count": 0,
            "total_count": total_count,
            "fresh_ratio": 0.0,
            "state_update_allowed": False,
            "detail": "株価日付を確認できないため状態更新を停止",
        }

    dates = pd.to_datetime(all_ranked["price_date"], errors="coerce")
    valid = dates.dropna()
    if valid.empty:
        return {
            "status": "EMPTY",
            "latest_price_date": "",
            "fresh_count": 0,
            "total_count": total_count,
            "fresh_ratio": 0.0,
            "state_update_allowed": False,
            "detail": "有効な株価日付がないため状態更新を停止",
        }

    target_date = pd.Timestamp(today).date()
    latest_date = valid.max().date()
    fresh_count = int((dates.dt.date == target_date).sum())
    fresh_ratio = fresh_count / total_count if total_count else 0.0
    if latest_date == target_date and fresh_ratio >= minimum_fresh_ratio:
        status = "FRESH"
        detail = "当日株価が十分に揃っているため状態更新を許可"
    elif latest_date == target_date and fresh_count > 0:
        status = "PARTIAL"
        detail = "当日株価の取得率が基準未満のため状態更新を停止"
    else:
        status = "STALE"
        detail = f"最新株価日 {latest_date.isoformat()} が実行日 {today} と一致しないため状態更新を停止"
    return {
        "status": status,
        "latest_price_date": latest_date.isoformat(),
        "fresh_count": fresh_count,
        "total_count": total_count,
        "fresh_ratio": fresh_ratio,
        "state_update_allowed": status == "FRESH",
        "detail": detail,
    }


def attach_market_data_freshness_health(
    run_health: pd.DataFrame,
    freshness: dict[str, Any],
) -> pd.DataFrame:
    columns = ["check_name", "status", "actual", "expected", "detail"]
    work = run_health.copy() if run_health is not None else pd.DataFrame(columns=columns)
    if not work.empty:
        work = work[~work["check_name"].isin(["overall", "market_data_current_day"])].copy()
    freshness_status = optional_text(freshness.get("status"))
    status = "PASS" if freshness_status == "FRESH" else "WARN" if freshness_status == "PARTIAL" else "FAIL"
    row = pd.DataFrame([{
        "check_name": "market_data_current_day",
        "status": status,
        "actual": f"{freshness_status} / {float(freshness.get('fresh_ratio', 0.0)):.1%}",
        "expected": "FRESH / >=95%",
        "detail": optional_text(freshness.get("detail")),
    }], columns=columns)
    work = pd.concat([work, row], ignore_index=True)
    statuses = work["status"].tolist()
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    overall_row = pd.DataFrame([{
        "check_name": "overall",
        "status": overall,
        "actual": overall,
        "expected": "PASS",
        "detail": f"PASS {statuses.count('PASS')} / WARN {statuses.count('WARN')} / FAIL {statuses.count('FAIL')}",
    }], columns=columns)
    return pd.concat([overall_row, work], ignore_index=True)


def load_existing_paper_state(
    regime: dict[str, Any],
    run_health: pd.DataFrame,
    initial_capital: float = PAPER_INITIAL_CAPITAL,
) -> dict[str, Any]:
    portfolio = load_csv_with_columns("data/paper_portfolio.csv", PAPER_POSITION_COLUMNS)
    trade_history = load_csv_with_columns("data/paper_trade_history.csv", PAPER_TRADE_HISTORY_COLUMNS)
    equity_history = load_csv_with_columns("data/paper_equity_history.csv", PAPER_EQUITY_COLUMNS)
    totals = paper_portfolio_totals(portfolio, trade_history, initial_capital)
    risk_budget = build_risk_budget(portfolio, totals, regime, run_health)
    if equity_history.empty:
        performance = pd.DataFrame([{
            "date": "",
            **totals,
            "peak_equity": totals["equity"],
            "drawdown": 0.0,
            "open_positions": len(portfolio),
            "closed_trades": len(trade_history),
            "win_rate": None,
        }], columns=PAPER_EQUITY_COLUMNS)
    else:
        performance = equity_history.tail(1).copy()
    return {
        "portfolio": portfolio,
        "plan": pd.DataFrame(columns=PAPER_PLAN_COLUMNS),
        "trade_history": trade_history,
        "risk_budget": risk_budget,
        "equity_history": equity_history,
        "performance": performance,
        "closed_today": pd.DataFrame(columns=PAPER_TRADE_HISTORY_COLUMNS),
    }


STATE_SCHEMA_VERSION = "1.0"
EXECUTION_MODE = "RESEARCH_AND_PAPER_ONLY"

RELEASE_READINESS_COLUMNS = [
    "release_status", "execution_mode", "criterion", "actual", "required",
    "passed", "blocking", "detail",
]

OPERATIONAL_ALERT_COLUMNS = [
    "severity", "category", "title", "status", "actual", "required", "action",
]

STATE_INVENTORY_COLUMNS = [
    "state_name", "path", "exists", "size_bytes", "row_count", "column_count",
    "modified_at", "sha256", "schema_version", "status",
]

STATE_SNAPSHOT_COLUMNS = [
    "snapshot_date", "state_name", "source_path", "snapshot_path", "status", "size_bytes", "sha256",
]

EXECUTION_AUDIT_COLUMNS = [
    "run_id", "date", "app_version", "execution_mode", "release_status", "run_health",
    "p0_alerts", "p1_alerts", "p2_alerts", "state_files_ok", "state_files_total",
    "snapshots_created", "manifest_sha256",
]


def sha256_file(path: str) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_shape(path: str) -> tuple[int | None, int | None]:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return (0, 0) if target.exists() else (None, None)
    try:
        frame = pd.read_csv(target)
        return len(frame), len(frame.columns)
    except Exception:
        return None, None


def build_state_inventory(state_paths: dict[str, str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name, raw_path in state_paths.items():
        target = Path(raw_path)
        exists = target.exists()
        row_count, column_count = csv_shape(str(target))
        size = target.stat().st_size if exists else 0
        if not exists:
            status = "MISSING"
        elif row_count is None:
            status = "UNREADABLE"
        elif size == 0:
            status = "EMPTY"
        else:
            status = "OK"
        rows.append({
            "state_name": name,
            "path": str(target),
            "exists": exists,
            "size_bytes": size,
            "row_count": row_count,
            "column_count": column_count,
            "modified_at": datetime.fromtimestamp(target.stat().st_mtime).isoformat(timespec="seconds") if exists else "",
            "sha256": sha256_file(str(target)),
            "schema_version": STATE_SCHEMA_VERSION,
            "status": status,
        })
    return pd.DataFrame(rows, columns=STATE_INVENTORY_COLUMNS)


def snapshot_state_files(
    today: str,
    state_paths: dict[str, str],
    snapshot_root: str = "data/state_snapshots",
) -> pd.DataFrame:
    destination = Path(snapshot_root) / today
    destination.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for name, raw_path in state_paths.items():
        source = Path(raw_path)
        suffix = source.suffix or ".dat"
        snapshot_path = destination / f"{name}{suffix}"
        if source.exists() and source.is_file():
            shutil.copy2(source, snapshot_path)
            status = "SNAPSHOT_CREATED"
            size = snapshot_path.stat().st_size
            checksum = sha256_file(str(snapshot_path))
        else:
            status = "SOURCE_MISSING"
            size = 0
            checksum = ""
        rows.append({
            "snapshot_date": today,
            "state_name": name,
            "source_path": str(source),
            "snapshot_path": str(snapshot_path),
            "status": status,
            "size_bytes": size,
            "sha256": checksum,
        })
    return pd.DataFrame(rows, columns=STATE_SNAPSHOT_COLUMNS)


def release_status_value(readiness: pd.DataFrame) -> str:
    if readiness is None or readiness.empty or "release_status" not in readiness.columns:
        return "UNKNOWN"
    return optional_text(readiness.iloc[0].get("release_status")) or "UNKNOWN"


def build_release_readiness(
    run_health: pd.DataFrame,
    signal_governance: pd.DataFrame,
    sector_leader_performance: pd.DataFrame,
    paper_performance: pd.DataFrame,
    paper_trade_history: pd.DataFrame,
    paper_risk_budget: pd.DataFrame,
) -> pd.DataFrame:
    health = run_health_overall(run_health)
    degradation_count = int((signal_governance.get("status", pd.Series(dtype=str)) == "劣化警戒").sum()) if signal_governance is not None and not signal_governance.empty else 0
    ten_day_stats = performance_overall_stats(sector_leader_performance, 10)
    leader_evidence = int(ten_day_stats.get("count", 0) or 0)
    paper_trades = len(paper_trade_history) if paper_trade_history is not None else 0
    perf = {} if paper_performance is None or paper_performance.empty else paper_performance.iloc[0].to_dict()
    paper_win_rate = perf.get("win_rate")
    paper_equity = float(perf.get("equity", PAPER_INITIAL_CAPITAL) or PAPER_INITIAL_CAPITAL)
    paper_return = paper_equity / PAPER_INITIAL_CAPITAL - 1
    paper_drawdown = float(perf.get("drawdown", 0.0) or 0.0)
    risk_failures = int((paper_risk_budget.get("status", pd.Series(dtype=str)) == "FAIL").sum()) if paper_risk_budget is not None and not paper_risk_budget.empty else 0

    criteria = [
        ("Run Health", health, "PASS", health == "PASS", True, "全データ品質ゲートがPASS"),
        ("シグナル劣化", degradation_count, "0件", degradation_count == 0, True, "Signal Governanceの劣化警戒がない"),
        ("業種リーダー10日実績", leader_evidence, "30件以上", leader_evidence >= 30, False, "十分なアウトオブサンプル実績"),
        ("ペーパー決済実績", paper_trades, "20件以上", paper_trades >= 20, False, "出口ルールを含む運用実績"),
        ("ペーパー勝率", paper_win_rate, "50%以上", paper_win_rate is not None and not pd.isna(paper_win_rate) and float(paper_win_rate) >= 0.50, False, "決済済み取引の勝率"),
        ("ペーパー累積収益", paper_return, "0%超", paper_return > 0, False, "仮想元本に対する累積収益"),
        ("最大ドローダウン", paper_drawdown, "-10%以上", paper_drawdown >= -0.10, True, "ピーク資産からの下落を10%以内に制御"),
        ("リスク予算超過", risk_failures, "0件", risk_failures == 0, True, "銘柄・業種・総投資比率の上限遵守"),
    ]
    blocking_failure = any(blocking and not passed for _, _, _, passed, blocking, _ in criteria)
    all_passed = all(passed for _, _, _, passed, _, _ in criteria)
    if blocking_failure:
        release_status = "HOLD"
    elif all_passed:
        release_status = "READY_FOR_MANUAL_REVIEW"
    elif paper_trades == 0:
        release_status = "RESEARCH"
    else:
        release_status = "PAPER_VALIDATION"
    rows = [{
        "release_status": release_status,
        "execution_mode": EXECUTION_MODE,
        "criterion": criterion,
        "actual": actual,
        "required": required,
        "passed": passed,
        "blocking": blocking,
        "detail": detail,
    } for criterion, actual, required, passed, blocking, detail in criteria]
    return pd.DataFrame(rows, columns=RELEASE_READINESS_COLUMNS)


def build_operational_alerts(readiness: pd.DataFrame) -> pd.DataFrame:
    if readiness is None or readiness.empty:
        return pd.DataFrame([{
            "severity": "P0",
            "category": "release",
            "title": "リリース判定を生成できません",
            "status": "OPEN",
            "actual": "UNKNOWN",
            "required": "readiness available",
            "action": "新規ペーパーエントリーを停止し、状態ファイルを確認",
        }], columns=OPERATIONAL_ALERT_COLUMNS)
    alerts: list[dict[str, Any]] = []
    for _, row in readiness[readiness["passed"] != True].iterrows():
        criterion = optional_text(row.get("criterion"))
        blocking = bool(row.get("blocking"))
        if criterion in {"Run Health", "最大ドローダウン", "リスク予算超過"}:
            severity = "P0"
            action = "新規ペーパーエントリーを停止し、原因解消までHOLD"
        elif criterion == "シグナル劣化":
            severity = "P1"
            action = "対象シグナルを縮小し、劣化原因をレビュー"
        elif blocking:
            severity = "P1"
            action = "ブロッキング条件を解消"
        else:
            severity = "P2"
            action = "実績を蓄積し、昇格条件を再評価"
        alerts.append({
            "severity": severity,
            "category": "release_readiness",
            "title": criterion,
            "status": "OPEN",
            "actual": row.get("actual"),
            "required": row.get("required"),
            "action": action,
        })
    if not alerts:
        alerts.append({
            "severity": "INFO",
            "category": "release_readiness",
            "title": "全昇格条件を充足",
            "status": "CLOSED",
            "actual": release_status_value(readiness),
            "required": "READY_FOR_MANUAL_REVIEW",
            "action": "手動レビューを実施。自動発注は引き続き無効",
        })
    order = {"P0": 0, "P1": 1, "P2": 2, "INFO": 3}
    result = pd.DataFrame(alerts, columns=OPERATIONAL_ALERT_COLUMNS)
    result["severity_order"] = result["severity"].map(order).fillna(9)
    return result.sort_values(["severity_order", "title"]).drop(columns=["severity_order"])


def build_execution_audit(
    today: str,
    readiness: pd.DataFrame,
    alerts: pd.DataFrame,
    inventory: pd.DataFrame,
    snapshots: pd.DataFrame,
    run_health: pd.DataFrame,
) -> pd.DataFrame:
    manifest_material = "|".join(
        inventory.sort_values("state_name").get("sha256", pd.Series(dtype=str)).fillna("").astype(str).tolist()
    )
    manifest_sha = hashlib.sha256(manifest_material.encode("utf-8")).hexdigest()
    return pd.DataFrame([{
        "run_id": f"{today}-{APP_VERSION}",
        "date": today,
        "app_version": APP_VERSION,
        "execution_mode": EXECUTION_MODE,
        "release_status": release_status_value(readiness),
        "run_health": run_health_overall(run_health),
        "p0_alerts": int((alerts.get("severity", pd.Series(dtype=str)) == "P0").sum()) if alerts is not None and not alerts.empty else 0,
        "p1_alerts": int((alerts.get("severity", pd.Series(dtype=str)) == "P1").sum()) if alerts is not None and not alerts.empty else 0,
        "p2_alerts": int((alerts.get("severity", pd.Series(dtype=str)) == "P2").sum()) if alerts is not None and not alerts.empty else 0,
        "state_files_ok": int((inventory.get("status", pd.Series(dtype=str)) == "OK").sum()) if inventory is not None and not inventory.empty else 0,
        "state_files_total": len(inventory) if inventory is not None else 0,
        "snapshots_created": int((snapshots.get("status", pd.Series(dtype=str)) == "SNAPSHOT_CREATED").sum()) if snapshots is not None and not snapshots.empty else 0,
        "manifest_sha256": manifest_sha,
    }], columns=EXECUTION_AUDIT_COLUMNS)


def append_execution_audit(path: str, current: pd.DataFrame) -> pd.DataFrame:
    old = load_csv_with_columns(path, EXECUTION_AUDIT_COLUMNS)
    frames = [frame for frame in (old, current) if frame is not None and not frame.empty]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=EXECUTION_AUDIT_COLUMNS)
    if not combined.empty:
        combined = combined.drop_duplicates("run_id", keep="last").sort_values(["date", "run_id"])
    atomic_write_csv(combined, path)
    return combined[EXECUTION_AUDIT_COLUMNS]


def plain_release_readiness_section(readiness: pd.DataFrame, alerts: pd.DataFrame, inventory: pd.DataFrame) -> list[str]:
    status = release_status_value(readiness)
    lines = [
        "【リリース準備状況】",
        f"判定: {status} / 実行モード: {EXECUTION_MODE}",
        "証券会社への自動発注は無効です。昇格は手動レビューまでです。",
    ]
    failed = readiness[readiness["passed"] != True] if readiness is not None and not readiness.empty else pd.DataFrame()
    for _, row in failed.head(5).iterrows():
        lines.append(f"  未達: {row['criterion']} / 実績 {row['actual']} / 条件 {row['required']}")
    if alerts is not None and not alerts.empty:
        counts = {severity: int((alerts["severity"] == severity).sum()) for severity in ["P0", "P1", "P2"]}
        lines.append(f"アラート: P0 {counts['P0']} / P1 {counts['P1']} / P2 {counts['P2']}")
    if inventory is not None and not inventory.empty:
        lines.append(f"状態ファイル: OK {int((inventory['status'] == 'OK').sum())}/{len(inventory)}")
    lines.append("")
    return lines


def html_release_readiness_section(readiness: pd.DataFrame, alerts: pd.DataFrame, inventory: pd.DataFrame) -> str:
    status = release_status_value(readiness)
    color = "#15803d" if status == "READY_FOR_MANUAL_REVIEW" else "#b91c1c" if status == "HOLD" else "#a16207"
    failed = readiness[readiness["passed"] != True] if readiness is not None and not readiness.empty else pd.DataFrame()
    items = "".join(
        f'<div style="font-size:11px;color:#b45309;margin-top:3px">未達: {html_text(row["criterion"])} ・ 実績 {html_text(row["actual"])} ・ 条件 {html_text(row["required"])}</div>'
        for _, row in failed.head(5).iterrows()
    )
    alert_text = ""
    if alerts is not None and not alerts.empty:
        alert_text = " / ".join(f'{severity} {int((alerts["severity"] == severity).sum())}' for severity in ["P0", "P1", "P2"])
    state_ok = int((inventory.get("status", pd.Series(dtype=str)) == "OK").sum()) if inventory is not None and not inventory.empty else 0
    state_total = len(inventory) if inventory is not None else 0
    return f'''<div style="background:#fff;border:2px solid {color};border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:{color}">リリース準備状況 <span style="float:right">{html_text(status)}</span></div>
<div style="clear:both;font-size:11px;color:#64748b;margin-top:5px">実行モード {html_text(EXECUTION_MODE)}。証券会社への自動発注は無効です。</div>
<div style="font-size:12px;color:#334155;margin-top:7px">アラート {html_text(alert_text)} ・ 状態ファイル OK {state_ok}/{state_total}</div>{items}</div>'''


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


def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, relative_strength: pd.DataFrame, relative_strength_lifecycle: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_signal_history: pd.DataFrame, sector_leader_outcomes: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_trade_history: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, release_readiness: pd.DataFrame, operational_alerts: pd.DataFrame, state_inventory: pd.DataFrame, state_snapshots: pd.DataFrame, execution_audit: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame([summary]).to_excel(w, sheet_name="Summary", index=False)
        top100.to_excel(w, sheet_name="Momentum Top100", index=False)
        relative_strength.to_excel(w, sheet_name="Relative Strength", index=False)
        relative_strength_lifecycle.to_excel(w, sheet_name="RS Lifecycle", index=False)
        sector_momentum.to_excel(w, sheet_name="Sector Momentum", index=False)
        sector_rotation.to_excel(w, sheet_name="Sector Rotation", index=False)
        sector_leaders.to_excel(w, sheet_name="Sector Leaders", index=False)
        sector_signal_history.to_excel(w, sheet_name="Sector Leader History", index=False)
        sector_leader_outcomes.to_excel(w, sheet_name="Sector Leader Outcomes", index=False)
        sector_leader_performance.to_excel(w, sheet_name="Sector Leader Performance", index=False)
        signal_governance.to_excel(w, sheet_name="Signal Governance", index=False)
        adaptive_thresholds.to_excel(w, sheet_name="Adaptive Thresholds", index=False)
        run_health.to_excel(w, sheet_name="Run Health", index=False)
        paper_portfolio.to_excel(w, sheet_name="Paper Portfolio", index=False)
        paper_trade_plan.to_excel(w, sheet_name="Paper Trade Plan", index=False)
        paper_trade_history.to_excel(w, sheet_name="Paper Trade History", index=False)
        paper_risk_budget.to_excel(w, sheet_name="Risk Budget", index=False)
        paper_performance.to_excel(w, sheet_name="Paper Performance", index=False)
        release_readiness.to_excel(w, sheet_name="Release Readiness", index=False)
        operational_alerts.to_excel(w, sheet_name="Operational Alerts", index=False)
        state_inventory.to_excel(w, sheet_name="State Inventory", index=False)
        state_snapshots.to_excel(w, sheet_name="State Snapshots", index=False)
        execution_audit.to_excel(w, sheet_name="Execution Audit", index=False)
        new_entries.to_excel(w, sheet_name="New Entries", index=False)
        rising_fast.to_excel(w, sheet_name="Rising Fast", index=False)
        top30_streak.to_excel(w, sheet_name="Top30 Streak", index=False)
        ytd_high_ranking.to_excel(w, sheet_name="YTD High Ranking", index=False)
        priority_changes.to_excel(w, sheet_name="Priority Changes", index=False)
        priority_lifecycle.to_excel(w, sheet_name="Priority Lifecycle", index=False)
        priority_expectancy.to_excel(w, sheet_name="Priority Expectancy", index=False)
        action_priority.to_excel(w, sheet_name="Action Priority", index=False)
        priority_performance.to_excel(w, sheet_name="Priority Performance", index=False)
        signal_performance.to_excel(w, sheet_name="Signal Performance", index=False)
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
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
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


def priority_labels_text(labels: Any) -> str:
    if isinstance(labels, (list, tuple, set)):
        return " / ".join(str(label) for label in labels if str(label).strip())
    return optional_text(labels)


def latest_previous_top100(history: pd.DataFrame, today: str, top_limit: int) -> tuple[pd.DataFrame, str]:
    if history is None or history.empty or "date" not in history.columns or "rank" not in history.columns:
        return pd.DataFrame(), ""
    work = history.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work["rank"] = pd.to_numeric(work["rank"], errors="coerce")
    work = work.dropna(subset=["date_sort", "rank"])
    work = work[work["date"].astype(str) != str(today)]
    if work.empty:
        return pd.DataFrame(), ""
    previous_date_value = work["date_sort"].max()
    previous = work[(work["date_sort"] == previous_date_value) & (work["rank"] <= top_limit)].copy()
    if previous.empty:
        return previous, previous_date_value.date().isoformat()
    previous["code"] = previous["code"].map(normalize_code)
    return previous.sort_values("rank"), previous_date_value.date().isoformat()


def optional_number(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def compare_priority_candidates(top100: pd.DataFrame, history: pd.DataFrame, today: str, top_limit: int) -> dict[str, Any]:
    current_top100 = top100.copy()
    if not current_top100.empty:
        current_top100["code"] = current_top100["code"].map(normalize_code)
    current = select_priority_candidates(current_top100, max(top_limit, len(current_top100))).copy()
    previous_top100, previous_date = latest_previous_top100(history, today, top_limit)
    previous = select_priority_candidates(previous_top100, max(top_limit, len(previous_top100))).copy() if not previous_top100.empty else pd.DataFrame()

    current_index = current.set_index("code", drop=False) if not current.empty else pd.DataFrame()
    previous_index = previous.set_index("code", drop=False) if not previous.empty else pd.DataFrame()
    top100_index = current_top100.set_index("code", drop=False) if not current_top100.empty else pd.DataFrame()
    current_codes = set(current["code"]) if not current.empty else set()
    previous_codes = set(previous["code"]) if not previous.empty else set()
    records: list[dict[str, Any]] = []

    for _, row in current.iterrows():
        code = normalize_code(row.get("code"))
        previous_row = previous_index.loc[code] if code in previous_codes else None
        current_labels = list(row.get("priority_labels", []))
        previous_labels = list(previous_row.get("priority_labels", [])) if previous_row is not None else []
        status = "継続" if previous_row is not None else "新規"
        records.append({
            "date": today,
            "previous_date": previous_date,
            "status": status,
            "code": code,
            "name": row.get("name", ""),
            "current_rank": optional_number(row.get("rank")),
            "previous_rank": optional_number(previous_row.get("rank")) if previous_row is not None else None,
            "current_score": optional_number(row.get("score")),
            "previous_score": optional_number(previous_row.get("score")) if previous_row is not None else None,
            "current_labels": priority_labels_text(current_labels),
            "previous_labels": priority_labels_text(previous_labels),
            "label_changed": bool(previous_row is not None and set(current_labels) != set(previous_labels)),
            "exit_reason": "",
            "return_20d": optional_number(row.get("return_20d")),
            "volume_ratio": optional_number(row.get("volume_ratio")),
            "trading_value": optional_number(row.get("trading_value")),
        })

    for _, row in previous.iterrows():
        code = normalize_code(row.get("code"))
        if code in current_codes:
            continue
        current_row = top100_index.loc[code] if not top100_index.empty and code in top100_index.index else None
        records.append({
            "date": today,
            "previous_date": previous_date,
            "status": "脱落",
            "code": code,
            "name": row.get("name", ""),
            "current_rank": optional_number(current_row.get("rank")) if current_row is not None else None,
            "previous_rank": optional_number(row.get("rank")),
            "current_score": optional_number(current_row.get("score")) if current_row is not None else None,
            "previous_score": optional_number(row.get("score")),
            "current_labels": priority_labels_text(priority_candidate_labels(current_row)) if current_row is not None else "",
            "previous_labels": priority_labels_text(row.get("priority_labels", [])),
            "label_changed": False,
            "exit_reason": "重点条件外" if current_row is not None else "Top100圏外",
            "return_20d": optional_number(current_row.get("return_20d")) if current_row is not None else optional_number(row.get("return_20d")),
            "volume_ratio": optional_number(current_row.get("volume_ratio")) if current_row is not None else optional_number(row.get("volume_ratio")),
            "trading_value": optional_number(current_row.get("trading_value")) if current_row is not None else optional_number(row.get("trading_value")),
        })

    columns = [
        "date", "previous_date", "status", "code", "name", "current_rank", "previous_rank",
        "current_score", "previous_score", "current_labels", "previous_labels", "label_changed",
        "exit_reason", "return_20d", "volume_ratio", "trading_value",
    ]
    table = pd.DataFrame(records, columns=columns)
    if not table.empty:
        status_order = pd.Categorical(table["status"], categories=["新規", "継続", "脱落"], ordered=True)
        table = table.assign(status_order=status_order).sort_values(
            ["status_order", "current_rank", "previous_rank"], na_position="last"
        ).drop(columns=["status_order"])

    new_rows = table[table["status"] == "新規"].copy() if not table.empty else table.copy()
    continued_rows = table[table["status"] == "継続"].copy() if not table.empty else table.copy()
    dropped_rows = table[table["status"] == "脱落"].copy() if not table.empty else table.copy()
    changed_rows = continued_rows[continued_rows["label_changed"] == True].copy() if not continued_rows.empty else continued_rows.copy()
    return {
        "previous_date": previous_date,
        "current": current,
        "table": table,
        "new": new_rows,
        "continued": continued_rows,
        "dropped": dropped_rows,
        "label_changed": changed_rows,
    }


def priority_lifecycle_status(streak_days: int, total_days: int, run_count: int) -> str:
    if total_days <= 1:
        return "初登場"
    if run_count >= 2 and streak_days == 1:
        return "再浮上"
    if streak_days >= 10:
        return "長期定着"
    if streak_days >= 5:
        return "定着"
    return "継続"


def priority_candidate_history_events(history: pd.DataFrame, top100: pd.DataFrame, today: str, top_limit: int) -> tuple[pd.DataFrame, list[str]]:
    frames: list[pd.DataFrame] = []
    report_dates: set[str] = {str(today)}

    if history is not None and not history.empty and {"date", "rank"}.issubset(history.columns):
        work = history.copy()
        work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
        work["rank"] = pd.to_numeric(work["rank"], errors="coerce")
        work = work.dropna(subset=["date_sort", "rank"])
        work = work[(work["date"].astype(str) != str(today)) & (work["rank"] <= top_limit)].copy()
        if not work.empty:
            work["date"] = work["date_sort"].dt.date.astype(str)
            work["code"] = work["code"].map(normalize_code)
            report_dates.update(work["date"].unique().tolist())
            for report_date, day_rows in work.groupby("date", sort=True):
                selected = select_priority_candidates(day_rows, max(top_limit, len(day_rows))).copy()
                if selected.empty:
                    continue
                selected["priority_date"] = str(report_date)
                frames.append(selected[["priority_date", "code"]].drop_duplicates())

    current = top100.copy()
    if not current.empty:
        current["code"] = current["code"].map(normalize_code)
        selected_current = select_priority_candidates(current, max(top_limit, len(current))).copy()
        if not selected_current.empty:
            selected_current["priority_date"] = str(today)
            frames.append(selected_current[["priority_date", "code"]].drop_duplicates())

    events = pd.concat(frames, ignore_index=True).drop_duplicates(["priority_date", "code"]) if frames else pd.DataFrame(columns=["priority_date", "code"])
    ordered_dates = sorted(report_dates, key=lambda value: pd.Timestamp(value))
    return events, ordered_dates


def calculate_priority_candidate_lifecycle(history: pd.DataFrame, top100: pd.DataFrame, today: str, top_limit: int) -> pd.DataFrame:
    current = select_priority_candidates(top100, max(top_limit, len(top100))).copy() if not top100.empty else pd.DataFrame()
    columns = [
        "code", "priority_first_date", "priority_last_date", "priority_streak_days",
        "priority_total_days", "priority_run_count", "priority_lifecycle_status",
    ]
    if current.empty:
        return pd.DataFrame(columns=columns)

    current["code"] = current["code"].map(normalize_code)
    events, report_dates = priority_candidate_history_events(history, top100, today, top_limit)
    event_dates_by_code = {
        code: set(group["priority_date"].astype(str))
        for code, group in events.groupby("code")
    }
    records: list[dict[str, Any]] = []
    for code in current["code"].drop_duplicates():
        qualified_dates = event_dates_by_code.get(code, {str(today)})
        ordered_qualified = sorted(qualified_dates, key=lambda value: pd.Timestamp(value))
        streak_days = 0
        for report_date in reversed(report_dates):
            if report_date in qualified_dates:
                streak_days += 1
            else:
                break

        run_count = 0
        active = False
        for report_date in report_dates:
            qualified = report_date in qualified_dates
            if qualified and not active:
                run_count += 1
            active = qualified

        total_days = len(ordered_qualified)
        records.append({
            "code": code,
            "priority_first_date": ordered_qualified[0],
            "priority_last_date": ordered_qualified[-1],
            "priority_streak_days": streak_days,
            "priority_total_days": total_days,
            "priority_run_count": run_count,
            "priority_lifecycle_status": priority_lifecycle_status(streak_days, total_days, run_count),
        })
    return pd.DataFrame(records, columns=columns)


def attach_priority_candidate_lifecycle(changes: dict[str, Any], history: pd.DataFrame, top100: pd.DataFrame, today: str, top_limit: int) -> dict[str, Any]:
    enriched = dict(changes)
    lifecycle = calculate_priority_candidate_lifecycle(history, top100, today, top_limit)
    current = changes.get("current", pd.DataFrame()).copy()
    if not current.empty:
        current["code"] = current["code"].map(normalize_code)
        current = current.merge(lifecycle, on="code", how="left")
        current = current.sort_values(
            ["priority_signal_count", "score", "trading_value", "rank"],
            ascending=[False, False, False, True],
        )

    table = changes.get("table", pd.DataFrame()).copy()
    if not table.empty:
        table["code"] = table["code"].map(normalize_code)
        table = table.merge(lifecycle, on="code", how="left")

    enriched["current"] = current
    enriched["lifecycle"] = current.sort_values(
        ["priority_streak_days", "priority_total_days", "rank"],
        ascending=[False, False, True],
        na_position="last",
    ) if not current.empty else current.copy()
    enriched["table"] = table
    enriched["new"] = table[table["status"] == "新規"].copy() if not table.empty else table.copy()
    enriched["continued"] = table[table["status"] == "継続"].copy() if not table.empty else table.copy()
    enriched["dropped"] = table[table["status"] == "脱落"].copy() if not table.empty else table.copy()
    enriched["label_changed"] = enriched["continued"][enriched["continued"]["label_changed"] == True].copy() if not enriched["continued"].empty else enriched["continued"].copy()
    return enriched


def priority_lifecycle_count(changes: dict[str, Any], status: str) -> int:
    lifecycle = changes.get("lifecycle", pd.DataFrame())
    if lifecycle is None or lifecycle.empty or "priority_lifecycle_status" not in lifecycle.columns:
        return 0
    return int((lifecycle["priority_lifecycle_status"] == status).sum())


def priority_lifecycle_summary(priority: pd.DataFrame) -> str:
    if priority.empty or "priority_lifecycle_status" not in priority.columns:
        return ""
    order = ["初登場", "再浮上", "継続", "定着", "長期定着"]
    counts = priority["priority_lifecycle_status"].value_counts()
    parts = [f"{status} {int(counts.get(status, 0))}件" for status in order if int(counts.get(status, 0)) > 0]
    return " / ".join(parts)


def priority_lifecycle_detail(row: pd.Series) -> str:
    status = optional_text(row.get("priority_lifecycle_status"))
    first_date = optional_text(row.get("priority_first_date"))
    streak = optional_number(row.get("priority_streak_days"))
    total = optional_number(row.get("priority_total_days"))
    if not status:
        return ""
    return f"{status} / 初回 {first_date or '-'} / 連続 {int(streak or 0)}営業日 / 累計 {int(total or 0)}日"


def priority_change_count(changes: dict[str, Any], key: str) -> int:
    value = changes.get(key)
    return len(value) if isinstance(value, pd.DataFrame) else 0


def priority_rank_label(value: Any, prefix: str = "#") -> str:
    number = optional_number(value)
    return "-" if number is None else f"{prefix}{int(number)}"


def plain_priority_changes_section(changes: dict[str, Any]) -> list[str]:
    previous_date = optional_text(changes.get("previous_date"))
    if not previous_date:
        return ["【重点候補の変化】", "前回のランキング履歴がないため、本日から比較を開始します。", ""]
    new_rows = changes.get("new", pd.DataFrame())
    continued_rows = changes.get("continued", pd.DataFrame())
    dropped_rows = changes.get("dropped", pd.DataFrame())
    changed_rows = changes.get("label_changed", pd.DataFrame())
    lines = [
        "【重点候補の変化】",
        f"比較日 {previous_date} / 新規 {len(new_rows)}件 / 継続 {len(continued_rows)}件 / 脱落 {len(dropped_rows)}件 / タグ変化 {len(changed_rows)}件",
    ]
    if not new_rows.empty:
        lines.append("■ 今日から重点候補")
        for _, row in new_rows.head(5).iterrows():
            lines.append(f"{priority_rank_label(row.get('current_rank'))} {row['code']} {row['name']}｜{row['current_labels']}｜{fmt_int(row.get('current_score'))}点")
    if not changed_rows.empty:
        lines.append("■ タグ変化")
        for _, row in changed_rows.head(5).iterrows():
            lines.append(f"{priority_rank_label(row.get('current_rank'))} {row['code']} {row['name']}｜{row['previous_labels']} → {row['current_labels']}")
    if not dropped_rows.empty:
        lines.append("■ 重点候補から脱落")
        for _, row in dropped_rows.head(5).iterrows():
            current_rank = priority_rank_label(row.get("current_rank"))
            current_text = f"現在{current_rank}" if current_rank != "-" else "現在Top100圏外"
            lines.append(f"前回{priority_rank_label(row.get('previous_rank'))} {row['code']} {row['name']}｜{row['previous_labels']}｜{row['exit_reason']}（{current_text}）")
    lines.append("")
    return lines


def html_priority_changes_section(changes: dict[str, Any]) -> str:
    previous_date = optional_text(changes.get("previous_date"))
    if not previous_date:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:14px;margin-top:14px"><b>重点候補の変化</b><div style="font-size:12px;color:#64748b;margin-top:5px">前回履歴がないため、本日から比較を開始します。</div></div>'
    new_rows = changes.get("new", pd.DataFrame())
    continued_rows = changes.get("continued", pd.DataFrame())
    dropped_rows = changes.get("dropped", pd.DataFrame())
    changed_rows = changes.get("label_changed", pd.DataFrame())

    def rows_html(df: pd.DataFrame, kind: str) -> str:
        parts = []
        for _, row in df.head(5).iterrows():
            if kind == "new":
                title = f"{priority_rank_label(row.get('current_rank'))} {row['code']} {row['name']}"
                detail = f"{row['current_labels']} ・ {fmt_int(row.get('current_score'))}点"
                color = "#15803d"
            elif kind == "changed":
                title = f"{priority_rank_label(row.get('current_rank'))} {row['code']} {row['name']}"
                detail = f"{row['previous_labels']} → {row['current_labels']}"
                color = "#2563eb"
            else:
                title = f"前回{priority_rank_label(row.get('previous_rank'))} {row['code']} {row['name']}"
                current_rank = priority_rank_label(row.get("current_rank"))
                current_text = f"現在{current_rank}" if current_rank != "-" else "現在Top100圏外"
                detail = f"{row['previous_labels']} ・ {row['exit_reason']}（{current_text}）"
                color = "#b91c1c"
            parts.append(f'<div style="border-top:1px solid #e5e7eb;padding:8px 0"><div style="font-size:13px;font-weight:800;color:{color}">{html_text(title)}</div><div style="font-size:11px;line-height:1.6;color:#475569">{html_text(detail)}</div></div>')
        return "".join(parts)

    groups = []
    if not new_rows.empty:
        groups.append(f'<div style="font-size:12px;font-weight:900;color:#15803d;margin-top:10px">今日から重点候補</div>{rows_html(new_rows, "new")}')
    if not changed_rows.empty:
        groups.append(f'<div style="font-size:12px;font-weight:900;color:#2563eb;margin-top:10px">タグ変化</div>{rows_html(changed_rows, "changed")}')
    if not dropped_rows.empty:
        groups.append(f'<div style="font-size:12px;font-weight:900;color:#b91c1c;margin-top:10px">重点候補から脱落</div>{rows_html(dropped_rows, "dropped")}')
    return f'''<div style="background:#fff;border:1px solid #cbd5e1;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:17px;font-weight:900;color:#0f172a">重点候補の変化</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">比較日 {html_text(previous_date)} ・ 新規 {len(new_rows)}件 ・ 継続 {len(continued_rows)}件 ・ 脱落 {len(dropped_rows)}件 ・ タグ変化 {len(changed_rows)}件</div>
{"".join(groups)}
</div>'''


def combined_ranking_history(history: pd.DataFrame, current: pd.DataFrame, today: str) -> pd.DataFrame:
    frames = []
    if history is not None and not history.empty:
        old = history.copy()
        if "date" in old.columns:
            old = old[old["date"].astype(str) != str(today)]
        frames.append(old)
    if current is not None and not current.empty:
        frames.append(current.copy())
    if not frames:
        return pd.DataFrame(columns=ranking_history_columns())
    out = pd.concat(frames, ignore_index=True)
    out["code"] = out["code"].map(normalize_code)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date.astype(str)
    out["rank"] = pd.to_numeric(out["rank"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    return out.dropna(subset=["date", "rank", "close", "code"]).drop_duplicates(["date", "code"], keep="last")


def calculate_priority_performance(history: pd.DataFrame, top_limit: int, horizons: tuple[int, ...] = (5, 10, 20)) -> pd.DataFrame:
    columns = [
        "signal_date", "code", "name", "signal_rank", "signal_score", "signal_close", "signal_labels",
        *[f"target_date_{h}d" for h in horizons],
        *[f"return_{h}d_after" for h in horizons],
        "max_return_20d_after", "min_return_20d_after", "observed_report_days",
    ]
    if history is None or history.empty:
        return pd.DataFrame(columns=columns)

    work = history.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work["rank"] = pd.to_numeric(work["rank"], errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["date_sort", "rank", "close", "code"])
    if work.empty:
        return pd.DataFrame(columns=columns)
    work["date"] = work["date_sort"].dt.date.astype(str)
    work["code"] = work["code"].map(normalize_code)
    dates = sorted(work["date"].unique(), key=pd.Timestamp)
    date_index = {date: index for index, date in enumerate(dates)}
    price_lookup = work.set_index(["date", "code"])["close"].to_dict()
    rows: list[dict[str, Any]] = []

    for signal_date, day_rows in work.groupby("date", sort=True):
        top100 = day_rows[day_rows["rank"] <= top_limit].copy()
        selected = select_priority_candidates(top100, max(top_limit, len(top100)))
        if selected.empty:
            continue
        start_index = date_index[signal_date]
        for _, signal in selected.iterrows():
            code = normalize_code(signal.get("code"))
            entry_close = float(signal["close"])
            labels = priority_labels_text(signal.get("priority_labels", []))
            record: dict[str, Any] = {
                "signal_date": signal_date,
                "code": code,
                "name": signal.get("name", ""),
                "signal_rank": int(signal.get("rank", 0)),
                "signal_score": float(signal.get("score", 0)),
                "signal_close": entry_close,
                "signal_labels": labels,
            }
            observed_returns: list[float] = []
            max_horizon = max(horizons)
            for offset in range(1, min(max_horizon, len(dates) - start_index - 1) + 1):
                future_date = dates[start_index + offset]
                future_close = price_lookup.get((future_date, code))
                if future_close is not None and entry_close:
                    observed_returns.append(float(future_close) / entry_close - 1)
            for horizon in horizons:
                if start_index + horizon < len(dates):
                    target_date = dates[start_index + horizon]
                    target_close = price_lookup.get((target_date, code))
                else:
                    target_date = None
                    target_close = None
                record[f"target_date_{horizon}d"] = target_date
                record[f"return_{horizon}d_after"] = (float(target_close) / entry_close - 1) if target_close is not None and entry_close else None
            record["max_return_20d_after"] = max(observed_returns) if observed_returns else None
            record["min_return_20d_after"] = min(observed_returns) if observed_returns else None
            record["observed_report_days"] = len(observed_returns)
            rows.append(record)
    return pd.DataFrame(rows, columns=columns)


def performance_stats(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {"count": 0, "win_rate": None, "average": None, "median": None, "best": None, "worst": None}
    return {
        "count": int(len(clean)),
        "win_rate": float((clean > 0).mean()),
        "average": float(clean.mean()),
        "median": float(clean.median()),
        "best": float(clean.max()),
        "worst": float(clean.min()),
    }


def build_signal_performance_summary(performance: pd.DataFrame, horizons: tuple[int, ...] = (5, 10, 20)) -> pd.DataFrame:
    columns = ["group", "horizon", "count", "win_rate", "average_return", "median_return", "best_return", "worst_return"]
    if performance is None or performance.empty:
        return pd.DataFrame(columns=columns)
    groups: list[tuple[str, pd.DataFrame]] = [("全重点候補", performance)]
    label_values = sorted({label.strip() for value in performance["signal_labels"].fillna("") for label in str(value).split("/") if label.strip()})
    for label in label_values:
        mask = performance["signal_labels"].fillna("").map(lambda value: label in [item.strip() for item in str(value).split("/")])
        groups.append((label, performance[mask].copy()))
    records = []
    for group_name, group_df in groups:
        for horizon in horizons:
            stats = performance_stats(group_df[f"return_{horizon}d_after"])
            records.append({
                "group": group_name,
                "horizon": horizon,
                "count": stats["count"],
                "win_rate": stats["win_rate"],
                "average_return": stats["average"],
                "median_return": stats["median"],
                "best_return": stats["best"],
                "worst_return": stats["worst"],
            })
    return pd.DataFrame(records, columns=columns)


def overall_performance_stats(summary: pd.DataFrame, horizon: int) -> dict[str, Any]:
    if summary is None or summary.empty:
        return {"count": 0, "win_rate": None, "average_return": None, "median_return": None, "best_return": None, "worst_return": None}
    rows = summary[(summary["group"] == "全重点候補") & (summary["horizon"] == horizon)]
    return rows.iloc[0].to_dict() if not rows.empty else {"count": 0, "win_rate": None, "average_return": None, "median_return": None, "best_return": None, "worst_return": None}


def best_signal_groups(summary: pd.DataFrame, horizon: int = 20, minimum_count: int = 3, limit: int = 3) -> pd.DataFrame:
    if summary is None or summary.empty:
        return pd.DataFrame(columns=summary.columns if summary is not None else [])
    rows = summary[(summary["group"] != "全重点候補") & (summary["horizon"] == horizon) & (summary["count"] >= minimum_count)].copy()
    return rows.sort_values(["average_return", "win_rate", "count"], ascending=[False, False, False]).head(limit)


def fmt_optional_pct(value: Any) -> str:
    return "-" if value is None or pd.isna(value) else fmt_pct(value)


def plain_performance_scorecard(summary: pd.DataFrame) -> list[str]:
    if summary is None or summary.empty:
        return ["【シグナル実績】", "履歴不足のため、実績集計を開始します。", ""]
    lines = ["【シグナル実績】"]
    for horizon in (5, 10, 20):
        stats = overall_performance_stats(summary, horizon)
        lines.append(
            f"{horizon}日後｜件数 {int(stats.get('count', 0) or 0)}｜勝率 {fmt_optional_pct(stats.get('win_rate'))}｜平均 {fmt_optional_pct(stats.get('average_return'))}｜中央値 {fmt_optional_pct(stats.get('median_return'))}"
        )
    best = best_signal_groups(summary)
    if not best.empty:
        lines.append("期待値上位タグ（20日後・3件以上）")
        for _, row in best.iterrows():
            lines.append(f"{row['group']}｜{int(row['count'])}件｜勝率 {fmt_optional_pct(row['win_rate'])}｜平均 {fmt_optional_pct(row['average_return'])}")
    lines.append("")
    return lines


def html_performance_scorecard(summary: pd.DataFrame) -> str:
    if summary is None or summary.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>シグナル実績</b><div style="font-size:12px;color:#64748b;margin-top:5px">履歴不足のため、実績集計を開始します。</div></div>'
    horizon_rows = []
    for horizon in (5, 10, 20):
        stats = overall_performance_stats(summary, horizon)
        horizon_rows.append(f'<div style="border-top:1px solid #e5e7eb;padding:8px 0;font-size:12px;color:#334155"><b>{horizon}日後</b> ・ {int(stats.get("count", 0) or 0)}件 ・ 勝率 <b>{fmt_optional_pct(stats.get("win_rate"))}</b> ・ 平均 <b>{fmt_optional_pct(stats.get("average_return"))}</b> ・ 中央値 {fmt_optional_pct(stats.get("median_return"))}</div>')
    best = best_signal_groups(summary)
    best_html = ""
    if not best.empty:
        items = "".join(f'<div style="font-size:12px;color:#475569;padding:3px 0">{html_text(row["group"])} ・ {int(row["count"])}件 ・ 勝率 {fmt_optional_pct(row["win_rate"])} ・ 平均 {fmt_optional_pct(row["average_return"])}</div>' for _, row in best.iterrows())
        best_html = f'<div style="font-size:12px;font-weight:900;color:#7c3aed;margin-top:8px">期待値上位タグ（20日後・3件以上）</div>{items}'
    return f'<div style="background:#fff;border:2px solid #7c3aed;border-radius:18px;padding:16px;margin-top:14px"><div style="font-size:18px;font-weight:900;color:#4c1d95">シグナル実績</div>{"".join(horizon_rows)}{best_html}</div>'


def expectancy_confidence(sample_count: int) -> str:
    if sample_count >= 20:
        return "高"
    if sample_count >= 8:
        return "中"
    if sample_count >= 3:
        return "低"
    return "蓄積中"


def build_tag_expectancy(signal_performance: pd.DataFrame, prior_strength: int = 5) -> pd.DataFrame:
    columns = [
        "tag", "expectancy_score", "expected_return", "expected_win_rate",
        "evidence_count", "confidence", "available_horizons",
    ]
    if signal_performance is None or signal_performance.empty:
        return pd.DataFrame(columns=columns)

    weights = {5: 0.20, 10: 0.30, 20: 0.50}
    overall = {
        int(row["horizon"]): row
        for _, row in signal_performance[signal_performance["group"] == "全重点候補"].iterrows()
    }
    records = []
    for tag, rows in signal_performance[signal_performance["group"] != "全重点候補"].groupby("group"):
        horizon_values = []
        counts = []
        for _, row in rows.iterrows():
            horizon = int(row["horizon"])
            count = int(row.get("count", 0) or 0)
            average = optional_number(row.get("average_return"))
            win_rate = optional_number(row.get("win_rate"))
            prior_row = overall.get(horizon)
            prior_average = optional_number(prior_row.get("average_return")) if prior_row is not None else 0.0
            prior_win = optional_number(prior_row.get("win_rate")) if prior_row is not None else 0.5
            if count <= 0 or average is None or win_rate is None:
                continue
            prior_average = 0.0 if prior_average is None else prior_average
            prior_win = 0.5 if prior_win is None else prior_win
            shrunk_average = (count * average + prior_strength * prior_average) / (count + prior_strength)
            shrunk_win = (count * win_rate + prior_strength * prior_win) / (count + prior_strength)
            horizon_score = max(0.0, min(100.0, 50.0 + shrunk_average * 200.0 + (shrunk_win - 0.5) * 30.0))
            horizon_values.append((weights.get(horizon, 0.0), horizon_score, shrunk_average, shrunk_win, horizon))
            counts.append(count)
        if not horizon_values:
            continue
        total_weight = sum(value[0] for value in horizon_values) or 1.0
        score = sum(value[0] * value[1] for value in horizon_values) / total_weight
        expected_return = sum(value[0] * value[2] for value in horizon_values) / total_weight
        expected_win = sum(value[0] * value[3] for value in horizon_values) / total_weight
        evidence_count = max(counts)
        records.append({
            "tag": str(tag),
            "expectancy_score": round(score, 1),
            "expected_return": expected_return,
            "expected_win_rate": expected_win,
            "evidence_count": evidence_count,
            "confidence": expectancy_confidence(evidence_count),
            "available_horizons": ",".join(str(value[4]) for value in horizon_values),
        })
    return pd.DataFrame(records, columns=columns)


def candidate_expectancy_values(labels: Any, tag_expectancy: pd.DataFrame) -> dict[str, Any]:
    label_list = list(labels) if isinstance(labels, (list, tuple, set)) else [item.strip() for item in str(labels or "").split("/") if item.strip()]
    if tag_expectancy is None or tag_expectancy.empty:
        return {
            "expectancy_score": 50.0,
            "expectancy_expected_return": None,
            "expectancy_win_rate": None,
            "expectancy_evidence_count": 0,
            "expectancy_confidence": "蓄積中",
            "expectancy_tags": "",
        }
    matched = tag_expectancy[tag_expectancy["tag"].isin(label_list)].copy()
    if matched.empty:
        return {
            "expectancy_score": 50.0,
            "expectancy_expected_return": None,
            "expectancy_win_rate": None,
            "expectancy_evidence_count": 0,
            "expectancy_confidence": "蓄積中",
            "expectancy_tags": "",
        }
    matched["blend_weight"] = matched["evidence_count"].clip(lower=1, upper=20)
    total_weight = float(matched["blend_weight"].sum()) or 1.0
    score = float((matched["expectancy_score"] * matched["blend_weight"]).sum() / total_weight)
    expected_return = float((matched["expected_return"] * matched["blend_weight"]).sum() / total_weight)
    win_rate = float((matched["expected_win_rate"] * matched["blend_weight"]).sum() / total_weight)
    evidence_count = int(matched["evidence_count"].max())
    return {
        "expectancy_score": round(score, 1),
        "expectancy_expected_return": expected_return,
        "expectancy_win_rate": win_rate,
        "expectancy_evidence_count": evidence_count,
        "expectancy_confidence": expectancy_confidence(evidence_count),
        "expectancy_tags": " / ".join(matched.sort_values("expectancy_score", ascending=False)["tag"].astype(str)),
    }


def attach_priority_expectancy(changes: dict[str, Any], signal_performance: pd.DataFrame) -> dict[str, Any]:
    enriched = dict(changes)
    tag_expectancy = build_tag_expectancy(signal_performance)
    current = changes.get("current", pd.DataFrame()).copy()
    expectancy_columns = [
        "expectancy_score", "expectancy_expected_return", "expectancy_win_rate",
        "expectancy_evidence_count", "expectancy_confidence", "expectancy_tags",
    ]
    if not current.empty:
        expectancy_rows = current.apply(lambda row: pd.Series(candidate_expectancy_values(row.get("priority_labels", []), tag_expectancy)), axis=1)
        for column in expectancy_columns:
            current[column] = expectancy_rows[column].values
        current["expectancy_has_evidence"] = current["expectancy_evidence_count"] >= 3
        current = current.sort_values(
            ["expectancy_has_evidence", "expectancy_score", "priority_signal_count", "score", "rank"],
            ascending=[False, False, False, False, True],
        )

    table = changes.get("table", pd.DataFrame()).copy()
    lifecycle = changes.get("lifecycle", pd.DataFrame()).copy()
    merge_columns = ["code", *expectancy_columns]
    expectancy_by_code = current[merge_columns].drop_duplicates("code") if not current.empty else pd.DataFrame(columns=merge_columns)
    for frame_name, frame in (("table", table), ("lifecycle", lifecycle)):
        if not frame.empty:
            frame = frame.drop(columns=[column for column in expectancy_columns if column in frame.columns], errors="ignore")
            frame = frame.merge(expectancy_by_code, on="code", how="left")
        enriched[frame_name] = frame
    enriched["current"] = current
    enriched["tag_expectancy"] = tag_expectancy
    enriched["expectancy"] = current.copy()
    return enriched


ACTION_PRIORITY_ORDER = {"A": 0, "B": 1, "C": 2, "見送り": 3}


def action_priority_values(row: pd.Series, regime: dict[str, Any]) -> dict[str, Any]:
    """Assign a transparent research priority without changing Momentum ranking."""
    expectancy_score = row_number(row, "expectancy_score", 50.0)
    evidence_count = int(row_number(row, "expectancy_evidence_count", 0.0))
    confidence = optional_text(row.get("expectancy_confidence")) or "蓄積中"
    momentum_score = row_number(row, "score")
    momentum_rank = int(row_number(row, "rank", 999.0))
    trading_value = row_number(row, "trading_value")
    volume_ratio = row_number(row, "volume_ratio")
    ma20_deviation = row_number(row, "ma20_deviation")
    labels = list(row.get("priority_labels", [])) if isinstance(row.get("priority_labels", []), (list, tuple, set)) else [item.strip() for item in str(row.get("priority_labels", "")).split("/") if item.strip()]
    lifecycle = optional_text(row.get("priority_lifecycle_status"))
    lifecycle_streak = int(row_number(row, "priority_streak_days", 0.0))
    regime_label = optional_text(regime.get("label")) or "中立"

    positive: list[str] = []
    cautions: list[str] = []
    action_score = 0.0

    if evidence_count >= 3:
        if expectancy_score >= 80:
            action_score += 30
        elif expectancy_score >= 70:
            action_score += 25
        elif expectancy_score >= 60:
            action_score += 18
        elif expectancy_score >= 50:
            action_score += 10
        else:
            action_score += 3
        positive.append(f"期待値{expectancy_score:.1f}点")
    else:
        action_score += 5
        cautions.append(f"期待値の実績蓄積中（{evidence_count}件）")

    confidence_points = {"高": 15, "中": 10, "低": 5, "蓄積中": 0}
    action_score += confidence_points.get(confidence, 0)
    if confidence in {"高", "中", "低"}:
        positive.append(f"信頼度 {confidence}")

    if momentum_score >= 85:
        action_score += 15
    elif momentum_score >= 75:
        action_score += 12
    elif momentum_score >= 65:
        action_score += 8
    elif momentum_score >= 60:
        action_score += 5
    if momentum_score >= 75:
        positive.append(f"Momentum {int(momentum_score)}点")

    if momentum_rank <= 10:
        action_score += 10
        positive.append("Momentum上位10位")
    elif momentum_rank <= 30:
        action_score += 7
        positive.append("Momentum上位30位")
    elif momentum_rank <= 60:
        action_score += 4
    else:
        action_score += 1

    if trading_value >= 5_000_000_000:
        action_score += 12
        liquidity_check = "流動性十分（売買代金50億円以上）"
        positive.append("売買代金50億円以上")
    elif trading_value >= 1_000_000_000:
        action_score += 9
        liquidity_check = "流動性良好（売買代金10億円以上）"
        positive.append("売買代金10億円以上")
    elif trading_value >= 300_000_000:
        action_score += 6
        liquidity_check = "流動性確認済み（売買代金3億円以上）"
    elif trading_value >= 100_000_000:
        action_score += 3
        liquidity_check = "最低流動性基準を充足"
    elif trading_value >= 50_000_000:
        action_score -= 12
        liquidity_check = "流動性不足（売買代金1億円未満）"
        cautions.append(liquidity_check)
    else:
        action_score -= 25
        liquidity_check = "流動性不足（売買代金5,000万円未満）"
        cautions.append(liquidity_check)

    if volume_ratio >= 3.0:
        action_score += 7
        positive.append(f"出来高{volume_ratio:.1f}倍")
    elif volume_ratio >= 2.0:
        action_score += 5
        positive.append(f"出来高{volume_ratio:.1f}倍")
    elif volume_ratio >= 1.5:
        action_score += 3
    elif volume_ratio < 1.0:
        cautions.append("出来高倍率1倍未満")

    bullish = regime_label in {"強気", "やや強気"}
    defensive = regime_label in {"中立", "弱気"}
    if bullish:
        tag_points = {"初動": 6, "加速": 7, "継続": 4, "大型資金": 7}
    elif defensive:
        tag_points = {"初動": 2, "加速": 2, "継続": 7, "大型資金": 6}
    else:
        tag_points = {"初動": 1, "加速": 2, "継続": 5, "大型資金": 5}
    for label in labels:
        if label == "過熱注意":
            continue
        action_score += tag_points.get(label, 0)
        if label in {"初動", "加速", "継続", "大型資金"}:
            positive.append(label)
    if bullish and any(label in labels for label in {"初動", "加速"}):
        positive.append(f"{regime_label}相場の初動・加速候補")
    if defensive and any(label in labels for label in {"継続", "大型資金"}):
        positive.append(f"{regime_label}相場の継続候補")

    lifecycle_points = {"長期定着": 8, "定着": 5, "継続": 3, "再浮上": 2, "初登場": 3 if bullish else 0}
    action_score += lifecycle_points.get(lifecycle, 0)
    if lifecycle in {"長期定着", "定着"}:
        positive.append(lifecycle)
    elif lifecycle == "継続" and lifecycle_streak > 0:
        positive.append(f"継続{lifecycle_streak}日")
    elif lifecycle == "初登場" and not bullish:
        cautions.append("初登場のため継続確認が必要")

    if regime_label == "過熱警戒":
        action_score -= 5
        cautions.append("過熱警戒相場")
    elif regime_label == "弱気":
        cautions.append("弱気相場のため選別を厳格化")

    if ma20_deviation >= 0.25:
        action_score -= 12
        cautions.append(f"20日線乖離{ma20_deviation:.1%}")
    elif ma20_deviation >= 0.20:
        action_score -= 8
        cautions.append(f"20日線乖離{ma20_deviation:.1%}")
    elif ma20_deviation >= 0.15:
        cautions.append(f"20日線乖離{ma20_deviation:.1%}")

    action_score = round(max(0.0, min(100.0, action_score)), 1)
    a_threshold = 88.0 if regime_label == "過熱警戒" else 80.0
    if action_score >= a_threshold:
        priority = "A"
    elif action_score >= 65:
        priority = "B"
    elif action_score >= 50:
        priority = "C"
    else:
        priority = "見送り"

    if evidence_count < 3 and priority == "A":
        priority = "B"
    if trading_value < 50_000_000:
        priority = "見送り"
    elif trading_value < 100_000_000 and priority in {"A", "B"}:
        priority = "C"
    if momentum_score < 60 and priority == "A":
        priority = "B"
    if regime_label == "過熱警戒" and confidence not in {"中", "高"} and priority == "A":
        priority = "B"

    overheat = "過熱注意" in labels
    if overheat:
        overheat_check = "過熱注意あり・原則1段階引き下げ"
        cautions.append("過熱注意")
        priority = {"A": "B", "B": "C", "C": "見送り", "見送り": "見送り"}[priority]
    else:
        overheat_check = "過熱注意なし"

    return {
        "market_regime": regime_label,
        "action_priority": priority,
        "action_score": action_score,
        "positive_reasons": " / ".join(dict.fromkeys(positive)),
        "caution_reasons": " / ".join(dict.fromkeys(cautions)),
        "liquidity_check": liquidity_check,
        "overheat_check": overheat_check,
    }


def attach_action_priority(changes: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(changes)
    current = changes.get("current", pd.DataFrame()).copy()
    columns = [
        "code", "name", "momentum_rank", "momentum_score", "priority_labels",
        "lifecycle_status", "expectancy_score", "expectancy_confidence",
        "expectancy_evidence_count", "market_regime", "action_priority", "action_score",
        "positive_reasons", "caution_reasons", "liquidity_check", "overheat_check",
        "return_20d", "ma20_deviation", "volume_ratio", "trading_value",
    ]
    if current.empty:
        enriched["action_priority"] = pd.DataFrame(columns=columns)
        return enriched

    scored = current.apply(lambda row: pd.Series(action_priority_values(row, regime)), axis=1)
    for column in scored.columns:
        current[column] = scored[column].values
    current["action_priority_order"] = current["action_priority"].map(ACTION_PRIORITY_ORDER).fillna(99)
    action = current.rename(columns={
        "rank": "momentum_rank",
        "score": "momentum_score",
        "priority_lifecycle_status": "lifecycle_status",
    }).copy()
    action["priority_labels"] = action["priority_labels"].map(priority_labels_text)
    action = action.sort_values(
        ["action_priority_order", "action_score", "expectancy_score", "momentum_rank"],
        ascending=[True, False, False, True],
    )
    action = action[[column for column in columns if column in action.columns]]
    enriched["current"] = current.drop(columns=["action_priority_order"], errors="ignore")
    enriched["action_priority"] = action
    return enriched


def action_priority_count(action: pd.DataFrame, priority: str) -> int:
    if action is None or action.empty or "action_priority" not in action.columns:
        return 0
    return int((action["action_priority"] == priority).sum())


def plain_action_priority_section(action: pd.DataFrame) -> list[str]:
    if action is None or action.empty:
        return ["【本日の調査優先度】", "本日の重点候補はありません。", ""]
    counts = {priority: action_priority_count(action, priority) for priority in ["A", "B", "C", "見送り"]}
    lines = [
        "【本日の調査優先度】",
        "売買推奨ではなく、本日詳しく調査する順番です。",
        f"A評価 {counts['A']}件 / B評価 {counts['B']}件 / C評価 {counts['C']}件 / 見送り {counts['見送り']}件",
    ]
    if counts["A"] == 0:
        lines.append("本日のA評価はありません。")
    for priority in ["A", "B"]:
        subset = action[action["action_priority"] == priority].head(5)
        if subset.empty:
            continue
        lines.append(f"■ {priority}評価")
        for _, row in subset.iterrows():
            count = int(row_number(row, "expectancy_evidence_count"))
            lines.extend([
                f"#{int(row_number(row, 'momentum_rank'))} {row['code']} {row['name']}",
                f"調査優先度 {priority} / {row_number(row, 'action_score'):.1f}点",
                f"期待値 {row_number(row, 'expectancy_score', 50):.1f}点 / 信頼度 {optional_text(row.get('expectancy_confidence')) or '蓄積中'} / 実績{count}件",
                f"理由：{optional_text(row.get('positive_reasons')) or '-'}",
                f"注意：{optional_text(row.get('caution_reasons')) or '特記事項なし'}",
                "",
            ])
    return lines


def html_action_priority_section(action: pd.DataFrame) -> str:
    if action is None or action.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>本日の調査優先度</b><div style="font-size:12px;color:#64748b;margin-top:5px">本日の重点候補はありません。</div></div>'
    colors = {"A": ("#14532d", "#f0fdf4"), "B": ("#1d4ed8", "#eff6ff")}
    counts = {priority: action_priority_count(action, priority) for priority in ["A", "B", "C", "見送り"]}
    groups = []
    if counts["A"] == 0:
        groups.append('<div style="font-size:12px;color:#64748b;margin-top:10px">本日のA評価はありません。</div>')
    for priority in ["A", "B"]:
        subset = action[action["action_priority"] == priority].head(5)
        if subset.empty:
            continue
        color, background = colors[priority]
        items = []
        for _, row in subset.iterrows():
            count = int(row_number(row, "expectancy_evidence_count"))
            caution = optional_text(row.get("caution_reasons")) or "特記事項なし"
            items.append(f'''<div style="border-top:1px solid #dbeafe;padding:10px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(row_number(row, "momentum_rank"))} {html_text(row["code"])} {html_text(row["name"])} <span style="float:right;color:{color}">{priority} / {row_number(row, "action_score"):.1f}点</span></div>
<div style="clear:both;font-size:11px;color:#475569;margin-top:4px">期待値 {row_number(row, "expectancy_score", 50):.1f}点 ・ 信頼度 {html_text(optional_text(row.get("expectancy_confidence")) or "蓄積中")} ・ 実績{count}件</div>
<div style="font-size:11px;color:{color};font-weight:800;margin-top:3px">理由：{html_text(optional_text(row.get("positive_reasons")) or "-")}</div>
<div style="font-size:11px;color:#b45309;margin-top:3px">注意：{html_text(caution)}</div>
</div>''')
        groups.append(f'<div style="background:{background};border:1px solid {color};border-radius:14px;padding:12px;margin-top:10px"><div style="font-size:15px;font-weight:900;color:{color}">{priority}評価</div>{"".join(items)}</div>')
    return f'''<div style="background:#fff;border:2px solid #334155;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#0f172a">本日の調査優先度</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">売買推奨ではなく、本日詳しく調査する順番です。</div>
<div style="font-size:13px;font-weight:800;color:#334155;margin-top:8px">A評価 {counts["A"]}件 ・ B評価 {counts["B"]}件 ・ C評価 {counts["C"]}件 ・ 見送り {counts["見送り"]}件</div>
{"".join(groups)}
</div>'''


def expectancy_detail(row: pd.Series) -> str:
    count = int(optional_number(row.get("expectancy_evidence_count")) or 0)
    if count < 3:
        return f"実績蓄積中（{count}件）"
    return (
        f"期待値 {float(row.get('expectancy_score', 50)):.1f}点 / 信頼度 {optional_text(row.get('expectancy_confidence'))} / "
        f"実績 {count}件 / 推定勝率 {fmt_optional_pct(row.get('expectancy_win_rate'))} / "
        f"加重期待騰落率 {fmt_optional_pct(row.get('expectancy_expected_return'))}"
    )


def plain_priority_section(priority: pd.DataFrame) -> list[str]:
    if priority.empty:
        return []
    lines = [
        "【今日の重点候補】",
        "複数のモメンタム条件が重なった銘柄です。過熱注意は買い推奨ではなく、値動き確認の注意タグです。",
    ]
    lifecycle_summary = priority_lifecycle_summary(priority)
    if lifecycle_summary:
        lines.append(f"継続力: {lifecycle_summary}")
    for _, r in priority.iterrows():
        tags = " / ".join(r.get("priority_labels", []))
        rank_change = fmt_rank_change(r.get("rank_change"))
        movement = f" / {rank_change}" if rank_change else ""
        lines += [
            f"#{int(r['rank'])} {r['code']} {r['name']}｜{int(r['score'])}点｜{tags}",
            f"   20日 {fmt_pct(r.get('return_20d'))} / 出来高 {fmt_num(r.get('volume_ratio'))}倍 / 売買代金 {fmt_trading_value(r.get('trading_value'))}{movement}",
        ]
        lifecycle_detail = priority_lifecycle_detail(r)
        if lifecycle_detail:
            lines.append(f"   継続力 {lifecycle_detail}")
        lines.append(f"   実績評価 {expectancy_detail(r)}")
        if optional_text(r.get("expectancy_tags")):
            lines.append(f"   根拠タグ {optional_text(r.get('expectancy_tags'))}")
        lines.append("")
    return lines


def html_priority_section(priority: pd.DataFrame) -> str:
    if priority.empty:
        return ""
    items = []
    lifecycle_colors = {
        "初登場": ("#dcfce7", "#166534"),
        "再浮上": ("#ffedd5", "#9a3412"),
        "継続": ("#dbeafe", "#1d4ed8"),
        "定着": ("#ede9fe", "#6d28d9"),
        "長期定着": ("#f3e8ff", "#581c87"),
    }
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
        lifecycle_status = optional_text(r.get("priority_lifecycle_status"))
        lifecycle_html = ""
        if lifecycle_status:
            lifecycle_background, lifecycle_color = lifecycle_colors.get(lifecycle_status, ("#f1f5f9", "#475569"))
            lifecycle_html = f'<span style="display:inline-block;margin:2px 0 2px 4px;padding:3px 8px;border-radius:999px;background:{lifecycle_background};color:{lifecycle_color};font-size:12px;font-weight:900">{html_text(lifecycle_status)}</span>'
        rank_change = fmt_rank_change(r.get("rank_change"))
        movement = f" ・ {html_text(rank_change)}" if rank_change else ""
        lifecycle_detail = priority_lifecycle_detail(r)
        lifecycle_detail_html = f'<div style="font-size:11px;line-height:1.7;color:#7c3aed;font-weight:800;margin-top:3px">継続力 {html_text(lifecycle_detail)}</div>' if lifecycle_detail else ""
        expectancy_count = int(optional_number(r.get("expectancy_evidence_count")) or 0)
        expectancy_color = "#15803d" if float(r.get("expectancy_score", 50) or 50) >= 65 and expectancy_count >= 3 else "#a16207" if expectancy_count >= 3 else "#64748b"
        expectancy_html = f'<div style="font-size:11px;line-height:1.7;color:{expectancy_color};font-weight:900;margin-top:3px">実績評価 {html_text(expectancy_detail(r))}</div>'
        expectancy_tags_html = f'<div style="font-size:10px;color:#64748b">根拠タグ {html_text(optional_text(r.get("expectancy_tags")))}</div>' if optional_text(r.get("expectancy_tags")) else ""
        items.append(
            f"""<div style="border-top:1px solid #e5e7eb;padding:11px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(r["rank"])} {html_text(r["code"])} {html_text(r["name"])} <span style="color:{score_color(r["score"])}">{int(r["score"])}点</span></div>
<div style="margin:5px 0">{"".join(tag_html)}{lifecycle_html}</div>
<div style="font-size:12px;line-height:1.7;color:#475569">20日 {fmt_pct(r.get("return_20d"))} ・ 出来高 {fmt_num(r.get("volume_ratio"))}倍 ・ 売買代金 {fmt_trading_value(r.get("trading_value"))}{movement}</div>
{lifecycle_detail_html}
{expectancy_html}
{expectancy_tags_html}
</div>"""
        )
    lifecycle_summary = priority_lifecycle_summary(priority)
    lifecycle_summary_html = f'<div style="font-size:12px;font-weight:800;color:#7c3aed;margin-top:6px">継続力: {html_text(lifecycle_summary)}</div>' if lifecycle_summary else ""
    return f"""<div style="background:#fff;border:2px solid #0f172a;border-radius:18px;padding:16px;margin-top:18px">
<div style="font-size:18px;font-weight:900;color:#0f172a">今日の重点候補</div>
<div style="font-size:12px;line-height:1.7;color:#64748b;margin-top:4px">複数のモメンタム条件が重なった銘柄です。過熱注意は売買指示ではなく、値動き確認の注意タグです。</div>
{lifecycle_summary_html}
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


def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, relative_strength: pd.DataFrame, relative_strength_lifecycle: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, release_readiness: pd.DataFrame, operational_alerts: pd.DataFrame, state_inventory: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:
    top_n = cfg["ranking"]["email_top_n"]
    temp = {} if temperature.empty else temperature.iloc[0].to_dict()
    long_streak = top30_streak[top30_streak["top30_streak"] >= 10].copy() if not top30_streak.empty and "top30_streak" in top30_streak.columns else pd.DataFrame()
    price_date = latest_price_date(top100)
    priority = priority_changes.get("current", select_priority_candidates(top100, 10)).head(10)
    compact_ranked = compact_rank_slice(top100, top_n + 1, 30)
    regime = enrich_regime_from_temperature(calculate_market_regime(top100, temperature), temperature)
    lines = [
        "本日のモメンタム・ダッシュボードです。",
        "",
        "【まず見るポイント】",
        f"レポート日: {summary.get('実行日', '-')}",
        f"株価データ日: {price_date}",
        f"買い候補TOP100: {len(top100)}件",
        f"今日の重点候補: {priority_change_count(priority_changes, 'current')}件",
        f"重点候補変化: 新規 {priority_change_count(priority_changes, 'new')} / 継続 {priority_change_count(priority_changes, 'continued')} / 脱落 {priority_change_count(priority_changes, 'dropped')}",
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
    lines += plain_relative_strength_section(relative_strength)
    lines += rs_lifecycle.plain_section(relative_strength_lifecycle)
    lines += plain_sector_momentum_section(sector_momentum)
    lines += plain_sector_rotation_section(sector_rotation)
    lines += plain_sector_leaders_section(sector_leaders)
    lines += plain_governance_section(sector_leader_performance, signal_governance, adaptive_thresholds, run_health)
    lines += plain_paper_portfolio_section(paper_portfolio, paper_trade_plan, paper_performance, paper_risk_budget)
    lines += plain_release_readiness_section(release_readiness, operational_alerts, state_inventory)
    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))
    lines += plain_performance_scorecard(summary.get("_signal_performance", pd.DataFrame()))
    lines += plain_priority_section(priority)
    lines += plain_priority_changes_section(priority_changes)
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


def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, relative_strength: pd.DataFrame, relative_strength_lifecycle: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, release_readiness: pd.DataFrame, operational_alerts: pd.DataFrame, state_inventory: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:
    top_n = cfg["ranking"]["email_top_n"]
    temp = {} if temperature.empty else temperature.iloc[0].to_dict()
    long_streak = top30_streak[top30_streak["top30_streak"] >= 10].copy() if not top30_streak.empty and "top30_streak" in top30_streak.columns else pd.DataFrame()
    price_date = latest_price_date(top100)
    priority = priority_changes.get("current", select_priority_candidates(top100, 10)).head(10)
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
        html_relative_strength_section(relative_strength),
        rs_lifecycle.html_section(relative_strength_lifecycle),
        html_sector_momentum_section(sector_momentum),
        html_sector_rotation_section(sector_rotation),
        html_sector_leaders_section(sector_leaders),
        html_governance_section(sector_leader_performance, signal_governance, adaptive_thresholds, run_health),
        html_paper_portfolio_section(paper_portfolio, paper_trade_plan, paper_performance, paper_risk_budget),
        html_release_readiness_section(release_readiness, operational_alerts, state_inventory),
        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),
        html_performance_scorecard(summary.get("_signal_performance", pd.DataFrame())),
        html_priority_section(priority),
        html_priority_changes_section(priority_changes),
        html_metric_highlights(top100),
        html_section("年初来高値更新ランキング 上位10件", ytd_high_ranking, 10),
        html_section("新規ランクイン 上位10件", new_entries, 10),
        html_section("急上昇 上位10件", rising_fast, 10),
        html_section("TOP30継続10日以上 上位10件", long_streak, 10),
        html_section(f"Momentum Top{top_n}（詳細）", top100, top_n, show_empty=True),
        html_compact_ranking_section(f"Momentum {top_n + 1}-30（コンパクト）", compact_ranked),
    ])
    return f'''<!doctype html><html><body style="margin:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans','Yu Gothic',Meiryo,Arial,sans-serif;color:#111827"><div style="max-width:720px;margin:0 auto;padding:16px"><div style="background:#0f172a;color:#fff;border-radius:20px;padding:20px"><div style="font-size:13px;color:#cbd5e1">モメンタムチンパン ダッシュボード</div><div style="font-size:24px;font-weight:900">{html_text(summary.get('実行日', ''))}</div><div style="margin-top:8px;color:#e2e8f0">株価データ日: {html_text(price_date)} / 売買指示ではなく、モメンタム確認用の自動スクリーニングです。</div></div><table width="100%" style="margin-top:12px;border-collapse:collapse"><tr>{cards[0]}{cards[1]}</tr><tr>{cards[2]}{cards[3]}</tr><tr>{cards[4]}{cards[5]}</tr></table><div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>今日の読み方</b><div style="font-size:13px;line-height:1.8;color:#334155">{html_text(reading_summary(summary))}</div></div><div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>Market Temperature</b><div style="font-size:13px;line-height:1.8;color:#334155">YTD高値 {fmt_int(temp.get('ytd_high_count'))} ({fmt_delta(temp.get('delta_ytd_high_count'), 0)}) / Top100平均スコア {fmt_num(temp.get('top100_avg_score'), 2)} ({fmt_delta(temp.get('delta_top100_avg_score'), 2)})<br>Top100平均20日騰落率 {fmt_pct(temp.get('top100_avg_return_20d'))}（前回比 {fmt_pct_point(temp.get('delta_top100_avg_return_20d'))}） / Top100平均出来高倍率 {fmt_num(temp.get('top100_avg_volume_ratio'), 2)} ({fmt_delta(temp.get('delta_top100_avg_volume_ratio'), 2)})</div></div>{sections}<div style="font-size:12px;color:#64748b;line-height:1.7;margin-top:16px">詳細はGitHub Actions artifactのdaily_report.xlsxを確認してください。<br>{html_text(DISCLAIMER)}</div></div></body></html>'''


def send_email(summary: dict[str, Any], top100: pd.DataFrame, relative_strength: pd.DataFrame, relative_strength_lifecycle: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, release_readiness: pd.DataFrame, operational_alerts: pd.DataFrame, state_inventory: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:
    load_dotenv()
    sender, to, pw = os.getenv("EMAIL_FROM"), os.getenv("EMAIL_TO"), os.getenv("EMAIL_APP_PASSWORD")
    if not sender or not to or not pw:
        logger.info("Email secrets are not set; skip email")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"【モメンタムチンパン】{summary['実行日']} 引け後レポート"
    msg["From"], msg["To"] = sender, to
    msg.attach(MIMEText(build_plain_email(summary, top100, relative_strength, relative_strength_lifecycle, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, release_readiness, operational_alerts, state_inventory, priority_changes, cfg), "plain", "utf-8"))
    msg.attach(MIMEText(build_html_email(summary, top100, relative_strength, relative_strength_lifecycle, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, release_readiness, operational_alerts, state_inventory, priority_changes, cfg), "html", "utf-8"))
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
            row = {"code": st.code, "name": st.name, "sector33": st.sector33, "score": sc, "reason": reason, **score_breakdown, **m}
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
    all_ranked = attach_relative_strength(all_ranked)
    all_ranked = rs_lifecycle.attach(all_ranked, history, today)
    if not all_ranked.empty:
        cols = [c for c in ranking_history_columns() if c in all_ranked.columns] + [c for c in all_ranked.columns if c not in ranking_history_columns()]
        all_ranked = all_ranked[cols]
    market_freshness = evaluate_market_data_freshness(today, all_ranked)
    state_update_allowed = bool(market_freshness["state_update_allowed"])
    all_ranked = attach_strategy_provenance(all_ranked)
    if state_update_allowed:
        write_ranking_history(all_ranked, cfg["data"]["ranking_history_path"])
    else:
        logger.warning("Market data guard blocked ranking history update: %s", market_freshness["detail"])

    top100 = all_ranked[all_ranked["rank"] <= top_limit].copy() if not all_ranked.empty else pd.DataFrame(columns=ranking_history_columns())
    relative_strength = build_relative_strength_table(top100)
    relative_strength_lifecycle = rs_lifecycle.build_table(top100)
    new_entries = top100[top100["is_new_entry"] == True].copy() if not top100.empty else top100.copy()
    rising_fast = top100[top100["is_rising_fast"] == True].copy() if not top100.empty else top100.copy()
    top30_streak = top100[top100["top30_streak"] > 0].sort_values(["top30_streak", "rank"], ascending=[False, True]).copy() if not top100.empty else top100.copy()
    top30_streak_10 = top100[top100["top30_streak"] >= 10].copy() if not top100.empty else top100.copy()
    ytd_high_ranking = all_ranked[all_ranked["ytd_high_flag"] == True].sort_values(["ytd_high_streak", "ytd_high_count", "score"], ascending=[False, False, False]).copy() if not all_ranked.empty else all_ranked.copy()
    sector_momentum = attach_sector_rotation(calculate_sector_momentum(all_ranked, history, today, top_limit))
    priority_changes = compare_priority_candidates(top100, history, today, top_limit)
    priority_changes = attach_priority_candidate_lifecycle(priority_changes, history, top100, today, top_limit)
    performance_history = combined_ranking_history(history, all_ranked, today) if state_update_allowed else history.copy()
    priority_performance = calculate_priority_performance(performance_history, top_limit)
    signal_performance = build_signal_performance_summary(priority_performance)
    priority_changes = attach_priority_expectancy(priority_changes, signal_performance)

    temp_path = cfg["data"]["market_temperature_path"]
    old_temp = pd.read_csv(temp_path) if Path(temp_path).exists() else pd.DataFrame()
    temperature = market_temperature(today, all_ranked, top100, old_temp)
    regime = calculate_market_regime(top100, temperature)
    temperature = attach_market_regime_history(today, temperature, regime, old_temp)
    regime = enrich_regime_from_temperature(regime, temperature)
    priority_changes = attach_action_priority(priority_changes, regime)
    action_priority = priority_changes.get("action_priority", pd.DataFrame())
    sector_leaders = build_sector_leaders(all_ranked, sector_momentum, action_priority)
    sector_rotation = build_sector_rotation_table(sector_momentum, sector_leaders)
    sector_history_path = "data/sector_leader_signal_history.csv"
    if state_update_allowed:
        current_sector_signals = current_sector_signal_snapshot(today, sector_leaders)
        sector_signal_history = update_sector_signal_history(sector_history_path, current_sector_signals)
    else:
        sector_signal_history = load_sector_signal_history(sector_history_path)
    sector_leader_outcomes = calculate_sector_leader_outcomes(sector_signal_history, performance_history)
    sector_leader_performance = build_sector_leader_performance_summary(sector_leader_outcomes)
    signal_governance = build_signal_governance(sector_leader_outcomes)
    adaptive_thresholds = build_adaptive_threshold_recommendations(signal_governance)
    run_health = build_run_health(today, all_ranked, top100, sector_momentum, sector_leaders, errors, len(stocks), success)
    run_health = attach_market_data_freshness_health(run_health, market_freshness)
    if state_update_allowed:
        paper_result = run_paper_portfolio(today, all_ranked, sector_leaders, regime, run_health)
        pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)
    else:
        paper_result = load_existing_paper_state(regime, run_health)
        logger.warning("Market data guard preserved all persistent state files")
    paper_portfolio = paper_result["portfolio"]
    paper_trade_plan = paper_result["plan"]
    paper_trade_history = paper_result["trade_history"]
    paper_risk_budget = paper_result["risk_budget"]
    paper_performance = paper_result["performance"]
    state_paths = {
        "ranking_history": cfg["data"]["ranking_history_path"],
        "market_temperature": temp_path,
        "sector_leader_signals": sector_history_path,
        "paper_portfolio": "data/paper_portfolio.csv",
        "paper_trade_history": "data/paper_trade_history.csv",
        "paper_equity_history": "data/paper_equity_history.csv",
    }
    state_inventory = build_state_inventory(state_paths)
    release_readiness = build_release_readiness(run_health, signal_governance, sector_leader_performance, paper_performance, paper_trade_history, paper_risk_budget)
    operational_alerts = build_operational_alerts(release_readiness)
    if state_update_allowed:
        state_snapshots = snapshot_state_files(today, state_paths)
        current_audit = build_execution_audit(today, release_readiness, operational_alerts, state_inventory, state_snapshots, run_health)
        execution_audit = append_execution_audit("data/execution_audit.csv", current_audit)
    else:
        state_snapshots = pd.DataFrame(columns=STATE_SNAPSHOT_COLUMNS)
        execution_audit = load_csv_with_columns("data/execution_audit.csv", EXECUTION_AUDIT_COLUMNS)

    elapsed = round(perf_counter() - started_at, 1)
    limited_mode = max_symbols > 0 and max_symbols < full_universe_count
    universe_df = pd.DataFrame([{"code": st.code, "name": st.name, "market": st.market, "sector33": st.sector33, "scan_mode": "verification_limited" if limited_mode else "full"} for st in stocks])
    summary = {
        "実行日": today,
        "アプリ版": APP_VERSION,
        "レポート形式": "dashboard_relative_strength_lifecycle_v19",
        "市場データ鮮度": market_freshness["status"],
        "最新株価日": market_freshness["latest_price_date"],
        "当日株価件数": market_freshness["fresh_count"],
        "当日株価比率": market_freshness["fresh_ratio"],
        "状態更新実行": "YES" if state_update_allowed else "NO",
        "株価データ日": latest_price_date(top100),
        "JPX上場銘柄数": universe_stats.get("jpx_listed_count", 0),
        "通常株ユニバース数": full_universe_count,
        "除外銘柄数": universe_stats.get("excluded_count", 0),
        "実スキャン対象銘柄数": len(stocks),
        "取得成功": success,
        "取得失敗": len(errors),
        "年初来高値更新": int(all_ranked.get("ytd_high_flag", pd.Series(dtype=bool)).fillna(False).sum()) if not all_ranked.empty else 0,
        "Momentum Top100": len(top100),
        "相対強度S/A": int(relative_strength.get("relative_strength_grade", pd.Series(dtype=str)).isin(["S", "A"]).sum()) if not relative_strength.empty else 0,
        "市場・同業双方超過": int(relative_strength.get("dual_outperformer", pd.Series(dtype=bool)).fillna(False).sum()) if not relative_strength.empty else 0,
        "相対強度急加速": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "急加速"),
        "相対強度再浮上": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "再浮上"),
        "相対強度加速": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "加速"),
        "相対強度主導継続": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "主導継続"),
        "相対強度失速警戒": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "失速警戒"),
        "相対強度崩れ": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "崩れ"),
        "相対強度A以上5日継続": int((relative_strength_lifecycle.get("relative_strength_strong_streak", pd.Series(dtype=float)).fillna(0) >= 5).sum()) if not relative_strength_lifecycle.empty else 0,
        "相対強度双方超過5日継続": int((relative_strength_lifecycle.get("dual_outperformer_streak", pd.Series(dtype=float)).fillna(0) >= 5).sum()) if not relative_strength_lifecycle.empty else 0,
        "相対強度トップ": (str(relative_strength.iloc[0]["code"]) + " " + str(relative_strength.iloc[0]["name"])) if not relative_strength.empty else "",
        "相対強度トップスコア": float(relative_strength.iloc[0]["relative_strength_score"]) if not relative_strength.empty else None,
        "市場中央値20日騰落率": float(all_ranked["return_20d"].median()) if not all_ranked.empty else None,
        "市場中央値60日騰落率": float(all_ranked["return_60d"].median()) if not all_ranked.empty else None,
        "業種集計数": len(sector_momentum),
        "強い業種数": int((sector_momentum.get("sector_strength", pd.Series(dtype=str)) == "強い").sum()) if not sector_momentum.empty else 0,
        "やや強い業種数": int((sector_momentum.get("sector_strength", pd.Series(dtype=str)) == "やや強い").sum()) if not sector_momentum.empty else 0,
        "最上位業種": str(sector_momentum.iloc[0]["sector33"]) if not sector_momentum.empty else "",
        "最上位業種スコア": float(sector_momentum.iloc[0]["sector_momentum_score"]) if not sector_momentum.empty else None,
        "加速業種数": int((sector_momentum.get("sector_rotation", pd.Series(dtype=str)) == "加速").sum()) if not sector_momentum.empty else 0,
        "主導業種数": int((sector_momentum.get("sector_rotation", pd.Series(dtype=str)) == "主導").sum()) if not sector_momentum.empty else 0,
        "改善業種数": int((sector_momentum.get("sector_rotation", pd.Series(dtype=str)) == "改善").sum()) if not sector_momentum.empty else 0,
        "業種リーダー候補数": len(sector_leaders),
        "業種リーダー最優先": sector_research_priority_count(sector_leaders, "最優先"),
        "業種リーダー優先": sector_research_priority_count(sector_leaders, "優先"),
        "最上位業種リーダー": (str(sector_leaders.iloc[0]["code"]) + " " + str(sector_leaders.iloc[0]["name"])) if not sector_leaders.empty else "",
        "最上位業種リーダースコア": float(sector_leaders.iloc[0]["sector_leader_score"]) if not sector_leaders.empty else None,
        "業種リーダー履歴件数": len(sector_signal_history),
        "業種リーダー5日実績件数": int(performance_overall_stats(sector_leader_performance, 5).get("count", 0) or 0),
        "業種リーダー5日勝率": performance_overall_stats(sector_leader_performance, 5).get("win_rate"),
        "業種リーダー5日平均騰落率": performance_overall_stats(sector_leader_performance, 5).get("average_return"),
        "業種リーダー10日実績件数": int(performance_overall_stats(sector_leader_performance, 10).get("count", 0) or 0),
        "業種リーダー10日勝率": performance_overall_stats(sector_leader_performance, 10).get("win_rate"),
        "業種リーダー10日平均騰落率": performance_overall_stats(sector_leader_performance, 10).get("average_return"),
        "業種リーダー20日実績件数": int(performance_overall_stats(sector_leader_performance, 20).get("count", 0) or 0),
        "業種リーダー20日勝率": performance_overall_stats(sector_leader_performance, 20).get("win_rate"),
        "業種リーダー20日平均騰落率": performance_overall_stats(sector_leader_performance, 20).get("average_return"),
        "シグナル劣化警戒数": int((signal_governance.get("status", pd.Series(dtype=str)) == "劣化警戒").sum()) if not signal_governance.empty else 0,
        "閾値調整モード": "shadow_only",
        "Run Health": run_health_overall(run_health),
        "Run Health WARN": int((run_health.get("status", pd.Series(dtype=str)) == "WARN").sum()) if not run_health.empty else 0,
        "Run Health FAIL": int((run_health.get("status", pd.Series(dtype=str)) == "FAIL").sum()) if not run_health.empty else 0,
        "ペーパー元本": PAPER_INITIAL_CAPITAL,
        "ペーパー資産": float(paper_performance.iloc[0]["equity"]) if not paper_performance.empty else PAPER_INITIAL_CAPITAL,
        "ペーパー現金": float(paper_performance.iloc[0]["cash"]) if not paper_performance.empty else PAPER_INITIAL_CAPITAL,
        "ペーパー投資比率": float(paper_performance.iloc[0]["exposure_ratio"]) if not paper_performance.empty else 0.0,
        "ペーパー実現損益": float(paper_performance.iloc[0]["realized_pnl"]) if not paper_performance.empty else 0.0,
        "ペーパー含み損益": float(paper_performance.iloc[0]["unrealized_pnl"]) if not paper_performance.empty else 0.0,
        "ペーパードローダウン": float(paper_performance.iloc[0]["drawdown"]) if not paper_performance.empty else 0.0,
        "ペーパー保有数": len(paper_portfolio),
        "ペーパー新規計画数": len(paper_trade_plan),
        "ペーパー決済数": len(paper_trade_history),
        "リリース判定": release_status_value(release_readiness),
        "実行モード": EXECUTION_MODE,
        "運用P0アラート": int((operational_alerts.get("severity", pd.Series(dtype=str)) == "P0").sum()) if not operational_alerts.empty else 0,
        "運用P1アラート": int((operational_alerts.get("severity", pd.Series(dtype=str)) == "P1").sum()) if not operational_alerts.empty else 0,
        "状態ファイルOK": int((state_inventory.get("status", pd.Series(dtype=str)) == "OK").sum()) if not state_inventory.empty else 0,
        "状態ファイル総数": len(state_inventory),
        "状態スナップショット数": int((state_snapshots.get("status", pd.Series(dtype=str)) == "SNAPSHOT_CREATED").sum()) if not state_snapshots.empty else 0,
        "重点候補数": priority_change_count(priority_changes, "current"),
        "重点候補新規": priority_change_count(priority_changes, "new"),
        "重点候補継続": priority_change_count(priority_changes, "continued"),
        "重点候補脱落": priority_change_count(priority_changes, "dropped"),
        "重点候補タグ変化": priority_change_count(priority_changes, "label_changed"),
        "重点候補比較日": priority_changes.get("previous_date", ""),
        "重点候補初登場": priority_lifecycle_count(priority_changes, "初登場"),
        "重点候補再浮上": priority_lifecycle_count(priority_changes, "再浮上"),
        "重点候補定着": priority_lifecycle_count(priority_changes, "定着"),
        "重点候補長期定着": priority_lifecycle_count(priority_changes, "長期定着"),
        "重点候補連続5日以上": int((priority_changes.get("lifecycle", pd.DataFrame()).get("priority_streak_days", pd.Series(dtype=float)).fillna(0) >= 5).sum()),
        "重点候補5日実績件数": int(overall_performance_stats(signal_performance, 5).get("count", 0) or 0),
        "重点候補5日勝率": overall_performance_stats(signal_performance, 5).get("win_rate"),
        "重点候補5日平均騰落率": overall_performance_stats(signal_performance, 5).get("average_return"),
        "重点候補10日実績件数": int(overall_performance_stats(signal_performance, 10).get("count", 0) or 0),
        "重点候補10日勝率": overall_performance_stats(signal_performance, 10).get("win_rate"),
        "重点候補10日平均騰落率": overall_performance_stats(signal_performance, 10).get("average_return"),
        "重点候補20日実績件数": int(overall_performance_stats(signal_performance, 20).get("count", 0) or 0),
        "重点候補20日勝率": overall_performance_stats(signal_performance, 20).get("win_rate"),
        "重点候補20日平均騰落率": overall_performance_stats(signal_performance, 20).get("average_return"),
        "期待値評価済み候補": int((priority_changes.get("current", pd.DataFrame()).get("expectancy_evidence_count", pd.Series(dtype=float)).fillna(0) >= 3).sum()),
        "期待値高信頼度候補": int((priority_changes.get("current", pd.DataFrame()).get("expectancy_confidence", pd.Series(dtype=str)) == "高").sum()),
        "重点候補平均期待値スコア": float(priority_changes.get("current", pd.DataFrame()).get("expectancy_score", pd.Series(dtype=float)).mean()) if not priority_changes.get("current", pd.DataFrame()).empty else None,
        "調査優先度A": action_priority_count(action_priority, "A"),
        "調査優先度B": action_priority_count(action_priority, "B"),
        "調査優先度C": action_priority_count(action_priority, "C"),
        "調査優先度見送り": action_priority_count(action_priority, "見送り"),
        "A評価平均期待値": float(action_priority[action_priority["action_priority"] == "A"]["expectancy_score"].mean()) if not action_priority.empty and action_priority_count(action_priority, "A") > 0 else None,
        "A評価高信頼度件数": int(((action_priority.get("action_priority", pd.Series(dtype=str)) == "A") & (action_priority.get("expectancy_confidence", pd.Series(dtype=str)) == "高")).sum()) if not action_priority.empty else 0,
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
    summary["_signal_performance"] = signal_performance
    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, relative_strength, relative_strength_lifecycle, sector_momentum, sector_rotation, sector_leaders, sector_signal_history, sector_leader_outcomes, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_trade_history, paper_risk_budget, paper_performance, release_readiness, operational_alerts, state_inventory, state_snapshots, execution_audit, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)
    backup_error_artifacts(errors, cfg, cfg["data"]["output_path"])
    try:
        send_email(summary, top100, relative_strength, relative_strength_lifecycle, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, release_readiness, operational_alerts, state_inventory, priority_changes, cfg)
    except Exception as exc:
        logger.exception("Email sending failed: %s", exc)


if __name__ == "__main__":
    main()
