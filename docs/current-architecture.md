# Current Architecture

**Status:** Part 13E implemented (real-provider evaluation harness). Final package merge not yet implemented. Pattern ingestion not yet implemented.

## Design Goals

1. **Modular pipeline** — replaceable stages with explicit contracts
2. **Deterministic-first** — no LLM on default extraction path
3. **Source-faithful** — raw text and trace preserved end-to-end
4. **Local-first** — Marker runs locally; cloud optional
5. **Review-gated** — low confidence never auto-approved

## Pipeline Stages

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│  Input PDF  │───▶│ Extraction Pkg   │───▶│ Deterministic Parser │
│  (CLI args) │    │ (Marker adapter) │    │ (question blocks)    │
└─────────────┘    └──────────────────┘    └──────────┬──────────┘
                                                        │
                       ┌──────────────────┐             ▼
                       │ Validation Report│◀───┌─────────────────────┐
                       │ + Review Status  │    │ Pydantic Validation │
                       └────────┬─────────┘    │ + Faithfulness Rules│
                                │              └──────────┬──────────┘
                                ▼                         │
                       ┌──────────────────┐                ▼
                       │ Final Question   │◀───────────────┘
                       │ JSON             │
                       └────────┬─────────┘
                                │
                                ▼ (future)
                       ┌──────────────────┐
                       │ MeritRanker      │
                       │ Pattern Ingestion│
                       └──────────────────┘
```

## Stage Definitions

### 1. CLI / Ingest

- Accepts: PDF path(s), output directory, optional flags (future: OCR, vision).
- Validates paths (no traversal).
- Orchestrates pipeline; contains no parsing logic.

### 2. Extraction Package (Evidence Extractors)

- **Adapter boundary** supports Marker (default) and Azure Document Intelligence (optional).
- **Marker output:** `marker/raw.md`, `marker/assets/`, package `manifest.json`.
- **Azure DI output (Part 13A):** layout/OCR evidence under `extractors/azure-di/` plus `extractors/extractor-manifest.json`.
- **CLI:** `prepare --extractor marker|azure-di|both` (default: `marker`).
- No semantic question binding in Part 13A/13B — evidence only for future semantic binder.
- **Part 13B:** `normalize-evidence` produces `evidence/document-evidence.json` with pages, lines, tables, figures, images, role hints, metadata candidates, and extractor comparison diagnostics.
- Supports partial extractor success (`extraction_status: partial`) when one of Marker/Azure succeeds.
- **Part 13C–13E:** `bind-semantically` reads evidence, calls LLM (mock, `openai-compatible`, or `azure-openai`), validates source grounding, writes `semantic-binding/` artifacts. `evaluate-semantic-binding` recomputes quality reports with thresholds and deterministic comparison. Does not mutate `questions/` or `final/`.
- Persisted to output directory for audit and re-parse without re-running extractors.

### 3. Deterministic Parser

- Reads `ExtractionPackage` only—not raw PDF directly.
- Identifies question boundaries, options, answer keys, solution blocks.
- Handles layout variants: inline answers, solutions-at-end, questions-only.
- Produces parser candidates with confidence scores.
- **No LLM** in v1.

### 4. Validation + Faithfulness Rules

- Pydantic models enforce schema.
- Rule engine enforces `feature-context/source-faithfulness-rules.md`.
- Checks: sequence, duplicates, mapping, missing answers policy, source trace presence.

### 5. Review Status Assignment

| Status | Meaning |
|--------|---------|
| `approved` | High confidence, complete trace, faithfulness checks pass |
| `needs_review` | Low confidence, missing optional fields, mapping uncertain |
| `rejected` | Faithfulness violation or unparseable without guessing |

### 6. Output Artifacts

- `questions.json` (or per-document JSON files)
- `validation-report.json`
- Extraction package directory (markdown, images)
- `review/review-items.json` and `review/review-items.md` (Part 8 — flagged items only)
- `eligibility/` artifacts (Part 9 — eligible/review/blocked gate for future ingestion)
- `pattern-input/` handoff package (Part 12 — source-faithful input for future pattern ingestion)
- `diagnostics/artifact-reconciliation.json` (Part 11 — cross-artifact quality gate)

### 7. Pattern Ingestion (Future, Out of Repo Scope for v1)

Downstream MeritRanker consumer reads `pattern-input/pattern-question-input-package.json` (or filtered subsets). Contract must remain stable once published. This repo does not perform pattern ingestion.

## Implemented Module Layout (Part 1–6)

```
meritranker-data-ingestion/
├── pyproject.toml
├── src/
│   └── meritranker_data_ingestion/   # DEC-011
│       ├── cli.py                    # stage commands + run-pipeline orchestration
│       ├── config.py                 # package paths, MERITRANKER_MARKER_COMMAND
│       ├── schemas/
│       │   ├── extraction.py         # ExtractionPackageManifest
│       │   ├── classification.py     # Line/block records (DEC-013)
│       │   ├── question_candidates.py # Candidate shells (DEC-014)
│       │   ├── answer_solution_mapping.py # Mapping artifacts (DEC-015)
│       │   ├── final_question_package.py  # Final package (DEC-016)
│       │   ├── final_package_audit.py     # Quality audit (DEC-017)
│       │   ├── pipeline.py                # Pipeline run schemas (Part 8)
│       │   └── review_export.py           # Review export schemas (Part 8)
│       │   # ContentLineRecord in classification.py (DEC-018)
│       ├── services/
│       │   ├── parser_adapter.py     # ParserAdapter protocol
│       │   ├── marker_adapter.py     # Subprocess Marker extraction (DEC-012)
│       │   ├── marker_runner.py      # CLI argv + subprocess runner
│       │   ├── extraction_package.py # Package layout + artifact copy
│       │   ├── markdown_classifier.py # Regex line/block classifier
│       │   ├── question_candidate_parser.py # Candidate assembly
│       │   ├── answer_solution_mapper.py    # Answer/solution mapping
│       │   ├── final_question_package_builder.py # Final package
│       │   ├── final_package_auditor.py       # Read-only audit
│       │   ├── line_text_classifier.py        # Shared text classification
│       │   ├── content_line_expander.py       # Table <br> expansion (DEC-018)
│       │   ├── raw_markdown_inspector.py      # Diagnostics inspector
│       │   ├── question_coverage_diagnostician.py
│       │   ├── pipeline_runner.py             # One-command orchestration (Part 8)
│       │   ├── review_exporter.py             # Read-only review export (Part 8)
│       │   ├── ingestion_eligibility_builder.py  # Eligibility gate (Part 9)
│       │   └── file_service.py       # Safe path utilities
│       └── utils/
│           └── logging.py
├── tests/
│   ├── test_marker_adapter.py
│   ├── test_marker_runner.py
│   └── ...
└── docs/
```

### Extraction package on disk (Part 2–7 output)

```
output/extraction_package/
├── source/original.pdf
├── marker/raw.md
├── classified/
│   ├── lines.json
│   ├── content-lines.json
│   └── blocks.json
├── diagnostics/
├── questions/
├── mappings/
├── manifest.json
├── final/
│   ├── questions.json
│   └── validation-report.json
├── audit/
│   ├── final-package-audit.json
│   └── final-package-audit.md
├── review/
│   ├── review-items.json
│   └── review-items.md
└── eligibility/
    ├── ingestion-eligibility-report.json
    ├── eligible-questions.json
    ├── review-required-questions.json
    ├── blocked-questions.json
    ├── duplicate-solution-diagnostics.json
    └── ingestion-eligibility.md
