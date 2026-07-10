from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one anchor, found {count}: {old[:180]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-11-dashboard-performance-governance-v14"',
    'APP_VERSION = "2026-07-11-dashboard-paper-portfolio-v15"',
)

paper_functions = r"""

PAPER_INITIAL_CAPITAL = 10_000_000.0
PAPER_MAX_POSITIONS = 10
PAPER_MAX_POSITION_WEIGHT = 0.12
PAPER_MAX_SECTOR_WEIGHT = 0.25
PAPER_RISK_PER_TRADE = 0.01
PAPER_LOT_SIZE = 100
PAPER_MAX_HOLDING_DAYS = 20

PAPER_POSITION_COLUMNS = [
    "position_id", "status", "code", "name", "sector33", "entry_date", "entry_price",
    "quantity", "cost_basis", "current_price", "market_value", "highest_close",
    "stop_price", "target_price", "trailing_stop_pct", "holding_days",
    "sector_research_priority", "sector_leader_score", "sector_rotation",
    "unrealized_pnl", "unrealized_return",
]

PAPER_TRADE_HISTORY_COLUMNS = PAPER_POSITION_COLUMNS + [
    "exit_date", "exit_price", "exit_reason", "realized_pnl", "realized_return",
]

PAPER_PLAN_COLUMNS = [
    "plan_date", "action", "code", "name", "sector33", "entry_reference_price",
    "quantity", "planned_value", "portfolio_weight", "stop_price", "target_price",
    "risk_per_share", "planned_risk", "sector_research_priority", "sector_leader_score",
    "sector_rotation", "reason", "blocked_reason",
]

PAPER_EQUITY_COLUMNS = [
    "date", "initial_capital", "cash", "invested_cost", "market_value", "equity",
    "realized_pnl", "unrealized_pnl", "exposure_ratio", "peak_equity", "drawdown",
    "open_positions", "closed_trades", "win_rate",
]

RISK_BUDGET_COLUMNS = [
    "budget_type", "label", "current_value", "limit_value", "utilization", "status", "detail",
]


def atomic_write_csv(frame: pd.DataFrame, path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(target)


def load_csv_with_columns(path: str, columns: list[str]) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        return pd.DataFrame(columns=columns)
    try:
        frame = pd.read_csv(target)
    except Exception as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    if "code" in frame.columns:
        frame["code"] = frame["code"].map(normalize_code)
    return frame[columns]


def paper_target_exposure(regime_label: str, health_status: str) -> float:
    base = {
        "強気": 0.80,
        "やや強気": 0.65,
        "中立": 0.45,
        "弱気": 0.20,
        "過熱警戒": 0.30,
    }.get(optional_text(regime_label), 0.35)
    health = optional_text(health_status)
    if health == "FAIL":
        return 0.0
    if health == "WARN":
        return round(base * 0.50, 4)
    return base


def business_holding_days(entry_date: Any, current_date: Any) -> int:
    entry = pd.to_datetime(entry_date, errors="coerce")
    current = pd.to_datetime(current_date, errors="coerce")
    if pd.isna(entry) or pd.isna(current) or current <= entry:
        return 0
    return max(len(pd.bdate_range(entry.normalize(), current.normalize())) - 1, 0)


def current_price_lookup(all_ranked: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if all_ranked is None or all_ranked.empty or "code" not in all_ranked.columns:
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for _, row in all_ranked.iterrows():
        lookup[normalize_code(row.get("code"))] = row.to_dict()
    return lookup


def mark_paper_positions(
    today: str,
    portfolio: pd.DataFrame,
    all_ranked: pd.DataFrame,
    eligible_codes: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if portfolio is None or portfolio.empty:
        return pd.DataFrame(columns=PAPER_POSITION_COLUMNS), pd.DataFrame(columns=PAPER_TRADE_HISTORY_COLUMNS)
    prices = current_price_lookup(all_ranked)
    active_rows: list[dict[str, Any]] = []
    closed_rows: list[dict[str, Any]] = []
    for _, source in portfolio.iterrows():
        row = source.to_dict()
        code = normalize_code(row.get("code"))
        price_data = prices.get(code, {})
        current_price = row_number(pd.Series(price_data), "close", row_number(source, "current_price", row_number(source, "entry_price")))
        entry_price = row_number(source, "entry_price")
        quantity = int(row_number(source, "quantity"))
        highest_close = max(row_number(source, "highest_close", entry_price), current_price)
        holding_days = business_holding_days(source.get("entry_date"), today)
        stop_price = row_number(source, "stop_price", entry_price * 0.92)
        target_price = row_number(source, "target_price", entry_price * 1.16)
        trailing_stop_pct = row_number(source, "trailing_stop_pct", 0.10)
        trailing_price = highest_close * (1 - trailing_stop_pct)
        exit_reason = ""
        if current_price <= stop_price:
            exit_reason = "STOP_LOSS"
        elif current_price >= target_price:
            exit_reason = "TAKE_PROFIT"
        elif holding_days >= 5 and current_price <= trailing_price:
            exit_reason = "TRAILING_STOP"
        elif holding_days >= PAPER_MAX_HOLDING_DAYS:
            exit_reason = "TIME_EXIT"
        elif holding_days >= 5 and code not in eligible_codes:
            exit_reason = "SIGNAL_EXIT"
        market_value = current_price * quantity
        cost_basis = entry_price * quantity
        unrealized_pnl = market_value - cost_basis
        common = {
            **row,
            "code": code,
            "status": "OPEN" if not exit_reason else "CLOSED",
            "current_price": current_price,
            "market_value": market_value,
            "highest_close": highest_close,
            "holding_days": holding_days,
            "unrealized_pnl": unrealized_pnl if not exit_reason else 0.0,
            "unrealized_return": current_price / entry_price - 1 if entry_price else None,
        }
        if exit_reason:
            realized_pnl = (current_price - entry_price) * quantity
            closed_rows.append({
                **common,
                "exit_date": today,
                "exit_price": current_price,
                "exit_reason": exit_reason,
                "realized_pnl": realized_pnl,
                "realized_return": current_price / entry_price - 1 if entry_price else None,
            })
        else:
            active_rows.append(common)
    active = pd.DataFrame(active_rows)
    closed = pd.DataFrame(closed_rows)
    for column in PAPER_POSITION_COLUMNS:
        if column not in active.columns:
            active[column] = None
    for column in PAPER_TRADE_HISTORY_COLUMNS:
        if column not in closed.columns:
            closed[column] = None
    return active[PAPER_POSITION_COLUMNS], closed[PAPER_TRADE_HISTORY_COLUMNS]


def paper_portfolio_totals(
    portfolio: pd.DataFrame,
    trade_history: pd.DataFrame,
    initial_capital: float = PAPER_INITIAL_CAPITAL,
) -> dict[str, float]:
    realized = float(pd.to_numeric(trade_history.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if trade_history is not None and not trade_history.empty else 0.0
    cost = float(pd.to_numeric(portfolio.get("cost_basis", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if portfolio is not None and not portfolio.empty else 0.0
    market_value = float(pd.to_numeric(portfolio.get("market_value", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if portfolio is not None and not portfolio.empty else 0.0
    unrealized = market_value - cost
    cash = initial_capital + realized - cost
    equity = cash + market_value
    return {
        "initial_capital": initial_capital,
        "realized_pnl": realized,
        "invested_cost": cost,
        "market_value": market_value,
        "unrealized_pnl": unrealized,
        "cash": cash,
        "equity": equity,
        "exposure_ratio": market_value / equity if equity > 0 else 0.0,
    }


def build_paper_trade_plan(
    today: str,
    sector_leaders: pd.DataFrame,
    portfolio: pd.DataFrame,
    trade_history: pd.DataFrame,
    regime: dict[str, Any],
    run_health: pd.DataFrame,
    blocked_codes: set[str] | None = None,
    initial_capital: float = PAPER_INITIAL_CAPITAL,
) -> pd.DataFrame:
    if sector_leaders is None or sector_leaders.empty:
        return pd.DataFrame(columns=PAPER_PLAN_COLUMNS)
    health_status = run_health_overall(run_health)
    target_exposure = paper_target_exposure(optional_text(regime.get("label")), health_status)
    if target_exposure <= 0:
        return pd.DataFrame(columns=PAPER_PLAN_COLUMNS)
    totals = paper_portfolio_totals(portfolio, trade_history, initial_capital)
    equity = totals["equity"]
    target_market_value = max(equity * target_exposure, 0.0)
    available_value = max(target_market_value - totals["market_value"], 0.0)
    slots = max(PAPER_MAX_POSITIONS - len(portfolio), 0)
    if slots <= 0 or available_value <= 0:
        return pd.DataFrame(columns=PAPER_PLAN_COLUMNS)
    existing_codes = set(portfolio.get("code", pd.Series(dtype=str)).map(normalize_code)) if portfolio is not None and not portfolio.empty else set()
    blocked = {normalize_code(code) for code in (blocked_codes or set())}
    sector_used: dict[str, float] = {}
    if portfolio is not None and not portfolio.empty:
        for sector, group in portfolio.groupby("sector33"):
            sector_used[optional_text(sector)] = float(pd.to_numeric(group["market_value"], errors="coerce").fillna(0).sum())
    candidates = sector_leaders[sector_leaders["sector_research_priority"].isin(["最優先", "優先"])].copy()
    candidates = candidates.sort_values(["sector_research_priority", "sector_leader_score", "momentum_rank"], ascending=[True, False, True])
    rows: list[dict[str, Any]] = []
    planned_total = 0.0
    for _, candidate in candidates.iterrows():
        if len(rows) >= slots:
            break
        code = normalize_code(candidate.get("code"))
        if code in existing_codes or code in blocked:
            continue
        entry = row_number(candidate, "close")
        if entry <= 0:
            continue
        sector = optional_text(candidate.get("sector33")) or "未分類"
        max_position_value = equity * PAPER_MAX_POSITION_WEIGHT
        sector_remaining = max(equity * PAPER_MAX_SECTOR_WEIGHT - sector_used.get(sector, 0.0), 0.0)
        remaining_target = max(available_value - planned_total, 0.0)
        allocation_cap = min(max_position_value, sector_remaining, remaining_target)
        if allocation_cap < entry * PAPER_LOT_SIZE:
            continue
        stop_pct = 0.07 if optional_text(regime.get("label")) in {"強気", "やや強気"} else 0.08
        if optional_text(regime.get("label")) == "過熱警戒":
            stop_pct = 0.06
        stop_price = round(entry * (1 - stop_pct), 2)
        risk_per_share = entry - stop_price
        risk_budget = equity * PAPER_RISK_PER_TRADE
        quantity_by_value = int(allocation_cap // (entry * PAPER_LOT_SIZE)) * PAPER_LOT_SIZE
        quantity_by_risk = int(risk_budget // (risk_per_share * PAPER_LOT_SIZE)) * PAPER_LOT_SIZE if risk_per_share > 0 else 0
        quantity = min(quantity_by_value, quantity_by_risk)
        if quantity < PAPER_LOT_SIZE:
            continue
        planned_value = entry * quantity
        planned_risk = risk_per_share * quantity
        target_price = round(entry + risk_per_share * 2.0, 2)
        rows.append({
            "plan_date": today,
            "action": "PAPER_OPEN",
            "code": code,
            "name": optional_text(candidate.get("name")),
            "sector33": sector,
            "entry_reference_price": entry,
            "quantity": quantity,
            "planned_value": planned_value,
            "portfolio_weight": planned_value / equity if equity > 0 else 0.0,
            "stop_price": stop_price,
            "target_price": target_price,
            "risk_per_share": risk_per_share,
            "planned_risk": planned_risk,
            "sector_research_priority": optional_text(candidate.get("sector_research_priority")),
            "sector_leader_score": row_number(candidate, "sector_leader_score"),
            "sector_rotation": optional_text(candidate.get("sector_rotation")),
            "reason": f"業種{optional_text(candidate.get('sector_rotation'))} / リーダー{row_number(candidate, 'sector_leader_score'):.1f}点 / Run Health {health_status}",
            "blocked_reason": "",
        })
        planned_total += planned_value
        sector_used[sector] = sector_used.get(sector, 0.0) + planned_value
    return pd.DataFrame(rows, columns=PAPER_PLAN_COLUMNS)


def apply_paper_trade_plan(today: str, portfolio: pd.DataFrame, plan: pd.DataFrame) -> pd.DataFrame:
    active = portfolio.copy() if portfolio is not None else pd.DataFrame(columns=PAPER_POSITION_COLUMNS)
    if plan is None or plan.empty:
        return active[PAPER_POSITION_COLUMNS]
    new_rows: list[dict[str, Any]] = []
    for _, row in plan.iterrows():
        entry = row_number(row, "entry_reference_price")
        quantity = int(row_number(row, "quantity"))
        code = normalize_code(row.get("code"))
        new_rows.append({
            "position_id": f"{today}-{code}",
            "status": "OPEN",
            "code": code,
            "name": optional_text(row.get("name")),
            "sector33": optional_text(row.get("sector33")),
            "entry_date": today,
            "entry_price": entry,
            "quantity": quantity,
            "cost_basis": entry * quantity,
            "current_price": entry,
            "market_value": entry * quantity,
            "highest_close": entry,
            "stop_price": row_number(row, "stop_price"),
            "target_price": row_number(row, "target_price"),
            "trailing_stop_pct": 0.10,
            "holding_days": 0,
            "sector_research_priority": optional_text(row.get("sector_research_priority")),
            "sector_leader_score": row_number(row, "sector_leader_score"),
            "sector_rotation": optional_text(row.get("sector_rotation")),
            "unrealized_pnl": 0.0,
            "unrealized_return": 0.0,
        })
    combined = pd.concat([active, pd.DataFrame(new_rows)], ignore_index=True)
    combined = combined.drop_duplicates("position_id", keep="last")
    return combined[PAPER_POSITION_COLUMNS]


def append_paper_trade_history(history: pd.DataFrame, closed: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (history, closed) if frame is not None and not frame.empty]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PAPER_TRADE_HISTORY_COLUMNS)
    if not combined.empty:
        combined = combined.drop_duplicates("position_id", keep="last").sort_values(["exit_date", "position_id"])
    for column in PAPER_TRADE_HISTORY_COLUMNS:
        if column not in combined.columns:
            combined[column] = None
    return combined[PAPER_TRADE_HISTORY_COLUMNS]


def build_risk_budget(
    portfolio: pd.DataFrame,
    totals: dict[str, float],
    regime: dict[str, Any],
    run_health: pd.DataFrame,
) -> pd.DataFrame:
    health = run_health_overall(run_health)
    target_exposure = paper_target_exposure(optional_text(regime.get("label")), health)
    rows: list[dict[str, Any]] = []

    def add(kind: str, label: str, current: float, limit: float, detail: str) -> None:
        utilization = current / limit if limit > 0 else 0.0
        status = "PASS" if current <= limit + 1e-9 else "FAIL"
        rows.append({
            "budget_type": kind,
            "label": label,
            "current_value": current,
            "limit_value": limit,
            "utilization": utilization,
            "status": status,
            "detail": detail,
        })

    equity = totals.get("equity", PAPER_INITIAL_CAPITAL)
    add("portfolio", "投資比率", totals.get("exposure_ratio", 0.0), target_exposure, f"Market Regime {optional_text(regime.get('label'))} / Run Health {health}")
    add("portfolio", "保有銘柄数", float(len(portfolio)), float(PAPER_MAX_POSITIONS), "最大10銘柄")
    if portfolio is not None and not portfolio.empty and equity > 0:
        for sector, group in portfolio.groupby("sector33"):
            sector_value = float(pd.to_numeric(group["market_value"], errors="coerce").fillna(0).sum())
            add("sector", optional_text(sector) or "未分類", sector_value / equity, PAPER_MAX_SECTOR_WEIGHT, "1業種25%上限")
        for _, row in portfolio.iterrows():
            add("position", normalize_code(row.get("code")), row_number(row, "market_value") / equity, PAPER_MAX_POSITION_WEIGHT, "1銘柄12%上限")
    return pd.DataFrame(rows, columns=RISK_BUDGET_COLUMNS)


def update_paper_equity_history(
    path: str,
    today: str,
    totals: dict[str, float],
    portfolio: pd.DataFrame,
    trade_history: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    old = load_csv_with_columns(path, PAPER_EQUITY_COLUMNS)
    closed_count = len(trade_history) if trade_history is not None else 0
    wins = int((pd.to_numeric(trade_history.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0) > 0).sum()) if trade_history is not None and not trade_history.empty else 0
    win_rate = wins / closed_count if closed_count else None
    prior_peak = float(pd.to_numeric(old.get("equity", pd.Series(dtype=float)), errors="coerce").max()) if not old.empty else totals["equity"]
    peak = max(prior_peak, totals["equity"])
    drawdown = totals["equity"] / peak - 1 if peak > 0 else 0.0
    current = pd.DataFrame([{
        "date": today,
        **totals,
        "peak_equity": peak,
        "drawdown": drawdown,
        "open_positions": len(portfolio),
        "closed_trades": closed_count,
        "win_rate": win_rate,
    }], columns=PAPER_EQUITY_COLUMNS)
    combined = pd.concat([old, current], ignore_index=True).drop_duplicates("date", keep="last").sort_values("date")
    atomic_write_csv(combined, path)
    return combined, current.iloc[0].to_dict()


def run_paper_portfolio(
    today: str,
    all_ranked: pd.DataFrame,
    sector_leaders: pd.DataFrame,
    regime: dict[str, Any],
    run_health: pd.DataFrame,
    portfolio_path: str = "data/paper_portfolio.csv",
    trade_history_path: str = "data/paper_trade_history.csv",
    equity_history_path: str = "data/paper_equity_history.csv",
    initial_capital: float = PAPER_INITIAL_CAPITAL,
) -> dict[str, Any]:
    portfolio = load_csv_with_columns(portfolio_path, PAPER_POSITION_COLUMNS)
    trade_history = load_csv_with_columns(trade_history_path, PAPER_TRADE_HISTORY_COLUMNS)
    eligible_codes = set(
        sector_leaders[sector_leaders["sector_research_priority"].isin(["最優先", "優先"])]["code"].map(normalize_code)
    ) if sector_leaders is not None and not sector_leaders.empty else set()
    marked, closed_today = mark_paper_positions(today, portfolio, all_ranked, eligible_codes)
    trade_history = append_paper_trade_history(trade_history, closed_today)
    blocked_codes = set(closed_today.get("code", pd.Series(dtype=str)).map(normalize_code)) if not closed_today.empty else set()
    plan = build_paper_trade_plan(today, sector_leaders, marked, trade_history, regime, run_health, blocked_codes, initial_capital)
    portfolio = apply_paper_trade_plan(today, marked, plan)
    totals = paper_portfolio_totals(portfolio, trade_history, initial_capital)
    risk_budget = build_risk_budget(portfolio, totals, regime, run_health)
    equity_history, performance = update_paper_equity_history(equity_history_path, today, totals, portfolio, trade_history)
    atomic_write_csv(portfolio, portfolio_path)
    atomic_write_csv(trade_history, trade_history_path)
    return {
        "portfolio": portfolio,
        "plan": plan,
        "trade_history": trade_history,
        "risk_budget": risk_budget,
        "equity_history": equity_history,
        "performance": pd.DataFrame([performance]),
        "closed_today": closed_today,
    }


def plain_paper_portfolio_section(
    portfolio: pd.DataFrame,
    plan: pd.DataFrame,
    performance: pd.DataFrame,
    risk_budget: pd.DataFrame,
) -> list[str]:
    perf = {} if performance is None or performance.empty else performance.iloc[0].to_dict()
    lines = [
        "【ペーパーポートフォリオ】",
        "実注文は行いません。終値ベースの仮想検証で、売買推奨ではありません。",
        f"資産 {fmt_num(perf.get('equity'), 0)}円 / 現金 {fmt_num(perf.get('cash'), 0)}円 / 投資比率 {fmt_pct(perf.get('exposure_ratio'))}",
        f"実現損益 {fmt_num(perf.get('realized_pnl'), 0)}円 / 含み損益 {fmt_num(perf.get('unrealized_pnl'), 0)}円 / DD {fmt_pct(perf.get('drawdown'))}",
        f"保有 {len(portfolio) if portfolio is not None else 0}件 / 本日の新規計画 {len(plan) if plan is not None else 0}件",
    ]
    if plan is not None and not plan.empty:
        for _, row in plan.head(5).iterrows():
            lines.append(f"  OPEN {row['code']} {row['name']} / {int(row['quantity'])}株 / {fmt_price(row['entry_reference_price'])} / 損切 {fmt_price(row['stop_price'])} / 目標 {fmt_price(row['target_price'])}")
    failures = risk_budget[risk_budget["status"] == "FAIL"] if risk_budget is not None and not risk_budget.empty else pd.DataFrame()
    for _, row in failures.head(3).iterrows():
        lines.append(f"  リスク超過: {row['label']} {fmt_pct(row['current_value'])} > {fmt_pct(row['limit_value'])}")
    lines.append("")
    return lines


def html_paper_portfolio_section(
    portfolio: pd.DataFrame,
    plan: pd.DataFrame,
    performance: pd.DataFrame,
    risk_budget: pd.DataFrame,
) -> str:
    perf = {} if performance is None or performance.empty else performance.iloc[0].to_dict()
    failures = risk_budget[risk_budget["status"] == "FAIL"] if risk_budget is not None and not risk_budget.empty else pd.DataFrame()
    plan_items = "".join(
        f'<div style="border-top:1px solid #e5e7eb;padding:8px 0;font-size:11px;color:#334155"><b>OPEN {html_text(row["code"])} {html_text(row["name"])}</b> ・ {int(row["quantity"])}株 ・ {fmt_price(row["entry_reference_price"])} ・ 損切 {fmt_price(row["stop_price"])} ・ 目標 {fmt_price(row["target_price"])}</div>'
        for _, row in (plan.head(5).iterrows() if plan is not None and not plan.empty else [])
    )
    fail_html = "".join(
        f'<div style="font-size:11px;color:#b91c1c;margin-top:3px">リスク超過: {html_text(row["label"])} {fmt_pct(row["current_value"])} &gt; {fmt_pct(row["limit_value"])}</div>'
        for _, row in failures.head(3).iterrows()
    )
    return f'''<div style="background:#fff;border:2px solid #7c3aed;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#581c87">ペーパーポートフォリオ</div>
<div style="font-size:11px;color:#64748b;margin-top:4px">実注文は行わない終値ベースの仮想検証です。売買推奨ではありません。</div>
<div style="font-size:13px;color:#334155;margin-top:8px">資産 <b>{fmt_num(perf.get('equity'), 0)}円</b> ・ 現金 <b>{fmt_num(perf.get('cash'), 0)}円</b> ・ 投資比率 <b>{fmt_pct(perf.get('exposure_ratio'))}</b></div>
<div style="font-size:12px;color:#475569">実現損益 {fmt_num(perf.get('realized_pnl'), 0)}円 ・ 含み損益 {fmt_num(perf.get('unrealized_pnl'), 0)}円 ・ DD {fmt_pct(perf.get('drawdown'))}</div>
<div style="font-size:12px;font-weight:800;color:#334155;margin-top:6px">保有 {len(portfolio) if portfolio is not None else 0}件 ・ 本日の新規計画 {len(plan) if plan is not None else 0}件</div>{plan_items}{fail_html}</div>'''
"""

