from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import site_builder


def sample_workbook(path: Path) -> None:
    summary = pd.DataFrame([{
        "実行日": "2026-07-13",
        "株価データ日": "2026-07-13",
        "Momentum Top100": 2,
        "Market Regime": "強気",
        "Market Regime Score": 93,
        "Data Quality A": 1,
        "Data Quality C": 1,
        "当日株価比率": 1.0,
        "Run Health": "PASS",
    }])
    top100 = pd.DataFrame([
        {
            "rank": 1, "code": "1001", "name": "サンプルA", "sector33": "機械",
            "close": 1000, "score": 90, "return_20d": 0.2, "return_5d": 0.05,
            "return_60d": 0.3, "volume_ratio": 3.0, "trading_value": 1_000_000_000,
            "data_quality_grade": "A", "relative_strength_grade": "S",
            "relative_strength_score": 95, "relative_strength_lifecycle": "初登場",
            "is_new_entry": True, "is_rising_fast": True,
        },
        {
            "rank": 2, "code": "1002", "name": "サンプルB", "sector33": "小売業",
            "close": 500, "score": 80, "return_20d": 0.1, "return_5d": 0.02,
            "return_60d": 0.15, "volume_ratio": 2.0, "trading_value": 500_000_000,
            "data_quality_grade": "C", "data_quality_warnings": "流動性を確認",
            "relative_strength_grade": "A", "relative_strength_score": 82,
            "relative_strength_lifecycle": "再浮上",
        },
    ])
    action = pd.DataFrame([{
        "code": "1001", "name": "サンプルA", "sector33": "機械",
        "research_bucket": "A", "daily_action_list": True, "daily_action_rank": 1,
        "action_priority": "A", "action_score": 90, "momentum_rank": 1,
        "momentum_score": 90, "why_today": "初動と出来高", "what_changed": "新規",
        "risk_summary": "過熱注意なし", "next_research_questions": "開示確認",
        "data_quality_grade": "A", "relative_strength_grade": "S",
    }])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        action.to_excel(writer, sheet_name="Action Priority", index=False)
        top100.to_excel(writer, sheet_name="Momentum Top100", index=False)
        top100.head(1).to_excel(writer, sheet_name="New Entries", index=False)
        top100.head(1).to_excel(writer, sheet_name="Rising Fast", index=False)
        pd.DataFrame([{"status": "新規", "code": "1001", "name": "サンプルA", "current_rank": 1}]).to_excel(writer, sheet_name="Priority Changes", index=False)
        pd.DataFrame([{"sector_rank": 1, "sector33": "機械", "sector_momentum_score": 70}]).to_excel(writer, sheet_name="Sector Momentum", index=False)
        pd.DataFrame([{"relative_strength_rank": 1, "code": "1001", "name": "サンプルA", "relative_strength_score": 95}]).to_excel(writer, sheet_name="Relative Strength", index=False)
        pd.DataFrame([{"relative_strength_lifecycle": "初登場", "code": "1001", "name": "サンプルA"}]).to_excel(writer, sheet_name="RS Lifecycle", index=False)
        pd.DataFrame([{"section": "Current Decision", "label": "出来高倍率", "status": "UNRESOLVED", "weight_points": 15}]).to_excel(writer, sheet_name="Research Evidence", index=False)
        pd.DataFrame([{"check_name": "overall", "status": "PASS", "detail": "PASS"}]).to_excel(writer, sheet_name="Run Health", index=False)


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        workbook = root / "daily_report.xlsx"
        output = root / "site"
        history = root / "ranking.csv"
        temperature = root / "temperature.csv"
        sample_workbook(workbook)
        pd.DataFrame([
            {"date": "2026-07-10", "code": "1001", "rank": 10, "score": 70},
            {"date": "2026-07-13", "code": "1001", "rank": 1, "score": 90},
        ]).to_csv(history, index=False)
        pd.DataFrame([
            {"date": "2026-07-13", "top100_avg_score": 63.14, "market_regime": "強気"}
        ]).to_csv(temperature, index=False)
        result = site_builder.build_site(
            workbook,
            output,
            ranking_history_path=history,
            market_temperature_path=temperature,
            site_url="https://example.test/",
        )
        validation = site_builder.validate_site(output)
        assert validation["passed"] is True
        assert result["manifest"]["report_date"] == "2026-07-13"
        assert (output / "downloads" / "daily_report.xlsx").read_bytes() == workbook.read_bytes()
        data_text = (output / "assets" / "data.js").read_text(encoding="utf-8")
        assert data_text.startswith("window.MOMENTUM_DASHBOARD=")
        payload = json.loads(data_text.split("=", 1)[1].rstrip(";\n"))
        assert len(payload["top100"]) == 2
        assert payload["top100"][0]["research_bucket"] == "A"
        assert len(payload["ranking_history"]["1001"]) == 2
        assert payload["automatic_strategy_change"] is False
        assert "EMAIL_APP_PASSWORD" not in data_text
        assert "@icloud.com" not in data_text
        assert "@gmail.com" not in data_text
        print("static dashboard validation passed")


if __name__ == "__main__":
    main()
