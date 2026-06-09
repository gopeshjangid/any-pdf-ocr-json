# Change Log

Chronological record of repository changes. Every agent or contributor must append an entry after each change.

---

## 2026-06-06 — Governance and skill system setup

**Author:** Cursor agent (governance setup task)  
**Type:** Documentation / governance  
**Summary:** Initial creation of role-based review system, feature context, project docs, and agent handoff workflow. No implementation code added.

**Files created:**

- `skills/SKILL.md` — master governance skill for Cursor agents
- `roles/*.md` — 11 role responsibility documents
- `feature-context/*.md` — 4 feature context documents
- `docs/project-context.md`
- `docs/current-architecture.md`
- `docs/implementation-decisions.md` (DEC-001 through DEC-010)
- `docs/next-agent-handoff.md`
- `README.md`

**Decisions recorded:** DEC-001 through DEC-010 in `implementation-decisions.md`

**Implementation impact:** None — markdown-only setup

**Next step:** Pre-implementation role review before Python/uv project scaffold

---

## 2026-06-06 — Part 1: Python + uv scaffold and stable contracts

**Author:** Cursor agent (Part 1 implementation)  
**Type:** Implementation (scaffold only)  
**Summary:** Added Python/uv project scaffold with extraction manifest schema, ParserAdapter protocol, MarkerAdapter placeholder, safe path utilities, CLI `prepare` command, and minimal tests. No PDF parsing or Marker execution.

**Files created:**

- `pyproject.toml`
- `src/meritranker_data_ingestion/` — package (`cli.py`, `config.py`, `schemas/`, `services/`, `utils/`)
- `tests/test_imports.py`, `tests/test_extraction_manifest.py`, `tests/test_cli_path_validation.py`

**Files updated:**

- `README.md` — uv setup, test, and CLI instructions
- `docs/next-agent-handoff.md`
- `docs/current-architecture.md` — implemented module layout
- `docs/implementation-decisions.md` — DEC-011 package naming

**Dependencies added:** `pydantic` (runtime), `pytest` (dev). No Marker, OCR, LLM, or cloud SDKs.

**Tests:** 12 passed (`uv run pytest -v`)

**Next step:** Part 2 — Marker adapter extraction package generation

---

## 2026-06-06 — Part 2: Marker extraction package generation

**Author:** Cursor agent (Part 2 implementation)  
**Type:** Implementation (extraction only)  
**Summary:** Implemented real `MarkerAdapter.extract()` via external Marker CLI subprocess. CLI `prepare` now produces standard extraction packages with source PDF copy, raw markdown, optional assets, logs, and manifest. No question parsing, OCR, LLM, or cloud calls.

**Files created:**

- `src/meritranker_data_ingestion/services/marker_runner.py` — subprocess runner + command builder
- `src/meritranker_data_ingestion/services/extraction_package.py` — package layout + artifact copy
- `tests/test_marker_adapter.py`, `tests/test_marker_runner.py`

**Files updated:**

- `src/meritranker_data_ingestion/services/marker_adapter.py` — real extraction
- `src/meritranker_data_ingestion/cli.py` — calls MarkerAdapter
- `src/meritranker_data_ingestion/config.py` — package paths + `MERITRANKER_MARKER_COMMAND`
- `src/meritranker_data_ingestion/services/file_service.py` — `copy_file_into_output`
- `tests/test_cli_path_validation.py`, `tests/test_extraction_manifest.py`
- `README.md`, `docs/next-agent-handoff.md`, `docs/current-architecture.md`, `docs/implementation-decisions.md` (DEC-012)

**Dependencies added:** None (Marker is external CLI, not a Python dependency)

**Tests:** 20 passed (`uv run pytest -v`)

**Next step:** Part 3 — markdown line/block classifier for question boundaries

---

## 2026-06-06 — Part 3: Deterministic markdown line/block classifier

**Author:** Cursor agent (Part 3 implementation)  
**Type:** Implementation (classification only)  
**Summary:** Added regex-based markdown classifier reading `marker/raw.md` and writing `classified/lines.json`, `blocks.json`, `classification-report.json`. New CLI `classify-markdown`. No question JSON, no answer/solution mapping, no AI.

**Files created:**

