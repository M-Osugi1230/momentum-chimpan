from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one anchor, found {count}: {old[:180]!r}")
    text = text.replace(old, new, 1)


replace_once("import html\n", "import hashlib\nimport html\n",)
replace_once(
    'APP_VERSION = "2026-07-11-dashboard-paper-portfolio-v15"',
    'APP_VERSION = "2026-07-11-dashboard-release-readiness-v16"',
)

release_functions = r"""

STATE_SCHEMA_VERSION = "1.0"
EXECUTION_MODE = "RESEARCH_AND_PAPER_ONLY"

RELEASE_READINESS_COLUMNS = [
    "release_status", "execution_mode", "criterion", "actual", "required",
    "passed", "blocking", "detail",
]

OPERATIONAL_ALERT_COLUMNS = [
    "severity", "category", "title", "status", "actual", "required", "action",
]

STATE_INVENTORY_COLUMNS = [
    "state_name", "path", "exists", "size_bytes", "row_count", "column_count",
    "modified_at", "sha256", "schema_version", "status",
]

STATE_SNAPSHOT_COLUMNS = [
    "snapshot_date", "state_name", "source_path", "snapshot_path", "status", "size_bytes", "sha256",
]

EXECUTION_AUDIT_COLUMNS = [
    "run_id", "date", "app_version", "execution_mode", "release_status", "run_health",
    "p0_alerts", "p1_alerts", "p2_alerts", "state_files_ok", "state_files_total",
    "snapshots_created", "manifest_sha256",
]


def sha256_file(path: str) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_shape(path: str) -> tuple[int | None, int | None]:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return (0, 0) if target.exists() else (None, None)
    try:
        frame = pd.read_csv(target)
        return len(frame), len(frame.columns)
    except Exception:
        return None, None


def build_state_inventory(state_paths: dict[str, str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name, raw_path in state_paths.items():
        target = Path(raw_path)
        exists = target.exists()
        row_count, column_count = csv_shape(str(target))
        size = target.stat().st_size if exists else 0
        if not exists:
            status = "MISSING"
        elif row_count is None:
            status = "UNREADABLE"
        elif size == 0:
            status = "EMPTY"
        else:
            status = "OK"
        rows.append({
            "state_name": name,
            "path": str(target),
            "exists": exists,
            "size_bytes": size,
            "row_count": row_count,
            "column_count": column_count,
            "modified_at": datetime.fromtimestamp(target.stat().st_mtime).isoformat(timespec="seconds") if exists else "",
            "sha256": sha256_file(str(target)),
            "schema_version": STATE_SCHEMA_VERSION,
            "status": status,
        })
    return pd.DataFrame(rows, columns=STATE_INVENTORY_COLUMNS)


def snapshot_state_files(
    today: str,
    state_paths: dict[str, str],
    snapshot_root: str = "data/state_snapshots",
) -> pd.DataFrame:
    destination = Path(snapshot_root) / today
    destination.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for name, raw_path in state_paths.items():
        source = Path(raw_path)
        suffix = source.suffix or ".dat"
        snapshot_path = destination / f"{name}{suffix}"
        if source.exists() and source.is_file():
            shutil.copy2(source, snapshot_path)
            status = "SNAPSHOT_CREATED"
            size = snapshot_path.stat().st_size
            checksum = sha256_file(str(snapshot_path))
        else:
            status = "SOURCE_MISSING"
            size = 0
            checksum = ""
        rows.append({
            "snapshot_date": today,
            "state_name": name,
            "source_path": str(source),
            "snapshot_path": str(snapshot_path),
            "status": status,
            "size_bytes": size,
            "sha256": checksum,
        })
    return pd.DataFrame(rows, columns=STATE_SNAPSHOT_COLUMNS)


def release_status_value(readiness: pd.DataFrame) -> str:
    if readiness is None or readiness.empty or "release_status" not in readiness.columns:
        return "UNKNOWN"
    return optional_text(readiness.iloc[0].get("release_status")) or "UNKNOWN"


def build_release_readiness(
    run_health: pd.DataFrame,
    signal_governance: pd.DataFrame,
    sector_leader_performance: pd.DataFrame,
    paper_performance: pd.DataFrame,
    paper_trade_history: pd.DataFrame,
    paper_risk_budget: pd.DataFrame,
) -> pd.DataFrame:
    health = run_health_overall(run_health)
    degradation_count = int((signal_governance.get("status", pd.Series(dtype=str)) == "劣化警戒").sum()) if signal_governance is not None and not signal_governance.empty else 0
    ten_day_stats = performance_overall_stats(sector_leader_performance, 10)
    leader_evidence = int(ten_day_stats.get("count", 0) or 0)
    paper_trades = len(paper_trade_history) if paper_trade_history is not None else 0
    perf = {} if paper_performance is None or paper_performance.empty else paper_performance.iloc[0].to_dict()
    paper_win_rate = perf.get("win_rate")
    paper_equity = float(perf.get("equity", PAPER_INITIAL_CAPITAL) or PAPER_INITIAL_CAPITAL)
    paper_return = paper_equity / PAPER_INITIAL_CAPITAL - 1
    paper_drawdown = float(perf.get("drawdown", 0.0) or 0.0)
    risk_failures = int((paper_risk_budget.get("status", pd.Series(dtype=str)) == "FAIL").sum()) if paper_risk_budget is not None and not paper_risk_budget.empty else 0

    criteria = [
        ("Run Health", health, "PASS", health == "PASS", True, "全データ品質ゲートがPASS"),
        ("シグナル劣化", degradation_count, "0件", degradation_count == 0, True, "Signal Governanceの劣化警戒がない"),
        ("業種リーダー10日実績", leader_evidence, "30件以上", leader_evidence >= 30, False, "十分なアウトオブサンプル実績"),
        ("ペーパー決済実績", paper_trades, "20件以上", paper_trades >= 20, False, "出口ルールを含む運用実績"),
        ("ペーパー勝率", paper_win_rate, "50%以上", paper_win_rate is not None and not pd.isna(paper_win_rate) and float(paper_win_rate) >= 0.50, False, "決済済み取引の勝率"),
        ("ペーパー累積収益", paper_return, "0%超", paper_return > 0, False, "仮想元本に対する累積収益"),
        ("最大ドローダウン", paper_drawdown, "-10%以上", paper_drawdown >= -0.10, True, "ピーク資産からの下落を10%以内に制御"),
        ("リスク予算超過", risk_failures, "0件", risk_failures == 0, True, "銘柄・業種・総投資比率の上限遵守"),
    ]
    blocking_failure = any(blocking and not passed for _, _, _, passed, blocking, _ in criteria)
    all_passed = all(passed for _, _, _, passed, _, _ in criteria)
    if blocking_failure:
        release_status = "HOLD"
    elif all_passed:
        release_status = "READY_FOR_MANUAL_REVIEW"
    elif paper_trades == 0:
        release_status = "RESEARCH"
    else:
        release_status = "PAPER_VALIDATION"
    rows = [{
        "release_status": release_status,
        "execution_mode": EXECUTION_MODE,
        "criterion": criterion,
        "actual": actual,
        "required": required,
        "passed": passed,
        "blocking": blocking,
        "detail": detail,
    } for criterion, actual, required, passed, blocking, detail in criteria]
    return pd.DataFrame(rows, columns=RELEASE_READINESS_COLUMNS)


def build_operational_alerts(readiness: pd.DataFrame) -> pd.DataFrame:
    if readiness is None or readiness.empty:
        return pd.DataFrame([{
            "severity": "P0",
            "category": "release",
            "title": "リリース判定を生成できません",
            "status": "OPEN",
            "actual": "UNKNOWN",
            "required": "readiness available",
            "action": "新規ペーパーエントリーを停止し、状態ファイルを確認",
        }], columns=OPERATIONAL_ALERT_COLUMNS)
    alerts: list[dict[str, Any]] = []
    for _, row in readiness[readiness["passed"] != True].iterrows():
        criterion = optional_text(row.get("criterion"))
        blocking = bool(row.get("blocking"))
        if criterion in {"Run Health", "最大ドローダウン", "リスク予算超過"}:
            severity = "P0"
            action = "新規ペーパーエントリーを停止し、原因解消までHOLD"
        elif criterion == "シグナル劣化":
            severity = "P1"
            action = "対象シグナルを縮小し、劣化原因をレビュー"
        elif blocking:
            severity = "P1"
            action = "ブロッキング条件を解消"
        else:
            severity = "P2"
            action = "実績を蓄積し、昇格条件を再評価"
        alerts.append({
            "severity": severity,
            "category": "release_readiness",
            "title": criterion,
            "status": "OPEN",
            "actual": row.get("actual"),
            "required": row.get("required"),
            "action": action,
        })
    if not alerts:
        alerts.append({
            "severity": "INFO",
            "category": "release_readiness",
            "title": "全昇格条件を充足",
            "status": "CLOSED",
            "actual": release_status_value(readiness),
            "required": "READY_FOR_MANUAL_REVIEW",
            "action": "手動レビューを実施。自動発注は引き続き無効",
        })
    order = {"P0": 0, "P1": 1, "P2": 2, "INFO": 3}
    result = pd.DataFrame(alerts, columns=OPERATIONAL_ALERT_COLUMNS)
    result["severity_order"] = result["severity"].map(order).fillna(9)
    return result.sort_values(["severity_order", "title"]).drop(columns=["severity_order"])


def build_execution_audit(
    today: str,
    readiness: pd.DataFrame,
    alerts: pd.DataFrame,
    inventory: pd.DataFrame,
    snapshots: pd.DataFrame,
    run_health: pd.DataFrame,
) -> pd.DataFrame:
    manifest_material = "|".join(
        inventory.sort_values("state_name").get("sha256", pd.Series(dtype=str)).fillna("").astype(str).tolist()
    )
    manifest_sha = hashlib.sha256(manifest_material.encode("utf-8")).hexdigest()
    return pd.DataFrame([{
        "run_id": f"{today}-{APP_VERSION}",
        "date": today,
        "app_version": APP_VERSION,
        "execution_mode": EXECUTION_MODE,
        "release_status": release_status_value(readiness),
        "run_health": run_health_overall(run_health),
        "p0_alerts": int((alerts.get("severity", pd.Series(dtype=str)) == "P0").sum()) if alerts is not None and not alerts.empty else 0,
        "p1_alerts": int((alerts.get("severity", pd.Series(dtype=str)) == "P1").sum()) if alerts is not None and not alerts.empty else 0,
        "p2_alerts": int((alerts.get("severity", pd.Series(dtype=str)) == "P2").sum()) if alerts is not None and not alerts.empty else 0,
        "state_files_ok": int((inventory.get("status", pd.Series(dtype=str)) == "OK").sum()) if inventory is not None and not inventory.empty else 0,
        "state_files_total": len(inventory) if inventory is not None else 0,
        "snapshots_created": int((snapshots.get("status", pd.Series(dtype=str)) == "SNAPSHOT_CREATED").sum()) if snapshots is not None and not snapshots.empty else 0,
        "manifest_sha256": manifest_sha,
    }], columns=EXECUTION_AUDIT_COLUMNS)


def append_execution_audit(path: str, current: pd.DataFrame) -> pd.DataFrame:
    old = load_csv_with_columns(path, EXECUTION_AUDIT_COLUMNS)
    frames = [frame for frame in (old, current) if frame is not None and not frame.empty]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=EXECUTION_AUDIT_COLUMNS)
    if not combined.empty:
        combined = combined.drop_duplicates("run_id", keep="last").sort_values(["date", "run_id"])
    atomic_write_csv(combined, path)
    return combined[EXECUTION_AUDIT_COLUMNS]


def plain_release_readiness_section(readiness: pd.DataFrame, alerts: pd.DataFrame, inventory: pd.DataFrame) -> list[str]:
    status = release_status_value(readiness)
    lines = [
        "【リリース準備状況】",
        f"判定: {status} / 実行モード: {EXECUTION_MODE}",
        "証券会社への自動発注は無効です。昇格は手動レビューまでです。",
    ]
    failed = readiness[readiness["passed"] != True] if readiness is not None and not readiness.empty else pd.DataFrame()
    for _, row in failed.head(5).iterrows():
        lines.append(f"  未達: {row['criterion']} / 実績 {row['actual']} / 条件 {row['required']}")
    if alerts is not None and not alerts.empty:
        counts = {severity: int((alerts["severity"] == severity).sum()) for severity in ["P0", "P1", "P2"]}
        lines.append(f"アラート: P0 {counts['P0']} / P1 {counts['P1']} / P2 {counts['P2']}")
    if inventory is not None and not inventory.empty:
        lines.append(f"状態ファイル: OK {int((inventory['status'] == 'OK').sum())}/{len(inventory)}")
    lines.append("")
    return lines


def html_release_readiness_section(readiness: pd.DataFrame, alerts: pd.DataFrame, inventory: pd.DataFrame) -> str:
    status = release_status_value(readiness)
    color = "#15803d" if status == "READY_FOR_MANUAL_REVIEW" else "#b91c1c" if status == "HOLD" else "#a16207"
    failed = readiness[readiness["passed"] != True] if readiness is not None and not readiness.empty else pd.DataFrame()
    items = "".join(
        f'<div style="font-size:11px;color:#b45309;margin-top:3px">未達: {html_text(row["criterion"])} ・ 実績 {html_text(row["actual"])} ・ 条件 {html_text(row["required"])}</div>'
        for _, row in failed.head(5).iterrows()
    )
    alert_text = ""
    if alerts is not None and not alerts.empty:
        alert_text = " / ".join(f'{severity} {int((alerts["severity"] == severity).sum())}' for severity in ["P0", "P1", "P2"])
    state_ok = int((inventory.get("status", pd.Series(dtype=str)) == "OK").sum()) if inventory is not None and not inventory.empty else 0
    state_total = len(inventory) if inventory is not None else 0
    return f'''<div style="background:#fff;border:2px solid {color};border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:{color}">リリース準備状況 <span style="float:right">{html_text(status)}</span></div>
<div style="clear:both;font-size:11px;color:#64748b;margin-top:5px">実行モード {html_text(EXECUTION_MODE)}。証券会社への自動発注は無効です。</div>
<div style="font-size:12px;color:#334155;margin-top:7px">アラート {html_text(alert_text)} ・ 状態ファイル OK {state_ok}/{state_total}</div>{items}</div>'''
"""

