"""Price-path analysis for Detailed OOS Evidence v2."""
from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd
from detailed_oos_shared import *

MAX_ENTRY_GAP_DAYS = 7
MAX_SESSION_GAP_DAYS = 10
MAX_ADJACENT_PRICE_MULTIPLIER = 4.0


def path_quality(methods: pd.DataFrame, panel_by_code: dict[str, pd.DataFrame], max_horizon: int=60, up_threshold: float=0.05, down_threshold: float=-0.05) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = methods[methods['eligible'] & methods['method_rank'].le(100)].drop_duplicates(['method', 'signal_date', 'code'])
    records: list[dict[str, Any]] = []
    for row in selected.itertuples(index=False):
        prices = panel_by_code.get(str(row.code))
        if prices is None or prices.empty:
            continue
        dates = prices['date'].to_numpy(dtype='datetime64[ns]')
        entry_pos = int(np.searchsorted(dates, np.datetime64(row.signal_date), side='right'))
        if entry_pos >= len(prices):
            continue
        window = prices.iloc[entry_pos:min(entry_pos + max_horizon, len(prices))].copy()
        if window.empty:
            continue
        entry_date = pd.Timestamp(window.iloc[0]['date'])
        entry_gap_days = int((entry_date.normalize() - pd.Timestamp(row.signal_date).normalize()).days)
        if entry_gap_days < 1 or entry_gap_days > MAX_ENTRY_GAP_DAYS:
            continue
        if 'volume' in window.columns:
            volume = pd.to_numeric(window['volume'], errors='coerce')
            if volume.isna().any() or volume.le(0).any():
                continue
        session_gaps = pd.to_datetime(window['date'], errors='coerce').diff().dt.days.dropna()
        max_session_gap_days = int(session_gaps.max()) if len(session_gaps) else 0
        if max_session_gap_days > MAX_SESSION_GAP_DAYS:
            continue
        close_prices = pd.to_numeric(window['adjusted_close'], errors='coerce')
        adjacent_ratio = close_prices / close_prices.shift(1)
        valid_ratio = adjacent_ratio.dropna()
        if valid_ratio.gt(MAX_ADJACENT_PRICE_MULTIPLIER).any() or valid_ratio.lt(1.0 / MAX_ADJACENT_PRICE_MULTIPLIER).any():
            continue
        entry_price = float(window.iloc[0]['adjusted_open'])
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue
        highs = pd.to_numeric(window['adjusted_high'], errors='coerce').to_numpy(float) / entry_price - 1.0
        lows = pd.to_numeric(window['adjusted_low'], errors='coerce').to_numpy(float) / entry_price - 1.0
        closes = close_prices.to_numpy(float) / entry_price - 1.0
        if not np.isfinite(highs).all() or not np.isfinite(lows).all() or not np.isfinite(closes).all():
            continue
        up_hits = np.flatnonzero(highs >= up_threshold)
        down_hits = np.flatnonzero(lows <= down_threshold)
        up_session = int(up_hits[0] + 1) if len(up_hits) else None
        down_session = int(down_hits[0] + 1) if len(down_hits) else None
        if up_session is None and down_session is None:
            first_touch = 'NEITHER'
        elif up_session is not None and down_session is not None and (up_session == down_session):
            first_touch = 'BOTH_SAME_SESSION'
        elif down_session is None or (up_session is not None and up_session < down_session):
            first_touch = 'UP_5_FIRST'
        else:
            first_touch = 'DOWN_5_FIRST'
        running_high = np.maximum.accumulate(np.r_[0.0, closes])
        close_level = np.r_[0.0, closes]
        drawdown = (1.0 + close_level) / (1.0 + running_high) - 1.0
        records.append({'method': row.method, 'signal_date': pd.Timestamp(row.signal_date), 'year': int(pd.Timestamp(row.signal_date).year), 'code': str(row.code), 'name': getattr(row, 'name', ''), 'sector33': getattr(row, 'sector33', ''), 'method_rank': float(row.method_rank), 'method_score': float(row.method_score) if pd.notna(row.method_score) else np.nan, 'entry_date': entry_date, 'entry_price': entry_price, 'entry_gap_days': entry_gap_days, 'max_session_gap_days': max_session_gap_days, 'path_data_quality': 'OK', 'available_sessions': len(window), 'first_up_5_session': up_session, 'first_down_5_session': down_session, 'first_touch_5pct': first_touch, 'mfe_60': float(np.nanmax(highs)), 'mae_60': float(np.nanmin(lows)), 'time_to_mfe_session': int(np.nanargmax(highs) + 1), 'time_to_mae_session': int(np.nanargmin(lows) + 1), 'max_close_drawdown_60': float(np.nanmin(drawdown)), 'positive_close_session_ratio': float(np.nanmean(closes > 0)), 'terminal_return_available': float(closes[-1])})
    detail = pd.DataFrame(records)
    summary_records: list[dict[str, Any]] = []
    if not detail.empty:
        detail['rank_band'] = pd.cut(detail['method_rank'], bins=[0, 10, 30, 100], labels=['1-10', '11-30', '31-100'])
        for keys, group in detail.groupby(['year', 'method', 'rank_band'], observed=True, sort=True):
            year, method, band = keys
            summary_records.append({'year': int(year), 'method': method, 'rank_band': str(band), 'observations': len(group), 'up_5_first_rate': group['first_touch_5pct'].eq('UP_5_FIRST').mean(), 'down_5_first_rate': group['first_touch_5pct'].eq('DOWN_5_FIRST').mean(), 'neither_rate': group['first_touch_5pct'].eq('NEITHER').mean(), 'median_first_up_5_session': group['first_up_5_session'].median(), 'median_first_down_5_session': group['first_down_5_session'].median(), 'mean_mfe_60': group['mfe_60'].mean(), 'mean_mae_60': group['mae_60'].mean(), 'mean_max_close_drawdown_60': group['max_close_drawdown_60'].mean(), 'mean_positive_close_session_ratio': group['positive_close_session_ratio'].mean()})
    return (detail, pd.DataFrame(summary_records))


def top_method_candidates(ranking: pd.DataFrame, limit: int=100) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    base_columns = ['date', 'code', 'name', 'sector33']
    for method, (rank_column, score_column, _) in METHODS.items():
        columns = [c for c in base_columns + [rank_column, score_column] if c in ranking]
        frame = ranking[columns].copy()
        frame[rank_column] = pd.to_numeric(frame[rank_column], errors='coerce')
        frame = frame[frame[rank_column].notna() & frame[rank_column].le(int(limit))]
        frame = frame.rename(columns={'date': 'signal_date', rank_column: 'method_rank', score_column: 'method_score'})
        frame['method'] = method
        frame['eligible'] = True
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True, sort=False)
    result['signal_date'] = pd.to_datetime(result['signal_date'], errors='coerce').dt.normalize()
    return result
