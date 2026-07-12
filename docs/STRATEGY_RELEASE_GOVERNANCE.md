# Strategy Release Governance

Last updated: 2026-07-13

## Purpose

Every future production change to score weights, score components, filters, exits, execution, Data Quality rules, or Daily Research Focus rules must pass one governed release lifecycle.

A favorable research result is not permission to edit production behavior. The lifecycle requires registration, disjoint and prospective evidence, shadow operation, explicit approval, a separate production PR, rollback readiness, and post-release audit.

The machine-readable sources are:

- `research/strategy_release_policy.yaml`;
- `research/strategy_release_candidates.yaml`;
- `research/strategy_approvals.yaml`.

The enforcement implementation is `strategy_release_gate.py`.

## Scope

Governed change types:

- `SCORE_WEIGHT`;
- `SCORE_COMPONENT`;
- `FILTER`;
- `EXIT_RULE`;
- `PRIORITY_RULE`;
- `EXECUTION_RULE`;
- `DATA_QUALITY_RULE`.

The release-surface fingerprint covers:

1. strategy-relevant AST from `main.py`;
2. the `market`, `ranking`, and `signals` sections of `config.yaml`;
3. `evaluate_row` and `apply_priority_gate` in `data_quality.py`;
4. `base_bucket` and `attach_daily_focus` in `daily_research_focus.py`;
5. semantic thresholds, grade boundaries, focus limits, bucket mapping, and watch rules from the quality and focus policies.

Presentation-only helpers and documentation-only YAML fields do not change the release-surface fingerprint.

## Lifecycle

### 1. `REGISTERED_RESEARCH`

Before results exist, register:

- unique release ID;
- change type;
- hypothesis;
- expected mechanism;
- primary metric;
- explicit failure conditions;
- acceptance criteria;
- hash of the frozen acceptance criteria;
- current and proposed release-surface fingerprints;
- research PR number.

Registration and criteria freezing must strictly precede the first evidence timestamp.

Post-result gate changes and favorable-subperiod-only evidence are prohibited.

### 2. `EVIDENCE_READY`

The evidence packet must prove:

- discovery and holdout separation;
- prospective or separately registered shadow evidence;
- no lookahead;
- next-available-session adjusted-open execution;
- no same-day close entry;
- transaction-cost sensitivity;
- early and late stability;
- market-regime stability;
- sector or concentration stability;
- adequate sample size;
- confidence intervals;
- multiple-testing control when applicable;
- registered evidence origin;
- exact evidence-status, evidence-packet, and review-packet hashes.

### 3. `SHADOW_RUNNING`

Current and proposed rules run in parallel while production behavior remains unchanged.

The shadow record must bind both exact fingerprints and preserve the start date.

### 4. `READY_FOR_MANUAL_APPROVAL`

At least 20 distinct market sessions are required.

The candidate must also have:

- a signed shadow result;
- zero unresolved shadow incidents;
- all frozen acceptance criteria passed.

### 5. `APPROVED_FOR_PRODUCTION_PR`

A human-authored entry in `research/strategy_approvals.yaml` must match:

- candidate approval ID;
- proposed release-surface fingerprint;
- evidence-status SHA-256;
- review-packet SHA-256;
- decision `APPROVE`;
- scope `MANUAL_REVIEW_ONLY`.

Approval must occur after shadow completion.

A production PR must be different from the research PR and target `main`.

A complete rollback plan is mandatory:

- rollback procedure;
- exact rollback ref;
- named owner;
- explicit trigger conditions;
- automatic rollback disabled.

### 6. `RELEASED`

After the separately approved production PR is merged, record:

- merge commit;
- release timestamp;
- merged PR status;
- automatic activation false.

Merging the production PR is still a human action. The release gate never merges automatically.

### 7. `POST_RELEASE_AUDIT_COMPLETE`

After at least 10 market sessions, record:

- operational incident review;
- performance and risk review;
- signed post-release audit;
- explicit `KEEP` or `ROLLBACK` decision.

### Terminal alternatives

- `REJECTED` — research or review rejected the candidate;
- `ROLLED_BACK` — a released candidate was reverted with an exact rollback commit.

## Pull-request enforcement

`Strategy Release Governance` runs on every PR to `main`.

The workflow calculates the release-surface fingerprint for:

- the PR base commit;
- the proposed PR contents.

When the fingerprints are equal, the strategy release gate passes without requiring a candidate.

When the fingerprints differ, the PR fails unless exactly one candidate satisfies all of the following:

- candidate current fingerprint equals the PR base fingerprint;
- candidate proposed fingerprint equals the PR head fingerprint;
- candidate status is `APPROVED_FOR_PRODUCTION_PR`;
- candidate production PR number equals the actual PR number;
- candidate research PR number is different;
- all evidence, shadow, approval, and rollback checks pass.

An experiment entry with status `proposed`, `running`, or `evidence_ready` is not enough to change production behavior.

## Relationship to existing governance

### Experiment registry

`research/experiment_registry.yaml` registers hypotheses and allows governed research measurement. It does not authorize production release.

### Evidence catalog

`research/evidence_catalog.yaml` decides which evidence has precedence and records the current production decision. It does not authorize automatic changes.

### Review packets

`release_review.py` and `volume_component_forward_review.py` create evidence and manual-review packets. The candidate binds their exact hashes.

### Approval registry

`research/strategy_approvals.yaml` remains human-authored. Its approval is necessary but not sufficient: the candidate must also have completed shadow operation, identify the separate production PR, and include rollback readiness.

## Candidate example structure

A candidate includes the following conceptual sections:

```yaml
release_id: unique-release-id
change_type: SCORE_WEIGHT
status: APPROVED_FOR_PRODUCTION_PR
registered_at_utc: ...
acceptance_criteria_frozen_at_utc: ...
hypothesis: ...
expected_mechanism: ...
primary_metric: ...
failure_conditions: [...]
acceptance_criteria: {...}
acceptance_criteria_sha256: ...
current_strategy_fingerprint: ...
proposed_strategy_fingerprint: ...
research_pr_number: 123
registration:
  gate_changed_after_results: false
  favorable_subperiod_only: false
evidence: {...}
shadow: {...}
approval:
  approval_id: ...
production_pr:
  pr_number: 456
  target_branch: main
rollback: {...}
status_history: [...]
```

The registry is currently empty because no strategy change is approved for release.

## Permanent prohibitions

The policy fixes the following values:

- automatic activation: false;
- automatic merge: false;
- automatic score change: false;
- automatic weight change: false;
- automatic strategy change: false;
- automatic priority-rule change: false;
- live orders: false;
- production-state mutations by the gate: none;
- manual review required: true.

The current volume-ratio weight remains 15 points until a future candidate completes this entire lifecycle and a separate production PR is explicitly merged.

## Artifacts

The read-only workflow produces:

- `strategy_release_gate.json`;
- `strategy_release_gate.md`;
- `pr_gate.json` for pull-request executions;
- validation logs.

These artifacts explain why a PR passed or was blocked. They cannot activate a release.
