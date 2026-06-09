# Implementation Decisions

Recorded decisions are binding for future agents unless explicitly superseded through Architecture Gatekeeper + Release Gatekeeper approval.

## Decision Log

### DEC-001: Local-First Processing

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | All v1 extraction runs locally by default. No cloud API required. |
| **Rationale** | Private PDFs (teacher content), zero marginal cost, offline capability. |
| **Rejected alternatives** | Cloud-first Azure DI pipeline; SaaS upload model |
| **Owner** | Solution Architect + Performance-Cost Reviewer |

### DEC-002: Python + uv

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Python runtime with uv for project/dependency management. |
| **Rationale** | Ecosystem fit for PDF/ML tools; uv for fast reproducible envs. |
| **Rejected alternatives** | Node.js toolchain; plain pip without lockfile |
| **Owner** | Senior Python Engineer |

### DEC-003: Marker Primary for v1

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Marker is the primary PDF-to-markdown extractor for v1 digital PDFs. |
| **Rationale** | Strong local layout-aware conversion; avoids cloud dependency. |
| **Rejected alternatives** | Direct pypdf text dump; Azure DI as primary; LLM vision parsing |
| **Owner** | Solution Architect |
| **Constraint** | Must be behind parser adapter (DEC-005) |

### DEC-004: No LLM for Normal Extraction

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | LLM/VLM not used on default extraction or parsing path for v1. |
| **Rationale** | Hallucination and paraphrase risk violate source faithfulness. |
| **Rejected alternatives** | LLM-structured JSON from raw PDF text; GPT-4o vision per page |
| **Owner** | AI Solution Architect + Product Manager |
| **Exception** | Future low-confidence repair with review flag only |

### DEC-005: No Direct PDF-to-Final-JSON

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Pipeline must have intermediate extraction package and deterministic parse stage. |
| **Rationale** | Debuggability, adapter swap, validation before final JSON. |
| **Rejected alternatives** | Single-shot PDF→JSON; LLM end-to-end |
| **Owner** | Solution Architect |

### DEC-006: No Fact Rewriting

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Output JSON preserves source facts verbatim in `raw_*` fields. |
| **Rationale** | MeritRanker trust and teacher audit requirements. |
| **Rejected alternatives** | Auto-correct typos; paraphrase for clarity; solve missing answers |
| **Owner** | Business Analyst + AI Solution Architect |
| **Reference** | `feature-context/source-faithfulness-rules.md` |

### DEC-007: Source Trace Required

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Every approved question includes file, page, block, and method trace. |
| **Rationale** | Audit disputes, re-extraction, downstream debugging. |
| **Rejected alternatives** | JSON-only without provenance |
| **Owner** | Business Analyst |

### DEC-008: Human/Review Gate for Low Confidence

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Low-confidence or incomplete extractions → `needs_review`, never silent `approved`. |
| **Rationale** | Unsafe extraction must not enter MeritRanker patterns automatically. |
| **Rejected alternatives** | Auto-approve with best guess; drop uncertain questions silently |
| **Owner** | QA Reviewer + Product Manager |

### DEC-009: Role-Based Governance Before Implementation

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | All 11 roles must review before code changes; Release Gatekeeper requires ≥99% confidence. |
| **Rationale** | Prevent agent drift, stack churn, and faithfulness violations. |
| **Rejected alternatives** | Ad-hoc implementation without review |
| **Owner** | Release Gatekeeper |
| **Reference** | `feature-context/review-and-approval-workflow.md` |

### DEC-023: Pattern Question Input Handoff Package (Part 12)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Add derived `pattern-input/` handoff artifacts filtered by eligibility status. No pattern IDs, graph generation, or content rewriting. Requires eligibility; fails on failed quality gate unless explicitly overridden. |
| **Rationale** | Future MeritRanker pattern ingestion needs a stable, source-faithful input contract separate from extraction artifacts. |
| **Rejected alternatives** | Direct pattern graph generation; LLM normalization; auto-promoting visual questions to eligible |
| **Owner** | Product Manager + Solution Architect + Architecture Gatekeeper |

