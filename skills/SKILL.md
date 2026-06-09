---
name: meritranker-governance
description: >-
  Mandatory governance for MeritRanker data ingestion. Use before any code change,
  dependency addition, architecture change, or feature implementation. Enforces
  role review, source-faithfulness rules, and documentation updates.
---

# MeritRanker Data Ingestion — Project Governance

This repository uses a **role-based review system**. No implementation work may proceed until all applicable roles have reviewed the proposed change.

## When to Apply This Skill

Apply this skill **before**:

- Writing or modifying Python code
- Adding dependencies (`pyproject.toml`, `uv.lock`, etc.)
- Changing folder structure or pipeline contracts
- Implementing PDF parsing, validation, or JSON output logic
- Introducing LLM, OCR, or vision components
- Merging or releasing any feature

## Mandatory Read Order (Before Any Code Change)

1. `docs/project-context.md`
2. `docs/current-architecture.md`
3. `docs/implementation-decisions.md`
4. `docs/next-agent-handoff.md`
5. All files in `roles/`
6. All files in `feature-context/`
7. `feature-context/review-and-approval-workflow.md` (execute this workflow)

## Core Rules (Non-Negotiable)

1. **Source faithfulness** — Do not paraphrase, summarize, infer, or alter facts from source PDFs. See `feature-context/source-faithfulness-rules.md`.
2. **No direct PDF-to-final-JSON** — Pipeline must be modular: extract → parse → validate → review → output.
3. **Local-first** — Prefer local processing (Marker for v1). Cloud/AI only as optional fallback, not default.
4. **No LLM for normal extraction** — Deterministic parsing first; AI only for low-confidence repair or vision tasks (future).
5. **Documentation after every change** — Update `docs/change-log.md` and `docs/next-agent-handoff.md`.
6. **Architecture stability** — Do not change stack or design without Architecture Gatekeeper approval.

## Role Review Workflow

For every proposed change, walk through roles in this order:

| Order | Role | File |
|-------|------|------|
| 1 | Product Manager | `roles/product-manager.md` |
| 2 | Business Analyst | `roles/business-analyst.md` |
| 3 | Solution Architect | `roles/solution-architect.md` |
| 4 | AI Solution Architect | `roles/ai-solution-architect.md` |
| 5 | Senior Python Engineer | `roles/senior-python-engineer.md` |
| 6 | QA Reviewer | `roles/qa-reviewer.md` |
| 7 | Security Reviewer | `roles/security-reviewer.md` |
| 8 | Performance-Cost Reviewer | `roles/performance-cost-reviewer.md` |
| 9 | Documentation Mentor | `roles/documentation-mentor.md` |
| 10 | Architecture Gatekeeper | `roles/architecture-gatekeeper.md` |
| 11 | Release Gatekeeper | `roles/release-gatekeeper.md` |

Each role must explicitly pass or reject. Cross-check concerns raised by earlier roles.

## Agent Output Template (Pre-Implementation)

Before writing code, produce a **Role Review Summary** containing:

```markdown
## Proposed Change
<one-paragraph description>

## Role Review Summary
| Role | Status | Notes |
|------|--------|-------|
| Product Manager | PASS/REJECT | ... |
| ... | ... | ... |

## Confidence
<percentage> — Release Gatekeeper decision

## Blockers (if any)
- [Role]: <exact blocker>

## Docs to Update After Implementation
- docs/change-log.md
- docs/next-agent-handoff.md
- <other docs if architecture or decisions change>
```

If Release Gatekeeper confidence is below 99%, **stop and resolve blockers**. Do not implement.

## Documentation Update Workflow

After every approved implementation:

1. Update `docs/change-log.md` with date, author/agent, summary, and files touched.
2. Update `docs/next-agent-handoff.md` with: what changed, what remains, risks, next step.
3. Update `docs/implementation-decisions.md` if a new architectural or product decision was made.
4. Update `docs/current-architecture.md` if pipeline structure changed.
5. Documentation Mentor must verify all updates before Release Gatekeeper final sign-off.

## What Not to Do

- Do not implement PDF ingestion in this governance setup phase unless explicitly tasked.
- Do not add dependencies without Solution Architect + Architecture Gatekeeper approval.
- Do not skip role review because the change "seems small."
- Do not use LLM to generate or rewrite question content from PDFs.



# MeritRanker PDF Question Extraction — Requirement Context

## Core Goal

I want a fully automated, reliable pipeline that takes **any exam/question paper PDF** and exports a clean, source-faithful **questions JSON file**.

