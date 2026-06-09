"""Build deterministic local question windows from evidence (Part 14C)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    MERGED_DOCUMENT_EVIDENCE_JSON_NAME,
    QUESTION_WINDOWS_JSON_NAME,
    QUESTION_WINDOWS_MD_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage, EvidenceLine
from meritranker_data_ingestion.schemas.question_window import (
    QuestionWindow,
    QuestionWindowStatus,
    QuestionWindowsPackage,
)
from meritranker_data_ingestion.services.document_section_splitter import (
    DocumentSectionSplit,
    DocumentSectionType,
    is_solution_style_anchor,
    split_document_sections,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.answer_key_zone_detector import (
    count_answer_key_pairs,
    find_answer_key_zone_start,
    is_answer_key_anchor_line,
    is_answer_key_line,
)
from meritranker_data_ingestion.services.unsupported_layout_detector import detect_unsupported_layout
from meritranker_data_ingestion.services.ocr_role_hints import (
    is_chosen_option_instruction_line,
    is_chosen_option_metadata_line,
)

RE_Q_ANCHOR = re.compile(
    r"^(?:\|+\s*)*(?:[-•*]\s*)?(?:\*\*)?(?:Q\.?\s*)?(\d{1,3})\s*[\.\)]",
    re.IGNORECASE,
)
RE_Q_INLINE = re.compile(r"\bQ\.?\s*(\d{1,3})\b", re.IGNORECASE)
RE_OPTION = re.compile(
    r"(?:^|\s|>|-\s*)"
    r"(?:"
    r"(\d+)\s*[\.\)]\s*\S"
    r"|\(\*?[A-Da-d]\*?\)\s*\S"
    r"|[A-Da-d]\.\s+\S"
    r")",
    re.IGNORECASE,
)
RE_CHOSEN = re.compile(r"chosen\s*option", re.IGNORECASE)
RE_STATUS = re.compile(r"status\s*:", re.IGNORECASE)
RE_QUESTION_ID = re.compile(r"question\s*id", re.IGNORECASE)
RE_NOISE = re.compile(
    r"(?:free\s+mock|download\s+pdf|www\.|subscribe\s+now|previous\s+papers?|"
    r"all\s+exams|one\s+subscription|attempt\s+free\s+mock|personalised\s+report|"
    r"unlimited\s+re-?attempt|exam\s+covered|refund|mock\s+tests?)",
    re.IGNORECASE,
)
RE_ANS_PREFIX = re.compile(r"\bans\s*\d", re.IGNORECASE)
RE_BARE_OPTION_LINE = re.compile(
    r"^(?:"
    r"\d+\.\s*\d"
    r"|\(\*?[A-Ea-e]\*?\)\s*"
    r"|\([A-Ea-e]\)\s*"
    r"|\d+\.\s+[A-Za-z]{1,25}$"
    r")",
    re.IGNORECASE,
)
RE_STRONG_QUESTION_ANCHOR = re.compile(
    r"^(?:\|+\s*)*(?:[-•*]\s*)?(?:\*\*)?\s*(?:Q\.?\s*)?\d{1,3}\s*[\.\)]\s+\S",
    re.IGNORECASE,
)
RE_EXPLICIT_Q_ANCHOR = re.compile(
    r"^(?:\|+\s*)*(?:[-•*]\s*)?(?:\*\*)?\s*Q\.?\s*(\d{1,3})\b",
    re.IGNORECASE,
)
RE_PIPE_Q_ANCHOR = re.compile(
    r"^\|\s*Q\.?\s*(\d{1,3})\s*\|",
    re.IGNORECASE,
)
QUESTION_WINDOW_MIN_RATIO = 0.70


@dataclass(frozen=True)
class QuestionWindowBuildResult:
    package: QuestionWindowsPackage
    json_path: Path
    md_path: Path
    line_id_to_window: dict[str, str]


class QuestionWindowBuildError(Exception):
    """Raised when question windows cannot be built."""


def build_question_windows(
    package_dir: Path,
    *,
    expected_count: int | None = None,
) -> QuestionWindowBuildResult:
    """Build local question windows from merged or document evidence."""
    resolved = resolve_path(package_dir)
    evidence = _load_evidence(resolved)
    split = split_document_sections(evidence.lines)
    layout = detect_unsupported_layout(
        evidence,
        answer_source_mode=_answer_source_hint(evidence),
    )

    all_anchors = _find_anchors(evidence.lines)
    question_anchors = _select_question_anchors(all_anchors, split)
    question_anchors = _prefer_question_anchors(
        question_anchors,
        solution_heading_index=split.solution_heading_index,
    )
    section_split_fallback_used = False

    windows, line_id_to_window = _build_windows(
        question_anchors,
        evidence.lines,
        layout,
    )

    if expected_count and len(windows) < int(expected_count * QUESTION_WINDOW_MIN_RATIO):
        fallback_anchors = _select_question_anchors(
            all_anchors,
            split,
            ignore_heading=True,
        )
        fallback_anchors = _prefer_question_anchors(
            fallback_anchors,
            solution_heading_index=split.solution_heading_index,
        )
        if len(fallback_anchors) > len(question_anchors):
            section_split_fallback_used = True
            windows, line_id_to_window = _build_windows(
                fallback_anchors,
                evidence.lines,
                layout,
            )

    warnings: list[str] = []
    mixed = False
    build_status = "ok"
    if section_split_fallback_used:
        warnings.append("section_split_fallback_used")
    if expected_count and len(windows) > int(expected_count * 1.3):
        mixed = True
        warnings.append("question_solution_section_mixed")
    elif split.solution_section_detected and len(windows) > 130:
        mixed = True
        warnings.append("question_solution_section_mixed")
    if expected_count and len(windows) < int(expected_count * QUESTION_WINDOW_MIN_RATIO):
        build_status = "failed"
        warnings.append("question_window_build_failed")
    if len(windows) == 0:
        build_status = "failed"

    pkg = QuestionWindowsPackage(
        source_file_name=evidence.source_file_name,
        total_windows=len(windows),
        windows_with_4_options=sum(
            1 for w in windows if w.status == QuestionWindowStatus.READY
        ),
        windows_with_chosen_option=sum(1 for w in windows if w.chosen_option_line_ids),
        repeated_question_numbers=layout.repeated_question_numbers,
        unsupported_layout_detected=layout.unsupported_layout_detected,
        line_reuse_warnings=_detect_line_reuse(windows),
        solution_section_detected=split.solution_section_detected,
        question_solution_section_mixed=mixed,
        question_window_build_status=build_status,
        section_split_status="fallback" if section_split_fallback_used else "ok",
        section_split_fallback_used=section_split_fallback_used,
        windows=windows,
        warnings=warnings,
    )

    ev_dir = resolved / EVIDENCE_DIR
    ev_dir.mkdir(parents=True, exist_ok=True)
    json_path = ev_dir / QUESTION_WINDOWS_JSON_NAME
    md_path = ev_dir / QUESTION_WINDOWS_MD_NAME
    json_path.write_text(pkg.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(_render_md(pkg), encoding="utf-8")

    return QuestionWindowBuildResult(
        package=pkg,
        json_path=json_path,
        md_path=md_path,
        line_id_to_window=line_id_to_window,
    )


def load_question_windows(package_dir: Path) -> QuestionWindowsPackage | None:
    path = resolve_path(package_dir) / EVIDENCE_DIR / QUESTION_WINDOWS_JSON_NAME
    if not path.exists():
        return None
    return QuestionWindowsPackage.model_validate_json(path.read_text(encoding="utf-8"))


def window_line_ids_for_question(
    windows_pkg: QuestionWindowsPackage,
    *,
    question_number: int | None,
    window_id: str | None = None,
) -> set[str]:
    """Return allowed line_ids for a question's local window."""
    for window in windows_pkg.windows:
        if window_id and window.window_id == window_id:
            return set(window.line_ids)
        if question_number is not None and window.parsed_question_number == question_number:
            return set(window.line_ids)
    return set()


