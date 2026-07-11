from pathlib import Path


portfolio_path = Path("portfolio_research.py")
portfolio_text = portfolio_path.read_text(encoding="utf-8")
old_version = 'PORTFOLIO_RESEARCH_VERSION = "2026-07-11-execution-portfolio-v1"'
new_version = 'PORTFOLIO_RESEARCH_VERSION = "2026-07-11-execution-portfolio-v2-eligibility-calendar"'
if old_version in portfolio_text:
    portfolio_text = portfolio_text.replace(old_version, new_version, 1)
elif new_version not in portfolio_text:
    raise RuntimeError("portfolio version anchor not found")

helper_anchor = 'PRIORITY_ORDER = {"最優先": 0, "優先": 1, "監視": 2, "見送り": 3}\n\n\n'
helper = (
    helper_anchor
    + 'def signal_eligibility_mask(signals: pd.DataFrame) -> pd.Series:\n'
    + '    """Return research eligibility while preserving the complete report calendar."""\n'
    + '    if "portfolio_eligible" not in signals.columns:\n'
    + '        return pd.Series(True, index=signals.index, dtype=bool)\n'
    + '    values = signals["portfolio_eligible"]\n'
    + '    if values.dtype == bool:\n'
    + '        return values.fillna(False)\n'
    + '    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y", "t"})\n\n\n'
)
if "def signal_eligibility_mask(" not in portfolio_text:
    if helper_anchor not in portfolio_text:
        raise RuntimeError("eligibility helper anchor not found")
    portfolio_text = portfolio_text.replace(helper_anchor, helper, 1)

old_simulation = (
    '    events = build_entry_events(signals, prices)\n'
    '    report_dates = set(pd.to_datetime(signals["signal_date"], errors="coerce").dropna().dt.normalize())\n'
    '    active_codes_by_report = {\n'
    '        pd.Timestamp(date).normalize(): set(group["code"].map(main.normalize_code))\n'
    '        for date, group in signals.groupby(pd.to_datetime(signals["signal_date"], errors="coerce").dt.normalize())\n'
    '    }\n'
)
new_simulation = (
    '    signal_frame = signals.copy()\n'
    '    signal_frame["_report_date"] = pd.to_datetime(signal_frame["signal_date"], errors="coerce").dt.normalize()\n'
    '    report_dates = set(signal_frame["_report_date"].dropna())\n'
    '    eligible_mask = signal_eligibility_mask(signal_frame)\n'
    '    entry_signals = signal_frame.loc[eligible_mask].drop(columns=["_report_date"], errors="ignore")\n'
    '    events = build_entry_events(entry_signals, prices)\n'
    '    active_codes_by_report = {pd.Timestamp(report_date).normalize(): set() for report_date in report_dates}\n'
    '    for report_date, group in signal_frame.loc[eligible_mask].groupby("_report_date"):\n'
    '        if pd.isna(report_date):\n'
    '            continue\n'
    '        active_codes_by_report[pd.Timestamp(report_date).normalize()] = set(group["code"].map(main.normalize_code))\n'
)
if old_simulation in portfolio_text:
    portfolio_text = portfolio_text.replace(old_simulation, new_simulation, 1)
elif "eligible_mask = signal_eligibility_mask(signal_frame)" not in portfolio_text:
    raise RuntimeError("simulation eligibility calendar anchor not found")
portfolio_path.write_text(portfolio_text, encoding="utf-8")

lab_path = Path("portfolio_filter_lab.py")
lab_text = lab_path.read_text(encoding="utf-8")
old_status = '''    metrics_frame["improvement_status"] = "NOT_EVALUATED"
    if not baseline_rows.empty:
        evaluable = metrics_frame["sample_status"] == "EVALUABLE"
        improved_return = pd.to_numeric(metrics_frame.get("delta_excess_total_return_vs_baseline"), errors="coerce") > 0
        improved_drawdown = pd.to_numeric(metrics_frame.get("delta_max_drawdown_vs_baseline"), errors="coerce") >= 0
        metrics_frame.loc[evaluable & improved_return & improved_drawdown, "improvement_status"] = "IMPROVED"
        metrics_frame.loc[evaluable & ~(improved_return & improved_drawdown), "improvement_status"] = "NOT_IMPROVED"
        metrics_frame.loc[metrics_frame["filter_rule"] == "baseline", "improvement_status"] = "BASELINE"
    metrics_frame = metrics_frame.sort_values(
        ["sample_status", "excess_total_return", "max_drawdown"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
'''
new_status = '''    metrics_frame["improvement_status"] = "NOT_EVALUATED"
    if not baseline_rows.empty:
        evaluable = metrics_frame["sample_status"] == "EVALUABLE"
        total_return = pd.to_numeric(metrics_frame.get("total_return"), errors="coerce")
        excess_return = pd.to_numeric(metrics_frame.get("excess_total_return"), errors="coerce")
        improved_return = pd.to_numeric(metrics_frame.get("delta_excess_total_return_vs_baseline"), errors="coerce") > 0
        improved_drawdown = pd.to_numeric(metrics_frame.get("delta_max_drawdown_vs_baseline"), errors="coerce") >= 0
        outperformed = evaluable & total_return.gt(0) & excess_return.gt(0)
        positive_underperformer = evaluable & total_return.gt(0) & ~excess_return.gt(0)
        loss_reduced = evaluable & total_return.le(0) & improved_return & improved_drawdown
        metrics_frame.loc[outperformed, "improvement_status"] = "OUTPERFORMED"
        metrics_frame.loc[positive_underperformer, "improvement_status"] = "POSITIVE_BUT_UNDERPERFORMED"
        metrics_frame.loc[loss_reduced, "improvement_status"] = "LOSS_REDUCED_ONLY"
        metrics_frame.loc[
            evaluable & metrics_frame["improvement_status"].eq("NOT_EVALUATED"),
            "improvement_status",
        ] = "NOT_IMPROVED"
        metrics_frame.loc[metrics_frame["filter_rule"] == "baseline", "improvement_status"] = "BASELINE"
    metrics_frame["_sample_order"] = metrics_frame["sample_status"].map({"EVALUABLE": 0, "INSUFFICIENT": 1}).fillna(9)
    metrics_frame = metrics_frame.sort_values(
        ["_sample_order", "excess_total_return", "max_drawdown"],
        ascending=[True, False, False],
    ).drop(columns="_sample_order").reset_index(drop=True)
'''
if old_status in lab_text:
    lab_text = lab_text.replace(old_status, new_status, 1)
elif "LOSS_REDUCED_ONLY" not in lab_text:
    raise RuntimeError("filter evidence status anchor not found")
lab_path.write_text(lab_text, encoding="utf-8")
print("portfolio filter engine and evidence statuses applied")
