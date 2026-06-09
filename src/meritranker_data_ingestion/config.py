"""Application configuration constants."""

import os
from pathlib import Path

PACKAGE_NAME = "meritranker_data_ingestion"
DEFAULT_PARSER_ENGINE = "marker"
PDF_SUFFIX = ".pdf"

# Extraction package layout (under output_dir)
EXTRACTION_PACKAGE_DIR = "extraction_package"
SOURCE_DIR = "source"
ORIGINAL_PDF_NAME = "original.pdf"
MARKER_DIR = "marker"
MARKER_WORK_DIR = "_marker_work"
RAW_MARKDOWN_NAME = "raw.md"
ASSETS_DIR_NAME = "assets"
LOGS_DIR = "logs"
EXTRACTION_LOG_NAME = "extraction.log"
PACKAGE_MANIFEST_NAME = "manifest.json"
CLASSIFIED_DIR = "classified"
CLASSIFIED_LINES_NAME = "lines.json"
CLASSIFIED_BLOCKS_NAME = "blocks.json"
CLASSIFIED_CONTENT_LINES_NAME = "content-lines.json"
CLASSIFICATION_REPORT_NAME = "classification-report.json"
DIAGNOSTICS_DIR = "diagnostics"
RAW_MARKDOWN_INSPECTION_JSON_NAME = "raw-markdown-inspection.json"
RAW_MARKDOWN_INSPECTION_MD_NAME = "raw-markdown-inspection.md"
QUESTION_COVERAGE_JSON_NAME = "question-coverage.json"
QUESTION_COVERAGE_MD_NAME = "question-coverage.md"
QUESTIONS_DIR = "questions"
QUESTION_CANDIDATES_NAME = "question-candidates.json"
QUESTION_CANDIDATE_REPORT_NAME = "question-candidate-report.json"
QUESTION_STRUCTURE_AUDIT_NAME = "question-structure-audit.json"
MAPPINGS_DIR = "mappings"
ANSWER_SOLUTION_MAP_NAME = "answer-solution-map.json"
ANSWER_SOLUTION_REPORT_NAME = "answer-solution-report.json"
CANDIDATES_WITH_MAPPINGS_NAME = "question-candidates-with-mappings.json"
FINAL_DIR = "final"
FINAL_QUESTIONS_NAME = "questions.json"
FINAL_VALIDATION_REPORT_NAME = "validation-report.json"
AUDIT_DIR = "audit"
FINAL_PACKAGE_AUDIT_JSON_NAME = "final-package-audit.json"
FINAL_PACKAGE_AUDIT_MD_NAME = "final-package-audit.md"
REVIEW_DIR = "review"
REVIEW_ITEMS_JSON_NAME = "review-items.json"
REVIEW_ITEMS_MD_NAME = "review-items.md"
ELIGIBILITY_DIR = "eligibility"
INGESTION_ELIGIBILITY_REPORT_NAME = "ingestion-eligibility-report.json"
ELIGIBLE_QUESTIONS_NAME = "eligible-questions.json"
REVIEW_REQUIRED_QUESTIONS_NAME = "review-required-questions.json"
BLOCKED_QUESTIONS_NAME = "blocked-questions.json"
DUPLICATE_SOLUTION_DIAGNOSTICS_NAME = "duplicate-solution-diagnostics.json"
INGESTION_ELIGIBILITY_MD_NAME = "ingestion-eligibility.md"
ARTIFACT_RECONCILIATION_JSON_NAME = "artifact-reconciliation.json"
ARTIFACT_RECONCILIATION_MD_NAME = "artifact-reconciliation.md"
PATTERN_INPUT_DIR = "pattern-input"
PATTERN_INPUT_PACKAGE_NAME = "pattern-question-input-package.json"
ELIGIBLE_PATTERN_INPUT_NAME = "eligible-pattern-input.json"
REVIEW_PATTERN_INPUT_NAME = "review-pattern-input.json"
BLOCKED_PATTERN_INPUT_NAME = "blocked-pattern-input.json"
PATTERN_INPUT_SUMMARY_MD_NAME = "pattern-question-input-summary.md"

# Evidence extractors (Part 13A)
EXTRACTORS_DIR = "extractors"
AZURE_DI_DIR = "azure-di"
EXTRACTOR_MANIFEST_NAME = "extractor-manifest.json"
AZURE_DI_LAYOUT_RESPONSE_NAME = "layout-response.json"
AZURE_DI_CONTENT_MD_NAME = "content.md"
AZURE_DI_PAGES_NAME = "pages.json"
AZURE_DI_LINES_NAME = "lines.json"
AZURE_DI_TABLES_NAME = "tables.json"
AZURE_DI_FIGURES_NAME = "figures.json"
AZURE_DI_PARAGRAPHS_NAME = "paragraphs.json"
AZURE_DI_EXTRACTION_LOG_NAME = "extraction-log.json"
DEFAULT_AZURE_DI_MODEL = "prebuilt-layout"
AZURE_DI_ENDPOINT_ENV = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"
AZURE_DI_KEY_ENV = "AZURE_DOCUMENT_INTELLIGENCE_KEY"

