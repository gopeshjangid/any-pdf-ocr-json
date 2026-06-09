# Source Faithfulness Rules

These rules are **non-negotiable** for all extraction, parsing, validation, and AI-assisted fallback stages. Violations are Release Gatekeeper blockers.

## Core Principle

The JSON output is a **structured representation of the source document**, not an interpretation. Facts, numbers, symbols, option text, answers, and solutions must match the PDF (or extracted markdown faithful to the PDF) unless explicitly marked for human review.

## Strict Rules

### 1. No Paraphrasing

- Do not reword question stems, options, or solutions.
- Do not "simplify" grammar or spelling from the source.
- Do not expand abbreviations not expanded in the source.
- **Allowed:** whitespace normalization only if documented and lossless (e.g., NFC unicode normalization)—must not merge words or change digits.

### 2. No Fact Changes

- Numbers, units, chemical formulas, dates, names, and proper nouns must be preserved exactly.
- Do not correct perceived errors in the source PDF (typos stay as typos).
- Do not substitute synonyms in options.

### 3. No Solving Missing Answers

- If the PDF lacks an answer key, `answer` is `null` and `review_status` is `needs_review`.
- Do not use LLM, solver, or external knowledge to compute answers.
- Do not infer correct option from solution text unless solution explicitly states the answer mapping in source.

### 4. No Removing Meaningful Content

- Do not drop tables, formulas, footnotes, or sub-questions because they are hard to parse.
- Unparsed content must appear in `raw_question_text`, an `unparsed_blocks` field, or validation report—not silently omitted.
- Diagrams and images must retain references even if text extraction is incomplete.

### 5. Preserve Raw Text

- Store verbatim extracted strings in `raw_*` fields.
- Any derived or normalized field must link to `raw_*` source and document transformation in validation report.
- Intermediate markdown from Marker is an extraction artifact, not a license to edit wording.

### 6. Preserve Source Trace

Every question and material field must include trace metadata:

- Source file path (or hash)
- Page number(s)
- Block or offset identifier from extraction layer
- Extraction method (`marker`, `ocr`, `manual`) and version
- Timestamp of extraction run

### 7. Faithful Mapping

- Question ↔ options ↔ answer ↔ solution ↔ images mapping must reflect source layout.
- Solutions-at-end documents: mapping must use explicit rules (e.g., "Answers: 1-B, 2-C"); low confidence → `needs_review`.
- Do not attach solution text to the wrong question to "complete" the record.

### 8. Uncertain Items → Review

- Any field below confidence threshold → `needs_review`, never `approved`.
- Ambiguous question boundaries → split conservatively or mark whole section `needs_review`; do not guess merges.
- Scanned or illegible content → `needs_review` or `rejected` with report reason—no AI guess as fact.

## AI-Specific Rules (Future Fallback)

When AI/OCR/VLM is used:

- AI output is **provisional** until validated against source blocks.
- AI may suggest block boundaries or diagram labels; it may not replace `raw_*` text without review flag.
- AI repair requires `ai_assisted: true` metadata and human-reviewable diff in validation report.

## Validation Enforcement

Pydantic schema validation ensures structure; **faithfulness** requires:

- Rule-based checks: option count, sequence, empty answer policy
- Optional exact-match checks against raw extraction layer
- QA fixtures with known PDFs asserting verbatim text preservation

## Rejection Triggers (Automatic)

Mark output `rejected` or block release if:

- Answer populated when source has no answer key (unless mapped from explicit answer section)
- Question text visibly paraphrased vs extraction layer
- Options missing with no validation report entry
- Source trace missing on any `approved` question
