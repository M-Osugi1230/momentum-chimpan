# Real-data validation notes

The first production workbook (2026-07-13) exposed two presentation issues to close:

1. Data Quality and Daily Action List summary fields are added while generating the workbook, but the same enriched summary must also reach the email renderer.
2. Daily Action List prose is intentionally rich in the workbook; the email needs evidence-aware selection rather than simple character truncation so volume, liquidity, relative strength and the primary caution are not hidden.

This note records the observed production-data contract. Implementation and deterministic regression tests are tracked separately.
