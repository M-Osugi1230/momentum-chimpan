"""Ablations, baselines, placebo, and robustness for Detailed OOS Evidence v2."""
from __future__ import annotations
import math
from typing import Any, Iterable
import numpy as np
import pandas as pd
from detailed_oos_shared import *
def ablation_summary(ranking: pd.DataFrame, outcomes: pd.DataFrame, top_sizes: Iterable[int], horizons: Iterable[int]) -> pd.DataFrame:
    required = {'healthy_exclusion_reasons', 'healthy_selection_score', 'date', 'code'}
    if not required.issubset(ranking.columns):
        return pd.DataFrame()
    reason_text = ranking['healthy_exclusion_reasons'].fillna('').astype(str)
    parsed = reason_text.map(lambda value: frozenset((item for item in value.split('|') if item)))
    all_reasons = sorted(set().union(*parsed.tolist())) if len(parsed) else []
    variants = [('ORIGINAL_V1', None)] + [(f'REMOVE_{reason}', reason) for reason in all_reasons]
    base_columns = [c for c in ['date', 'code', 'name', 'sector33', 'healthy_selection_score'] if c in ranking]
    max_top = max((int(v) for v in top_sizes))
    records: list[dict[str, Any]] = []
    outcome_columns = ['signal_date', 'code', 'horizon_sessions', 'net_return', 'market_excess_net', 'mae']
    outcome_view = outcomes[[c for c in outcome_columns if c in outcomes]].copy()
    for variant, removed_reason in variants:
        if removed_reason is None:
            mask = parsed.map(len).eq(0)
        else:
            mask = parsed.map(lambda values: values.issubset({removed_reason}))
        candidates = ranking.loc[mask, base_columns].copy()
        if candidates.empty:
            continue
        candidates['method_score'] = pd.to_numeric(candidates['healthy_selection_score'], errors='coerce')
        candidates = candidates.dropna(subset=['method_score'])
        candidates = candidates.sort_values(['date', 'method_score', 'code'], ascending=[True, False, True])
        candidates['method_rank'] = candidates.groupby('date').cumcount() + 1
        candidates = candidates[candidates['method_rank'].le(max_top)]
        candidates = candidates.rename(columns={'date': 'signal_date'})
        merged = candidates.merge(outcome_view, on=['signal_date', 'code'], how='inner', validate='one_to_many')
        merged = merged[merged['horizon_sessions'].isin(list(horizons))]
        merged['year'] = merged['signal_date'].dt.year.astype(int)
        for top_size in top_sizes:
            top = merged[merged['method_rank'].le(int(top_size))]
            for (year, horizon), group in top.groupby(['year', 'horizon_sessions'], sort=True):
                records.append({'year': int(year), 'ablation_variant': variant, 'top_size': int(top_size), 'horizon_sessions': int(horizon), 'observations': len(group), 'dates': group['signal_date'].nunique(), 'mean_eligible_count': candidates.groupby('signal_date')['code'].count().clip(upper=int(top_size)).mean(), 'date_weighted_mean_net_return': group.groupby('signal_date')['net_return'].mean().mean(), 'median_net_return': group['net_return'].median(), 'win_rate': group['net_return'].gt(0).mean(), 'mean_market_excess_net': group['market_excess_net'].mean(), 'mean_mae': group['mae'].mean() if 'mae' in group else np.nan})
        del candidates, merged
    result = pd.DataFrame(records)
    if result.empty:
        return result
    originals = result[result['ablation_variant'] == 'ORIGINAL_V1'][['year', 'top_size', 'horizon_sessions', 'date_weighted_mean_net_return', 'mean_mae']].rename(columns={'date_weighted_mean_net_return': 'original_v1_return', 'mean_mae': 'original_v1_mae'})
    result = result.merge(originals, on=['year', 'top_size', 'horizon_sessions'], how='left')
    result['return_delta_vs_original_v1'] = result['date_weighted_mean_net_return'] - result['original_v1_return']
    result['mae_delta_vs_original_v1'] = result['mean_mae'] - result['original_v1_mae']
    return result

