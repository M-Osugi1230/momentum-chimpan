"""Prospective 5/10/20-session outcome tracking for Daily Research Focus.

The module ingests the exact Daily Momentum Report artifact, persists the
human-facing research-priority decision, and evaluates it only after future
market sessions exist. It never changes Momentum scores, priority rules, paper
execution, production state, or live orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

POLICY_PATH = "research/priority_outcomes/policy.yaml"
DEFAULT_DECISIONS = "research/priority_outcomes/daily_research_decisions.csv"
DEFAULT_OUTCOMES = "research/priority_outcomes/daily_research_outcomes.csv"
DEFAULT_CALIBRATION_JSON = "research/priority_outcomes/latest_calibration.json"
DEFAULT_CALIBRATION_MD = "research/priority_outcomes/latest_calibration.md"

DECISION_SCHEMA_VERSION = "2026-07-12-priority-decision-v1"
OUTCOME_SCHEMA_VERSION = "2026-07-12-priority-outcome-v1"
CALIBRATION_VERSION = "2026-07-12-priority-calibration-v1"

DECISION_COLUMNS = [
    "decision_schema_version",
    "decision_id",
    "source_run_id",
    "source_run_url",
    "source_artifact_sha256",
    "recorded_at_utc",
    "decision_date",
    "strategy_fingerprint",
    "focus_policy_version",
    "code",
    "name",
    "sector33",
    "research_bucket",
    "daily_action_list",
    "daily_action_rank",
    "action_priority",
    "action_priority_before_quality",
    "action_priority_before_daily_focus",
    "momentum_rank",
    "momentum_score",
    "action_score",
    "expectancy_score",
    "expectancy_confidence",
    "lifecycle_status",
    "market_regime",
    "relative_strength_grade",
    "data_quality_grade",
    "data_quality_reason_codes",
    "why_today",
    "what_changed",
    "risk_summary",
    "next_research_questions",
    "focus_adjustment_reason",
    "entry_model",
    "round_trip_cost_bps",
    "research_only",
]

OUTCOME_COLUMNS = [
    "outcome_schema_version",
    "decision_id",
    "decision_date",
    "strategy_fingerprint",
    "focus_policy_version",
    "code",
    "name",
    "sector33",
    "research_bucket",
    "lifecycle_status",
    "market_regime",
    "data_quality_grade",
    "momentum_rank",
    "momentum_score",
    "horizon_sessions",
    "entry_model",
    "entry_date",
    "exit_date",
    "entry_adjusted_open",
    "exit_adjusted_close",
    "gross_return",
    "round_trip_cost_bps",
    "net_return",
    "market_benchmark_ticker",
    "market_entry_adjusted_open",
    "market_exit_adjusted_close",
    "market_return",
    "market_excess_return",
    "sector_proxy_method",
    "sector_peer_count",
    "sector_proxy_return",
    "sector_excess_return",
    "outcome_status",
    "outcome_detail",
    "same_day_close_entry",
    "no_lookahead_verified",
    "price_source",
    "price_fingerprint",
    "calculated_at_utc",
    "research_only",
]

BOOL_DECISION_COLUMNS = {"daily_action_list", "research_only"}
NUMERIC_DECISION_COLUMNS = {
    "daily_action_rank",
    "momentum_rank",
    "momentum_score",
    "action_score",
    "expectancy_score",
    "round_trip_cost_bps",
}
BOOL_OUTCOME_COLUMNS = {
    "same_day_close_entry",
    "no_lookahead_verified",
    "research_only",
}
NUMERIC_OUTCOME_COLUMNS = {
    "momentum_rank",
    "momentum_score",
    "horizon_sessions",
    "entry_adjusted_open",
    "exit_adjusted_close",
    "gross_return",
    "round_trip_cost_bps",
    "net_return",
    "market_entry_adjusted_open",
    "market_exit_adjusted_close",
    "market_return",
    "market_excess_return",
    "sector_peer_count",
    "sector_proxy_return",
    "sector_excess_return",
}


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("priority outcome policy must be a mapping")
    validate_policy(payload)
    return payload


def validate_policy(payload: dict[str, Any]) -> None:
    policy = payload.get("policy", {})
    source = payload.get("source", {})
    execution = payload.get("execution_model", {})
    calibration = payload.get("calibration", {})
    governance = payload.get("governance", {})
    if policy.get("id") != "daily-research-priority-outcomes-v1":
        raise ValueError("invalid priority outcome policy id")
    if source.get("require_full_state_update") is not True:
        raise ValueError("full state update must be required")
    if source.get("require_strategy_fingerprint") is not True:
        raise ValueError("strategy fingerprint must be required")
    if execution.get("entry") != "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN":
        raise ValueError("entry model must use the next available adjusted open")
    if execution.get("same_day_close_entry_allowed") is not False:
        raise ValueError("same-day close entry must be disabled")
    if execution.get("horizons_sessions") != [5, 10, 20]:
        raise ValueError("required horizons are 5/10/20 sessions")
    if execution.get("entry_session_counts_as_session_one") is not True:
        raise ValueError("entry session must count as session one")
    if execution.get("exit") != "ADJUSTED_CLOSE":
        raise ValueError("exit must use adjusted close")
    if int(execution.get("round_trip_cost_bps", -1)) < 0:
        raise ValueError("round-trip cost must be non-negative")
    if int(calibration.get("bootstrap_iterations", 0)) < 500:
        raise ValueError("bootstrap_iterations is too small")
    for key in (
        "automatic_score_change",
        "automatic_weight_change",
        "automatic_strategy_change",
        "automatic_priority_rule_change",
        "live_orders",
    ):
        if governance.get(key) is not False:
            raise ValueError(f"{key} must be false")
    if governance.get("production_state_mutations") != []:
        raise ValueError("production_state_mutations must be empty")
    if governance.get("manual_review_required") is not True:
        raise ValueError("manual review must be required")


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


def to_float(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def to_int(value: Any) -> int | None:
    converted = to_float(value)
    return None if converted is None else int(converted)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    return optional_text(value).lower() in {"true", "1", "yes", "y"}


def normalize_code(value: Any) -> str:
    text = optional_text(value).split(".")[0]
    return text.zfill(4) if text else ""


def normalized_date(value: Any) -> str:
    converted = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(converted) else converted.date().isoformat()


def find_file(root: Path, name: str) -> Path | None:
    if not root.exists():
        return None
    matches = sorted(path for path in root.rglob(name) if path.is_file())
    return matches[0] if matches else None


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def empty_decisions() -> pd.DataFrame:
    return pd.DataFrame(columns=DECISION_COLUMNS)


def empty_outcomes() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTCOME_COLUMNS)


def load_csv(path: str | Path, columns: list[str], code_column: bool = False) -> pd.DataFrame:
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        return pd.DataFrame(columns=columns)
    dtype = {"code": str, "decision_id": str, "source_run_id": str} if code_column else {"decision_id": str}
    try:
        frame = pd.read_csv(target, dtype=dtype)
    except Exception:
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    return frame[columns]


def normalize_frame(
    frame: pd.DataFrame,
    columns: list[str],
    bool_columns: set[str],
    numeric_columns: set[str],
) -> pd.DataFrame:
    work = frame.copy()
    for column in columns:
        if column not in work.columns:
            work[column] = None
    for column in bool_columns:
        work[column] = work[column].map(to_bool)
    for column in numeric_columns:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    for column in set(columns) - bool_columns - numeric_columns:
        work[column] = work[column].fillna("").astype(str)
    return work[columns]


def load_decisions(path: str | Path = DEFAULT_DECISIONS) -> pd.DataFrame:
    return normalize_frame(
        load_csv(path, DECISION_COLUMNS, code_column=True),
        DECISION_COLUMNS,
        BOOL_DECISION_COLUMNS,
        NUMERIC_DECISION_COLUMNS,
    )


def load_outcomes(path: str | Path = DEFAULT_OUTCOMES) -> pd.DataFrame:
    return normalize_frame(
        load_csv(path, OUTCOME_COLUMNS, code_column=True),
        OUTCOME_COLUMNS,
        BOOL_OUTCOME_COLUMNS,
        NUMERIC_OUTCOME_COLUMNS,
    )


def atomic_write_csv(frame: pd.DataFrame, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(target)


def atomic_write_json(payload: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def atomic_write_text(text: str, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)


def artifact_fingerprint(root: Path) -> str:
    entries: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            entries.append({
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return canonical_hash(entries)


def extract_decisions(
    artifact_root: str | Path,
    source_run_id: str,
    source_run_url: str,
    recorded_at_utc: str,
    policy: dict[str, Any],
) -> pd.DataFrame:
    root = Path(artifact_root)
    workbook_path = find_file(root, "daily_report.xlsx")
    heartbeat = load_json(find_file(root, "operations_heartbeat.json"))
    fingerprint_manifest = load_json(find_file(root, "strategy_fingerprint.json"))
    if workbook_path is None or not heartbeat:
        return empty_decisions()
    if heartbeat.get("state_update_executed") is not True:
        return empty_decisions()
    if str(heartbeat.get("workflow_status", "")).upper() != "SUCCESS":
        return empty_decisions()
    strategy_fingerprint = optional_text(fingerprint_manifest.get("strategy_fingerprint"))
    if not strategy_fingerprint:
        return empty_decisions()
    report_date = normalized_date(heartbeat.get("report_date"))
    if not report_date:
        try:
            summary = pd.read_excel(workbook_path, sheet_name="Summary")
            if not summary.empty:
                report_date = normalized_date(summary.iloc[0].get("実行日"))
        except Exception:
            report_date = ""
    cutoff = str(policy["source"]["eligible_decision_date_from"])
    if not report_date or report_date < cutoff:
        return empty_decisions()
    try:
        action = pd.read_excel(workbook_path, sheet_name="Action Priority", dtype={"code": str})
    except Exception:
        return empty_decisions()
    if action.empty or "code" not in action.columns:
        return empty_decisions()
    artifact_sha = artifact_fingerprint(root)
    cost_bps = int(policy["execution_model"]["round_trip_cost_bps"])
    rows: list[dict[str, Any]] = []
    for _, source in action.iterrows():
        code = normalize_code(source.get("code"))
        if not (len(code) == 4 and code.isdigit()):
            continue
        focus_version = optional_text(source.get("daily_focus_version")) or "UNKNOWN"
        natural_key = {
            "decision_date": report_date,
            "code": code,
            "strategy_fingerprint": strategy_fingerprint,
            "focus_policy_version": focus_version,
        }
        decision_id = canonical_hash(natural_key)
        bucket = optional_text(source.get("research_bucket"))
        if not bucket:
            bucket = optional_text(source.get("action_priority")) or "Skip"
            if bucket == "見送り":
                bucket = "Skip"
        row = {
            "decision_schema_version": DECISION_SCHEMA_VERSION,
            "decision_id": decision_id,
            "source_run_id": str(source_run_id),
            "source_run_url": source_run_url,
            "source_artifact_sha256": artifact_sha,
            "recorded_at_utc": recorded_at_utc,
            "decision_date": report_date,
            "strategy_fingerprint": strategy_fingerprint,
            "focus_policy_version": focus_version,
            "code": code,
            "name": optional_text(source.get("name")),
            "sector33": optional_text(source.get("sector33")),
            "research_bucket": bucket,
            "daily_action_list": to_bool(source.get("daily_action_list")),
            "daily_action_rank": to_int(source.get("daily_action_rank")),
            "action_priority": optional_text(source.get("action_priority")),
            "action_priority_before_quality": optional_text(source.get("action_priority_before_quality")),
            "action_priority_before_daily_focus": optional_text(source.get("action_priority_before_daily_focus")),
            "momentum_rank": to_int(source.get("momentum_rank")),
            "momentum_score": to_float(source.get("momentum_score")),
            "action_score": to_float(source.get("action_score")),
            "expectancy_score": to_float(source.get("expectancy_score")),
            "expectancy_confidence": optional_text(source.get("expectancy_confidence")),
            "lifecycle_status": optional_text(source.get("lifecycle_status")),
            "market_regime": optional_text(source.get("market_regime")),
            "relative_strength_grade": optional_text(source.get("relative_strength_grade")),
            "data_quality_grade": optional_text(source.get("data_quality_grade")),
            "data_quality_reason_codes": optional_text(source.get("data_quality_reason_codes")),
            "why_today": optional_text(source.get("why_today")),
            "what_changed": optional_text(source.get("what_changed")),
            "risk_summary": optional_text(source.get("risk_summary")),
            "next_research_questions": optional_text(source.get("next_research_questions")),
            "focus_adjustment_reason": optional_text(source.get("focus_adjustment_reason")),
            "entry_model": policy["execution_model"]["entry"],
            "round_trip_cost_bps": cost_bps,
            "research_only": True,
        }
        rows.append(row)
    return normalize_frame(
        pd.DataFrame(rows, columns=DECISION_COLUMNS),
        DECISION_COLUMNS,
        BOOL_DECISION_COLUMNS,
        NUMERIC_DECISION_COLUMNS,
    )


def append_decisions(history: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if incoming is None or incoming.empty:
        return normalize_frame(history, DECISION_COLUMNS, BOOL_DECISION_COLUMNS, NUMERIC_DECISION_COLUMNS)
    combined = pd.concat([history, incoming], ignore_index=True)
    combined = combined.drop_duplicates(["decision_id"], keep="last")
    combined["_date"] = pd.to_datetime(combined["decision_date"], errors="coerce")
    combined = combined.sort_values(["_date", "daily_action_rank", "code"], na_position="last")
    combined = combined.drop(columns="_date").reset_index(drop=True)
    return normalize_frame(combined, DECISION_COLUMNS, BOOL_DECISION_COLUMNS, NUMERIC_DECISION_COLUMNS)


def adjusted_price_frame(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "adjusted_open", "adjusted_close"])
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    if "Date" not in frame.columns:
        frame = frame.reset_index()
    frame.columns = [str(column) for column in frame.columns]
    date_column = "Date" if "Date" in frame.columns else frame.columns[0]
    frame["date"] = pd.to_datetime(frame[date_column], errors="coerce").dt.date
    close = pd.to_numeric(frame.get("Close"), errors="coerce")
    open_price = pd.to_numeric(frame.get("Open"), errors="coerce")
    adjusted_close = pd.to_numeric(frame.get("Adj Close"), errors="coerce")
    if adjusted_close is None or adjusted_close.isna().all():
        adjusted_close = close
    factor = adjusted_close / close.replace(0, np.nan)
    factor = factor.replace([np.inf, -np.inf], np.nan).fillna(1.0)
    frame["adjusted_open"] = open_price * factor
    frame["adjusted_close"] = adjusted_close
    frame = frame.dropna(subset=["date", "adjusted_open", "adjusted_close"])
    frame = frame[frame["adjusted_open"].gt(0) & frame["adjusted_close"].gt(0)]
    return frame[["date", "adjusted_open", "adjusted_close"]].drop_duplicates("date").sort_values("date").reset_index(drop=True)


def fetch_prices(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    raw = yf.download(
        ticker,
        start=start_date,
        end=end_date,
        progress=False,
        auto_adjust=False,
        actions=False,
        threads=False,
        timeout=30,
    )
    return adjusted_price_frame(raw)


def price_fingerprint(frame: pd.DataFrame, entry_index: int, exit_index: int) -> str:
    selected = frame.iloc[entry_index : exit_index + 1].copy()
    payload = [
        {
            "date": row["date"].isoformat(),
            "adjusted_open": round(float(row["adjusted_open"]), 10),
            "adjusted_close": round(float(row["adjusted_close"]), 10),
        }
        for _, row in selected.iterrows()
    ]
    return canonical_hash(payload)


def outcome_base(decision: pd.Series, horizon: int, policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "outcome_schema_version": OUTCOME_SCHEMA_VERSION,
        "decision_id": decision["decision_id"],
        "decision_date": decision["decision_date"],
        "strategy_fingerprint": decision["strategy_fingerprint"],
        "focus_policy_version": decision["focus_policy_version"],
        "code": decision["code"],
        "name": decision["name"],
        "sector33": decision["sector33"],
        "research_bucket": decision["research_bucket"],
        "lifecycle_status": decision["lifecycle_status"],
        "market_regime": decision["market_regime"],
        "data_quality_grade": decision["data_quality_grade"],
        "momentum_rank": decision["momentum_rank"],
        "momentum_score": decision["momentum_score"],
        "horizon_sessions": horizon,
        "entry_model": policy["execution_model"]["entry"],
        "entry_date": "",
        "exit_date": "",
        "entry_adjusted_open": None,
        "exit_adjusted_close": None,
        "gross_return": None,
        "round_trip_cost_bps": int(policy["execution_model"]["round_trip_cost_bps"]),
        "net_return": None,
        "market_benchmark_ticker": policy["execution_model"]["market_benchmark_ticker"],
        "market_entry_adjusted_open": None,
        "market_exit_adjusted_close": None,
        "market_return": None,
        "market_excess_return": None,
        "sector_proxy_method": policy["execution_model"]["sector_proxy"],
        "sector_peer_count": None,
        "sector_proxy_return": None,
        "sector_excess_return": None,
        "outcome_status": "PENDING",
        "outcome_detail": "future sessions are not yet available",
        "same_day_close_entry": False,
        "no_lookahead_verified": False,
        "price_source": policy["execution_model"]["price_source"],
        "price_fingerprint": "",
        "calculated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "research_only": True,
    }


def lookup_market_prices(market: pd.DataFrame, entry_date: date, exit_date: date) -> tuple[float | None, float | None]:
    entry = market[market["date"] == entry_date]
    exit_rows = market[market["date"] == exit_date]
    if entry.empty or exit_rows.empty:
        return None, None
    return float(entry.iloc[0]["adjusted_open"]), float(exit_rows.iloc[0]["adjusted_close"])


def calculate_outcome(
    decision: pd.Series,
    horizon: int,
    stock_prices: pd.DataFrame,
    market_prices: pd.DataFrame,
    policy: dict[str, Any],
) -> dict[str, Any]:
    row = outcome_base(decision, horizon, policy)
    decision_date = pd.to_datetime(decision["decision_date"], errors="coerce")
    if pd.isna(decision_date):
        row["outcome_status"] = "INVALID_DECISION"
        row["outcome_detail"] = "decision date is invalid"
        return row
    future = stock_prices[stock_prices["date"] > decision_date.date()].reset_index(drop=True)
    if future.empty:
        return row
    entry_index = 0
    exit_index = horizon - 1
    entry_date = future.iloc[entry_index]["date"]
    row["entry_date"] = entry_date.isoformat()
    row["entry_adjusted_open"] = float(future.iloc[entry_index]["adjusted_open"])
    row["same_day_close_entry"] = entry_date == decision_date.date()
    if len(future) <= exit_index:
        row["outcome_detail"] = f"{len(future)}/{horizon} future sessions available"
        return row
    exit_date = future.iloc[exit_index]["date"]
    entry_open = float(future.iloc[entry_index]["adjusted_open"])
    exit_close = float(future.iloc[exit_index]["adjusted_close"])
    gross_return = exit_close / entry_open - 1.0
    cost = int(policy["execution_model"]["round_trip_cost_bps"]) / 10000.0
    market_entry, market_exit = lookup_market_prices(market_prices, entry_date, exit_date)
    row.update({
        "exit_date": exit_date.isoformat(),
        "exit_adjusted_close": exit_close,
        "gross_return": gross_return,
        "net_return": gross_return - cost,
        "market_entry_adjusted_open": market_entry,
        "market_exit_adjusted_close": market_exit,
        "price_fingerprint": price_fingerprint(future, entry_index, exit_index),
        "no_lookahead_verified": bool(entry_date > decision_date.date() and exit_date >= entry_date),
    })
    if market_entry is None or market_exit is None:
        row["outcome_status"] = "COMPLETE_WITHOUT_MARKET"
        row["outcome_detail"] = "stock outcome matured but aligned market benchmark is missing"
        return row
    market_return = market_exit / market_entry - 1.0
    row.update({
        "market_return": market_return,
        "market_excess_return": row["net_return"] - market_return,
        "outcome_status": "COMPLETE",
        "outcome_detail": "matured with aligned market benchmark",
    })
    return row


def apply_sector_proxy(outcomes: pd.DataFrame, policy: dict[str, Any]) -> pd.DataFrame:
    work = outcomes.copy()
    if work.empty:
        return work
    minimum_peers = int(policy["execution_model"]["minimum_sector_peer_count"])
    complete_mask = work["outcome_status"].eq("COMPLETE") & work["net_return"].notna()
    complete = work[complete_mask].copy()
    for index, row in complete.iterrows():
        peers = complete[
            complete["decision_date"].eq(row["decision_date"])
            & complete["horizon_sessions"].eq(row["horizon_sessions"])
            & complete["sector33"].eq(row["sector33"])
            & complete["decision_id"].ne(row["decision_id"])
        ]
        peer_count = len(peers)
        work.at[index, "sector_peer_count"] = peer_count
        if optional_text(row["sector33"]) and peer_count >= minimum_peers:
            proxy = float(peers["net_return"].median())
            work.at[index, "sector_proxy_return"] = proxy
            work.at[index, "sector_excess_return"] = float(row["net_return"]) - proxy
    return normalize_frame(work, OUTCOME_COLUMNS, BOOL_OUTCOME_COLUMNS, NUMERIC_OUTCOME_COLUMNS)


def update_outcomes(
    decisions: pd.DataFrame,
    existing: pd.DataFrame,
    policy: dict[str, Any],
    price_loader: Callable[[str, str, str], pd.DataFrame] = fetch_prices,
    as_of_date: str | None = None,
) -> pd.DataFrame:
    if decisions.empty:
        return normalize_frame(existing, OUTCOME_COLUMNS, BOOL_OUTCOME_COLUMNS, NUMERIC_OUTCOME_COLUMNS)
    completed = {
        (row["decision_id"], int(row["horizon_sessions"])): row.to_dict()
        for _, row in existing.iterrows()
        if row["outcome_status"] == "COMPLETE"
    }
    start_date = min(decisions["decision_date"].astype(str))
    end_value = pd.to_datetime(as_of_date or date.today().isoformat()).date() + timedelta(days=7)
    end_date = end_value.isoformat()
    market_ticker = policy["execution_model"]["market_benchmark_ticker"]
    try:
        market_prices = price_loader(market_ticker, start_date, end_date)
    except Exception:
        market_prices = pd.DataFrame(columns=["date", "adjusted_open", "adjusted_close"])
    stock_cache: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    for _, decision in decisions.iterrows():
        ticker = f"{decision['code']}.T"
        if ticker not in stock_cache:
            try:
                stock_cache[ticker] = price_loader(ticker, decision["decision_date"], end_date)
            except Exception:
                stock_cache[ticker] = pd.DataFrame(columns=["date", "adjusted_open", "adjusted_close"])
        for horizon in policy["execution_model"]["horizons_sessions"]:
            key = (decision["decision_id"], int(horizon))
            if key in completed:
                rows.append(completed[key])
                continue
            prices = stock_cache[ticker]
            if prices.empty:
                row = outcome_base(decision, int(horizon), policy)
                row["outcome_status"] = "PRICE_ERROR"
                row["outcome_detail"] = "stock price history is unavailable"
            else:
                row = calculate_outcome(
                    decision,
                    int(horizon),
                    prices,
                    market_prices,
                    policy,
                )
            rows.append(row)
    result = normalize_frame(
        pd.DataFrame(rows, columns=OUTCOME_COLUMNS),
        OUTCOME_COLUMNS,
        BOOL_OUTCOME_COLUMNS,
        NUMERIC_OUTCOME_COLUMNS,
    )
    result = result.drop_duplicates(["decision_id", "horizon_sessions"], keep="last")
    result = apply_sector_proxy(result, policy)
    result["_date"] = pd.to_datetime(result["decision_date"], errors="coerce")
    result = result.sort_values(["_date", "horizon_sessions", "code"], na_position="last")
    return result.drop(columns="_date").reset_index(drop=True)


def momentum_rank_bucket(value: Any) -> str:
    rank = to_int(value)
    if rank is None:
        return "UNKNOWN"
    if rank <= 5:
        return "1-5"
    if rank <= 10:
        return "6-10"
    if rank <= 30:
        return "11-30"
    if rank <= 100:
        return "31-100"
    return "101+"


def momentum_score_bucket(value: Any) -> str:
    score = to_float(value)
    if score is None:
        return "UNKNOWN"
    if score >= 80:
        return "80+"
    if score >= 70:
        return "70-79"
    if score >= 60:
        return "60-69"
    return "<60"


def bootstrap_mean_ci(values: pd.Series, iterations: int, seed_text: str) -> tuple[float | None, float | None]:
    numeric = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(numeric) < 2:
        return None, None
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    samples = rng.choice(numeric, size=(iterations, len(numeric)), replace=True).mean(axis=1)
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def calibration_rows(joined: pd.DataFrame, policy: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions = list(policy["calibration"]["dimensions"])
    iterations = int(policy["calibration"]["bootstrap_iterations"])
    warning_below = int(policy["calibration"]["small_sample_warning_below"])
    rows: list[dict[str, Any]] = []
    for dimension in dimensions:
        if dimension not in joined.columns:
            continue
        for (horizon, value), group in joined.groupby(["horizon_sessions", dimension], dropna=False):
            label = optional_text(value) or "UNKNOWN"
            market_excess = pd.to_numeric(group["market_excess_return"], errors="coerce").dropna()
            net_return = pd.to_numeric(group["net_return"], errors="coerce").dropna()
            lower, upper = bootstrap_mean_ci(
                market_excess,
                iterations,
                f"{dimension}|{label}|{int(horizon)}",
            )
            rows.append({
                "dimension": dimension,
                "value": label,
                "horizon_sessions": int(horizon),
                "sample_size": int(len(group)),
                "distinct_decision_dates": int(group["decision_date"].nunique()),
                "mean_net_return": float(net_return.mean()) if len(net_return) else None,
                "mean_market_excess_return": float(market_excess.mean()) if len(market_excess) else None,
                "median_market_excess_return": float(market_excess.median()) if len(market_excess) else None,
                "positive_market_excess_rate": float((market_excess > 0).mean()) if len(market_excess) else None,
                "bootstrap_ci_lower": lower,
                "bootstrap_ci_upper": upper,
                "small_sample_warning": len(group) < warning_below,
            })
    return sorted(rows, key=lambda row: (row["dimension"], row["value"], row["horizon_sessions"]))


def build_calibration(decisions: pd.DataFrame, outcomes: pd.DataFrame, policy: dict[str, Any]) -> dict[str, Any]:
    complete = outcomes[outcomes["outcome_status"].eq("COMPLETE")].copy()
    joined = complete.copy()
    if not joined.empty:
        joined["momentum_rank_bucket"] = joined["momentum_rank"].map(momentum_rank_bucket)
        joined["momentum_score_bucket"] = joined["momentum_score"].map(momentum_score_bucket)
    rows = calibration_rows(joined, policy) if not joined.empty else []
    minimum_n = int(policy["calibration"]["human_review_minimum_per_required_bucket_horizon"])
    minimum_dates = int(policy["calibration"]["human_review_minimum_distinct_decision_dates"])
    required_buckets = list(policy["calibration"]["required_buckets_for_review"])
    required_horizons = list(policy["calibration"]["required_horizons_for_review"])
    gates: list[dict[str, Any]] = []
    for bucket in required_buckets:
        for horizon in required_horizons:
            subset = joined[
                joined["research_bucket"].eq(bucket)
                & joined["horizon_sessions"].eq(horizon)
            ] if not joined.empty else joined
            gates.append({
                "research_bucket": bucket,
                "horizon_sessions": horizon,
                "sample_size": int(len(subset)),
                "distinct_decision_dates": int(subset["decision_date"].nunique()) if not subset.empty else 0,
                "minimum_sample_size": minimum_n,
                "minimum_distinct_decision_dates": minimum_dates,
                "passed": bool(
                    len(subset) >= minimum_n
                    and subset["decision_date"].nunique() >= minimum_dates
                ) if not subset.empty else False,
            })
    lookahead_violations = int(
        outcomes[
            outcomes["outcome_status"].isin(["COMPLETE", "COMPLETE_WITHOUT_MARKET"])
            & ~outcomes["no_lookahead_verified"]
        ].shape[0]
    ) if not outcomes.empty else 0
    complete_count = int(outcomes["outcome_status"].eq("COMPLETE").sum()) if not outcomes.empty else 0
    pending_count = int(outcomes["outcome_status"].eq("PENDING").sum()) if not outcomes.empty else 0
    error_count = int(outcomes["outcome_status"].isin(["PRICE_ERROR", "INVALID_DECISION"]).sum()) if not outcomes.empty else 0
    ready = bool(gates and all(gate["passed"] for gate in gates) and lookahead_violations == 0)
    substantive = {
        "calibration_version": CALIBRATION_VERSION,
        "policy_id": policy["policy"]["id"],
        "eligible_decision_date_from": policy["source"]["eligible_decision_date_from"],
        "entry_model": policy["execution_model"]["entry"],
        "same_day_close_entry_allowed": False,
        "horizons_sessions": policy["execution_model"]["horizons_sessions"],
        "round_trip_cost_bps": policy["execution_model"]["round_trip_cost_bps"],
        "decision_count": int(len(decisions)),
        "outcome_row_count": int(len(outcomes)),
        "complete_outcome_count": complete_count,
        "pending_outcome_count": pending_count,
        "error_outcome_count": error_count,
        "lookahead_violation_count": lookahead_violations,
        "distinct_decision_dates": int(decisions["decision_date"].nunique()) if not decisions.empty else 0,
        "review_gates": gates,
        "ready_for_human_priority_rule_review": ready,
        "production_rule_change_allowed": False,
        "manual_review_required": True,
        "calibration_rows": rows,
        "production_state_mutations": [],
        "automatic_score_change": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "automatic_priority_rule_change": False,
        "research_only": True,
    }
    payload = {
        **substantive,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calibration_fingerprint": canonical_hash(substantive),
    }
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def validate_calibration(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("calibration_version") != CALIBRATION_VERSION:
        errors.append("invalid calibration_version")
    if payload.get("same_day_close_entry_allowed") is not False:
        errors.append("same_day_close_entry_allowed must be false")
    if payload.get("production_rule_change_allowed") is not False:
        errors.append("production_rule_change_allowed must be false")
    if payload.get("manual_review_required") is not True:
        errors.append("manual_review_required must be true")
    if payload.get("production_state_mutations") != []:
        errors.append("production_state_mutations must be empty")
    for key in (
        "automatic_score_change",
        "automatic_weight_change",
        "automatic_strategy_change",
        "automatic_priority_rule_change",
    ):
        if payload.get(key) is not False:
            errors.append(f"{key} must be false")
    if int(payload.get("lookahead_violation_count", -1)) != 0:
        errors.append("lookahead violations must be zero")
    status_copy = dict(payload)
    supplied_status_hash = status_copy.pop("status_sha256", "")
    if supplied_status_hash != canonical_hash(status_copy):
        errors.append("status_sha256 mismatch")
    substantive = dict(status_copy)
    substantive.pop("generated_at_utc", None)
    supplied_fingerprint = substantive.pop("calibration_fingerprint", "")
    if supplied_fingerprint != canonical_hash(substantive):
        errors.append("calibration_fingerprint mismatch")
    return errors


def format_pct(value: Any) -> str:
    number = to_float(value)
    return "-" if number is None else f"{number:.2%}"


def calibration_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Daily Research Priority Calibration",
        "",
        f"Generated: `{payload.get('generated_at_utc', '')}`",
        "",
        "## Status",
        "",
        f"- Decisions: **{payload.get('decision_count', 0)}**",
        f"- Complete outcomes: **{payload.get('complete_outcome_count', 0)}**",
        f"- Pending outcomes: **{payload.get('pending_outcome_count', 0)}**",
        f"- Distinct decision dates: **{payload.get('distinct_decision_dates', 0)}**",
        f"- Lookahead violations: **{payload.get('lookahead_violation_count', 0)}**",
        f"- Ready for human priority-rule review: **{payload.get('ready_for_human_priority_rule_review', False)}**",
        "- Production rule change allowed: **False**",
        "",
        "## A/B review gates",
        "",
        "| Bucket | Horizon | N | Decision dates | Gate |",
        "|---|---:|---:|---:|---|",
    ]
    for gate in payload.get("review_gates", []):
        lines.append(
            f"| {gate['research_bucket']} | {gate['horizon_sessions']} | "
            f"{gate['sample_size']}/{gate['minimum_sample_size']} | "
            f"{gate['distinct_decision_dates']}/{gate['minimum_distinct_decision_dates']} | "
            f"{'PASS' if gate['passed'] else 'ACCUMULATING'} |"
        )
    lines.extend([
        "",
        "## Bucket calibration",
        "",
        "| Bucket | Horizon | N | Mean net | Mean market excess | 95% CI | Positive excess | Warning |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    rows = [row for row in payload.get("calibration_rows", []) if row.get("dimension") == "research_bucket"]
    for row in rows:
        ci = f"{format_pct(row.get('bootstrap_ci_lower'))} to {format_pct(row.get('bootstrap_ci_upper'))}"
        lines.append(
            f"| {row['value']} | {row['horizon_sessions']} | {row['sample_size']} | "
            f"{format_pct(row.get('mean_net_return'))} | {format_pct(row.get('mean_market_excess_return'))} | "
            f"{ci} | {format_pct(row.get('positive_market_excess_rate'))} | "
            f"{'SMALL SAMPLE' if row.get('small_sample_warning') else ''} |"
        )
    lines.extend([
        "",
        "## Governance",
        "",
        "This is prospective research evidence only. It does not authorize an automatic score, weight, strategy, or priority-rule change. Manual review remains mandatory.",
        "",
    ])
    return "\n".join(lines)


def validate_histories(decisions: pd.DataFrame, outcomes: pd.DataFrame, policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if decisions["decision_id"].duplicated().any():
        errors.append("duplicate decision_id")
    if outcomes.duplicated(["decision_id", "horizon_sessions"]).any():
        errors.append("duplicate decision/horizon outcome")
    if not decisions.empty:
        if not decisions["strategy_fingerprint"].astype(str).str.strip().ne("").all():
            errors.append("empty strategy fingerprint")
        if not decisions["decision_date"].astype(str).ge(policy["source"]["eligible_decision_date_from"]).all():
            errors.append("decision before eligible cutoff")
    complete = outcomes[outcomes["outcome_status"].isin(["COMPLETE", "COMPLETE_WITHOUT_MARKET"])]
    if not complete.empty:
        entry = pd.to_datetime(complete["entry_date"], errors="coerce")
        decision = pd.to_datetime(complete["decision_date"], errors="coerce")
        if not entry.gt(decision).all():
            errors.append("same-day or earlier entry detected")
        if not complete["no_lookahead_verified"].all():
            errors.append("no-lookahead verification failed")
        if complete["same_day_close_entry"].any():
            errors.append("same-day close entry detected")
    return errors


def update_all(
    artifact_root: str | None,
    source_run_id: str,
    source_run_url: str,
    recorded_at_utc: str,
    decisions_path: str,
    outcomes_path: str,
    calibration_json_path: str,
    calibration_md_path: str,
    policy_path: str = POLICY_PATH,
    price_loader: Callable[[str, str, str], pd.DataFrame] = fetch_prices,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    decisions = load_decisions(decisions_path)
    if artifact_root:
        incoming = extract_decisions(
            artifact_root,
            source_run_id,
            source_run_url,
            recorded_at_utc,
            policy,
        )
        decisions = append_decisions(decisions, incoming)
    outcomes = update_outcomes(
        decisions,
        load_outcomes(outcomes_path),
        policy,
        price_loader=price_loader,
        as_of_date=as_of_date,
    )
    history_errors = validate_histories(decisions, outcomes, policy)
    if history_errors:
        raise ValueError("; ".join(history_errors))
    calibration = build_calibration(decisions, outcomes, policy)
    calibration_errors = validate_calibration(calibration)
    if calibration_errors:
        raise ValueError("; ".join(calibration_errors))
    atomic_write_csv(decisions, decisions_path)
    atomic_write_csv(outcomes, outcomes_path)
    atomic_write_json(calibration, calibration_json_path)
    atomic_write_text(calibration_markdown(calibration), calibration_md_path)
    return {
        "decisions_added_or_total": len(decisions),
        "outcomes_total": len(outcomes),
        "calibration": calibration,
    }


def initialize(
    decisions_path: str,
    outcomes_path: str,
    calibration_json_path: str,
    calibration_md_path: str,
    policy_path: str = POLICY_PATH,
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    decisions = empty_decisions()
    outcomes = empty_outcomes()
    calibration = build_calibration(decisions, outcomes, policy)
    atomic_write_csv(decisions, decisions_path)
    atomic_write_csv(outcomes, outcomes_path)
    atomic_write_json(calibration, calibration_json_path)
    atomic_write_text(calibration_markdown(calibration), calibration_md_path)
    return calibration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track prospective Daily Research Focus outcomes")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_paths(command: argparse.ArgumentParser) -> None:
        command.add_argument("--policy", default=POLICY_PATH)
        command.add_argument("--decisions", default=DEFAULT_DECISIONS)
        command.add_argument("--outcomes", default=DEFAULT_OUTCOMES)
        command.add_argument("--calibration-json", default=DEFAULT_CALIBRATION_JSON)
        command.add_argument("--calibration-md", default=DEFAULT_CALIBRATION_MD)

    initialize_parser = subparsers.add_parser("initialize")
    add_paths(initialize_parser)

    update_parser = subparsers.add_parser("update")
    add_paths(update_parser)
    update_parser.add_argument("--artifact-root", default="")
    update_parser.add_argument("--source-run-id", default="")
    update_parser.add_argument("--source-run-url", default="")
    update_parser.add_argument("--recorded-at-utc", default=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    update_parser.add_argument("--as-of-date", default="")

    validate_parser = subparsers.add_parser("validate")
    add_paths(validate_parser)
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    if args.command == "initialize":
        result = initialize(
            args.decisions,
            args.outcomes,
            args.calibration_json,
            args.calibration_md,
            args.policy,
        )
    elif args.command == "update":
        result = update_all(
            artifact_root=args.artifact_root or None,
            source_run_id=args.source_run_id,
            source_run_url=args.source_run_url,
            recorded_at_utc=args.recorded_at_utc,
            decisions_path=args.decisions,
            outcomes_path=args.outcomes,
            calibration_json_path=args.calibration_json,
            calibration_md_path=args.calibration_md,
            policy_path=args.policy,
            as_of_date=args.as_of_date or None,
        )
    else:
        policy = load_policy(args.policy)
        decisions = load_decisions(args.decisions)
        outcomes = load_outcomes(args.outcomes)
        payload = json.loads(Path(args.calibration_json).read_text(encoding="utf-8"))
        errors = validate_histories(decisions, outcomes, policy) + validate_calibration(payload)
        rebuilt = build_calibration(decisions, outcomes, policy)
        for key in ("generated_at_utc", "status_sha256"):
            rebuilt.pop(key, None)
            payload.pop(key, None)
        if rebuilt != payload:
            errors.append("history/calibration semantic mismatch")
        if errors:
            print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        result = {"valid": True, "decision_count": len(decisions), "outcome_count": len(outcomes)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
