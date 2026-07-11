"""Research-only portfolio filter comparison lab.

The lab keeps the execution-aware portfolio engine fixed and changes only
pre-declared signal eligibility filters. It compares each filter against the
same baseline capital, execution costs, position limits and benchmark. Results
from historical current-universe backfills are permanently non-promotable.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import main
import portfolio_research as portfolio
import replay

FILTER_LAB_VERSION = "2026-07-11-portfolio-filter-lab-v1"
DEFAULT_SIGNALS = "output/backfill/replay/replay_signals.csv"
DEFAULT_HISTORY = "output/backfill/historical_ranking.csv"
DEFAULT_PRICES = "output/backfill/historical_price_panel.csv"
DEFAULT_PROVENANCE = "output/backfill/replay/evidence_provenance.json"
DEFAULT_OUTPUT_DIR = "output/backfill/portfolio_filters"

POSITIVE_LIFECYCLES = ("急加速", "再浮上", "加速", "主導継続", "主導")
NON_WEAK_REGIMES = ("強気", "やや強気", "中立")
RISK_ON_REGIMES = ("強気", "やや強気")


@dataclass(frozen=True)
class FilterRule:
    name: str
    description: str
    allowed_rs_grades: tuple[str, ...] = ()
    minimum_rs_score: float | None = None
    allowed_lifecycles: tuple[str, ...] = ()
    require_dual_outperformer: bool = False
    allowed_market_regimes: tuple[str, ...] = ()
    allowed_action_priorities: tuple[str, ...] = ()
    allowed_sector_rotations: tuple[str, ...] = ()
    maximum_positive_gap: float | None = None
    minimum_entry_trading_value: float = 0.0
    maximum_participation: float = 0.01


RULES: tuple[FilterRule, ...] = (
    FilterRule("baseline", "現行の最優先・優先シグナルを追加条件なしで運用"),
    FilterRule(
        "liquid_gap3",
        "寄付上昇ギャップ3%以下かつentry売買代金5億円以上",
        maximum_positive_gap=0.03,
        minimum_entry_trading_value=500_000_000.0,
    ),
    FilterRule(
        "relative_strength_a_s",
        "相対強度グレードSまたはA",
        allowed_rs_grades=("S", "A"),
    ),
    FilterRule(
        "positive_lifecycle",
        "急加速・再浮上・加速・主導継続・主導",
        allowed_lifecycles=POSITIVE_LIFECYCLES,
    ),
    FilterRule(
        "rs_a_s_positive_lifecycle",
        "相対強度S/Aかつポジティブなライフサイクル",
        allowed_rs_grades=("S", "A"),
        allowed_lifecycles=POSITIVE_LIFECYCLES,
    ),
    FilterRule(
        "dual_outperformer",
        "市場と同業種の双方を上回る銘柄",
        require_dual_outperformer=True,
    ),
    FilterRule(
        "market_not_weak",
        "市場レジームが弱気以外",
        allowed_market_regimes=NON_WEAK_REGIMES,
    ),
    FilterRule(
        "risk_on_rs_lifecycle",
        "強気・やや強気で相対強度S/Aかつポジティブなライフサイクル",
        allowed_rs_grades=("S", "A"),
        allowed_lifecycles=POSITIVE_LIFECYCLES,
        allowed_market_regimes=RISK_ON_REGIMES,
    ),
    FilterRule(
        "quality_liquid",
        "相対強度S/A・ポジティブライフサイクル・gap3%以下・売買代金5億円以上",
        allowed_rs_grades=("S", "A"),
        allowed_lifecycles=POSITIVE_LIFECYCLES,
        maximum_positive_gap=0.03,
        minimum_entry_trading_value=500_000_000.0,
    ),
    FilterRule(
        "action_ab_quality_liquid",
        "Action Priority A/B・相対強度S/A・ポジティブライフサイクル・流動性条件",
        allowed_rs_grades=("S", "A"),
        allowed_lifecycles=POSITIVE_LIFECYCLES,
        allowed_action_priorities=("A", "B"),
        maximum_positive_gap=0.03,
        minimum_entry_trading_value=500_000_000.0,
    ),
)


def _boolean_series(values: pd.Series) -> pd.Series:
    if values.dtype == bool:
        return values.fillna(False)
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y", "t"})


def _number(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def load_history(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(target, dtype={"code": str})
    required = {"date", "code", "rank", "relative_strength_score", "relative_strength_grade", "relative_strength_lifecycle"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"historical ranking missing filter context: {missing}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date.astype(str)
    frame["code"] = frame["code"].map(main.normalize_code)
    frame["rank"] = pd.to_numeric(frame["rank"], errors="coerce")
    frame["relative_strength_score"] = pd.to_numeric(frame["relative_strength_score"], errors="coerce")
    if "dual_outperformer" in frame.columns:
        frame["dual_outperformer"] = _boolean_series(frame["dual_outperformer"])
    else:
        frame["dual_outperformer"] = False
    return frame.dropna(subset=["date", "code", "rank"]).drop_duplicates(["date", "code"], keep="last")


def build_market_regime_panel(history: pd.DataFrame, top_limit: int = 100) -> pd.DataFrame:
    """Rebuild the production market regime sequentially with no future rows."""
    records: list[dict[str, Any]] = []
    temperature_history = pd.DataFrame()
    for report_date in sorted(history["date"].astype(str).unique()):
        day_all = history[history["date"].astype(str) == report_date].copy()
        day_top = day_all[pd.to_numeric(day_all["rank"], errors="coerce") <= top_limit].copy()
        temperature = main.market_temperature(report_date, day_all, day_top, temperature_history)
        regime = main.calculate_market_regime(day_top, temperature)
        temperature = main.attach_market_regime_history(
            report_date, temperature, regime, temperature_history
        )
        regime = main.enrich_regime_from_temperature(regime, temperature)
        records.append({
            "signal_date": report_date,
            "market_regime": str(regime.get("label") or "中立"),
            "market_risk_budget": _number(regime.get("risk_budget")),
            "market_regime_reason": str(regime.get("reason") or ""),
        })
        temperature_history = (
            pd.concat([temperature_history, temperature], ignore_index=True)
            .drop_duplicates(["date"], keep="last")
            .sort_values("date")
        )
    return pd.DataFrame(records)


def attach_filter_context(signals: pd.DataFrame, history: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = signals.copy()
    result["signal_date"] = pd.to_datetime(result["signal_date"], errors="coerce").dt.date.astype(str)
    result["code"] = result["code"].map(main.normalize_code)
    context_columns = [
        "date", "code", "relative_strength_score", "relative_strength_rank",
        "relative_strength_grade", "dual_outperformer", "relative_strength_lifecycle",
        "relative_strength_alert", "relative_strength_trajectory_score",
        "relative_strength_strong_streak", "dual_outperformer_streak",
    ]
    available = [column for column in context_columns if column in history.columns]
    context = history[available].copy().rename(columns={"date": "signal_date"})
    result = result.merge(context, on=["signal_date", "code"], how="left", suffixes=("", "_history"))
    for column in [item for item in context_columns if item not in {"date", "code"}]:
        historical_column = f"{column}_history"
        if historical_column not in result.columns:
            continue
        if column not in result.columns:
            result[column] = result[historical_column]
        else:
            result[column] = result[column].where(result[column].notna(), result[historical_column])
        result = result.drop(columns=[historical_column])
    regime_panel = build_market_regime_panel(history)
    result = result.merge(regime_panel, on="signal_date", how="left")
    result["market_regime"] = result["market_regime"].fillna("中立")
    result["dual_outperformer"] = _boolean_series(result.get("dual_outperformer", pd.Series(False, index=result.index)))
    result["relative_strength_score"] = pd.to_numeric(result.get("relative_strength_score"), errors="coerce")
    coverage = pd.DataFrame([{
        "signal_count": len(result),
        "relative_strength_score_coverage": float(result["relative_strength_score"].notna().mean()) if len(result) else 0.0,
        "relative_strength_grade_coverage": float(result.get("relative_strength_grade", pd.Series(index=result.index, dtype=str)).fillna("").astype(str).str.strip().ne("").mean()) if len(result) else 0.0,
        "lifecycle_coverage": float(result.get("relative_strength_lifecycle", pd.Series(index=result.index, dtype=str)).fillna("").astype(str).str.strip().ne("").mean()) if len(result) else 0.0,
        "market_regime_coverage": float(result["market_regime"].fillna("").astype(str).str.strip().ne("").mean()) if len(result) else 0.0,
        "first_signal_date": result["signal_date"].min() if len(result) else "",
        "last_signal_date": result["signal_date"].max() if len(result) else "",
    }])
    result["signal_date"] = pd.to_datetime(result["signal_date"], errors="coerce")
    return result, coverage


def apply_rule(signals: pd.DataFrame, rule: FilterRule) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = signals.copy()
    condition_masks: dict[str, pd.Series] = {}
    if rule.allowed_rs_grades:
        condition_masks["relative_strength_grade"] = result.get(
            "relative_strength_grade", pd.Series(index=result.index, dtype=str)
        ).fillna("").astype(str).isin(rule.allowed_rs_grades)
    if rule.minimum_rs_score is not None:
        condition_masks["minimum_relative_strength_score"] = pd.to_numeric(
            result.get("relative_strength_score"), errors="coerce"
        ).ge(rule.minimum_rs_score).fillna(False)
    if rule.allowed_lifecycles:
        condition_masks["relative_strength_lifecycle"] = result.get(
            "relative_strength_lifecycle", pd.Series(index=result.index, dtype=str)
        ).fillna("").astype(str).isin(rule.allowed_lifecycles)
    if rule.require_dual_outperformer:
        condition_masks["dual_outperformer"] = _boolean_series(
            result.get("dual_outperformer", pd.Series(False, index=result.index))
        )
    if rule.allowed_market_regimes:
        condition_masks["market_regime"] = result.get(
            "market_regime", pd.Series(index=result.index, dtype=str)
        ).fillna("").astype(str).isin(rule.allowed_market_regimes)
    if rule.allowed_action_priorities:
        condition_masks["action_priority"] = result.get(
            "action_priority", pd.Series(index=result.index, dtype=str)
        ).fillna("").astype(str).isin(rule.allowed_action_priorities)
    if rule.allowed_sector_rotations:
        condition_masks["sector_rotation"] = result.get(
            "sector_rotation", pd.Series(index=result.index, dtype=str)
        ).fillna("").astype(str).isin(rule.allowed_sector_rotations)

    eligible = pd.Series(True, index=result.index)
    for mask in condition_masks.values():
        eligible &= mask.fillna(False)
    result["portfolio_eligible"] = eligible
    result["portfolio_filter_rule"] = rule.name
    if condition_masks:
        failures: list[str] = []
        for index in result.index:
            failed = [name for name, mask in condition_masks.items() if not bool(mask.loc[index])]
            failures.append(" / ".join(failed))
        result["portfolio_filter_rejection"] = failures
    else:
        result["portfolio_filter_rejection"] = ""

    audit_rows: list[dict[str, Any]] = []
    for report_date, group in result.groupby(result["signal_date"].dt.date.astype(str)):
        record: dict[str, Any] = {
            "filter_rule": rule.name,
            "signal_date": report_date,
            "total_signals": len(group),
            "eligible_signals": int(group["portfolio_eligible"].sum()),
            "eligible_ratio": float(group["portfolio_eligible"].mean()) if len(group) else 0.0,
        }
        for name, mask in condition_masks.items():
            record[f"rejected_{name}"] = int((~mask.loc[group.index]).sum())
        audit_rows.append(record)
    return result, pd.DataFrame(audit_rows)


def run_filter_rules(
    signals: pd.DataFrame,
    history: pd.DataFrame,
    prices: pd.DataFrame,
    rules: tuple[FilterRule, ...] = RULES,
) -> dict[str, pd.DataFrame]:
    enriched, coverage = attach_filter_context(signals, history)
    metrics_rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    equity_frames: list[pd.DataFrame] = []
    skip_frames: list[pd.DataFrame] = []
    audit_frames: list[pd.DataFrame] = []

    for rule in rules:
        flagged, audit = apply_rule(enriched, rule)
        scenario = portfolio.PortfolioScenario(
            rule.name,
            rule.maximum_positive_gap,
            rule.minimum_entry_trading_value,
            rule.maximum_participation,
        )
        result = portfolio.simulate_scenario(flagged, prices, scenario)
        metrics = dict(result["metrics"])
        metrics.update({
            "filter_rule": rule.name,
            "filter_description": rule.description,
            "eligible_signal_count": int(flagged["portfolio_eligible"].sum()),
            "total_signal_count": len(flagged),
            "eligible_signal_ratio": float(flagged["portfolio_eligible"].mean()) if len(flagged) else 0.0,
            "allowed_rs_grades": ",".join(rule.allowed_rs_grades),
            "allowed_lifecycles": ",".join(rule.allowed_lifecycles),
            "require_dual_outperformer": rule.require_dual_outperformer,
            "allowed_market_regimes": ",".join(rule.allowed_market_regimes),
            "allowed_action_priorities": ",".join(rule.allowed_action_priorities),
        })
        metrics_rows.append(metrics)
        for key, frames in (("trades", trade_frames), ("equity", equity_frames), ("skips", skip_frames)):
            frame = result[key].copy()
            if not frame.empty:
                frame["filter_rule"] = rule.name
                frames.append(frame)
        audit_frames.append(audit)

    metrics_frame = pd.DataFrame(metrics_rows)
    baseline_rows = metrics_frame[metrics_frame["filter_rule"] == "baseline"]
    if not baseline_rows.empty:
        baseline = baseline_rows.iloc[0]
        for column in ("total_return", "excess_total_return", "max_drawdown", "sharpe", "win_rate", "closed_trades", "turnover_ratio"):
            values = pd.to_numeric(metrics_frame.get(column), errors="coerce")
            base_value = _number(baseline.get(column))
            metrics_frame[f"delta_{column}_vs_baseline"] = values - base_value if base_value is not None else None
    metrics_frame["sample_status"] = metrics_frame.get("closed_trades", 0).map(
        lambda value: "INSUFFICIENT" if int(value or 0) < 5 else "EVALUABLE"
    )
    metrics_frame["improvement_status"] = "NOT_EVALUATED"
    if not baseline_rows.empty:
        evaluable = metrics_frame["sample_status"] == "EVALUABLE"
        improved_return = pd.to_numeric(metrics_frame.get("delta_excess_total_return_vs_baseline"), errors="coerce") > 0
        improved_drawdown = pd.to_numeric(metrics_frame.get("delta_max_drawdown_vs_baseline"), errors="coerce") >= 0
        metrics_frame.loc[evaluable & improved_return & improved_drawdown, "improvement_status"] = "IMPROVED"
        metrics_frame.loc[evaluable & ~(improved_return & improved_drawdown), "improvement_status"] = "NOT_IMPROVED"
        metrics_frame.loc[metrics_frame["filter_rule"] == "baseline", "improvement_status"] = "BASELINE"
    metrics_frame = metrics_frame.sort_values(
        ["sample_status", "excess_total_return", "max_drawdown"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    return {
        "metrics": metrics_frame,
        "trades": pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame(),
        "equity": pd.concat(equity_frames, ignore_index=True) if equity_frames else pd.DataFrame(),
        "skips": pd.concat(skip_frames, ignore_index=True) if skip_frames else pd.DataFrame(),
        "eligibility_audit": pd.concat(audit_frames, ignore_index=True) if audit_frames else pd.DataFrame(),
        "context_coverage": coverage,
        "enriched_signals": enriched,
    }


def write_outputs(results: dict[str, pd.DataFrame], provenance_path: str, output_dir: str) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    paths = {
        "metrics": output / "portfolio_filter_metrics.csv",
        "trades": output / "portfolio_filter_trades.csv",
        "equity": output / "portfolio_filter_equity.csv",
        "skips": output / "portfolio_filter_skips.csv",
        "audit": output / "portfolio_filter_eligibility_audit.csv",
        "coverage": output / "portfolio_filter_context_coverage.csv",
        "signals": output / "portfolio_filter_enriched_signals.csv",
        "excel": output / "portfolio_filter_lab.xlsx",
        "manifest": output / "portfolio_filter_manifest.json",
    }
    mapping = {
        "metrics": "metrics", "trades": "trades", "equity": "equity", "skips": "skips",
        "audit": "eligibility_audit", "coverage": "context_coverage", "signals": "enriched_signals",
    }
    for path_key, result_key in mapping.items():
        results[result_key].to_csv(paths[path_key], index=False)
    manifest = {
        "filter_lab_version": FILTER_LAB_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_promotion_evidence_allowed": provenance.get("promotion_evidence_allowed") is True,
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "automatic_filter_activation": False,
        "manual_review_required": True,
        "research_only": True,
        "production_state_mutations": [],
        "rule_count": len(RULES),
        "rules": [asdict(rule) for rule in RULES],
        "baseline_engine": "EXECUTION_AWARE_PORTFOLIO_V1",
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as writer:
        pd.DataFrame([manifest | {"rules": len(RULES)}]).drop(columns=["rules"], errors="ignore").to_excel(writer, sheet_name="Lab Summary", index=False)
        results["metrics"].to_excel(writer, sheet_name="Filter Metrics", index=False)
        results["trades"].to_excel(writer, sheet_name="Trades", index=False)
        results["equity"].to_excel(writer, sheet_name="Equity", index=False)
        results["skips"].to_excel(writer, sheet_name="Skipped Entries", index=False)
        results["eligibility_audit"].to_excel(writer, sheet_name="Eligibility Audit", index=False)
        results["context_coverage"].to_excel(writer, sheet_name="Context Coverage", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    max((len(str(cell.value or "")) for cell in column), default=8) + 2,
                    48,
                )
    return {"manifest": manifest, "paths": {key: str(value) for key, value in paths.items()}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare governed portfolio signal filters")
    parser.add_argument("--signals", default=DEFAULT_SIGNALS)
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--prices", default=DEFAULT_PRICES)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    before = replay.live_state_hashes()
    signals = portfolio.load_signals(args.signals)
    history = load_history(args.history)
    prices = portfolio.load_prices(args.prices)
    results = run_filter_rules(signals, history, prices)
    output = write_outputs(results, args.provenance, args.output_dir)
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    output["manifest"]["production_state_mutations"] = mutations
    Path(output["paths"]["manifest"]).write_text(
        json.dumps(output["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.strict:
        metrics = results["metrics"]
        coverage = results["context_coverage"].iloc[0]
        if mutations:
            raise RuntimeError(f"production state mutated: {mutations}")
        if set(metrics["filter_rule"]) != {rule.name for rule in RULES}:
            raise RuntimeError("one or more filter rules are missing")
        if float(coverage["relative_strength_score_coverage"]) < 0.99:
            raise RuntimeError("relative strength context coverage is below 99%")
        if float(coverage["lifecycle_coverage"]) < 0.99:
            raise RuntimeError("lifecycle context coverage is below 99%")
        if results["equity"].empty:
            raise RuntimeError("filter lab produced no equity curves")
        if not results["trades"].empty and pd.to_datetime(results["trades"]["entry_date"]).le(pd.to_datetime(results["trades"]["signal_date"])).any():
            raise RuntimeError("same-day or pre-signal entry detected")
    print(results["metrics"][[
        "filter_rule", "eligible_signal_count", "closed_trades", "total_return",
        "max_drawdown", "excess_total_return", "improvement_status",
    ]].to_string(index=False))
    print(json.dumps(output["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