replace_once(
    '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
    paper_functions + '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_signal_history: pd.DataFrame, sector_leader_outcomes: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_signal_history: pd.DataFrame, sector_leader_outcomes: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_trade_history: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
)
replace_once(
    '        run_health.to_excel(w, sheet_name="Run Health", index=False)\n        new_entries.to_excel(w, sheet_name="New Entries", index=False)',
    '        run_health.to_excel(w, sheet_name="Run Health", index=False)\n        paper_portfolio.to_excel(w, sheet_name="Paper Portfolio", index=False)\n        paper_trade_plan.to_excel(w, sheet_name="Paper Trade Plan", index=False)\n        paper_trade_history.to_excel(w, sheet_name="Paper Trade History", index=False)\n        paper_risk_budget.to_excel(w, sheet_name="Risk Budget", index=False)\n        paper_performance.to_excel(w, sheet_name="Paper Performance", index=False)\n        new_entries.to_excel(w, sheet_name="New Entries", index=False)',
)

replace_once(
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '    lines += plain_governance_section(sector_leader_performance, signal_governance, adaptive_thresholds, run_health)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
    '    lines += plain_governance_section(sector_leader_performance, signal_governance, adaptive_thresholds, run_health)\n    lines += plain_paper_portfolio_section(paper_portfolio, paper_trade_plan, paper_performance, paper_risk_budget)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
)
replace_once(
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '        html_governance_section(sector_leader_performance, signal_governance, adaptive_thresholds, run_health),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
    '        html_governance_section(sector_leader_performance, signal_governance, adaptive_thresholds, run_health),\n        html_paper_portfolio_section(paper_portfolio, paper_trade_plan, paper_performance, paper_risk_budget),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
)
replace_once(
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
)
replace_once(
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, priority_changes, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, priority_changes, cfg), "html", "utf-8"))',
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, priority_changes, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, priority_changes, cfg), "html", "utf-8"))',
)

