"""Map internal visuals to minimal public visual contract (Part 14P)."""

from __future__ import annotations

from meritranker_data_ingestion.schemas.final_questions_export import FinalQuestionVisual
from meritranker_data_ingestion.schemas.final_questions_public import PublicQuestionVisual, PublicVisualSyntax

ALLOWED_PUBLIC_VISUAL_TYPES = frozenset({
    "table",
    "formula",
    "chart",
    "geometry",
    "number_line",
    "venn",
    "dice",
    "mirror_image",
    "paper_folding",
    "embedded_figure",
    "image_option",
    "other",
})

INTERNAL_TYPE_MAP: dict[str, str] = {
    "diagram": "geometry",
    "figure": "embedded_figure",
    "graph": "chart",
    "image": "image_option",
    "geometry": "geometry",
    "chart": "chart",
    "table": "table",
    "formula": "formula",
    "number_line": "number_line",
    "venn": "venn",
    "dice": "dice",
    "mirror_image": "mirror_image",
    "paper_folding": "paper_folding",
    "embedded_figure": "embedded_figure",
    "image_option": "image_option",
}


def serialize_public_visual(visual: FinalQuestionVisual) -> PublicQuestionVisual:
    syntax = _extract_syntax(visual)
    issues = ["visual_syntax_missing"] if syntax is None else []
    return PublicQuestionVisual(
        visual_id=visual.visual_id,
        type=normalize_visual_type(visual.type, role=visual.role),
        role=visual.role or "question",
        linked_option_label=visual.linked_option_label,
        description=visual.description or "",
        syntax=syntax,
        issues=_dedupe(issues),
    )


def normalize_visual_type(raw_type: str, *, role: str = "question") -> str:
    lower = (raw_type or "").lower().strip()
    if lower in ALLOWED_PUBLIC_VISUAL_TYPES:
        return lower
    if lower in INTERNAL_TYPE_MAP:
        return INTERNAL_TYPE_MAP[lower]
    if role == "option" or "option" in lower:
        return "image_option"
    return "other"


def visual_has_syntax(visual: FinalQuestionVisual) -> bool:
    return _extract_syntax(visual) is not None


def _extract_syntax(visual: FinalQuestionVisual) -> PublicVisualSyntax | None:
    spec = visual.render_spec
    if not spec or visual.extraction_status != "render_ready":
        return None
    fmt = str(spec.get("format", "")).lower()
    if fmt == "merit_visual_v1":
        return PublicVisualSyntax(
            format="merit_visual_v1",
            kind=str(spec.get("kind", normalize_visual_type(visual.type))),
            canvas=spec.get("canvas", {"width": 800, "height": 500}),
            objects=list(spec.get("objects", spec.get("elements", []))),
            constraints=list(spec.get("constraints", [])),
        )
    if fmt == "canva_render_spec_v1" and spec.get("canvas"):
        return PublicVisualSyntax(
            format="merit_visual_v1",
            kind=normalize_visual_type(visual.type),
            canvas=spec.get("canvas", {"width": 800, "height": 500}),
            objects=list(spec.get("elements", spec.get("objects", []))),
            constraints=list(spec.get("constraints", [])),
        )
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
