"""Apply numeric and response-sheet option repair to semantic items (Part 14B)."""

from __future__ import annotations

from dataclasses import dataclass

from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage
from meritranker_data_ingestion.schemas.question_window import QuestionWindowsPackage
from meritranker_data_ingestion.services.question_window_builder import window_line_ids_for_question
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBindingPackage,
    SemanticBoundOption,
)
from meritranker_data_ingestion.services.option_key_normalizer import (
    NormalizedOptionKey,
    parse_multiple_numeric_options,
    parse_numeric_option_line,
    split_combined_option_text,
)
from meritranker_data_ingestion.services.response_sheet_option_parser import (
    bind_response_sheet_options_to_item,
)


@dataclass
class NumericOptionRepairStats:
    options_split_count: int = 0
    response_sheet_bound_count: int = 0


def apply_numeric_option_repair(
    package: SemanticBindingPackage,
    evidence: DocumentEvidencePackage,
    *,
    windows_pkg: QuestionWindowsPackage | None = None,
) -> NumericOptionRepairStats:
    """Normalize numeric option keys and bind response-sheet table options."""
    stats = NumericOptionRepairStats()

    for item in package.items:
        expanded_options: list[SemanticBoundOption] = []
        for option in item.options:
            expanded_options.extend(_normalize_option_entries(option, stats))

        if expanded_options:
            item.options = expanded_options

        allowed_ids = (
            window_line_ids_for_question(
                windows_pkg,
                question_number=item.question_number,
                window_id=item.window_id,
            )
            if windows_pkg
            else None
        )
        scoped_lines = (
            [line for line in evidence.lines if line.line_id in allowed_ids]
            if allowed_ids
            else evidence.lines
        )
        stats.response_sheet_bound_count += bind_response_sheet_options_to_item(
            item,
            scoped_lines,
            allowed_line_ids=allowed_ids,
        )

    return stats


def _normalize_option_entries(
    option: SemanticBoundOption,
    stats: NumericOptionRepairStats,
) -> list[SemanticBoundOption]:
    if not (option.key or option.key_raw or "").strip() and option.text_raw.strip():
        multi = parse_multiple_numeric_options(option.text_raw)
        if len(multi) > 1:
            stats.options_split_count += len(multi)
            return [_from_normalized(norm, option) for norm in multi]

        split = split_combined_option_text(option.text_raw)
        if split:
            option.key = split.key
            option.key_raw = split.key_raw
            option.text_raw = split.text_raw
            stats.options_split_count += 1
            return [option]

    if (option.key or option.key_raw or "").strip():
        reparsed = parse_numeric_option_line(
            f"{option.key_raw or option.key}. {option.text_raw}".strip(),
        )
        if reparsed and option.text_raw.strip().startswith(
            (reparsed.key_raw, f"{reparsed.key})", f"{reparsed.key}."),
        ):
            option.key = reparsed.key
            option.key_raw = reparsed.key_raw
            option.text_raw = reparsed.text_raw
            stats.options_split_count += 1

    return [option]


def _from_normalized(
    norm: NormalizedOptionKey,
    template: SemanticBoundOption,
) -> SemanticBoundOption:
    return SemanticBoundOption(
        key=norm.key,
        key_raw=norm.key_raw,
        text_raw=norm.text_raw,
        asset_refs=list(template.asset_refs),
        source_spans=list(template.source_spans),
        confidence=template.confidence,
        issues=list(template.issues),
    )
