"""Shared definitions for Detailed OOS Evidence v2."""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import numpy as np
import pandas as pd
import yaml
VERSION = '2026-07-22-detailed-oos-evidence-v2'
METHODS = {'production': ('rank', 'score', None), 'healthy_v1': ('healthy_rank', 'healthy_selection_score', 'healthy_eligible'), 'balanced_v2': ('healthy_v2_rank', 'healthy_v2_selection_score', 'healthy_v2_eligible')}
BASELINES = {'baseline_return_5d': 'return_5d', 'baseline_return_20d': 'return_20d', 'baseline_relative_strength': 'healthy_relative_strength_score', 'baseline_ytd_streak': 'ytd_high_streak', 'baseline_volume_ratio': 'volume_ratio'}
DEFAULT_HORIZONS = (1, 3, 5, 10, 20, 40, 60)
DEFAULT_TOP_SIZES = (10, 30, 100)
ROUND_TRIP_COST_BPS = 20.0
RANK_BINS = [0, 10, 30, 50, 100, 300, np.inf]
RANK_LABELS = ['1-10', '11-30', '31-50', '51-100', '101-300', '301+']
@dataclass(frozen=True)
class Protocol:
    horizons: tuple[int, ...]
    top_sizes: tuple[int, ...]
    cost_bps: float
    random_repetitions: int
    primary_horizons: tuple[int, ...]
    primary_top_sizes: tuple[int, ...]
    minimum_years_positive: int
    minimum_rank_ic_positive_rate: float
    minimum_leave_one_sector_positive_rate: float

def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def load_protocol(path: str | Path) -> tuple[Protocol, dict[str, Any]]:
    raw = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    if raw.get('mode') != 'RESEARCH_ONLY_NON_PROMOTABLE':
        raise ValueError('detailed OOS protocol must remain RESEARCH_ONLY_NON_PROMOTABLE')
    if raw.get('automatic_strategy_change') is not False:
        raise ValueError('automatic_strategy_change must be false')
    if raw.get('promotion_evidence_allowed') is not False:
        raise ValueError('promotion_evidence_allowed must be false')
    evaluation = raw.get('evaluation') or {}
    gates = raw.get('evidence_gates') or {}
    protocol = Protocol(horizons=tuple((int(v) for v in evaluation.get('horizons', DEFAULT_HORIZONS))), top_sizes=tuple((int(v) for v in evaluation.get('top_sizes', DEFAULT_TOP_SIZES))), cost_bps=float(evaluation.get('round_trip_cost_bps', ROUND_TRIP_COST_BPS)), random_repetitions=int(evaluation.get('random_placebo_repetitions', 200)), primary_horizons=tuple((int(v) for v in gates.get('primary_horizons', (5, 10, 20)))), primary_top_sizes=tuple((int(v) for v in gates.get('primary_top_sizes', (10, 30)))), minimum_years_positive=int(gates.get('minimum_years_positive', 3)), minimum_rank_ic_positive_rate=float(gates.get('minimum_rank_ic_positive_rate', 0.55)), minimum_leave_one_sector_positive_rate=float(gates.get('minimum_leave_one_sector_positive_rate', 0.8)))
    return (protocol, raw)

def bool_series(frame: pd.DataFrame, column: str, default: bool=False) -> pd.Series:
    if column not in frame:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(default)
    return values.astype(str).str.strip().str.lower().isin({'true', '1', 'yes', 'y'})

def numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors='coerce')

def price_lookup(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {str(code): group.sort_values('date').reset_index(drop=True) for code, group in panel.groupby('code', sort=False)}

def spearman_pair(x: pd.Series, y: pd.Series) -> float:
    pair = pd.DataFrame({'x': pd.to_numeric(x, errors='coerce'), 'y': pd.to_numeric(y, errors='coerce')}).dropna()
    if len(pair) < 20 or pair['x'].nunique() < 2 or pair['y'].nunique() < 2:
        return np.nan
    return float(pair['x'].rank(method='average').corr(pair['y'].rank(method='average')))


def select_top(events: pd.DataFrame, top_size: int) -> pd.DataFrame:
    return events[events["eligible"] & events["method_rank"].le(int(top_size))].copy()
