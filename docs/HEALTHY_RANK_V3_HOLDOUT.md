# Healthy Rank v3 Holdout

## Purpose

Detailed OOS Evidence v2 showed that Healthy v1 improved the candidate set but did not reliably order eligible stocks. Healthy Rank v3 tests one pre-registered ordering candidate on an untouched 2018–2021 holdout.

This is a research-only ranking layer. Healthy v1 eligibility, Production rank/score, Daily Action List, email, site, paper positions, and real orders are unchanged.

## Fixed candidate

Healthy Rank v3 ranks only Healthy v1 eligible rows using four equal-weight components:

1. 5-session return cross-sectional percentile: 25%
2. 20-session return cross-sectional percentile: 25%
3. Healthy relative-strength score cross-sectional percentile: 25%
4. MA20 deviation middle preference: 25%

The MA20 component peaks at +4% deviation and declines linearly to zero at 16 percentage points from the peak. Missing any component makes the row ineligible for the v3 rank, but it does not change Healthy v1 eligibility.

The definition is stored in `research/healthy_rank_v3_protocol.yaml` and may not be tuned after viewing holdout results.

## Holdout

- Evaluation: 2018-01-01 through 2021-12-31
- Warm-up download start: 2016-10-01
- Deterministic sector-balanced current-list sample: 1,500 securities
- Ranking snapshots: every five trading sessions
- Entry: next available adjusted open
- Horizons: 1, 3, 5, 10, 20, 40, and 60 sessions
- Costs: 20bp primary, 50bp and 100bp robustness
- Freshness and discontinuity filters: same strict rules as Detailed OOS Evidence v2

## Primary benchmark

Healthy Rank v3 is compared primarily with Healthy v1. Production is a reference benchmark only. Random placebo selections are drawn from the Healthy v1 eligible universe so the test isolates ordering quality rather than eligibility quality.

## Fixed gates

For each Top10/Top30 and 5/10/20-session cell:

- outperform Healthy v1 in at least three of four years;
- outperform after 5% trimming in at least three of four years;
- daily outperformance rate at least 52%;
- positive Rank IC date rate at least 55%;
- positive leave-one-sector delta rate at least 80%;
- positive absolute return after 50bp in at least three years;
- random placebo p <= 0.10 in at least two years.

Passing the historical holdout does not promote the strategy. Live forward confirmation, a separate issue and PR, and manual approval remain mandatory.

## Outputs

- `healthy_rank_v3_rankings.csv`
- `healthy_rank_v3_events.csv`
- `robust_summary_by_year.csv`
- `paired_comparisons_by_year.csv`
- `rank_ic_daily.csv` and `rank_ic_summary.csv`
- `rank_monotonicity.csv`
- `leave_one_sector_out.csv`
- `random_placebo.csv`
- `evidence_scorecard.csv`
- `path_detail.csv` and `path_summary.csv`
- `report_ja.md`
- audited manifests and source hashes