def _select_question_anchors(
    all_anchors: list[tuple[int, str, int | None]],
    split: DocumentSectionSplit,
    *,
    ignore_heading: bool = False,
) -> list[tuple[int, str, int | None]]:
    heading_idx = split.solution_heading_index
    strict_solution_cutoff = _is_strict_solution_cutoff(split)
    selected: list[tuple[int, str, int | None]] = []
    for idx, raw, qnum in all_anchors:
        if is_solution_style_anchor(raw):
            continue
        if is_answer_key_anchor_line(raw):
            continue
        if not ignore_heading and heading_idx is not None and idx >= heading_idx:
            if strict_solution_cutoff or not _is_continued_question_anchor(raw):
                continue
        selected.append((idx, raw, qnum))
    return selected


def _is_strict_solution_cutoff(split: DocumentSectionSplit) -> bool:
    if not split.solution_section_detected or split.solution_heading_index is None:
        return False
    for boundary in split.boundaries:
        if boundary.section_type in {
            DocumentSectionType.EXPLANATION,
            DocumentSectionType.SOLUTION,
        }:
            return True
    return False


def _is_continued_question_anchor(raw: str) -> bool:
    """Allow MCQ anchors after mid-document answer-key/ad headings."""
    stripped = raw.strip()
    pipe_stripped = stripped.lstrip("|").strip()
    if RE_STRONG_QUESTION_ANCHOR.match(stripped) or RE_STRONG_QUESTION_ANCHOR.match(pipe_stripped):
        return True
    if not _is_question_anchor_line(stripped):
        return False
    match = RE_Q_ANCHOR.match(pipe_stripped) or RE_Q_ANCHOR.match(stripped)
    if not match:
        return False
    remainder = stripped[match.end() :].strip().lstrip("|").strip()
    return len(remainder) >= 12 and not is_answer_key_line(stripped)


