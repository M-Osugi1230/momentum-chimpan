"""CLI and audited output writer for Detailed OOS Evidence v2."""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from detailed_oos_shared import *
from detailed_oos_metrics import *
def core_report_markdown(manifest: dict[str, Any], summary: pd.DataFrame, rank_ic_summary: pd.DataFrame) -> str:
    lines = ['# Detailed OOS Evidence v2 — Core Analysis', '', '> Research-only historical evidence. This report cannot promote or modify production ranking.', '', '## Scope', '', f"- Period: {manifest['evaluation_start']} to {manifest['evaluation_end']}", f"- Ranking dates: {manifest['ranking_date_count']}", f"- Ranking rows: {manifest['ranking_row_count']:,}", f"- Available horizons: {', '.join(map(str, manifest['available_horizons']))}", '', '## Primary date-weighted results', '', '| Year | Method | Top | Horizon | Return | Market excess | Win rate |', '|---:|---|---:|---:|---:|---:|---:|']
    focus = summary[summary['top_size'].isin([10, 30]) & summary['horizon_sessions'].isin([5, 10, 20])]
    for row in focus.sort_values(['year', 'horizon_sessions', 'top_size', 'method']).itertuples(index=False):
        lines.append(f'| {row.year} | {row.method} | {row.top_size} | {row.horizon_sessions} | {row.date_weighted_mean_net_return:.3%} | {row.mean_market_excess_net:.3%} | {row.win_rate:.1%} |')
    lines += ['', '## Rank IC', '', '| Year | Method | Horizon | Mean IC | Positive-date rate |', '|---:|---|---:|---:|---:|']
    focus_ic = rank_ic_summary[rank_ic_summary['horizon_sessions'].isin([5, 10, 20])]
    for row in focus_ic.sort_values(['year', 'horizon_sessions', 'method']).itertuples(index=False):
        lines.append(f'| {row.year} | {row.method} | {row.horizon_sessions} | {row.mean_rank_ic:.4f} | {row.positive_rank_ic_rate:.1%} |')
    lines += ['', '## Safety', '', '- Historical current-list backfill remains non-promotable.', '- No thresholds or production state were changed.', '']
    return '\n'.join(lines)

