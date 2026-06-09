# Sample Audit Checklist (Part 7)

Use this checklist after running the full pipeline and `audit-final-package` on a real PDF.

## Prerequisites

1. Marker installed (`pip install marker-pdf`)
2. Full pipeline completed through `build-final-package`
3. Audit command run with optional `--expected-count`

## Commands

```bash
# Full pipeline
uv run python -m meritranker_data_ingestion.cli prepare \
  --input path/to/exam.pdf --output output/

uv run python -m meritranker_data_ingestion.cli classify-markdown \
  --package output/extraction_package

uv run python -m meritranker_data_ingestion.cli parse-question-candidates \
  --package output/extraction_package

uv run python -m meritranker_data_ingestion.cli map-answers-solutions \
  --package output/extraction_package

uv run python -m meritranker_data_ingestion.cli build-final-package \
  --package output/extraction_package

# Quality audit (read-only)
uv run python -m meritranker_data_ingestion.cli audit-final-package \
  --package output/extraction_package \
  --expected-count 100
```

## Audit Review Checklist

| Check | Where to verify | Pass criteria |
|-------|-----------------|---------------|
| Total questions detected | `audit/final-package-audit.json` → `total_questions` | Matches PDF question count (± tolerance) |
| Expected count match | `expected_count_match` | `true` if count supplied |
| Missing question numbers | `missing_question_numbers` | Empty or documented gaps |
| Duplicate question numbers | `duplicate_question_numbers` | Empty or manually resolved |
| PYQ-only items valid | `question_only_validated_count` | Expected for question-only PDFs |
| Answer mapping coverage | `answered_count` / `total_questions` | Matches answer key availability |
| Solution mapping coverage | `solved_count` | Matches solution section presence |
| Review-required items | `needs_review_count`, `high_risk_items` | Spot-checked against source PDF |
| Answer-option mismatches | `answer_option_mismatch_count` | Zero or manually verified |
| Visual questions | `visual_question_count` | Assets preserved in `final/questions.json` |
| Candidates without options | `candidates_without_options` | Expected for some PYQ formats |
| Source faithfulness | Spot-check 5–10 items | `raw_text` matches PDF verbatim |

## Status Interpretation

| Audit status | Meaning | Action |
|--------------|---------|--------|
| `passed` | No serious issues | Spot-check sample; audit more PDFs |
| `warning` | Review flags present | Manual review before any ingestion |
| `failed` | Critical problem | Fix pipeline inputs; re-run |

## Important

- Audit does **not** modify `final/questions.json` or any upstream artifacts.
- Do **not** start MeritRanker pattern ingestion until at least 2–3 real PDFs pass audit with acceptable warning levels.
- Part 8 scope should be decided only after reviewing audit findings across multiple samples.
