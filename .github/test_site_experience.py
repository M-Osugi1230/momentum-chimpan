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
import site_experience


def sample_workbook(path: Path) -> None:
    summary = pd.DataFrame([{
        "実行日": "2026-07-13",
        "株価データ日": "2026-07-13",
        "市場データ鮮度": "FRESH",
        "状態更新実行": "YES",
        "Momentum Top100": 2,
        "Market Regime": "強気",
        "Market Regime Score": 93,
        "急上昇": 1,
        "Data Quality A": 1,
        "Data Quality C": 1,
        "当日株価比率": 1.0,
        "Run Health": "PASS",
        "運用P0アラート": 0,
        "運用P1アラート": 0,
    }])
    top100 = pd.DataFrame([
        {"rank": 1, "code": "1001", "name": "サンプルA", "sector33": "機械", "score": 90, "return_5d": 0.05, "return_20d": 0.20, "volume_ratio": 3.0, "trading_value": 1_000_000_000, "relative_strength_grade": "S", "relative_strength_score": 95, "data_quality_grade": "A", "is_new_entry": True},
        {"rank": 2, "code": "1002", "name": "サンプルB", "sector33": "小売業", "score": 80, "return_5d": 0.02, "return_20d": 0.10, "volume_ratio": 2.0, "trading_value": 500_000_000, "relative_strength_grade": "A", "relative_strength_score": 82, "data_quality_grade": "C"},
    ])
    action = pd.DataFrame([{
        "code": "1001", "name": "サンプルA", "sector33": "機械", "research_bucket": "A", "daily_action_list": True, "daily_action_rank": 1, "action_priority": "A", "action_score": 90, "why_today": "初動と出来高", "what_changed": "新規", "risk_summary": "継続確認が必要", "data_quality_grade": "A", "relative_strength_grade": "S",
    }])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        action.to_excel(writer, sheet_name="Action Priority", index=False)
        top100.to_excel(writer, sheet_name="Momentum Top100", index=False)
        top100.head(1).to_excel(writer, sheet_name="New Entries", index=False)
        top100.head(1).to_excel(writer, sheet_name="Rising Fast", index=False)
        pd.DataFrame([{"status": "新規", "code": "1001", "name": "サンプルA", "current_rank": 1}]).to_excel(writer, sheet_name="Priority Changes", index=False)
        pd.DataFrame([{"sector_rank": 1, "sector33": "機械", "sector_momentum_score": 70}]).to_excel(writer, sheet_name="Sector Momentum", index=False)
        pd.DataFrame([{"check_name": "overall", "status": "PASS"}]).to_excel(writer, sheet_name="Run Health", index=False)


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        workbook = root / "daily_report.xlsx"
        output = root / "site"
        sample_workbook(workbook)
        site_builder.build_site(workbook, output)
        result = site_experience.apply(output)
        assert result["validation"]["passed"] is True
        assert site_builder.validate_site(output)["passed"] is True
        assert site_experience.validate(output)["passed"] is True

        index = (output / "index.html").read_text(encoding="utf-8")
        assert index.count(site_experience.HEAD_MARKER) == 1
        assert index.count(site_experience.BODY_MARKER) == 1
        script = (output / "assets" / "experience.js").read_text(encoding="utf-8")
        assert "momentum-watchlist-v2" in script
        assert "momentum-compare-v2" in script
        assert "URLSearchParams" in script
        assert "ux-mobile-ranking" in script
        assert "PRIMARY CAUTION" in script
        manifest = json.loads((output / "site_manifest.json").read_text(encoding="utf-8"))
        paths = {entry["path"] for entry in manifest["files"]}
        assert "assets/experience.css" in paths
        assert "assets/experience.js" in paths
        assert manifest["research_only"] is True
        assert manifest["production_state_mutations"] == []
        assert site_experience.EXPERIENCE_VERSION in manifest["site_version"]

        site_experience.apply(output)
        index = (output / "index.html").read_text(encoding="utf-8")
        assert index.count(site_experience.HEAD_MARKER) == 1
        assert index.count(site_experience.BODY_MARKER) == 1
        print("dashboard daily-decision experience validation passed")


if __name__ == "__main__":
    main()
