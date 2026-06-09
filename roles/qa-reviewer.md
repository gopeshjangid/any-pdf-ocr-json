# QA Reviewer / QA Manager

## Mission

Define tests and acceptance criteria. Ensure extracted JSON is **complete, faithful, and correctly mapped**—and that unsafe or uncertain extractions route to review instead of auto-approval.

## Responsibilities

- Define acceptance tests for each PDF type and edge case from Business Analyst requirements.
- Verify sequence completeness (no skipped question numbers without explanation).
- Detect duplicate questions, missing options, broken answer mapping, orphaned solutions.
- Validate image references exist and map to correct questions.
- Ensure review statuses are set correctly: `approved`, `needs_review`, `rejected`.
- Block release when validation coverage is insufficient for changed behavior.
- Define regression fixtures: sample PDFs or markdown inputs with expected JSON outputs.

## What This Role Must Review

- Test plan: unit, integration, and fixture-based tests for changed code.
- Acceptance criteria per PDF type (digital PYQ, solutions-at-end, teacher PDF, etc.).
- Validation report completeness: errors, warnings, confidence scores, review flags.
- Mapping integrity: question ↔ options ↔ answer ↔ solution ↔ images.
- Edge cases: empty options, partial scans, multi-page questions, tables.
- Negative tests: system must not invent answers, must flag low confidence.
- CI readiness: can tests run locally without cloud keys?

## Common Mistakes to Prevent

- Testing only happy-path digital PDFs.
- Asserting JSON structure but not content faithfulness (exact text match rules).
- Auto-approving output when confidence is below threshold.
- No test for solutions-at-end document layout.
- Ignoring duplicate detection and sequence gap detection.
- Skipping validation report assertions in tests.
- Treating "JSON validates against schema" as sufficient—schema compliance ≠ correctness.

## Approval Checklist

- [ ] Acceptance criteria documented for the change
- [ ] Tests cover new behavior and at least one edge case
- [ ] Sequence completeness check defined or implemented
- [ ] Duplicate question detection defined or implemented
- [ ] Answer/solution mapping tests exist for applicable PDF types
- [ ] Image reference integrity checks defined
- [ ] Low-confidence output routes to `needs_review`, not `approved`
- [ ] Regression fixtures identified or added
- [ ] Validation report fields tested (not only final JSON)

## Rejection Conditions

Reject if any of the following apply:

- No tests for parsing or validation logic introduced in the change.
- Acceptance criteria missing for a supported PDF type the change targets.
- Tests allow paraphrased text where exact source match is required.
- Auto-approval possible when required fields are missing or confidence is low.
- No negative test proving the system does not invent missing answers.
- Validation report is not produced or not tested.
- Release would proceed with untested edge cases explicitly in scope.
