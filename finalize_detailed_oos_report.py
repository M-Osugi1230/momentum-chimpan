"""Combine modular detailed OOS outputs into one audited Japanese report and manifest."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import detailed_oos_analysis as core

VERSION = "2026-07-22-detailed-oos-final-report-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill-manifest", required=True)
    parser.add_argument("--analysis-manifest", required=True)
    parser.add_argument("--core-dir", required=True)
    parser.add_argument("--path-dir", required=True)
    parser.add_argument("--ablation-dir", required=True)
    parser.add_argument("--robustness-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def percentage(value: object) -> str:
    return "" if pd.isna(value) else f"{float(value):.3%}"


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    core_dir = Path(args.core_dir)
    path_dir = Path(args.path_dir)
    ablation_dir = Path(args.ablation_dir)
    robustness_dir = Path(args.robustness_dir)
    backfill = json.loads(Path(args.backfill_manifest).read_text(encoding="utf-8"))
    analysis = json.loads(Path(args.analysis_manifest).read_text(encoding="utf-8"))
    summary = pd.read_csv(core_dir / "method_summary_by_year.csv")
    rank_ic = pd.read_csv(core_dir / "rank_ic_summary.csv")
    monotonicity = pd.read_csv(core_dir / "rank_monotonicity.csv")
    calibration = pd.read_csv(core_dir / "score_calibration.csv")
    regimes = pd.read_csv(core_dir / "regime_summary.csv")
    lifecycle = pd.read_csv(core_dir / "signal_lifecycle_summary.csv")
    path = pd.read_csv(path_dir / "path_quality_summary.csv")
    ablation = pd.read_csv(ablation_dir / "healthy_v1_ablation_summary.csv")
    baselines = pd.read_csv(ablation_dir / "simple_baseline_summary.csv")
    scorecard = pd.read_csv(robustness_dir / "evidence_scorecard.csv")
    placebo = pd.read_csv(robustness_dir / "random_placebo.csv")
    leave_sector = pd.read_csv(robustness_dir / "leave_one_sector_out.csv")
    production = summary[summary["method"] == "production"][
        ["year", "top_size", "horizon_sessions", "date_weighted_mean_net_return"]
    ].rename(columns={"date_weighted_mean_net_return": "production_return"})
    comparison = summary.merge(
        production,
        on=["year", "top_size", "horizon_sessions"],
        how="left",
    )
    comparison["delta_vs_production"] = (
        comparison["date_weighted_mean_net_return"] - comparison["production_return"]
    )
    comparison.to_csv(output_dir / "method_vs_production_by_year.csv", index=False)
    primary = comparison[
        comparison["top_size"].isin([10, 30])
        & comparison["horizon_sessions"].isin([5, 10, 20])
    ]
    lines = [
        "# Detailed OOS Evidence v2｜2022–2025 多年度検証",
        "",
        "> 調査・シャドー検証専用です。本番ランキング、メール、サイト、ペーパー取引、実注文は変更していません。",
        "",
        "## 検証範囲",
        "",
        f"- 評価期間：{backfill.get('evaluation_start')}〜{backfill.get('evaluation_end')}",
        f"- ランキング日数：{backfill.get('ranking_date_count')}",
        f"- 選定ユニバース：{backfill.get('selected_universe_count')}銘柄",
        f"- ランキング行数：{backfill.get('ranking_row_count'):,}",
        f"- 選定イベント：{analysis.get('selection_event_count'):,}",
        "- エントリー：ランキング翌取引日の調整後始値",
        "- 評価：1・3・5・10・20・40・60営業日後、往復20bp控除",
        "- 現在の上場一覧を使うため、サバイバーシップ・過去構成銘柄バイアスがあります。",
        "",
        "## 年別の主要成績（日時点等金額・コスト控除後）",
        "",
        "| 年 | 手法 | Top | 期間 | 平均 | Production差 | 市場中央値超過 | 勝率 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in primary.sort_values(
        ["year", "horizon_sessions", "top_size", "method"]
    ).itertuples(index=False):
        lines.append(
            f"| {row.year} | {row.method} | {row.top_size} | {row.horizon_sessions}日 | "
            f"{percentage(row.date_weighted_mean_net_return)} | {percentage(row.delta_vs_production)} | "
            f"{percentage(row.mean_market_excess_net)} | {float(row.win_rate):.1%} |"
        )
    lines += [
        "",
        "## Evidence Scorecard",
        "",
        "| 手法 | Top | 期間 | Production超過年数 | 平均差 | Rank IC正の日率 | 業種除外頑健性 | 判定 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in scorecard.sort_values(
        ["method", "top_size", "horizon_sessions"]
    ).itertuples(index=False):
        lines.append(
            f"| {row.method} | {row.top_size} | {row.horizon_sessions}日 | "
            f"{row.years_outperforming_production}/{row.years_available} | "
            f"{percentage(row.mean_delta_vs_production)} | {float(row.mean_positive_rank_ic_rate):.1%} | "
            f"{float(row.leave_one_sector_positive_return_rate):.1%} | "
            f"{'PASS' if row.all_research_gates_pass else 'NOT PASS'} |"
        )
    lines += [
        "",
        "## 順位品質",
        "",
        "正のRank ICは、上位ほどその後の成績が良かったことを示します。",
        "",
        "| 年 | 手法 | 期間 | 平均Rank IC | 正の日率 |",
        "|---:|---|---:|---:|---:|",
    ]
    for row in rank_ic[rank_ic["horizon_sessions"].isin([5, 10, 20])].sort_values(
        ["year", "horizon_sessions", "method"]
    ).itertuples(index=False):
        lines.append(
            f"| {row.year} | {row.method} | {row.horizon_sessions}日 | "
            f"{float(row.mean_rank_ic):.4f} | {float(row.positive_rank_ic_rate):.1%} |"
        )
    focus_ablation = ablation[
        (ablation["top_size"] == 30)
        & ablation["horizon_sessions"].isin([5, 20])
        & ablation["ablation_variant"].ne("ORIGINAL_V1")
    ]
    lines += [
        "",
        "## Healthy v1 条件アブレーション",
        "",
        "各条件を1つだけ外した場合の変化です。プラスでも自動採用はしません。",
        "",
    ]
    if len(focus_ablation):
        grouped = focus_ablation.groupby("ablation_variant")[
            "return_delta_vs_original_v1"
        ].mean().sort_values(ascending=False)
        lines += ["改善方向上位："] + [
            f"- {name}: {value:+.3%}" for name, value in grouped.head(5).items()
        ]
        lines += ["", "悪化方向上位："] + [
            f"- {name}: {value:+.3%}" for name, value in grouped.tail(5).items()
        ]
    lines += [
        "",
        "## 価格経路品質",
        "",
        "| 年 | 手法 | 順位帯 | +5%先着 | −5%先着 | 60日MFE | 60日MAE | 最大終値DD |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in path.sort_values(["year", "method", "rank_band"]).itertuples(index=False):
        lines.append(
            f"| {row.year} | {row.method} | {row.rank_band} | {float(row.up_5_first_rate):.1%} | "
            f"{float(row.down_5_first_rate):.1%} | {percentage(row.mean_mfe_60)} | "
            f"{percentage(row.mean_mae_60)} | {percentage(row.mean_max_close_drawdown_60)} |"
        )
    lines += [
        "",
        "## 単純ベースラインとの比較",
        "",
        "20日騰落率順、5日騰落率順、相対強度順、出来高倍率順、単純複合スコアを別手法として同じ条件で評価しています。",
        f"- ベースライン集計行数：{len(baselines)}",
        f"- ランダム・プラセボ比較行数：{len(placebo)}",
        f"- Leave-One-Sector-Out行数：{len(leave_sector)}",
        "",
        "## 出力済み詳細表",
        "",
        "- 年別・手法別・順位別リターン",
        "- 日次Rank ICとスコア校正",
        "- 市場局面別成績",
        "- 初回選定・改善・悪化・再ランクイン",
        "- +5%／−5%先着、MFE、MAE、最大ドローダウン",
        "- Healthy v1全条件の1条件ずつ除外",
        "- 単純ベースライン、ランダム選定、業種除外",
        "- 事前固定Evidence Scorecard",
        "",
        "## 制約",
        "",
        "- point-in-time上場銘柄一覧ではないため、この結果だけで本番昇格できません。",
        "- 市場ベンチマークはサンプル断面中央値であり、TOPIXそのものではありません。",
        "- 過去結果を見た自動閾値調整は行っていません。",
        "- 本番変更には前向きシャドー結果、別Issue、別PR、手動承認が必要です。",
        "",
    ]
    (output_dir / "detailed_report_ja.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    manifest = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backfill_manifest_sha256": core.sha256_file(args.backfill_manifest),
        "analysis_manifest_sha256": core.sha256_file(args.analysis_manifest),
        "years": sorted(int(value) for value in summary["year"].unique()),
        "summary_rows": len(summary),
        "rank_ic_rows": len(rank_ic),
        "monotonicity_rows": len(monotonicity),
        "calibration_rows": len(calibration),
        "regime_rows": len(regimes),
        "lifecycle_rows": len(lifecycle),
        "path_rows": len(path),
        "ablation_rows": len(ablation),
        "baseline_rows": len(baselines),
        "scorecard_rows": len(scorecard),
        "placebo_rows": len(placebo),
        "leave_one_sector_rows": len(leave_sector),
        "research_only": True,
        "promotion_evidence_allowed": False,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
    }
    (output_dir / "combined_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.strict:
        expected = (
            set(range(2022, 2026))
            if str(backfill.get("evaluation_start", "")).startswith("2022")
            else set(manifest["years"])
        )
        if not expected.issubset(set(manifest["years"])):
            raise RuntimeError(f"missing years: {expected - set(manifest['years'])}")
        if scorecard.empty or path.empty or ablation.empty:
            raise RuntimeError("required final evidence missing")
        if manifest["production_state_mutations"]:
            raise RuntimeError("production state mutated")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
