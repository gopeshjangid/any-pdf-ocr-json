# Part 14Q Quality Root-Cause Analysis

## Iteration summary

| Iter | SSC 2023 ready | SSC 2023 audit | SSC 2021 ready | SSC 2021 audit |
| --- | --- | --- | --- | --- |
| 0 (batch) | 31 | FAIL | — | — |
| 1 (replay) | 73 | FAIL | — | — |
| 2 (replay) | 73 | PASS | — | — |
| 3 (replay) | 73 | PASS | 9 | PASS |

## Root causes addressed (generic fixes)

| Root cause | Fix | Files |
| --- | --- | --- |
| Wrong question window for option supplement (duplicate windows per QN) | `index_question_windows()` prefers question-section window | `answer_option_reconciler.py` |
| Stale `answer_key_not_in_options` after option recovery | `reconcile_item_answers()` re-joins answers from recovered options | `answer_option_reconciler.py`, `final_export_enhancer.py` |
| Duplicate semantic/window options (`- (a)` + labeled A) | `consolidate_options()` dedupe by canonical key | `option_recovery.py` |
| Extra unnumbered semantic item breaks audit | `_block_duplicate_and_unnumbered_items()` | `final_completeness_verifier.py` |
| Blocked items with empty text fail public audit | Add `question_missing_from_extraction` when blocked + empty text | `final_readiness_resolver.py` |

## Remaining blockers — SSC 2023 (73% ready)

| Category | Count | Notes |
| --- | --- | --- |
| visual/VLM_required | 7 | `visual_syntax_missing`; no fake syntax |
| manual_review_required (hallucination) | 6 | Q063, Q065, Q078, Q087, Q093, Q095 |
| answer_solution_join_gap | 7 | Q5,8,13,15,16,19,49 — no solution-window in source map |
| incomplete_options (source) | ~6 | Q33/35/36/80 missing option in PDF window |
| extra semantic item | 1 | `fq_sq_0101` blocked |

## Remaining blockers — SSC 2021 OCR (9% ready)

| Category | Count | Notes |
| --- | --- | --- |
| ocr_quality_issue | — | 1356 OCR lines, heavy scan noise |
| unsupported_layout | — | 234 question windows, duplicate numbering |
| missing_questions | 75 | Placeholders added as blocked |
| incomplete_options | 152 | Options not recovered from OCR layout |

## Commands used

```bash
# Iter 0 — SSC 2023 batch
uv run python -m meritranker_data_ingestion.cli run-pdf-folder \
  --input-dir input_pdfs --output-dir batch_outputs \
  --answer-mode auto --expected-count 100 --extractor marker \
  --ocr-engine auto --provider azure-openai \
  --model "$MERITRANKER_BINDER_MODEL" --timeout-seconds 180 \
  --continue-on-error --clean-output --max-files 2

# Iter 1–3 — replay (SSC 2023)
uv run python -m meritranker_data_ingestion.cli replay-finalization \
  --package batch_outputs/30_yearwise_ssc_cgl_solved_paper_english_2023/extraction_package \
  --expected-count 100 --answer-mode auto

# SSC 2021 — continue from evidence + bind + replay
uv run python -m meritranker_data_ingestion.cli bind-semantically \
  --package batch_outputs/ssc-cgl_2021_aug_ocr/extraction_package \
  --expected-count 100 --answer-mode auto --provider azure-openai \
  --model gpt-5.1 --timeout-seconds 180
uv run python -m meritranker_data_ingestion.cli repair-semantic-binding \
  --package batch_outputs/ssc-cgl_2021_aug_ocr/extraction_package \
  --expected-count 100 --answer-mode auto
uv run python -m meritranker_data_ingestion.cli replay-finalization \
  --package batch_outputs/ssc-cgl_2021_aug_ocr/extraction_package \
  --expected-count 100 --answer-mode auto
```