### DEC-022: Artifact Reconciliation as Read-Only Quality Gate (Part 11)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Candidate report counters must be derived from `question-candidates.json`, not stale heuristics. Add `diagnostics/artifact-reconciliation.json` cross-checking candidate/mapping/final/review/eligibility artifacts. `quality_gate_status`: passed/warning/failed. Auto-run reconciliation when `--build-eligibility` is set. |
| **Rationale** | Downstream ingestion gates rely on truthful reports; false zero noise counts are dangerous. |
| **Rejected alternatives** | AI-based quality scoring; mutating artifacts during reconciliation |
| **Owner** | Business Analyst + Solution Architect + Architecture Gatekeeper |

### DEC-021: Deterministic Structural Audit and Asset Binding Rules (Part 10)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Split embedded option markers in option parsing context only; classify pre-option images as `question_image` + `question_support_image` for visual-dependent questions; bind trailing images to text options only when source position proves option image; emit `question-structure-audit.json`; visual-dependent complete candidates are `review_required` not eligible. |
| **Rationale** | Candidate JSON must be truthful; false option-image binding and merged options are worse than blocking/review. |
| **Rejected alternatives** | Inferring A–D from image order; OCR/VLM asset labeling; diagram syntax generation |
| **Owner** | Solution Architect + Business Analyst + Architecture Gatekeeper |

### DEC-020: Ingestion Eligibility as Read-Only Safety Gate (Part 9)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Add derived `eligibility/` artifacts categorizing final items as `eligible_for_ingestion`, `review_required`, or `blocked`. Duplicate solution diagnostics use exact string equality only. Does not perform pattern ingestion. |
| **Rationale** | Future pattern ingestion needs a deterministic gate; duplicate solutions and visual ambiguity must not be silently eligible. |
| **Rejected alternatives** | Auto-resolving duplicates; AI eligibility scoring; DynamoDB writes |
| **Owner** | Solution Architect + Business Analyst + Architecture Gatekeeper |

### DEC-019: Deterministic Visual Asset Binding and Solution Splitting

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Bind option images by source order (`linked_asset_paths` on options, `option_key` on assets). First pre-option image is `question_image`; additional pre-option images are unlabeled. Split multi-anchor solution lines deterministically via `RE_S_ANCHOR_FIND`. No LLM/OCR. |
| **Rationale** | Visual MCQs need option-image linkage for review; merged S1/S2 solutions corrupt mappings. |
| **Rejected alternatives** | VLM image understanding; guessing missing A/D labels; rewriting solution text |
| **Owner** | Solution Architect + AI Solution Architect + Senior Python Engineer |

### DEC-018: Source-Traced Content Lines for Table-Cell Expansion

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Keep `classified/lines.json` as raw-line classification. Add `classified/content-lines.json` with source-traced logical segments from table cells (`<br>` splits). Downstream parsers prefer content-lines when present. |
| **Rationale** | Marker two-column PDFs produce markdown table rows containing multiple questions; raw-line classification alone yields zero anchors. Expansion preserves raw markdown while enabling parsing. |
| **Rejected alternatives** | Rewriting raw.md; dropping table rows; LLM cell parsing |
| **Owner** | Solution Architect + AI Solution Architect + Business Analyst |

### DEC-017: Final Package Audit Is Read-Only (Part 7)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Part 7 audit reads `final/questions.json` and writes separate artifacts under `audit/`. No mutation of final package or upstream artifacts. Deterministic rules only — no AI scoring or auto-fix. |
| **Rationale** | Quality gate before pattern ingestion; evidence-based review; preserves source faithfulness of final package. |
| **Rejected alternatives** | Auto-correcting final JSON; AI quality scoring; modifying Part 1–6 contracts |
| **Owner** | Product Manager + Solution Architect + Security Reviewer |

### DEC-016: Final Package Preserves All Candidates (Part 6)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Part 6 merges candidates + mappings into final package without dropping items. Missing answers → `question_only_validated`. Uncertainty → review statuses, not correction. |
| **Rationale** | Source faithfulness; PYQ-only PDFs valid; safe input for future pattern ingestion without premature rejection. |
| **Rejected alternatives** | Dropping questions without answers; rewriting text; final pattern ingestion in Part 6 |
| **Owner** | Product Manager + AI Solution Architect + Business Analyst |

