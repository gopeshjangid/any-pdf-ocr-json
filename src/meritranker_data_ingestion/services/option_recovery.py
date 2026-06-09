"""Recover incomplete MCQ options from local window evidence (Part 14M)."""

from __future__ import annotations

import re

from meritranker_data_ingestion.schemas.document_evidence import DocumentEvidencePackage, EvidenceLine, SourceSpan
from meritranker_data_ingestion.schemas.final_questions_export import (
    FinalQuestionItem,
    FinalQuestionOption,
    FinalQuestionVisual,
)
from meritranker_data_ingestion.schemas.question_window import QuestionWindow
from meritranker_data_ingestion.services.answer_key_zone_detector import is_answer_key_line
from meritranker_data_ingestion.services.deterministic_option_parser import (
    ParsedOption,
    count_usable_options,
    parse_options_from_window_lines,
)

RE_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
RE_OPTION_LABEL_ONLY = re.compile(r"^\s*(?:[-•*]\s*)*\(?\s*([A-Da-d1-4])\s*\)?\s*[\.\)]?\s*$")
RE_OPTION_LABEL_PREFIX = re.compile(r"^\s*(?:[-•*]\s*)*\(([A-Da-d])\)\s*$")
RE_OPTION_TEXT_PREFIX = re.compile(
    r"^\s*(?:[-•*]\s*)*\(\*?([A-Da-d1-4])\*?\)\s*",
    re.IGNORECASE,
)


def supplement_options_from_window(
    item: FinalQuestionItem,
    *,
    window: QuestionWindow | None,
    evidence: DocumentEvidencePackage | None,
) -> FinalQuestionItem:
    """Prefer window-local options when semantic/export options are underfilled."""
    item = item.model_copy(update={"options": consolidate_options(item.options)})
    if count_usable_options(item.options) >= 4:
        return item
    recovered = recover_incomplete_options(item, window=window, evidence=evidence)
    if count_usable_options(recovered.options) > count_usable_options(item.options):
        return recovered
    return item.model_copy(update={"options": consolidate_options(item.options)})


def recover_incomplete_options(
    item: FinalQuestionItem,
    *,
    window: QuestionWindow | None,
    evidence: DocumentEvidencePackage | None,
) -> FinalQuestionItem:
    """Attempt local recovery for items with fewer than four usable options."""
    if count_usable_options(item.options) >= 4:
        return item
    if window is None or evidence is None:
        return item

    line_by_id = {line.line_id: line for line in evidence.lines}
    window_lines = _merge_option_continuations(
        _expanded_window_lines(window, line_by_id, evidence.lines),
    )
    parse_result = parse_options_from_window_lines(
        window_lines,
        anchor_line_ids=set(window.question_anchor_line_ids),
        option_candidate_line_ids=set(window.option_candidate_line_ids),
    )

    options = [_to_final_option(parsed) for parsed in parse_result.options]
    options, visuals = _recover_image_only_options(options, window_lines, item)
    if (
        count_usable_options(options) <= count_usable_options(item.options)
        and len(visuals) <= len(item.visuals)
    ):
        return item

    issues = [issue for issue in item.issues if issue != "incomplete_options"]
    if count_usable_options(options) < 4:
        issues.append("incomplete_options")

    options = consolidate_options(options)
    return item.model_copy(
        update={
            "options": options,
            "visuals": visuals,
            "issues": _dedupe(issues),
        },
    )


def consolidate_options(options: list[FinalQuestionOption]) -> list[FinalQuestionOption]:
    """Merge duplicate labels and strip embedded option prefixes from text."""
    by_canon: dict[str, FinalQuestionOption] = {}
    for opt in options:
        canon = _resolve_option_canonical(opt)
        if not canon:
            continue
        text = _clean_option_text(opt.text_raw)
        if not text:
            continue
        merged = opt.model_copy(
            update={
                "canonical_key": canon,
                "key": canon.lower(),
                "key_raw": canon,
                "text_raw": text,
            },
        )
        existing = by_canon.get(canon)
        if existing is None or _option_quality(merged) > _option_quality(existing):
            by_canon[canon] = merged
    return [by_canon[key] for key in sorted(by_canon.keys())]


def _resolve_option_canonical(opt: FinalQuestionOption) -> str | None:
    canon = (opt.canonical_key or "").strip().upper()
    if canon and canon != "?":
        return canon[:1]
    for raw in (opt.key, opt.key_raw):
        if raw and str(raw).strip().upper() in {"A", "B", "C", "D"}:
            return str(raw).strip().upper()
    match = RE_OPTION_TEXT_PREFIX.match(opt.text_raw or "")
    if match:
        raw = match.group(1).upper()
        return {"1": "A", "2": "B", "3": "C", "4": "D"}.get(raw, raw[:1])
    return None


def _clean_option_text(text: str) -> str:
    cleaned = RE_OPTION_TEXT_PREFIX.sub("", (text or "").strip()).strip()
    return cleaned.strip("-•* ").strip()


def _option_quality(opt: FinalQuestionOption) -> int:
    score = 0
    if opt.canonical_key and opt.canonical_key != "?":
        score += 2
    if len(opt.text_raw or "") > 2:
        score += 1
    if not (opt.text_raw or "").startswith("-"):
        score += 1
    return score


