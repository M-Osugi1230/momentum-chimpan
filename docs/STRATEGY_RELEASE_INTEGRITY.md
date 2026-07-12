# Strategy Release Integrity

Last updated: 2026-07-13

## Purpose

`strategy_release_gate.py` governs evidence, shadow operation, approval, production PRs, rollback readiness, and post-release audit. `strategy_release_integrity.py` adds an independent append-only boundary around the governance sources themselves.

This prevents a later pull request from weakening the release-surface definition, rewriting preregistered criteria after results are known, replacing a human approval, or introducing an approving candidate in the same PR that changes production behavior.

## Pinned policy contract

The integrity check fixes the critical release surface to:

- strategy-relevant `main.py` AST;
- `market`, `ranking`, and `signals` configuration;
- Data Quality `evaluate_row` and `apply_priority_gate` functions;
- Daily Research Focus `base_bucket` and `attach_daily_focus` functions;
- semantic Data Quality thresholds, grades, and priority boundary;
- semantic Daily Research Focus limits, bucket mapping, watch rules, and score/rank/paper preservation flags.

The policy cannot reduce the 20-session shadow requirement, 10-session post-release audit, next-session adjusted-open execution model, no-lookahead requirements, manual approval, or automatic-change prohibitions.

## Candidate registry integrity

For an existing `release_id`, these registration fields are immutable:

- registration timestamp;
- change type;
- hypothesis and expected mechanism;
- primary metric;
- acceptance-criteria hash and contents;
- failure conditions;
- current and proposed fingerprints;
- research PR number;
- registration flags.

`status_history` is append-only. Candidates cannot be deleted; they must move to a terminal status such as `REJECTED`, `POST_RELEASE_AUDIT_COMPLETE`, or `ROLLED_BACK`.

A PR that changes the protected release surface cannot introduce its authorizing candidate for the first time. The candidate must already exist in the base branch from the prior research and evidence process.

## Approval registry integrity

Approval IDs must be unique. Existing approval records are append-only, immutable, and non-deletable. The approval policy must continue to require:

- no automatic activation;
- exact strategy fingerprint;
- exact evidence-status hash;
- exact review-packet hash;
- `MANUAL_REVIEW_ONLY` scope.

## Git object identifiers

GitHub currently uses 40-character object IDs in this repository, while future repositories may use 64-character IDs. Released and rolled-back candidates therefore record both:

- `merge_commit_oid` or `rollback_commit_oid`: the actual 40- or 64-character Git object ID;
- the existing 64-character `merge_commit_sha` or `rollback_commit_sha`: `SHA-256(lowercase object ID)`.

The integrity check verifies the binding. This preserves the existing release-gate SHA-256 field while removing ambiguity about the actual Git commit identifier.

## Workflow boundary

`Strategy Release Integrity` runs on every pull request to `main` and on relevant pushes. It has `contents: read`, writes only temporary diagnostics and Actions artifacts, and cannot activate, merge, trade, or mutate production state.

The current strategy, score weights, filters, exits, priority rules, paper execution, and 15-point volume-ratio weight remain unchanged.

## Trusted base-branch enforcement

A normal pull-request workflow executes code from the proposed revision, so it cannot by itself prove that its own validator was not weakened in the same PR. After this integrity layer is merged, `Trusted Strategy Release Integrity` runs on `pull_request_target` and executes only the validator and dependencies from the trusted base commit.

The proposed checkout is treated strictly as untrusted data:

- it is placed in a separate directory;
- no proposed Python, shell, action, or dependency file is executed;
- credentials are not persisted in either checkout;
- permissions remain `contents: read`;
- symlinked proposed files are rejected by the trusted reader;
- outputs are diagnostics only.

Repository branch protection should require the trusted check before merging changes to `main`. The normal pull-request integrity workflow remains useful for immediate feedback, while the trusted check is the authoritative self-protection boundary.