def load_existing_outputs(enriched_ranking_path: str, universe_outcomes_path: str, selection_events_path: str, backfill_manifest_path: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    ranking_columns = {'date', 'code', 'return_5d', 'return_20d', 'above_ma20'}
    outcome_columns = {'signal_date', 'code', 'sector33', 'horizon_sessions', 'rank', 'score', 'healthy_rank', 'healthy_selection_score', 'healthy_v2_rank', 'healthy_v2_selection_score', 'net_return', 'market_excess_net', 'mfe', 'mae'}
    selection_columns = {'signal_date', 'code', 'name', 'sector33', 'method_rank', 'method_score', 'method', 'horizon_sessions', 'entry_date', 'exit_date', 'net_return', 'market_excess_net', 'sector_excess_net', 'mfe', 'mae'}
    ranking = pd.read_csv(enriched_ranking_path, dtype={'code': str}, low_memory=False, usecols=lambda c: c in ranking_columns)
    outcomes = pd.read_csv(universe_outcomes_path, dtype={'code': str}, low_memory=False, usecols=lambda c: c in outcome_columns)
    selections = pd.read_csv(selection_events_path, dtype={'code': str}, low_memory=False, usecols=lambda c: c in selection_columns)
    manifest = json.loads(Path(backfill_manifest_path).read_text(encoding='utf-8'))
    if manifest.get('promotion_evidence_allowed') is not False:
        raise ValueError('source backfill must be non-promotable')
    if manifest.get('production_state_mutations') not in ([], None):
        raise ValueError('source backfill mutated production state')
    for frame, date_column in ((ranking, 'date'), (outcomes, 'signal_date'), (selections, 'signal_date')):
        frame['code'] = frame['code'].astype(str).str.split('.').str[0].str.zfill(4)
        frame[date_column] = pd.to_datetime(frame[date_column], errors='coerce').dt.normalize()
    for column in ('entry_date', 'exit_date'):
        if column in selections:
            selections[column] = pd.to_datetime(selections[column], errors='coerce').dt.normalize()
    outcomes['year'] = outcomes['signal_date'].dt.year.astype(int)
    selections['year'] = selections['signal_date'].dt.year.astype(int)
    return (ranking, outcomes, selections, manifest)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--enriched-ranking', required=True)
    parser.add_argument('--universe-outcomes', required=True)
    parser.add_argument('--selection-events', required=True)
    parser.add_argument('--prices', required=True)
    parser.add_argument('--backfill-manifest', required=True)
    parser.add_argument('--protocol', default='research/detailed_oos_protocol.yaml')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--strict', action='store_true')
    return parser.parse_args()

def main_cli() -> int:
    args = parse_args()
    protocol, raw_protocol = load_protocol(args.protocol)
    ranking, outcomes, selection_events, backfill_manifest = load_existing_outputs(args.enriched_ranking, args.universe_outcomes, args.selection_events, args.backfill_manifest)
    available_horizons = tuple(sorted((int(v) for v in outcomes['horizon_sessions'].dropna().unique())))
    expected_horizons = tuple((v for v in protocol.horizons if v in available_horizons))
    if not expected_horizons:
        raise RuntimeError('no protocol horizons are available in universe outcomes')
    effective_protocol = Protocol(horizons=expected_horizons, top_sizes=protocol.top_sizes, cost_bps=protocol.cost_bps, random_repetitions=protocol.random_repetitions, primary_horizons=tuple((v for v in protocol.primary_horizons if v in available_horizons)), primary_top_sizes=protocol.primary_top_sizes, minimum_years_positive=protocol.minimum_years_positive, minimum_rank_ic_positive_rate=protocol.minimum_rank_ic_positive_rate, minimum_leave_one_sector_positive_rate=protocol.minimum_leave_one_sector_positive_rate)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_events = selection_events.copy()
    selected_events['eligible'] = True
    selected_events['method_rank'] = pd.to_numeric(selected_events['method_rank'], errors='coerce')
    selected_events['method_score'] = pd.to_numeric(selected_events['method_score'], errors='coerce')
    selected_events['year'] = selected_events['signal_date'].dt.year.astype(int)
    if 'max_close_drawdown' not in selected_events:
        selected_events['max_close_drawdown'] = np.nan
    print('stage: summary', flush=True)
    summary = method_summary(selected_events, effective_protocol.top_sizes)
    print('stage: rank_ic', flush=True)
    ic_daily, ic_summary = rank_ic_from_outcomes(outcomes)
    print('stage: monotonicity', flush=True)
    monotonicity = rank_monotonicity_from_outcomes(outcomes)
    print('stage: calibration', flush=True)
    calibration = score_calibration_from_outcomes(outcomes)
    print('stage: regimes', flush=True)
    regimes = date_regimes(ranking)
    print('stage: regime_summary', flush=True)
    regimes_summary = regime_summary(selected_events, regimes, effective_protocol.top_sizes)
    print('stage: lifecycle', flush=True)
    lifecycle_detail, lifecycle_summary = signal_lifecycle(selected_events, effective_protocol.top_sizes)
    tables = {'method_summary_by_year.csv': summary, 'rank_ic_daily.csv': ic_daily, 'rank_ic_summary.csv': ic_summary, 'rank_monotonicity.csv': monotonicity, 'score_calibration.csv': calibration, 'date_regimes.csv': regimes, 'regime_summary.csv': regimes_summary, 'signal_lifecycle_detail.csv': lifecycle_detail, 'signal_lifecycle_summary.csv': lifecycle_summary}
    print('stage: write tables', flush=True)
    for filename, table in tables.items():
        table.to_csv(output_dir / filename, index=False)
    print('stage: manifest', flush=True)
    manifest = {'version': VERSION, 'generated_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'evaluation_start': str(ranking['date'].min().date()), 'evaluation_end': str(ranking['date'].max().date()), 'ranking_date_count': int(ranking['date'].nunique()), 'ranking_row_count': len(ranking), 'price_panel_row_count': int(backfill_manifest.get('price_panel_row_count', 0)), 'universe_outcome_rows': len(outcomes), 'selection_event_rows': len(selection_events), 'detailed_method_event_rows': len(selection_events), 'years': sorted((int(value) for value in ranking['date'].dt.year.unique())), 'methods': list(METHODS), 'requested_horizons': list(protocol.horizons), 'available_horizons': list(effective_protocol.horizons), 'top_sizes': list(effective_protocol.top_sizes), 'round_trip_cost_bps': effective_protocol.cost_bps, 'random_placebo_repetitions': effective_protocol.random_repetitions, 'research_only': True, 'promotion_evidence_allowed': False, 'automatic_strategy_change': False, 'production_state_mutations': [], 'source_universe_bias': backfill_manifest.get('universe_bias'), 'enriched_ranking_sha256': sha256_file(args.enriched_ranking), 'universe_outcomes_sha256': sha256_file(args.universe_outcomes), 'selection_events_sha256': sha256_file(args.selection_events), 'price_panel_sha256': sha256_file(args.prices), 'backfill_manifest_sha256': sha256_file(args.backfill_manifest), 'protocol_sha256': sha256_file(args.protocol), 'protocol': raw_protocol, 'output_rows': {filename: len(table) for filename, table in tables.items()}}
    (output_dir / 'detailed_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (output_dir / 'report.md').write_text(core_report_markdown(manifest, summary, ic_summary), encoding='utf-8')
    print('stage: strict', flush=True)
    if args.strict:
        if set(manifest['methods']) != set(METHODS):
            raise RuntimeError('method set mismatch')
        if len(manifest['years']) < 1:
            raise RuntimeError('no years analyzed')
        if summary.empty or ic_summary.empty:
            raise RuntimeError('required detailed evidence tables are empty')
        if selected_events.duplicated(['method', 'signal_date', 'code', 'horizon_sessions']).any():
            raise RuntimeError('duplicate method/date/code/horizon events')
        if not pd.to_datetime(selected_events['entry_date']).gt(pd.to_datetime(selected_events['signal_date'])).all():
            raise RuntimeError('entry is not strictly after signal date')
        if manifest['production_state_mutations']:
            raise RuntimeError('production state mutated')
        if manifest['promotion_evidence_allowed'] is not False:
            raise RuntimeError('historical evidence became promotable')
        if not set(effective_protocol.primary_horizons).issubset(set(effective_protocol.horizons)):
            raise RuntimeError('primary horizons unavailable')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0
