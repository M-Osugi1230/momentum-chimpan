# Strategy Release Policy

Last updated: 2026-07-13

## Purpose

This policy governs any future production change to Momentum Chimpan's:

- score weights or score components;
- filters or production eligibility;
- priority rules;
- paper exits or execution assumptions;
- other behavior included in the protected release fingerprint.

Research results never authorize a production change by themselves. A favorable historical result, a completed forward study, or a passing review packet can only advance a candidate to human review.

## Current state

- Execution mode remains `RESEARCH_AND_PAPER_ONLY`.
- Automatic activation is disabled.
- The current production strategy is unchanged.
- The candidate registry is intentionally empty.
- The current volume-ratio weight remains 15 points.
- Existing prospective evidence continues to accumulate under its registered gates.

## Protected release surface

The release fingerprint combines:

1. the canonical strategy fingerprint derived from strategy-relevant nodes in `main.py`;
2. the governed `market`, `ranking`, and `signals` sections of `config.yaml`;
3. the exact content of `research/daily_research_focus_policy.yaml`;
4. the exact content of `research/data_quality_policy.yaml`.

Display-only, documentation-only, and research-only changes remain outside this release surface unless they alter one of those governed files.

## Mandatory process

Every production strategy change must complete these stages in order:

1. **Research PR**
   - register the hypothesis before examining the result;
   - define acceptance and rejection criteria;
   - record the current and proposed release fingerprints;
   - prohibit automatic activation.
2. **Evidence review packet**
   - preserve discovery and disjoint holdout separation;
   - use prospective, shadow, or disjoint holdout evidence;
   - use a no-lookahead execution model;
   - test transaction-cost sensitivity;
   - test early, late, and market-regime stability;
   - show sample size and confidence intervals;
   - bind the packet to a SHA-256.
3. **Shadow comparison**
   - run the proposed behavior without changing production;
   - observe at least 20 market sessions;
   - preserve an artifact and SHA-256;
   - obtain a `PASS` result before human approval.
4. **Manual decision**
   - record an explicit approval or rejection;
   - identify the decision maker and date;
   - bind the decision to the candidate, fingerprints, evidence packet, shadow artifact, and production PR;
   - store the canonical approval in `research/strategy_approvals.yaml`.
5. **Separate production-change PR**
   - must be different from the research PR;
   - must include the approved candidate record;
   - must match the exact proposed release fingerprint;
   - cannot change the preregistered gate after the result is known.
6. **Post-release audit**
   - schedule and retain an audit record;
   - compare actual behavior with the approved candidate;
   - retain a tested rollback path;
   - roll back or require review when the release diverges from its approval.

## Prohibited actions

The gate rejects:

- direct production edits justified only by a research result;
- automatic promotion or automatic strategy activation;
- changing acceptance gates after results are known;
- using only a favorable recent subperiod;
- approval records that are not hash-bound to the evidence and proposed fingerprint;
- a research PR serving as its own production-change PR;
- fewer than 20 shadow market sessions for an approved release;
- same-day close entry in evidence used for approval.

## Canonical files

| File | Purpose |
|---|---|
| `research/strategy_release_policy.yaml` | Machine-readable mandatory policy |
| `research/strategy_release_candidates.yaml` | Candidate lifecycle and evidence bindings |
| `research/strategy_approvals.yaml` | Canonical human approval records |
| `strategy_release_gate.py` | Validator and protected-change gate |
| `.github/workflows/strategy-release-gate.yml` | Read-only CI enforcement |
| `.github/test_strategy_release_gate.py` | Deterministic regression contract |

## Candidate lifecycle

Allowed states:

- `REGISTERED`
- `EVIDENCE_READY`
- `SHADOW_RUNNING`
- `READY_FOR_HUMAN_DECISION`
- `APPROVED`
- `REJECTED`
- `RELEASED`
- `ROLLED_BACK`

Only `APPROVED` or `RELEASED` can authorize a protected strategy diff. The gate requires exactly one matching candidate, and the candidate must be bound to:

- the base release fingerprint;
- the proposed release fingerprint;
- a separate production-change PR;
- an evidence review packet SHA-256;
- a shadow comparison SHA-256;
- an exact canonical approval record.

## Approval record contract

An approval record in `research/strategy_approvals.yaml` must include:

- `approval_id`;
- `candidate_id`;
- explicit `decision: APPROVED`;
- `approved: true`;
- `approved_by` and `approved_at`;
- current and proposed release fingerprints;
- evidence review packet SHA-256;
- shadow comparison SHA-256;
- production-change PR;
- `automatic_activation: false`.

The release candidate's manual-decision section must reference the same approval ID. Any mismatch blocks the production change.

## CI behavior

For every relevant pull request, the workflow:

1. checks out the proposed branch and the exact base commit;
2. validates the policy, candidate registry, and approval registry;
3. calculates base and proposed release fingerprints;
4. allows the PR when the protected surface is unchanged;
5. blocks the PR when the protected surface changes without exactly one valid approval-bound candidate;
6. runs deterministic tests;
7. confirms the workflow did not modify repository files;
8. uploads diagnostics as an Actions artifact.

The workflow has `contents: read` and cannot modify production state, score weights, strategy, priority rules, paper execution, or live orders.

## Example approved candidate

The registry is empty today. A future approved candidate must follow this shape:

```yaml
candidate_id: example-change-v1
change_type: SCORE_WEIGHT
status: APPROVED
registered_at: '2026-08-01'
registered_before_results: true
hypothesis: A bounded change improves prospective research prioritization.
acceptance_criteria:
  - positive excess return
  - confidence interval excludes material harm
research_pr: '123'
production_change_pr: '145'
current_release_fingerprint: <64-char SHA-256>
proposed_release_fingerprint: <64-char SHA-256>
rollback_plan: Revert PR 145 and restore the prior fingerprint.
automatic_activation: false
evidence:
  complete: true
  origin: PROSPECTIVE_LIVE
  discovery_holdout:
    separated: true
    overlap_count: 0
  execution:
    no_lookahead: true
    same_day_close_entry_allowed: false
    entry_model: NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN
  transaction_costs:
    sensitivity_tested: true
  stability:
    early_period_tested: true
    late_period_tested: true
    market_regimes_tested: true
  statistics:
    sample_size: 100
    confidence_interval_available: true
  review_packet:
    artifact: actions://review-packet/123
    sha256: <64-char SHA-256>
shadow_comparison:
  market_sessions: 20
  status: PASS
  artifact: actions://shadow/123
  sha256: <64-char SHA-256>
manual_decision:
  approval_id: approval-example-change-v1
  decision: APPROVED
  decided_by: repository-owner
  decided_at: '2026-09-01'
  record: research/strategy_approvals.yaml#approval-example-change-v1
  record_sha256: <64-char SHA-256>
```

This example is illustrative only and does not authorize any change.
