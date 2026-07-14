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


def production_focus() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "code": "7453",
            "name": "良品計画",
            "research_bucket": "B",
            "daily_action_list": True,
            "daily_action_rank": 1,
            "action_score": 72,
            "why_today": "Top100新規ランクイン / 前回比+2409位 / 自己最高順位を更新 / 重点候補ライフサイクル: 初登場 / Momentum #13 / Momentum 75点 / 相対強度S・全体155位 / 小売業中央値比20日+14.9% / 期待値35.2点 / 信頼度 高 / Momentum上位30位 / 売買代金50億円以上 / 出来高4.1倍 / 初動 / 大型資金 / 強気相場の初動・加速候補",
            "what_changed": "Top100新規ランクイン / 前回比+2409位 / 自己最高順位を更新 / 重点候補ライフサイクル: 初登場",
            "risk_summary": "Data Quality A / 20日線乖離17.1% / 流動性十分（売買代金50億円以上） / 過熱注意なし",
            "lifecycle_status": "初登場",
            "is_new_entry": True,
            "data_quality_grade": "A",
            "relative_strength_grade": "S",
        },
        {
            "code": "3436",
            "name": "ＳＵＭＣＯ",
            "research_bucket": "B",
            "daily_action_list": True,
            "daily_action_rank": 2,
            "action_score": 65,
            "why_today": "Top100新規ランクイン / 前回比+97位 / 重点候補ライフサイクル: 再浮上 / Momentum #22 / Momentum 70点 / 相対強度S・全体23位 / 金属製品中央値比20日+17.4% / 売買代金50億円以上 / 出来高2.5倍 / 初動 / 大型資金",
            "what_changed": "Top100新規ランクイン / 前回比+97位 / 重点候補ライフサイクル: 再浮上",
            "risk_summary": "Data Quality A / 20日線乖離17.1% / 流動性十分（売買代金50億円以上） / 過熱注意なし",
            "lifecycle_status": "再浮上",
            "is_new_entry": True,
            "data_quality_grade": "A",
            "relative_strength_grade": "S",
        },
        {
            "code": "6136",
            "name": "オーエスジー",
            "research_bucket": "B",
            "daily_action_list": True,
            "daily_action_rank": 3,
            "action_score": 65,
            "why_today": "Top100新規ランクイン / 前回比+334位 / 重点候補ライフサイクル: 再浮上 / Momentum #23 / Momentum 70点 / 相対強度S・全体52位 / 機械中央値比20日+14.5% / 売買代金50億円以上 / 出来高2.4倍 / 初動 / 大型資金",
            "what_changed": "Top100新規ランクイン / 前回比+334位 / 重点候補ライフサイクル: 再浮上",
            "risk_summary": "Data Quality A / 流動性十分（売買代金50億円以上） / 過熱注意なし",
            "lifecycle_status": "再浮上",
            "is_new_entry": True,
            "data_quality_grade": "A",
            "relative_strength_grade": "S",
        },
    ])


def main() -> None:
    summary = {
        "実行日": "2026-07-13",
        "株価データ日": "2026-07-13",
        "市場データ鮮度": "FRESH",
        "状態更新実行": "YES",
        "Market Regime": "強気",
        "Market Regime Score": 93,
        "Market Regime転換": "強気 → 強気",
        "Market Regime転換種別": "維持",
        "Market Regime Score前回比": 4,
        "Market Regime継続日数": 2,
        "年初来高値更新": 103,
        "Data Quality A": 68,
        "Data Quality B": 0,
        "Data Quality C": 32,
        "Data Quality D": 0,
        "Data Quality現行日率": 0.99,
        "Daily Action List": 3,
        "Run Health": "PASS",
        "運用P0アラート": 0,
        "運用P1アラート": 0,
        "Forward Evidence": "ACCUMULATING",
        "出来高倍率配点": 15,
        "Top100 過熱銘柄数": 12,
    }
    focus = production_focus()
    args = build_args(summary)
    plain = email_digest.build_plain(*args, daily_focus=focus)
    html = email_digest.build_html(*args, daily_focus=focus)

    assert email_digest.subject(summary, focus) == "【モメンタムチンパン】2026-07-13 強気93｜調査3件"
    assert "約90秒" in plain
    assert "強気 → 強気・前回比 +4点" in plain
    assert "最大の注意: 良品計画: 20日線乖離17.1%" in plain
    assert "相対強度S・全体155位・出来高4.1倍・売買代金50億円以上・初動" in plain
    assert "Top100新規ランクイン・前回比+2409位・自己最高順位を更新" in plain
    assert "Data Quality A・" not in plain
    assert "流動性十分" not in plain
    assert "過熱注意なし" not in plain
    assert "再浮上のため、翌日以降の出来高と順位継続を確認" in plain
    assert "品質 A 68 / B 0 / C 32 / D 0" in plain
    assert "https://example.test/dashboard/?code=7453#ranking" in plain
    assert "https://example.test/dashboard/?code=7453#ranking" in html
    assert "良品計画" in plain and "オーエスジー" in plain
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
    assert "鮮度または状態更新に問題" in stale_plain
    assert email_digest.subject(stale, focus) == "【モメンタムチンパン】2026-07-13 要確認｜調査3件"
    assert "#fef2f2" in stale_html

    # Governed freshness takes precedence over a calendar-date mismatch, which
    # prevents a false stale warning on a holiday/weekend manual rendering.
    non_trading_day = dict(summary)
    non_trading_day.update({"実行日": "2026-07-14", "株価データ日": "2026-07-13"})
    non_trading_plain = email_digest.build_plain(
        *build_args(non_trading_day), daily_focus=focus
    )
    assert "【本日の要点】" in non_trading_plain
    assert "【要確認】" not in non_trading_plain
    print("production-shaped decision email validation passed")


if __name__ == "__main__":
    main()
