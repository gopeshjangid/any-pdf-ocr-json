"""Post-export enhancement: recovery, visuals, readiness, review (Part 14M)."""

from __future__ import annotations

from dataclasses import dataclass

from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage
from meritranker_data_ingestion.schemas.evidence_answer_solution_map import AnswerSolutionMapPackage
from meritranker_data_ingestion.schemas.final_questions_export import FinalQuestionItem
from meritranker_data_ingestion.schemas.question_window import QuestionWindowsPackage
from meritranker_data_ingestion.services.answer_solution_join_diagnostics import (
    AnswerSolutionJoinDiagnostics,
    diagnose_answer_solution_join_gaps,
)
from meritranker_data_ingestion.services.expected_count_canonicalizer import (
    CanonicalizationReport,
    canonicalize_for_expected_count,
    split_canonicalization_candidates,
)
from meritranker_data_ingestion.services.final_item_acceptance_gate import (
    apply_gate_to_items,
    compute_final_item_gate_metrics,
    FinalItemGateMetrics,
)
from meritranker_data_ingestion.services.final_readiness_resolver import (
    answers_expected_from_profile,
    apply_readiness_metadata,
    compute_status_counts,
    StatusCounts,
)
from meritranker_data_ingestion.services.answer_option_reconciler import (
    index_question_windows,
    reconcile_item_answers,
)
from meritranker_data_ingestion.services.hallucination_triage import triage_hallucination_blocked_item
from meritranker_data_ingestion.services.option_recovery import supplement_options_from_window
from meritranker_data_ingestion.services.visual_detection import apply_visual_metadata


@dataclass(frozen=True)
class FinalExportEnhancementResult:
    items: list[FinalQuestionItem]
    gate_metrics: FinalItemGateMetrics
    readiness_counts: StatusCounts
    join_diagnostics: AnswerSolutionJoinDiagnostics
    canonicalization: CanonicalizationReport
    extra_candidates: list[FinalQuestionItem]
    duplicate_candidates: list[FinalQuestionItem]


def enhance_final_export_items(
    items: list[FinalQuestionItem],
    *,
    windows_pkg: QuestionWindowsPackage | None,
    evidence: DocumentEvidencePackage | None,
    answer_map_pkg: AnswerSolutionMapPackage | None,
    expected_count: int | None = None,
    source_file_name: str = "unknown.pdf",
    answers_expected: bool = False,
) -> FinalExportEnhancementResult:
    windows_by_qnum = index_question_windows(windows_pkg)
    answer_by_qnum = {
        entry.question_number: entry
        for entry in (answer_map_pkg.entries if answer_map_pkg else [])
        if entry.question_number is not None
    }

    enhanced: list[FinalQuestionItem] = []
    for item in items:
        qnum = item.question_number or -1
        window = windows_by_qnum.get(qnum)
        current = supplement_options_from_window(item, window=window, evidence=evidence)
        current = reconcile_item_answers(
            current,
            map_entry=answer_by_qnum.get(qnum) if qnum > 0 else None,
        )
        current = apply_visual_metadata(current)
        current = triage_hallucination_blocked_item(
            current,
            window=window,
            evidence=evidence,
        )
        enhanced.append(current)

    gated = apply_gate_to_items(enhanced)
    with_metadata = [
        apply_readiness_metadata(item, answers_expected=answers_expected) for item in gated
    ]
    if expected_count and expected_count > 0:
        _, extra_candidates, duplicate_candidates = split_canonicalization_candidates(
            with_metadata,
            expected_count=expected_count,
        )
        canonical_items, canonicalization = canonicalize_for_expected_count(
            with_metadata,
            expected_count=expected_count,
            source_file_name=source_file_name,
        )
        if canonicalization.missing_placeholder_count:
            canonical_items = [
                apply_readiness_metadata(item, answers_expected=answers_expected)
                for item in canonical_items
            ]
    else:
        extra_candidates = []
        duplicate_candidates = []
        canonical_items = with_metadata
        canonicalization = CanonicalizationReport(
            expected_count=0,
            raw_candidate_count=len(with_metadata),
            public_question_count=len(with_metadata),
            extra_candidate_count=0,
            duplicate_candidate_count=0,
            missing_placeholder_count=0,
            missing_question_numbers=[],
            duplicate_question_numbers=[],
            over_detection=False,
        )

    join_diag = diagnose_answer_solution_join_gaps(canonical_items, answer_map_pkg)

    return FinalExportEnhancementResult(
        items=canonical_items,
        gate_metrics=compute_final_item_gate_metrics(canonical_items),
        readiness_counts=compute_status_counts(canonical_items),
        join_diagnostics=join_diag,
        canonicalization=canonicalization,
        extra_candidates=extra_candidates,
        duplicate_candidates=duplicate_candidates,
    )
