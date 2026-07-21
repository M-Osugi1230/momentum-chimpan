# Detailed OOS Evidence v2

## Purpose

This research layer tests Production, Healthy Momentum v1, and Balanced v2 across multiple historical years without changing any live ranking or trading state.

The first execution stage covers 2022–2025 with a deterministic sector-balanced sample of 1,500 currently listed JPX securities and rankings every five trading sessions. The final target remains a point-in-time full-universe daily study, which requires a historical membership and delisting data source.

## Pre-registered primary questions

1. Does Healthy v1 outperform Production in Top10 and Top30 at 5, 10, and 20 sessions in at least three of four years?
2. Do higher ranks have positive cross-sectional Rank IC and broadly monotonic rank-band outcomes?
3. Does the result survive removal of any one sector and comparison with random matched-count portfolios?
4. Does Balanced v2 independently improve Healthy v1 rather than only Production?
5. Are signals more useful over 5–20 sessions than over one session?

The protocol is stored in `research/detailed_oos_protocol.yaml`. Historical results cannot rewrite that protocol automatically.

## Modular evidence jobs

- `run_detailed_historical_oos.py`: produces 1/3/5/10/20/40/60-session outcomes.
- `detailed_oos_analysis.py`: year summaries, Rank IC, rank monotonicity, score calibration, regimes, and lifecycle.
- `detailed_path_quality.py`: +5%/-5% first touch, MFE, MAE, time to extremes, and close-path drawdown.
- `detailed_ablation_baseline.py`: removes one Healthy v1 exclusion at a time and compares simple baselines.
- `detailed_robustness.py`: leave-one-sector-out, deterministic random placebo, and the fixed scorecard.
- `finalize_detailed_oos_report.py`: combines all modules into a Japanese report and audited manifest.

Each heavy module runs in a separate Python process to keep memory bounded on large historical panels.

## Key outputs

- `method_summary_by_year.csv`
- `rank_ic_daily.csv` / `rank_ic_summary.csv`
- `rank_monotonicity.csv`
- `score_calibration.csv`
- `regime_summary.csv`
- `signal_lifecycle_detail.csv` / `signal_lifecycle_summary.csv`
- `path_quality_detail.csv` / `path_quality_summary.csv`
- `healthy_v1_ablation_summary.csv`
- `simple_baseline_summary.csv`
- `leave_one_sector_out.csv`
- `random_placebo.csv`
- `evidence_scorecard.csv`
- `detailed_report_ja.md`

## Safety and interpretation

- All workflows use `contents: read` only.
- Production rank/score, Daily Action List, email, site, paper positions, and real orders are unchanged.
- Historical backfills are permanently non-promotable.
- Current-list reconstruction has survivorship, delisting, and historical-membership bias.
- Cross-sectional sample medians are not TOPIX or official sector indices.
- Any strategy change requires live forward evidence, a separate issue and PR, and manual approval.
