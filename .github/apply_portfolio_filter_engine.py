from pathlib import Path


path = Path("portfolio_research.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if old not in text:
        raise RuntimeError(f"anchor not found: {label}")
    text = text.replace(old, new, 1)


replace_once(
    'PORTFOLIO_RESEARCH_VERSION = "2026-07-11-execution-portfolio-v1"',
    'PORTFOLIO_RESEARCH_VERSION = "2026-07-11-execution-portfolio-v2-eligibility-calendar"',
    "portfolio version",
)
replace_once(
    'PRIORITY_ORDER = {"最優先": 0, "優先": 1, "監視": 2, "見送り": 3}\n\n\n',
    'PRIORITY_ORDER = {"最優先": 0, "優先": 1, "監視": 2, "見送り": 3}\n\n\n'
    'def signal_eligibility_mask(signals: pd.DataFrame) -> pd.Series:\n'
    '    """Return research eligibility while preserving the complete report calendar."""\n'
    '    if "portfolio_eligible" not in signals.columns:\n'
    '        return pd.Series(True, index=signals.index, dtype=bool)\n'
    '    values = signals["portfolio_eligible"]\n'
    '    if values.dtype == bool:\n'
    '        return values.fillna(False)\n'
    '    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y", "t"})\n\n\n',
    "eligibility helper",
)
replace_once(
    '    events = build_entry_events(signals, prices)\n'
    '    report_dates = set(pd.to_datetime(signals["signal_date"], errors="coerce").dropna().dt.normalize())\n'
    '    active_codes_by_report = {\n'
    '        pd.Timestamp(date).normalize(): set(group["code"].map(main.normalize_code))\n'
    '        for date, group in signals.groupby(pd.to_datetime(signals["signal_date"], errors="coerce").dt.normalize())\n'
    '    }\n',
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
    '        active_codes_by_report[pd.Timestamp(report_date).normalize()] = set(group["code"].map(main.normalize_code))\n',
    "simulation eligibility calendar",
)

path.write_text(text, encoding="utf-8")
print("portfolio eligibility calendar applied")
