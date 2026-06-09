# PDF to Question JSON Ingestion

## What We Are Building

A **local-first Python CLI and tooling package** that ingests exam and study PDFs and produces **source-faithful structured question JSON** plus a **validation report**. Output is designed for later MeritRanker pattern ingestion—not for direct publishing without review.

**Pipeline concept:**

```
PDF / Markdown / extracted content
  → extraction package (markdown + assets)
  → deterministic parser (question blocks)
  → Pydantic validation
  → review status assignment
  → final question JSON + validation report
  → (later) MeritRanker pattern ingestion
```

## Supported PDF Types

| Category | Examples | v1 Expectation |
|----------|----------|----------------|
| Previous year questions (PYQ) | JEE, NEET, UPSC, state boards | Primary v1 target—digital text PDFs |
| Government exam papers | SSC, banking, railway | Support if structurally similar to PYQ |
| Teacher-provided PDFs | Classroom tests, coaching sheets | Support; layout variance higher |
| Questions only | No answer key in document | Extract questions; answer fields empty + `needs_review` |
| Solutions at end | Answers/solutions in last section | Parse mapping; flag mapping confidence |
| Digital text PDFs | Born-digital, selectable text | Primary Marker path |
| Scanned PDFs | Image-only pages | Defer to future OCR; v1 marks `needs_review` or skips with report |
| Handwritten/scanned notes | Student notes | **Future scope** |
| Image/diagram questions | Geometry, graphs, reasoning | Extract text + image refs; diagram syntax **future** |
| Tables, formulas, options | STEM papers | Preserve raw text and structure references |

## v1 Scope

**In scope:**

- Local CLI accepting PDF path(s) and output directory
- Marker-based extraction behind parser adapter
- Intermediate markdown/block representation (debuggable, auditable)
- Deterministic parsing into question JSON schema (Pydantic)
- Validation report: errors, warnings, confidence, review flags
- Source trace on every question field (file, page, block reference, raw snippet)
- Review statuses: `approved`, `needs_review`, `rejected`
- Digital text PDFs with standard MCQ layouts
- PYQ with or without inline answers (no inventing missing answers)
- Solutions-at-end documents with explicit mapping attempt + confidence

**Out of v1 implementation (document only):**

- PaddleOCR for scanned pages
- Qwen3-VL for diagram-to-syntax
- Azure Document Intelligence (benchmark/fallback reference only)
- Handwriting recognition
- MeritRanker pattern ingestion consumer
- Web UI or API server

## Future Scope

- OCR fallback (PaddleOCR) for scanned/low-confidence pages
- Vision model (Qwen3-VL) for diagram and figure syntax extraction
- Optional Azure Document Intelligence for benchmark comparison
- Batch/resumable processing for large PYQ libraries
- Handwritten note ingestion
- Auto-suggest repair for low-confidence blocks (AI-assisted, always reviewed)
- Integration hook for MeritRanker pattern ingestion pipeline

## Non-Goals

- **Not** a general-purpose PDF summarizer or study guide generator
- **Not** rewriting questions into "cleaner" language
- **Not** solving questions to fill missing answer keys
- **Not** a cloud-first SaaS in v1
- **Not** direct PDF → final JSON in one LLM call
- **Not** auto-publishing to MeritRanker without human review for low-confidence output
- **Not** replacing teachers' content with AI-generated variants

## Expected Output Concept

### Question JSON (per document or batch)

Each question record should conceptually include:

- `question_id` — stable ID within document (e.g., `Q12` or sequential)
- `raw_question_text` — verbatim extracted text, not paraphrased
- `options` — array of `{ label, raw_text }` preserving original wording
- `answer` — raw answer text if present in source; `null` if absent (never invented)
- `solution` — raw solution text if mapped; mapping metadata if solutions-at-end
- `images` — references to cropped/rendered assets with page and bbox metadata
- `metadata` — exam, year, subject if detectable from source (low confidence → review)
- `source_trace` — file, page(s), block offsets, extraction method, parser version
- `review_status` — `approved` | `needs_review` | `rejected`
- `confidence` — per-field or aggregate score from deterministic rules

### Validation Report

- Document-level summary: total questions, approved count, needs_review count
- Per-question issues: missing options, sequence gaps, duplicate IDs, mapping failures
- Parser/extraction warnings: low OCR confidence (future), parse ambiguities
- Source faithfulness flags: any field that required normalization must be listed

### Downstream Contract

JSON must be consumable by a future MeritRanker pattern ingestion stage without requiring re-read of the original PDF for factual fields—while still retaining source trace for audit.