- `src/meritranker_data_ingestion/schemas/classification.py`
- `src/meritranker_data_ingestion/services/markdown_classifier.py`
- `tests/test_markdown_classifier.py`, `tests/test_classify_cli.py`

**Files updated:**

- `src/meritranker_data_ingestion/cli.py` — `classify-markdown` command
- `src/meritranker_data_ingestion/config.py` — classified paths
- `src/meritranker_data_ingestion/schemas/__init__.py`
- `README.md`, `docs/next-agent-handoff.md`, `docs/current-architecture.md`, `docs/implementation-decisions.md` (DEC-013)

**Dependencies added:** None (stdlib regex + Pydantic)

**Tests:** 45 passed (`uv run pytest -v`)

**Next step:** Part 4 — deterministic question candidate parser from classified lines/blocks

---

## 2026-06-06 — Part 4: Deterministic question candidate parser

**Author:** Cursor agent (Part 4 implementation)  
**Type:** Implementation (question candidates only)  
**Summary:** Added question candidate parser reading classified lines/blocks and writing `questions/question-candidates.json` and `question-candidate-report.json`. New CLI `parse-question-candidates`. No answer/solution mapping, no final JSON, no AI.

**Files created:**

- `src/meritranker_data_ingestion/schemas/question_candidates.py`
- `src/meritranker_data_ingestion/services/question_candidate_parser.py`
- `tests/test_question_candidate_parser.py`, `tests/test_parse_question_cli.py`

**Files updated:**

- `src/meritranker_data_ingestion/cli.py` — `parse-question-candidates` command
- `src/meritranker_data_ingestion/config.py` — questions paths
- `src/meritranker_data_ingestion/schemas/__init__.py`
- `README.md`, `docs/next-agent-handoff.md`, `docs/current-architecture.md`, `docs/implementation-decisions.md` (DEC-014)

**Dependencies added:** None

**Tests:** 63 passed (`uv run pytest -v`)

**Next step:** Part 5 — deterministic answer/solution mapper

---

## 2026-06-06 — Part 5: Deterministic answer/solution mapper

**Author:** Cursor agent (Part 5 implementation)  
**Type:** Implementation (mapping artifacts only)  
**Summary:** Added answer/solution mapper reading classified lines + question candidates. Writes `mappings/answer-solution-map.json`, `answer-solution-report.json`, and optional `question-candidates-with-mappings.json`. New CLI `map-answers-solutions`. No final JSON, no AI, no answer solving.

**Files created:**

- `src/meritranker_data_ingestion/schemas/answer_solution_mapping.py`
- `src/meritranker_data_ingestion/services/answer_solution_mapper.py`
- `tests/test_answer_solution_mapper.py`, `tests/test_map_answers_cli.py`

**Files updated:**

- `src/meritranker_data_ingestion/cli.py` — `map-answers-solutions` command
- `src/meritranker_data_ingestion/config.py` — mappings paths
- `README.md`, `docs/next-agent-handoff.md`, `docs/current-architecture.md`, `docs/implementation-decisions.md` (DEC-015)

**Dependencies added:** None

**Tests:** 81 passed (`uv run pytest -v`)

**Next step:** Part 6 — source-faithful final question package validator/finalizer

---

## 2026-06-06 — Part 6: Final question package validator/finalizer

**Author:** Cursor agent (Part 6 implementation)  
**Type:** Implementation (final package only)  
**Summary:** Added final question package builder merging candidates + mappings into `final/questions.json` and `final/validation-report.json`. New CLI `build-final-package`. Source-faithful validation statuses. No pattern ingestion, no AI.

**Files created:**

- `src/meritranker_data_ingestion/schemas/final_question_package.py`
- `src/meritranker_data_ingestion/services/final_question_package_builder.py`
- `tests/test_final_question_package_builder.py`, `tests/test_build_final_cli.py`

**Files updated:**

- `src/meritranker_data_ingestion/cli.py` — `build-final-package` command
- `src/meritranker_data_ingestion/config.py` — final/ paths
- `README.md`, `docs/next-agent-handoff.md`, `docs/current-architecture.md`, `docs/implementation-decisions.md` (DEC-016)

**Dependencies added:** None

**Tests:** 99 passed (`uv run pytest -v`)

**Next step:** Part 7 — real-world sample run and quality review with actual PDF

---

## 2026-06-06 — Part 7: Final package quality audit tooling

