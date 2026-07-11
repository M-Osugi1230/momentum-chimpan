from pathlib import Path


module_path = Path('portfolio_regime_attribution.py')
text = module_path.read_text(encoding='utf-8')
text = text.replace(
    'ATTRIBUTION_VERSION = "2026-07-11-portfolio-regime-attribution-v1"',
    'ATTRIBUTION_VERSION = "2026-07-11-portfolio-regime-attribution-v2-signal-window"',
    1,
)
anchor = '''def safe_profit_factor(pnl: pd.Series) -> float | None:
    values = pd.to_numeric(pnl, errors="coerce").dropna()
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    if gross_loss > 0:
        return gross_profit / gross_loss
    return None if gross_profit == 0 else float("inf")


'''
addition = anchor + '''def align_prices_to_signal_window(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    maximum_holding_sessions: int = portfolio.MAX_HOLDING_SESSIONS,
) -> pd.DataFrame:
    """Align benchmark and portfolio dates to the tradable signal window.

    Historical price panels intentionally include warm-up data for indicators.
    Those pre-signal sessions must not accrue benchmark return while the
    portfolio is structurally unable to hold a position.
    """
    signal_dates = pd.to_datetime(signals.get("signal_date"), errors="coerce").dropna().dt.normalize()
    if signal_dates.empty or prices is None or prices.empty:
        return prices.copy()
    price_dates = pd.DatetimeIndex(
        sorted(pd.to_datetime(prices["date"], errors="coerce").dropna().dt.normalize().unique())
    )
    if price_dates.empty:
        return prices.copy()
    start = signal_dates.min()
    last_signal = signal_dates.max()
    end_index = int(price_dates.searchsorted(last_signal, side="right"))
    end_index = min(end_index + maximum_holding_sessions + 2, len(price_dates))
    end = price_dates[end_index - 1] if end_index else last_signal
    normalized = pd.to_datetime(prices["date"], errors="coerce").dt.normalize()
    return prices[(normalized >= start) & (normalized <= end)].copy()


'''
if 'def align_prices_to_signal_window(' not in text:
    if anchor not in text:
        raise RuntimeError('safe profit factor anchor not found')
    text = text.replace(anchor, addition, 1)
old_run = '''def run_attribution(signals: pd.DataFrame, history: pd.DataFrame, prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    enriched_signals, coverage = filter_lab.attach_filter_context(signals, history)
    baseline = run_baseline(enriched_signals, prices)
    trades = enrich_trades(baseline["trades"], enriched_signals)
    daily = attach_daily_regime(baseline["equity"], history)
    return {
        "baseline_metrics": pd.DataFrame([baseline["metrics"]]),
        "trades": trades,
        "trade_attribution": build_trade_attribution(trades),
        "daily_equity": daily,
        "daily_regime_attribution": build_daily_regime_attribution(daily),
        "quarterly_stability": build_quarterly_stability(daily),
        "rolling_stability": build_rolling_stability(daily),
        "counterfactuals": run_counterfactuals(enriched_signals, prices, baseline["metrics"]),
        "context_coverage": coverage,
    }
'''
new_run = '''def run_attribution(signals: pd.DataFrame, history: pd.DataFrame, prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    enriched_signals, coverage = filter_lab.attach_filter_context(signals, history)
    aligned_prices = align_prices_to_signal_window(enriched_signals, prices)
    baseline = run_baseline(enriched_signals, aligned_prices)
    trades = enrich_trades(baseline["trades"], enriched_signals)
    daily = attach_daily_regime(baseline["equity"], history)
    return {
        "baseline_metrics": pd.DataFrame([baseline["metrics"]]),
        "trades": trades,
        "trade_attribution": build_trade_attribution(trades),
        "daily_equity": daily,
        "daily_regime_attribution": build_daily_regime_attribution(daily),
        "quarterly_stability": build_quarterly_stability(daily),
        "rolling_stability": build_rolling_stability(daily),
        "counterfactuals": run_counterfactuals(enriched_signals, aligned_prices, baseline["metrics"]),
        "context_coverage": coverage,
    }
'''
if old_run in text:
    text = text.replace(old_run, new_run, 1)
elif 'aligned_prices = align_prices_to_signal_window' not in text:
    raise RuntimeError('run attribution anchor not found')
manifest_anchor = '        "same_day_close_entry_allowed": False,\n'
if '"price_window_aligned_to_signals": True' not in text:
    text = text.replace(
        manifest_anchor,
        manifest_anchor + '        "price_window_aligned_to_signals": True,\n',
        1,
    )
module_path.write_text(text, encoding='utf-8')


test_path = Path('.github/test_portfolio_regime_attribution.py')
test = test_path.read_text(encoding='utf-8')
assertion_anchor = 'results = attribution.run_attribution(signals, history, prices)\n'
if 'aligned_prices = attribution.align_prices_to_signal_window' not in test:
    test = test.replace(
        assertion_anchor,
        'aligned_prices = attribution.align_prices_to_signal_window(signals, prices)\n'
        'assert pd.to_datetime(aligned_prices["date"]).min().normalize() >= signals["signal_date"].min().normalize()\n'
        'assert len(aligned_prices) < len(prices)\n\n'
        + assertion_anchor,
        1,
    )
manifest_test_anchor = '    assert manifest["same_day_close_entry_allowed"] is False\n'
if 'price_window_aligned_to_signals' not in test:
    test = test.replace(
        manifest_test_anchor,
        manifest_test_anchor + '    assert manifest["price_window_aligned_to_signals"] is True\n',
        1,
    )
test_path.write_text(test, encoding='utf-8')
print('attribution signal window correction applied')