def _build_windows(
    anchors: list[tuple[int, str, int | None]],
    lines: list[EvidenceLine],
    layout,
) -> tuple[list[QuestionWindow], dict[str, str]]:
    windows: list[QuestionWindow] = []
    line_id_to_window: dict[str, str] = {}

    for order, (start_idx, qnum_raw, qnum) in enumerate(anchors, start=1):
        end_idx = anchors[order][0] if order < len(anchors) else len(lines)
        if order < len(anchors):
            end_idx = anchors[order][0]
        window_lines = [
            ln for ln in lines[start_idx:end_idx]
            if not is_answer_key_line(ln.text_raw)
        ]
        window_id = f"qw_{order:04d}"

        option_ids: list[str] = []
        chosen_ids: list[str] = []
        status_ids: list[str] = []
        answer_ids: list[str] = []
        solution_ids: list[str] = []
        anchor_ids = [lines[start_idx].line_id]
        engines: set[str] = set()
        issues: list[str] = []

        for line in window_lines:
            engines.add(line.source_extractor)
            text = line.text_raw
            if RE_NOISE.search(text):
                continue
            if RE_OPTION.search(text) or RE_ANS_PREFIX.search(text):
                option_ids.append(line.line_id)
            if is_chosen_option_metadata_line(text):
                chosen_ids.append(line.line_id)
            if RE_STATUS.search(text):
                status_ids.append(line.line_id)
            if RE_QUESTION_ID.search(text):
                status_ids.append(line.line_id)

        option_count = _count_window_options(window_lines)
        status = QuestionWindowStatus.READY
        if option_count < 4:
            status = QuestionWindowStatus.INCOMPLETE_OPTIONS
            issues.append(f"options_found:{option_count}")

        window = QuestionWindow(
            window_id=window_id,
            source_question_number_raw=qnum_raw,
            parsed_question_number=qnum,
            global_order=order,
            start_line_id=lines[start_idx].line_id,
            end_line_id=window_lines[-1].line_id if window_lines else None,
            line_ids=[ln.line_id for ln in window_lines],
            question_anchor_line_ids=anchor_ids,
            option_candidate_line_ids=_dedupe(option_ids),
            chosen_option_line_ids=_dedupe(chosen_ids),
            status_line_ids=_dedupe(status_ids),
            answer_candidate_line_ids=answer_ids,
            solution_candidate_line_ids=solution_ids,
            source_engines=sorted(engines),
            confidence=0.8 if option_count >= 4 else 0.4,
            issues=issues,
            status=status,
        )
        windows.append(window)
        for lid in window.line_ids:
            line_id_to_window[lid] = window_id

    return windows, line_id_to_window


