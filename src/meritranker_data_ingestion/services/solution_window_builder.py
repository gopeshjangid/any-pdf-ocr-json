"""Build solution windows from explanation/solution section evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from meritranker_data_ingestion.config import (
    DOCUMENT_EVIDENCE_JSON_NAME,
    EVIDENCE_DIR,
    MERGED_DOCUMENT_EVIDENCE_JSON_NAME,
    SOLUTION_WINDOWS_JSON_NAME,
    SOLUTION_WINDOWS_MD_NAME,
)
from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage, EvidenceLine
from meritranker_data_ingestion.schemas.solution_window import SolutionWindow, SolutionWindowsPackage
from meritranker_data_ingestion.services.document_section_splitter import (
    is_solution_style_anchor,
    split_document_sections,
)
from meritranker_data_ingestion.services.file_service import resolve_path

RE_SOLUTION_NUM = re.compile(
    r"^(?:\|?\s*)?(?:\*\*)?\s*(?:Q\.?\s*)?(\d{1,3})\s*[\.\)]",
    re.IGNORECASE,
)
RE_ANSWER_LABEL = re.compile(
    r"(?:Ans(?:wer)?[\.\:]?\s*)?"
    r"(?:\(\s*\*?([A-Ea-e])\s*\*?\s*\)|"
    r"([A-Ea-e])\s*[\)\.:]|"
    r"Answer\s*:\s*([A-Ea-e])\b)",
    re.IGNORECASE,
)
RE_NOISE = re.compile(
    r"(?:free\s+mock|download\s+pdf|www\.|subscribe\s+now)",
    re.IGNORECASE,
)
SOLUTION_WINDOW_MAX_RATIO = 1.30


@dataclass(frozen=True)
class SolutionWindowBuildResult:
    package: SolutionWindowsPackage
    json_path: Path
    md_path: Path


class SolutionWindowBuildError(Exception):
    """Raised when solution windows cannot be built."""


def build_solution_windows(
    package_dir: Path,
    *,
    expected_count: int | None = None,
) -> SolutionWindowBuildResult:
    resolved = resolve_path(package_dir)
    evidence = _load_evidence(resolved)
    split = split_document_sections(evidence.lines)

    if not split.solution_section_detected or split.solution_section_confidence < 0.5:
        pkg = SolutionWindowsPackage(
            source_file_name=evidence.source_file_name,
            solution_section_detected=False,
            solution_window_detection_status="no_section",
            warnings=["no_solution_section_detected"],
        )
        return _write_package(resolved, pkg)

    anchors = _find_solution_anchors(split.solution_lines)
    windows = _build_windows_from_anchors(anchors, split.solution_lines)
    windows = _dedupe_by_question_number(windows)

    detection_status = "ok"
    warnings: list[str] = []
    if expected_count and len(windows) > int(expected_count * SOLUTION_WINDOW_MAX_RATIO):
        detection_status = "over_detected"
        warnings.append("solution_over_detection")

    pkg = SolutionWindowsPackage(
        source_file_name=evidence.source_file_name,
        total_windows=len(windows),
        solution_section_detected=True,
        solution_section_confidence=split.solution_section_confidence,
        solution_window_detection_status=detection_status,
        windows=windows,
        warnings=warnings,
    )
    return _write_package(resolved, pkg)


def load_solution_windows(package_dir: Path) -> SolutionWindowsPackage | None:
    path = resolve_path(package_dir) / EVIDENCE_DIR / SOLUTION_WINDOWS_JSON_NAME
    if not path.exists():
        return None
    return SolutionWindowsPackage.model_validate_json(path.read_text(encoding="utf-8"))


def _load_evidence(package_dir: Path) -> DocumentEvidencePackage:
    merged = package_dir / EVIDENCE_DIR / MERGED_DOCUMENT_EVIDENCE_JSON_NAME
    doc = package_dir / EVIDENCE_DIR / DOCUMENT_EVIDENCE_JSON_NAME
    path = merged if merged.exists() else doc
    if not path.exists():
        raise SolutionWindowBuildError(f"Missing evidence at {path}")
    return DocumentEvidencePackage.model_validate_json(path.read_text(encoding="utf-8"))


def _find_solution_anchors(
    lines: list[EvidenceLine],
) -> list[tuple[int, int, str | None, str | None]]:
    """Return (line_index, qnum, answer_label_raw, canonical_label)."""
    anchors: list[tuple[int, int, str | None, str | None]] = []
    for idx, line in enumerate(lines):
        text = line.text_raw.strip()
        if not text or RE_NOISE.search(text):
            continue
        if not is_solution_style_anchor(text):
            continue
        num_match = RE_SOLUTION_NUM.match(text)
        if not num_match:
            continue
        qnum = int(num_match.group(1))
        label_raw, label = _extract_answer_label(text)
        if not label:
            continue
        anchors.append((idx, qnum, label_raw, label))
    return anchors


def _build_windows_from_anchors(
    anchors: list[tuple[int, int, str | None, str | None]],
    lines: list[EvidenceLine],
) -> list[SolutionWindow]:
    windows: list[SolutionWindow] = []
    for order, (start_idx, qnum, label_raw, label) in enumerate(anchors, start=1):
        end_idx = anchors[order][0] if order < len(anchors) else len(lines)
        if order < len(anchors):
            end_idx = anchors[order][0]
        chunk = lines[start_idx:end_idx]
        text_parts: list[str] = []
        line_ids: list[str] = []
        pages: set[int] = set()

        for line in chunk:
            if RE_NOISE.search(line.text_raw):
                continue
            line_ids.append(line.line_id)
            if line.page_number is not None:
                pages.add(line.page_number)
            if line.text_raw.strip():
                text_parts.append(line.text_raw.strip())

        solution_text = "\n".join(text_parts).strip()
        solution_text = _strip_anchor_prefix(solution_text, qnum, label_raw)

        windows.append(
            SolutionWindow(
                solution_window_id=f"sol_{order:04d}",
                source_question_number=qnum,
                answer_label_raw=label_raw,
                answer_label=label,
                solution_text_raw=solution_text,
                source_pages=sorted(pages),
                line_ids=line_ids,
                confidence=0.95 if label and solution_text else 0.5,
                issues=[],
            ),
        )
    return windows


def _dedupe_by_question_number(windows: list[SolutionWindow]) -> list[SolutionWindow]:
    best: dict[int, SolutionWindow] = {}
    for window in windows:
        existing = best.get(window.source_question_number)
        if existing is None or window.confidence > existing.confidence:
            best[window.source_question_number] = window
    return sorted(best.values(), key=lambda w: w.source_question_number)


def _extract_answer_label(text: str) -> tuple[str | None, str | None]:
    match = RE_ANSWER_LABEL.search(text)
    if not match:
        return None, None
    letter = next((g for g in match.groups() if g), None)
    if not letter:
        return None, None
    raw = match.group(0).strip()
    return raw, letter.upper()


def _strip_anchor_prefix(text: str, qnum: int, label_raw: str | None) -> str:
    if not text:
        return text
    prefix_patterns = [
        rf"^\*\*{qnum}\.\*\*\s*",
        rf"^{qnum}\.\s*",
        rf"^\*\*{qnum}\.\*\*",
    ]
    if label_raw:
        prefix_patterns.append(re.escape(label_raw))
    out = text
    for pat in prefix_patterns:
        out = re.sub(pat, "", out, count=1, flags=re.IGNORECASE).strip()
    return out


def _write_package(package_dir: Path, pkg: SolutionWindowsPackage) -> SolutionWindowBuildResult:
    ev_dir = package_dir / EVIDENCE_DIR
    ev_dir.mkdir(parents=True, exist_ok=True)
    json_path = ev_dir / SOLUTION_WINDOWS_JSON_NAME
    md_path = ev_dir / SOLUTION_WINDOWS_MD_NAME
    json_path.write_text(pkg.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(_render_md(pkg), encoding="utf-8")
    return SolutionWindowBuildResult(package=pkg, json_path=json_path, md_path=md_path)


def _render_md(pkg: SolutionWindowsPackage) -> str:
    return "\n".join(
        [
            "# Solution Windows",
            "",
            f"- total_windows: {pkg.total_windows}",
            f"- solution_section_detected: {pkg.solution_section_detected}",
            f"- solution_window_detection_status: {pkg.solution_window_detection_status}",
            "",
        ],
    )