replace_once(
    '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
    release_functions + '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_signal_history: pd.DataFrame, sector_leader_outcomes: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_trade_history: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_signal_history: pd.DataFrame, sector_leader_outcomes: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_trade_history: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, release_readiness: pd.DataFrame, operational_alerts: pd.DataFrame, state_inventory: pd.DataFrame, state_snapshots: pd.DataFrame, execution_audit: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
)
replace_once(
    '        paper_performance.to_excel(w, sheet_name="Paper Performance", index=False)\n        new_entries.to_excel(w, sheet_name="New Entries", index=False)',
    '        paper_performance.to_excel(w, sheet_name="Paper Performance", index=False)\n        release_readiness.to_excel(w, sheet_name="Release Readiness", index=False)\n        operational_alerts.to_excel(w, sheet_name="Operational Alerts", index=False)\n        state_inventory.to_excel(w, sheet_name="State Inventory", index=False)\n        state_snapshots.to_excel(w, sheet_name="State Snapshots", index=False)\n        execution_audit.to_excel(w, sheet_name="Execution Audit", index=False)\n        new_entries.to_excel(w, sheet_name="New Entries", index=False)',
)

replace_once(
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, release_readiness: pd.DataFrame, operational_alerts: pd.DataFrame, state_inventory: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '    lines += plain_paper_portfolio_section(paper_portfolio, paper_trade_plan, paper_performance, paper_risk_budget)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
    '    lines += plain_paper_portfolio_section(paper_portfolio, paper_trade_plan, paper_performance, paper_risk_budget)\n    lines += plain_release_readiness_section(release_readiness, operational_alerts, state_inventory)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
)
replace_once(
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, release_readiness: pd.DataFrame, operational_alerts: pd.DataFrame, state_inventory: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '        html_paper_portfolio_section(paper_portfolio, paper_trade_plan, paper_performance, paper_risk_budget),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
    '        html_paper_portfolio_section(paper_portfolio, paper_trade_plan, paper_performance, paper_risk_budget),\n        html_release_readiness_section(release_readiness, operational_alerts, state_inventory),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
)
replace_once(
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, paper_portfolio: pd.DataFrame, paper_trade_plan: pd.DataFrame, paper_risk_budget: pd.DataFrame, paper_performance: pd.DataFrame, release_readiness: pd.DataFrame, operational_alerts: pd.DataFrame, state_inventory: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
)
replace_once(
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, priority_changes, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, priority_changes, cfg), "html", "utf-8"))',
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, release_readiness, operational_alerts, state_inventory, priority_changes, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, release_readiness, operational_alerts, state_inventory, priority_changes, cfg), "html", "utf-8"))',
)