**Author:** Cursor agent (Part 7 implementation)  
**Type:** Implementation (audit/reporting only)  
**Summary:** Added deterministic read-only audit for final question packages. New CLI `audit-final-package` with optional `--expected-count`. Writes `audit/final-package-audit.json` and `audit/final-package-audit.md`. No extraction logic changes, no AI, no final JSON mutation.

**Files created:**

- `src/meritranker_data_ingestion/schemas/final_package_audit.py`
- `src/meritranker_data_ingestion/services/final_package_auditor.py`
- `tests/test_final_package_auditor.py`, `tests/test_audit_final_cli.py`
- `docs/sample-audit-checklist.md`

**Files updated:**

- `src/meritranker_data_ingestion/cli.py` — `audit-final-package` command
- `src/meritranker_data_ingestion/config.py` — audit/ paths
- `README.md`, `docs/next-agent-handoff.md`, `docs/current-architecture.md`, `docs/implementation-decisions.md` (DEC-017)

**Dependencies added:** None

**Tests:** 118 passed (`uv run pytest -v`)

**Next step:** Part 8 — decide scope only after auditing 2–3 real PDFs

---

## 2026-06-07 — Real-sample fix: table-row content-line expansion

**Author:** Cursor agent (real-sample blocker fix)  
**Type:** Bug fix (classification + parsing)  
**Summary:** Fixed zero-question detection for Marker two-column table markdown. Added `classified/content-lines.json` with source-traced table cell `<br>` expansion. Extended question/option detection for list+bold formats (`- **Q11.**`, `- (a)`). Added quality gates, Marker nested asset discovery, `inspect-raw-markdown` CLI. Real `paper1.pdf` now yields 92 candidates (was 0).

**Files created:**

- `src/meritranker_data_ingestion/services/line_text_classifier.py`
- `src/meritranker_data_ingestion/services/content_line_expander.py`
- `src/meritranker_data_ingestion/services/raw_markdown_inspector.py`
- `tests/test_content_line_expander.py`

**Files updated:**

- `schemas/classification.py`, `services/markdown_classifier.py`, `services/question_candidate_parser.py`
- `services/extraction_package.py`, `services/marker_adapter.py`, `cli.py`, `config.py`
- `README.md`, `docs/next-agent-handoff.md`, `docs/current-architecture.md`, `docs/implementation-decisions.md` (DEC-018)

**Tests:** 131 passed (`uv run pytest -v`)

**Real sample:** `paper1.pdf` — 92 question candidates, audit warning (expected 100)

---

## 2026-06-07 — Real-sample quality hardening

**Author:** Cursor agent  
**Type:** Bug fix / quality hardening  
**Summary:** Fixed provenance vs issues handling, candidate validation status, mapper content-lines usage, heading-based Q/S anchor detection (Q32–Q39), status distribution accounting, `diagnose-question-coverage` CLI. `paper1.pdf` now: 100 candidates, 99 mappings, audit expected-count match.

**Files created:** `classified_lines_loader.py`, `question_coverage_diagnostician.py`, `tests/test_quality_hardening.py`

**Files updated:** `question_candidates.py`, `question_candidate_parser.py`, `answer_solution_mapper.py`, `line_text_classifier.py`, `content_line_expander.py`, `final_question_package_builder.py`, `cli.py`, docs

**Tests:** 139 passed

---

## 2026-06-07 — Part 8: one-command pipeline runner and review export

**Author:** Cursor agent  
**Type:** Feature (orchestration + review workflow)  
**Summary:** Added `meritranker-ingest` console script, `run-pipeline` orchestration service, and read-only `export-review-items` for flagged final questions. Existing stage commands unchanged.

**Files created:**

- `src/meritranker_data_ingestion/schemas/pipeline.py`
- `src/meritranker_data_ingestion/schemas/review_export.py`
- `src/meritranker_data_ingestion/services/pipeline_runner.py`
- `src/meritranker_data_ingestion/services/review_exporter.py`
- `tests/test_pipeline_runner.py`, `tests/test_review_exporter.py`

**Files updated:**

- `pyproject.toml` — `[project.scripts] meritranker-ingest`
- `src/meritranker_data_ingestion/cli.py` — `run-pipeline`, `export-review-items`
- `src/meritranker_data_ingestion/config.py` — review paths
- `README.md`, `docs/next-agent-handoff.md`, `docs/current-architecture.md`

