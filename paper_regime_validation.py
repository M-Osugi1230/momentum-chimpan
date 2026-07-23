"""Research-only validation of paper portfolio behavior by market regime.

The validator reads committed paper and market state, produces signed diagnostics,
and never changes production ranking, paper rules, or portfolio state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

POLICY_PATH = "research/paper_regime_validation_policy.yaml"
OUTPUT_VERSION = "2026-07-23-paper-regime-validation-v1"


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


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def atomic_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)


def atomic_json(path: str | Path, payload: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def atomic_csv(path: str | Path, frame: pd.DataFrame) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(target)


def read_csv(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(target)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def load_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    validate_policy(payload)
    return payload


def validate_policy(payload: dict[str, Any]) -> None:
    study = payload.get("study", {})
    governance = payload.get("governance", {})
    if study.get("id") != "paper-portfolio-regime-validation-v1":
        raise ValueError("invalid paper regime validation study id")
    if study.get("research_only") is not True:
        raise ValueError("paper regime validation must remain research-only")
    regimes = payload.get("market_regimes", [])
    expected = {"強気", "やや強気", "中立", "弱気", "過熱警戒"}
    labels = {optional_text(item.get("label")) for item in regimes if isinstance(item, dict)}
    if labels != expected:
        raise ValueError(f"market regimes must be exactly {sorted(expected)}")
    health = payload.get("operational_health", {})
    if set(health) != {"PASS", "WARN", "FAIL"}:
        raise ValueError("operational health must define PASS, WARN, and FAIL")
    if float(health["PASS"]["exposure_multiplier"]) != 1.0:
        raise ValueError("PASS multiplier must be 1.0")
    if float(health["WARN"]["exposure_multiplier"]) != 0.5:
        raise ValueError("WARN multiplier must be 0.5")
    if float(health["FAIL"]["exposure_multiplier"]) != 0.0:
        raise ValueError("FAIL multiplier must be 0.0")
    if health["FAIL"].get("new_entries_allowed") is not False:
        raise ValueError("FAIL must block new entries")
    for key in (
        "automatic_score_change",
        "automatic_weight_change",
        "automatic_strategy_change",
        "automatic_paper_rule_change",
        "live_orders",
    ):
        if governance.get(key) is not False:
            raise ValueError(f"{key} must be false")
    if governance.get("production_state_mutations") != []:
        raise ValueError("production_state_mutations must remain empty")
    if governance.get("manual_review_required") is not True:
        raise ValueError("manual review must be required")


def target_maps(policy: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    regime_targets = {
        optional_text(item["label"]): float(item["target_exposure"])
        for item in policy["market_regimes"]
    }
    health_multipliers = {
        label: float(values["exposure_multiplier"])
        for label, values in policy["operational_health"].items()
    }
    return regime_targets, health_multipliers


def normalize_dates(frame: pd.DataFrame, column: str = "date") -> pd.DataFrame:
    work = frame.copy()
    if column not in work.columns:
        work[column] = pd.Series(dtype=str)
    parsed = pd.to_datetime(work[column], errors="coerce")
    work[column] = parsed.dt.date.astype("string")
    return work


def market_daily(market: pd.DataFrame) -> pd.DataFrame:
    if market.empty:
        return pd.DataFrame(columns=["date", "market_regime"])
    work = normalize_dates(market)
    candidates = ["market_regime", "regime_label", "label"]
    source = next((column for column in candidates if column in work.columns), None)
    if source is None:
        work["market_regime"] = ""
    elif source != "market_regime":
        work["market_regime"] = work[source]
    return work[["date", "market_regime"]].dropna(subset=["date"]).drop_duplicates("date", keep="last")


def health_daily(execution: pd.DataFrame) -> pd.DataFrame:
    if execution.empty:
        return pd.DataFrame(columns=["date", "run_health"])
    work = normalize_dates(execution)
    if "run_health" not in work.columns:
        work["run_health"] = ""
    return work[["date", "run_health"]].dropna(subset=["date"]).drop_duplicates("date", keep="last")


def equity_daily(
    equity: pd.DataFrame,
    market: pd.DataFrame,
    execution: pd.DataFrame,
    policy: dict[str, Any],
) -> pd.DataFrame:
    columns = [
        "date",
        "market_regime",
        "run_health",
        "base_target_exposure",
        "health_multiplier",
        "target_exposure",
        "actual_exposure",
        "exposure_gap",
        "equity",
        "daily_return",
        "drawdown",
        "open_positions",
        "closed_trades",
        "win_rate",
    ]
    if equity.empty:
        return pd.DataFrame(columns=columns)
    work = normalize_dates(equity)
    work = work.merge(market_daily(market), on="date", how="left")
    work = work.merge(health_daily(execution), on="date", how="left")
    work["market_regime"] = work["market_regime"].fillna("UNKNOWN").astype(str)
    work["run_health"] = work["run_health"].fillna("UNKNOWN").astype(str)
    regime_targets, health_multipliers = target_maps(policy)
    work["base_target_exposure"] = work["market_regime"].map(regime_targets)
    work["health_multiplier"] = work["run_health"].map(health_multipliers).fillna(0.0)
    work["target_exposure"] = work["base_target_exposure"] * work["health_multiplier"]
    work["actual_exposure"] = pd.to_numeric(
        work.get("exposure_ratio", pd.Series(index=work.index, dtype=float)), errors="coerce"
    )
    work["exposure_gap"] = work["actual_exposure"] - work["target_exposure"]
    work["equity"] = pd.to_numeric(
        work.get("equity", pd.Series(index=work.index, dtype=float)), errors="coerce"
    )
    work = work.sort_values("date")
    work["daily_return"] = work["equity"].pct_change(fill_method=None)
    for column in ("drawdown", "open_positions", "closed_trades", "win_rate"):
        work[column] = pd.to_numeric(
            work.get(column, pd.Series(index=work.index, dtype=float)), errors="coerce"
        )
    return work[columns]


def trade_regimes(
    trades: pd.DataFrame,
    market: pd.DataFrame,
    execution: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "position_id",
        "code",
        "name",
        "sector33",
        "entry_date",
        "entry_market_regime",
        "entry_run_health",
        "exit_date",
        "exit_market_regime",
        "exit_run_health",
        "exit_reason",
        "realized_pnl",
        "realized_return",
        "win",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)
    work = trades.copy()
    for date_column in ("entry_date", "exit_date"):
        parsed = pd.to_datetime(work.get(date_column), errors="coerce")
        work[date_column] = parsed.dt.date.astype("string")
    market_lookup = market_daily(market).set_index("date")["market_regime"].to_dict()
    health_lookup = health_daily(execution).set_index("date")["run_health"].to_dict()
    work["entry_market_regime"] = work["entry_date"].map(market_lookup).fillna("UNKNOWN")
    work["exit_market_regime"] = work["exit_date"].map(market_lookup).fillna("UNKNOWN")
    work["entry_run_health"] = work["entry_date"].map(health_lookup).fillna("UNKNOWN")
    work["exit_run_health"] = work["exit_date"].map(health_lookup).fillna("UNKNOWN")
    work["realized_pnl"] = pd.to_numeric(
        work.get("realized_pnl", pd.Series(index=work.index, dtype=float)), errors="coerce"
    )
    work["realized_return"] = pd.to_numeric(
        work.get("realized_return", pd.Series(index=work.index, dtype=float)), errors="coerce"
    )
    work["win"] = work["realized_return"] > 0
    for column in columns:
        if column not in work.columns:
            work[column] = None
    return work[columns]


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def regime_summary(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    policy: dict[str, Any],
) -> pd.DataFrame:
    minimums = policy["minimums"]
    rows: list[dict[str, Any]] = []
    for item in policy["market_regimes"]:
        label = optional_text(item["label"])
        observations = daily[daily["market_regime"] == label].copy()
        completed = trades[trades["entry_market_regime"] == label].copy()
        actual = pd.to_numeric(observations.get("actual_exposure"), errors="coerce")
        target = pd.to_numeric(observations.get("target_exposure"), errors="coerce")
        returns = pd.to_numeric(observations.get("daily_return"), errors="coerce")
        equity = pd.to_numeric(observations.get("equity"), errors="coerce").dropna()
        drawdown = pd.to_numeric(observations.get("drawdown"), errors="coerce")
        realized = pd.to_numeric(completed.get("realized_return"), errors="coerce")
        sectors = completed.get("sector33", pd.Series(dtype=str)).dropna().astype(str)
        sector_share = None
        if len(sectors):
            sector_share = float(sectors.value_counts(normalize=True).iloc[0])
        cumulative = None
        if len(equity) >= 2 and equity.iloc[0] != 0:
            cumulative = float(equity.iloc[-1] / equity.iloc[0] - 1)
        observation_count = int(len(observations))
        completed_count = int(realized.notna().sum())
        distinct_dates = int(observations["date"].nunique()) if not observations.empty else 0
        exit_distribution = (
            completed.get("exit_reason", pd.Series(dtype=str)).fillna("UNKNOWN").value_counts().to_dict()
        )
        rows.append({
            "market_regime": label,
            "observation_count": observation_count,
            "distinct_dates": distinct_dates,
            "average_target_exposure": _safe_float(target.mean()),
            "average_actual_exposure": _safe_float(actual.mean()),
            "exposure_gap": _safe_float((actual - target).mean()),
            "mean_daily_return": _safe_float(returns.mean()),
            "median_daily_return": _safe_float(returns.median()),
            "cumulative_equity_return": cumulative,
            "maximum_drawdown": _safe_float(drawdown.min()),
            "completed_trades": completed_count,
            "win_rate": _safe_float((realized > 0).mean()),
            "mean_realized_return": _safe_float(realized.mean()),
            "median_realized_return": _safe_float(realized.median()),
            "largest_sector_trade_share": sector_share,
            "exit_reason_distribution": json.dumps(exit_distribution, ensure_ascii=False, sort_keys=True),
            "observation_gate_passed": observation_count >= int(minimums["observations_per_market_regime"]),
            "date_gate_passed": distinct_dates >= int(minimums["distinct_dates_per_market_regime"]),
            "trade_gate_passed": completed_count >= int(minimums["completed_trades_per_market_regime"]),
            "coverage_state": "OBSERVED" if observation_count else "MISSING",
        })
    return pd.DataFrame(rows)


def build_status(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    summary: pd.DataFrame,
    policy: dict[str, Any],
) -> dict[str, Any]:
    missing = summary.loc[summary["coverage_state"] == "MISSING", "market_regime"].tolist()
    mature = summary[
        summary["observation_gate_passed"]
        & summary["date_gate_passed"]
        & summary["trade_gate_passed"]
    ]["market_regime"].tolist()
    status = "READY_FOR_MANUAL_REVIEW" if len(mature) == len(summary) and len(summary) else "ACCUMULATING"
    payload: dict[str, Any] = {
        "validation_version": OUTPUT_VERSION,
        "study_id": policy["study"]["id"],
        "validation_status": status,
        "daily_observation_count": int(len(daily)),
        "completed_trade_count": int(pd.to_numeric(trades.get("realized_return"), errors="coerce").notna().sum()) if not trades.empty else 0,
        "observed_market_regimes": summary.loc[summary["coverage_state"] == "OBSERVED", "market_regime"].tolist(),
        "missing_market_regimes": missing,
        "mature_market_regimes": mature,
        "all_market_regimes_mature": len(mature) == len(summary) and len(summary) > 0,
        "manual_review_required": True,
        "production_state_mutations": [],
        "automatic_score_change": False,
        "automatic_weight_change": False,
        "automatic_strategy_change": False,
        "automatic_paper_rule_change": False,
        "live_orders": False,
        "research_only": True,
    }
    payload["validation_fingerprint"] = canonical_hash(payload)
    payload["status_sha256"] = canonical_hash(payload)
    return payload


def markdown(status: dict[str, Any], summary: pd.DataFrame) -> str:
    lines = [
        "# Paper Portfolio Regime Validation",
        "",
        f"- Status: `{status['validation_status']}`",
        f"- Daily observations: {status['daily_observation_count']}",
        f"- Completed trades: {status['completed_trade_count']}",
        f"- Missing regimes: {', '.join(status['missing_market_regimes']) or 'NONE'}",
        f"- Mature regimes: {', '.join(status['mature_market_regimes']) or 'NONE'}",
        "- Research only: true",
        "- Production or paper-rule mutation: none",
        "",
        "## Regime scorecard",
        "",
    ]
    if summary.empty:
        lines.append("No observations are available.")
    else:
        lines.append(summary.to_markdown(index=False))
    lines.extend([
        "",
        "A missing or small-sample regime is not treated as evidence of success.",
        "Manual review remains mandatory even when all minimum gates are reached.",
    ])
    return "\n".join(lines) + "\n"


def build(
    equity_path: str | Path,
    trades_path: str | Path,
    execution_path: str | Path,
    market_path: str | Path,
    policy_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    equity = read_csv(equity_path)
    trades = read_csv(trades_path)
    execution = read_csv(execution_path)
    market = read_csv(market_path)
    daily = equity_daily(equity, market, execution, policy)
    trade_frame = trade_regimes(trades, market, execution)
    summary = regime_summary(daily, trade_frame, policy)
    status = build_status(daily, trade_frame, summary, policy)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    atomic_csv(output / "paper_regime_daily.csv", daily)
    atomic_csv(output / "paper_trade_regimes.csv", trade_frame)
    atomic_csv(output / "paper_regime_summary.csv", summary)
    atomic_json(output / "paper_regime_validation.json", status)
    atomic_text(output / "paper_regime_validation.md", markdown(status, summary))
    return status


def validate_output(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    required = [
        "paper_regime_daily.csv",
        "paper_trade_regimes.csv",
        "paper_regime_summary.csv",
        "paper_regime_validation.json",
        "paper_regime_validation.md",
    ]
    issues = [f"missing {name}" for name in required if not (root / name).is_file()]
    if issues:
        return issues
    payload = json.loads((root / "paper_regime_validation.json").read_text(encoding="utf-8"))
    supplied = optional_text(payload.get("status_sha256"))
    work = dict(payload)
    work.pop("status_sha256", None)
    expected = canonical_hash(work)
    if supplied != expected:
        issues.append("status_sha256 mismatch")
    if payload.get("research_only") is not True:
        issues.append("research_only must be true")
    if payload.get("production_state_mutations") != []:
        issues.append("production state mutations must be empty")
    if payload.get("automatic_paper_rule_change") is not False:
        issues.append("automatic paper rule changes must remain disabled")
    if payload.get("live_orders") is not False:
        issues.append("live orders must remain disabled")
    summary = pd.read_csv(root / "paper_regime_summary.csv")
    expected_regimes = {"強気", "やや強気", "中立", "弱気", "過熱警戒"}
    if set(summary.get("market_regime", pd.Series(dtype=str))) != expected_regimes:
        issues.append("summary must retain all registered market regimes")
    return sorted(set(issues))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the paper portfolio by regime")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("build")
    run.add_argument("--equity", default="data/paper_equity_history.csv")
    run.add_argument("--trades", default="data/paper_trade_history.csv")
    run.add_argument("--execution", default="data/execution_audit.csv")
    run.add_argument("--market", default="data/market_temperature.csv")
    run.add_argument("--policy", default=POLICY_PATH)
    run.add_argument("--output-dir", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "build":
        status = build(
            equity_path=args.equity,
            trades_path=args.trades,
            execution_path=args.execution,
            market_path=args.market,
            policy_path=args.policy,
            output_dir=args.output_dir,
        )
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    issues = validate_output(args.output_dir)
    print(json.dumps({"passed": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