def _load_evidence(package_dir: Path) -> DocumentEvidencePackage:
    merged = package_dir / EVIDENCE_DIR / MERGED_DOCUMENT_EVIDENCE_JSON_NAME
    doc = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    path = merged if merged.exists() else doc
    if not path.exists():
        raise QuestionWindowBuildError(f"Missing evidence at {path}")
    return DocumentEvidencePackage.model_validate_json(path.read_text(encoding="utf-8"))


def _find_anchors(lines: list[EvidenceLine]) -> list[tuple[int, str, int | None]]:
    answer_key_zone = find_answer_key_zone_start(lines)
    anchors: list[tuple[int, str, int | None]] = []
    seen_at: dict[int, int] = {}
    for idx, line in enumerate(lines):
        text = line.text_raw.strip()
        if RE_NOISE.search(text):
            continue
        if not _is_question_anchor_line(text):
            continue
        if answer_key_zone is not None and idx == answer_key_zone and is_answer_key_line(text):
            continue
        match = RE_Q_ANCHOR.match(text) or RE_Q_INLINE.search(text)
        if not match:
            continue
        raw = text
        try:
            qnum = int(match.group(1))
        except (ValueError, IndexError):
            qnum = None
        if qnum is not None and qnum in seen_at and idx - seen_at[qnum] < 5:
            continue
        if qnum is not None:
            seen_at[qnum] = idx
        anchors.append((idx, raw, qnum))
    return anchors


def _prefer_question_anchors(
    anchors: list[tuple[int, str, int | None]],
    *,
    solution_heading_index: int | None = None,
) -> list[tuple[int, str, int | None]]:
    """Keep the strongest anchor per question number (reject answer-key duplicates)."""
    by_number: dict[int, tuple[int, str, int | None]] = {}
    unnumbered: list[tuple[int, str, int | None]] = []
    for anchor in anchors:
        qnum = anchor[2]
        if qnum is None:
            unnumbered.append(anchor)
            continue
        current = by_number.get(qnum)
        if current is None or _anchor_rank(
            anchor,
            solution_heading_index=solution_heading_index,
        ) > _anchor_rank(current, solution_heading_index=solution_heading_index):
            by_number[qnum] = anchor
    ordered = sorted(by_number.values(), key=lambda item: item[0])
    return ordered + sorted(unnumbered, key=lambda item: item[0])


def _anchor_rank(
    anchor: tuple[int, str, int | None],
    *,
    solution_heading_index: int | None = None,
) -> int:
    idx, raw, _ = anchor
    score = len(raw)
    if solution_heading_index is not None and idx >= solution_heading_index:
        score -= 10000
    if is_solution_style_anchor(raw):
        score -= 5000
    if _is_instruction_anchor_line(raw):
        score -= 20000
    if RE_EXPLICIT_Q_ANCHOR.search(raw) or RE_PIPE_Q_ANCHOR.search(raw):
        score += 500
    if is_answer_key_anchor_line(raw):
        score -= 1000
    if "|" in raw and count_answer_key_pairs(raw) >= 1:
        score -= 500
    return score


def _count_window_options(window_lines: list[EvidenceLine]) -> int:
    keys: set[str] = set()
    for line in window_lines:
        text = line.text_raw
        for match in re.finditer(r"(?:^|\s)(\d)\s*[\.\)]\s*\S", text):
            keys.add(match.group(1))
        for match in re.finditer(r"\(\*?([A-Da-d])\*?\)\s*\S", text):
            keys.add(match.group(1).upper())
        for match in re.finditer(r"(?:^|\s|-\s*)([A-Da-d])\.\s+\S", text):
            keys.add(match.group(1).upper())
    return len(keys)


