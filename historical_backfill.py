"""Build research-only historical Momentum Chimpan ranking snapshots.

The backfill uses the current JPX listed-issue cache, so it has explicit
survivorship and delisting bias. It is isolated from production state and may
only be used for exploratory research, regression discovery, and hypothesis
generation. It is never accepted as promotion evidence by strategy_governance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
import yaml

import main
import relative_strength_lifecycle as rs_lifecycle
import replay

BACKFILL_VERSION = "2026-07-11-historical-relative-strength-v2"
DEFAULT_OUTPUT_DIR = "output/backfill"
DEFAULT_CACHE = "data/jpx_list_cache.csv"
DEFAULT_CONFIG = "config.yaml"
MIN_HISTORY_ROWS = 61


@dataclass(frozen=True)
class UniverseMember:
    code: str
    name: str
    market: str
    sector33: str


def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        for column in columns:
            if candidate in str(column):
                return column
    return None


def load_config(path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"market": {"include_markets": ["Prime", "Standard", "Growth"], "min_trading_value": 100_000_000}}
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


def load_current_universe(cache_path: str, config: dict[str, Any]) -> list[UniverseMember]:
    target = Path(cache_path)
    if not target.exists():
        raise FileNotFoundError(f"JPX cache not found: {cache_path}")
    frame = pd.read_csv(target, dtype=str)
    columns = [str(column) for column in frame.columns]
    code_col = _find_column(columns, ["コード", "code"])
    name_col = _find_column(columns, ["銘柄名", "name"])
    market_col = _find_column(columns, ["市場・商品区分", "市場", "区分"])
    sector_col = _find_column(columns, ["33業種区分", "33業種"])
    if not code_col or not name_col:
        raise ValueError("JPX cache does not contain code/name columns")

    include_markets = set((config.get("market") or {}).get("include_markets", []))
    excluded_words = ("ETF", "REIT", "不動産投信", "インフラ", "優先", "外国", "ETN")
    members: list[UniverseMember] = []
    for _, row in frame.iterrows():
        code = main.normalize_code(row.get(code_col, ""))
        if not code.isdigit() or len(code) != 4:
            continue
        name = str(row.get(name_col, "") or "")
        market = str(row.get(market_col, "") or "") if market_col else ""
        sector = main.normalize_sector33(row.get(sector_col, "")) if sector_col else ""
        if include_markets and not main.market_matches(market, include_markets):
            continue
        combined = f"{name} {market} {sector}".lower()
        if any(word.lower() in combined for word in excluded_words):
            continue
        members.append(UniverseMember(code, name, market, sector))
    return sorted(members, key=lambda member: member.code)


def stratified_limit(members: list[UniverseMember], max_symbols: int) -> list[UniverseMember]:
    if max_symbols <= 0 or max_symbols >= len(members):
        return members
    groups: dict[str, list[UniverseMember]] = {}
    for member in members:
        groups.setdefault(member.sector33 or "未分類", []).append(member)
    selected: list[UniverseMember] = []
    positions = {sector: 0 for sector in groups}
    sectors = sorted(groups)
    while len(selected) < max_symbols:
        progressed = False
        for sector in sectors:
            position = positions[sector]
            if position < len(groups[sector]):
                selected.append(groups[sector][position])
                positions[sector] += 1
                progressed = True
                if len(selected) >= max_symbols:
                    break
        if not progressed:
            break
    return sorted(selected, key=lambda member: member.code)


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_ticker_frame(raw: pd.DataFrame, ticker: str, ticker_count: int) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0).astype(str))
        level1 = set(raw.columns.get_level_values(1).astype(str))
        if ticker in level0:
            frame = raw[ticker].copy()
        elif ticker in level1:
            frame = raw.xs(ticker, axis=1, level=1).copy()
        elif ticker_count == 1:
            frame = raw.copy()
            frame.columns = frame.columns.get_level_values(0)
        else:
            return pd.DataFrame()
    else:
        if ticker_count != 1:
            return pd.DataFrame()
        frame = raw.copy()
    frame.columns = [str(column).title() for column in frame.columns]
    frame = frame.reset_index()
    date_column = _find_column([str(column) for column in frame.columns], ["Date", "Datetime"])
    if date_column and date_column != "Date":
        frame = frame.rename(columns={date_column: "Date"})
    return frame


def normalize_downloaded_prices(raw: pd.DataFrame, members: list[UniverseMember]) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    ticker_count = len(members)
    for member in members:
        ticker = f"{member.code}.T"
        frame = _extract_ticker_frame(raw, ticker, ticker_count)
        required = {"Date", "Open", "High", "Low", "Close", "Volume"}
        if frame.empty or not required.issubset(frame.columns):
            continue
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.tz_localize(None)
        for column in ("Open", "High", "Low", "Close", "Volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if "Adj Close" in frame.columns:
            frame["Adj Close"] = pd.to_numeric(frame["Adj Close"], errors="coerce")
            factor = frame["Adj Close"] / frame["Close"]
            factor = factor.where((factor > 0) & factor.notna(), 1.0)
        else:
            factor = pd.Series(1.0, index=frame.index)
        frame["RawClose"] = frame["Close"]
        for column in ("Open", "High", "Low", "Close"):
            frame[column] = frame[column] * factor
        frame = frame.dropna(subset=["Date", "Open", "High", "Low", "Close", "Volume", "RawClose"])
        frame = frame[frame["Close"] > 0].sort_values("Date").drop_duplicates("Date", keep="last")
        if not frame.empty:
            result[member.code] = frame[["Date", "Open", "High", "Low", "Close", "Volume", "RawClose"]].reset_index(drop=True)
    return result


def download_price_history(
    members: list[UniverseMember],
    start: date,
    end: date,
    batch_size: int = 50,
    timeout_seconds: int = 60,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    price_frames: dict[str, pd.DataFrame] = {}
    errors: list[dict[str, Any]] = []
    for offset in range(0, len(members), max(batch_size, 1)):
        batch = members[offset : offset + max(batch_size, 1)]
        tickers = [f"{member.code}.T" for member in batch]
        try:
            raw = yf.download(
                tickers,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                progress=False,
                auto_adjust=False,
                actions=False,
                group_by="ticker",
                threads=True,
                timeout=timeout_seconds,
            )
            normalized = normalize_downloaded_prices(raw, batch)
            price_frames.update(normalized)
            for member in batch:
                if member.code not in normalized:
                    errors.append({"code": member.code, "name": member.name, "stage": "batch_download", "error": "empty or invalid price history"})
        except Exception as exc:
            for member in batch:
                errors.append({"code": member.code, "name": member.name, "stage": "batch_download", "error": str(exc)})
    return price_frames, errors


def data_quality_table(members: list[UniverseMember], prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for member in members:
        frame = prices.get(member.code, pd.DataFrame())
        rows.append({
            "code": member.code,
            "name": member.name,
            "market": member.market,
            "sector33": member.sector33,
            "row_count": len(frame),
            "first_date": frame["Date"].min().date().isoformat() if not frame.empty else "",
            "last_date": frame["Date"].max().date().isoformat() if not frame.empty else "",
            "status": "OK" if len(frame) >= MIN_HISTORY_ROWS else "INSUFFICIENT" if len(frame) else "MISSING",
        })
    return pd.DataFrame(rows)


def eligible_evaluation_dates(
    prices: dict[str, pd.DataFrame],
    sample_every: int,
    minimum_coverage_ratio: float,
) -> list[pd.Timestamp]:
    counts: dict[pd.Timestamp, int] = {}
    for frame in prices.values():
        if len(frame) < MIN_HISTORY_ROWS:
            continue
        for value in frame["Date"].iloc[MIN_HISTORY_ROWS - 1 :]:
            day = pd.Timestamp(value).normalize()
            counts[day] = counts.get(day, 0) + 1
    if not counts:
        return []
    maximum = max(counts.values())
    minimum = max(int(maximum * minimum_coverage_ratio), 1)
    dates = sorted(day for day, count in counts.items() if count >= minimum)
    step = max(int(sample_every), 1)
    sampled = dates[::step]
    if dates and dates[-1] not in sampled:
        sampled.append(dates[-1])
    return sampled


def build_historical_ranking(
    members: list[UniverseMember],
    prices: dict[str, pd.DataFrame],
    config: dict[str, Any],
    sample_every: int = 5,
    minimum_coverage_ratio: float = 0.70,
    top_limit: int = 100,
    lookback_rows: int = 260,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metadata = {member.code: member for member in members}
    dates = eligible_evaluation_dates(prices, sample_every, minimum_coverage_ratio)
    historical = pd.DataFrame(columns=main.ranking_history_columns())
    coverage_rows: list[dict[str, Any]] = []
    min_trading_value = int((config.get("market") or {}).get("min_trading_value", 100_000_000))

    for evaluation_date in dates:
        rows: list[dict[str, Any]] = []
        for code, frame in prices.items():
            available = frame[frame["Date"] <= evaluation_date].tail(lookback_rows)
            if len(available) < MIN_HISTORY_ROWS:
                continue
            try:
                metrics = main.metrics(available[["Date", "Open", "High", "Low", "Close", "Volume"]])
                metrics["trading_value"] = float(available["RawClose"].iloc[-1]) * float(available["Volume"].iloc[-1])
                score, reason, breakdown = main.score(metrics, min_trading_value)
                member = metadata[code]
                rows.append({
                    "code": code,
                    "name": member.name,
                    "sector33": member.sector33,
                    "score": score,
                    "reason": reason,
                    **breakdown,
                    **metrics,
                })
            except Exception:
                continue
        base = pd.DataFrame(rows)
        if base.empty:
            continue
        base = base.sort_values(["score", "return_20d", "volume_ratio"], ascending=[False, False, False], na_position="last")
        day = evaluation_date.date().isoformat()
        ranked = main.enrich_ranking_features(base, historical, day, top_limit)
        ranked = main.attach_relative_strength(ranked)
        ranked = rs_lifecycle.attach(ranked, historical, day)
        columns = [column for column in main.ranking_history_columns() if column in ranked.columns]
        columns += [column for column in ranked.columns if column not in columns]
        ranked = ranked[columns]
        historical = pd.concat([historical, ranked], ignore_index=True)
        coverage_rows.append({
            "date": day,
            "ranked_count": len(ranked),
            "top_count": int((pd.to_numeric(ranked["rank"], errors="coerce") <= top_limit).sum()),
            "sector_count": int(ranked["sector33"].astype(str).str.strip().replace("nan", "").nunique()),
            "minimum_coverage_ratio": minimum_coverage_ratio,
        })
    if not historical.empty:
        historical["code"] = historical["code"].map(main.normalize_code)
        historical = historical.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "rank"]).reset_index(drop=True)
    return historical, pd.DataFrame(coverage_rows)


def write_outputs(
    history: pd.DataFrame,
    coverage: pd.DataFrame,
    quality: pd.DataFrame,
    errors: list[dict[str, Any]],
    output_dir: str,
    universe_count: int,
    selected_count: int,
    start: date,
    end: date,
    sample_every: int,
    cache_hash: str,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "history": target / "historical_ranking.csv",
        "coverage": target / "backfill_coverage.csv",
        "quality": target / "backfill_data_quality.csv",
        "errors": target / "backfill_errors.csv",
        "excel": target / "historical_backfill.xlsx",
        "manifest": target / "backfill_manifest.json",
    }
    history.to_csv(paths["history"], index=False)
    coverage.to_csv(paths["coverage"], index=False)
    quality.to_csv(paths["quality"], index=False)
    pd.DataFrame(errors).to_csv(paths["errors"], index=False)
    manifest = {
        "backfill_version": BACKFILL_VERSION,
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "sample_every_trading_days": sample_every,
        "current_jpx_universe_count": universe_count,
        "selected_universe_count": selected_count,
        "downloaded_symbol_count": int((quality["row_count"] > 0).sum()) if not quality.empty else 0,
        "sufficient_history_symbol_count": int((quality["status"] == "OK").sum()) if not quality.empty else 0,
        "ranking_date_count": int(history["date"].nunique()) if not history.empty else 0,
        "ranking_row_count": len(history),
        "relative_strength_enabled": True,
        "relative_strength_non_null_ratio": (
            float(pd.to_numeric(history.get("relative_strength_score"), errors="coerce").notna().mean())
            if not history.empty else 0.0
        ),
        "relative_strength_grade_count": (
            int(history.get("relative_strength_grade", pd.Series(dtype=str)).replace("", pd.NA).dropna().nunique())
            if not history.empty else 0
        ),
        "relative_strength_lifecycle_enabled": True,
        "relative_strength_lifecycle_non_null_ratio": (
            float(history.get("relative_strength_lifecycle", pd.Series(dtype=str)).replace("", pd.NA).notna().mean())
            if not history.empty else 0.0
        ),
        "jpx_cache_sha256": cache_hash,
        "price_adjustment": "ADJUSTED_OHLC_WITH_RAW_CLOSE_VOLUME_FOR_TRADING_VALUE",
        "universe_bias": "CURRENT_LIST_ONLY_SURVIVORSHIP_AND_DELISTING_BIAS",
        "promotion_evidence_allowed": False,
        "production_state_mutation_allowed": False,
        "research_only": True,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest]).to_excel(writer, sheet_name="Backfill Summary", index=False)
        coverage.to_excel(writer, sheet_name="Coverage", index=False)
        quality.to_excel(writer, sheet_name="Data Quality", index=False)
        pd.DataFrame(errors).to_excel(writer, sheet_name="Errors", index=False)
        history.head(10000).to_excel(writer, sheet_name="Ranking Sample", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(max((len(str(cell.value or "")) for cell in column), default=8) + 2, 42)
    return {"paths": {key: str(value) for key, value in paths.items()}, "manifest": manifest}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build isolated historical research ranking snapshots")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-symbols", type=int, default=300)
    parser.add_argument("--lookback-calendar-days", type=int, default=500)
    parser.add_argument("--sample-every", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--minimum-coverage-ratio", type=float, default=0.70)
    parser.add_argument("--top-limit", type=int, default=100)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before_state = replay.live_state_hashes()
    config = load_config(args.config)
    full_universe = load_current_universe(args.cache, config)
    selected = stratified_limit(full_universe, args.max_symbols)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(args.lookback_calendar_days, 120))
    prices, errors = download_price_history(selected, start, end, args.batch_size)
    quality = data_quality_table(selected, prices)
    history, coverage = build_historical_ranking(
        selected,
        prices,
        config,
        args.sample_every,
        args.minimum_coverage_ratio,
        args.top_limit,
    )
    result = write_outputs(
        history,
        coverage,
        quality,
        errors,
        args.output_dir,
        len(full_universe),
        len(selected),
        start,
        end,
        args.sample_every,
        sha256_file(args.cache),
    )
    after_state = replay.live_state_hashes()
    mutations = [path for path in before_state if before_state[path] != after_state.get(path, "")]
    result["manifest"]["production_state_mutations"] = mutations
    Path(result["paths"]["manifest"]).write_text(json.dumps(result["manifest"], ensure_ascii=False, indent=2), encoding="utf-8")
    if args.strict:
        if mutations:
            raise RuntimeError(f"production state mutated: {mutations}")
        if history.empty or history["date"].nunique() < 2:
            raise RuntimeError("historical ranking did not produce at least two dates")
        if history.duplicated(["date", "code"]).any():
            raise RuntimeError("duplicate date/code rows in historical ranking")
        relative_score = pd.to_numeric(history.get("relative_strength_score"), errors="coerce")
        if relative_score.isna().any():
            raise RuntimeError("historical ranking contains missing relative strength scores")
        lifecycle = history.get("relative_strength_lifecycle", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
        if lifecycle.eq("").any():
            raise RuntimeError("historical ranking contains missing relative strength lifecycle states")
        sufficient_ratio = float((quality["status"] == "OK").mean()) if len(quality) else 0.0
        if sufficient_ratio < 0.50:
            raise RuntimeError(f"less than 50% of selected symbols have sufficient history: {sufficient_ratio:.1%}")
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