```

### Pipeline orchestration (Part 8)

`run-pipeline` calls stage services directly (no subprocess except Marker in `prepare`). `export-review-items` reads final package + audit and writes review artifacts without mutating upstream JSON.

Console entry: `meritranker-ingest = meritranker_data_ingestion.cli:main`

## Planned (Future)
- MeritRanker pattern ingestion consumer (after sample validation)
- `tests/fixtures/` end-to-end packages

## Adapter Boundary (Critical)

```
Marker SDK  →  MarkerAdapter  →  ExtractionPackage  →  Parser  →  Validator
```

Nothing except `MarkerAdapter` imports Marker. Tests inject fake `ExtractionPackage` fixtures.

## Optional Fallback Stages (Future)

Attach after deterministic parser, gated by confidence:

- `OcrFallbackAdapter` (PaddleOCR)
- `VisionFallbackAdapter` (Qwen3-VL)
- `BenchmarkAdapter` (Azure DI, compare-only)

Fallback outputs merge as provisional blocks with `ai_assisted` metadata—never silent overwrite of high-confidence deterministic fields.

## Anti-Patterns (Forbidden)

- PDF → LLM → JSON in one step
- Marker calls from validation or CLI modules
- Skipping `ExtractionPackage` persistence
- Auto-approve when answer key absent in source
- Cloud API in default CLI invocation

## Architecture Change Process

Any deviation from this document requires:

1. Solution Architect proposal
2. Architecture Gatekeeper four-question review
3. Update to `docs/implementation-decisions.md` and this file
4. Release Gatekeeper ≥99% approval
