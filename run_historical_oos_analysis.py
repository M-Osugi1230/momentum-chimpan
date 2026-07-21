"""Safe entrypoint for the historical OOS analyzer.

This wrapper keeps the main analysis module readable while enforcing unique selection
columns for production, Healthy v1, and Balanced v2 and correcting historical market
breadth regime classification before running the full analysis.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

import analyze_historical_oos as analysis
import main


_ORIGINAL_BUILD_UNIVERSE_OUTCOMES = analysis.build_universe_outcomes


def build_universe_outcomes_fixed(
    ranking: pd.DataFrame,
    panel_by_code: dict[str, pd.DataFrame],
    horizons: Iterable[int],
) -> pd.DataFrame:
    outcomes = _ORIGINAL_BUILD_UNIVERSE_OUTCOMES(ranking, panel_by_code, horizons)
    if outcomes.empty:
        return outcomes
    daily_breadth = (
        outcomes.drop_duplicates(["signal_date", "code"])
        .groupby("signal_date")["return_20d"]
        .median()
    )
    outcomes = outcomes.drop(columns=["market_breadth_quintile"], errors="ignore")
    if daily_breadth.dropna().nunique() >= 5:
        percentiles = daily_breadth.rank(method="average", pct=True)
        quintiles = np.ceil(
            percentiles.clip(lower=1e-12, upper=1.0) * 5
        ).astype("Int64")
        outcomes = outcomes.join(
            quintiles.rename("market_breadth_quintile"), on="signal_date"
        )
    else:
        outcomes["market_breadth_quintile"] = pd.NA
    return outcomes


def select_method_events_fixed(
    ranking: pd.DataFrame,
    universe_outcomes: pd.DataFrame,
    top_limit: int,
) -> pd.DataFrame:
    selections: list[pd.DataFrame] = []
    for method, (rank_column, eligible_column, score_column) in analysis.METHODS.items():
        for _, group in ranking.groupby("date", sort=True):
            candidates = group.copy()
            if eligible_column:
                candidates = candidates[candidates[eligible_column].fillna(False)]
            candidates = candidates.sort_values(rank_column, na_position="last").head(top_limit)
            if candidates.empty:
                continue
            requested = [
                "date",
                "code",
                "name",
                "sector33",
                rank_column,
                score_column,
                "rank",
                "score",
                "return_5d",
                "return_20d",
                "return_60d",
                "ma20_deviation",
                "ma60_deviation",
                "volume_ratio",
                "trading_value",
                "healthy_v2_confirmation_score",
                "healthy_v2_confirmation_state",
                "healthy_v2_caution_reasons",
            ]
            selected_columns = list(
                dict.fromkeys(column for column in requested if column in candidates.columns)
            )
            selected = candidates[selected_columns].copy()
            selected = selected.rename(
                columns={
                    "date": "signal_date",
                    rank_column: "method_rank",
                    score_column: "method_score",
                }
            )
            selected["method"] = method
            selections.append(selected)
    if not selections:
        return pd.DataFrame()
    selection_table = pd.concat(selections, ignore_index=True, sort=False)
    if selection_table.columns.duplicated().any():
        duplicated = selection_table.columns[selection_table.columns.duplicated()].tolist()
        raise RuntimeError(f"duplicate selection columns: {duplicated}")
    selection_table["code"] = selection_table["code"].map(main.normalize_code)
    selection_table["signal_date"] = pd.to_datetime(
        selection_table["signal_date"], errors="coerce"
    ).dt.normalize()
    outcome_columns = [
        "signal_date",
        "code",
        "horizon_sessions",
        "entry_date",
        "entry_price",
        "exit_date",
        "exit_price",
        "gross_return",
        "net_return",
        "mfe",
        "mae",
        "market_median_return",
        "sector_median_return",
        "market_excess_gross",
        "market_excess_net",
        "sector_excess_gross",
        "sector_excess_net",
        "quarter",
        "month",
        "market_breadth_quintile",
    ]
    merged = selection_table.merge(
        universe_outcomes[outcome_columns],
        on=["signal_date", "code"],
        how="inner",
        validate="many_to_many",
    )
    expected_duplicates = merged.duplicated(
        ["method", "signal_date", "code", "horizon_sessions"]
    )
    if expected_duplicates.any():
        raise RuntimeError("duplicate method/date/code/horizon outcome rows")
    return merged.sort_values(
        ["method", "signal_date", "method_rank", "horizon_sessions"]
    ).reset_index(drop=True)


def main_cli() -> int:
    analysis.build_universe_outcomes = build_universe_outcomes_fixed
    analysis.select_method_events = select_method_events_fixed
    return analysis.main_cli()


if __name__ == "__main__":
    raise SystemExit(main_cli())
