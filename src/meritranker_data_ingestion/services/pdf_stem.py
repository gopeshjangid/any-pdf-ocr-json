"""Safe PDF filename stem for batch output folders."""

from __future__ import annotations

import re
from pathlib import Path


def safe_pdf_stem(filename: str) -> str:
    """Lowercase stem with spaces → underscores; strip unsafe characters."""
    stem = Path(filename).stem.lower().replace(" ", "_")
    stem = re.sub(r"[^a-z0-9._-]", "", stem)
    stem = re.sub(r"_+", "_", stem).strip("._-")
    return stem or "unnamed_pdf"