### DEC-015: Explicit Answer/Solution Mapping Only (Part 5)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Part 5 maps only explicit answer/solution evidence from source text. No solving, inferring, or rewriting. PYQ-only PDFs with `not_available` are valid. |
| **Rationale** | Source faithfulness; conservative mapping beats wrong mapping; separates mapping from final JSON packaging. |
| **Rejected alternatives** | Inferring answers from solutions; dropping unmapped candidates; final ingestion JSON in Part 5 |
| **Owner** | AI Solution Architect + Business Analyst + Solution Architect |

### DEC-014: Question Candidate Shells Only (Part 4)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Part 4 produces question candidate JSON shells from classified lines. No answer mapping, solution mapping, or final ingestion JSON. |
| **Rationale** | Separates boundary detection from answer/solution mapping; preserves source faithfulness; enables conservative review flags. |
| **Rejected alternatives** | Full question JSON in Part 4; auto-mapping answers from solution section; dropping questions without options |
| **Owner** | Solution Architect + Business Analyst + AI Solution Architect |

### DEC-013: Deterministic Regex Markdown Classifier (Part 3)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Part 3 classifies `marker/raw.md` into line/block JSON using stdlib regex only. No LLM, no content rewriting. `raw_text` preserved exactly per line. |
| **Rationale** | Stable intermediate artifact for Part 4 question parser; maintains source faithfulness; fast and testable. |
| **Rejected alternatives** | LLM line labeling; merging question body into anchors in Part 3; dropping blank/noise lines |
| **Owner** | Solution Architect + AI Solution Architect + Business Analyst |

### DEC-012: Marker via Subprocess CLI (External Install)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Invoke Marker via subprocess (`marker_single`) with configurable `MERITRANKER_MARKER_COMMAND`. Do not import Marker Python internals or add `marker-pdf` to project dependencies. |
| **Rationale** | Isolates heavy ML stack from ingestion package; keeps tests mockable; aligns with adapter boundary (DEC-003, DEC-005). |
| **Rejected alternatives** | `pip install marker-pdf` as runtime dependency; direct Marker SDK imports in adapter |
| **Owner** | Solution Architect + Performance-Cost Reviewer + Security Reviewer |
| **Constraint** | `shell=False`; argv built with `shlex.split`; stdout/stderr logged to `extraction.log` |

### DEC-011: Package Name `meritranker_data_ingestion`

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Active |
| **Decision** | Python package namespace is `meritranker_data_ingestion` under `src/` layout. |
| **Rationale** | PEP 8 module naming; matches repo purpose; stable import path for CLI and tests. |
| **Rejected alternatives** | `meritranker_ingestion` (ambiguous); flat module without `src/` layout |
| **Owner** | Architecture Gatekeeper + Senior Python Engineer |

### DEC-010: Azure Document Intelligence Optional Benchmark Only

| Field | Value |
|-------|-------|
| **Date** | 2026-06-06 |
| **Status** | Superseded (evidence extractor scope) by DEC-024 |
| **Decision** | Azure DI may be used later for benchmark/fallback experiments—not v1 requirement. |
| **Rationale** | Cost, network, and privacy constraints for default path. |
| **Rejected alternatives** | Azure DI as primary extractor |
| **Owner** | Performance-Cost Reviewer + AI Solution Architect |

### DEC-029: LLM Provider Preflight + .env Loading

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Add `python-dotenv` `.env` loading, Azure stable deployment URL mode, optional `MERITRANKER_BINDER_CHAT_COMPLETIONS_URL` full-URL mode, provider preflight before semantic chunks, `test-llm-provider` CLI, fail-fast on 401/403/404. |
| **Rationale** | Azure 404 errors on every chunk indicated wrong URL construction; preflight prevents costly repeated failures. |
| **Rejected alternatives** | Continuing per-chunk 404 retries; logging full prompts to stdout |
| **Owner** | Senior Python Engineer + Security Reviewer |

### DEC-039: Batch PDF Folder Runner + Share Logs

| Field | Value |
|-------|-------|
| **Date** | 2026-06-08 |
| **Status** | Active |
| **Decision** | Add `run-pdf-folder` CLI: sequential batch over `input_pdfs/`, per-PDF `<stem>.questions.json` + `<stem>.share-log.md`, batch `batch-summary.md` + `batch-run.log.jsonl`. Reuse semantic pipeline with `auto_profile` and `build_final_questions_export`. |
| **Rationale** | Reduce manual PDF-by-PDF debugging; one shareable log per PDF for ChatGPT review. |
| **Rejected alternatives** | Per-PDF YAML config; duplicate report files; parallel concurrency >1 in v1 |
| **Owner** | Product Manager + Release Gatekeeper |

