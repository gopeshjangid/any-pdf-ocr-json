"""Export non-ready questions to per-PDF review JSON (Part 14X)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from meritranker_data_ingestion.services.public_questions_audit import audit_public_questions_json

NON_READY_STATUSES = frozenset({"review", "visual_required", "blocked"})


def review_json_path(output_folder: Path, stem: str) -> Path:
    return output_folder / f"{stem}.review.json"


def export_review_json(
    questions_json_path: Path,
    *,
    output_path: Path | None = None,
) -> Path | None:
    """Write review JSON containing only non-ready questions."""
    if not questions_json_path.exists():
        return None

    payload = json.loads(questions_json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None

    file_meta = payload.get("fileMeta") or {}
    all_questions = payload.get("questions") or []
    review_questions = [
        q for q in all_questions
        if isinstance(q, dict)
        and (q.get("metadata") or {}).get("status") in NON_READY_STATUSES
    ]

    stem = questions_json_path.parent.name
    target = output_path or (questions_json_path.parent / f"{stem}.review.json")
    review_payload: dict[str, Any] = {
        "fileMeta": dict(file_meta),
        "questions": review_questions,
    }
    target.write_text(json.dumps(review_payload, indent=2), encoding="utf-8")
    return target


def load_public_questions(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