replace_once(
    '    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)\n\n    elapsed = round(perf_counter() - started_at, 1)',
    '    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)\n    state_paths = {\n        "ranking_history": cfg["data"]["ranking_history_path"],\n        "market_temperature": temp_path,\n        "sector_leader_signals": sector_history_path,\n        "paper_portfolio": "data/paper_portfolio.csv",\n        "paper_trade_history": "data/paper_trade_history.csv",\n        "paper_equity_history": "data/paper_equity_history.csv",\n    }\n    state_inventory = build_state_inventory(state_paths)\n    state_snapshots = snapshot_state_files(today, state_paths)\n    release_readiness = build_release_readiness(run_health, signal_governance, sector_leader_performance, paper_performance, paper_trade_history, paper_risk_budget)\n    operational_alerts = build_operational_alerts(release_readiness)\n    current_audit = build_execution_audit(today, release_readiness, operational_alerts, state_inventory, state_snapshots, run_health)\n    execution_audit = append_execution_audit("data/execution_audit.csv", current_audit)\n\n    elapsed = round(perf_counter() - started_at, 1)',
)
replace_once(
    '        "レポート形式": "dashboard_paper_portfolio_v15",',
    '        "レポート形式": "dashboard_release_readiness_v16",',
)
replace_once(
    '        "ペーパー決済数": len(paper_trade_history),\n        "重点候補数": priority_change_count(priority_changes, "current"),',
    '        "ペーパー決済数": len(paper_trade_history),\n        "リリース判定": release_status_value(release_readiness),\n        "実行モード": EXECUTION_MODE,\n        "運用P0アラート": int((operational_alerts.get("severity", pd.Series(dtype=str)) == "P0").sum()) if not operational_alerts.empty else 0,\n        "運用P1アラート": int((operational_alerts.get("severity", pd.Series(dtype=str)) == "P1").sum()) if not operational_alerts.empty else 0,\n        "状態ファイルOK": int((state_inventory.get("status", pd.Series(dtype=str)) == "OK").sum()) if not state_inventory.empty else 0,\n        "状態ファイル総数": len(state_inventory),\n        "状態スナップショット数": int((state_snapshots.get("status", pd.Series(dtype=str)) == "SNAPSHOT_CREATED").sum()) if not state_snapshots.empty else 0,\n        "重点候補数": priority_change_count(priority_changes, "current"),',
)
replace_once(
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, sector_momentum, sector_rotation, sector_leaders, sector_signal_history, sector_leader_outcomes, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_trade_history, paper_risk_budget, paper_performance, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)',
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, sector_momentum, sector_rotation, sector_leaders, sector_signal_history, sector_leader_outcomes, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_trade_history, paper_risk_budget, paper_performance, release_readiness, operational_alerts, state_inventory, state_snapshots, execution_audit, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)',
)
replace_once(
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, priority_changes, cfg)',
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, paper_portfolio, paper_trade_plan, paper_risk_budget, paper_performance, release_readiness, operational_alerts, state_inventory, priority_changes, cfg)',
)

path.write_text(text, encoding="utf-8")
print("Applied release readiness and operations audit batch")
