from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one anchor, found {count}: {old[:180]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-11-dashboard-release-readiness-v16"',
    'APP_VERSION = "2026-07-11-dashboard-market-freshness-v17"',
)

freshness_functions = r'''

def evaluate_market_data_freshness(
    today: str,
    all_ranked: pd.DataFrame,
    minimum_fresh_ratio: float = 0.95,
) -> dict[str, Any]:
    total_count = len(all_ranked) if all_ranked is not None else 0
    if all_ranked is None or all_ranked.empty or "price_date" not in all_ranked.columns:
        return {
            "status": "EMPTY",
            "latest_price_date": "",
            "fresh_count": 0,
            "total_count": total_count,
            "fresh_ratio": 0.0,
            "state_update_allowed": False,
            "detail": "株価日付を確認できないため状態更新を停止",
        }

    dates = pd.to_datetime(all_ranked["price_date"], errors="coerce")
    valid = dates.dropna()
    if valid.empty:
        return {
            "status": "EMPTY",
            "latest_price_date": "",
            "fresh_count": 0,
            "total_count": total_count,
            "fresh_ratio": 0.0,
            "state_update_allowed": False,
            "detail": "有効な株価日付がないため状態更新を停止",
        }

    target_date = pd.Timestamp(today).date()
    latest_date = valid.max().date()
    fresh_count = int((dates.dt.date == target_date).sum())
    fresh_ratio = fresh_count / total_count if total_count else 0.0
    if latest_date == target_date and fresh_ratio >= minimum_fresh_ratio:
        status = "FRESH"
        detail = "当日株価が十分に揃っているため状態更新を許可"
    elif latest_date == target_date and fresh_count > 0:
        status = "PARTIAL"
        detail = "当日株価の取得率が基準未満のため状態更新を停止"
    else:
        status = "STALE"
        detail = f"最新株価日 {latest_date.isoformat()} が実行日 {today} と一致しないため状態更新を停止"
    return {
        "status": status,
        "latest_price_date": latest_date.isoformat(),
        "fresh_count": fresh_count,
        "total_count": total_count,
        "fresh_ratio": fresh_ratio,
        "state_update_allowed": status == "FRESH",
        "detail": detail,
    }


def attach_market_data_freshness_health(
    run_health: pd.DataFrame,
    freshness: dict[str, Any],
) -> pd.DataFrame:
    columns = ["check_name", "status", "actual", "expected", "detail"]
    work = run_health.copy() if run_health is not None else pd.DataFrame(columns=columns)
    if not work.empty:
        work = work[~work["check_name"].isin(["overall", "market_data_current_day"])].copy()
    freshness_status = optional_text(freshness.get("status"))
    status = "PASS" if freshness_status == "FRESH" else "WARN" if freshness_status == "PARTIAL" else "FAIL"
    row = pd.DataFrame([{
        "check_name": "market_data_current_day",
        "status": status,
        "actual": f"{freshness_status} / {float(freshness.get('fresh_ratio', 0.0)):.1%}",
        "expected": "FRESH / >=95%",
        "detail": optional_text(freshness.get("detail")),
    }], columns=columns)
    work = pd.concat([work, row], ignore_index=True)
    statuses = work["status"].tolist()
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    overall_row = pd.DataFrame([{
        "check_name": "overall",
        "status": overall,
        "actual": overall,
        "expected": "PASS",
        "detail": f"PASS {statuses.count('PASS')} / WARN {statuses.count('WARN')} / FAIL {statuses.count('FAIL')}",
    }], columns=columns)
    return pd.concat([overall_row, work], ignore_index=True)


def load_existing_paper_state(
    regime: dict[str, Any],
    run_health: pd.DataFrame,
    initial_capital: float = PAPER_INITIAL_CAPITAL,
) -> dict[str, Any]:
    portfolio = load_csv_with_columns("data/paper_portfolio.csv", PAPER_POSITION_COLUMNS)
    trade_history = load_csv_with_columns("data/paper_trade_history.csv", PAPER_TRADE_HISTORY_COLUMNS)
    equity_history = load_csv_with_columns("data/paper_equity_history.csv", PAPER_EQUITY_COLUMNS)
    totals = paper_portfolio_totals(portfolio, trade_history, initial_capital)
    risk_budget = build_risk_budget(portfolio, totals, regime, run_health)
    if equity_history.empty:
        performance = pd.DataFrame([{
            "date": "",
            **totals,
            "peak_equity": totals["equity"],
            "drawdown": 0.0,
            "open_positions": len(portfolio),
            "closed_trades": len(trade_history),
            "win_rate": None,
        }], columns=PAPER_EQUITY_COLUMNS)
    else:
        performance = equity_history.tail(1).copy()
    return {
        "portfolio": portfolio,
        "plan": pd.DataFrame(columns=PAPER_PLAN_COLUMNS),
        "trade_history": trade_history,
        "risk_budget": risk_budget,
        "equity_history": equity_history,
        "performance": performance,
        "closed_today": pd.DataFrame(columns=PAPER_TRADE_HISTORY_COLUMNS),
    }
'''

replace_once(
    '\n\nSTATE_SCHEMA_VERSION = "1.0"',
    freshness_functions + '\n\nSTATE_SCHEMA_VERSION = "1.0"',
)

replace_once(
    '    write_ranking_history(all_ranked, cfg["data"]["ranking_history_path"])',
    '    market_freshness = evaluate_market_data_freshness(today, all_ranked)\n'
    '    state_update_allowed = bool(market_freshness["state_update_allowed"])\n'
    '    if state_update_allowed:\n'
    '        write_ranking_history(all_ranked, cfg["data"]["ranking_history_path"])\n'
    '    else:\n'
    '        logger.warning("Market data guard blocked ranking history update: %s", market_freshness["detail"])',
)

