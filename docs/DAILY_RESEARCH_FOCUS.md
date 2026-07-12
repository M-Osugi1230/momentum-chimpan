# Daily Research Focus

Last updated: 2026-07-12

## Purpose

Daily Research Focus turns the existing Action Priority analysis into a concise research plan that can be understood in about three minutes.

It is not a buy list. It does not change Momentum scores, ranks, thresholds, paper execution, or live orders.

The machine-readable source is `research/daily_research_focus_policy.yaml`.

## Existing inputs retained

The system already calculates A/B/C/見送り using:

- Momentum score and rank;
- historical expectancy and evidence count;
- market regime;
- lifecycle status;
- liquidity;
- volume;
- moving-average deviation;
- overheating conditions;
- data-quality restrictions.

Daily Research Focus uses these existing results rather than introducing another scoring strategy.

## Daily buckets

| Bucket | Meaning |
|---|---|
| A | Must research today; capped at five names |
| B | Research if time permits |
| C | Continue monitoring an established candidate |
| Watch | Wait for score, continuity, or data conditions to improve |
| Skip | Low current research priority |

The existing `action_priority` field remains compatible with A/B/C/見送り. The more expressive daily state is stored in `research_bucket`.

## Limits

- A candidates: maximum 5;
- Daily Action List: maximum 10;
- the action list contains only A and B;
- excess A candidates are downgraded to B for the daily research plan;
- Data Quality C/D restrictions are applied before the A cap.

These are attention-management limits, not changes to the underlying Momentum rank.

## Required explanation fields

Every row receives:

### `why_today`

Combines the most relevant current evidence, including:

- new Top100 entry;
- rank rise or rapid rise;
- best-rank update;
- Top30 streak;
- lifecycle;
- Momentum rank and score;
- relative-strength grade and rank;
- sector-relative return;
- existing positive action-priority reasons.

### `what_changed`

Summarizes the change from the previous run. If no major change exists, it explicitly states that the stock remains under continued monitoring.

### `risk_summary`

Combines:

- Data Quality grade and warnings;
- existing caution reasons;
- liquidity check;
- overheating check.

### `next_research_questions`

Provides a deterministic checklist, such as:

- latest earnings, revisions, and timely disclosure;
- sustainability of the catalyst;
- chart, gaps, moving-average deviation, and recent highs;
- direct cause of a new entry or rapid rise;
- persistence of a volume spike;
- lifecycle confirmation;
- verification of data or corporate-action warnings.

## Adjustment audit

The output retains:

- `action_priority_before_daily_focus`;
- `focus_adjustment_reason`;
- `positive_reasons_before_daily_focus`;
- `caution_reasons_before_daily_focus`.

The final human-facing reasons are placed in `positive_reasons` and `caution_reasons` so the existing email Action Priority section also becomes more explanatory.

## Daily output

### Summary

- A/B/C/Watch/Skip counts;
- Daily Action List count;
- incomplete-explanation count;
- A-cap violation count, required to be zero.

### Workbook

A `Daily Action List` sheet is inserted near Summary and contains the maximum ten A/B candidates with:

- daily action rank;
- bucket;
- Momentum rank and score;
- action score;
- Data Quality grade;
- what changed;
- why today;
- risk summary;
- next research questions;
- adjustment reason.

The existing `Action Priority` sheet retains the complete candidate set and the additional focus fields.

### Email

A new `今日の結論・Daily Action List` card appears before Market Temperature. It displays up to ten candidates and all four explanation fields.

## Outcome tracking

The focus policy may not be treated as validated because it looks useful in one report.

Before changing classification rules based on performance, Issue #72 requires prospective 5/10/20-session outcome tracking with:

- exact decision date;
- exact strategy and focus-policy versions;
- A/B/C/Watch/Skip state;
- market and sector benchmark;
- sample size and confidence interval;
- no same-day close entry;
- manual review before any rule change.

## Governance

- display and research planning only;
- score and rank preserved;
- paper execution preserved;
- no new persisted production state in this version;
- no automatic score, weight, or strategy changes;
- no live orders.