### DEC-038: Azure OCR Rendered-Image Fallback + Failed OCR Artifacts (Part 14D)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-08 |
| **Status** | Active |
| **Decision** | On Azure `UnsupportedContent` or `InvalidContentLength` for per-page PDF bytes, render page to PNG/JPEG (200/150/120/100 DPI, max 4 MB) and retry. Save images under `ocr/page-images/`. Always write `ocr-evidence.json`/`ocr-evidence.md` even when OCR fails (`status: failed`). `azure-page-ocr-status.json` lists per-page `attempts[]`. Explicit `--ocr-engine azure` with zero lines still stops before LLM unless `--allow-ocr-fallback`. |
| **Rationale** | SSC-CGL_2021_aug per-page PDF bytes were rejected by Azure DI; pipeline raised before writing OCR evidence. Screenshot-style PDFs need image OCR. |
| **Rejected alternatives** | VLM tick extraction; silent OCR skip; LLM without OCR evidence file |
| **Owner** | Solution Architect + Release Gatekeeper |

### DEC-037: OCR Input Split + Local Question Window Binder (Part 14C)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-08 |
| **Status** | Active |
| **Decision** | Azure OCR retries per-page with resize on `InvalidContentLength`. Semantic binder chunks by `question-windows.json`. Option spans restricted to local window. Unsupported response-sheet/repeated-numbering layouts stop before LLM unless `--allow-unsupported-layout`. |
| **Rationale** | SSC-CGL_2021_aug had OCR `InvalidContentLength`, cross-question option span reuse, and global binding leakage. |
| **Rejected alternatives** | LLM window repair; section-wise pipeline; VLM tick extraction |
| **Owner** | Solution Architect + Release Gatekeeper |

### DEC-036: OCR Runtime Enforcement + Numeric Option Binding (Part 14B)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-08 |
| **Status** | Active |
| **Decision** | Explicit `--ocr-engine azure|paddle` fails preflight before LLM if dependency/config missing. Numeric options `1-4` preserved with canonical A-D mapping. Question-only mode exports source-backed items as `answer_unavailable`, not blocked/hallucinated. `ocr_used` true only when OCR lines exist. |
| **Rationale** | SSC-CGL_2021_aug response-sheet PDF had 0 OCR lines despite `--ocr-engine azure`; hallucination explosion and empty option keys made final JSON useless. |
| **Rejected alternatives** | Treating chosen option as correct answer; relaxing hallucination for all fields; VLM tick extraction |
| **Owner** | Solution Architect + Release Gatekeeper |

### DEC-035: OCR Evidence Augmentation + Unified Final Questions JSON (Part 14A)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-08 |
| **Status** | Active |
| **Decision** | Add OCR evidence layer (Azure DI primary, Paddle optional) that enriches but does not replace Marker. Merge into `merged-document-evidence.json`; semantic binder prefers merged evidence. Export unified `final-questions.json` with all detected items and quality statuses. `Chosen Option` stored separately — never used as correct answer. Pipeline ends at final JSON export (no pattern ingestion/DB). |
| **Rationale** | Screenshot/webpage PDFs have options visible in images but blank in Marker text; need source-faithful final JSON with review lanes, not blind acceptance. |
| **Rejected alternatives** | OCR-only pipeline; VLM tick-as-answer; pattern ingestion in same step |
| **Owner** | Solution Architect + AI Solution Architect + Release Gatekeeper |

### DEC-034: Strict Final Acceptance Gate + Evidence Quality Router (Part 13J)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-08 |
| **Status** | Active |
| **Decision** | `accepted-only` semantic final export includes only items with `final_gate_status == accepted_safe`. Binding `accepted` alone is insufficient. Visual/figure-dependent items route to `review_visual_required`; corrupt/incomplete text MCQ to `review_evidence_corrupt`; bad/quarantined/hallucinated to `blocked_bad_item`. No LLM/VLM in gate. `ready_for_partial_accepted_ingestion` is true only when all exported items are accepted_safe. |
| **Rationale** | 88/100 binding-accepted export still contained empty options, missing answer text, and visual questions — unsafe for pattern ingestion. Prefer fewer accepted-safe items over polluted export. |
| **Rejected alternatives** | Relax validation for visual MCQ; auto-guess options; more PDF-specific regex repair; pattern ingestion in same step |
| **Owner** | Solution Architect + Business Analyst + Release Gatekeeper |

