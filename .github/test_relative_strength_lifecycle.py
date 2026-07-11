from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pandas as pd

import main
import relative_strength_lifecycle as lifecycle


def current_row(code: str, score: float, rank: int, dual: bool = False) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": code,
                "name": f"Stock {code}",
                "sector33": "情報・通信業",
                "rank": rank,
                "score": 80,
                "relative_strength_score": score,
                "relative_strength_rank": rank,
                "relative_strength_grade": "A" if score >= 70 else "B" if score >= 55 else "C",
                "dual_outperformer": dual,
                "market_relative_20d": 0.03,
                "market_relative_60d": 0.05,
                "sector_relative_20d": 0.02,
                "sector_relative_60d": 0.04,
                "trading_value": 1_000_000_000,
                "volume_ratio": 2.0,
            }
        ]
    )


def history_rows(code: str, values: list[tuple[str, float, int, bool]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": date,
                "code": code,
                "relative_strength_score": score,
                "relative_strength_rank": rank,
                "relative_strength_grade": "A" if score >= 70 else "B" if score >= 55 else "C",
                "dual_outperformer": dual,
            }
            for date, score, rank, dual in values
        ]
    )


def lifecycle_state(
    code: str,
    current_score: float,
    current_rank: int,
    history: list[tuple[str, float, int, bool]],
    dual: bool = False,
) -> pd.Series:
    attached = lifecycle.attach(
        current_row(code, current_score, current_rank, dual),
        history_rows(code, history),
        "2026-07-11",
    )
    assert len(attached) == 1
    return attached.iloc[0]


def test_transitions() -> None:
    first = lifecycle.attach(current_row("1001", 72, 20), pd.DataFrame(), "2026-07-11").iloc[0]
    assert first["relative_strength_lifecycle"] == "初登場"

    surge = lifecycle_state("1002", 76, 20, [("2026-07-10", 60, 45, False)], True)
    assert surge["relative_strength_lifecycle"] == "急加速"
    assert surge["relative_strength_alert"] == "調査優先"

    resurfaced = lifecycle_state("1003", 66, 50, [("2026-07-10", 45, 75, False)])
    assert resurfaced["relative_strength_lifecycle"] == "再浮上"

    accelerating = lifecycle_state("1004", 68, 35, [("2026-07-10", 63, 45, False)])
    assert accelerating["relative_strength_lifecycle"] == "加速"

    warning = lifecycle_state("1005", 64, 45, [("2026-07-10", 75, 20, True)])
    assert warning["relative_strength_lifecycle"] == "失速警戒"
    assert warning["relative_strength_alert"] == "警戒"

    broken = lifecycle_state("1006", 50, 55, [("2026-07-10", 75, 20, True)])
    assert broken["relative_strength_lifecycle"] == "崩れ"

    leadership_history = [
        ("2026-07-07", 71, 25, True),
        ("2026-07-08", 72, 24, True),
        ("2026-07-09", 73, 23, True),
        ("2026-07-10", 74, 22, True),
    ]
    leader = lifecycle_state("1007", 75, 21, leadership_history, True)
    assert leader["relative_strength_lifecycle"] == "主導継続"
    assert int(leader["relative_strength_strong_streak"]) == 5
    assert int(leader["dual_outperformer_streak"]) == 5


def test_non_mutation_and_output() -> None:
    frame = current_row("2001", 75, 10, True)
    original = frame.copy(deep=True)
    result = lifecycle.attach(frame, pd.DataFrame(), "2026-07-11")
    pd.testing.assert_frame_equal(frame, original)
    assert result.loc[0, "rank"] == original.loc[0, "rank"]
    assert result.loc[0, "score"] == original.loc[0, "score"]
    assert "相対強度ライフサイクル" in "\n".join(lifecycle.plain_section(result))
    assert "相対強度ライフサイクル" in lifecycle.html_section(result)
    table = lifecycle.build_table(result)
    assert "relative_strength_trajectory_score" in table.columns


def test_main_integration_and_excel() -> None:
    assert main.APP_VERSION == "2026-07-11-dashboard-relative-strength-lifecycle-v19"
    assert set(lifecycle.LIFECYCLE_COLUMNS).issubset(main.ranking_history_columns())
    source = inspect.getsource(main.main)
    assert "rs_lifecycle.attach(all_ranked, history, today)" in source
    assert source.index("rs_lifecycle.attach(all_ranked, history, today)") < source.index("write_ranking_history")

    parameters = inspect.signature(main.excel_report).parameters
    kwargs = {}
    for name in parameters:
        if name == "path":
            continue
        if name == "summary":
            kwargs[name] = {"実行日": "2026-07-11"}
        elif name == "errors":
            kwargs[name] = []
        else:
            kwargs[name] = pd.DataFrame()
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "lifecycle.xlsx"
        main.excel_report(str(report), **kwargs)
        workbook = pd.ExcelFile(report)
        assert "RS Lifecycle" in workbook.sheet_names


if __name__ == "__main__":
    test_transitions()
    test_non_mutation_and_output()
    test_main_integration_and_excel()
    print("relative strength lifecycle validation passed")