**Tests:** 153 passed (`uv run pytest -v`)

---

## 2026-06-07 — Visual asset binding and solution splitting hardening

**Author:** Cursor agent  
**Type:** Bug fix / quality hardening  
**Summary:** Deterministic option-image binding (`linked_asset_paths`), visual review statuses, solution anchor splitting for merged S1/S2 lines, mapper diagnostics, precise review export reasons.

**Files created:** `tests/test_visual_hardening.py`

**Files updated:** `question_candidates.py`, `final_question_package.py`, `answer_solution_mapping.py`, `question_candidate_parser.py`, `answer_solution_mapper.py`, `final_question_package_builder.py`, `review_exporter.py`, docs

**Real sample (paper1):** mapped_count 100 (was 99); Q67 image-backed options; Q1 solution no longer contains S2

**Tests:** 163 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 9: ingestion eligibility report and duplicate solution diagnostics

**Author:** Cursor agent  
**Type:** Feature (read-only safety gate)  
**Summary:** Added `build-ingestion-eligibility` command producing eligible/review/blocked JSON artifacts, duplicate solution diagnostics, and markdown report. Optional `--build-eligibility` on `run-pipeline`. Does not perform pattern ingestion.

**Files created:**

- `src/meritranker_data_ingestion/schemas/ingestion_eligibility.py`
- `src/meritranker_data_ingestion/services/ingestion_eligibility_builder.py`
- `tests/test_ingestion_eligibility.py`

**Files updated:** `config.py`, `cli.py`, `pipeline_runner.py`, `test_pipeline_runner.py`, docs

**Real sample:** eligible 48, review 21, blocked 31; duplicate S6/S10 flagged as conflicts

**Tests:** 177 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 10: candidate structural audit and parser hardening

**Author:** Cursor agent  
**Type:** Bug fix / quality hardening  
**Summary:** Same-line option marker splitting, stricter deterministic asset role classification (`question_support_image`, `noise_candidate`), visual-intent eligibility gating, structural audit artifact, and precise review reasons. No source text mutation.

**Files created:**

- `src/meritranker_data_ingestion/services/option_marker_splitter.py`
- `src/meritranker_data_ingestion/services/visual_intent_detector.py`
- `src/meritranker_data_ingestion/services/asset_role_classifier.py`
- `src/meritranker_data_ingestion/services/candidate_structure_auditor.py`
- `tests/test_candidate_structure_hardening.py`

**Files updated:** `question_candidate_parser.py`, `question_candidates.py`, `ingestion_eligibility_builder.py`, `review_exporter.py`, `config.py`, `test_visual_hardening.py`, docs

**New artifact:** `questions/question-structure-audit.json`

**Real sample (paper1):** Q29 → 4 options after split; Q19/Q100 trailing images unbound; Q31–Q40 `review_required`; Q41–Q50 support images; Q67 image options preserved; Q52 remains blocked

**Tests:** 189 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 11: cross-PDF report reconciliation and quality gate

**Author:** Cursor agent  
**Type:** Quality gate / reporting hardening  
**Summary:** Fixed candidate report counters (noise, visual, option metrics) derived from actual candidates; added `artifact_reconciler` service, `reconcile-artifacts` CLI, pipeline `--reconcile-artifacts` / `--strict-quality-gate` flags, and `diagnostics/artifact-reconciliation.json`.

**Files created:**

- `src/meritranker_data_ingestion/services/candidate_report_metrics.py`
- `src/meritranker_data_ingestion/services/artifact_reconciler.py`
- `src/meritranker_data_ingestion/schemas/artifact_reconciliation.py`
- `tests/test_artifact_reconciliation.py`

**Files updated:** `question_candidate_parser.py`, `question_candidates.py`, `pipeline_runner.py`, `cli.py`, `config.py`, `test_pipeline_runner.py`, docs

**Tests:** 198 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 12: PatternQuestionInputPackage handoff builder

**Author:** Cursor agent  
**Type:** Feature (handoff package only — not pattern ingestion)  
**Summary:** Added source-faithful `pattern-input/` handoff package with export modes, eligibility/quality-gate safety checks, visual asset preservation, and `build-pattern-input` CLI/pipeline flag.

**Files created:**

