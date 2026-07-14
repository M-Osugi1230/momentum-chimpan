from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import email_digest


def build_args(summary: dict) -> list:
    top100 = pd.DataFrame([
        {"code": str(1000 + index), "price_date": summary.get("株価データ日", "2026-07-13")}
        for index in range(100)
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
    return args


def main() -> None:
    summary = {
        "実行日": "2026-07-13",
        "株価データ日": "2026-07-13",
        "市場データ鮮度": "FRESH",
        "状態更新実行": "YES",
        "Market Regime": "強気",
        "Market Regime Score": 93,
        "Market Regime転換": "やや強気 → 強気",
        "Market Regime転換種別": "改善",
        "Market Regime Score前回比": 5,
        "Market Regime継続日数": 1,
        "年初来高値更新": 103,
        "Data Quality A": 68,
        "Data Quality B": 0,
        "Data Quality C": 32,
        "Data Quality D": 0,
        "Data Quality現行日率": 0.99,
        "Run Health": "PASS",
        "運用P0アラート": 0,
        "運用P1アラート": 0,
        "Forward Evidence": "ACCUMULATING",
        "出来高倍率配点": 15,
        "Top100 過熱銘柄数": 3,
    }
    focus = pd.DataFrame([
        {
            "code": str(7000 + index),
            "name": f"候補{index}",
            "research_bucket": "A" if index == 0 else "B",
            "daily_action_list": True,
            "daily_action_rank": index + 1,
            "action_score": 90 - index,
            "why_today": "順位と出来高が改善 / 市場と同業を上回る相対強度 / 売買代金も十分",
            "what_changed": "新規ランクイン",
            "risk_summary": "20日線からの乖離を確認",
            "next_research_questions": "最新開示とチャートを確認",
            "data_quality_grade": "A",
            "relative_strength_grade": "S",
        }
        for index in range(8)
    ])
    args = build_args(summary)
    plain = email_digest.build_plain(*args, daily_focus=focus)
    html = email_digest.build_html(*args, daily_focus=focus)

    assert "約90秒" in plain
    assert "やや強気 → 強気・改善・前回比 +5点" in plain
    assert "最大の注意" in plain
    assert "https://example.test/dashboard/?code=7000#ranking" in plain
    assert "https://example.test/dashboard/?code=7000#ranking" in html
    assert "候補0" in plain and "候補4" in plain
    assert "候補5" not in plain
    assert "Momentum Top30" not in plain
    assert "業種別モメンタム" not in plain
    assert "ペーパーポートフォリオ" not in plain
    assert len(plain) < 7000
    assert len(html) < 19000
    assert "売買推奨ではなく" in plain
    assert "出来高倍率配点は15点のまま" in html
    assert email_digest.resolve_site_url() == "https://momentum-chimpan.osugimurata.chatgpt.site/"

    stale = dict(summary)
    stale.update({
        "株価データ日": "2026-07-10",
        "市場データ鮮度": "STALE",
        "状態更新実行": "NO",
        "運用P0アラート": 1,
    })
    stale_plain = email_digest.build_plain(*build_args(stale), daily_focus=focus)
    stale_html = email_digest.build_html(*build_args(stale), daily_focus=focus)
    assert "【要確認】" in stale_plain
    assert "株価データが当日基準を満たしていません" in stale_plain
    assert "#fef2f2" in stale_html
    print("decision-first email digest validation passed")


if __name__ == "__main__":
    main()