### DEC-033: Semantic Stability Guard + Chunk Diagnostics (Part 13I)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Add per-chunk diagnostics, bad-item quarantine before repair, replay plan for suspicious chunks only (`--execute` required for LLM). No prompt/model changes. Quarantined items excluded from final export. |
| **Rationale** | Fresh runs vary (79 vs 91 accepted); need traceability to chunk/LLM vs repair vs validation without hiding failures. |
| **Rejected alternatives** | Auto-delete overflow items; full-PDF rebind on replay; storing full prompts by default |
| **Owner** | Solution Architect + AI Solution Architect + Security Reviewer |

### DEC-032: Semantic Final Export + Review Patch (Part 13H)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Add `semantic-final/` export: accepted-only / accepted-plus-patched / all-with-status modes, review items + patch template, manual patch application (`confirm_no_guessing` + `reviewer_notes` required), final JSON with correct_answer_key/text, source trace, provenance. No pattern ingestion or DB writes. |
| **Rationale** | 91/100 accepted is useful but incomplete; safe export + human patch path prepares pattern ingestion without auto-including unresolved items. |
| **Rejected alternatives** | Auto-accept review items; LLM patch generation; merging into deterministic `final/questions.json`; pattern ingestion in same step |
| **Owner** | Solution Architect + Business Analyst + Security Reviewer |

### DEC-031: Semantic Edge-Case Repair + One-Command Pipeline (Part 13G)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Extend deterministic repair with embedded option parsing (pipe rows, same-line `(A)…(D)`, multi-bold), neighbouring-line scan, remaining-issue diagnostician, `run-semantic-pipeline` one-command orchestrator, `--clean-output` with path guards. Validator resets issues per run (no stale LLM issues). `questions_with_4_options_count` counts only source-backed non-empty options. |
| **Rationale** | Part 13F left 9 edge cases (table rows, mega-lines, visual-only, noise); users need one PDF→repaired-evaluation command with a single output folder. |
| **Rejected alternatives** | LLM re-bind; final merge; positional option guessing without labels |
| **Owner** | Senior Python Engineer + Solution Architect |
| **Constraint** | All artifacts under `{output}/extraction_package/`; no extra output roots |

### DEC-030: Semantic Binding Deterministic Repair (Part 13F)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Add deterministic post-LLM repair: `semantic_key_normalizer`, `semantic_source_span_resolver`, `repair-semantic-binding` CLI. Normalize option/answer keys (A–E), fill empty option slots and attach `source_spans` from `document-evidence.json` when text matches, re-run validator/evaluation. Write `.repaired.json` artifacts by default; `--overwrite-semantic-binding` optional. `evaluate-semantic-binding --use-repaired` reads repaired artifacts. Validator compares canonical normalized keys; skips source-span requirements for empty option placeholders. |
| **Rationale** | Real Azure bind on SSC recovered 100 questions/answers but failed validation (321 missing spans, 99 answer-key mismatches) because LLM left empty option keys/text and omitted spans. Repair grounds structure in evidence without another LLM call. |
| **Rejected alternatives** | Prompt rewrite; relaxing hallucination rules; inventing option text without evidence; final-package merge; pattern ingestion |
| **Owner** | Senior Python Engineer + AI Solution Architect + QA Reviewer |
| **Constraint** | No LLM/API calls during repair; no mutation of `final/` or original evidence |

### DEC-028: Real-Provider Evaluation Harness (Part 13E)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Add `AzureOpenAIProvider` (api-key auth, deployment URL + api-version), `evaluate-semantic-binding` CLI, configurable quality thresholds (passed/warning/failed), deterministic vs semantic comparison in evaluation report, pipeline passthrough for `--include-answer-key-evidence` and `--timeout-seconds`. Cache hash includes prompt version. |
| **Rationale** | Mock provider proves infrastructure only; real-provider evaluation needs Azure compatibility, quality measurement, and pipeline integration without mutating final artifacts. |
| **Rejected alternatives** | Auto-merge semantic output to final; eligibility from semantic output; real API calls in unit tests |
| **Owner** | AI Solution Architect + Senior Python Engineer + QA Reviewer |
| **Constraint** | `--strict-semantic-quality` optional hard fail; no final/package mutation |