The pipeline must end at:

```text
PDF → final questions JSON
```

The output JSON will later be used by another separate pipeline. This PDF pipeline must not do pattern classification, pattern ingestion, database writes, or question generation.

The main output should be:

```text
output/extraction_package/final-questions/final-questions.json
```

## What I Want Extracted

For every question found in the PDF, extract and preserve:

```text
question text
all options
correct answer key, if source-backed
correct answer text, if source-backed
solution/explanation, if available and source-backed
chosen option, if present, as metadata only
visual/image references, if used in question/options
source trace / page / line / OCR / image evidence
quality status
review/blocking issues
```

The pipeline must not silently skip important questions. If a question is detected but incomplete, it should still appear in final JSON with a proper status.

## Supported PDF Types

The pipeline should eventually handle:

```text
clean digital text PDFs
two-column PDFs
Hindi/English mixed PDFs
scanned PDFs
screenshot/webpage-style PDFs
question papers with answer key at end
question papers with inline answers
question papers with separate solutions
response-sheet PDFs with Chosen Option
visual/image-based questions
visual/image-based options
tables and mixed layouts
PDFs where Marker extraction misses text but OCR can read it
```

## Main Requirement

The pipeline must dynamically understand the PDF structure and use the right extraction strategy.

It should not depend on one fixed PDF layout, one fixed answer style, or one fixed option format.

For every PDF, the system should determine:

```text
Is this a text PDF, scanned PDF, image-heavy PDF, or mixed PDF?
Are questions available as text?
Are options available as text or only in images?
Are options A/B/C/D, 1/2/3/4, table-based, inline, or mixed?
Is the correct answer available from answer key, inline answer, solution, visual tick, or unavailable?
Are solutions available separately?
Does OCR need to run?
Is VLM needed later for image/tick interpretation?
Can the question be accepted safely or should it be review/visual/answer-unavailable?
```

## Accuracy Requirement

The goal is **high source-faithful accuracy**, not fake 100% acceptance.

I want the final JSON to be as complete as possible, but correctness is more important than accepted count.

The pipeline must never:

```text
guess missing options
guess correct answers
treat Chosen Option as correct answer by default
rewrite question meaning
silently drop questions
silently accept weak/hallucinated extraction
mix options from one question into another
reuse wrong source lines across questions
```

If something is uncertain, preserve it in JSON with the correct status.

## Quality Statuses

Every final question must have a clear status:

```text
accepted_safe
review_required
visual_required
answer_unavailable
blocked
```

Meaning:

```text
accepted_safe:
Question, options, answer, and source trace are reliable and directly usable.

review_required:
Question is extracted but some part needs human/OCR/VLM/manual check.

visual_required:
Question/options/answer depend on image, diagram, green tick, figure, or visual understanding.

answer_unavailable:
Question/options are extracted, but correct answer is not source-backed.

blocked:
Extraction is unsafe, corrupt, hallucinated, unsupported, or not trustworthy.
```

All statuses should remain in the final JSON. Do not export only accepted-safe items unless explicitly requested.

## Chosen Option Rule

`Chosen Option` is not the correct answer.

It is response metadata only.

It must be stored separately as:

```text
chosen_option_key
chosen_option_canonical_key
chosen_option_source
```

It must not be copied into:

```text
correct_answer_key
correct_answer_text
```

unless a future explicit VLM/manual verification stage confirms it.

## Correct Answer Rule

Correct answer can be filled only when source-backed from:

```text
answer key table
inline answer
official solution
separate solution section
verified visual tick / future VLM stage
manual review patch
```

If not source-backed:

```text
correct_answer_key = null
correct_answer_text = null
answer_source = unavailable
quality_status = answer_unavailable or visual_required
```

## OCR Requirement

Marker alone is not enough.

For screenshot, scanned, printed webpage-style, Hindi/English mixed, or image-heavy PDFs, the pipeline must use OCR.

OCR should enrich evidence, not replace Marker.

Evidence should come from:

```text
Marker text
Azure Document Intelligence OCR/layout
optional local OCR fallback
rendered page images
merged evidence with line/page/bounding-box trace
```

If OCR is explicitly requested and fails, the pipeline should fail early before LLM binding unless fallback is explicitly allowed.

If OCR is unavailable in auto mode, it may continue with warning, but the final JSON must show that evidence is weaker.

## Visual/VLM Requirement

VLM is not the first step, but the pipeline must be designed to support it later.

OCR should first recover readable text/options.

VLM should later handle:

```text
green tick/cross answer detection
image-only options
diagram-based questions
mirror/image/figure reasoning
visual answer choices
```

Until VLM is implemented, such items should remain in JSON as:

```text
visual_required
```

## Option Handling Requirement

Options may appear as:

```text
A/B/C/D
(a)/(b)/(c)/(d)
1/2/3/4
Ans 1 / Ans 2 / Ans 3 / Ans 4
inline options
table-cell options
multi-line options
image options
Hindi/English mixed options
```

Numeric options must be normalized without losing original form.

Example:

```json
{
  "key": "1",
  "key_raw": "1.",
  "canonical_key": "A",
  "option_index": 1,
  "text_raw": "कनाटक"
}
```

The pipeline must not assume only A/B/C/D options.

## Source Trace Requirement

Every extracted field should keep source trace where possible:

```text
page number
line id
source engine: marker / azure_ocr / paddle_ocr / merged / visual / manual
bounding box if available
asset/image reference if available
confidence
```

Source trace is mandatory for trust and debugging.

## Local Binding Requirement

Question/option binding must be local.

The pipeline must build question windows and bind options only inside the correct question window.

It must never reuse option lines from another question.

Bad example:

```text
Q5 using Q1 option lines
Q10 using Q1 source spans
global option search filling unrelated questions
```

This must be prevented.

## Final JSON Requirement

The final JSON must include all detected questions where possible.

Top-level should include:

```text
source_file_name
created_at
total_questions_detected
accepted_safe_count
review_required_count
visual_required_count
answer_unavailable_count
blocked_count
items
warnings
errors
extraction summary
OCR summary
quality summary
```

Each question item should include:

```text
final_question_id
global_order
source_question_number_raw
question_text_raw
options
correct_answer_key
correct_answer_text
answer_source
chosen_option_key
chosen_option_canonical_key
chosen_option_source
solution_text_raw
solution_source
visual_assets
source_trace
quality_status
final_gate_status
confidence
issues
reviewer_notes
```

## Automation Expectation

I want to process many PDFs with minimal manual work.

The pipeline should:

```text
run with one main command
create one organized output folder
produce all required diagnostic artifacts
fail early with clear errors when required tools are missing
avoid wasting LLM calls when OCR/extraction is not ready
never produce misleading success
clearly separate accepted, review, visual, answer-unavailable, and blocked items
```

The goal is not endless PDF-specific fixing. The design should be modular and adaptive.

## Error Handling Requirement

The pipeline should never crash with raw tracebacks for normal expected failures.

It should produce clear errors such as:

```text
marker_missing
azure_ocr_failed
ocr_produced_zero_lines
unsupported_content
llm_provider_failed
unsupported_layout
source_span_missing
cross_window_option_span_reuse
answer_unavailable
visual_required
```

Errors must be actionable.

## Output Folder Requirement

All outputs must stay under the selected output folder.

No confusing duplicate output roots like random `runs/`, `smoke/`, or multiple output directories unless explicitly requested.

Expected structure:

```text
output/
└── extraction_package/
    ├── source/
    ├── marker/
    ├── ocr/
    ├── evidence/
    ├── semantic-binding/
    ├── semantic-final/
    ├── final-questions/
    └── diagnostics/
```

## Engineering Principle

Do not over-engineer, but do not build fragile shortcuts.

Preferred approach:

```text
modular stages
clear schemas
source-grounded extraction
strict validation
local question windows
OCR before VLM
VLM only when visual interpretation is required
honest final statuses
tests for every behavior
```

## What Success Looks Like

For a PDF with 100 questions:

Good result:

```text
100 questions detected
all included in final JSON
accepted_safe items are truly source-backed
visual/image/tick items marked visual_required
answer-missing items marked answer_unavailable
corrupt items marked review_required or blocked
Chosen Option stored separately
no hallucinated answers
no dropped questions without reason
no cross-question option leakage
```

Bad result:

```text
only 50 questions output without explanation
blank options treated as accepted
Chosen Option treated as correct answer
visual questions accepted without visual evidence
incorrect source spans reused
LLM guesses missing answers/options
pipeline says success when OCR failed
```

## Final Reminder for AI/Cursor Agents

This is a PDF-to-final-questions-JSON pipeline.

Always align with this goal:

```text
Extract all questions from any question PDF as accurately and source-faithfully as possible, preserve evidence, mark uncertainty honestly, and export one final JSON file.
```

Do not drift into pattern ingestion, DB writes, classification, solving, or generation.