RE_OPTION_START = re.compile(
    r"^\s*(?:[-•*]+\s*)*(?:\(\*?[A-Da-d1-4]\*?\)|\([A-Da-d]\)|[A-Da-d]\.)",
    re.IGNORECASE,
)


def _merge_option_continuations(lines: list[EvidenceLine]) -> list[EvidenceLine]:
    """Join label-only option lines with their continuation text on the next line."""
    if not lines:
        return lines
    merged: list[EvidenceLine] = []
    idx = 0
    while idx < len(lines):
        current = lines[idx]
        text = (current.text_raw or "").strip()
        label_only = bool(RE_OPTION_LABEL_ONLY.match(text))
        if idx + 1 < len(lines):
            next_text = (lines[idx + 1].text_raw or "").strip()
            if (
                (RE_OPTION_START.match(text) or label_only)
                and not RE_OPTION_START.search(next_text)
                and not RE_OPTION_LABEL_ONLY.match(next_text)
                and len(text) < 80
            ):
                nxt = lines[idx + 1]
                combined = f"{text} {(nxt.text_raw or '').strip()}".strip()
                merged.append(current.model_copy(update={"text_raw": combined}))
                idx += 2
                continue
        merged.append(current)
        idx += 1
    return merged


def _expanded_window_lines(
    window: QuestionWindow,
    line_by_id: dict[str, EvidenceLine],
    all_lines: list[EvidenceLine],
) -> list[EvidenceLine]:
    ids = set(window.line_ids)
    if window.start_line_id:
        ids.add(window.start_line_id)
    if window.end_line_id:
        ids.add(window.end_line_id)
    for lid in window.option_candidate_line_ids:
        ids.add(lid)

    ordered_ids = [line.line_id for line in all_lines]
    if window.start_line_id in ordered_ids and window.end_line_id in ordered_ids:
        start_idx = ordered_ids.index(window.start_line_id)
        end_idx = ordered_ids.index(window.end_line_id)
        for lid in ordered_ids[start_idx : end_idx + 1]:
            ids.add(lid)

    return [
        line_by_id[lid]
        for lid in ordered_ids
        if lid in ids
        and lid in line_by_id
        and not is_answer_key_line(line_by_id[lid].text_raw)
    ]


def _recover_image_only_options(
    options: list[FinalQuestionOption],
    window_lines: list[EvidenceLine],
    item: FinalQuestionItem,
) -> tuple[list[FinalQuestionOption], list[FinalQuestionVisual]]:
    by_key = {
        (opt.canonical_key or opt.key or "").upper(): opt
        for opt in options
        if (opt.canonical_key or opt.key)
    }
    visuals = list(item.visuals)

    for line in window_lines:
        text = line.text_raw.strip()
        if not RE_IMAGE.search(text):
            continue
        asset = RE_IMAGE.search(text).group(1).strip()  # type: ignore[union-attr]
        label = _image_option_label(text)
        if not label:
            continue
        key = label.upper()
        placeholder = f"[visual option {key}]"
        if key not in by_key or not by_key[key].text_raw.strip():
            by_key[key] = FinalQuestionOption(
                key=label.lower(),
                key_raw=label.lower(),
                text_raw=placeholder,
                canonical_key=key,
                visual_asset_refs=[asset],
                source_spans=[
                    SourceSpan(
                        line_id=line.line_id,
                        page_number=line.page_number,
                        extractor=line.source_extractor,
                    ),
                ],
                source_engine=line.source_extractor,
                confidence=0.6,
            )
            visuals.append(
                FinalQuestionVisual(
                    visual_id=f"Q{item.question_number or item.global_order:03d}_OPT_{key}",
                    type="diagram",
                    role="option",
                    linked_option_label=key,
                    description=f"Image-only option {key} from source PDF.",
                    extraction_status="image_required",
                    render_target="canva",
                    asset_ref=asset,
                    issues=["option_image_required"],
                ),
            )

    ordered = sorted(by_key.values(), key=lambda opt: opt.canonical_key or opt.key)
    merged_visuals = list(item.visuals) + [v for v in visuals if v.visual_id not in {x.visual_id for x in item.visuals}]
    return ordered, merged_visuals


def _image_option_label(text: str) -> str | None:
    stripped = RE_IMAGE.sub("", text).strip()
    match = RE_OPTION_LABEL_PREFIX.match(stripped)
    if match:
        return match.group(1).upper()
    match = RE_OPTION_LABEL_ONLY.match(stripped)
    if match:
        raw = match.group(1)
        return raw.upper() if raw.isalpha() else {"1": "A", "2": "B", "3": "C", "4": "D"}.get(raw)
    return None


def _to_final_option(parsed: ParsedOption) -> FinalQuestionOption:
    return FinalQuestionOption(
        key=parsed.canonical_key.lower(),
        key_raw=parsed.key_raw,
        text_raw=parsed.text_raw,
        canonical_key=parsed.canonical_key,
        source_spans=[
            SourceSpan(
                line_id=parsed.source_line.line_id,
                page_number=parsed.source_line.page_number,
                extractor=parsed.source_line.source_extractor,
            ),
        ],
        source_engine=parsed.source_line.source_extractor,
        confidence=0.75,
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
