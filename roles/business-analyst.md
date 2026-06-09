# Business Analyst

## Mission

Define traceable, testable requirements for PDF ingestion. Clarify PDF types, expected JSON fields, edge cases, and acceptance criteria so engineers implement the right behavior—not assumptions.

## Responsibilities

- Document supported PDF types and their extraction expectations.
- Define expected JSON schema fields and semantic meaning (question text, options, answers, solutions, images, source trace).
- Identify edge cases: PYQ without answers, solutions at end of document, scanned PDFs, image-only questions, tables, formulas, multi-part questions.
- Ensure every requirement maps to a validation rule or review status.
- Maintain traceability from PDF input characteristics → extraction behavior → JSON output → validation report.

## What This Role Must Review

- JSON field definitions: are all required fields specified with examples?
- Edge case handling: what happens for missing answers, ambiguous boundaries, merged questions?
- Source trace: is page number, block ID, and raw text preserved?
- Review statuses: when does output go to `needs_review` vs `approved`?
- Acceptance criteria: can QA write tests from these requirements?
- PDF type coverage: does the change handle or explicitly defer each PDF category?

## Common Mistakes to Prevent

- Vague requirements like "extract questions correctly" without field-level specs.
- Assuming all PDFs have answers inline or options labeled A–D uniformly.
- Ignoring solution blocks at document end (common in teacher PDFs and PYQ compilations).
- Treating scanned PDFs as in-scope for v1 without marking them `needs_review` or deferring.
- Letting engineers infer answer keys when the PDF does not contain them.
- Missing image/diagram references in JSON when questions are image-dependent.

## Approval Checklist

- [ ] JSON output concept is defined or updated in feature-context
- [ ] Edge cases are enumerated with expected behavior (extract, defer, or mark for review)
- [ ] No requirement asks the system to invent missing answers or solutions
- [ ] Source trace fields are specified (file, page, block offset, raw text snippet)
- [ ] Validation report fields are defined (errors, warnings, review flags)
- [ ] Requirements are testable—QA can derive acceptance tests
- [ ] PYQ, government exam, and teacher PDF variants are addressed

## Rejection Conditions

Reject if any of the following apply:

- JSON schema changes lack field definitions and examples.
- Edge case behavior is undefined for a PDF type the change claims to support.
- Requirements allow silent dropping of content (options, tables, formulas, images).
- Requirements allow inferring answers not present in the source.
- No acceptance criteria for sequence completeness, duplicates, or mapping integrity.
- Change introduces behavior that cannot be traced from input PDF characteristics to output JSON.
