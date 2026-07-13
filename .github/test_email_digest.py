from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import email_digest


def main() -> None:
    summary = {
        "実行日": "2026-07-13",
        "株価データ日": "2026-07-13",
        "Market Regime": "強気",
        "Market Regime Score": 93,
        "年初来高値更新": 103,
        "Data Quality A": 68,
        "Data Quality C": 32,
        "Data Quality現行日率": 0.99,
        "Run Health": "PASS",
        "運用P0アラート": 0,
        "運用P1アラート": 0,
        "Forward Evidence": "ACCUMULATING",
        "出来高倍率配点": 15,
    }
    top100 = pd.DataFrame([
        {"code": str(1000 + index), "price_date": "2026-07-13"}
        for index in range(100)
    ])
    focus = pd.DataFrame([
        {
            "code": str(7000 + index),
            "name": f"候補{index}",
            "research_bucket": "A" if index == 0 else "B",
            "daily_action_list": True,
            "daily_action_rank": index + 1,
            "action_score": 90 - index,
            "why_today": "順位と出来高が改善",
            "what_changed": "新規ランクイン",
            "risk_summary": "過熱注意なし",
            "next_research_questions": "最新開示とチャートを確認",
            "data_quality_grade": "A",
            "relative_strength_grade": "S",
        }
        for index in range(8)
    ])
    args = [summary, top100, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()]
    while len(args) <= 24:
        args.append(pd.DataFrame())
    args[8] = pd.DataFrame([{
        "top100_avg_score": 63.14,
        "top100_avg_return_20d": 0.1556,
    }])
    args[23] = {"table": pd.DataFrame([
        {"status": "新規"}, {"status": "継続"}, {"status": "脱落"}
    ])}
    args[24] = {"site": {"url": "https://example.test/dashboard/"}}

    plain = email_digest.build_plain(*args, daily_focus=focus)
    html = email_digest.build_html(*args, daily_focus=focus)

    assert "https://example.test/dashboard/" in plain
    assert "https://example.test/dashboard/" in html
    assert "候補0" in plain and "候補4" in plain
    assert "候補5" not in plain
    assert "Momentum Top30" not in plain
    assert "業種別モメンタム" not in plain
    assert "ペーパーポートフォリオ" not in plain
    assert len(plain) < 6000
    assert len(html) < 18000
    assert "売買推奨ではなく" in plain
    assert "出来高倍率配点 15点据え置き" in plain
    print("concise email digest validation passed")


if __name__ == "__main__":
    main()
