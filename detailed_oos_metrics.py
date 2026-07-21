"""Core statistical tables for Detailed OOS Evidence v2."""
from __future__ import annotations
from typing import Any, Iterable
import numpy as np
import pandas as pd
from detailed_oos_shared import *
def date_regimes(ranking: pd.DataFrame) -> pd.DataFrame:
    work = ranking.copy()
    work['date'] = pd.to_datetime(work['date'], errors='coerce').dt.normalize()
    work['return_20d'] = numeric(work, 'return_20d')
    work['return_5d'] = numeric(work, 'return_5d')
    work['above_ma20_bool'] = bool_series(work, 'above_ma20')
    grouped = work.groupby('date', sort=True)
    regime = grouped.agg(ranked_count=('code', 'count'), median_return_20d=('return_20d', 'median'), positive_20d_ratio=('return_20d', lambda s: pd.to_numeric(s, errors='coerce').gt(0).mean()), above_ma20_ratio=('above_ma20_bool', 'mean'), median_abs_return_5d=('return_5d', lambda s: pd.to_numeric(s, errors='coerce').abs().median()), dispersion_return_20d=('return_20d', 'std')).reset_index().rename(columns={'date': 'signal_date'})

    def trend(row: pd.Series) -> str:
        if row['median_return_20d'] >= 0.05 and row['positive_20d_ratio'] >= 0.6 and (row['above_ma20_ratio'] >= 0.6):
            return 'BROAD_BULL'
        if row['median_return_20d'] >= 0.03 and row['positive_20d_ratio'] < 0.6:
            return 'NARROW_BULL'
        if row['median_return_20d'] < 0 and row['positive_20d_ratio'] < 0.45:
            return 'BEAR_OR_RISK_OFF'
        return 'RANGE_OR_MIXED'
    regime['trend_regime'] = regime.apply(trend, axis=1)
    valid_vol = regime['median_abs_return_5d'].dropna()
    if valid_vol.nunique() >= 3:
        q1, q2 = valid_vol.quantile([1 / 3, 2 / 3]).tolist()
        regime['volatility_regime'] = pd.cut(regime['median_abs_return_5d'], [-np.inf, q1, q2, np.inf], labels=['LOW_VOL', 'MID_VOL', 'HIGH_VOL'], include_lowest=True).astype(str)
    else:
        regime['volatility_regime'] = 'UNKNOWN'
    regime['year'] = regime['signal_date'].dt.year.astype(int)
    return regime

def regime_summary(events: pd.DataFrame, regimes: pd.DataFrame, top_sizes: Iterable[int]) -> pd.DataFrame:
    merged = events.merge(regimes, on=['signal_date', 'year'], how='left', validate='many_to_one')
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = merged[merged['eligible'] & merged['method_rank'].le(int(top_size))]
        for dimension in ('trend_regime', 'volatility_regime'):
            for keys, group in top.groupby(['year', 'method', 'horizon_sessions', dimension], dropna=False, sort=True):
                year, method, horizon, value = keys
                records.append({'dimension': dimension, 'regime': str(value), 'year': int(year), 'method': method, 'top_size': int(top_size), 'horizon_sessions': int(horizon), 'observations': len(group), 'dates': group['signal_date'].nunique(), 'date_weighted_mean_net_return': group.groupby('signal_date')['net_return'].mean().mean(), 'median_net_return': group['net_return'].median(), 'win_rate': group['net_return'].gt(0).mean(), 'mean_market_excess_net': group['market_excess_net'].mean(), 'mean_mae': group['mae'].mean()})
    return pd.DataFrame(records)

