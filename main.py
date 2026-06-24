"""Momentum Chimpan: Japanese stock momentum screener.

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。
特定銘柄の売買を推奨するものではありません。
最終的な投資判断は利用者自身の責任で行ってください。
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
import yaml
from dotenv import load_dotenv

APP_VERSION = "2026-06-23-email-score-breakdown-v1"
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
    for p in ["data/price_cache", Path(config["data"]["history_path"]).parent, Path(config["data"]["output_path"]).parent]:
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
            holdings = load_holdings()
            fallback = [Stock(normalize_code(r.code), getattr(r, "name", "")) for r in holdings.itertuples()]
            stats["universe_count"] = len(fallback)
            return fallback, errors, stats

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


def load_holdings() -> pd.DataFrame:
    path = Path("holdings.csv")
    cols = ["code", "name", "buy_price", "quantity", "memo"]
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"code": str})
    for c in cols:
        if c not in df.columns:
            df[c] = "" if c in ["code", "name", "memo"] else 0
    df["code"] = df["code"].map(normalize_code)
    return df[cols].dropna(subset=["code"])


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
        if f: cum += 1
        running = max(running, float(h), float(c))
    for f in reversed(flags):
        if f: streak += 1
        else: break
    ma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else None
    avg20vol = float(vol.iloc[-21:-1].mean()) if len(vol) >= 21 else float(vol.tail(20).mean())
    def ret(n: int) -> float | None:
        return float(close.iloc[-1] / close.iloc[-n-1] - 1) if len(close) > n and close.iloc[-n-1] else None
    return {
        "close": last_close, "high": last_high, "volume": last_vol, "date": pd.to_datetime(df["Date"].iloc[-1]).date().isoformat(),
        "ytd_high_flag": ytd_flag, "ytd_high_streak": streak, "ytd_high_count": cum,
        "return_5d": ret(5), "return_20d": ret(20), "return_60d": ret(60), "ma20": ma20, "ma60": ma60,
        "ma20_deviation": (last_close / ma20 - 1) if ma20 else None, "ma60_deviation": (last_close / ma60 - 1) if ma60 else None,
        "volume_ratio": (last_vol / avg20vol) if avg20vol else None, "trading_value": last_close * last_vol,
        "above_ma20": bool(ma20 and last_close > ma20), "above_ma60": bool(ma60 and last_close > ma60),
        "prev_close": float(close.iloc[-2]) if len(close) >= 2 else None, "recent_high": float(high.tail(60).max()),
    }


def score(m: dict[str, Any], min_trading_value: int) -> tuple[int, str, dict[str, int]]:
    s, reasons = 0, []
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
    s = min(sum(breakdown.values()), 100)
    return s, "、".join(reasons), breakdown


def sell_signals(m: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    sig = []
    if m.get("ma20") and m["close"] < m["ma20"]: sig.append("20日線割れ")
    if m.get("recent_high") and (m["close"] / m["recent_high"] - 1) <= -cfg["signals"]["drawdown_threshold"]: sig.append("高値から10%以上下落")
    if (m.get("return_5d") or 0) < 0: sig.append("短期モメンタム低下")
    if m.get("prev_close") and (m["close"] / m["prev_close"] - 1) <= -cfg["signals"]["big_drop_threshold"] and (m.get("volume_ratio") or 0) >= cfg["signals"]["volume_spike_threshold"]: sig.append("出来高を伴う急落")
    if m.get("ma60") and m["close"] < m["ma60"]: sig.append("60日線割れ")
    return sig


def write_history(rows: list[dict[str, Any]], path: str) -> None:
    cols = ["code","name","date","ytd_high_flag","ytd_high_streak","ytd_high_count","close","high","volume","score"]
    new = pd.DataFrame(rows, columns=cols)
    old = pd.read_csv(path, dtype={"code": str}) if Path(path).exists() else pd.DataFrame(columns=cols)
    frames = [df for df in (old, new) if not df.empty]
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    out = out.drop_duplicates(["code", "date"], keep="last")
    out.to_csv(path, index=False)


def excel_report(path: str, summary: dict[str, Any], buy: pd.DataFrame, sell: pd.DataFrame, rank: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame([summary]).to_excel(w, sheet_name="Summary", index=False)
        buy.to_excel(w, sheet_name="Buy Candidates", index=False)
        sell.to_excel(w, sheet_name="Sell Candidates", index=False)
        rank.to_excel(w, sheet_name="YTD High Ranking", index=False)
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
    for src in [report_path, cfg["data"].get("history_path"), "data/jpx_list_cache.csv"]:
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


def score_breakdown_text(r: pd.Series) -> str:
    return (
        f"年初来高値 {int(r.get('score_ytd_high', 0))}/30、"
        f"連続更新 {int(r.get('score_ytd_streak', 0))}/20、"
        f"20日騰落率 {int(r.get('score_return_20d', 0))}/20、"
        f"出来高 {int(r.get('score_volume_ratio', 0))}/15、"
        f"移動平均 {int(r.get('score_ma', 0))}/10、"
        f"売買代金 {int(r.get('score_trading_value', 0))}/5"
    )


def send_email(summary: dict[str, Any], buy: pd.DataFrame, sell: pd.DataFrame, cfg: dict[str, Any]) -> None:
    load_dotenv()
    sender, to, pw = os.getenv("EMAIL_FROM"), os.getenv("EMAIL_TO"), os.getenv("EMAIL_APP_PASSWORD")
    if not sender or not to or not pw:
        logger.info("Email secrets are not set; skip email")
        return

    top_n = cfg["ranking"]["email_top_n"]
    errors_count = int(summary.get("取得失敗", 0) or 0)
    sell_count = int(summary.get("売り候補", 0) or 0)
    ytd_count = int(summary.get("年初来高値更新", 0) or 0)
    success_count = int(summary.get("取得成功", 0) or 0)
    target_count = int(summary.get("実スキャン対象銘柄数", summary.get("通常株ユニバース数", 0)) or 0)
    success_rate = success_count / target_count if target_count else 0

    lines = [
        "本日のモメンタムチンパン戦法レポートです。",
        "",
        "【まず見るポイント】",
        f"・買い候補は上位{summary.get('買い候補', 0)}件です。最上位は「{buy.iloc[0]['code']} {buy.iloc[0]['name']} score {int(buy.iloc[0]['score'])}」です。" if not buy.empty else "・買い候補はありませんでした。",
        f"・売り候補は{sell_count}件です。" + ("保有銘柄の確認が必要です。" if sell_count else "本日は該当なしです。"),
        f"・年初来高値更新は{ytd_count}件、取得失敗は{errors_count}件です。",
        "・これは売買指示ではなく、確認すべき銘柄を絞るためのスクリーニング結果です。",
        "",
        "【サマリー】",
        f"実行日：{summary.get('実行日', '')}",
        f"スキャン対象：{target_count}銘柄（取得成功 {success_count} / 取得失敗 {errors_count} / 成功率 {success_rate:.1%}）",
        f"JPX上場銘柄数：{summary.get('JPX上場銘柄数', '')}",
        f"通常株ユニバース数：{summary.get('通常株ユニバース数', '')}",
        f"除外銘柄数：{summary.get('除外銘柄数', '')}",
        f"年初来高値更新：{ytd_count}",
        f"買い候補：{summary.get('買い候補', 0)}",
        f"売り候補：{sell_count}",
        f"検証モード：{summary.get('検証モード', '')}",
        f"処理時間：{summary.get('処理時間秒', '')}秒",
        "",
        "【スコアの見方】",
        "100点満点です。内訳は、年初来高値更新30点、連続更新20点、20日騰落率20点、出来高倍率15点、20日線・60日線上10点、売買代金5点です。",
        "点数が高いほど『直近で強い値動き・出来高・トレンド条件がそろっている』ことを表します。",
        "",
        f"【買い候補 上位{top_n}件】",
    ]

    if buy.empty:
        lines.append("該当なし")
    for _, r in buy.head(top_n).iterrows():
        lines.append(
            f"{int(r['rank'])}. {r['code']} {r['name']} score {int(r['score'])} "
            f"｜20日騰落率 {fmt_pct(r.get('return_20d'))} "
            f"｜出来高 {fmt_num(r.get('volume_ratio'))}倍 "
            f"｜連続更新 {int(r.get('ytd_high_streak', 0))}日"
        )
        lines.append(f"   理由：{r.get('reason') or '条件該当なし'}")
        lines.append(f"   スコア内訳：{score_breakdown_text(r)}")

    lines += ["", "【売り候補（保有銘柄の確認対象）】"]
    if sell.empty:
        lines.append("該当なし")
    for _, r in sell.iterrows():
        lines.append(
            f"・{r['code']} {r['name']}：{r['sell_signal']} "
            f"｜終値 {fmt_num(r.get('close'), 0)} "
            f"｜含み損益率 {fmt_pct(r.get('unrealized_pnl_rate'))} "
            f"｜5日騰落率 {fmt_pct(r.get('return_5d'))}"
        )
        lines.append("   ※即売りではなく、手動でチャート・材料・保有方針を確認する対象です。")

    lines += [
        "",
        "【エラー・注意】",
        f"取得失敗：{errors_count}件",
        "取得失敗がある場合は、Excelの Errors シートと data/error_backups のバックアップを確認してください。" if errors_count else "本日の取得失敗はありません。",
        "",
        "【詳細確認】",
        "詳細な指標、全ランキング、エラー一覧は GitHub Actions artifact の daily_report.xlsx を確認してください。",
        "",
        DISCLAIMER,
    ]
    msg = MIMEText("\n".join(lines), "plain", "utf-8")
    msg["Subject"] = f"【モメンタムチンパン】{summary['実行日']} 引け後レポート"
    msg["From"], msg["To"] = sender, to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, pw)
        smtp.send_message(msg)


def main() -> None:
    started_at = perf_counter()
    cfg = load_config(); ensure_dirs(cfg)
    stocks, errors, universe_stats = load_universe(cfg)
    full_universe_count = len(stocks)
    max_symbols = int(os.getenv("MOMENTUM_MAX_SYMBOLS", "0") or "0")
    if max_symbols > 0:
        logger.warning("VERIFICATION MODE: limiting universe from %s to first %s symbols", full_universe_count, max_symbols)
        stocks = stocks[:max_symbols]
    holdings = load_holdings()
    holding_map = {normalize_code(r.code): r for r in holdings.itertuples()}
    rows = []; sell_rows = []; history_rows = []; success = 0
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
            if m["close"] >= cfg["market"].get("min_price", 0): rows.append(row)
            history_rows.append({k: row.get(k) for k in ["code","name","date","ytd_high_flag","ytd_high_streak","ytd_high_count","close","high","volume","score"]})
            if st.code in holding_map:
                sig = sell_signals(m, cfg)
                if sig:
                    h = holding_map[st.code]
                    buy_price, qty = float(h.buy_price or 0), float(h.quantity or 0)
                    sell_rows.append({"code": st.code, "name": h.name or st.name, "close": m["close"], "buy_price": buy_price, "quantity": qty,
                        "unrealized_pnl": (m["close"] - buy_price) * qty, "unrealized_pnl_rate": (m["close"] / buy_price - 1) if buy_price else None,
                        "sell_signal": " / ".join(sig), "reason": "確認対象（即売りではありません）", "return_5d": m["return_5d"],
                        "drawdown_from_recent_high": m["close"] / m["recent_high"] - 1 if m.get("recent_high") else None, "volume_ratio": m["volume_ratio"], "ma20": m["ma20"], "ma60": m["ma60"]})
        except Exception as exc:
            if STOP_REQUESTED:
                logger.warning("Scan interrupted while processing %s; stopping", st.code)
                errors.append(error_entry(st.code, st.name, f"interrupted: {exc}", "fetch_price", recoverable=True))
                break
            logger.exception("Failed processing %s", st.code)
            errors.append(error_entry(st.code, st.name, str(exc), "fetch_price", recoverable=True))
    all_df = pd.DataFrame(rows)
    buy_cols = ["rank","code","name","close","score","reason","score_ytd_high","score_ytd_streak","score_return_20d","score_volume_ratio","score_ma","score_trading_value","ytd_high_flag","ytd_high_streak","ytd_high_count","return_5d","return_20d","return_60d","volume_ratio","trading_value","ma20","ma60","above_ma20","above_ma60"]
    buy = all_df.sort_values("score", ascending=False).head(cfg["ranking"]["buy_candidate_limit"]).copy() if not all_df.empty else pd.DataFrame(columns=buy_cols)
    if not buy.empty: buy.insert(0, "rank", range(1, len(buy)+1)); buy = buy[buy_cols]
    rank_cols = ["code","name","close","ytd_high_streak","ytd_high_count","score","return_20d","volume_ratio"]
    ranking = all_df.sort_values(["ytd_high_streak","ytd_high_count","score"], ascending=False)[rank_cols] if not all_df.empty else pd.DataFrame(columns=rank_cols)
    sell = pd.DataFrame(sell_rows)
    write_history(history_rows, cfg["data"]["history_path"])
    elapsed = round(perf_counter() - started_at, 1)
    limited_mode = max_symbols > 0 and max_symbols < full_universe_count
    universe_df = pd.DataFrame([{"code": st.code, "name": st.name, "market": st.market, "scan_mode": "verification_limited" if limited_mode else "full"} for st in stocks])
    summary = {"実行日": today, "アプリ版": APP_VERSION, "レポート形式": "full_universe_summary_v2", "JPX上場銘柄数": universe_stats.get("jpx_listed_count", 0), "通常株ユニバース数": full_universe_count, "除外銘柄数": universe_stats.get("excluded_count", 0), "実スキャン対象銘柄数": len(stocks), "取得成功": success, "取得失敗": len(errors), "年初来高値更新": int(all_df.get("ytd_high_flag", pd.Series(dtype=bool)).sum()), "買い候補": len(buy), "売り候補": len(sell), "検証モード": "YES" if limited_mode else "NO", "銘柄数制限": max_symbols if max_symbols > 0 else "なし", "処理時間秒": elapsed, "注意事項": DISCLAIMER}
    excel_report(cfg["data"]["output_path"], summary, buy, sell, ranking, errors, universe_df)
    backup_error_artifacts(errors, cfg, cfg["data"]["output_path"])
    try: send_email(summary, buy, sell, cfg)
    except Exception as exc: logger.exception("Email sending failed: %s", exc)

if __name__ == "__main__":
    main()