# Normalized document evidence (Part 13B)
EVIDENCE_DIR = "evidence"
DOCUMENT_EVIDENCE_JSON_NAME = "document-evidence.json"
DOCUMENT_EVIDENCE_MD_NAME = "document-evidence.md"
EVIDENCE_SUMMARY_JSON_NAME = "evidence-summary.json"
EXTRACTOR_COMPARISON_JSON_NAME = "extractor-comparison.json"
EXTRACTOR_COMPARISON_MD_NAME = "extractor-comparison.md"
DOCUMENT_EVIDENCE_PACKAGE_VERSION = "1.0"

# Semantic binding (Part 13C–13E)
SEMANTIC_BINDING_DIR = "semantic-binding"
SEMANTIC_BOUND_QUESTIONS_NAME = "semantic-bound-questions.json"
SEMANTIC_BINDING_REPORT_NAME = "semantic-binding-report.json"
SEMANTIC_BINDING_VALIDATION_NAME = "semantic-binding-validation.json"
SEMANTIC_BINDING_SUMMARY_MD_NAME = "semantic-binding-summary.md"
SEMANTIC_BINDING_CACHE_NAME = "binder-cache-manifest.json"
SEMANTIC_BINDING_PROMPTS_DIR = "semantic-binding-prompts"
SEMANTIC_BINDING_PACKAGE_VERSION = "1.0"
SEMANTIC_BINDING_EVALUATION_JSON_NAME = "semantic-binding-evaluation.json"
SEMANTIC_BINDING_EVALUATION_MD_NAME = "semantic-binding-evaluation.md"
SEMANTIC_BOUND_QUESTIONS_REPAIRED_NAME = "semantic-bound-questions.repaired.json"
SEMANTIC_BINDING_REPAIR_REPORT_NAME = "semantic-binding-repair-report.json"
SEMANTIC_BINDING_VALIDATION_REPAIRED_NAME = "semantic-binding-validation.repaired.json"
SEMANTIC_BINDING_EVALUATION_REPAIRED_NAME = "semantic-binding-evaluation.repaired.json"
SEMANTIC_BINDING_REPAIR_SUMMARY_MD_NAME = "semantic-binding-repair-summary.md"
SEMANTIC_BINDING_REMAINING_ISSUES_JSON_NAME = "semantic-remaining-issues.json"
SEMANTIC_BINDING_REMAINING_ISSUES_MD_NAME = "semantic-remaining-issues.md"
SEMANTIC_BINDING_CHUNKS_DIR = "chunks"
SEMANTIC_BINDING_CHUNKS_RAW_DIR = "raw"
SEMANTIC_BINDING_CHUNK_DIAGNOSTICS_NAME = "chunk-diagnostics.json"
SEMANTIC_BINDING_BAD_ITEMS_JSON_NAME = "semantic-bad-items.json"
SEMANTIC_BINDING_BAD_ITEMS_MD_NAME = "semantic-bad-items.md"
SEMANTIC_BINDING_REPLAY_PLAN_NAME = "semantic-chunk-replay-plan.json"

# Semantic final export (Part 13H)
SEMANTIC_FINAL_DIR = "semantic-final"
SEMANTIC_FINAL_PACKAGE_VERSION = "1.0"
SEMANTIC_FINAL_QUESTIONS_NAME = "semantic-final-questions.json"
SEMANTIC_FINAL_REPORT_NAME = "semantic-final-report.json"
SEMANTIC_FINAL_SUMMARY_MD_NAME = "semantic-final-summary.md"
SEMANTIC_FINAL_REVIEW_ITEMS_JSON_NAME = "review-items.json"
SEMANTIC_FINAL_REVIEW_ITEMS_MD_NAME = "review-items.md"
SEMANTIC_FINAL_PATCH_TEMPLATE_NAME = "review-patch.template.json"
SEMANTIC_FINAL_PATCH_APPLIED_NAME = "review-patch.applied.json"
SEMANTIC_FINAL_PATCH_REPORT_NAME = "review-patch-report.json"
SEMANTIC_FINAL_GATE_REPORT_NAME = "final-gate-report.json"
SEMANTIC_FINAL_GATE_SUMMARY_MD_NAME = "final-gate-summary.md"

