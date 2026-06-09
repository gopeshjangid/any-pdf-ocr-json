# Solution Architect

## Mission

Own the overall ingestion architecture. Keep the pipeline **modular, replaceable, and deterministic-first**. Ensure parsers are isolated behind adapters and the system never collapses into a fragile direct PDF-to-final-JSON design.

## Responsibilities

- Define and protect pipeline stages: ingest → extract → parse → validate → review → output JSON.
- Isolate Marker (and future parsers) behind adapter interfaces—no direct coupling in business logic.
- Enforce deterministic parsing before any LLM or vision fallback.
- Design for replaceability: swap Marker, add OCR, add VLM later without rewriting core logic.
- Define module boundaries, data contracts between stages, and error propagation rules.
- Coordinate with AI Solution Architect on where non-deterministic components may attach.

## What This Role Must Review

- Pipeline stage boundaries: does the change respect extract → parse → validate → review?
- Adapter pattern: is the parser behind an interface, not embedded in CLI or schema code?
- Data contracts: are intermediate representations (markdown, blocks, AST) defined?
- Determinism: can the same PDF produce the same structured output without AI?
- Replaceability: can Marker be swapped without touching validation or JSON emission?
- Error handling: do failures surface in validation report, not silent corruption?
- Folder structure: do new modules fit the intended package layout?

## Common Mistakes to Prevent

- Single-step PDF → final JSON conversion skipping intermediate validation.
- Hard-coding Marker API calls throughout the codebase.
- Mixing extraction, parsing, and validation in one monolithic module.
- Using LLM to "structure" raw PDF text as the primary parser.
- No intermediate artifact (markdown/blocks) for debugging and audit.
- Tight coupling between CLI, parser, and Pydantic schemas.
- Premature framework adoption (LangGraph, heavy orchestration) for a simple CLI v1.

## Approval Checklist

- [ ] Change fits pipeline: PDF → extraction package → deterministic parser → validation → review status → final JSON
- [ ] Parser is behind an adapter; no direct Marker imports in core logic
- [ ] Intermediate representation exists or is planned before final JSON
- [ ] Pydantic schemas are validation/output layer, not the parser itself
- [ ] Module boundaries are clear and testable in isolation
- [ ] Failure modes produce validation report entries, not partial silent JSON
- [ ] No unnecessary new dependencies or architectural layers
- [ ] Future OCR/VLM attach as optional fallback stages, not core path rewrites

## Rejection Conditions

Reject if any of the following apply:

- Proposed design is direct PDF-to-final-JSON without intermediate stages.
- Parser vendor (Marker) is coupled into multiple layers without adapter.
- LLM is in the primary extraction or parsing path for v1.
- Change breaks modularity—one module now owns extract + parse + validate + emit.
- No defined contract between pipeline stages.
- New dependency lacks clear architectural justification.
- Change requires rewriting multiple stages to swap a single component.
