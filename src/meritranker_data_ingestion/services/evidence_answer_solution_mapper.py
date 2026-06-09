"""Deterministic answer/solution map from solution windows (evidence path)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    EVIDENCE_ANSWER_SOLUTION_MAP_JSON_NAME,
    EVIDENCE_ANSWER_SOLUTION_MAP_MD_NAME,
    EVIDENCE_DIR,
)
from meritranker_data_ingestion.schemas.evidence_answer_solution_map import (
    AnswerSolutionMapEntry,
    AnswerSolutionMapPackage,
)
from meritranker_data_ingestion.services.file_service import resolve_path
from meritranker_data_ingestion.services.solution_window_builder import (
    SOLUTION_WINDOW_MAX_RATIO,
    SolutionWindowBuildError,
    build_solution_windows,
    load_solution_windows,
)


@dataclass(frozen=True)
class EvidenceAnswerSolutionMapResult:
    package: AnswerSolutionMapPackage
    json_path: Path
    md_path: Path


class EvidenceAnswerSolutionMapError(Exception):
    """Raised when evidence answer/solution map cannot be built."""


def build_evidence_answer_solution_map(
    package_dir: Path,
    *,
    expected_count: int | None = None,
) -> EvidenceAnswerSolutionMapResult:
    resolved = resolve_path(package_dir)
    sol_pkg = load_solution_windows(resolved)
    if sol_pkg is None:
        try:
            sol_result = build_solution_windows(resolved, expected_count=expected_count)
            sol_pkg = sol_result.package
        except SolutionWindowBuildError as exc:
            raise EvidenceAnswerSolutionMapError(str(exc)) from exc

    entries: list[AnswerSolutionMapEntry] = []
    warnings: list[str] = []
    by_qnum: dict[int, AnswerSolutionMapEntry] = {}

    for window in sol_pkg.windows:
        if not window.answer_label:
            continue
        issues = list(window.issues)
        entry = AnswerSolutionMapEntry(
            question_number=window.source_question_number,
            answer_label=window.answer_label,
            answer_label_raw=window.answer_label_raw,
            solution_text=window.solution_text_raw,
            source="solution_section",
            confidence=window.confidence,
            line_ids=list(window.line_ids),
            issues=issues,
        )
        if window.source_question_number in by_qnum:
            warnings.append(f"duplicate_solution_number:{window.source_question_number}")
            continue
        by_qnum[window.source_question_number] = entry
        entries.append(entry)

    entries.sort(key=lambda e: e.question_number)
    answers_detected = sum(1 for e in entries if e.answer_label)
    solutions_detected = sum(1 for e in entries if e.solution_text.strip())

    map_status = "ok"
    map_usable = True
    if sol_pkg.solution_window_detection_status == "over_detected":
        map_usable = False
        map_status = "over_detected"
        warnings.append("answer_solution_map_over_detected")
    if expected_count and len(entries) > int(expected_count * SOLUTION_WINDOW_MAX_RATIO):
        map_usable = False
        map_status = "over_detected"
        warnings.append("answer_solution_map_over_detected")
    if sol_pkg.solution_section_confidence < 0.5:
        map_usable = False
        map_status = "low_confidence"
        warnings.append("solution_section_low_confidence")

    pkg = AnswerSolutionMapPackage(
        source_file_name=sol_pkg.source_file_name,
        total_mapped=len(entries),
        answers_detected=answers_detected,
        solutions_detected=solutions_detected,
        entries=entries,
        map_usable=map_usable,
        answer_solution_map_status=map_status,
        warnings=warnings,
    )
    return _write_package(resolved, pkg)


def load_evidence_answer_solution_map(package_dir: Path) -> AnswerSolutionMapPackage | None:
    path = resolve_path(package_dir) / EVIDENCE_DIR / EVIDENCE_ANSWER_SOLUTION_MAP_JSON_NAME
    if not path.exists():
        return None
    return AnswerSolutionMapPackage.model_validate_json(path.read_text(encoding="utf-8"))


def map_index(package: AnswerSolutionMapPackage) -> dict[int, AnswerSolutionMapEntry]:
    if not package.map_usable:
        return {}
    return {entry.question_number: entry for entry in package.entries if entry.answer_label}


def _write_package(
    package_dir: Path,
    pkg: AnswerSolutionMapPackage,
) -> EvidenceAnswerSolutionMapResult:
    ev_dir = package_dir / EVIDENCE_DIR
    ev_dir.mkdir(parents=True, exist_ok=True)
    json_path = ev_dir / EVIDENCE_ANSWER_SOLUTION_MAP_JSON_NAME
    md_path = ev_dir / EVIDENCE_ANSWER_SOLUTION_MAP_MD_NAME
    json_path.write_text(pkg.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(_render_md(pkg), encoding="utf-8")
    return EvidenceAnswerSolutionMapResult(
        package=pkg,
        json_path=json_path,
        md_path=md_path,
    )


def _render_md(pkg: AnswerSolutionMapPackage) -> str:
    lines = [
        "# Answer Solution Map",
        "",
        f"- total_mapped: {pkg.total_mapped}",
        f"- answers_detected: {pkg.answers_detected}",
        f"- solutions_detected: {pkg.solutions_detected}",
        f"- map_usable: {pkg.map_usable}",
        f"- answer_solution_map_status: {pkg.answer_solution_map_status}",
        "",
    ]
    if pkg.warnings:
        lines.append("## Warnings")
        lines.extend(f"- {w}" for w in pkg.warnings[:20])
        lines.append("")
    return "\n".join(lines)
