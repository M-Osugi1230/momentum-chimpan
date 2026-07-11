from __future__ import annotations

import html
from typing import Any

import pandas as pd


LIFECYCLE_COLUMNS = [
    "previous_relative_strength_date",
    "previous_relative_strength_score",
    "previous_relative_strength_rank",
    "previous_relative_strength_grade",
    "previous_dual_outperformer",
    "relative_strength_score_delta",
    "relative_strength_rank_change",
    "relative_strength_direction",
    "relative_strength_strong_streak",
    "dual_outperformer_streak",
    "relative_strength_total_strong_days",
    "relative_strength_run_count",
    "relative_strength_first_date",
    "relative_strength_best_score",
    "relative_strength_best_rank",
    "relative_strength_new_high",
    "relative_strength_lifecycle",
    "relative_strength_alert",
    "relative_strength_trajectory_score",
    "relative_strength_lifecycle_reason",
]

LIFECYCLE_ORDER = {
    "急加速": 0,
    "再浮上": 1,
    "加速": 2,
    "主導継続": 3,
    "主導": 4,
    "継続": 5,
    "初登場": 6,
    "失速警戒": 7,
    "崩れ": 8,
    "低位": 9,
}

POSITIVE_STATES = ["急加速", "再浮上", "加速", "主導継続", "主導"]
WARNING_STATES = ["失速警戒", "崩れ"]


def normalize_code(value: Any) -> str:
    return str(value or "").strip().split(".")[0].zfill(4)