### DEC-027: Semantic Binder Evaluation + Answer-Key Hardening (Part 13D)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Harden semantic binder with improved prompts/chunking, deterministic `answer_key_evidence_extractor` (evidence + post-validation only — never overwrites LLM output), evaluation artifacts (`semantic-binding-evaluation.json/.md`), and OpenAI-compatible provider robustness. Add CLI `--eval-only`, `--include-answer-key-evidence`, `--timeout-seconds`. Report success thresholds without hard-failing. |
| **Rationale** | Part 13C mock binder extracted options but missed answer keys; compact answer-key PDF layouts need deterministic pre-scan + richer prompts. Evaluation reports measure gap to expected count before any final-package merge. |
| **Rejected alternatives** | LLM-only answer keys without source spans; overwriting final/questions.json; automatic eligibility from semantic output; VLM image interpretation |
| **Owner** | AI Solution Architect + Senior Python Engineer + QA Reviewer |
| **Constraint** | Temperature 0; JSON only; cache by evidence hash; no API keys in logs; no full evidence text on stdout |

### DEC-026: Source-Grounded Semantic Binder v1 (Part 13C)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Add LLM-assisted semantic binding from `document-evidence.json` with mandatory source-grounded validation. Output under `semantic-binding/` only; never overwrite `questions/`, `final/`, or pattern-input artifacts. Temperature 0, structured JSON, mock provider for tests. |
| **Rationale** | Deterministic parser fails on layouts where evidence has option lines (e.g. `- **A** 50`) but binding fails; semantic binder with validator improves binding safely. |
| **Rejected alternatives** | LLM-only final output; automatic pattern ingestion; replacing deterministic parser globally; VLM visual reasoning |
| **Owner** | AI Solution Architect + Solution Architect + Security Reviewer |
| **Constraint** | API keys from env only; cache by evidence hash; binder runs only when explicitly invoked or pipeline flag + quality trigger |

### DEC-025: Unified Evidence Normalization (Part 13B)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Normalize Marker/Azure artifacts into canonical `evidence/document-evidence.json` with deterministic role hints only. Support partial extractor success (`extraction_status: partial`). Primary extractor auto-select prefers Azure DI when succeeded with lines, else Marker. No semantic binding or parser changes. |
| **Rationale** | Stable internal evidence contract for future semantic binder; handles `--extractor both` partial failures without blocking Marker evidence. |
| **Rejected alternatives** | Line-level merge across extractors; LLM/VLM normalization; replacing question candidate parser input |
| **Owner** | Solution Architect + AI Solution Architect + Business Analyst |
| **Constraint** | Raw Marker/Azure artifacts unchanged; no network calls during normalization |

### DEC-024: Evidence Extractor Layer — Marker + Azure DI (Part 13A)

| Field | Value |
|-------|-------|
| **Date** | 2026-06-07 |
| **Status** | Active |
| **Decision** | Add `AzureDocumentIntelligenceAdapter` behind the existing adapter boundary. Keep `MarkerAdapter` as default. Support `prepare --extractor marker\|azure-di\|both`. Persist Azure layout/OCR evidence under `extractors/azure-di/` and `extractors/extractor-manifest.json`. No semantic binding, LLM repair, or final JSON changes in Part 13A. |
| **Rationale** | Marker fails on some layouts; richer layout/OCR evidence supports Part 13B normalization without replacing deterministic parser yet. |
| **Rejected alternatives** | Azure DI as sole default extractor; LLM/VLM semantic extraction in Part 13A; parser rewrite in Part 13A |
| **Owner** | Solution Architect + AI Solution Architect + Performance-Cost Reviewer |
| **Constraint** | Azure credentials from env only; no real Azure calls in unit tests; Azure SDK optional extra (`uv sync --extra azure`) |

## How to Add a Decision

1. Propose during pre-implementation review.
2. Document with: ID, date, status, decision, rationale, rejected alternatives, owner.
3. Architecture Gatekeeper approves if architectural.
4. Update this file in same PR/change as the implementation.
5. Documentation Mentor verifies cross-links.

## Superseding a Decision

Requires explicit new decision entry referencing the old ID, plus Architecture Gatekeeper and Release Gatekeeper approval. Never silently contradict an active decision.
