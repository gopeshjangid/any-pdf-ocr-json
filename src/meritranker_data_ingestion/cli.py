"""CLI entry point — thin orchestrator, no parsing logic."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from meritranker_data_ingestion.schemas.classification import ClassificationStatus
from meritranker_data_ingestion.services.file_service import PathValidationError
from meritranker_data_ingestion.schemas.answer_solution_mapping import MapperStatus
from meritranker_data_ingestion.schemas.question_candidates import ParseStatus
from meritranker_data_ingestion.services.answer_solution_mapper import (
    MappingError,
    map_answers_solutions_package,
)
from meritranker_data_ingestion.services.markdown_classifier import (
    ClassificationError,
    classify_package,
)
from meritranker_data_ingestion.schemas.extractor import ExtractorType
from meritranker_data_ingestion.schemas.document_evidence import (
    EvidenceExtractionStatus,
    PrimaryExtractorMode,
)
from meritranker_data_ingestion.services.document_evidence_normalizer import (
    EvidenceNormalizationError,
    normalize_evidence_package,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingStatus,
)
from meritranker_data_ingestion.env_loader import load_dotenv_if_present
from meritranker_data_ingestion.services.llm_provider import (
    LlmProviderError,
    LlmProviderPreflightError,
    probe_llm_provider,
)
from meritranker_data_ingestion.services.semantic_binder import (
    SemanticBindingError,
    bind_semantically_package,
    evaluate_binder_trigger,
    evaluate_semantic_binding_package,
)
from meritranker_data_ingestion.services.semantic_binding_repair import (
    SemanticBindingRepairError,
    repair_semantic_binding_package,
)
from meritranker_data_ingestion.services.semantic_remaining_issue_diagnostician import (
    SemanticRemainingIssuesError,
    diagnose_semantic_remaining_issues,
)
from meritranker_data_ingestion.services.semantic_pipeline_runner import (
    SemanticPipelineError,
    SemanticPipelineOptions,
    run_semantic_pipeline,
)
from meritranker_data_ingestion.services.semantic_final_export_builder import (
    SemanticFinalExportError,
    build_semantic_final_export,
)
from meritranker_data_ingestion.services.semantic_review_exporter import (
    SemanticReviewExportError,
    export_semantic_review_items,
)
from meritranker_data_ingestion.services.semantic_review_patch_applier import (
    SemanticReviewPatchError,
    apply_semantic_review_patch,
)
from meritranker_data_ingestion.services.semantic_bad_item_guard import (
    SemanticBadItemGuardError,
    apply_semantic_bad_item_guard,
)
from meritranker_data_ingestion.services.semantic_chunk_replay import (
    SemanticChunkReplayError,
    replay_semantic_chunks,
)
from meritranker_data_ingestion.services.ocr_evidence_builder import (
    OcrEvidenceError,
    build_ocr_evidence_package,
)
from meritranker_data_ingestion.services.evidence_merger import (
    EvidenceMergerError,
    merge_evidence_package,
)
from meritranker_data_ingestion.services.extraction_capability_router import (
    profile_extraction_capability,
)
from meritranker_data_ingestion.services.final_questions_export_builder import (
    FinalQuestionsExportError,
    build_final_questions_export,
)
from meritranker_data_ingestion.services.finalization_replay import (
    FinalizationReplayError,
    replay_finalization,
)
from meritranker_data_ingestion.services.batch_pdf_runner import (
    BatchPdfRunnerError,
    BatchPdfRunnerOptions,
    run_pdf_folder,
)
from meritranker_data_ingestion.services.process_pdfs_runner import (
    ProcessPdfsOptions,
    process_pdfs,
)
from meritranker_data_ingestion.services.review_merge_service import (
    ReviewMergeError,
    merge_reviewed_questions,
    merge_reviewed_questions_folder,
)
from meritranker_data_ingestion.services.public_questions_audit import (
    audit_public_questions_json,
)
from meritranker_data_ingestion.schemas.semantic_final_export import SemanticFinalExportMode
from meritranker_data_ingestion.services.extractor_orchestrator import (
    ExtractorOrchestrator,
    PrepareError,
)
from meritranker_data_ingestion.schemas.final_question_package import FinalizeStatus
from meritranker_data_ingestion.schemas.final_package_audit import AuditStatus
from meritranker_data_ingestion.services.final_package_auditor import (
    AuditError,
    audit_final_package_from_directory,
)
from meritranker_data_ingestion.services.final_question_package_builder import (
    FinalizeError,
    build_final_package_from_directory,
)
from meritranker_data_ingestion.services.question_candidate_parser import (
    QuestionParseError,
    parse_question_candidates_package,
)
from meritranker_data_ingestion.services.question_coverage_diagnostician import (
    CoverageDiagnosticError,
    diagnose_question_coverage_package,
)
from meritranker_data_ingestion.services.pipeline_runner import (
    PipelineError,
    PipelineOptions,
    build_pipeline_summary,
    run_pipeline,
)
from meritranker_data_ingestion.services.raw_markdown_inspector import (
    InspectionError,
    inspect_raw_markdown_package,
)
from meritranker_data_ingestion.services.review_exporter import (
    ReviewExportError,
    export_review_items_package,
)
from meritranker_data_ingestion.schemas.ingestion_eligibility import AnswerMode
from meritranker_data_ingestion.services.ingestion_eligibility_builder import (
    EligibilityBuildError,
    build_ingestion_eligibility_package,
)
from meritranker_data_ingestion.schemas.pipeline import PipelineRunStatus
from meritranker_data_ingestion.schemas.artifact_reconciliation import QualityGateStatus
from meritranker_data_ingestion.services.artifact_reconciler import (
    ArtifactReconciliationError,
    reconcile_artifacts_package,
)
from meritranker_data_ingestion.schemas.pattern_question_input import PatternExportMode
from meritranker_data_ingestion.services.pattern_question_input_builder import (
    PatternInputBuildError,
    build_pattern_question_input_package,
)
from meritranker_data_ingestion.utils.logging import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meritranker-data-ingestion",
        description=(
            "MeritRanker PDF ingestion CLI. "
            "Extraction packages and deterministic markdown classification."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help="Validate PDF, run Marker extraction, and write extraction package + manifest.",
    )
    prepare.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to input PDF file.",
    )
    prepare.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output directory for extraction artifacts.",
    )
    prepare.add_argument(
        "--extractor",
        choices=[e.value for e in ExtractorType],
        default=ExtractorType.MARKER.value,
        help="Evidence extractor: marker (default), azure-di, or both.",
    )
    prepare.add_argument(
        "--azure-di-endpoint",
        default=None,
        help="Azure Document Intelligence endpoint (overrides env).",
    )
    prepare.add_argument(
        "--azure-di-model",
        default=None,
        help="Azure DI model id (default: prebuilt-layout).",
    )
    prepare.add_argument(
        "--force",
        action="store_true",
        help="Rerun even if extraction_package already exists.",
    )

    normalize_evidence = subparsers.add_parser(
        "normalize-evidence",
        help="Normalize Marker/Azure extractor artifacts into canonical evidence (no question JSON).",
    )
    normalize_evidence.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    normalize_evidence.add_argument(
        "--primary-extractor",
        choices=[m.value for m in PrimaryExtractorMode],
        default=PrimaryExtractorMode.AUTO.value,
        help="Primary extractor for canonical line stream (default: auto).",
    )

    bind_sem = subparsers.add_parser(
        "bind-semantically",
        help="Source-grounded semantic binding from document evidence (separate artifacts).",
    )
    bind_sem.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    bind_sem.add_argument(
        "--provider",
        default=None,
        help="LLM provider (mock, openai-compatible, azure-openai). Defaults to env.",
    )
    bind_sem.add_argument(
        "--model",
        default=None,
        help="LLM model id. Defaults to env MERITRANKER_BINDER_MODEL.",
    )
    bind_sem.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
        help="Answer/solution validation mode (default: answer-key-only).",
    )
    bind_sem.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Optional expected question count for validation warnings.",
    )
    bind_sem.add_argument(
        "--force",
        action="store_true",
        help="Re-run binder even when cache matches evidence hash.",
    )
    bind_sem.add_argument(
        "--debug-prompts",
        action="store_true",
        help="Write binder prompts under semantic-binding-prompts/.",
    )
    bind_sem.add_argument(
        "--max-lines-per-chunk",
        type=int,
        default=None,
        help="Max evidence lines per LLM chunk.",
    )
    bind_sem.add_argument(
        "--include-answer-key-evidence",
        action="store_true",
        default=True,
        help="Include deterministic answer-key evidence in prompts (default: true).",
    )
    bind_sem.add_argument(
        "--no-include-answer-key-evidence",
        action="store_false",
        dest="include_answer_key_evidence",
        help="Disable answer-key evidence in prompts.",
    )
    bind_sem.add_argument(
        "--eval-only",
        action="store_true",
        help="Recompute evaluation report from existing semantic-bound output only.",
    )
    bind_sem.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="LLM request timeout in seconds (default: 120).",
    )
    bind_sem.add_argument(
        "--strict-semantic-quality",
        action="store_true",
        help="Exit non-zero when semantic quality_status is failed.",
    )

    test_llm = subparsers.add_parser(
        "test-llm-provider",
        help="Test LLM provider configuration and preflight (no semantic binding).",
    )
    test_llm.add_argument(
        "--provider",
        default=None,
        help="LLM provider (mock, openai-compatible, azure-openai). Defaults to env.",
    )
    test_llm.add_argument(
        "--model",
        default=None,
        help="Model or Azure deployment name. Defaults to env.",
    )
    test_llm.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="LLM request timeout in seconds (default: 120).",
    )

    eval_sem = subparsers.add_parser(
        "evaluate-semantic-binding",
        help="Evaluate semantic binding quality from existing artifacts (no LLM by default).",
    )
    eval_sem.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    eval_sem.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Expected question count for threshold checks.",
    )
    eval_sem.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
        help="Answer mode for validation (default: answer-key-only).",
    )
    eval_sem.add_argument(
        "--strict-semantic-quality",
        action="store_true",
        help="Exit non-zero when quality_status is failed.",
    )
    eval_sem.add_argument(
        "--bind-if-missing",
        action="store_true",
        help="Run bind-semantically if semantic artifacts are missing.",
    )
    eval_sem.add_argument(
        "--provider",
        default=None,
        help="LLM provider when --bind-if-missing is set.",
    )
    eval_sem.add_argument(
        "--model",
        default=None,
        help="LLM model when --bind-if-missing is set.",
    )
    eval_sem.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="LLM timeout when --bind-if-missing is set.",
    )
    eval_sem.add_argument(
        "--use-repaired",
        action="store_true",
        help="Evaluate repaired semantic-binding artifacts (.repaired.json).",
    )

    repair_sem = subparsers.add_parser(
        "repair-semantic-binding",
        help="Deterministic repair: normalize keys, resolve source spans, re-validate (no LLM).",
    )
    repair_sem.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    repair_sem.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
        help="Answer mode for validation (default: answer-key-only).",
    )
    repair_sem.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Expected question count for validation.",
    )
    repair_sem.add_argument(
        "--overwrite-semantic-binding",
        action="store_true",
        help="Overwrite canonical semantic-binding artifacts with repaired output.",
    )

    diag_sem = subparsers.add_parser(
        "diagnose-semantic-issues",
        help="Diagnose remaining non-accepted semantic items (no LLM).",
    )
    diag_sem.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    diag_sem.add_argument(
        "--use-repaired",
        action="store_true",
        default=True,
        help="Read repaired semantic artifacts (default).",
    )
    diag_sem.add_argument(
        "--use-original",
        action="store_true",
        help="Read canonical semantic-bound-questions.json instead of repaired.",
    )
    diag_sem.add_argument(
        "--no-use-repaired",
        action="store_false",
        dest="use_repaired",
        help="Alias for --use-original.",
    )
    diag_sem.add_argument(
        "--context-radius",
        type=int,
        default=5,
        help="Nearby evidence lines to include (default: 5).",
    )

    replay_sem = subparsers.add_parser(
        "replay-semantic-chunks",
        help="Plan or replay suspicious semantic binding chunks only.",
    )
    replay_sem.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    replay_sem.add_argument(
        "--only-suspicious",
        action="store_true",
        default=True,
        help="Select only suspicious chunks from diagnostics (default).",
    )
    replay_sem.add_argument(
        "--all-chunks",
        action="store_true",
        help="Include all chunks in replay plan (not just suspicious).",
    )
    replay_sem.add_argument(
        "--execute",
        action="store_true",
        help="Re-call LLM for selected chunks (default: dry-run plan only).",
    )
    replay_sem.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
    )
    replay_sem.add_argument("--expected-count", type=int, default=None)
    replay_sem.add_argument("--provider", default=None)
    replay_sem.add_argument("--model", default=None)
    replay_sem.add_argument("--timeout-seconds", type=int, default=180)

    guard_sem = subparsers.add_parser(
        "apply-semantic-bad-item-guard",
        help="Quarantine bad semantic items before repair/export (no LLM).",
    )
    guard_sem.add_argument("--package", required=True, type=Path)
    guard_sem.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
    )
    guard_sem.add_argument("--expected-count", type=int, default=None)

    run_sem_pipe = subparsers.add_parser(
        "run-semantic-pipeline",
        help="One-command PDF → evidence → bind → guard → repair → diagnose → evaluate.",
    )
    run_sem_pipe.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to input PDF file.",
    )
    run_sem_pipe.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output folder (all artifacts under output/extraction_package/).",
    )
    run_sem_pipe.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
    )
    run_sem_pipe.add_argument(
        "--expected-count",
        type=int,
        default=None,
    )
    run_sem_pipe.add_argument(
        "--extractor",
        choices=["marker", "azure-di", "both"],
        default="marker",
    )
    run_sem_pipe.add_argument("--provider", default=None)
    run_sem_pipe.add_argument("--model", default=None)
    run_sem_pipe.add_argument(
        "--include-answer-key-evidence",
        action="store_true",
        default=True,
    )
    run_sem_pipe.add_argument(
        "--no-include-answer-key-evidence",
        action="store_false",
        dest="include_answer_key_evidence",
    )
    run_sem_pipe.add_argument("--timeout-seconds", type=int, default=180)
    run_sem_pipe.add_argument("--force", action="store_true")
    run_sem_pipe.add_argument(
        "--clean-output",
        action="store_true",
        help="Delete --output folder before run (refuses dangerous paths).",
    )
    run_sem_pipe.add_argument(
        "--build-semantic-final-export",
        action="store_true",
        help="Build semantic-final export after repair/diagnose.",
    )
    run_sem_pipe.add_argument(
        "--semantic-final-export-mode",
        choices=[m.value for m in SemanticFinalExportMode],
        default=SemanticFinalExportMode.ACCEPTED_ONLY.value,
    )
    run_sem_pipe.add_argument(
        "--generate-review-patch-template",
        action="store_true",
        help="Generate semantic-final/review-patch.template.json.",
    )
    run_sem_pipe.add_argument(
        "--ocr-engine",
        choices=["auto", "azure", "paddle", "none"],
        default="auto",
        help="OCR evidence engine (Part 14A).",
    )
    run_sem_pipe.add_argument(
        "--auto-profile",
        action="store_true",
        help="Auto-select answer mode from extraction capability profile.",
    )
    run_sem_pipe.add_argument(
        "--build-final-questions-export",
        action="store_true",
        help="Build final-questions/final-questions.json (Part 14A).",
    )
    run_sem_pipe.add_argument(
        "--allow-ocr-fallback",
        action="store_true",
        help="Continue Marker-only if explicit OCR engine fails (Part 14C).",
    )
    run_sem_pipe.add_argument(
        "--allow-unsupported-layout",
        action="store_true",
        help="Continue binding on response-sheet/repeated-numbering layouts (Part 14C).",
    )

    run_pdf_folder = subparsers.add_parser(
        "run-pdf-folder",
        help="Batch run PDF folder → per-PDF questions JSON + share log.",
    )
    run_pdf_folder.add_argument(
        "--input-dir",
        required=True,
        type=Path,
        help="Folder containing input PDF files.",
    )
    run_pdf_folder.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Batch output root (one subfolder per PDF).",
    )
    run_pdf_folder.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.AUTO.value,
    )
    run_pdf_folder.add_argument(
        "--extractor",
        choices=["marker", "azure-di", "both"],
        default="marker",
    )
    run_pdf_folder.add_argument(
        "--ocr-engine",
        choices=["auto", "azure", "paddle", "none"],
        default="auto",
    )
    run_pdf_folder.add_argument("--provider", default=None)
    run_pdf_folder.add_argument("--model", default=None)
    run_pdf_folder.add_argument("--timeout-seconds", type=int, default=180)
    run_pdf_folder.add_argument("--expected-count", type=int, default=None)
    run_pdf_folder.add_argument("--max-files", type=int, default=None)
    run_pdf_folder.add_argument("--max-concurrency", type=int, default=1)
    run_pdf_folder.add_argument("--continue-on-error", action="store_true")
    run_pdf_folder.add_argument("--clean-output", action="store_true")
    run_pdf_folder.add_argument("--allow-ocr-fallback", action="store_true")
    run_pdf_folder.add_argument("--allow-unsupported-layout", action="store_true")

    process_pdfs_cmd = subparsers.add_parser(
        "process-pdfs",
        help="Simple workflow: route extractors, export questions + review JSON.",
    )
    process_pdfs_cmd.add_argument("--input-dir", type=Path, default=Path("input_pdfs"))
    process_pdfs_cmd.add_argument("--output-dir", type=Path, default=Path("batch_outputs"))
    process_pdfs_cmd.add_argument(
        "--ready-dir",
        type=Path,
        default=Path("ready_for_question_bank"),
    )
    process_pdfs_cmd.add_argument("--expected-count", type=int, default=100)
    process_pdfs_cmd.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default="auto",
    )
    process_pdfs_cmd.add_argument(
        "--extractor-strategy",
        choices=["auto", "marker_primary", "azure_primary", "dual_debug"],
        default="auto",
    )
    process_pdfs_cmd.add_argument("--provider", default="azure-openai")
    process_pdfs_cmd.add_argument("--model", default=None)
    process_pdfs_cmd.add_argument("--timeout-seconds", type=int, default=180)
    process_pdfs_cmd.add_argument("--continue-on-error", action="store_true", default=True)
    process_pdfs_cmd.add_argument("--allow-auto-fallback", action="store_true", default=True)
    process_pdfs_cmd.add_argument("--enable-llm-window-repair", action="store_true")

    merge_reviewed = subparsers.add_parser(
        "merge-reviewed-questions",
        help="Merge edited review JSON into questions and write final output.",
    )
    merge_reviewed.add_argument("--batch-dir", type=Path, default=Path("batch_outputs"))
    merge_reviewed.add_argument(
        "--ready-dir",
        type=Path,
        default=Path("ready_for_question_bank"),
    )
    merge_reviewed.add_argument("--expected-count", type=int, default=100)
    merge_reviewed.add_argument("--stem", default=None, help="Merge one PDF stem only.")
    merge_reviewed.add_argument(
        "--allow-partial",
        action="store_true",
        help="Copy final JSON to ready_for_question_bank even when non-ready items remain.",
    )

    validate_final = subparsers.add_parser(
        "validate-final-question-json",
        help="Audit public questions JSON contract compliance.",
    )
    validate_final.add_argument("path", type=Path, help="Path to .questions.json file or folder.")
    validate_final.add_argument("--expected-count", type=int, default=None)

    run_ocr = subparsers.add_parser(
        "run-ocr-evidence",
        help="Build OCR evidence package from PDF/page images.",
    )
    run_ocr.add_argument("--package", required=True, type=Path)
    run_ocr.add_argument(
        "--ocr-engine",
        choices=["auto", "azure", "paddle", "none"],
        default="auto",
    )

    merge_ev = subparsers.add_parser(
        "merge-evidence",
        help="Merge Marker document evidence with OCR evidence.",
    )
    merge_ev.add_argument("--package", required=True, type=Path)

    profile_cap = subparsers.add_parser(
        "profile-extraction-capability",
        help="Profile language, answer source, OCR need from merged evidence.",
    )
    profile_cap.add_argument("--package", required=True, type=Path)

    build_fq = subparsers.add_parser(
        "build-final-questions-export",
        help="Build unified final-questions.json (all items with statuses).",
    )
    build_fq.add_argument("--package", required=True, type=Path)
    build_fq.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.AUTO.value,
        help="Answer mode for final gate (default: auto from extraction profile).",
    )

    replay_fin = subparsers.add_parser(
        "replay-finalization",
        help="Replay question windows, option parsing, join, gate, and export (no OCR/LLM).",
    )
    replay_fin.add_argument("--package", required=True, type=Path)
    replay_fin.add_argument("--expected-count", type=int, default=None)
    replay_fin.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
    )
    replay_fin.add_argument(
        "--no-share-log",
        action="store_true",
        help="Skip share-log refresh.",
    )

    build_sem_final = subparsers.add_parser(
        "build-semantic-final-export",
        help="Build accepted-only or patched semantic final JSON (no LLM).",
    )
    build_sem_final.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    build_sem_final.add_argument(
        "--export-mode",
        choices=[m.value for m in SemanticFinalExportMode],
        default=SemanticFinalExportMode.ACCEPTED_ONLY.value,
    )
    build_sem_final.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
    )
    build_sem_final.add_argument(
        "--generate-review-artifacts",
        action="store_true",
        help="Also write review-items and patch template.",
    )

    apply_patch = subparsers.add_parser(
        "apply-semantic-review-patch",
        help="Apply human-reviewed patch to semantic final export.",
    )
    apply_patch.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    apply_patch.add_argument(
        "--patch",
        required=True,
        type=Path,
        help="Path to review-patch.json inside package.",
    )
    apply_patch.add_argument(
        "--answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
    )
    apply_patch.add_argument(
        "--allow-accepted-item-patch",
        action="store_true",
        help="Allow patching items already accepted by semantic validator.",
    )

    export_review = subparsers.add_parser(
        "export-semantic-review-items",
        help="Export review/rejected items and patch template.",
    )
    export_review.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    export_review.add_argument(
        "--generate-patch-template",
        action="store_true",
        default=True,
    )
    export_review.add_argument(
        "--no-generate-patch-template",
        action="store_false",
        dest="generate_patch_template",
    )

    classify = subparsers.add_parser(
        "classify-markdown",
        help="Classify marker/raw.md into line/block records (no question JSON).",
    )
    classify.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )

    parse_q = subparsers.add_parser(
        "parse-question-candidates",
        help="Parse question candidate shells from classified lines/blocks (no answer mapping).",
    )
    parse_q.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )

    map_as = subparsers.add_parser(
        "map-answers-solutions",
        help="Map explicit answers/solutions to question candidates (not final JSON).",
    )
    map_as.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )

    build_final = subparsers.add_parser(
        "build-final-package",
        help="Build source-faithful final question package (not pattern ingestion).",
    )
    build_final.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )

    audit_final = subparsers.add_parser(
        "audit-final-package",
        help="Audit final question package quality (read-only, no mutations).",
    )
    audit_final.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    audit_final.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Optional expected question count for comparison.",
    )

    inspect_md = subparsers.add_parser(
        "inspect-raw-markdown",
        help="Inspect raw markdown diagnostics (read-only).",
    )
    inspect_md.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )

    diagnose_cov = subparsers.add_parser(
        "diagnose-question-coverage",
        help="Diagnose question number coverage gaps (read-only).",
    )
    diagnose_cov.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    diagnose_cov.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Optional expected question count.",
    )

    run_pipe = subparsers.add_parser(
        "run-pipeline",
        help="Run full ingestion pipeline (prepare through review export).",
    )
    run_pipe.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to input PDF file.",
    )
    run_pipe.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output directory for extraction artifacts.",
    )
    run_pipe.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Optional expected question count for audit and diagnosis.",
    )
    run_pipe.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip audit-final-package stage.",
    )
    run_pipe.add_argument(
        "--skip-inspection",
        action="store_true",
        help="Skip inspect-raw-markdown stage.",
    )
    run_pipe.add_argument(
        "--skip-diagnosis",
        action="store_true",
        help="Skip diagnose-question-coverage stage.",
    )
    run_pipe.add_argument(
        "--force",
        action="store_true",
        help="Rerun even if extraction_package already exists.",
    )
    run_pipe.add_argument(
        "--no-stop-on-failure",
        action="store_true",
        help="Continue pipeline after a stage failure (default: stop).",
    )
    run_pipe.add_argument(
        "--build-eligibility",
        action="store_true",
        help="Run build-ingestion-eligibility after review export.",
    )
    run_pipe.add_argument(
        "--answer-mode",
        choices=["required", "optional", "question-only"],
        default="required",
        help="Answer expectation for eligibility (default: required).",
    )
    run_pipe.add_argument(
        "--reconcile-artifacts",
        action="store_true",
        help="Run artifact reconciliation after pipeline (auto with --build-eligibility).",
    )
    run_pipe.add_argument(
        "--strict-quality-gate",
        action="store_true",
        help="Exit non-zero unless quality_gate_status is passed.",
    )
    run_pipe.add_argument(
        "--normalize-evidence",
        action="store_true",
        help="Run normalize-evidence after prepare (optional; not default).",
    )
    run_pipe.add_argument(
        "--use-semantic-binder",
        action="store_true",
        help="Run semantic binder when quality trigger fires (requires normalize-evidence).",
    )
    run_pipe.add_argument(
        "--force-semantic-binder",
        action="store_true",
        help="Force semantic binder even if quality trigger does not fire.",
    )
    run_pipe.add_argument(
        "--semantic-binder-provider",
        default=None,
        help="Semantic binder LLM provider override.",
    )
    run_pipe.add_argument(
        "--semantic-binder-model",
        default=None,
        help="Semantic binder LLM model override.",
    )
    run_pipe.add_argument(
        "--semantic-answer-mode",
        choices=[m.value for m in SemanticBinderAnswerMode],
        default=SemanticBinderAnswerMode.ANSWER_KEY_ONLY.value,
        help="Semantic binder answer mode (default: answer-key-only).",
    )
    run_pipe.add_argument(
        "--include-answer-key-evidence",
        action="store_true",
        default=True,
        help="Include answer-key evidence in semantic binder prompts (default: true).",
    )
    run_pipe.add_argument(
        "--no-include-answer-key-evidence",
        action="store_false",
        dest="include_answer_key_evidence",
        help="Disable answer-key evidence in semantic binder prompts.",
    )
    run_pipe.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="Semantic binder LLM timeout in seconds (default: 120).",
    )
    run_pipe.add_argument(
        "--build-pattern-input",
        action="store_true",
        help="Build pattern-ingestion handoff package (requires eligibility).",
    )
    run_pipe.add_argument(
        "--pattern-export-mode",
        choices=["eligible-only", "include-review", "include-blocked", "all"],
        default="eligible-only",
        help="Pattern input export filter (default: eligible-only).",
    )
    run_pipe.add_argument(
        "--allow-failed-quality-gate-for-pattern",
        action="store_true",
        help="Allow pattern input build when quality_gate_status is failed.",
    )

    build_pattern = subparsers.add_parser(
        "build-pattern-input",
        help="Build source-faithful pattern-ingestion handoff package (not ingestion).",
    )
    build_pattern.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    build_pattern.add_argument(
        "--export-mode",
        choices=["eligible-only", "include-review", "include-blocked", "all"],
        default="eligible-only",
        help="Export filter (default: eligible-only).",
    )
    build_pattern.add_argument(
        "--allow-missing-eligibility",
        action="store_true",
        help="Allow build when eligibility artifacts are missing (exports as review hold).",
    )
    build_pattern.add_argument(
        "--allow-failed-quality-gate",
        action="store_true",
        help="Allow build when quality_gate_status is failed.",
    )

    reconcile = subparsers.add_parser(
        "reconcile-artifacts",
        help="Reconcile extraction package artifacts and write quality gate report.",
    )
    reconcile.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )

    build_elig = subparsers.add_parser(
        "build-ingestion-eligibility",
        help="Build ingestion eligibility report (read-only safety gate).",
    )
    build_elig.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    build_elig.add_argument(
        "--answer-mode",
        choices=["required", "optional", "question-only"],
        default="required",
        help="Answer expectation for eligibility (default: required).",
    )

    export_review = subparsers.add_parser(
        "export-review-items",
        help="Export flagged final items for manual review (read-only).",
    )
    export_review.add_argument(
        "--package",
        required=True,
        type=Path,
        help="Path to extraction_package directory.",
    )
    export_review.add_argument(
        "--include-validated",
        action="store_true",
        help="Include clean validated items in export.",
    )
    return parser


def cmd_prepare(args: argparse.Namespace) -> int:
    logger = setup_logging()
    orchestrator = ExtractorOrchestrator()

    try:
        result = orchestrator.prepare(
            args.input,
            args.output,
            extractor=ExtractorType(args.extractor),
            azure_endpoint=args.azure_di_endpoint,
            azure_model=args.azure_di_model,
            force=args.force,
        )
    except (PathValidationError, PrepareError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    manifest_path = args.output / "extraction_package" / "extractors" / "extractor-manifest.json"

    if result.succeeded and result.partial:
        logger.warning("Partial evidence extraction: %s", manifest_path)
        for warning in result.extractor_manifest.warnings:
            logger.warning("Warning: %s", warning)
    elif result.succeeded:
        logger.info("Evidence extraction succeeded: %s", manifest_path)
    else:
        logger.error("Evidence extraction failed: %s", manifest_path)
        for error in result.extractor_manifest.errors:
            print(f"Error: {error}", file=sys.stderr)

    if result.package_manifest is not None:
        print(json.dumps(json.loads(result.package_manifest.model_dump_json()), indent=2))
    else:
        print(json.dumps(json.loads(result.extractor_manifest.model_dump_json()), indent=2))

    return 0 if result.succeeded else 1


def cmd_bind_semantically(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        result = bind_semantically_package(
            args.package,
            provider=args.provider,
            model=args.model,
            answer_mode=SemanticBinderAnswerMode(args.answer_mode),
            expected_count=args.expected_count,
            force=args.force,
            debug_prompts=args.debug_prompts,
            max_lines_per_chunk=args.max_lines_per_chunk or 120,
            include_answer_key_evidence=args.include_answer_key_evidence,
            timeout_seconds=args.timeout_seconds,
            eval_only=args.eval_only,
        )
    except (PathValidationError, SemanticBindingError, LlmProviderError, LlmProviderPreflightError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    package = result.package
    validation = package.validation_summary

    if package.status == SemanticBindingStatus.SUCCEEDED:
        logger.info("Semantic binding succeeded: %s", result.output_path)
    elif package.status == SemanticBindingStatus.WARNING:
        logger.warning("Semantic binding completed with warnings: %s", result.output_path)
    else:
        logger.error("Semantic binding failed: %s", result.output_path)
        for error in package.errors:
            print(f"Error: {error}", file=sys.stderr)

    eval_path = result.evaluation_path
    evaluation_data: dict | None = None
    if eval_path.exists():
        try:
            evaluation_data = json.loads(eval_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            evaluation_data = None

    summary = {
        "status": package.status.value,
        "from_cache": result.from_cache,
        "provider": package.binder_provider,
        "model": package.binder_model,
        "binder_provider": package.binder_provider,
        "binder_model": package.binder_model,
        "answer_mode": package.answer_mode.value,
        "cache_hit": result.from_cache,
        "chunk_count": evaluation_data.get("estimated_chunk_count", 0) if evaluation_data else 0,
        "semantic_item_count": validation.total_items,
        "total_items": validation.total_items,
        "accepted_count": validation.accepted_count,
        "review_required_count": validation.review_required_count,
        "rejected_count": validation.rejected_count,
        "questions_with_4_options_count": (
            evaluation_data.get("questions_with_4_options_count", 0) if evaluation_data else 0
        ),
        "answer_available_count": (
            evaluation_data.get("answer_available_count", 0) if evaluation_data else 0
        ),
        "hallucination_suspected_count": validation.hallucination_suspected_count,
        "source_span_missing_count": validation.source_span_missing_count,
        "quality_status": (
            evaluation_data.get("quality_status", "warning") if evaluation_data else "warning"
        ),
        "semantic_bound_questions": str(result.output_path),
        "validation_report": str(result.validation_path),
        "binding_report": str(result.report_path),
        "evaluation_report": str(result.evaluation_path),
        "warnings": package.warnings,
        "errors": package.errors,
    }
    if evaluation_data is not None:
        summary["evaluation"] = evaluation_data
    print(json.dumps(summary, indent=2))
    if package.status == SemanticBindingStatus.FAILED:
        return 1
    if args.strict_semantic_quality and summary["quality_status"] == "failed":
        return 1
    return 0


def cmd_test_llm_provider(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = probe_llm_provider(
            provider=args.provider,
            model=args.model,
            timeout_seconds=args.timeout_seconds or 120,
        )
    except (LlmProviderError, LlmProviderPreflightError) as exc:
        payload: dict = {"status": "failed", "error": str(exc)}
        if isinstance(exc, LlmProviderPreflightError) and exc.diagnostics:
            payload["diagnostics"] = exc.diagnostics
        print(json.dumps(payload, indent=2))
        return 1

    print(json.dumps(result, indent=2))
    return 0


def cmd_diagnose_semantic_issues(args: argparse.Namespace) -> int:
    setup_logging()
    use_repaired = args.use_repaired and not args.use_original
    try:
        result = diagnose_semantic_remaining_issues(
            args.package,
            use_repaired=use_repaired,
            context_radius=args.context_radius,
        )
    except (PathValidationError, SemanticRemainingIssuesError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "total_items": result.report.total_items,
        "non_accepted_count": result.report.non_accepted_count,
        "json_path": str(result.json_path),
        "md_path": str(result.md_path),
        "items": [item.model_dump() for item in result.report.items],
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_run_semantic_pipeline(args: argparse.Namespace) -> int:
    setup_logging()
    if args.clean_output:
        print(f"Cleaning output folder: {args.output.resolve()}", file=sys.stderr)

    try:
        result = run_semantic_pipeline(
            SemanticPipelineOptions(
                input_pdf=args.input,
                output_dir=args.output,
                expected_count=args.expected_count,
                answer_mode=SemanticBinderAnswerMode(args.answer_mode),
                extractor=args.extractor,
                provider=args.provider,
                model=args.model,
                include_answer_key_evidence=args.include_answer_key_evidence,
                timeout_seconds=args.timeout_seconds,
                force=args.force,
                clean_output=args.clean_output,
                build_semantic_final_export_flag=args.build_semantic_final_export,
                semantic_final_export_mode=args.semantic_final_export_mode,
                generate_review_patch_template=args.generate_review_patch_template,
                ocr_engine=args.ocr_engine,
                auto_profile=args.auto_profile,
                build_final_questions_export=args.build_final_questions_export,
                allow_ocr_fallback=args.allow_ocr_fallback,
                allow_unsupported_layout=args.allow_unsupported_layout,
            ),
        )
    except (PathValidationError, SemanticPipelineError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "source_file_name": result.source_file_name,
        "output_root": str(result.output_root),
        "package_dir": str(result.package_dir),
        "expected_count": result.expected_count,
        "semantic_item_count": result.semantic_item_count,
        "count_match": result.count_match,
        "accepted_count": result.accepted_count,
        "review_required_count": result.review_required_count,
        "rejected_count": result.rejected_count,
        "questions_with_4_options_count": result.questions_with_4_options_count,
        "answer_available_count": result.answer_available_count,
        "source_span_missing_count": result.source_span_missing_count,
        "answer_key_not_in_options_count": result.answer_key_not_in_options_count,
        "hallucination_suspected_count": result.hallucination_suspected_count,
        "semantic_quality_status": result.semantic_quality_status,
        "final_export_quality_status": result.final_export_quality_status,
        "exported_count": result.exported_count,
        "excluded_count": result.excluded_count,
        "ready_for_full_paper_ingestion": result.ready_for_full_paper_ingestion,
        "ready_for_partial_accepted_ingestion": result.ready_for_partial_accepted_ingestion,
        "bad_item_count": result.bad_item_count,
        "quarantined_item_count": result.quarantined_item_count,
        "accepted_safe_count": result.accepted_safe_count,
        "unsafe_previously_accepted_count": result.unsafe_previously_accepted_count,
        "quality_status": result.quality_status,
        "ocr_line_count": result.ocr_line_count,
        "merged_evidence_line_count": result.merged_evidence_line_count,
        "effective_answer_mode": result.effective_answer_mode,
        "total_questions_detected": result.total_questions_detected,
        "final_questions_accepted_safe_count": result.final_questions_accepted_safe_count,
        "artifact_paths": result.artifact_paths,
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_process_pdfs(args: argparse.Namespace) -> int:
    setup_logging()
    load_dotenv_if_present()
    try:
        result = process_pdfs(
            ProcessPdfsOptions(
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                ready_dir=args.ready_dir,
                expected_count=args.expected_count,
                answer_mode=args.answer_mode,
                extractor_strategy=args.extractor_strategy,
                provider=args.provider,
                model=args.model,
                timeout_seconds=args.timeout_seconds,
                continue_on_error=args.continue_on_error,
                allow_auto_fallback=args.allow_auto_fallback,
                enable_llm_window_repair=args.enable_llm_window_repair,
            ),
        )
    except (PathValidationError, BatchPdfRunnerError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "output_dir": str(result.output_dir),
        "summary_path": str(result.summary_path),
        "processed": len(result.items),
        "succeeded": result.succeeded,
        "failed": result.failed,
        "items": [
            {
                "stem": item.pdf_stem,
                "run_status": item.run_status,
                "ready_percentage": item.metrics.get("ready_percentage"),
                "questions_json": str(item.questions_json_path) if item.questions_json_path else None,
                "review_json": str(item.review_json_path) if item.review_json_path else None,
                "summary_md": str(item.summary_path) if item.summary_path else None,
            }
            for item in result.items
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.failed == 0 else 1


def cmd_merge_reviewed_questions(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        if args.stem:
            batch_dir = args.batch_dir.resolve()
            stem = args.stem
            result = merge_reviewed_questions(
                batch_dir / stem / f"{stem}.questions.json",
                batch_dir / stem / f"{stem}.review.json",
                ready_dir=args.ready_dir,
                expected_count=args.expected_count,
                allow_partial=args.allow_partial,
            )
            results = [result]
        else:
            results = merge_reviewed_questions_folder(
                args.batch_dir,
                ready_dir=args.ready_dir,
                expected_count=args.expected_count,
                allow_partial=args.allow_partial,
            )
    except (PathValidationError, ReviewMergeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not results:
        print(json.dumps({"merged": 0, "message": "no review.json files found"}, indent=2))
        return 0

    payload = {
        "merged": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "items": [
            {
                "stem": r.stem,
                "passed": r.passed,
                "merged_count": r.merged_count,
                "copied_to_ready_dir": r.copied_to_ready_dir,
                "reason_if_not_copied": r.reason_if_not_copied,
                "final_questions": str(r.final_questions_path) if r.final_questions_path else None,
                "ready_copy": str(r.ready_copy_path) if r.ready_copy_path else None,
                "errors": r.errors,
            }
            for r in results
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["failed"] == 0 else 1


def cmd_validate_final_question_json(args: argparse.Namespace) -> int:
    setup_logging()
    path = args.path.resolve()
    targets: list[Path] = []
    if path.is_dir():
        targets = sorted(path.rglob("*.questions.json")) + sorted(path.rglob("*.final.questions.json"))
    else:
        targets = [path]

    if not targets:
        print(f"Error: No questions JSON found at {path}", file=sys.stderr)
        return 1

    all_passed = True
    reports = []
    for target in targets:
        audit = audit_public_questions_json(target, expected_count=args.expected_count)
        reports.append({
            "path": str(target),
            "passed": audit.passed,
            "errors": audit.errors,
        })
        if not audit.passed:
            all_passed = False

    print(json.dumps({"passed": all_passed, "reports": reports}, indent=2))
    return 0 if all_passed else 1


def cmd_run_pdf_folder(args: argparse.Namespace) -> int:
    setup_logging()
    if args.clean_output:
        print(f"Cleaning batch output folder: {args.output_dir.resolve()}", file=sys.stderr)

    try:
        result = run_pdf_folder(
            BatchPdfRunnerOptions(
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                answer_mode=args.answer_mode,
                extractor=args.extractor,
                ocr_engine=args.ocr_engine,
                provider=args.provider,
                model=args.model,
                timeout_seconds=args.timeout_seconds,
                expected_count=args.expected_count,
                continue_on_error=args.continue_on_error,
                clean_output=args.clean_output,
                max_files=args.max_files,
                max_concurrency=args.max_concurrency,
                allow_ocr_fallback=args.allow_ocr_fallback,
                allow_unsupported_layout=args.allow_unsupported_layout,
            ),
        )
    except (PathValidationError, BatchPdfRunnerError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "output_dir": str(result.output_dir),
        "summary_path": str(result.summary_path),
        "log_path": str(result.log_path),
        "processed": len(result.items),
        "passed": result.succeeded,
        "failed": result.failed,
        "items": [
            {
                "pdf": str(item.pdf_path),
                "stem": item.pdf_stem,
                "run_status": item.run_status,
                "quality_verdict": item.quality_verdict,
                "questions_json": str(item.questions_json_path) if item.questions_json_path else None,
                "share_log": str(item.share_log_path) if item.share_log_path else None,
                "main_failure_reason": item.main_failure_reason,
            }
            for item in result.items
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.failed == 0 else 1


def cmd_run_ocr_evidence(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = build_ocr_evidence_package(args.package, engine=args.ocr_engine)
    except (PathValidationError, OcrEvidenceError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "line_count": len(result.package.lines),
        "ocr_engines_used": result.package.ocr_engines_used,
        "warnings": result.package.warnings,
        "json_path": str(result.json_path),
        "md_path": str(result.md_path),
    }, indent=2))
    return 0


def cmd_merge_evidence(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = merge_evidence_package(args.package)
    except (PathValidationError, EvidenceMergerError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "merged_line_count": result.summary["merged_line_count"],
        "ocr_option_recoveries": result.summary["ocr_option_recoveries"],
        "merged_json_path": str(result.merged_json_path),
        "summary_json_path": str(result.summary_json_path),
    }, indent=2))
    return 0


def cmd_profile_extraction_capability(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = profile_extraction_capability(args.package)
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "profile_path": str(result.profile_path),
        "recommended_answer_mode": result.recommended_answer_mode.value,
        "profile": result.raw,
    }, indent=2))
    return 0


def cmd_build_final_questions_export(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = build_final_questions_export(
            args.package,
            answer_mode=SemanticBinderAnswerMode(args.answer_mode),
        )
    except (PathValidationError, FinalQuestionsExportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "total_questions_detected": result.package.total_questions_detected,
        "accepted_safe_count": result.package.accepted_safe_count,
        "review_required_count": result.package.review_required_count,
        "visual_required_count": result.package.visual_required_count,
        "json_path": str(result.json_path),
        "report_path": str(result.report_path),
        "summary_path": str(result.summary_path),
    }, indent=2))
    return 0


def cmd_replay_finalization(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = replay_finalization(
            args.package,
            expected_count=args.expected_count,
            refresh_share_log=not args.no_share_log,
            answer_mode=SemanticBinderAnswerMode(args.answer_mode),
        )
    except (PathValidationError, FinalizationReplayError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "question_window_count": result.question_window_count,
        "total_questions_detected": result.total_questions_detected,
        "questions_with_4_options_count": result.questions_with_4_options_count,
        "accepted_safe_count": result.accepted_safe_count,
        "accepted_safe_with_incomplete_options_count": (
            result.accepted_safe_with_incomplete_options_count
        ),
        "ready_count": result.ready_count,
        "review_count": result.review_count,
        "review_items_count": result.review_items_count,
        "answer_solution_join_gap_count": result.answer_solution_join_gap_count,
        "answers_mapped_from_solution_count": result.answers_mapped_from_solution_count,
        "review_items_path": str(result.review_items_path) if result.review_items_path else None,
        "json_path": str(result.json_path),
        "report_path": str(result.report_path),
        "share_log_path": str(result.share_log_path) if result.share_log_path else None,
    }
    print(json.dumps(summary, indent=2))
    if result.accepted_safe_with_incomplete_options_count > 0:
        return 1
    return 0


def cmd_apply_semantic_bad_item_guard(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = apply_semantic_bad_item_guard(
            args.package,
            expected_count=args.expected_count,
            answer_mode=SemanticBinderAnswerMode(args.answer_mode),
        )
    except (PathValidationError, SemanticBadItemGuardError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "bad_item_count": result.report.bad_item_count,
        "quarantined_item_count": result.quarantined_count,
        "json_path": str(result.json_path),
        "md_path": str(result.md_path),
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_replay_semantic_chunks(args: argparse.Namespace) -> int:
    setup_logging()
    only_suspicious = not args.all_chunks
    try:
        result = replay_semantic_chunks(
            args.package,
            only_suspicious=only_suspicious,
            execute=args.execute,
            answer_mode=SemanticBinderAnswerMode(args.answer_mode),
            expected_count=args.expected_count,
            provider=args.provider,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
        )
    except (PathValidationError, SemanticChunkReplayError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "dry_run": result.plan.dry_run,
        "suspicious_chunk_count": result.plan.suspicious_chunk_count,
        "executed": result.executed,
        "replayed_chunk_count": result.replayed_chunk_count,
        "plan_path": str(result.plan_path),
        "chunks": [c.model_dump() for c in result.plan.chunks],
        "warnings": result.warnings or [],
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_build_semantic_final_export(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        if args.generate_review_artifacts:
            export_semantic_review_items(
                args.package,
                generate_patch_template=True,
            )
        result = build_semantic_final_export(
            args.package,
            export_mode=SemanticFinalExportMode(args.export_mode),
            answer_mode=SemanticBinderAnswerMode(args.answer_mode),
        )
    except (PathValidationError, SemanticFinalExportError, SemanticReviewExportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    pkg = result.package
    summary = {
        "export_mode": pkg.export_mode.value,
        "expected_count": pkg.expected_count,
        "semantic_item_count": pkg.semantic_item_count,
        "count_match": pkg.count_match,
        "exported_count": pkg.exported_count,
        "accepted_exported_count": pkg.accepted_exported_count,
        "excluded_count": pkg.excluded_count,
        "hallucination_suspected_count": pkg.hallucination_suspected_count,
        "quality_status_from_semantic_evaluation": pkg.quality_status_from_semantic_evaluation,
        "final_export_quality_status": pkg.final_export_quality_status,
        "ready_for_full_paper_ingestion": pkg.ready_for_full_paper_ingestion,
        "ready_for_partial_accepted_ingestion": pkg.ready_for_partial_accepted_ingestion,
        "accepted_safe_count": pkg.accepted_safe_count,
        "unsafe_previously_accepted_count": pkg.unsafe_previously_accepted_count,
        "quality_status": pkg.quality_status,
        "questions_path": str(result.questions_path),
        "gate_report_path": str(result.gate_report_path) if result.gate_report_path else None,
        "report_path": str(result.report_path),
        "summary_path": str(result.summary_path),
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_apply_semantic_review_patch(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = apply_semantic_review_patch(
            args.package,
            args.patch,
            answer_mode=SemanticBinderAnswerMode(args.answer_mode),
            allow_accepted_item_patch=args.allow_accepted_item_patch,
        )
    except (PathValidationError, SemanticReviewPatchError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report = result.report
    summary = {
        "applied_count": report.applied_count,
        "blocked_count": report.blocked_count,
        "hold_count": report.hold_count,
        "rejected_patch_count": report.rejected_patch_count,
        "applied_path": str(result.applied_path),
        "report_path": str(result.report_path),
    }
    print(json.dumps(summary, indent=2))
    return 0 if report.rejected_patch_count == 0 else 1


def cmd_export_semantic_review_items(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = export_semantic_review_items(
            args.package,
            generate_patch_template=args.generate_patch_template,
        )
    except (PathValidationError, SemanticReviewExportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "total_review_items": result.report.total_review_items,
        "json_path": str(result.json_path),
        "md_path": str(result.md_path),
        "patch_template_path": str(result.patch_template_path) if result.patch_template_path else None,
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_repair_semantic_binding(args: argparse.Namespace) -> int:
    setup_logging()
    try:
        result = repair_semantic_binding_package(
            args.package,
            answer_mode=SemanticBinderAnswerMode(args.answer_mode),
            expected_count=args.expected_count,
            overwrite_semantic_binding=args.overwrite_semantic_binding,
        )
    except (PathValidationError, SemanticBindingRepairError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report = result.repair_report
    summary = {
        "repaired_path": str(result.repaired_path),
        "repair_report_path": str(result.repair_report_path),
        "validation_path": str(result.validation_path),
        "evaluation_path": str(result.evaluation_path),
        "source_span_missing_before": report.source_span_missing_before,
        "source_span_missing_after": report.source_span_missing_after,
        "answer_key_not_in_options_before": report.answer_key_not_in_options_before,
        "answer_key_not_in_options_after": report.answer_key_not_in_options_after,
        "accepted_before": report.accepted_before,
        "accepted_after": report.accepted_after,
        "review_required_before": report.review_required_before,
        "review_required_after": report.review_required_after,
        "rejected_before": report.rejected_before,
        "rejected_after": report.rejected_after,
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_evaluate_semantic_binding(args: argparse.Namespace) -> int:
    setup_logging()

    try:
        result = evaluate_semantic_binding_package(
            args.package,
            expected_count=args.expected_count,
            answer_mode=SemanticBinderAnswerMode(args.answer_mode),
            bind_if_missing=args.bind_if_missing,
            use_repaired=args.use_repaired,
            provider=args.provider,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
        )
    except (PathValidationError, SemanticBindingError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    evaluation_data: dict | None = None
    if result.evaluation_path.exists():
        try:
            evaluation_data = json.loads(result.evaluation_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    validation = result.package.validation_summary
    summary = {
        "quality_status": evaluation_data.get("quality_status", "warning") if evaluation_data else "warning",
        "evaluation_report": str(result.evaluation_path),
        "semantic_item_count": validation.total_items,
        "accepted_count": validation.accepted_count,
        "review_required_count": validation.review_required_count,
        "rejected_count": validation.rejected_count,
        "provider": result.package.binder_provider,
        "model": result.package.binder_model,
    }
    if evaluation_data is not None:
        summary["evaluation"] = evaluation_data
    print(json.dumps(summary, indent=2))
    if args.strict_semantic_quality and summary["quality_status"] == "failed":
        return 1
    return 0


def cmd_normalize_evidence(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        result = normalize_evidence_package(
            args.package,
            primary_extractor=PrimaryExtractorMode(args.primary_extractor),
        )
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except EvidenceNormalizationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.package.extraction_status == EvidenceExtractionStatus.SUCCEEDED:
        logger.info("Evidence normalization succeeded: %s", result.evidence_json_path)
    elif result.package.extraction_status == EvidenceExtractionStatus.PARTIAL:
        logger.warning("Partial evidence normalization: %s", result.evidence_json_path)
        for warning in result.package.warnings:
            logger.warning("Warning: %s", warning)
    else:
        logger.error("Evidence normalization failed: %s", result.evidence_json_path)

    summary = {
        "extraction_status": result.package.extraction_status.value,
        "primary_extractor": result.package.primary_extractor,
        "line_count": result.summary.line_count,
        "page_count": result.summary.page_count,
        "warnings": result.package.warnings,
        "evidence_json": str(result.evidence_json_path),
        "evidence_summary_json": str(result.summary_json_path),
        "extractor_comparison_json": str(result.comparison_json_path),
    }
    print(json.dumps(summary, indent=2))
    return (
        0
        if result.package.extraction_status
        in {EvidenceExtractionStatus.SUCCEEDED, EvidenceExtractionStatus.PARTIAL}
        else 1
    )


def cmd_classify_markdown(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        result = classify_package(args.package)
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ClassificationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report_path = args.package / "classified" / "classification-report.json"
    if result.status == ClassificationStatus.SUCCEEDED:
        logger.info("Classification succeeded: %s", report_path)
    else:
        logger.error("Classification failed: %s", report_path)
        for error in result.errors:
            print(f"Error: {error}", file=sys.stderr)

    summary = {
        "status": result.status.value,
        "total_lines": result.total_lines,
        "total_blocks": result.total_blocks,
        "question_anchor_count": result.question_anchor_count,
        "content_question_anchor_count": result.content_question_anchor_count,
        "option_candidate_count": result.option_candidate_count,
        "content_option_candidate_count": result.content_option_candidate_count,
        "solution_anchor_count": result.solution_anchor_count,
        "image_reference_count": result.image_reference_count,
        "page_count_detected": result.page_count_detected,
        "table_row_count": result.table_row_count,
        "content_lines_json": str(args.package / "classified" / "content-lines.json"),
        "warnings": result.warnings,
        "errors": result.errors,
        "lines_json": str(args.package / "classified" / "lines.json"),
        "blocks_json": str(args.package / "classified" / "blocks.json"),
        "report_json": str(report_path),
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.status == ClassificationStatus.SUCCEEDED else 1


def cmd_parse_question_candidates(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        result = parse_question_candidates_package(args.package)
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except QuestionParseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report_path = args.package / "questions" / "question-candidate-report.json"
    if result.status == ParseStatus.SUCCEEDED:
        logger.info("Question candidate parse succeeded: %s", report_path)
    else:
        logger.error("Question candidate parse failed: %s", report_path)
        for error in result.errors:
            print(f"Error: {error}", file=sys.stderr)

    summary = {
        "status": result.status.value,
        "total_candidates": result.total_candidates,
        "valid_candidates": result.valid_candidates,
        "needs_review_candidates": result.needs_review_candidates,
        "duplicate_question_numbers": result.duplicate_question_numbers,
        "missing_question_numbers": result.missing_question_numbers,
        "candidates_with_images": result.candidates_with_images,
        "candidates_with_no_options": result.candidates_with_no_options,
        "warnings": result.warnings,
        "errors": result.errors,
        "candidates_json": str(args.package / "questions" / "question-candidates.json"),
        "report_json": str(report_path),
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.status == ParseStatus.SUCCEEDED else 1


def cmd_map_answers_solutions(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        result = map_answers_solutions_package(args.package)
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except MappingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report_path = args.package / "mappings" / "answer-solution-report.json"
    if result.status == MapperStatus.SUCCEEDED:
        logger.info("Answer/solution mapping succeeded: %s", report_path)
    else:
        logger.error("Answer/solution mapping failed: %s", report_path)
        for error in result.errors:
            print(f"Error: {error}", file=sys.stderr)

    summary = {
        "status": result.status.value,
        "total_question_candidates": result.total_question_candidates,
        "mapped_count": result.mapped_count,
        "answer_only_count": result.answer_only_count,
        "solution_only_count": result.solution_only_count,
        "not_available_count": result.not_available_count,
        "needs_review_count": result.needs_review_count,
        "duplicate_solution_numbers": result.duplicate_solution_numbers,
        "unmatched_solution_numbers": result.unmatched_solution_numbers,
        "content_lines_used": result.content_lines_used,
        "line_source_path": result.line_source_path,
        "mapping_source": result.mapping_source,
        "solution_anchor_count_seen_by_mapper": result.solution_anchor_count_seen_by_mapper,
        "answer_candidate_count": result.answer_candidate_count,
        "solution_candidate_count": result.solution_candidate_count,
        "first_solution_anchor_line": result.first_solution_anchor_line,
        "warnings": result.warnings,
        "errors": result.errors,
        "map_json": str(args.package / "mappings" / "answer-solution-map.json"),
        "report_json": str(report_path),
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.status == MapperStatus.SUCCEEDED else 1


def cmd_build_final_package(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        package, report = build_final_package_from_directory(args.package)
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except FinalizeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report_path = args.package / "final" / "validation-report.json"
    if report.status == FinalizeStatus.SUCCEEDED:
        logger.info("Final package built: %s", report_path)
    else:
        logger.error("Final package build failed: %s", report_path)
        for error in report.errors:
            print(f"Error: {error}", file=sys.stderr)

    summary = {
        "status": report.status.value,
        "total_questions": report.total_questions,
        "validated_count": report.validated_count,
        "question_only_validated_count": report.question_only_validated_count,
        "needs_review_count": report.needs_review_count,
        "incomplete_count": report.incomplete_count,
        "duplicate_conflict_count": report.duplicate_conflict_count,
        "visual_question_count": report.visual_question_count,
        "answer_option_mismatch_count": report.answer_option_mismatch_count,
        "warnings": report.warnings,
        "errors": report.errors,
        "questions_json": str(args.package / "final" / "questions.json"),
        "validation_report_json": str(report_path),
        "package_version": package.package_version,
    }
    print(json.dumps(summary, indent=2))
    return 0 if report.status == FinalizeStatus.SUCCEEDED else 1


def cmd_audit_final_package(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        report = audit_final_package_from_directory(
            args.package,
            expected_question_count=args.expected_count,
        )
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except AuditError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    audit_json = args.package / "audit" / "final-package-audit.json"
    if report.status == AuditStatus.FAILED:
        logger.error("Final package audit failed: %s", audit_json)
    elif report.status == AuditStatus.WARNING:
        logger.warning("Final package audit completed with warnings: %s", audit_json)
    else:
        logger.info("Final package audit passed: %s", audit_json)

    summary = {
        "status": report.status.value,
        "total_questions": report.total_questions,
        "expected_question_count": report.expected_question_count,
        "expected_count_match": report.expected_count_match,
        "validated_count": report.validated_count,
        "question_only_validated_count": report.question_only_validated_count,
        "needs_review_count": report.needs_review_count,
        "incomplete_count": report.incomplete_count,
        "duplicate_conflict_count": report.duplicate_conflict_count,
        "visual_question_count": report.visual_question_count,
        "answered_count": report.answered_count,
        "solved_count": report.solved_count,
        "candidates_without_options": report.candidates_without_options,
        "answer_option_mismatch_count": report.answer_option_mismatch_count,
        "high_risk_count": len(report.high_risk_items),
        "missing_question_numbers": report.missing_question_numbers,
        "duplicate_question_numbers": report.duplicate_question_numbers,
        "warnings": report.warnings,
        "errors": report.errors,
        "audit_json": str(audit_json),
        "audit_md": str(args.package / "audit" / "final-package-audit.md"),
    }
    print(json.dumps(summary, indent=2))
    return 0 if report.status != AuditStatus.FAILED else 1


def cmd_inspect_raw_markdown(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        report = inspect_raw_markdown_package(args.package)
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except InspectionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    inspection_json = args.package / "diagnostics" / "raw-markdown-inspection.json"
    logger.info("Raw markdown inspection written: %s", inspection_json)

    summary = {
        "total_raw_lines": report["total_raw_lines"],
        "table_row_count": report["table_row_count"],
        "image_reference_count": report["image_reference_count"],
        "image_asset_count": report["image_asset_count"],
        "warnings": report["warnings"],
        "inspection_json": str(inspection_json),
        "inspection_md": str(args.package / "diagnostics" / "raw-markdown-inspection.md"),
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_diagnose_question_coverage(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        report = diagnose_question_coverage_package(
            args.package,
            expected_question_count=args.expected_count,
        )
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except CoverageDiagnosticError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    coverage_json = args.package / "diagnostics" / "question-coverage.json"
    logger.info("Question coverage diagnostics written: %s", coverage_json)

    summary = {
        "expected_question_count": report.get("expected_question_count"),
        "content_line_anchor_count": report["content_line_anchor_count"],
        "candidate_count": report["candidate_count"],
        "final_count": report["final_count"],
        "missing_at_candidate_stage": report["missing_at_candidate_stage"],
        "continuous_missing_ranges": report["continuous_missing_ranges"],
        "missing_reasons": report["missing_reasons"],
        "image_reference_count": report["image_reference_count"],
        "image_asset_count": report["image_asset_count"],
        "missing_asset_reference_count": report["missing_asset_reference_count"],
        "warnings": report["warnings"],
        "coverage_json": str(coverage_json),
        "coverage_md": str(args.package / "diagnostics" / "question-coverage.md"),
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_run_pipeline(args: argparse.Namespace) -> int:
    setup_logging()

    options = PipelineOptions(
        input_pdf=args.input,
        output_dir=args.output,
        expected_count=args.expected_count,
        skip_audit=args.skip_audit,
        skip_inspection=args.skip_inspection,
        skip_diagnosis=args.skip_diagnosis,
        force=args.force,
        stop_on_failure=not args.no_stop_on_failure,
        build_eligibility=args.build_eligibility,
        answer_mode=AnswerMode(args.answer_mode),
        reconcile_artifacts=args.reconcile_artifacts,
        strict_quality_gate=args.strict_quality_gate,
        build_pattern_input=args.build_pattern_input,
        pattern_export_mode=PatternExportMode(args.pattern_export_mode),
        allow_failed_quality_gate_for_pattern=args.allow_failed_quality_gate_for_pattern,
        normalize_evidence=args.normalize_evidence,
        use_semantic_binder=args.use_semantic_binder,
        force_semantic_binder=args.force_semantic_binder,
        semantic_binder_provider=args.semantic_binder_provider,
        semantic_binder_model=args.semantic_binder_model,
        semantic_answer_mode=SemanticBinderAnswerMode(args.semantic_answer_mode),
        include_answer_key_evidence=args.include_answer_key_evidence,
        semantic_binder_timeout_seconds=args.timeout_seconds,
    )

    try:
        result = run_pipeline(options)
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except PipelineError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(build_pipeline_summary(result), indent=2))
    if result.status == PipelineRunStatus.FAILED:
        return 1
    if args.strict_quality_gate:
        gate = result.summary.get("quality_gate_status")
        if gate != QualityGateStatus.PASSED.value:
            return 1
    return 0


def cmd_build_pattern_input(args: argparse.Namespace) -> int:
    setup_logging()

    try:
        result = build_pattern_question_input_package(
            args.package,
            export_mode=PatternExportMode(args.export_mode),
            allow_missing_eligibility=args.allow_missing_eligibility,
            allow_failed_quality_gate=args.allow_failed_quality_gate,
        )
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except PatternInputBuildError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "status": result.status.value,
        "exported_count": result.package.exported_count,
        "export_mode": result.package.export_mode.value,
        "total_source_questions": result.package.total_source_questions,
        "quality_gate_status": result.package.quality_gate_status,
        "warnings": result.package.warnings,
        "package_json": result.output_paths.get("package_json"),
        "eligible_json": result.output_paths.get("eligible_json"),
        "review_json": result.output_paths.get("review_json"),
        "blocked_json": result.output_paths.get("blocked_json"),
        "summary_md": result.output_paths.get("summary_md"),
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.status.value == "succeeded" else 1


def cmd_reconcile_artifacts(args: argparse.Namespace) -> int:
    setup_logging()

    try:
        report = reconcile_artifacts_package(args.package)
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ArtifactReconciliationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "quality_gate_status": report.quality_gate_status.value,
        "failed_check_count": report.failed_check_count,
        "warning_count": report.warning_count,
        "passed_check_count": report.passed_check_count,
        "total_questions": report.summary.total_questions,
        "eligible_count": report.summary.eligible_count,
        "review_required_count": report.summary.review_required_count,
        "blocked_count": report.summary.blocked_count,
        "reconciliation_json": str(
            args.package / "diagnostics" / "artifact-reconciliation.json",
        ),
        "reconciliation_md": str(
            args.package / "diagnostics" / "artifact-reconciliation.md",
        ),
        "warnings": report.warnings,
    }
    print(json.dumps(summary, indent=2))
    if report.quality_gate_status == QualityGateStatus.FAILED:
        return 1
    return 0


def cmd_export_review_items(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        report = export_review_items_package(
            args.package,
            include_validated=args.include_validated,
        )
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ReviewExportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    review_json = args.package / "review" / "review-items.json"
    review_md = args.package / "review" / "review-items.md"
    logger.info("Review export written: %s", review_json)

    summary = {
        "total_final_questions": report.total_final_questions,
        "review_item_count": report.review_item_count,
        "include_validated": report.include_validated,
        "status_counts": report.status_counts,
        "reason_counts": report.reason_counts,
        "review_items_json": str(review_json),
        "review_items_md": str(review_md),
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_build_ingestion_eligibility(args: argparse.Namespace) -> int:
    logger = setup_logging()

    try:
        report = build_ingestion_eligibility_package(
            args.package,
            answer_mode=AnswerMode(args.answer_mode),
        )
    except PathValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except EligibilityBuildError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    elig_dir = args.package / "eligibility"
    logger.info("Ingestion eligibility written: %s", elig_dir / "ingestion-eligibility-report.json")

    summary = {
        "status": report.status.value,
        "total_questions": report.total_questions,
        "eligible_count": report.eligible_count,
        "review_required_count": report.review_required_count,
        "blocked_count": report.blocked_count,
        "answer_mode": report.answer_mode.value,
        "duplicate_solution_count": report.duplicate_solution_count,
        "duplicate_solution_conflict_count": report.duplicate_solution_conflict_count,
        "visual_question_count": report.visual_question_count,
        "warnings": report.warnings,
        "errors": report.errors,
        "eligibility_report_json": str(elig_dir / "ingestion-eligibility-report.json"),
        "eligible_questions_json": str(elig_dir / "eligible-questions.json"),
        "review_required_questions_json": str(elig_dir / "review-required-questions.json"),
        "blocked_questions_json": str(elig_dir / "blocked-questions.json"),
        "duplicate_diagnostics_json": str(elig_dir / "duplicate-solution-diagnostics.json"),
        "eligibility_md": str(elig_dir / "ingestion-eligibility.md"),
    }
    print(json.dumps(summary, indent=2))
    return 0 if report.status.value == "succeeded" else 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv_if_present()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "prepare":
        return cmd_prepare(args)
    if args.command == "normalize-evidence":
        return cmd_normalize_evidence(args)
    if args.command == "bind-semantically":
        return cmd_bind_semantically(args)
    if args.command == "test-llm-provider":
        return cmd_test_llm_provider(args)
    if args.command == "evaluate-semantic-binding":
        return cmd_evaluate_semantic_binding(args)
    if args.command == "repair-semantic-binding":
        return cmd_repair_semantic_binding(args)
    if args.command == "diagnose-semantic-issues":
        return cmd_diagnose_semantic_issues(args)
    if args.command == "run-semantic-pipeline":
        return cmd_run_semantic_pipeline(args)
    if args.command == "process-pdfs":
        return cmd_process_pdfs(args)
    if args.command == "merge-reviewed-questions":
        return cmd_merge_reviewed_questions(args)
    if args.command == "validate-final-question-json":
        return cmd_validate_final_question_json(args)
    if args.command == "run-pdf-folder":
        return cmd_run_pdf_folder(args)
    if args.command == "run-ocr-evidence":
        return cmd_run_ocr_evidence(args)
    if args.command == "merge-evidence":
        return cmd_merge_evidence(args)
    if args.command == "profile-extraction-capability":
        return cmd_profile_extraction_capability(args)
    if args.command == "build-final-questions-export":
        return cmd_build_final_questions_export(args)
    if args.command == "replay-finalization":
        return cmd_replay_finalization(args)
    if args.command == "build-semantic-final-export":
        return cmd_build_semantic_final_export(args)
    if args.command == "apply-semantic-review-patch":
        return cmd_apply_semantic_review_patch(args)
    if args.command == "export-semantic-review-items":
        return cmd_export_semantic_review_items(args)
    if args.command == "apply-semantic-bad-item-guard":
        return cmd_apply_semantic_bad_item_guard(args)
    if args.command == "replay-semantic-chunks":
        return cmd_replay_semantic_chunks(args)
    if args.command == "classify-markdown":
        return cmd_classify_markdown(args)
    if args.command == "parse-question-candidates":
        return cmd_parse_question_candidates(args)
    if args.command == "map-answers-solutions":
        return cmd_map_answers_solutions(args)
    if args.command == "build-final-package":
        return cmd_build_final_package(args)
    if args.command == "audit-final-package":
        return cmd_audit_final_package(args)
    if args.command == "inspect-raw-markdown":
        return cmd_inspect_raw_markdown(args)
    if args.command == "diagnose-question-coverage":
        return cmd_diagnose_question_coverage(args)
    if args.command == "run-pipeline":
        return cmd_run_pipeline(args)
    if args.command == "reconcile-artifacts":
        return cmd_reconcile_artifacts(args)
    if args.command == "build-pattern-input":
        return cmd_build_pattern_input(args)
    if args.command == "export-review-items":
        return cmd_export_review_items(args)
    if args.command == "build-ingestion-eligibility":
        return cmd_build_ingestion_eligibility(args)

    print(f"Error: Unknown command: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
