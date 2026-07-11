from pathlib import Path


path = Path("portfolio_research.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if old not in text:
        raise RuntimeError(f"anchor not found: {label}")
    text = text.replace(old, new, 1)


replace_once(
    'PORTFOLIO_RESEARCH_VERSION = "2026-07-11-execution-portfolio-v2-eligibility-calendar"',
    'PORTFOLIO_RESEARCH_VERSION = "2026-07-11-execution-portfolio-v3-exit-policy"',
    "portfolio version",
)
replace_once(
    '''@dataclass(frozen=True)
class PortfolioScenario:
    name: str
    maximum_positive_gap: float | None
    minimum_entry_trading_value: float
    maximum_participation: float


SCENARIOS:''',
    '''@dataclass(frozen=True)
class PortfolioScenario:
    name: str
    maximum_positive_gap: float | None
    minimum_entry_trading_value: float
    maximum_participation: float


@dataclass(frozen=True)
class ExitPolicy:
    name: str
    stop_loss_pct: float = STOP_LOSS_PCT
    target_gain_pct: float = TARGET_GAIN_PCT
    trailing_stop_pct: float = TRAILING_STOP_PCT
    maximum_holding_sessions: int = MAX_HOLDING_SESSIONS
    use_signal_exit: bool = True


DEFAULT_EXIT_POLICY = ExitPolicy("baseline")


SCENARIOS:''',
    "exit policy dataclass",
)
replace_once(
    '''def signal_eligibility_mask(signals: pd.DataFrame) -> pd.Series:
    """Return research eligibility while preserving the complete report calendar."""
    if "portfolio_eligible" not in signals.columns:
        return pd.Series(True, index=signals.index, dtype=bool)
    values = signals["portfolio_eligible"]
''',
    '''def signal_eligibility_mask(
    signals: pd.DataFrame,
    column: str = "portfolio_eligible",
) -> pd.Series:
    """Return a research eligibility mask while preserving the report calendar."""
    if column not in signals.columns:
        return pd.Series(True, index=signals.index, dtype=bool)
    values = signals[column]
''',
    "eligibility mask column",
)
replace_once(
    '''def resolve_exit(
    position: dict[str, Any],
    price_row: dict[str, Any],
    report_day: bool,
    active_codes: set[str],
) -> tuple[str, float] | None:
''',
    '''def resolve_exit(
    position: dict[str, Any],
    price_row: dict[str, Any],
    report_day: bool,
    active_codes: set[str],
    exit_policy: ExitPolicy = DEFAULT_EXIT_POLICY,
) -> tuple[str, float] | None:
''',
    "resolve exit signature",
)
replace_once(
    '    trailing_stop = float(position["highest_close"]) * (1 - TRAILING_STOP_PCT)\n',
    '    trailing_stop = float(position["highest_close"]) * (1 - exit_policy.trailing_stop_pct)\n',
    "trailing stop policy",
)
replace_once(
    '    if int(position["holding_sessions"]) >= MAX_HOLDING_SESSIONS - 1:\n',
    '    if int(position["holding_sessions"]) >= exit_policy.maximum_holding_sessions - 1:\n',
    "holding policy",
)
replace_once(
    '    if report_day and position["code"] not in active_codes:\n',
    '    if exit_policy.use_signal_exit and report_day and position["code"] not in active_codes:\n',
    "signal exit policy",
)
replace_once(
    '''def simulate_scenario(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    scenario: PortfolioScenario,
    initial_capital: float = INITIAL_CAPITAL,
) -> dict[str, pd.DataFrame | dict[str, Any]]:
''',
    '''def simulate_scenario(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    scenario: PortfolioScenario,
    initial_capital: float = INITIAL_CAPITAL,
    exit_policy: ExitPolicy = DEFAULT_EXIT_POLICY,
) -> dict[str, pd.DataFrame | dict[str, Any]]:
''',
    "simulate signature",
)
replace_once(
    '''    eligible_mask = signal_eligibility_mask(signal_frame)
    entry_signals = signal_frame.loc[eligible_mask].drop(columns=["_report_date"], errors="ignore")
    events = build_entry_events(entry_signals, prices)
    active_codes_by_report = {pd.Timestamp(report_date).normalize(): set() for report_date in report_dates}
    for report_date, group in signal_frame.loc[eligible_mask].groupby("_report_date"):
''',
    '''    entry_eligible_mask = signal_eligibility_mask(signal_frame, "portfolio_eligible")
    hold_eligible_mask = signal_eligibility_mask(signal_frame, "portfolio_hold_eligible")
    entry_signals = signal_frame.loc[entry_eligible_mask].drop(columns=["_report_date"], errors="ignore")
    events = build_entry_events(entry_signals, prices)
    active_codes_by_report = {pd.Timestamp(report_date).normalize(): set() for report_date in report_dates}
    for report_date, group in signal_frame.loc[hold_eligible_mask].groupby("_report_date"):
''',
    "entry and hold eligibility",
)
replace_once(
    '            exit_signal = resolve_exit(position, price_row, report_day, active_codes)\n',
    '            exit_signal = resolve_exit(position, price_row, report_day, active_codes, exit_policy)\n',
    "resolve policy call",
)
replace_once(
    '                risk_quantity = equity_open * RISK_PER_TRADE / (raw_entry * STOP_LOSS_PCT)\n',
    '                risk_quantity = equity_open * RISK_PER_TRADE / (raw_entry * exit_policy.stop_loss_pct)\n',
    "risk sizing policy",
)
replace_once(
    '                    "fixed_stop": raw_entry * (1 - STOP_LOSS_PCT),\n'
    '                    "target_price": raw_entry * (1 + TARGET_GAIN_PCT),\n',
    '                    "exit_policy": exit_policy.name,\n'
    '                    "stop_loss_pct": exit_policy.stop_loss_pct,\n'
    '                    "target_gain_pct": exit_policy.target_gain_pct,\n'
    '                    "trailing_stop_pct": exit_policy.trailing_stop_pct,\n'
    '                    "maximum_holding_sessions": exit_policy.maximum_holding_sessions,\n'
    '                    "use_signal_exit": exit_policy.use_signal_exit,\n'
    '                    "fixed_stop": raw_entry * (1 - exit_policy.stop_loss_pct),\n'
    '                    "target_price": raw_entry * (1 + exit_policy.target_gain_pct),\n',
    "position policy metadata",
)
replace_once(
    '''    metrics = calculate_metrics(equity_frame, trades_frame, initial_capital, cumulative_turnover)
    metrics.update({"scenario": scenario.name, **asdict(scenario)})
''',
    '''    metrics = calculate_metrics(equity_frame, trades_frame, initial_capital, cumulative_turnover)
    metrics.update({
        "scenario": scenario.name,
        **asdict(scenario),
        "exit_policy": exit_policy.name,
        "stop_loss_pct": exit_policy.stop_loss_pct,
        "target_gain_pct": exit_policy.target_gain_pct,
        "trailing_stop_pct": exit_policy.trailing_stop_pct,
        "maximum_holding_sessions": exit_policy.maximum_holding_sessions,
        "use_signal_exit": exit_policy.use_signal_exit,
    })
''',
    "metrics policy metadata",
)

path.write_text(text, encoding="utf-8")
print("configurable portfolio exit policy applied")