- `src/meritranker_data_ingestion/schemas/pattern_question_input.py`
- `src/meritranker_data_ingestion/services/pattern_question_input_builder.py`
- `tests/test_pattern_question_input_builder.py`

**Files updated:** `config.py`, `cli.py`, `pipeline_runner.py`, `test_pipeline_runner.py`, docs

**Tests:** 212 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 13A: Evidence extraction layer (Marker + Azure DI)

**Author:** Cursor agent  
**Type:** Feature (evidence extraction only — no semantic binding)  
**Summary:** Added Azure Document Intelligence layout adapter, extractor orchestration (`marker` / `azure-di` / `both`), `extractor-manifest.json`, and Azure evidence artifacts under `extractors/azure-di/`. Marker remains default; existing pipeline behavior unchanged.

**Files created:**

- `src/meritranker_data_ingestion/schemas/extractor.py`
- `src/meritranker_data_ingestion/services/azure_di_client.py`
- `src/meritranker_data_ingestion/services/azure_document_intelligence_adapter.py`
- `src/meritranker_data_ingestion/services/extractor_orchestrator.py`
- `tests/test_azure_document_intelligence_adapter.py`
- `tests/test_extractor_orchestrator.py`

**Files updated:** `config.py`, `extraction_package.py`, `cli.py`, `pyproject.toml`, `test_cli_path_validation.py`, docs

**Decision:** DEC-024 (supersedes DEC-010 primary-extractor scope)

**Tests:** 222 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 13B: Unified evidence normalization layer

**Author:** Cursor agent  
**Type:** Feature (normalization only — no semantic binding)  
**Summary:** Added canonical `DocumentEvidencePackage` schema, `normalize-evidence` CLI, extractor comparison diagnostics, partial extractor handling, and optional `--normalize-evidence` pipeline flag. Prepare `--extractor both` now succeeds partially when one extractor succeeds.

**Files created:**

- `src/meritranker_data_ingestion/schemas/document_evidence.py`
- `src/meritranker_data_ingestion/services/evidence_role_hints.py`
- `src/meritranker_data_ingestion/services/document_evidence_normalizer.py`
- `tests/test_document_evidence_normalizer.py`

**Files updated:** `config.py`, `cli.py`, `pipeline_runner.py`, `extractor_orchestrator.py`, `test_extractor_orchestrator.py`, docs

**Decision:** DEC-025

**Tests:** 232 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 13C: Source-grounded semantic binder v1

**Author:** Cursor agent  
**Type:** Feature (semantic binding artifacts only — not final package replacement)  
**Summary:** Added LLM provider abstraction, semantic binder, source-grounded validator, `bind-semantically` CLI, optional pipeline flags, and cache. Validates every field against evidence line IDs; rejects hallucinations.

**Files created:**

- `src/meritranker_data_ingestion/schemas/semantic_binding.py`
- `src/meritranker_data_ingestion/services/llm_provider.py`
- `src/meritranker_data_ingestion/services/semantic_binder.py`
- `src/meritranker_data_ingestion/services/semantic_binding_validator.py`
- `tests/test_semantic_binder.py`

**Files updated:** `config.py`, `cli.py`, `pipeline_runner.py`, docs

**Decision:** DEC-026

**Tests:** 247 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 13D: Real LLM semantic-binder evaluation + answer-key hardening

**Author:** Cursor agent  
**Type:** Feature (semantic binder hardening — not final package replacement)  
**Summary:** Improved semantic prompts, anchor-aware chunking, deterministic answer-key evidence extractor, evaluation reports, OpenAI-compatible provider robustness (timeout, retries, JSON parsing), and CLI flags (`--eval-only`, `--include-answer-key-evidence`, `--timeout-seconds`). Mock SSC run: 48 items, 30 with 4+ options, 48 answers from key evidence, 0 hallucinations.

**Files created:**

- `src/meritranker_data_ingestion/services/answer_key_evidence_extractor.py`
- `src/meritranker_data_ingestion/services/semantic_binding_evaluation.py`
- `tests/test_semantic_binder_13d.py`

**Files updated:**

- `src/meritranker_data_ingestion/schemas/semantic_binding.py` — evaluation report schema
- `src/meritranker_data_ingestion/services/llm_provider.py` — provider hardening, mock answer-key enrichment
- `src/meritranker_data_ingestion/services/semantic_binder.py` — smart chunking, evaluation, answer-key enrichment
- `src/meritranker_data_ingestion/config.py`, `cli.py`, docs

