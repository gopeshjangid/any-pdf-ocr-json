# Local Processing Strategy

## Principle: Local-First

MeritRanker data ingestion runs **on the user's machine** by default. No cloud API keys, network calls, or uploads are required for v1 happy-path operation. Private teacher PDFs and exam content never leave the local environment unless the user explicitly enables an optional cloud benchmark tool.

## v1 Stack (Planned — Not Yet Implemented)

| Component | Role | v1 Status |
|-----------|------|-----------|
| Python 3.11+ | Runtime | Planned |
| uv | Project and dependency management | Planned |
| Marker | Primary PDF → markdown/structure converter | Planned primary parser |
| Pydantic | JSON schemas and validation models | Planned |
| pypdfium2 | Page rendering and image cropping | Later within v1 if images needed |
| PaddleOCR | Scanned PDF fallback | Future |
| Qwen3-VL | Diagram-to-syntax, visual reasoning | Future |
| Azure Document Intelligence | Optional benchmark/fallback only | Not required for v1 |

## Marker as Primary Parser (v1)

- Marker converts digital PDFs to markdown with layout cues suitable for deterministic downstream parsing.
- Marker runs locally; no default cloud dependency.
- Marker is **not** imported throughout the codebase—it sits behind a **parser adapter** interface.

**Adapter contract (conceptual):**

```python
# Conceptual — not implemented yet
class ParserAdapter(Protocol):
    def extract(self, pdf_path: Path, output_dir: Path) -> ExtractionPackage: ...
```

`ExtractionPackage` contains: markdown paths, asset paths, metadata, parser version, per-page block map.

## Parser Isolation

- Business logic depends on `ExtractionPackage` and block models—not on Marker types.
- Swapping Marker for another extractor requires only a new adapter implementation.
- Tests use fixture markdown packages without invoking Marker.

## uv-Based Python Setup

- Single `pyproject.toml` managed by uv.
- `src/` layout with package namespace TBD at implementation time.
- CLI entry point: thin wrapper calling pipeline orchestrator.
- Lockfile committed for reproducible local and CI runs.
- Dev dependencies (pytest, ruff) separate from runtime.

## Processing Flow (Local)

1. User provides input PDF path and output directory via CLI.
2. CLI validates paths (Security Reviewer rules).
3. Parser adapter runs Marker locally → writes extraction package to output dir.
4. Deterministic parser reads extraction package → question candidates.
5. Pydantic validation + faithfulness rules → JSON + validation report.
6. Review statuses assigned; artifacts saved locally.

No step in the default flow requires network access.

## Cloud Tools: Optional Only

| Tool | When | Requirement |
|------|------|-------------|
| Azure Document Intelligence | Benchmarking extraction quality; fallback experiments | Explicit CLI flag; never default |
| Cloud LLM APIs | Must not be used in v1 default path | Blocked by AI Solution Architect |
| Remote OCR | Only if local PaddleOCR insufficient (future) | Opt-in flag + cost review |

## OCR / Vision Fallback (Later)

Triggered only when:

- Parser confidence below threshold, OR
- Page detected as image-only/scanned, OR
- User passes `--enable-ocr` / `--enable-vision` (future flags)

Fallback order (planned):

1. Deterministic Marker extraction
2. Local PaddleOCR for text recovery
3. Local Qwen3-VL for diagram syntax (if needed)
4. Optional Azure DI for benchmark comparison—not production default

Each fallback escalates cost and review scrutiny; outputs remain `needs_review` until validated.

## Batch Processing

- v1: sequential CLI over multiple files acceptable.
- Future: resumable batch with cached extraction packages to avoid re-running Marker on retry.
- Performance-Cost Reviewer validates memory for 150+ page PYQ files before batch features ship.

## What Agents Must Not Do Now

- Add `pyproject.toml`, Marker, or Python source unless a future task passes full role review.
- Wire Azure or OpenAI clients into default code paths.
- Skip adapter design and call Marker from CLI directly.