def baseline_candidates(ranking: pd.DataFrame) -> pd.DataFrame:
    base_columns = ['date', 'code', 'name', 'sector33']
    frames: list[pd.DataFrame] = []
    for baseline, source in BASELINES.items():
        if source not in ranking:
            continue
        frame = ranking[base_columns + [source]].copy()
        frame['method_score'] = pd.to_numeric(frame[source], errors='coerce')
        frame['method_rank'] = frame.groupby('date')['method_score'].rank(method='first', ascending=False)
        frame['method'] = baseline
        frame['eligible'] = frame['method_score'].notna()
        frames.append(frame.drop(columns=[source]))
    composite = ranking[base_columns].copy()
    components: list[pd.Series] = []
    for source in ('return_5d', 'return_20d', 'healthy_relative_strength_score'):
        if source in ranking:
            components.append(pd.to_numeric(ranking[source], errors='coerce').groupby(ranking['date']).rank(pct=True))
    if 'ma20_deviation' in ranking:
        dev = pd.to_numeric(ranking['ma20_deviation'], errors='coerce')
        preference = (1.0 - (dev - 0.04).abs() / 0.16).clip(0, 1)
        components.append(preference)
    if components:
        composite['method_score'] = pd.concat(components, axis=1).mean(axis=1)
        composite['method_rank'] = composite.groupby('date')['method_score'].rank(method='first', ascending=False)
        composite['method'] = 'baseline_simple_balanced'
        composite['eligible'] = composite['method_score'].notna()
        frames.append(composite)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()

def summarize_candidate_methods(candidates: pd.DataFrame, outcomes: pd.DataFrame, top_sizes: Iterable[int]) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    candidates = candidates.rename(columns={'date': 'signal_date'})
    merged = candidates.merge(outcomes[['signal_date', 'code', 'horizon_sessions', 'net_return', 'market_excess_net', 'mae', 'mfe']], on=['signal_date', 'code'], how='inner', validate='many_to_many')
    merged['year'] = merged['signal_date'].dt.year.astype(int)
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = merged[merged['eligible'] & merged['method_rank'].le(int(top_size))]
        for keys, group in top.groupby(['year', 'method', 'horizon_sessions'], sort=True):
            year, method, horizon = keys
            records.append({'year': int(year), 'method': method, 'top_size': int(top_size), 'horizon_sessions': int(horizon), 'observations': len(group), 'dates': group['signal_date'].nunique(), 'date_weighted_mean_net_return': group.groupby('signal_date')['net_return'].mean().mean(), 'median_net_return': group['net_return'].median(), 'win_rate': group['net_return'].gt(0).mean(), 'mean_market_excess_net': group['market_excess_net'].mean(), 'mean_mae': group['mae'].mean(), 'mean_mfe': group['mfe'].mean()})
    return pd.DataFrame(records)

def select_top(events: pd.DataFrame, top_size: int) -> pd.DataFrame:
    return events[events["eligible"] & events["method_rank"].le(int(top_size))].copy()