**Decision:** DEC-027

**Tests:** 258 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 13E: Real-provider evaluation harness + provider hardening

**Author:** Cursor agent  
**Type:** Feature (evaluation harness — not final package replacement)  
**Summary:** Added `AzureOpenAIProvider`, full-URL OpenAI-compatible mode, `evaluate-semantic-binding` CLI, configurable quality thresholds, deterministic vs semantic comparison, pipeline flag completion (`--include-answer-key-evidence`, `--timeout-seconds`), prompt version in cache hash.

**Files created:**

- `tests/test_semantic_binder_13e.py`

**Files updated:**

- `src/meritranker_data_ingestion/services/llm_provider.py` — Azure provider, shared HTTP base
- `src/meritranker_data_ingestion/services/semantic_binding_evaluation.py` — thresholds, comparison
- `src/meritranker_data_ingestion/schemas/semantic_binding.py` — threshold/comparison schemas
- `src/meritranker_data_ingestion/services/semantic_binder.py` — `evaluate_semantic_binding_package`
- `src/meritranker_data_ingestion/services/pipeline_runner.py`, `cli.py`, `config.py`, docs

**Decision:** DEC-028

**Tests:** 268 passed (`uv run pytest -v`)

---

## 2026-06-07 — Config import regression fix

**Author:** Cursor agent  
**Type:** Bug fix (import stability)  
**Summary:** Restored missing `config.py` constants (Azure DI, evidence, semantic binding, pattern-input, artifact reconciliation) accidentally truncated. Simplified `services/__init__.py` to avoid eager imports. Added `tests/test_import_smoke.py`.

**Root cause:** `config.py` was reduced to Part 11 constants only; Part 13A–13E constants (`AZURE_DI_CONTENT_MD_NAME`, etc.) were missing, breaking CLI import chain via `services/__init__.py` → `MarkerAdapter` → `extraction_package`.

**Tests:** 281 passed (`uv run pytest -v`)

---

## 2026-06-07 — LLM provider URL fix, .env loading, preflight

**Author:** Cursor agent  
**Type:** Bug fix (provider configuration)  
**Summary:** Fixed Azure OpenAI URL construction (`/v1/chat/completions` for OpenAI-compatible; stable deployment URL for Azure), added `MERITRANKER_BINDER_CHAT_COMPLETIONS_URL` full-URL mode, `.env` loading via `python-dotenv`, provider preflight before semantic chunking, `test-llm-provider` CLI, fail-fast on 401/403/404.

**Root cause of Azure 404:** Incorrect URL paths and missing endpoint normalization; requests never reached the correct deployment.

**Tests:** 297 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 13F: Semantic binding repair (key normalization + source-span resolver)

**Author:** Cursor agent  
**Type:** Feature (deterministic post-LLM quality hardening)  
**Summary:** Added `repair-semantic-binding` CLI and services to normalize option/answer keys, resolve missing `source_spans` from `document-evidence.json`, fill empty option slots from evidence (markdown bullets, table pipe rows, homoglyph A–D), re-validate and re-evaluate. Writes `.repaired.json` artifacts by default. `evaluate-semantic-binding --use-repaired` reads repaired output.

**Root cause addressed:** Real LLM on SSC recovered structure but left empty option keys/text and missing spans; validator counted 321 missing spans and 99 answer-key mismatches against empty placeholders.

**SSC repair result (repaired evaluation):** `source_span_missing` 321→8, `answer_key_not_in_options` 99→4, `accepted` 0→91, `hallucination_suspected` remains 0.

**Files created:**

- `src/meritranker_data_ingestion/services/semantic_key_normalizer.py`
- `src/meritranker_data_ingestion/services/semantic_source_span_resolver.py`
- `src/meritranker_data_ingestion/services/semantic_binding_repair.py`
- `tests/test_semantic_binding_repair_13f.py`

**Files updated:**

- `src/meritranker_data_ingestion/services/semantic_binding_validator.py` — canonical key comparison, skip empty option span requirements
- `src/meritranker_data_ingestion/services/semantic_binder.py` — `--use-repaired` evaluation path
- `src/meritranker_data_ingestion/cli.py`, `config.py`, docs

**Decision:** DEC-030