replace_once(
    '    run_health = build_run_health(today, all_ranked, top100, sector_momentum, sector_leaders, errors, len(stocks), success)\n    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)',
    '    run_health = build_run_health(today, all_ranked, top100, sector_momentum, sector_leaders, errors, len(stocks), success)\n    paper_result = run_paper_portfolio(today, all_ranked, sector_leaders, regime, run_health)\n    paper_portfolio = paper_result["portfolio"]\n    paper_trade_plan = paper_result["plan"]\n    paper_trade_history = paper_result["trade_history"]\n    paper_risk_budget = paper_result["risk_budget"]\n    paper_performance = paper_result["performance"]\n    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)',
)
replace_once(
    '        "レポート形式": "dashboard_performance_governance_v14",',
    '        "レポート形式": "dashboard_paper_portfolio_v15",',
)
replace_once(
    '        "Run Health FAIL": int((run_health.get("status", pd.Series(dtype=str)) == "FAIL").sum()) if not run_health.empty else 0,\n        "重点候補数": priority_change_count(priority_changes, "current"),',
    '        "Run Health FAIL": int((run_health.get("status", pd.Series(dtype=str)) == "FAIL").sum()) if not run_health.empty else 0,\n        "ペーパー元本": PAPER_INITIAL_CAPITAL,\n        "ペーパー資産": float(paper_performance.iloc[0]["equity"]) if not paper_performance.empty else PAPER_INITIAL_CAPITAL,\n        "ペーパー現金": float(paper_performance.iloc[0]["cash"]) if not paper_performance.empty else PAPER_INITIAL_CAPITAL,\n        "ペーパー投資比率": float(paper_performance.iloc[0]["exposure_ratio"]) if not paper_performance.empty else 0.0,\n        "ペーパー実現損益": float(paper_performance.iloc[0]["realized_pnl"]) if not paper_performance.empty else 0.0,\n        "ペーパー含み損益": float(paper_performance.iloc[0]["unrealized_pnl"]) if not paper_performance.empty else 0.0,\n        "ペーパードローダウン": float(paper_performance.iloc[0]["drawdown"]) if not paper_performance.empty else 0.0,\n        "ペーパー保有数": len(paper_portfolio),\n        "ペーパー新規計画数": len(paper_trade_plan),\n        "ペーパー決済数": len(paper_trade_history),\n        "重点候補数": priority_change_count(priority_changes, "current"),',
)
replace_once(
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, sector_momentum, sector_rotation, sector_leaders, sector_signal_history, sector_leader_outcomes, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)',
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, sector_momentum, sector_rotation, sector_leaders, sector_signal_history, sector_leader_outcomes, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_trade_history, paper_risk_budget, paper_performance, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)',
)
replace_once(
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, priority_changes, cfg)',
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, priority_changes, cfg)',
)

path.write_text(text, encoding="utf-8")
print("Applied paper portfolio and risk management batch")