def _detect_line_reuse(windows: list[QuestionWindow]) -> list[str]:
    usage: dict[str, list[str]] = {}
    for window in windows:
        for lid in window.option_candidate_line_ids:
            usage.setdefault(lid, []).append(window.window_id)
    warnings: list[str] = []
    for lid, wids in usage.items():
        if len(wids) > 1:
            warnings.append(f"cross_window_option_line_reuse:{lid}:{'|'.join(wids)}")
    return warnings


def _answer_source_hint(evidence: DocumentEvidencePackage) -> str:
    from meritranker_data_ingestion.services.ocr_role_hints import has_chosen_option_metadata

    full = "\n".join(line.text_raw for line in evidence.lines[:300])
    if re.search(
        r"\bAns(?:wer)?\s*(?:\*{1,2})?\s*[\.\:]?\s*\(?\s*[A-Da-d]\s*\)?",
        full,
        re.IGNORECASE,
    ):
        return "inline_answer"
    if has_chosen_option_metadata(evidence.lines):
        return "chosen_option_metadata_only"
    return "unavailable"


def _strip_pipe_prefix(text: str) -> str:
    stripped = text.strip()
    while stripped.startswith("|"):
        stripped = stripped.lstrip("|").strip()
    return stripped


def _is_instruction_anchor_line(text: str) -> bool:
    if is_chosen_option_instruction_line(text):
        return True
    lowered = text.lower()
    if re.search(
        r"^\s*(?:[-•*]\s*)?(?:\*\*)?\s*\d{1,3}\s*[\.\)]\s+"
        r".*(?:options?\s+shown|tick\s+icon|correct\s+answer\s+will|"
        r"incorrect\s+answer\s+will|negative\s+mark)",
        lowered,
    ):
        return True
    return False


def _is_question_anchor_line(text: str) -> bool:
    stripped = _strip_pipe_prefix(text)
    if not stripped or RE_BARE_OPTION_LINE.match(stripped):
        return False
    if _is_instruction_anchor_line(stripped):
        return False
    if is_answer_key_anchor_line(stripped):
        return False
    if is_solution_style_anchor(stripped):
        return False
    if RE_STRONG_QUESTION_ANCHOR.match(text.strip()) or RE_STRONG_QUESTION_ANCHOR.match(stripped):
        return True
    if re.search(r"\bQ\.?\s*\d", stripped, re.IGNORECASE):
        return True
    match = RE_Q_ANCHOR.match(text.strip()) or RE_Q_ANCHOR.match(stripped)
    if not match:
        return False
    rest = stripped[match.end() :].strip()
    if len(rest) < 8:
        return False
    if re.fullmatch(r"\d+", rest):
        return False
    return True


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _render_md(pkg: QuestionWindowsPackage) -> str:
    lines = [
        "# Question Windows",
        "",
        f"- total_windows: {pkg.total_windows}",
        f"- windows_with_4_options: {pkg.windows_with_4_options}",
        f"- windows_with_chosen_option: {pkg.windows_with_chosen_option}",
        f"- unsupported_layout_detected: {pkg.unsupported_layout_detected}",
        f"- repeated_question_numbers: {pkg.repeated_question_numbers}",
        f"- solution_section_detected: {pkg.solution_section_detected}",
        f"- question_solution_section_mixed: {pkg.question_solution_section_mixed}",
        f"- question_window_build_status: {pkg.question_window_build_status}",
        f"- section_split_status: {pkg.section_split_status}",
        f"- section_split_fallback_used: {pkg.section_split_fallback_used}",
        "",
    ]
    if pkg.warnings:
        lines.append("## Warnings")
        lines.extend(f"- {w}" for w in pkg.warnings)
        lines.append("")
    if pkg.line_reuse_warnings:
        lines.append("## Line reuse warnings")
        lines.extend(f"- {w}" for w in pkg.line_reuse_warnings[:20])
        lines.append("")
    return "\n".join(lines)
