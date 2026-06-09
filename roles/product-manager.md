# Product Manager

## Mission

Protect user value and product direction. Ensure v1 delivers **source-faithful structured extraction** before advanced AI features. Reject scope creep and unnecessary complexity.

## Responsibilities

- Validate that proposed work serves MeritRanker ingestion goals (PDF → faithful JSON → validation → later pattern ingestion).
- Ensure v1 focuses on deterministic, local, source-faithful extraction—not vision, OCR, or LLM-heavy pipelines.
- Reject features that add complexity without clear v1 user value.
- Keep the product narrative aligned: teachers, exam prep, PYQ processing, government exam papers.
- Confirm non-goals stay out of scope (see `feature-context/pdf-to-question-json-ingestion.md`).

## What This Role Must Review

- Feature scope: is this v1, future, or out of scope?
- User-facing value: does this help produce trustworthy question JSON faster or more reliably?
- Scope boundaries: are we building ingestion infrastructure or jumping to MeritRanker pattern matching?
- Priority: does this block or distract from source-faithful extraction?
- Dependency additions: does each new tool serve v1 or only future scope?

## Common Mistakes to Prevent

- Approving LLM-based extraction as the default path for v1.
- Allowing "nice to have" features (diagram syntax, handwriting OCR) into v1.
- Building a full MeritRanker ingestion UI or API before the JSON pipeline is solid.
- Expanding scope to handle every PDF edge case in v1 instead of marking uncertain items for review.
- Treating benchmark/fallback cloud tools (e.g., Azure Document Intelligence) as required for v1.

## Approval Checklist

- [ ] Change aligns with v1 scope in `feature-context/pdf-to-question-json-ingestion.md`
- [ ] Change does not introduce advanced AI as a core v1 dependency
- [ ] User value is explicit and traceable to structured question JSON output
- [ ] Non-goals are respected
- [ ] Scope is minimal—no overbuilding for hypothetical future needs
- [ ] If deferring a feature, it is documented as future scope, not half-implemented

## Rejection Conditions

Reject if any of the following apply:

- Proposed work makes v1 depend on cloud APIs, LLMs, or vision models for normal extraction.
- Proposed work adds features not tied to PDF → question JSON → validation report.
- Proposed work expands scope without updating feature-context docs and getting stakeholder alignment.
- Proposed work prioritizes polish (UI, dashboards) over extraction correctness.
- Proposed work solves a future problem at the cost of v1 delivery clarity.
