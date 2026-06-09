"""Response-sheet table option parsing (Part 14B)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.document_evidence import EvidenceLine, SourceSpan
from meritranker_data_ingestion.schemas.semantic_binding import SemanticBoundOption, SemanticBoundQuestion
from meritranker_data_ingestion.services.option_key_normalizer import (
    NormalizedOptionKey,
    parse_multiple_numeric_options,
    parse_numeric_option_line,
)

RE_TABLE_ROW = re.compile(r"^\|")
RE_ANS_CELL = re.compile(r"\bAns\b", re.IGNORECASE)


def parse_response_sheet_options_from_text(text: str) -> list[NormalizedOptionKey]:
    """Parse numeric options from response-sheet table cells."""
    if not text.strip():
        return []

    cell_text = _extract_option_cell_text(text)
    if not cell_text:
        return []

    options = parse_multiple_numeric_options(cell_text)
    if options:
        return options

    for segment in re.split(r"[\n|]+", cell_text):
        segment = segment.strip()
        if not segment or RE_ANS_CELL.fullmatch(segment):
            continue
        parsed = parse_numeric_option_line(segment)
        if parsed:
            options.append(parsed)

    return _dedupe_by_index(options)


def collect_response_sheet_options_near_question(
    evidence_lines: list[EvidenceLine],
    *,
    question_number: int,
    window: int = 8,
) -> list[NormalizedOptionKey]:
    """Collect table options from lines near a question anchor."""
    anchor_idx = _find_question_anchor_index(evidence_lines, question_number)
    if anchor_idx is None:
        return []

    collected: list[NormalizedOptionKey] = []
    for line in evidence_lines[anchor_idx : anchor_idx + window]:
        if not _looks_like_response_sheet_option_line(line.text_raw):
            continue
        collected.extend(parse_response_sheet_options_from_text(line.text_raw))

    return _dedupe_by_index(collected)


def bind_response_sheet_options_to_item(
    item: SemanticBoundQuestion,
    evidence_lines: list[EvidenceLine],
    *,
    allowed_line_ids: set[str] | None = None,
) -> int:
    """Fill missing/blank options from nearby response-sheet evidence."""
    if item.question_number is None:
        return 0

    scoped_lines = (
        [line for line in evidence_lines if line.line_id in allowed_line_ids]
        if allowed_line_ids
        else evidence_lines
    )
    parsed = collect_response_sheet_options_near_question(
        scoped_lines,
        question_number=item.question_number,
    )
    if not parsed:
        return 0

    changed = 0
    by_key = {opt.key: opt for opt in parsed}

    if not item.options:
        for norm in parsed[:4]:
            item.options.append(_to_bound_option(norm, evidence_lines))
            changed += 1
        return changed

    for option in item.options:
        if (option.key or option.key_raw or "").strip() and option.text_raw.strip():
            split = parse_numeric_option_line(option.text_raw)
            if split and not (option.key or "").strip():
                option.key = split.key
                option.key_raw = split.key_raw
                option.text_raw = split.text_raw
                changed += 1
            continue

        for norm in parsed:
            if _option_matches_slot(option, norm):
                if allowed_line_ids and not _norm_in_allowed(norm, allowed_line_ids, scoped_lines):
                    continue
                option.key = norm.key
                option.key_raw = norm.key_raw
                option.text_raw = norm.text_raw
                if not option.source_spans:
                    option.source_spans = _span_for_option(norm, scoped_lines)
                option.option_source_line_id = (
                    option.source_spans[0].line_id if option.source_spans else None
                )
                option.option_source_window_id = item.window_id
                changed += 1
                break
        else:
            if not option.text_raw.strip():
                for key, norm in by_key.items():
                    if key not in {o.key for o in item.options if o.key}:
                        option.key = norm.key
                        option.key_raw = norm.key_raw
                        option.text_raw = norm.text_raw
                        if not option.source_spans:
                            option.source_spans = _span_for_option(norm, evidence_lines)
                        changed += 1
                        break

    return changed


def _looks_like_response_sheet_option_line(text: str) -> bool:
    lowered = text.lower()
    if RE_TABLE_ROW.match(text.strip()):
        return bool(re.search(r"\d+\s*[\.\)]", text))
    return "ans" in lowered and bool(re.search(r"\d+\s*[\.\)]", text))


def _extract_option_cell_text(text: str) -> str:
    if "|" not in text:
        return text
    parts = [part.strip() for part in text.split("|") if part.strip()]
    for part in parts:
        if RE_ANS_CELL.search(part):
            continue
        if re.search(r"\d+\s*[\.\)]", part):
            return part
    return " ".join(parts)


def _find_question_anchor_index(
    lines: list[EvidenceLine],
    question_number: int,
) -> int | None:
    patterns = (
        re.compile(rf"^\*?\*?{question_number}\s*[\.\)]"),
        re.compile(rf"\bQ\.?\s*{question_number}\b", re.IGNORECASE),
    )
    for idx, line in enumerate(lines):
        text = line.text_raw.strip()
        if any(pat.search(text) for pat in patterns):
            return idx
    return None


def _to_bound_option(
    norm: NormalizedOptionKey,
    evidence_lines: list[EvidenceLine],
) -> SemanticBoundOption:
    return SemanticBoundOption(
        key=norm.key,
        key_raw=norm.key_raw,
        text_raw=norm.text_raw,
        source_spans=_span_for_option(norm, evidence_lines),
    )


def _span_for_option(
    norm: NormalizedOptionKey,
    evidence_lines: list[EvidenceLine],
) -> list[SourceSpan]:
    for line in evidence_lines:
        if norm.text_raw in line.text_raw or norm.key_raw in line.text_raw:
            return [
                SourceSpan(
                    extractor=line.source_extractor,
                    page_number=line.page_number,
                    line_id=line.line_id,
                    source_artifact_path=line.source_artifact_path,
                ),
            ]
    return []


def _option_matches_slot(
    option: SemanticBoundOption,
    norm: NormalizedOptionKey,
) -> bool:
    existing_key = (option.key or option.key_raw or "").strip().rstrip(".")
    if existing_key == norm.key:
        return True
    if not existing_key and norm.text_raw in option.text_raw:
        return True
    return False


def _norm_in_allowed(
    norm: NormalizedOptionKey,
    allowed_line_ids: set[str],
    evidence_lines: list[EvidenceLine],
) -> bool:
    for line in evidence_lines:
        if line.line_id not in allowed_line_ids:
            continue
        if norm.text_raw in line.text_raw or norm.key_raw in line.text_raw:
            return True
    return False


def _dedupe_by_index(options: list[NormalizedOptionKey]) -> list[NormalizedOptionKey]:
    seen: set[str] = set()
    out: list[NormalizedOptionKey] = []
    for opt in sorted(options, key=lambda o: o.option_index or 99):
        if opt.key in seen:
            continue
        seen.add(opt.key)
        out.append(opt)
    return out