def optional_number(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def row_number(row: pd.Series, column: str, default: float = 0.0) -> float:
    value = optional_number(row.get(column))
    return default if value is None else value


def optional_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none"} else text


def boolean_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def prior_relative_strength(history: pd.DataFrame, today: str) -> pd.DataFrame:
    columns = [
        "date",
        "code",
        "relative_strength_score",
        "relative_strength_rank",
        "relative_strength_grade",
        "dual_outperformer",
    ]
    if history is None or history.empty or not {"date", "code"}.issubset(history.columns):
        return pd.DataFrame(columns=[*columns, "date_sort"])

    work = history.copy()
    for column in columns:
        if column not in work.columns:
            work[column] = None
    work["code"] = work["code"].map(normalize_code)
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work["relative_strength_score"] = pd.to_numeric(work["relative_strength_score"], errors="coerce")
    work["relative_strength_rank"] = pd.to_numeric(work["relative_strength_rank"], errors="coerce")
    work["dual_outperformer"] = work["dual_outperformer"].map(boolean_value)
    work = work.dropna(subset=["date_sort"])
    work = work[work["date"].astype(str) != str(today)]
    return work[[*columns, "date_sort"]].sort_values(["date_sort", "code"])


def consecutive_count(states: dict[str, tuple[bool, bool]], dates: list[str], index: int) -> int:
    count = 0
    for report_date in reversed(dates):
        if states.get(report_date, (False, False))[index]:
            count += 1
        else:
            break
    return count


def lifecycle_values(
    row: pd.Series,
    previous_by_code: pd.DataFrame,
    code_history: pd.DataFrame,
    report_dates: list[str],
    today: str,
) -> dict[str, Any]:
    stock_code = normalize_code(row.get("code"))
    score = row_number(row, "relative_strength_score", 50.0)
    rank = int(row_number(row, "relative_strength_rank", 9999.0))
    grade = optional_text(row.get("relative_strength_grade")) or "C"
    dual = boolean_value(row.get("dual_outperformer"))

    previous = previous_by_code.loc[stock_code] if stock_code in previous_by_code.index else None
    if isinstance(previous, pd.DataFrame):
        previous = previous.iloc[-1]
    previous_date = optional_text(previous.get("date")) if previous is not None else ""
    previous_score = optional_number(previous.get("relative_strength_score")) if previous is not None else None
    previous_rank_value = optional_number(previous.get("relative_strength_rank")) if previous is not None else None
    previous_rank = int(previous_rank_value) if previous_rank_value is not None else None
    previous_grade = optional_text(previous.get("relative_strength_grade")) if previous is not None else ""
    previous_dual = boolean_value(previous.get("dual_outperformer")) if previous is not None else False

    score_delta = None if previous_score is None else score - previous_score
    rank_change = None if previous_rank is None else previous_rank - rank

    states: dict[str, tuple[bool, bool]] = {}
    if code_history is not None and not code_history.empty:
        for _, history_row in code_history.iterrows():
            history_date = pd.Timestamp(history_row["date_sort"]).date().isoformat()
            states[history_date] = (
                row_number(history_row, "relative_strength_score") >= 70,
                boolean_value(history_row.get("dual_outperformer")),
            )
    states[str(today)] = (score >= 70, dual)

    strong_streak = consecutive_count(states, report_dates, 0)
    dual_streak = consecutive_count(states, report_dates, 1)
    strong_states = [states.get(date, (False, False))[0] for date in report_dates]
    total_strong_days = int(sum(strong_states))
    run_count = 0
    active = False
    for is_strong in strong_states:
        if is_strong and not active:
            run_count += 1
        active = is_strong

    historical_scores = pd.to_numeric(
        code_history.get("relative_strength_score", pd.Series(dtype=float)), errors="coerce"
    ).dropna() if code_history is not None and not code_history.empty else pd.Series(dtype=float)
    historical_ranks = pd.to_numeric(
        code_history.get("relative_strength_rank", pd.Series(dtype=float)), errors="coerce"
    ).dropna() if code_history is not None and not code_history.empty else pd.Series(dtype=float)
    best_historical_score = float(historical_scores.max()) if not historical_scores.empty else None
    best_historical_rank = int(historical_ranks.min()) if not historical_ranks.empty else None
    new_high = (
        best_historical_score is None
        or score > best_historical_score
        or best_historical_rank is None
        or rank < best_historical_rank
    )
    best_score = score if best_historical_score is None else max(score, best_historical_score)
    best_rank = rank if best_historical_rank is None else min(rank, best_historical_rank)
    history_dates = code_history["date_sort"].dropna().sort_values() if code_history is not None and not code_history.empty else pd.Series(dtype="datetime64[ns]")
    first_date = history_dates.iloc[0].date().isoformat() if not history_dates.empty else str(today)

    if previous_score is None:
        lifecycle = "初登場"
    elif previous_score >= 70 and score < 55 and score_delta is not None and score_delta <= -12:
        lifecycle = "崩れ"
    elif (score_delta is not None and score_delta <= -8) or (rank_change is not None and rank_change <= -15):
        lifecycle = "失速警戒"
    elif previous_score < 55 and score >= 65 and score_delta is not None and score_delta >= 8:
        lifecycle = "再浮上"
    elif score >= 70 and (
        (score_delta is not None and score_delta >= 8)
        or (rank_change is not None and rank_change >= 15)
    ):
        lifecycle = "急加速"
    elif score >= 65 and (
        (score_delta is not None and score_delta >= 4)
        or (rank_change is not None and rank_change >= 8)
    ):
        lifecycle = "加速"
    elif score >= 70 and strong_streak >= 5:
        lifecycle = "主導継続"
    elif score >= 70:
        lifecycle = "主導"
    elif score >= 55:
        lifecycle = "継続"
    else:
        lifecycle = "低位"

    if lifecycle in {"急加速", "再浮上"}:
        alert = "調査優先"
    elif lifecycle in {"加速", "主導継続", "主導"}:
        alert = "継続確認"
    elif lifecycle in WARNING_STATES:
        alert = "警戒"
    else:
        alert = "観察"

    if score_delta is None:
        direction = "履歴開始"
    elif score_delta >= 4 or (rank_change is not None and rank_change >= 8):
        direction = "改善"
    elif score_delta <= -4 or (rank_change is not None and rank_change <= -8):
        direction = "悪化"
    else:
        direction = "横ばい"

    trajectory = score
    if score_delta is not None:
        trajectory += min(max(score_delta, -15), 15) * 1.2
    if rank_change is not None:
        trajectory += min(max(rank_change, -30), 30) * 0.35
    trajectory += min(strong_streak, 10) * 1.2
    trajectory += min(dual_streak, 10) * 0.8
    trajectory = round(min(max(trajectory, 0.0), 100.0), 1)

    reasons = [f"相対強度{score:.1f}点・{grade}"]
    if score_delta is not None:
        reasons.append(f"前回比{score_delta:+.1f}点")
    if rank_change is not None:
        reasons.append(f"順位{rank_change:+d}")
    if strong_streak:
        reasons.append(f"A以上{strong_streak}日")
    if dual_streak:
        reasons.append(f"市場・同業双方超過{dual_streak}日")
    if new_high:
        reasons.append("過去最高水準更新")

    return {
        "previous_relative_strength_date": previous_date,
        "previous_relative_strength_score": previous_score,
        "previous_relative_strength_rank": previous_rank,
        "previous_relative_strength_grade": previous_grade,
        "previous_dual_outperformer": previous_dual,
        "relative_strength_score_delta": score_delta,
        "relative_strength_rank_change": rank_change,
        "relative_strength_direction": direction,
        "relative_strength_strong_streak": strong_streak,
        "dual_outperformer_streak": dual_streak,
        "relative_strength_total_strong_days": total_strong_days,
        "relative_strength_run_count": run_count,
        "relative_strength_first_date": first_date,
        "relative_strength_best_score": best_score,
        "relative_strength_best_rank": best_rank,
        "relative_strength_new_high": bool(new_high),
        "relative_strength_lifecycle": lifecycle,
        "relative_strength_alert": alert,
        "relative_strength_trajectory_score": trajectory,
        "relative_strength_lifecycle_reason": " / ".join(reasons),
    }


def attach(frame: pd.DataFrame, history: pd.DataFrame, today: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        result = frame.copy() if frame is not None else pd.DataFrame()
        for column in LIFECYCLE_COLUMNS:
            if column not in result.columns:
                result[column] = pd.Series(dtype="object")
        return result

    result = frame.copy()
    result["code"] = result["code"].map(normalize_code)
    prior = prior_relative_strength(history, today)
    report_dates = sorted(
        set(prior.get("date", pd.Series(dtype=str)).astype(str)) | {str(today)},
        key=pd.Timestamp,
    )
    latest = prior.sort_values("date_sort").drop_duplicates("code", keep="last")
    previous_by_code = latest.set_index("code", drop=False) if not latest.empty else pd.DataFrame()
    histories = {code: group.sort_values("date_sort") for code, group in prior.groupby("code")}
    lifecycle_rows = result.apply(
        lambda row: pd.Series(
            lifecycle_values(
                row,
                previous_by_code,
                histories.get(normalize_code(row.get("code")), pd.DataFrame()),
                report_dates,
                today,
            )
        ),
        axis=1,
    )
    for column in LIFECYCLE_COLUMNS:
        result[column] = lifecycle_rows[column].values
    return result


def build_table(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "relative_strength_lifecycle",
        "relative_strength_alert",
        "relative_strength_trajectory_score",
        "relative_strength_rank",
        "rank",
        "code",
        "name",
        "sector33",
        "score",
        "relative_strength_score",
        "relative_strength_grade",
        "dual_outperformer",
        "previous_relative_strength_date",
        "previous_relative_strength_score",
        "relative_strength_score_delta",
        "previous_relative_strength_rank",
        "relative_strength_rank_change",
        "relative_strength_direction",
        "relative_strength_strong_streak",
        "dual_outperformer_streak",
        "relative_strength_total_strong_days",
        "relative_strength_run_count",
        "relative_strength_first_date",
        "relative_strength_best_score",
        "relative_strength_best_rank",
        "relative_strength_new_high",
        "market_relative_20d",
        "sector_relative_20d",
        "market_relative_60d",
        "sector_relative_60d",
        "relative_strength_lifecycle_reason",
        "trading_value",
        "volume_ratio",
    ]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    result = frame.copy()
    result["_lifecycle_order"] = result.get(
        "relative_strength_lifecycle", pd.Series(index=result.index, dtype=str)
    ).map(LIFECYCLE_ORDER).fillna(99)
    result = result.sort_values(
        ["_lifecycle_order", "relative_strength_trajectory_score", "relative_strength_score", "rank"],
        ascending=[True, False, False, True],
    ).drop(columns="_lifecycle_order")
    return result[[column for column in columns if column in result.columns]].reset_index(drop=True)


def lifecycle_count(frame: pd.DataFrame, status: str) -> int:
    if frame is None or frame.empty or "relative_strength_lifecycle" not in frame.columns:
        return 0
    return int((frame["relative_strength_lifecycle"] == status).sum())


def format_delta(value: Any, digits: int = 1) -> str:
    number = optional_number(value)
    if number is None:
        return "-"
    return f"{number:+.{digits}f}" if digits else f"{int(number):+d}"


def plain_section(frame: pd.DataFrame, positive_limit: int = 8, warning_limit: int = 5) -> list[str]:
    if frame is None or frame.empty:
        return ["【相対強度ライフサイクル】", "比較可能な相対強度履歴がありません。", ""]
    counts = {state: lifecycle_count(frame, state) for state in [*POSITIVE_STATES, *WARNING_STATES]}
    summary = " / ".join(
        f"{state} {counts[state]}件" for state in [*POSITIVE_STATES, *WARNING_STATES] if counts[state]
    ) or "大きな変化なし"
    lines = [
        "【相対強度ライフサイクル】",
        "前回差・順位変化・継続日数から強さの推移を判定します。売買推奨ではありません。",
        summary,
    ]
    for title, states, limit in [
        ("強さが改善・継続", POSITIVE_STATES, positive_limit),
        ("失速・崩れ警戒", WARNING_STATES, warning_limit),
    ]:
        subset = frame[frame["relative_strength_lifecycle"].isin(states)].head(limit)
        if subset.empty:
            continue
        lines.append(f"■ {title}")
        for _, row in subset.iterrows():
            lines.append(
                f"{optional_text(row.get('relative_strength_lifecycle'))}｜"
                f"#{int(row_number(row, 'relative_strength_rank'))} {row['code']} {row['name']}｜"
                f"{row_number(row, 'relative_strength_score'):.1f}点｜"
                f"前回比 {format_delta(row.get('relative_strength_score_delta'))}点｜"
                f"順位 {format_delta(row.get('relative_strength_rank_change'), 0)}｜"
                f"A以上 {int(row_number(row, 'relative_strength_strong_streak'))}日｜"
                f"双方超過 {int(row_number(row, 'dual_outperformer_streak'))}日"
            )
    lines.append("")
    return lines


def html_section(frame: pd.DataFrame, positive_limit: int = 8, warning_limit: int = 5) -> str:
    if frame is None or frame.empty:
        return '<div><b>相対強度ライフサイクル</b><div>比較可能な相対強度履歴がありません。</div></div>'
    colors = {
        "急加速": "#b45309",
        "再浮上": "#7c3aed",
        "加速": "#15803d",
        "主導継続": "#1d4ed8",
        "主導": "#0369a1",
        "失速警戒": "#c2410c",
        "崩れ": "#b91c1c",
    }
    counts = {state: lifecycle_count(frame, state) for state in [*POSITIVE_STATES, *WARNING_STATES]}
    summary = " ・ ".join(
        f"{state} {counts[state]}件" for state in [*POSITIVE_STATES, *WARNING_STATES] if counts[state]
    ) or "大きな変化なし"
    groups: list[str] = []
    for title, states, limit in [
        ("強さが改善・継続", POSITIVE_STATES, positive_limit),
        ("失速・崩れ警戒", WARNING_STATES, warning_limit),
    ]:
        items: list[str] = []
        for _, row in frame[frame["relative_strength_lifecycle"].isin(states)].head(limit).iterrows():
            state = optional_text(row.get("relative_strength_lifecycle"))
            items.append(
                '<div style="border-top:1px solid #e5e7eb;padding:9px 0">'
                f'<b>{html.escape(state)}｜#{int(row_number(row, "relative_strength_rank"))} '
                f'{html.escape(str(row["code"]))} {html.escape(str(row["name"]))}</b>'
                f'<span style="float:right;color:{colors.get(state, "#475569")}">'
                f'{row_number(row, "relative_strength_score"):.1f}点</span>'
                '<div style="clear:both;font-size:11px">'
                f'前回比 {format_delta(row.get("relative_strength_score_delta"))}点 ・ '
                f'順位 {format_delta(row.get("relative_strength_rank_change"), 0)} ・ '
                f'A以上 {int(row_number(row, "relative_strength_strong_streak"))}日 ・ '
                f'双方超過 {int(row_number(row, "dual_outperformer_streak"))}日'
                '</div></div>'
            )
        if items:
            groups.append(
                f'<div style="font-weight:900;margin-top:10px">{html.escape(title)}</div>'
                + "".join(items)
            )
    return (
        '<div style="background:#fff;border:2px solid #7c3aed;border-radius:18px;padding:16px;margin-top:14px">'
        '<div style="font-size:18px;font-weight:900;color:#581c87">相対強度ライフサイクル</div>'
        '<div style="font-size:12px;color:#64748b">'
        '前回差・順位変化・継続日数から強さの推移を判定します。売買推奨ではありません。'
        '</div>'
        f'<div style="font-size:12px;font-weight:800">{html.escape(summary)}</div>'
        + "".join(groups)
        + "</div>"
    )