replace_once(
    '    performance_history = combined_ranking_history(history, all_ranked, today)',
    '    performance_history = combined_ranking_history(history, all_ranked, today) if state_update_allowed else history.copy()',
)

old_operations = '''    sector_history_path = "data/sector_leader_signal_history.csv"
    current_sector_signals = current_sector_signal_snapshot(today, sector_leaders)
    sector_signal_history = update_sector_signal_history(sector_history_path, current_sector_signals)
    sector_leader_outcomes = calculate_sector_leader_outcomes(sector_signal_history, performance_history)
    sector_leader_performance = build_sector_leader_performance_summary(sector_leader_outcomes)
    signal_governance = build_signal_governance(sector_leader_outcomes)
    adaptive_thresholds = build_adaptive_threshold_recommendations(signal_governance)
    run_health = build_run_health(today, all_ranked, top100, sector_momentum, sector_leaders, errors, len(stocks), success)
    paper_result = run_paper_portfolio(today, all_ranked, sector_leaders, regime, run_health)
    paper_portfolio = paper_result["portfolio"]
    paper_trade_plan = paper_result["plan"]
    paper_trade_history = paper_result["trade_history"]
    paper_risk_budget = paper_result["risk_budget"]
    paper_performance = paper_result["performance"]
    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)
    state_paths = {
        "ranking_history": cfg["data"]["ranking_history_path"],
        "market_temperature": temp_path,
        "sector_leader_signals": sector_history_path,
        "paper_portfolio": "data/paper_portfolio.csv",
        "paper_trade_history": "data/paper_trade_history.csv",
        "paper_equity_history": "data/paper_equity_history.csv",
    }
    state_inventory = build_state_inventory(state_paths)
    state_snapshots = snapshot_state_files(today, state_paths)
    release_readiness = build_release_readiness(run_health, signal_governance, sector_leader_performance, paper_performance, paper_trade_history, paper_risk_budget)
    operational_alerts = build_operational_alerts(release_readiness)
    current_audit = build_execution_audit(today, release_readiness, operational_alerts, state_inventory, state_snapshots, run_health)
    execution_audit = append_execution_audit("data/execution_audit.csv", current_audit)'''

new_operations = '''    sector_history_path = "data/sector_leader_signal_history.csv"
    if state_update_allowed:
        current_sector_signals = current_sector_signal_snapshot(today, sector_leaders)
        sector_signal_history = update_sector_signal_history(sector_history_path, current_sector_signals)
    else:
        sector_signal_history = load_sector_signal_history(sector_history_path)
    sector_leader_outcomes = calculate_sector_leader_outcomes(sector_signal_history, performance_history)
    sector_leader_performance = build_sector_leader_performance_summary(sector_leader_outcomes)
    signal_governance = build_signal_governance(sector_leader_outcomes)
    adaptive_thresholds = build_adaptive_threshold_recommendations(signal_governance)
    run_health = build_run_health(today, all_ranked, top100, sector_momentum, sector_leaders, errors, len(stocks), success)
    run_health = attach_market_data_freshness_health(run_health, market_freshness)
    if state_update_allowed:
        paper_result = run_paper_portfolio(today, all_ranked, sector_leaders, regime, run_health)
        pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)
    else:
        paper_result = load_existing_paper_state(regime, run_health)
        logger.warning("Market data guard preserved all persistent state files")
    paper_portfolio = paper_result["portfolio"]
    paper_trade_plan = paper_result["plan"]
    paper_trade_history = paper_result["trade_history"]
    paper_risk_budget = paper_result["risk_budget"]
    paper_performance = paper_result["performance"]
    state_paths = {
        "ranking_history": cfg["data"]["ranking_history_path"],
        "market_temperature": temp_path,
        "sector_leader_signals": sector_history_path,
        "paper_portfolio": "data/paper_portfolio.csv",
        "paper_trade_history": "data/paper_trade_history.csv",
        "paper_equity_history": "data/paper_equity_history.csv",
    }
    state_inventory = build_state_inventory(state_paths)
    release_readiness = build_release_readiness(run_health, signal_governance, sector_leader_performance, paper_performance, paper_trade_history, paper_risk_budget)
    operational_alerts = build_operational_alerts(release_readiness)
    if state_update_allowed:
        state_snapshots = snapshot_state_files(today, state_paths)
        current_audit = build_execution_audit(today, release_readiness, operational_alerts, state_inventory, state_snapshots, run_health)
        execution_audit = append_execution_audit("data/execution_audit.csv", current_audit)
    else:
        state_snapshots = pd.DataFrame(columns=STATE_SNAPSHOT_COLUMNS)
        execution_audit = load_csv_with_columns("data/execution_audit.csv", EXECUTION_AUDIT_COLUMNS)'''

replace_once(old_operations, new_operations)

replace_once(
    '        "レポート形式": "dashboard_release_readiness_v16",',
    '        "レポート形式": "dashboard_market_freshness_v17",\n'
    '        "市場データ鮮度": market_freshness["status"],\n'
    '        "最新株価日": market_freshness["latest_price_date"],\n'
    '        "当日株価件数": market_freshness["fresh_count"],\n'
    '        "当日株価比率": market_freshness["fresh_ratio"],\n'
    '        "状態更新実行": "YES" if state_update_allowed else "NO",',
)

path.write_text(text, encoding="utf-8")
print("Applied market data freshness guard")