def leave_one_sector_out(events: pd.DataFrame, top_sizes: Iterable[int]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for top_size in top_sizes:
        top = select_top(events, int(top_size))
        for keys, group in top.groupby(['year', 'method', 'horizon_sessions'], sort=True):
            year, method, horizon = keys
            base = group.groupby('signal_date')['net_return'].mean().mean()
            sectors = sorted((value for value in group['sector33'].fillna('').astype(str).unique() if value))
            for sector in sectors:
                reduced = group[group['sector33'].fillna('').astype(str) != sector]
                if reduced.empty:
                    continue
                value = reduced.groupby('signal_date')['net_return'].mean().mean()
                records.append({'year': int(year), 'method': method, 'top_size': int(top_size), 'horizon_sessions': int(horizon), 'excluded_sector': sector, 'base_return': base, 'excluded_return': value, 'delta_vs_base': value - base, 'remaining_observations': len(reduced)})
    return pd.DataFrame(records)

def random_placebo(ranking: pd.DataFrame, outcomes: pd.DataFrame, actual_summary: pd.DataFrame, top_sizes: Iterable[int], horizons: Iterable[int], repetitions: int, seed: int=20220722) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    keys = ranking[['date', 'code']].drop_duplicates(['date', 'code']).rename(columns={'date': 'signal_date'})
    merged = keys.merge(outcomes[['signal_date', 'code', 'horizon_sessions', 'net_return']], on=['signal_date', 'code'], how='inner', validate='one_to_many')
    merged['year'] = merged['signal_date'].dt.year.astype(int)
    random_distributions: dict[tuple[int, int, int], list[float]] = {}
    for (year, horizon), frame in merged.groupby(['year', 'horizon_sessions'], sort=True):
        if int(horizon) not in horizons:
            continue
        by_date = [group['net_return'].dropna().to_numpy(float) for _, group in frame.groupby('signal_date', sort=True)]
        for top_size in top_sizes:
            values: list[float] = []
            for _ in range(int(repetitions)):
                daily: list[float] = []
                for array in by_date:
                    if len(array) == 0:
                        continue
                    n = min(int(top_size), len(array))
                    chosen = rng.choice(array, size=n, replace=False)
                    daily.append(float(np.mean(chosen)))
                values.append(float(np.mean(daily)) if daily else np.nan)
            random_distributions[int(year), int(horizon), int(top_size)] = values
    records: list[dict[str, Any]] = []
    for row in actual_summary.itertuples(index=False):
        key = (int(row.year), int(row.horizon_sessions), int(row.top_size))
        distribution = np.asarray(random_distributions.get(key, []), dtype=float)
        distribution = distribution[np.isfinite(distribution)]
        if len(distribution) == 0:
            continue
        actual = float(row.date_weighted_mean_net_return)
        records.append({'year': int(row.year), 'method': row.method, 'top_size': int(row.top_size), 'horizon_sessions': int(row.horizon_sessions), 'actual_date_weighted_mean_net_return': actual, 'random_mean': float(distribution.mean()), 'random_std': float(distribution.std(ddof=1)), 'random_p05': float(np.quantile(distribution, 0.05)), 'random_p50': float(np.quantile(distribution, 0.5)), 'random_p95': float(np.quantile(distribution, 0.95)), 'one_sided_empirical_p': float((1 + np.sum(distribution >= actual)) / (len(distribution) + 1)), 'actual_minus_random_mean': actual - float(distribution.mean()), 'repetitions': len(distribution)})
    return pd.DataFrame(records)

def evidence_scorecard(summary: pd.DataFrame, rank_ic_summary: pd.DataFrame, leave_sector: pd.DataFrame, placebo: pd.DataFrame, protocol: Protocol) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    production = summary[summary['method'] == 'production'][['year', 'top_size', 'horizon_sessions', 'date_weighted_mean_net_return']].rename(columns={'date_weighted_mean_net_return': 'production_return'})
    comparison = summary[summary['method'].isin(['healthy_v1', 'balanced_v2'])].merge(production, on=['year', 'top_size', 'horizon_sessions'], how='left')
    comparison['delta_vs_production'] = comparison['date_weighted_mean_net_return'] - comparison['production_return']
    for method in ('healthy_v1', 'balanced_v2'):
        for top_size in protocol.primary_top_sizes:
            for horizon in protocol.primary_horizons:
                cells = comparison[(comparison['method'] == method) & (comparison['top_size'] == top_size) & (comparison['horizon_sessions'] == horizon)]
                years_positive = int(cells['delta_vs_production'].gt(0).sum())
                ic = rank_ic_summary[(rank_ic_summary['method'] == method) & (rank_ic_summary['horizon_sessions'] == horizon)]
                mean_ic_positive_rate = ic['positive_rank_ic_rate'].mean()
                loso = leave_sector[(leave_sector['method'] == method) & (leave_sector['top_size'] == top_size) & (leave_sector['horizon_sessions'] == horizon)]
                loso_positive_rate = loso['excluded_return'].gt(0).mean() if len(loso) else np.nan
                placebo_cells = placebo[(placebo['method'] == method) & (placebo['top_size'] == top_size) & (placebo['horizon_sessions'] == horizon)]
                placebo_pass_rate = placebo_cells['one_sided_empirical_p'].le(0.1).mean() if len(placebo_cells) else np.nan
                passes = {'year_consistency': years_positive >= protocol.minimum_years_positive, 'rank_ic_consistency': bool(pd.notna(mean_ic_positive_rate) and mean_ic_positive_rate >= protocol.minimum_rank_ic_positive_rate), 'leave_one_sector': bool(pd.notna(loso_positive_rate) and loso_positive_rate >= protocol.minimum_leave_one_sector_positive_rate), 'placebo': bool(pd.notna(placebo_pass_rate) and placebo_pass_rate >= 0.5)}
                records.append({'method': method, 'top_size': int(top_size), 'horizon_sessions': int(horizon), 'years_available': cells['year'].nunique(), 'years_outperforming_production': years_positive, 'mean_delta_vs_production': cells['delta_vs_production'].mean(), 'mean_positive_rank_ic_rate': mean_ic_positive_rate, 'leave_one_sector_positive_return_rate': loso_positive_rate, 'placebo_pass_year_rate': placebo_pass_rate, **{f'pass_{key}': value for key, value in passes.items()}, 'all_research_gates_pass': all(passes.values()), 'promotion_status': 'RESEARCH_SUPPORT_ONLY_NON_PROMOTABLE'})
    return pd.DataFrame(records)
