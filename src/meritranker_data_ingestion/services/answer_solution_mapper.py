"""Deterministic answer/solution mapper from classified lines + question candidates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    ANSWER_SOLUTION_MAP_NAME,
    ANSWER_SOLUTION_REPORT_NAME,
    CANDIDATES_WITH_MAPPINGS_NAME,
    CLASSIFIED_BLOCKS_NAME,
    CLASSIFIED_DIR,
    CLASSIFIED_LINES_NAME,
    MAPPINGS_DIR,
    QUESTION_CANDIDATES_NAME,
    QUESTIONS_DIR,
)
from meritranker_data_ingestion.schemas.answer_solution_mapping import (
    AnswerCandidate,
    AnswerSolutionMappingResult,
    CandidateWithMapping,
    MapperStatus,
    MappingStatus,
    QuestionAnswerSolutionMapping,
    SolutionCandidate,
)
from meritranker_data_ingestion.schemas.classification import (
    LineType,
    MarkdownBlockRecord,
    MarkdownLineRecord,
)
from meritranker_data_ingestion.schemas.question_candidates import QuestionCandidate
from meritranker_data_ingestion.services.classified_lines_loader import load_lines_for_downstream
from meritranker_data_ingestion.services.line_text_classifier import classification_target
from meritranker_data_ingestion.services.file_service import (
    PathValidationError,
    assert_output_contains,
    resolve_path,
)

RE_SOLUTION_HEADING = re.compile(
    r"^#{0,6}\s*(Solutions?|Answer\s*Key|Answers?\s*(?:Key|Sheet)?|"
    r"Answer\s*Sheet|Explanations?)\s*:?\s*$",
    re.IGNORECASE,
)
RE_S_ANCHOR = re.compile(r"^S\s*(\d+)\s*[\.\):\-]", re.IGNORECASE)
RE_S_ANCHOR_FIND = re.compile(
    r"(?:\*{1,2})?(?:#{1,6}\s*)?(?:\*{1,2})?S\s*(\d+)\s*[\.\):\-]",
    re.IGNORECASE,
)
RE_S_WITH_ANS_PAREN = re.compile(
    r"^S\s*(\d+)\s*[\.\):\-]?\s*Ans(?:wer)?\s*[\.\:]*\s*\(\s*([A-Ea-e])\s*\)",
    re.IGNORECASE,
)
RE_S_WITH_ANS = re.compile(
    r"^S\s*(\d+)\s*[\.\):\-]?\s*Ans(?:wer)?\s*[\.\:]*\s*([A-Ea-e])\b",
    re.IGNORECASE,
)
RE_Q_WITH_ANS = re.compile(
    r"^Q\s*(\d+)\s*[\.\):\-]?\s*Ans(?:wer)?\s*[\.\:]*\s*([A-Ea-e])\b",
    re.IGNORECASE,
)
RE_Q_ANSWER_COLON = re.compile(
    r"^Q\s*(\d+)\s*Answer\s*:\s*([A-Ea-e])\b",
    re.IGNORECASE,
)
RE_ANSWER_KEY_COMPACT = re.compile(r"^(\d+)\s*[\.\)\-]\s*([A-Ea-e])\b")
RE_IMAGE_PATH = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

STRUCTURAL_SKIP = {
    LineType.PAGE_NUMBER_MARKER,
    LineType.PAGE_BREAK_MARKER,
}


class MappingError(Exception):
    """Raised when answer/solution mapping cannot proceed."""


@dataclass(frozen=True)
class MappingPaths:
    """Paths for answer/solution mapping input/output."""

    package_dir: Path
    lines_json: Path
    blocks_json: Path
    candidates_json: Path
    mappings_dir: Path
    map_json: Path
    report_json: Path
    candidates_with_mappings_json: Path


def build_mapping_paths(package_dir: Path) -> MappingPaths:
    resolved = resolve_path(package_dir)
    if not resolved.is_dir():
        raise PathValidationError(f"Package directory does not exist: {resolved}")

    mappings_dir = resolved / MAPPINGS_DIR
    return MappingPaths(
        package_dir=resolved,
        lines_json=resolved / CLASSIFIED_DIR / CLASSIFIED_LINES_NAME,
        blocks_json=resolved / CLASSIFIED_DIR / CLASSIFIED_BLOCKS_NAME,
        candidates_json=resolved / QUESTIONS_DIR / QUESTION_CANDIDATES_NAME,
        mappings_dir=mappings_dir,
        map_json=mappings_dir / ANSWER_SOLUTION_MAP_NAME,
        report_json=mappings_dir / ANSWER_SOLUTION_REPORT_NAME,
        candidates_with_mappings_json=mappings_dir / CANDIDATES_WITH_MAPPINGS_NAME,
    )


def load_lines(path: Path) -> list[MarkdownLineRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [MarkdownLineRecord.model_validate(item) for item in payload]


def load_blocks(path: Path) -> list[MarkdownBlockRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [MarkdownBlockRecord.model_validate(item) for item in payload]


def load_candidates(path: Path) -> list[QuestionCandidate]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [QuestionCandidate.model_validate(item) for item in payload]


def _find_solution_section_line(lines: list[MarkdownLineRecord]) -> int | None:
    for line in lines:
        if line.line_type == LineType.SOLUTION_SECTION_HEADING:
            return line.line_number
        if RE_SOLUTION_HEADING.match(classification_target(line.raw_text)):
            return line.line_number
    return None


def _is_solution_anchor_line(line: MarkdownLineRecord) -> bool:
    if line.line_type == LineType.SOLUTION_ANCHOR:
        return True
    return bool(RE_S_ANCHOR.match(classification_target(line.raw_text)))


def _extract_answer_from_line(raw: str) -> tuple[int | None, str | None, str | None, float, list[str]]:
    """Return (q_num, answer_key, answer_key_raw, confidence, issues)."""
    issues: list[str] = []
    text = classification_target(raw)

    for pattern, conf in (
        (RE_S_WITH_ANS_PAREN, 0.95),
        (RE_S_WITH_ANS, 0.9),
        (RE_Q_ANSWER_COLON, 0.9),
        (RE_Q_WITH_ANS, 0.85),
    ):
        match = pattern.match(text) or pattern.search(text)
        if match:
            q_num = int(match.group(1))
            key_raw = match.group(2)
            return q_num, key_raw.upper(), key_raw, conf, issues

    return None, None, None, 0.0, issues


def _extract_image_refs(lines: list[MarkdownLineRecord]) -> list[str]:
    refs: list[str] = []
    for line in lines:
        if line.line_type == LineType.IMAGE_REFERENCE or "!(" in line.raw_text:
            match = RE_IMAGE_PATH.search(line.raw_text)
            if match:
                refs.append(match.group(1))
    return refs


@dataclass(frozen=True)
class SolutionCollectionStats:
    """Diagnostics from solution block collection."""

    multi_anchor_solution_lines_count: int = 0
    solution_segments_created_from_splits: int = 0
    suspicious_merged_solution_count: int = 0


def _find_solution_anchor_positions(
    lines: list[MarkdownLineRecord],
    start_idx: int,
) -> list[tuple[int, int, int]]:
    """Return ordered (line_index, char_offset, question_number) anchor positions."""
    anchors: list[tuple[int, int, int]] = []
    for i in range(start_idx, len(lines)):
        for match in RE_S_ANCHOR_FIND.finditer(lines[i].raw_text):
            anchors.append((i, match.start(), int(match.group(1))))
    return anchors


def _solution_contains_other_anchor(raw_text: str, own_q_num: int) -> bool:
    for match in RE_S_ANCHOR_FIND.finditer(raw_text):
        if int(match.group(1)) != own_q_num:
            return True
    return False


def _build_solution_segment(
    lines: list[MarkdownLineRecord],
    start_line_idx: int,
    start_char: int,
    end_line_idx: int,
    end_char: int,
    q_num: int,
) -> SolutionCandidate:
    parts: list[str] = []
    if start_line_idx == end_line_idx:
        parts.append(lines[start_line_idx].raw_text[start_char:end_char])
        segment_lines = [lines[start_line_idx]]
    else:
        parts.append(lines[start_line_idx].raw_text[start_char:])
        segment_lines = [lines[start_line_idx]]
        for j in range(start_line_idx + 1, end_line_idx):
            parts.append(lines[j].raw_text)
            segment_lines.append(lines[j])
        if end_char > 0:
            parts.append(lines[end_line_idx].raw_text[:end_char])
            segment_lines.append(lines[end_line_idx])

    raw_text = "\n".join(part for part in parts if part != "")
    line_numbers = [ln.line_number for ln in segment_lines]
    pages = [ln.page_number for ln in segment_lines if ln.page_number is not None]
    issues: list[str] = []
    for ln in segment_lines:
        if ln.line_type in {LineType.PAGE_FOOTER_MARKER, LineType.NOISE_CANDIDATE}:
            issues.append(f"noise_in_solution_line_{ln.line_number}")
    if _solution_contains_other_anchor(raw_text, q_num):
        issues.append("solution_contains_next_anchor")

    return SolutionCandidate(
        question_number=q_num,
        question_number_raw=f"S{q_num}",
        raw_text=raw_text,
        start_line=line_numbers[0],
        end_line=line_numbers[-1],
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        line_numbers=line_numbers,
        image_references=_extract_image_refs(segment_lines),
        confidence=0.9,
        issues=sorted(set(issues)),
    )


def _collect_solution_blocks(
    lines: list[MarkdownLineRecord],
    solution_start: int | None,
) -> tuple[dict[int, list[SolutionCandidate]], SolutionCollectionStats]:
    """Extract solution blocks keyed by question number (may have duplicates)."""
    result: dict[int, list[SolutionCandidate]] = {}
    stats = SolutionCollectionStats()

    start_idx = 0
    if solution_start is not None:
        start_idx = next(
            (i for i, ln in enumerate(lines) if ln.line_number >= solution_start),
            len(lines),
        )

    anchors = _find_solution_anchor_positions(lines, start_idx)
    if not anchors:
        return result, stats

    for i in range(start_idx, len(lines)):
        matches = list(RE_S_ANCHOR_FIND.finditer(lines[i].raw_text))
        if len(matches) > 1:
            stats = SolutionCollectionStats(
                multi_anchor_solution_lines_count=stats.multi_anchor_solution_lines_count + 1,
                solution_segments_created_from_splits=stats.solution_segments_created_from_splits,
                suspicious_merged_solution_count=stats.suspicious_merged_solution_count,
            )

    line_level_anchor_count = sum(1 for idx in range(start_idx, len(lines)) if _is_solution_anchor_line(lines[idx]))
    if len(anchors) > line_level_anchor_count:
        stats = SolutionCollectionStats(
            multi_anchor_solution_lines_count=stats.multi_anchor_solution_lines_count,
            solution_segments_created_from_splits=len(anchors) - line_level_anchor_count,
            suspicious_merged_solution_count=stats.suspicious_merged_solution_count,
        )

    for idx, (line_idx, char_off, q_num) in enumerate(anchors):
        if idx + 1 < len(anchors):
            end_line_idx, end_char, _ = anchors[idx + 1]
        else:
            end_line_idx = len(lines) - 1
            end_char = len(lines[end_line_idx].raw_text)

        solution = _build_solution_segment(
            lines,
            line_idx,
            char_off,
            end_line_idx,
            end_char,
            q_num,
        )
        if "solution_contains_next_anchor" in solution.issues:
            stats = SolutionCollectionStats(
                multi_anchor_solution_lines_count=stats.multi_anchor_solution_lines_count,
                solution_segments_created_from_splits=stats.solution_segments_created_from_splits,
                suspicious_merged_solution_count=stats.suspicious_merged_solution_count + 1,
            )
        result.setdefault(q_num, []).append(solution)

    return result, stats


def _is_answer_key_region(lines: list[MarkdownLineRecord], start: int, end: int) -> bool:
    """Heuristic: region has multiple compact answer-key lines."""
    count = 0
    for i in range(start, min(end, len(lines))):
        if RE_ANSWER_KEY_COMPACT.match(classification_target(lines[i].raw_text)):
            count += 1
    return count >= 2


def _extract_answer_key_entries(
    lines: list[MarkdownLineRecord],
    solution_start: int | None,
) -> dict[int, list[AnswerCandidate]]:
    """Extract compact answer-key entries like '1. B'."""
    result: dict[int, list[AnswerCandidate]] = {}

    start_idx = 0
    if solution_start is not None:
        start_idx = next(
            (i for i, ln in enumerate(lines) if ln.line_number >= solution_start),
            len(lines),
        )

    # Scan for compact answer key runs after solution heading
    i = start_idx
    while i < len(lines):
        line = lines[i]
        match = RE_ANSWER_KEY_COMPACT.match(classification_target(line.raw_text))
        if not match:
            i += 1
            continue

        # Find extent of compact key run
        run_start = i
        run_end = i
        while run_end < len(lines) and RE_ANSWER_KEY_COMPACT.match(
            classification_target(lines[run_end].raw_text),
        ):
            run_end += 1

        if not _is_answer_key_region(lines, run_start, run_end):
            i = run_end
            continue

        for j in range(run_start, run_end):
            m = RE_ANSWER_KEY_COMPACT.match(classification_target(lines[j].raw_text))
            if not m:
                continue
            q_num = int(m.group(1))
            key_raw = m.group(2)
            answer = AnswerCandidate(
                question_number=q_num,
                question_number_raw=f"Q{q_num}",
                answer_key=key_raw.upper(),
                answer_key_raw=key_raw,
                source_line=lines[j].line_number,
                source_text_raw=lines[j].raw_text,
                confidence=0.75,
                issues=["compact_answer_key_low_confidence"],
            )
            result.setdefault(q_num, []).append(answer)

        i = run_end

    return result


def _extract_answers_from_solutions(
    lines_by_num: dict[int, MarkdownLineRecord],
    solution_blocks: dict[int, list[SolutionCandidate]],
) -> dict[int, list[AnswerCandidate]]:
    """Extract embedded answers from solution anchor first lines."""
    result: dict[int, list[AnswerCandidate]] = {}

    for q_num, sol_list in solution_blocks.items():
        for sol in sol_list:
            first_line = lines_by_num.get(sol.start_line)
            source_text = first_line.raw_text if first_line is not None else sol.raw_text.split("\n", 1)[0]
            source_line = first_line.line_number if first_line is not None else sol.start_line
            qn, key, key_raw, conf, issues = _extract_answer_from_line(source_text)
            if qn is None or key is None:
                qn, key, key_raw, conf, issues = _extract_answer_from_line(sol.raw_text)
                source_text = sol.raw_text.split("\n", 1)[0]
            if qn is not None and key is not None:
                result.setdefault(qn, []).append(
                    AnswerCandidate(
                        question_number=qn,
                        question_number_raw=f"S{qn}",
                        answer_key=key,
                        answer_key_raw=key_raw,
                        source_line=source_line,
                        source_text_raw=source_text,
                        confidence=conf,
                        issues=issues,
                    ),
                )

    return result


def _merge_answer_candidates(
    *sources: dict[int, list[AnswerCandidate]],
) -> tuple[dict[int, AnswerCandidate | None], dict[int, list[AnswerCandidate]], list[int]]:
    """
    Merge answer sources. Returns best answer per q, all candidates, duplicate conflicts.
    """
    all_by_q: dict[int, list[AnswerCandidate]] = {}
    for source in sources:
        for q_num, candidates in source.items():
            all_by_q.setdefault(q_num, []).extend(candidates)

    best: dict[int, AnswerCandidate | None] = {}
    conflicts: list[int] = []

    for q_num, candidates in all_by_q.items():
        keys = {c.answer_key for c in candidates if c.answer_key}
        if len(keys) > 1:
            conflicts.append(q_num)
            best[q_num] = None
        elif len(candidates) == 1:
            best[q_num] = candidates[0]
        elif candidates:
            # Same key repeated or pick highest confidence
            sorted_c = sorted(candidates, key=lambda c: c.confidence, reverse=True)
            if len(keys) == 1:
                best[q_num] = sorted_c[0]
            else:
                best[q_num] = sorted_c[0]

    return best, all_by_q, conflicts


def _pick_solution(solutions: list[SolutionCandidate] | None) -> SolutionCandidate | None:
    if not solutions:
        return None
    if len(solutions) == 1:
        return solutions[0]
    return max(solutions, key=lambda s: (s.confidence, -s.start_line))


def _determine_mapping_status(
    answer: AnswerCandidate | None,
    solution: SolutionCandidate | None,
    *,
    answer_conflict: bool,
    candidate_duplicate: bool,
    low_confidence: bool,
) -> MappingStatus:
    if answer_conflict or candidate_duplicate:
        return MappingStatus.DUPLICATE_CONFLICT
    has_answer = answer is not None
    has_solution = solution is not None

    if has_answer and has_solution:
        status = MappingStatus.MAPPED
    elif has_answer:
        status = MappingStatus.ANSWER_ONLY_MAPPED
    elif has_solution:
        status = MappingStatus.SOLUTION_ONLY_MAPPED
    else:
        return MappingStatus.NOT_AVAILABLE

    if low_confidence:
        return MappingStatus.NEEDS_REVIEW
    return status


def map_answers_solutions(
    lines: list[MarkdownLineRecord],
    blocks: list[MarkdownBlockRecord],  # noqa: ARG001 — reserved
    candidates: list[QuestionCandidate],
    package_dir: Path,
    *,
    line_source_path: str | None = None,
    content_lines_used: bool = False,
    mapping_source: str | None = None,
) -> AnswerSolutionMappingResult:
    """Produce answer/solution mappings for question candidates."""
    warnings: list[str] = []
    lines_by_num = {ln.line_number: ln for ln in lines}

    solution_anchor_count_seen = sum(1 for ln in lines if _is_solution_anchor_line(ln))
    first_solution_anchor_line = next(
        (ln.line_number for ln in lines if _is_solution_anchor_line(ln)),
        None,
    )

    solution_start = _find_solution_section_line(lines)
    if solution_start is None:
        warnings.append("no_solution_section_detected")

    solution_blocks, solution_stats = _collect_solution_blocks(lines, solution_start)
    answer_key_answers = _extract_answer_key_entries(lines, solution_start)
    embedded_answers = _extract_answers_from_solutions(lines_by_num, solution_blocks)

    best_answers, all_answers, answer_conflicts = _merge_answer_candidates(
        answer_key_answers,
        embedded_answers,
    )

    # Duplicate solution numbers
    duplicate_solution_numbers = sorted(
        q for q, sols in solution_blocks.items() if len(sols) > 1
    )
    if duplicate_solution_numbers:
        warnings.append(f"duplicate_solution_numbers:{duplicate_solution_numbers}")

    # Missing solution sequence
    sol_nums = sorted(solution_blocks.keys())
    missing_solutions: list[int] = []
    if sol_nums:
        expected = set(range(1, max(sol_nums) + 1))
        missing_solutions = sorted(expected - set(sol_nums))
        if missing_solutions:
            warnings.append(f"missing_solution_numbers:{missing_solutions}")

    # Duplicate question numbers in candidates
    candidate_q_counts: dict[int, int] = {}
    for c in candidates:
        if c.question_number is not None:
            candidate_q_counts[c.question_number] = candidate_q_counts.get(c.question_number, 0) + 1
    duplicate_candidate_qs = {q for q, n in candidate_q_counts.items() if n > 1}

    candidate_q_nums = {c.question_number for c in candidates if c.question_number is not None}
    unmatched_solutions = sorted(set(solution_blocks.keys()) - candidate_q_nums)

    mappings: list[QuestionAnswerSolutionMapping] = []

    for candidate in candidates:
        q_num = candidate.question_number
        issues: list[str] = list(candidate.issues)

        answer: AnswerCandidate | None = None
        solution: SolutionCandidate | None = None
        low_confidence = False

        if q_num is not None:
            answer = best_answers.get(q_num)
            if q_num in answer_conflicts:
                issues.append("conflicting_answer_keys")
                answer = None

            solution = _pick_solution(solution_blocks.get(q_num))
            if solution and "solution_contains_next_anchor" in solution.issues:
                issues.append("solution_contains_next_anchor")
                low_confidence = True

        candidate_duplicate = q_num is not None and q_num in duplicate_candidate_qs
        if candidate_duplicate:
            issues.append("duplicate_question_candidate_number")

        if answer and answer.confidence < 0.8:
            low_confidence = True
            issues.append("low_confidence_answer")
        if solution and solution.confidence < 0.8:
            low_confidence = True

        status = _determine_mapping_status(
            answer,
            solution,
            answer_conflict=q_num in answer_conflicts if q_num else False,
            candidate_duplicate=candidate_duplicate,
            low_confidence=low_confidence,
        )

        confidences = [c for c in [answer.confidence if answer else None, solution.confidence if solution else None, candidate.confidence] if c is not None]
        mapping_confidence = sum(confidences) / len(confidences) if confidences else 0.5

        mappings.append(
            QuestionAnswerSolutionMapping(
                question_id=candidate.question_id,
                question_number=q_num,
                answer_available=answer is not None,
                answer=answer,
                solution_available=solution is not None,
                solution=solution,
                mapping_status=status,
                confidence=mapping_confidence,
                issues=sorted(set(issues)),
            ),
        )

    mapped_count = sum(1 for m in mappings if m.mapping_status == MappingStatus.MAPPED)
    answer_only = sum(1 for m in mappings if m.mapping_status == MappingStatus.ANSWER_ONLY_MAPPED)
    solution_only = sum(1 for m in mappings if m.mapping_status == MappingStatus.SOLUTION_ONLY_MAPPED)
    not_available = sum(1 for m in mappings if m.mapping_status == MappingStatus.NOT_AVAILABLE)
    needs_review = sum(
        1
        for m in mappings
        if m.mapping_status
        in {MappingStatus.NEEDS_REVIEW, MappingStatus.DUPLICATE_CONFLICT}
    )

    all_answer_list = [a for answers in all_answers.values() for a in answers]
    solution_candidate_count = sum(len(v) for v in solution_blocks.values())
    unmapped_question_numbers = sorted(
        q
        for q, m in ((c.question_number, mappings[i]) for i, c in enumerate(candidates))
        if q is not None and m.mapping_status == MappingStatus.NOT_AVAILABLE
    )

    if solution_anchor_count_seen > 0 and mapped_count == 0 and answer_only == 0 and solution_only == 0:
        warnings.append("solution_anchors_detected_but_no_mappings_created")

    return AnswerSolutionMappingResult(
        package_dir=str(package_dir),
        status=MapperStatus.SUCCEEDED,
        mappings=mappings,
        total_question_candidates=len(candidates),
        answers_detected=len(all_answer_list),
        solutions_detected=sum(len(v) for v in solution_blocks.values()),
        mapped_count=mapped_count,
        answer_only_count=answer_only,
        solution_only_count=solution_only,
        not_available_count=not_available,
        needs_review_count=needs_review,
        duplicate_solution_numbers=duplicate_solution_numbers,
        unmatched_solution_numbers=unmatched_solutions,
        candidates_without_answers=sum(1 for m in mappings if not m.answer_available),
        candidates_without_solutions=sum(1 for m in mappings if not m.solution_available),
        content_lines_used=content_lines_used,
        line_source_path=line_source_path,
        mapping_source=mapping_source,
        solution_anchor_count_seen_by_mapper=solution_anchor_count_seen,
        answer_candidate_count=len(all_answer_list),
        solution_candidate_count=solution_candidate_count,
        first_solution_anchor_line=first_solution_anchor_line,
        multi_anchor_solution_lines_count=solution_stats.multi_anchor_solution_lines_count,
        solution_segments_created_from_splits=solution_stats.solution_segments_created_from_splits,
        suspicious_merged_solution_count=solution_stats.suspicious_merged_solution_count,
        unmapped_question_numbers=unmapped_question_numbers,
        warnings=warnings,
    )


def write_mapping_outputs(
    result: AnswerSolutionMappingResult,
    candidates: list[QuestionCandidate],
    paths: MappingPaths,
) -> None:
    """Write mapping JSON artifacts."""
    assert_output_contains(paths.package_dir, paths.mappings_dir)
    paths.mappings_dir.mkdir(parents=True, exist_ok=True)

    map_payload = [m.model_dump(mode="json") for m in result.mappings]
    assert_output_contains(paths.package_dir, paths.map_json)
    paths.map_json.write_text(json.dumps(map_payload, indent=2), encoding="utf-8")

    mapping_by_id = {m.question_id: m for m in result.mappings}
    combined = [
        CandidateWithMapping(
            question_id=c.question_id,
            question_number=c.question_number,
            mapping=mapping_by_id[c.question_id],
        ).model_dump(mode="json")
        for c in candidates
        if c.question_id in mapping_by_id
    ]
    assert_output_contains(paths.package_dir, paths.candidates_with_mappings_json)
    paths.candidates_with_mappings_json.write_text(
        json.dumps(combined, indent=2),
        encoding="utf-8",
    )

    report = {
        "package_dir": result.package_dir,
        "status": result.status.value,
        "created_at": result.created_at.isoformat(),
        "total_question_candidates": result.total_question_candidates,
        "answers_detected": result.answers_detected,
        "solutions_detected": result.solutions_detected,
        "mapped_count": result.mapped_count,
        "answer_only_count": result.answer_only_count,
        "solution_only_count": result.solution_only_count,
        "not_available_count": result.not_available_count,
        "needs_review_count": result.needs_review_count,
        "duplicate_solution_numbers": result.duplicate_solution_numbers,
        "unmatched_solution_numbers": result.unmatched_solution_numbers,
        "candidates_without_answers": result.candidates_without_answers,
        "candidates_without_solutions": result.candidates_without_solutions,
        "content_lines_used": result.content_lines_used,
        "line_source_path": result.line_source_path,
        "mapping_source": result.mapping_source,
        "solution_anchor_count_seen_by_mapper": result.solution_anchor_count_seen_by_mapper,
        "answer_candidate_count": result.answer_candidate_count,
        "solution_candidate_count": result.solution_candidate_count,
        "first_solution_anchor_line": result.first_solution_anchor_line,
        "multi_anchor_solution_lines_count": result.multi_anchor_solution_lines_count,
        "solution_segments_created_from_splits": result.solution_segments_created_from_splits,
        "suspicious_merged_solution_count": result.suspicious_merged_solution_count,
        "unmapped_question_numbers": result.unmapped_question_numbers,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    assert_output_contains(paths.package_dir, paths.report_json)
    paths.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")


def map_answers_solutions_package(package_dir: Path) -> AnswerSolutionMappingResult:
    """Load inputs, map answers/solutions, write outputs."""
    paths = build_mapping_paths(package_dir)

    missing: list[str] = []
    if not paths.lines_json.is_file():
        missing.append(f"Missing classified lines: {paths.lines_json}")
    if not paths.blocks_json.is_file():
        missing.append(f"Missing classified blocks: {paths.blocks_json}")
    if not paths.candidates_json.is_file():
        missing.append(f"Missing question candidates: {paths.candidates_json}")

    if missing:
        result = AnswerSolutionMappingResult(
            package_dir=str(paths.package_dir),
            status=MapperStatus.FAILED,
            errors=missing,
        )
        _try_write_failed_report(result, paths)
        raise MappingError(missing[0])

    lines, _, _, line_source_path, content_lines_used = load_lines_for_downstream(
        paths.package_dir,
    )
    blocks = load_blocks(paths.blocks_json)
    candidates = load_candidates(paths.candidates_json)
    mapping_source = "content_lines" if content_lines_used else "lines"

    result = map_answers_solutions(
        lines,
        blocks,
        candidates,
        paths.package_dir,
        line_source_path=line_source_path,
        content_lines_used=content_lines_used,
        mapping_source=mapping_source,
    )
    write_mapping_outputs(result, candidates, paths)
    return result


def _try_write_failed_report(result: AnswerSolutionMappingResult, paths: MappingPaths) -> None:
    try:
        paths.mappings_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "package_dir": result.package_dir,
            "status": result.status.value,
            "errors": result.errors,
        }
        paths.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except (PathValidationError, OSError):
        pass