# OCR evidence (Part 14A)
OCR_DIR = "ocr"
OCR_EVIDENCE_JSON_NAME = "ocr-evidence.json"
OCR_EVIDENCE_MD_NAME = "ocr-evidence.md"
OCR_PAGE_IMAGES_DIR = "page-images"
OCR_ENGINE_LOGS_DIR = "engine-logs"
OCR_EVIDENCE_PACKAGE_VERSION = "1.0"
MERGED_DOCUMENT_EVIDENCE_JSON_NAME = "merged-document-evidence.json"
MERGED_EVIDENCE_SUMMARY_JSON_NAME = "merged-evidence-summary.json"
EXTRACTION_CAPABILITY_PROFILE_NAME = "extraction-capability-profile.json"
QUESTION_WINDOWS_JSON_NAME = "question-windows.json"
QUESTION_WINDOWS_MD_NAME = "question-windows.md"
SOLUTION_WINDOWS_JSON_NAME = "solution-windows.json"
SOLUTION_WINDOWS_MD_NAME = "solution-windows.md"
EVIDENCE_ANSWER_SOLUTION_MAP_JSON_NAME = "answer-solution-map.json"
EVIDENCE_ANSWER_SOLUTION_MAP_MD_NAME = "answer-solution-map.md"
UNSUPPORTED_LAYOUT_REPORT_JSON_NAME = "unsupported-layout-report.json"
UNSUPPORTED_LAYOUT_REPORT_MD_NAME = "unsupported-layout-report.md"
AZURE_PAGE_OCR_STATUS_NAME = "azure-page-ocr-status.json"
FINAL_QUESTIONS_PARTIAL_JSON_NAME = "final-questions.partial.json"
OCR_ENGINE_ENV = "MERITRANKER_OCR_ENGINE"
OCR_AZURE_DI_ENDPOINT_ENV = "MERITRANKER_AZURE_DI_ENDPOINT"
OCR_AZURE_DI_KEY_ENV = "MERITRANKER_AZURE_DI_KEY"
PADDLE_OCR_LANG_ENV = "MERITRANKER_PADDLE_OCR_LANG"
DEFAULT_OCR_ENGINE = "auto"
DEFAULT_PADDLE_OCR_LANG = "auto"

# Final questions export (Part 14A)
FINAL_QUESTIONS_DIR = "final-questions"
FINAL_QUESTIONS_JSON_NAME = "final-questions.json"
FINAL_QUESTIONS_REPORT_NAME = "final-questions-report.json"
FINAL_QUESTIONS_SUMMARY_MD_NAME = "final-questions-summary.md"
FINAL_REVIEW_ITEMS_JSON_NAME = "final-review-items.json"
FINAL_REVIEW_ITEMS_MD_NAME = "final-review-items.md"
FINAL_QUESTIONS_PACKAGE_VERSION = "1.2"

DEFAULT_BINDER_MAX_LINES_PER_CHUNK = 120
DEFAULT_BINDER_TIMEOUT_SECONDS = 120
DEFAULT_BINDER_MAX_RETRIES = 2
SEMANTIC_BINDING_PROMPT_VERSION = "13e-v1"

BINDER_PROVIDER_ENV = "MERITRANKER_BINDER_PROVIDER"
BINDER_MODEL_ENV = "MERITRANKER_BINDER_MODEL"
BINDER_ENDPOINT_ENV = "MERITRANKER_BINDER_ENDPOINT"
BINDER_API_KEY_ENV = "MERITRANKER_BINDER_API_KEY"
BINDER_API_VERSION_ENV = "MERITRANKER_BINDER_API_VERSION"
BINDER_CHAT_COMPLETIONS_URL_ENV = "MERITRANKER_BINDER_CHAT_COMPLETIONS_URL"
DEFAULT_BINDER_PROVIDER = "openai-compatible"
DEFAULT_BINDER_MODEL = ""
BINDER_PROVIDER_AZURE_OPENAI = "azure-openai"
BINDER_PROVIDER_OPENAI_COMPATIBLE = "openai-compatible"
BINDER_PROVIDER_MOCK = "mock"

# Legacy Part 1 manifest at output root (deprecated; package manifest is canonical)
LEGACY_MANIFEST_FILENAME = "extraction-manifest.json"

# Marker subprocess configuration (external install — not a Python dependency)
DEFAULT_MARKER_COMMAND = "marker_single"
MARKER_COMMAND_ENV = "MERITRANKER_MARKER_COMMAND"
MARKER_OUTPUT_DIR_FLAG = "--output_dir"


def get_marker_command_base() -> str:
    """Return configured Marker executable/command (external CLI)."""
    return os.environ.get(MARKER_COMMAND_ENV, DEFAULT_MARKER_COMMAND)


# Marker-primary Azure fallback thresholds (Part 14Y)
MARKER_FALLBACK_MIN_LINES = 20
MARKER_FALLBACK_WINDOW_RATIO = 0.60
MARKER_FALLBACK_OPTION_COVERAGE = 0.60
MARKER_FALLBACK_MISSING_RATIO = 0.30
MARKER_FALLBACK_GOOD_WINDOW_RATIO = 0.85
MARKER_FALLBACK_GOOD_OPTION_COVERAGE = 0.75
MARKER_FALLBACK_GOOD_MISSING_RATIO = 0.10
MARKER_FALLBACK_SCANNED_THRESHOLD = 0.55
MARKER_FALLBACK_HIGH_IMAGE_RATIO = 0.50

# PDF profile sampling (Part 14Y)
PDF_PROFILE_FIRST_PAGES = 3
PDF_PROFILE_MIDDLE_PAGES = 2
PDF_PROFILE_LAST_PAGES = 2

# Semantic vs window coverage (Part 14Z)
SEMANTIC_UNDERBOUND_WINDOW_RATIO = 0.70
SEMANTIC_EXPECTED_COVERAGE_RATIO = 0.90
WINDOW_EXPECTED_COVERAGE_RATIO = 0.85
