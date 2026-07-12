# Momentum Chimpan Project Charter

Last updated: 2026-07-12

## Mission

Momentum Chimpan reduces the time required after each market close to understand Japanese-equity momentum and decide which companies deserve deeper research.

The product is successful when a user can understand the market state, important changes, and the day's highest-priority research candidates in about three minutes, while retaining enough evidence to review whether those priorities were useful 5, 10, and 20 sessions later.

## Primary user job

After the close, answer four questions:

1. Is momentum broad, narrow, accelerating, or weakening across the Japanese market?
2. Which stocks became newly strong today?
3. Which stocks are accelerating, continuing, resurfacing, or deteriorating?
4. Which stocks should be researched first, and why today?

## Product principles

- Research priority, not trade instruction.
- Explain changes, not only levels.
- Prefer reproducible evidence over retrospective stories.
- Preserve raw history and exact strategy fingerprints.
- Surface data-quality uncertainty.
- Keep important daily output concise; move detail to the workbook and artifacts.
- Use prospective evidence before changing production rules.
- Require explicit human approval for every production strategy change.

## Non-goals

Momentum Chimpan does not:

- place live orders;
- perform automatic trading;
- issue personalized buy or sell recommendations;
- manage a user's holdings or tax position;
- generate sell candidates;
- change score weights, filters, exits, or strategy rules automatically;
- present historical backtests as guaranteed future returns.

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

The canonical research catalog currently records:

- volume-ratio production weight: 15 points;
- historical consensus: `CONFLICTED_TIME_UNSTABLE`;
- current decision: `HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE`;
- governing study: `volume-component-forward-evidence-v1`;
- automatic weight change: disabled;
- automatic strategy change: disabled;
- manual review required.

The machine-readable source of truth is `research/evidence_catalog.yaml`.

## Current development policy

While prospective volume-component evidence is `ACCUMULATING`, the following work is allowed:

- operational reliability;
- data-quality controls;
- daily-report and email usability;
- research-priority explanations;
- no-lookahead outcome tracking;
- evidence integrity, status publication, and review tooling;
- bug fixes that do not silently alter production strategy.

The following work is frozen unless separately registered and explicitly approved:

- score-weight changes;
- new production score components;
- new production exclusions;
- result-driven changes to evidence gates;
- recent-window cherry-picking;
- automatic strategy activation.

## Decision rights

| Change type | Required path |
|---|---|
| Documentation only | Reviewed documentation PR |
| Display and report UX | Reviewed PR; strategy and state invariants tested |
| Operational reliability | Reviewed PR; recovery and persistence boundaries tested |
| Research workflow | Pre-registered, read-only or artifact-first research PR |
| Score/filter/exit change | Evidence packet, shadow comparison, manual approval, separate production PR |
| Emergency rollback | Restore the last sealed compatible state and strategy fingerprint; document incident |

## North-star outcome

A complete system:

- runs reliably every market session;
- identifies the few names worth researching first;
- explains why they matter today;
- displays uncertainty and data quality;
- records what was shown and why;
- measures subsequent 5/10/20-session outcomes;
- changes strategy only through governed evidence and explicit approval.

## Linked execution issues

- #67: P0 documentation and contracts
- #68: ten-session production audit
- #69: live Forward Evidence publisher verification
- #70: A/B/C/Watch/Skip research-priority experience
- #71: data-quality grades
- #72: 5/10/20-session outcome tracking
- #73: score-optimization freeze
- #74: monthly operations and evidence review
- #75: gated Web dashboard
- #76: gated monetization readiness
- #77: formal strategy-release criteria
- #78: roadmap execution index
