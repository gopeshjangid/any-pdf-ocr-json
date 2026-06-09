"""Deterministic source-span resolution from document evidence (Part 13F/13G)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from meritranker_data_ingestion.schemas.document_evidence import (
    DocumentEvidencePackage,
    EvidenceLine,
    SourceSpan,
)
from meritranker_data_ingestion.schemas.semantic_binding import (
    SemanticBinderAnswerMode,
    SemanticBindingPackage,
    SemanticBoundOption,
    SemanticBoundQuestion,
)
from meritranker_data_ingestion.services.answer_key_evidence_extractor import (
    extract_answer_key_candidates,
)
from meritranker_data_ingestion.services.semantic_binding_validator import normalize_for_match
from meritranker_data_ingestion.services.semantic_embedded_option_parser import (
    extract_options_from_line,
)
from meritranker_data_ingestion.schemas.question_window import QuestionWindowsPackage
from meritranker_data_ingestion.services.question_window_builder import window_line_ids_for_question
from meritranker_data_ingestion.services.semantic_key_normalizer import normalize_option_key

RE_Q_ANCHOR = re.compile(
    r"^(?:[-*+]\s+)?(?:\|\s*)?(?:\*\*)?(\d{1,3})\.(?:\*\*)?\s*(?:\|\s*)?",
)
RE_Q_INLINE = re.compile(r"\*\*(\d{1,3})\.\*\*")


@dataclass
class SourceSpanResolverStats:
    question_spans_resolved_count: int = 0
    option_spans_resolved_count: int = 0
    answer_spans_resolved_count: int = 0
    solution_spans_resolved_count: int = 0
    unresolved_question_spans_count: int = 0
    unresolved_option_spans_count: int = 0
    unresolved_answer_spans_count: int = 0
    options_filled_from_evidence_count: int = 0
    embedded_options_resolved_count: int = 0
    warnings: list[str] = field(default_factory=list)


def resolve_source_spans(
    package: SemanticBindingPackage,
    evidence: DocumentEvidencePackage,
    *,
    answer_mode: SemanticBinderAnswerMode = SemanticBinderAnswerMode.ANSWER_KEY_ONLY,
    windows_pkg: QuestionWindowsPackage | None = None,
) -> SourceSpanResolverStats:
    """Attach evidence-backed source spans and fill empty options from evidence."""
    stats = SourceSpanResolverStats()
    lines = evidence.lines
    line_by_id = {line.line_id: line for line in lines}
    anchors = _build_question_anchors(lines)
    answer_candidates = extract_answer_key_candidates(lines)
    answer_by_qnum = {c.question_number: c for c in answer_candidates}

    for item in package.items:
        allowed_ids = (
            window_line_ids_for_question(
                windows_pkg,
                question_number=item.question_number,
                window_id=item.window_id,
            )
            if windows_pkg
            else set()
        )
        window_lines = (
            [line for line in lines if line.line_id in allowed_ids]
            if allowed_ids
            else lines
        )
        window_anchors = _build_question_anchors(window_lines) if allowed_ids else anchors

        anchor_idx = _find_anchor_index(item, window_anchors, window_lines)
        block_end = _block_end_index(anchor_idx, window_anchors, len(window_lines))

        if windows_pkg and item.window_id is None:
            for window in windows_pkg.windows:
                if window.parsed_question_number == item.question_number:
                    item.window_id = window.window_id
                    break

        if not item.source_spans:
            resolved = _resolve_question_spans(
                item,
                window_lines,
                anchor_idx,
                block_end,
                line_by_id,
            )
            if resolved:
                item.source_spans = resolved
                stats.question_spans_resolved_count += 1
            else:
                stats.unresolved_question_spans_count += 1

        evidence_options = (
            _scan_options(window_lines, anchor_idx, block_end, item.question_number)
            if anchor_idx is not None
            else _scan_options_near_text(item, window_lines)
        )

        if evidence_options:
            filled = _apply_evidence_options(item, evidence_options, stats)
            if filled:
                stats.options_filled_from_evidence_count += filled

        _resolve_item_options(item, evidence_options, line_by_id, stats)
        _resolve_item_answer(item, answer_by_qnum, line_by_id, stats)
        _enforce_window_scoped_option_spans(item, allowed_ids, stats)

        if item.solution.available and item.solution.text_raw and not item.solution.source_spans:
            resolved = _resolve_text_spans(
                item.solution.text_raw,
                window_lines,
                anchor_idx,
                block_end,
            )
            if resolved:
                item.solution.source_spans = resolved
                stats.solution_spans_resolved_count += 1

    return stats


def _enforce_window_scoped_option_spans(
    item: SemanticBoundQuestion,
    allowed_ids: set[str],
    stats: SourceSpanResolverStats,
) -> None:
    if not allowed_ids:
        return
    cleaned: list[SemanticBoundOption] = []
    for option in item.options:
        valid_spans = [
            span for span in option.source_spans if span.line_id and span.line_id in allowed_ids
        ]
        if option.source_spans and not valid_spans:
            if "cross_window_option_span_reuse" not in option.issues:
                option.issues.append("cross_window_option_span_reuse")
            stats.warnings.append(
                f"cross_window_option_span_reuse:{item.semantic_question_id}",
            )
            option.source_spans = []
        else:
            option.source_spans = valid_spans
        if option.source_spans:
            option.option_source_line_id = option.source_spans[0].line_id
            option.option_source_window_id = item.window_id
        cleaned.append(option)
    item.options = cleaned


def _apply_evidence_options(
    item: SemanticBoundQuestion,
    evidence_options: list[tuple[str, str, str, EvidenceLine]],
    stats: SourceSpanResolverStats,
) -> int:
    by_key: dict[str, tuple[str, str, str, EvidenceLine]] = {}
    for key, raw_key, text, line in evidence_options:
        canonical, _ = normalize_option_key(key)
        if canonical and canonical not in by_key:
            by_key[canonical] = (canonical, raw_key, text, line)

    if not by_key:
        return 0

    if _options_are_empty(item):
        item.options = [
            SemanticBoundOption(
                key=canonical,
                key_raw=raw_key,
                text_raw=text,
                source_spans=[_make_span(line)],
                confidence=0.9,
            )
            for canonical, raw_key, text, line in by_key.values()
        ]
        return len(item.options)

    changed = 0
    existing_keys: set[str] = set()
    for option in item.options:
        canonical, _ = normalize_option_key(option.key or option.key_raw)
        if canonical:
            existing_keys.add(canonical)
        if canonical and canonical in by_key:
            _, raw_key, text, line = by_key[canonical]
            if not option.text_raw.strip():
                option.text_raw = text
            if not option.key:
                option.key = canonical
            if not option.key_raw:
                option.key_raw = raw_key
            if not option.source_spans:
                option.source_spans = [_make_span(line)]
                changed += 1
                stats.embedded_options_resolved_count += 1

    for canonical, raw_key, text, line in by_key.values():
        if canonical in existing_keys:
            continue
        item.options.append(
            SemanticBoundOption(
                key=canonical,
                key_raw=raw_key,
                text_raw=text,
                source_spans=[_make_span(line)],
                confidence=0.85,
            ),
        )
        changed += 1
        stats.embedded_options_resolved_count += 1

    return changed


def _resolve_item_options(
    item: SemanticBoundQuestion,
    evidence_options: list[tuple[str, str, str, EvidenceLine]],
    line_by_id: dict[str, EvidenceLine],
    stats: SourceSpanResolverStats,
) -> None:
    by_key: dict[str, tuple[str, str, str, EvidenceLine]] = {}
    for key, raw_key, text, line in evidence_options:
        canonical, _ = normalize_option_key(key)
        if canonical:
            by_key[canonical] = (canonical, raw_key, text, line)

    for option in item.options:
        if option.source_spans and _spans_valid(option.source_spans, option.text_raw, line_by_id):
            continue

        canonical, _ = normalize_option_key(option.key or option.key_raw)
        if canonical and canonical in by_key:
            _, raw_key, text, line = by_key[canonical]
            if not option.text_raw.strip():
                option.text_raw = text
            if not option.key:
                option.key = canonical
            if not option.key_raw:
                option.key_raw = raw_key
            option.source_spans = [_make_span(line)]
            stats.option_spans_resolved_count += 1
            continue

        if option.text_raw.strip():
            match = _find_option_line(option.text_raw, option.key or option.key_raw, evidence_options)
            if match:
                _, raw_key, text, line = match
                canonical, _ = normalize_option_key(option.key or option.key_raw or raw_key)
                if canonical:
                    option.key = canonical
                if not option.key_raw:
                    option.key_raw = raw_key
                option.source_spans = [_make_span(line)]
                stats.option_spans_resolved_count += 1
                continue

        if option.text_raw.strip() or (option.key or option.key_raw).strip():
            stats.unresolved_option_spans_count += 1


def _resolve_item_answer(
    item: SemanticBoundQuestion,
    answer_by_qnum: dict,
    line_by_id: dict[str, EvidenceLine],
    stats: SourceSpanResolverStats,
) -> None:
    if not item.answer.available:
        return

    if item.answer.source_spans and _spans_valid(
        item.answer.source_spans,
        item.answer.answer_text_raw or item.answer.key or "",
        line_by_id,
    ):
        return

    if item.question_number is not None and item.question_number in answer_by_qnum:
        cand = answer_by_qnum[item.question_number]
        line = line_by_id.get(cand.source_line_id)
        if line is not None:
            item.answer.source_spans = [_make_span(line)]
            if not item.answer.key:
                item.answer.key = cand.answer_key
            if not item.answer.key_raw:
                item.answer.key_raw = cand.answer_key
            if not item.answer.answer_text_raw:
                item.answer.answer_text_raw = cand.source_text_raw
            stats.answer_spans_resolved_count += 1
            return

    stats.unresolved_answer_spans_count += 1


def _resolve_question_spans(
    item: SemanticBoundQuestion,
    lines: list[EvidenceLine],
    anchor_idx: int | None,
    block_end: int,
    line_by_id: dict[str, EvidenceLine],
) -> list[SourceSpan]:
    if anchor_idx is None:
        return _resolve_question_spans_by_text(item, lines)

    block_lines = lines[anchor_idx:block_end]
    option_start = _first_option_index(block_lines)
    stem_lines = block_lines[:option_start] if option_start is not None else block_lines[:3]

    combined = " ".join(
        normalize_for_match(line.text_raw) for line in stem_lines if line.text_raw.strip()
    )
    question_norm = normalize_for_match(item.question_text_raw)

    if question_norm and question_norm in combined:
        spans = [_make_span(line) for line in stem_lines if line.text_raw.strip()]
        return _filter_grounded_spans(item.question_text_raw, spans, line_by_id)

    anchor_line = stem_lines[0] if stem_lines else None
    if anchor_line is not None and question_norm:
        anchor_norm = normalize_for_match(anchor_line.text_raw)
        if question_norm in anchor_norm or anchor_norm in question_norm:
            spans = [_make_span(anchor_line)]
            return _filter_grounded_spans(item.question_text_raw, spans, line_by_id)
        snippet = question_norm[:80]
        if snippet and snippet in anchor_norm:
            if item.question_number is not None and not _question_number_in_line(
                anchor_line.text_raw,
                item.question_number,
            ):
                return []
            spans = [_make_span(anchor_line)]
            return _filter_grounded_spans(item.question_text_raw, spans, line_by_id)
    return []


def _resolve_question_spans_by_text(
    item: SemanticBoundQuestion,
    lines: list[EvidenceLine],
) -> list[SourceSpan]:
    question_norm = normalize_for_match(item.question_text_raw)
    if not question_norm:
        return []
    snippet = question_norm[:80]
    for line in lines:
        line_norm = normalize_for_match(line.text_raw)
        if snippet and snippet in line_norm:
            if item.question_number is not None and not _question_number_in_line(
                line.text_raw,
                item.question_number,
            ):
                continue
            spans = [_make_span(line)]
            return _filter_grounded_spans(item.question_text_raw, spans, {line.line_id: line})
        if item.question_number is not None:
            inline = RE_Q_INLINE.search(line.text_raw)
            if inline and int(inline.group(1)) == item.question_number and snippet[:40] in line_norm:
                spans = [_make_span(line)]
                return _filter_grounded_spans(item.question_text_raw, spans, {line.line_id: line})
    return []


def _filter_grounded_spans(
    question_text: str,
    spans: list[SourceSpan],
    line_by_id: dict[str, EvidenceLine],
) -> list[SourceSpan]:
    """Drop spans when question text is not supported by evidence (avoids false hallucination flags)."""
    from meritranker_data_ingestion.services.semantic_binding_validator import text_in_evidence

    line_ids = [span.line_id for span in spans if span.line_id]
    if not line_ids:
        return []
    line_map = {lid: line_by_id[lid].text_raw for lid in line_ids if lid in line_by_id}
    if text_in_evidence(question_text, line_ids, line_map):
        return spans
    return []


def _question_number_in_line(text: str, qnum: int) -> bool:
    if _line_matches_question_number(text, qnum):
        return True
    inline = RE_Q_INLINE.search(text)
    return inline is not None and int(inline.group(1)) == qnum


def _resolve_text_spans(
    text: str,
    lines: list[EvidenceLine],
    anchor_idx: int | None,
    block_end: int,
) -> list[SourceSpan]:
    norm = normalize_for_match(text)
    if not norm:
        return []
    search_start = anchor_idx if anchor_idx is not None else 0
    search_end = block_end if anchor_idx is not None else len(lines)
    for line in lines[search_start:search_end]:
        if norm in normalize_for_match(line.text_raw):
            return [_make_span(line)]
    return []


def _build_question_anchors(lines: list[EvidenceLine]) -> list[tuple[int, int]]:
    anchors: list[tuple[int, int]] = []
    seen_qnums: set[int] = set()
    for idx, line in enumerate(lines):
        stripped = line.text_raw.strip()
        match = RE_Q_ANCHOR.match(stripped)
        if not match:
            inline = RE_Q_INLINE.search(stripped)
            if inline:
                qnum = int(inline.group(1))
                if qnum not in seen_qnums:
                    seen_qnums.add(qnum)
                    anchors.append((idx, qnum))
            continue
        qnum = int(match.group(1))
        if qnum in seen_qnums:
            continue
        seen_qnums.add(qnum)
        anchors.append((idx, qnum))
    return anchors


def _find_anchor_index(
    item: SemanticBoundQuestion,
    anchors: list[tuple[int, int]],
    lines: list[EvidenceLine],
) -> int | None:
    if item.question_number is not None:
        for idx, qnum in anchors:
            if qnum == item.question_number:
                return idx
        for idx, line in enumerate(lines):
            if _line_matches_question_number(line.text_raw, item.question_number):
                return idx
            inline = RE_Q_INLINE.search(line.text_raw)
            if inline and int(inline.group(1)) == item.question_number:
                return idx

    question_norm = normalize_for_match(item.question_text_raw)
    if not question_norm:
        return None

    snippet = question_norm[:80]
    for idx, line in enumerate(lines):
        line_norm = normalize_for_match(line.text_raw)
        if snippet and snippet in line_norm:
            return idx
        if item.question_number is not None and _line_matches_question_number(
            line.text_raw,
            item.question_number,
        ):
            if _token_overlap(question_norm, line_norm) >= 0.5:
                return idx
    return None


def _line_matches_question_number(text: str, qnum: int) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    prefix = stripped[:40]
    patterns = (
        rf"^(?:[-*+]\s+)?\*\*{qnum}\.\*\*",
        rf"^(?:[-*+]\s+)?\*\*{qnum}\.",
        rf"^{qnum}\.\s",
        rf"^{qnum}\.\s*\|",
        rf"^\|\s*{qnum}\.",
        rf"^\|\s*{qnum}\.\s*\|",
        rf"^{qnum}\s*\|\s*",
        rf"\*\*{qnum}\.\*\*",
    )
    return any(re.search(pattern, prefix) for pattern in patterns)


def _block_end_index(anchor_idx: int | None, anchors: list[tuple[int, int]], total: int) -> int:
    if anchor_idx is None:
        return total
    for idx, _qnum in anchors:
        if idx > anchor_idx:
            return idx
    return total


def _scan_options(
    lines: list[EvidenceLine],
    anchor_idx: int,
    block_end: int,
    question_number: int | None,
) -> list[tuple[str, str, str, EvidenceLine]]:
    found: list[tuple[str, str, str, EvidenceLine]] = []
    seen_keys: set[str] = set()

    scan_start = max(0, anchor_idx)
    for offset, line in enumerate(lines[scan_start:block_end]):
        text = line.text_raw.strip()
        if not text:
            continue
        if offset > 0 and _is_next_question_line(text, question_number):
            break

        for canonical, raw_key, opt_text, src_line in extract_options_from_line(text, line):
            if canonical in seen_keys:
                continue
            seen_keys.add(canonical)
            found.append((canonical, raw_key, opt_text, src_line))
            if len(found) >= 4:
                return found

    if len(found) < 4:
        neighbour_end = min(block_end, anchor_idx + 12)
        for line in lines[anchor_idx:neighbour_end]:
            text = line.text_raw.strip()
            if not text or _is_next_question_line(text, question_number):
                continue
            for canonical, raw_key, opt_text, src_line in extract_options_from_line(text, line):
                if canonical in seen_keys:
                    continue
                seen_keys.add(canonical)
                found.append((canonical, raw_key, opt_text, src_line))
                if len(found) >= 4:
                    break

    return found


def _scan_options_near_text(
    item: SemanticBoundQuestion,
    lines: list[EvidenceLine],
) -> list[tuple[str, str, str, EvidenceLine]]:
    anchor_idx = _find_anchor_index(item, [], lines)
    if anchor_idx is None:
        return []
    block_end = min(len(lines), anchor_idx + 15)
    return _scan_options(lines, anchor_idx, block_end, item.question_number)


def _is_next_question_line(text: str, current_qnum: int | None) -> bool:
    match = RE_Q_ANCHOR.match(text)
    if match:
        qnum = int(match.group(1))
        return current_qnum is None or qnum != current_qnum
    inline = RE_Q_INLINE.search(text)
    if inline:
        qnum = int(inline.group(1))
        return current_qnum is None or qnum != current_qnum
    return False


def _first_option_index(block_lines: list[EvidenceLine]) -> int | None:
    for idx, line in enumerate(block_lines):
        if extract_options_from_line(line.text_raw, line):
            return idx
    return None


def _find_option_line(
    text_raw: str,
    key_hint: str,
    evidence_options: list[tuple[str, str, str, EvidenceLine]],
) -> tuple[str, str, str, EvidenceLine] | None:
    norm_text = normalize_for_match(text_raw)
    canonical_hint, _ = normalize_option_key(key_hint)
    for key, raw_key, text, line in evidence_options:
        canonical, _ = normalize_option_key(key)
        if canonical_hint and canonical != canonical_hint:
            continue
        if norm_text and norm_text in normalize_for_match(text):
            return key, raw_key, text, line
    return None


def _options_are_empty(item: SemanticBoundQuestion) -> bool:
    if not item.options:
        return True
    return all(
        not option.text_raw.strip() and not (option.key or option.key_raw or "").strip()
        for option in item.options
    )


def _spans_valid(
    spans: list[SourceSpan],
    text: str,
    line_by_id: dict[str, EvidenceLine],
) -> bool:
    line_ids = [span.line_id for span in spans if span.line_id]
    if not line_ids:
        return False
    if not text or not str(text).strip():
        return True
    norm = normalize_for_match(str(text))
    combined = " ".join(
        normalize_for_match(line_by_id[lid].text_raw)
        for lid in line_ids
        if lid in line_by_id
    )
    return norm in combined or _token_overlap(norm, combined) >= 0.7


def _token_overlap(a: str, b: str) -> float:
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a)


def _make_span(line: EvidenceLine) -> SourceSpan:
    if line.source_span is not None:
        return line.source_span
    return SourceSpan(
        extractor=line.source_extractor,
        page_number=line.page_number,
        line_id=line.line_id,
        source_artifact_path=line.source_artifact_path,
        raw_index=line.source_line_number,
        bbox=line.bbox,
    )
