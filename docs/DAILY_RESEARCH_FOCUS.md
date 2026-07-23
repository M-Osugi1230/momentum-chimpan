# Daily Research Focus

Last updated: 2026-07-23

## Purpose

Daily Research Focus turns the existing Action Priority analysis into a concise plan of **five to ten stocks to research in detail today**.

It is not a buy list. It does not change Momentum scores, ranks, thresholds, Production strategy, paper execution, or live orders.

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

Daily Research Focus uses these existing results rather than introducing another Production scoring strategy.

## Daily buckets

| Bucket | Meaning |
|---|---|
| A | Must research today; capped at five names |
| B | Research if time permits |
| C | Continue monitoring an established candidate |
| Watch | Wait for score, continuity, or data conditions to improve |
| Skip | Low current research priority or unreliable data |

The existing `action_priority` field remains compatible with A/B/C/見送り. The more expressive daily state is stored in `research_bucket`.

## Five-to-ten selection contract

- A candidates: maximum 5;
- Daily Action List: target minimum 5 and maximum 10;
- A and B are selected first;
- excess A candidates are adjusted to B only in the research-plan layer;
- when A/B contains fewer than five names, C/Watch may supplement the list;
- supplemental rows must have complete explanations and cannot be Data Quality D;
- supplemental rows retain their C/Watch bucket and are marked by `daily_action_supplement=true`;
- if fewer than five quality candidates exist, `Daily Action List下限不足` shows the shortfall;
- the system never fills the list with unreliable names merely to reach five.

These are attention-management rules. They do not change Momentum rank, score, Production eligibility, or paper execution.

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
- `caution_reasons_before_daily_focus`;
- `daily_action_supplement`;
- `daily_action_rank`.

A supplemental reason explicitly states that the row was added to approach the five-name research minimum without changing Production rank/score.

## Daily output

### Summary

- A/B/C/Watch/Skip counts;
- Daily Action List count;
- supplemental-candidate count;
- minimum shortfall;
- incomplete-explanation count;
- A-cap violation count, required to be zero.

### Workbook

A `Daily Action List` sheet is inserted near Summary and contains up to ten detailed-research candidates with:

- daily action rank;
- bucket and supplemental flag;
- Momentum rank and score;
- action score;
- Data Quality grade;
- what changed;
- why today;
- risk summary;
- next research questions;
- adjustment reason.

The existing `Action Priority` sheet retains the complete candidate set and additional focus fields.

### Email and dashboard

The concise email shows the highest-priority names and links to the full dashboard. The dashboard and Workbook are the complete source for the five-to-ten Daily Action List, all explanation fields, quality warnings, and supplemental markers.

## Outcome tracking

The focus policy may not be treated as validated because it looks useful in one report.

Before changing classification or supplementation rules based on performance, prospective 5/10/20-session outcome tracking requires:

- exact decision date and source run;
- exact strategy and focus-policy versions;
- A/B/C/Watch/Skip state and supplemental flag where available;
- market and sector benchmark;
- sample size and confidence interval;
- no same-day close entry;
- manual review before any Production or paper-rule change.

## Governance

- display and research planning only;
- score and rank preserved;
- paper execution preserved;
- no automatic score, weight, priority, strategy, or paper-rule changes;
- no live orders;
- shortfalls and small samples remain visible.