def signal_lifecycle(events: pd.DataFrame, top_sizes: Iterable[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    unique = events.drop_duplicates(['method', 'signal_date', 'code']).copy()
    unique = unique.sort_values(['method', 'code', 'signal_date'])
    group_keys = ['method', 'code']
    unique['selection_number'] = unique.groupby(group_keys).cumcount() + 1
    unique['first_selection_date'] = unique.groupby(group_keys)['signal_date'].transform('min')
    unique['days_since_first'] = (unique['signal_date'] - unique['first_selection_date']).dt.days
    unique['previous_rank'] = unique.groupby(group_keys)['method_rank'].shift(1)
    unique['previous_signal_date'] = unique.groupby(group_keys)['signal_date'].shift(1)
    unique['days_since_previous'] = (unique['signal_date'] - unique['previous_signal_date']).dt.days
    unique['rank_delta_vs_previous'] = unique['previous_rank'] - unique['method_rank']
    typical_gap = unique.groupby('method')['signal_date'].apply(lambda values: pd.Series(sorted(values.dropna().unique())).diff().dt.days.median()).to_dict()

    def classify(row: pd.Series) -> str:
        if int(row['selection_number']) == 1:
            return 'FIRST_PICK'
        gap = typical_gap.get(row['method'], 7) or 7
        if pd.notna(row['days_since_previous']) and row['days_since_previous'] > 1.8 * gap:
            return 'REENTRY'
        delta = row['rank_delta_vs_previous']
        if pd.isna(delta):
            return 'REPEAT_UNKNOWN'
        if delta >= 10:
            return 'IMPROVING'
        if delta <= -10:
            return 'DETERIORATING'
        return 'STABLE_REPEAT'
    unique['lifecycle_state'] = unique.apply(classify, axis=1)
    metadata_columns = ['method', 'signal_date', 'code', 'selection_number', 'first_selection_date', 'days_since_first', 'previous_rank', 'previous_signal_date', 'days_since_previous', 'rank_delta_vs_previous', 'lifecycle_state']
    enriched = events.merge(unique[metadata_columns], on=['method', 'signal_date', 'code'], how='left', validate='many_to_one')
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = enriched[enriched['method_rank'].le(int(top_size))]
        for (year, method, horizon, state), group in top.groupby(['year', 'method', 'horizon_sessions', 'lifecycle_state'], sort=True):
            records.append({'year': int(year), 'method': method, 'top_size': int(top_size), 'horizon_sessions': int(horizon), 'lifecycle_state': state, 'observations': len(group), 'stocks': group['code'].nunique(), 'date_weighted_mean_net_return': group.groupby('signal_date')['net_return'].mean().mean(), 'median_net_return': group['net_return'].median(), 'win_rate': group['net_return'].gt(0).mean(), 'mean_market_excess_net': group['market_excess_net'].mean(), 'mean_mae': group['mae'].mean()})
    detail_columns = [c for c in ['method', 'signal_date', 'code', 'name', 'sector33', 'method_rank', 'method_score'] + metadata_columns[3:] if c in unique.columns]
    detail = unique[detail_columns].copy()
    return (detail, pd.DataFrame(records))

def method_summary(events: pd.DataFrame, top_sizes: Iterable[int]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = select_top(events, int(top_size))
        for keys, group in top.groupby(['year', 'method', 'horizon_sessions'], sort=True):
            year, method, horizon = keys
            daily = group.groupby('signal_date')['net_return'].mean()
            records.append({'year': int(year), 'method': method, 'top_size': int(top_size), 'horizon_sessions': int(horizon), 'observations': len(group), 'stocks': group['code'].nunique(), 'dates': group['signal_date'].nunique(), 'mean_net_return': group['net_return'].mean(), 'date_weighted_mean_net_return': daily.mean(), 'median_net_return': group['net_return'].median(), 'win_rate': group['net_return'].gt(0).mean(), 'mean_market_excess_net': group['market_excess_net'].mean(), 'beat_market_rate': group['market_excess_net'].gt(0).mean(), 'mean_sector_excess_net': group['sector_excess_net'].mean(), 'mean_mfe': group['mfe'].mean(), 'mean_mae': group['mae'].mean(), 'mean_max_close_drawdown': group['max_close_drawdown'].mean()})
    return pd.DataFrame(records)

def rank_ic_from_outcomes(outcomes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_records: list[dict[str, Any]] = []
    for method, (rank_column, score_column, _) in METHODS.items():
        if rank_column not in outcomes or score_column not in outcomes:
            continue
        subset = outcomes[['signal_date', 'horizon_sessions', rank_column, score_column, 'net_return']].copy()
        subset[rank_column] = pd.to_numeric(subset[rank_column], errors='coerce')
        subset[score_column] = pd.to_numeric(subset[score_column], errors='coerce')
        subset = subset[subset[rank_column].notna()]
        for (horizon, date), group in subset.groupby(['horizon_sessions', 'signal_date'], sort=True):
            daily_records.append({'method': method, 'horizon_sessions': int(horizon), 'signal_date': date, 'year': int(pd.Timestamp(date).year), 'observations': len(group), 'rank_ic': spearman_pair(-group[rank_column], group['net_return']), 'score_ic': spearman_pair(group[score_column], group['net_return'])})
    daily = pd.DataFrame(daily_records)
    records: list[dict[str, Any]] = []
    for (year, method, horizon), group in daily.groupby(['year', 'method', 'horizon_sessions'], sort=True):
        rank_values = group['rank_ic'].dropna()
        score_values = group['score_ic'].dropna()
        records.append({'year': int(year), 'method': method, 'horizon_sessions': int(horizon), 'dates': group['signal_date'].nunique(), 'mean_rank_ic': rank_values.mean(), 'median_rank_ic': rank_values.median(), 'positive_rank_ic_rate': rank_values.gt(0).mean(), 'mean_score_ic': score_values.mean(), 'positive_score_ic_rate': score_values.gt(0).mean()})
    return (daily, pd.DataFrame(records))

def rank_monotonicity_from_outcomes(outcomes: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for method, (rank_column, _, _) in METHODS.items():
        if rank_column not in outcomes:
            continue
        columns = ['signal_date', 'code', 'sector33', 'horizon_sessions', 'net_return', 'market_excess_net', 'mae', rank_column]
        subset = outcomes[[c for c in columns if c in outcomes]].copy()
        subset[rank_column] = pd.to_numeric(subset[rank_column], errors='coerce')
        subset = subset[subset[rank_column].notna()]
        subset['year'] = subset['signal_date'].dt.year.astype(int)
        subset['rank_band'] = pd.cut(subset[rank_column], bins=RANK_BINS, labels=RANK_LABELS, right=True)
        for (year, horizon, band), group in subset.groupby(['year', 'horizon_sessions', 'rank_band'], observed=True, sort=True):
            records.append({'year': int(year), 'method': method, 'horizon_sessions': int(horizon), 'rank_band': str(band), 'observations': len(group), 'stocks': group['code'].nunique(), 'dates': group['signal_date'].nunique(), 'mean_net_return': group['net_return'].mean(), 'date_weighted_mean_net_return': group.groupby('signal_date')['net_return'].mean().mean(), 'median_net_return': group['net_return'].median(), 'win_rate': group['net_return'].gt(0).mean(), 'mean_market_excess_net': group['market_excess_net'].mean(), 'mean_mae': group['mae'].mean() if 'mae' in group else np.nan})
    return pd.DataFrame(records)

def score_calibration_from_outcomes(outcomes: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for method, (rank_column, score_column, _) in METHODS.items():
        if rank_column not in outcomes or score_column not in outcomes:
            continue
        columns = ['signal_date', 'horizon_sessions', 'net_return', 'market_excess_net', 'mfe', 'mae', rank_column, score_column]
        subset = outcomes[[c for c in columns if c in outcomes]].copy()
        subset[rank_column] = pd.to_numeric(subset[rank_column], errors='coerce')
        subset[score_column] = pd.to_numeric(subset[score_column], errors='coerce')
        subset = subset[subset[rank_column].notna() & subset[score_column].notna()]
        subset['year'] = subset['signal_date'].dt.year.astype(int)
        subset['score_percentile'] = subset.groupby('signal_date')[score_column].rank(method='average', pct=True)
        subset['score_decile'] = np.ceil(subset['score_percentile'].clip(1e-12, 1.0) * 10).astype(int)
        for (year, horizon, decile), group in subset.groupby(['year', 'horizon_sessions', 'score_decile'], sort=True):
            records.append({'year': int(year), 'method': method, 'horizon_sessions': int(horizon), 'score_decile': int(decile), 'observations': len(group), 'dates': group['signal_date'].nunique(), 'mean_score': group[score_column].mean(), 'mean_net_return': group['net_return'].mean(), 'median_net_return': group['net_return'].median(), 'win_rate': group['net_return'].gt(0).mean(), 'positive_5pct_rate': group['net_return'].ge(0.05).mean(), 'mean_market_excess_net': group['market_excess_net'].mean(), 'mean_mae': group['mae'].mean() if 'mae' in group else np.nan, 'mean_mfe': group['mfe'].mean() if 'mfe' in group else np.nan})
    return pd.DataFrame(records)