**Tests:** 313 passed (`uv run pytest -v`; 16 new Part 13F tests)

---

## 2026-06-07 — Part 13G: Edge-case semantic repair + one-command pipeline

**Author:** Cursor agent  
**Type:** Feature (deterministic repair hardening + UX orchestration)  
**Summary:** Extended option parsing for pipe/table rows and same-line embedded labels; neighbouring-line scan; `diagnose-semantic-issues` CLI; `run-semantic-pipeline` one-command PDF→repaired evaluation; `--clean-output` path guard; env-loader test isolation; validator clears stale LLM issues on re-validation.

**SSC repaired evaluation:** `accepted_count` 91, `hallucination_suspected_count` 0, `source_span_missing_count` 8, `answer_key_not_in_options_count` 5 (9 non-accepted items diagnosed with repairability class).

**Files created:**

- `src/meritranker_data_ingestion/services/semantic_embedded_option_parser.py`
- `src/meritranker_data_ingestion/services/semantic_remaining_issue_diagnostician.py`
- `src/meritranker_data_ingestion/services/semantic_pipeline_runner.py`
- `src/meritranker_data_ingestion/services/output_path_guard.py`
- `tests/test_semantic_binding_repair_13g.py`

**Decision:** DEC-031

**Tests:** 326 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 13H: Semantic review patch + final export

**Author:** Cursor agent  
**Type:** Feature (local final export — not pattern ingestion)  
**Summary:** Added `semantic-final/` export layer: accepted-only final JSON, review items export, patch template, manual patch application with validation, `build-semantic-final-export` and `apply-semantic-review-patch` CLI. Integrated optional final export into `run-semantic-pipeline`.

**SSC accepted-only export:** 91 exported, 9 excluded (review/rejected), hallucination 0.

**Files created:**

- `src/meritranker_data_ingestion/schemas/semantic_final_export.py`
- `src/meritranker_data_ingestion/services/semantic_final_export_builder.py`
- `src/meritranker_data_ingestion/services/semantic_review_exporter.py`
- `src/meritranker_data_ingestion/services/semantic_review_patch_applier.py`
- `tests/test_semantic_final_export_13h.py`

**Decision:** DEC-032

**Tests:** 337 passed (`uv run pytest -v`)

---

## 2026-06-07 — Semantic final quality propagation fix

**Author:** Cursor agent  
**Type:** Bug fix (final export report layer)  
**Summary:** Final export report now reads `semantic-binding-evaluation.repaired.json` and propagates `failed`/`warning` quality (hallucination, count mismatch). Added count reconciliation fields, ingestion-safety flags, and standardized `semantic-remaining-issues.json` top-level `items` schema.

**Tests:** 343 passed (`uv run pytest -v`)

---

## 2026-06-07 — Part 13I: Semantic binder stability guard + chunk diagnostics

**Author:** Cursor agent  
**Type:** Feature (diagnostics/stability layer)  
**Summary:** Per-chunk diagnostics (`semantic-binding/chunks/`, `chunk-diagnostics.json`), bad-item quarantine guard (`semantic-bad-items.json`), optional suspicious-chunk replay plan/execute, pipeline integration after bind.

**Decision:** DEC-033

**Tests:** 353 passed (`uv run pytest -v`)

---

## 2026-06-08 — Part 13J: Strict acceptance gate + evidence quality router

**Author:** Cursor agent  
**Type:** Feature (final export safety gate)  
**Summary:** Added deterministic `semantic_final_acceptance_gate` as last gate before `accepted-only` export. Only `final_gate_status == accepted_safe` items export as `ready_for_pattern_input`. Visual/figure-dependent, corrupt evidence, and previously-accepted-but-unsafe items route to review lanes. Fixed duplicate warning noise when no duplicates exist.

**SSC accepted-only export (before → after):**

| Metric | Before | After |
|--------|--------|-------|
| `accepted_count` | 88 | 88 |
| `exported_count` | 88 | **24** |
| `accepted_safe_count` | — | **24** |
| `unsafe_previously_accepted_count` | — | **64** |
| `ready_for_partial_accepted_ingestion` | false | **true** (exported subset clean) |

**Files created:**

- `src/meritranker_data_ingestion/services/semantic_final_acceptance_gate.py`
- `tests/test_semantic_final_acceptance_gate_13j.py`

