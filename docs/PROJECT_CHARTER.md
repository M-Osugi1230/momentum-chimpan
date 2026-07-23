# Momentum Chimpan Project Charter

Last updated: 2026-07-23

## Mission

Momentum Chimpan reduces the time required after each market close to understand Japanese-equity momentum and select **five to ten companies for detailed research today**.

The product is successful when a user can understand the market state, important changes, the five-to-ten-name research order, and the main cautions in about three minutes, while retaining exact evidence to review whether those priorities were useful 5, 10, and 20 sessions later.

## Primary user job

After the close, answer five questions:

1. Is momentum broad, narrow, accelerating, overheated, or weakening across the Japanese market?
2. Which stocks became newly strong today?
3. Which stocks are accelerating, continuing, resurfacing, or deteriorating?
4. Which five to ten stocks should be researched in detail today?
5. Why today, what changed, what is uncertain, and what must be checked outside the system?

## Daily research contract

- A candidates are the highest research priority and remain capped at five.
- The full Daily Action List targets five to ten companies.
- A/B candidates are selected first.
- When A/B contains fewer than five names, quality-screened C/Watch names may be added as visibly marked supplemental research candidates.
- Data Quality D is never used to fill the list.
- When fewer than five quality candidates exist, the shortfall is shown rather than adding unreliable names.
- Supplemental selection does not change Momentum score, rank, Production strategy, or paper execution.

## Product principles

- Research priority, not trade instruction.
- Explain changes, not only levels.
- Prefer reproducible evidence over retrospective stories.
- Preserve raw history, exact source-run identity, and strategy fingerprints.
- Surface data-quality and sample-size uncertainty.
- Keep the daily conclusion concise; keep full evidence in the dashboard, workbook, and artifacts.
- Reconcile downstream research ledgers when an event-driven publisher misses a run.
- Apply research findings first to explanations, shadows, paper cohorts, and independent holdouts.
- Use prospective evidence before changing production rules.
- Require explicit human approval for every production strategy change.

## Non-goals

Momentum Chimpan does not:

- place live orders;
- perform automatic trading;
- issue personalized buy or sell recommendations;
- manage a user's holdings or tax position;
- generate sell recommendations;
- change score weights, filters, exits, priority rules, or paper rules automatically;
- present historical backtests as guaranteed future returns;
- hide failed, pending, missing-regime, or small-sample observations.

## Current production strategy

The current score remains unchanged:

| Component | Maximum points |
|---|---:|
| Year-to-date high update | 30 |
| Consecutive YTD-high updates | 20 |
| 20-session return | 20 |
| Volume ratio | 15 |
| Above 20- and 60-session moving averages | 10 |
| Trading value | 5 |

The canonical research catalog records:

- volume-ratio production weight: 15 points;
- historical consensus: `CONFLICTED_TIME_UNSTABLE`;
- current decision: `HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE`;
- governing study: `volume-component-forward-evidence-v1`;
- automatic weight and strategy changes: disabled;
- manual review required.

The machine-readable source of truth is `research/evidence_catalog.yaml`.

## Research insight application

Confirmed research tendencies may improve the system only through a governed path.

Allowed before prospective promotion evidence matures:

- clearer explanations and risk context;
- read-only shadow rankings;
- research-only paper cohorts;
- independent holdout and price-path research;
- data-quality corrections that do not optimize on outcomes;
- reconciliation and evidence integrity;
- regime-specific paper validation.

Frozen without a separate registered and approved process:

- score-weight changes;
- new Production score components or exclusions;
- automatic exit changes;
- result-driven evidence-gate changes;
- recent-window cherry-picking;
- automatic Production activation.

## Operational evidence contract

Every successful Daily Momentum Report should be traceable through:

1. the exact daily operations artifact;
2. production audit;
3. recovery-aware Live Session Readiness;
4. Live Session Eligibility and ranking-row identity;
5. 5/10/20-session priority outcomes;
6. Forward Evidence where applicable.

A scheduled reconciliation process must restore missing idempotent ledger rows without changing Production state.

## Paper validation contract

The paper portfolio is research-only and must be evaluated separately for:

- strong;
- mildly strong;
- neutral;
- weak;
- overheated-warning;
- operational WARN;
- operational FAIL/no-new-entry.

A regime with no observations or too few observations is `MISSING` or `ACCUMULATING`, not successful. Paper evidence cannot place live orders or change paper rules automatically.

## Decision rights

| Change type | Required path |
|---|---|
| Documentation only | Reviewed documentation PR |
| Display and research-plan UX | Reviewed PR; score/rank/paper invariants tested |
| Operational reliability | Reviewed PR; exact-artifact, recovery, idempotency, and persistence boundaries tested |
| Research workflow | Pre-registered, read-only or artifact-first research PR |
| Shadow or paper cohort | Research-only PR with explicit non-promotion boundary |
| Score/filter/exit/priority change | Evidence packet, live shadow, manual approval, separate Production PR |
| Emergency rollback | Restore the last sealed compatible state and strategy fingerprint; document incident |

## North-star outcome

A complete system:

- runs reliably every market session;
- selects five to ten names worth researching in detail;
- explains why they matter today;
- displays uncertainty, quality, and shortfalls;
- records exactly what was shown and why;
- self-heals missing research ledgers;
- measures subsequent 5/10/20-session outcomes;
- validates paper behavior across market regimes;
- changes Production strategy only through governed evidence and explicit approval.

## Linked execution issues

- #68: ten-session production audit
- #69: live Forward Evidence publisher verification
- #70: A/B/C/Watch/Skip research-priority experience
- #71: data-quality grades
- #72: 5/10/20-session outcome tracking
- #73: score-optimization freeze
- #74: monthly operations and evidence review
- #76: external-user and monetization readiness
- #77: formal strategy-release criteria
- #78: roadmap execution index
- #122: exact-run eligibility backfill
- #145: Research Insight Ledger