**Files updated:**

- `src/meritranker_data_ingestion/services/semantic_final_export_builder.py`
- `src/meritranker_data_ingestion/services/semantic_review_exporter.py`
- `src/meritranker_data_ingestion/services/semantic_final_quality.py`
- `src/meritranker_data_ingestion/services/semantic_binding_repair.py`
- `src/meritranker_data_ingestion/services/semantic_binding_evaluation.py`
- `src/meritranker_data_ingestion/services/semantic_binder.py`
- `src/meritranker_data_ingestion/services/semantic_pipeline_runner.py`
- `src/meritranker_data_ingestion/schemas/semantic_final_export.py`
- `src/meritranker_data_ingestion/config.py`
- `src/meritranker_data_ingestion/cli.py`
- `tests/test_semantic_final_export_13h.py`, `tests/test_semantic_stability_13i.py`
- `README.md`, `docs/next-agent-handoff.md`

**Decision:** DEC-034

**Tests:** 364 passed (`uv run pytest -v`)

---

## 2026-06-08 — Part 14A: OCR evidence augmentation + unified final questions JSON

**Author:** Cursor agent  
**Type:** Feature (OCR evidence layer + final questions export)  
**Summary:** Added OCR evidence schema/adapters (Azure DI primary, PaddleOCR optional fallback), page image rendering (optional PyMuPDF), evidence merge layer, extraction capability router, semantic binder merged-evidence selection, unified `final-questions/final-questions.json` export (all items with quality statuses), LLM connection retry resilience, and pipeline CLI integration (`--ocr-engine`, `--auto-profile`, `--build-final-questions-export`).

**Files created:**

- `schemas/ocr_evidence.py`, `schemas/final_questions_export.py`
- `services/ocr_adapter_base.py`, `azure_ocr_adapter.py`, `paddle_ocr_adapter.py`
- `services/pdf_page_renderer.py`, `ocr_evidence_builder.py`, `evidence_merger.py`
- `services/extraction_capability_router.py`, `evidence_resolver.py`
- `services/final_questions_export_builder.py`, `ocr_role_hints.py`
- `tests/test_part_14a.py`

**Decision:** DEC-035

**Tests:** 375 passed (`uv run pytest -v`)

---

## 2026-06-08 — Part 14B: OCR runtime enforcement + numeric option binding + question-only export

**Author:** Cursor agent  
**Type:** Feature (OCR preflight + response-sheet option repair)  
**Summary:** Added OCR runtime preflight (explicit `azure`/`paddle` fails before LLM if deps missing), numeric option normalizer, response-sheet table option parser, question-only export path (`answer_unavailable_export`), fixed `ocr_used` reporting, chosen-option canonical separation.

**Tests:** 390 passed (`uv run pytest -v`)

---

## 2026-06-08 — Batch PDF folder runner + share logs

**Author:** Cursor agent  
**Type:** Feature (batch runner CLI + share-log system)  
**Summary:** Add `run-pdf-folder` CLI to process `input_pdfs/` sequentially. Per PDF: `<stem>.questions.json`, `<stem>.share-log.md`, plus `batch-summary.md` and `batch-run.log.jsonl`.

**Tests:** 420 passed (`uv run pytest -v`)

---

## 2026-06-08 — Part 14D: Azure OCR rendered-image fallback + failed OCR artifacts

**Author:** Cursor agent  
**Type:** Feature (OCR robustness + artifact guarantees)  
**Summary:** Azure OCR falls back from per-page PDF bytes to rendered PNG/JPEG on `UnsupportedContent`/`InvalidContentLength`. Page images saved under `ocr/page-images/`. `ocr-evidence.json` always written on failure. `azure-page-ocr-status.json` includes per-page `attempts[]`.

**Tests:** 412 passed (`uv run pytest -v`); see `tests/test_part_14d.py`

---

## 2026-06-08 — Part 14C: OCR input split + local question window binder

**Author:** Cursor agent  
**Type:** Feature (OCR split/resize + question windows + unsupported layout routing)  
**Summary:** Azure OCR page-split/resize retry on `InvalidContentLength`, explicit OCR failure stop with `--allow-ocr-fallback`, local question-window builder, window-scoped option binding, unsupported response-sheet layout detection/stop.

**Tests:** 401 passed (`uv run pytest -v`)
