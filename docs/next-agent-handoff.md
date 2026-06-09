# Next Agent Handoff

**Last updated:** 2026-06-08  
**Repository phase:** Batch PDF runner + share logs. Pipeline ends at `PDF → final questions JSON`. **Pattern ingestion not implemented.**

---

## Batch PDF workflow (recommended for multi-PDF testing)

```bash
mkdir -p input_pdfs
# copy PDFs into input_pdfs/

uv run python -m meritranker_data_ingestion.cli run-pdf-folder \
  --input-dir input_pdfs \
  --output-dir batch_outputs \
  --answer-mode auto \
  --extractor marker \
  --ocr-engine auto \
  --provider azure-openai \
  --model "$MERITRANKER_BINDER_MODEL" \
  --timeout-seconds 180 \
  --continue-on-error \
  --clean-output
```

Per PDF: `batch_outputs/<stem>/<stem>.questions.json` + `<stem>.share-log.md`. Batch: `batch-summary.md`, `batch-run.log.jsonl`.

---

## Primary workflow

```bash
uv run meritranker-ingest run-pipeline \
  --input ~/Downloads/paper1.pdf \
  --output output \
  --expected-count 100 \
  --build-eligibility \
  --reconcile-artifacts \
  --build-pattern-input \
  --pattern-export-mode eligible-only
```

Standalone pattern input build:

```bash
uv run meritranker-ingest build-pattern-input \
  --package output/extraction_package \
  --export-mode eligible-only
```

---

## Pattern input artifacts (Part 12)

```
output/extraction_package/pattern-input/
├── pattern-question-input-package.json
├── eligible-pattern-input.json
├── review-pattern-input.json
├── blocked-pattern-input.json
└── pattern-question-input-summary.md
```

Export modes: `eligible-only` (default), `include-review`, `include-blocked`, `all`.

Safety gates:
- Eligibility required (unless `--allow-missing-eligibility`)
- Failed quality gate blocks build (unless `--allow-failed-quality-gate`)
- Warning quality gate allows export with package warnings

---

## Real sample (paper1.pdf)

| Metric | Value |
|--------|-------|
| Total questions | 100 |
| Eligible (eligibility) | 39 |
| Pattern input eligible-only export | **39** (must match eligible_count) |
| Quality gate | `warning` |

---

## ingestion_action mapping

| eligibility_status | ingestion_action |
|--------------------|------------------|
| `eligible_for_ingestion` | `ready_for_pattern_ingestion` |
| `review_required` | `hold_for_review` |
| `blocked` | `blocked_do_not_ingest` |

---

## Evidence extraction (Part 13A)

```bash
# Marker only (default — backward compatible)
uv run python -m meritranker_data_ingestion.cli prepare \
  --input ~/Downloads/SSC_CGL_Tier_27th.pdf \
  --output output

# Azure DI only (requires env credentials)
uv run python -m meritranker_data_ingestion.cli prepare \
  --input ~/Downloads/SSC_CGL_Tier_27th.pdf \
  --output output \
  --extractor azure-di

# Both extractors
uv run python -m meritranker_data_ingestion.cli prepare \
  --input ~/Downloads/SSC_CGL_Tier_27th.pdf \
  --output output \
  --extractor both \
  --force
```

Azure env vars:

- `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`
- `AZURE_DOCUMENT_INTELLIGENCE_KEY`

Optional SDK install: `uv sync --extra azure`

### Extractor artifacts

```
output/extraction_package/
├── marker/                    # unchanged Marker path
├── extractors/
│   ├── extractor-manifest.json
│   └── azure-di/
│       ├── layout-response.json
│       ├── content.md
│       ├── pages.json
│       ├── lines.json
│       ├── tables.json
│       ├── figures.json
│       ├── paragraphs.json
│       └── extraction-log.json
```

Part 13A/13B do **not** change `final/questions.json`, classification, or pattern input.

### Normalize evidence (Part 13B)

```bash
uv run python -m meritranker_data_ingestion.cli normalize-evidence \
  --package output/extraction_package \
  --primary-extractor auto
```

Artifacts:

```
output/extraction_package/
├── evidence/
│   ├── document-evidence.json
│   ├── document-evidence.md
│   └── evidence-summary.json
└── diagnostics/
    ├── extractor-comparison.json
    └── extractor-comparison.md
```

Partial extractor handling: when `--extractor both` and Azure fails, `prepare` exits 0 with partial success; `normalize-evidence` produces `extraction_status: partial` using Marker as primary.

Optional pipeline flag: `--normalize-evidence` (runs after prepare, not default).

---

### Semantic binding (Part 13C–13E)

```bash
uv run python -m meritranker_data_ingestion.cli bind-semantically \
  --package output/extraction_package \
  --answer-mode answer-key-only \
  --expected-count 100 \
  --provider mock \
  --include-answer-key-evidence \
  --force
```

Real provider:

```bash
export MERITRANKER_BINDER_PROVIDER=openai-compatible
export MERITRANKER_BINDER_ENDPOINT="<endpoint>"
export MERITRANKER_BINDER_API_KEY="<key>"
export MERITRANKER_BINDER_MODEL="<model>"

uv run python -m meritranker_data_ingestion.cli bind-semantically \
  --package output/extraction_package \
  --answer-mode answer-key-only \
  --expected-count 100 \
  --provider openai-compatible \
  --include-answer-key-evidence \
  --timeout-seconds 120 \
  --force
```

Azure OpenAI:

```bash
export MERITRANKER_BINDER_PROVIDER=azure-openai
export MERITRANKER_BINDER_ENDPOINT="<azure-openai-endpoint>"
export MERITRANKER_BINDER_API_KEY="<key>"
export MERITRANKER_BINDER_MODEL="<deployment-name>"
export MERITRANKER_BINDER_API_VERSION="<api-version>"
```

Evaluate binding quality (no LLM):

```bash
uv run python -m meritranker_data_ingestion.cli evaluate-semantic-binding \
  --package output/extraction_package \
  --expected-count 100
```

### Primary local workflow (Part 13G — one command)

```bash
uv run python -m meritranker_data_ingestion.cli run-semantic-pipeline \
  --input ~/Downloads/SSC_CGL_Tier_27th.pdf \
  --output output \
  --answer-mode answer-key-only \
  --expected-count 100 \
  --extractor marker \
  --provider azure-openai \
  --model "$MERITRANKER_BINDER_MODEL" \
  --include-answer-key-evidence \
  --timeout-seconds 180 \
  --clean-output
```

All artifacts land under `output/extraction_package/` (no `runs/` or duplicate roots unless you choose that path).

Stages: `prepare` → `normalize-evidence` → `bind-semantically` → `repair-semantic-binding` → `diagnose-semantic-issues` → `evaluate-semantic-binding --use-repaired`.

Optional flags: `--build-semantic-final-export`, `--generate-review-patch-template`.

### OCR evidence + final questions (Part 14A–14D)

Pipeline order: `prepare` → `normalize-evidence` → **OCR preflight** → `run-ocr-evidence` (page-split + image fallback) → `merge-evidence` → **question-windows** → `profile-extraction-capability` → **unsupported-layout gate** → `bind-semantically` (per-window chunks) → guard → repair (window-scoped options) → gate → `build-final-questions-export`.

**Part 14D image fallback:** Per-page PDF `UnsupportedContent`/`InvalidContentLength` → render PNG/JPEG (200/150/120/100 DPI) to `ocr/page-images/`. `ocr-evidence.json` always written (`status: failed` on zero lines). `azure-page-ocr-status.json` has per-page `attempts[]`.

**Part 14C OCR split:** Azure `InvalidContentLength` / `UnsupportedContent` → per-page PDF then rendered image. Explicit `--ocr-engine azure` failure stops before LLM unless `--allow-ocr-fallback`.

**Part 14C question windows:** `evidence/question-windows.json` scopes binder chunks and option source spans. Cross-window span reuse flagged/rejected.

**Part 14C unsupported layout:** Response-sheet / repeated Q-numbers → `stop_unsupported_layout` before LLM unless `--allow-unsupported-layout`. Report: `diagnostics/unsupported-layout-report.json`.

Install: `uv sync --extra azure --extra ocr`

```bash
uv run python -m meritranker_data_ingestion.cli run-semantic-pipeline \
  --input ~/Downloads/your-exam.pdf \
  --output output \
  --answer-mode auto \
  --ocr-engine auto \
  --auto-profile \
  --build-final-questions-export
```

Artifacts:

```
ocr/ocr-evidence.json
evidence/merged-document-evidence.json
evidence/extraction-capability-profile.json
final-questions/final-questions.json   # main pipeline output
```

- Azure DI: `uv sync --extra azure` + `AZURE_DOCUMENT_INTELLIGENCE_*` or `MERITRANKER_AZURE_DI_*`
- Page images: `uv sync --extra ocr` (PyMuPDF)
- Paddle fallback: `uv sync --extra paddle` (optional)
- `Chosen Option` is metadata only — never correct answer

### Semantic final export (Part 13H + 13J)

**Accepted-only = accepted-safe only.** Binding `accepted` is necessary but not sufficient. The final gate (`semantic_final_acceptance_gate.py`) routes visual/corrupt/unsafe items to review before export.

```bash
uv run python -m meritranker_data_ingestion.cli build-semantic-final-export \
  --package output/extraction_package \
  --export-mode accepted-only

uv run python -m meritranker_data_ingestion.cli export-semantic-review-items \
  --package output/extraction_package

# After editing review-patch.template.json → review-patch.json:
uv run python -m meritranker_data_ingestion.cli apply-semantic-review-patch \
  --package output/extraction_package \
  --patch output/extraction_package/semantic-final/review-patch.json

uv run python -m meritranker_data_ingestion.cli build-semantic-final-export \
  --package output/extraction_package \
  --export-mode accepted-plus-patched
```

Artifacts under `semantic-final/`:

```
semantic-final/
├── semantic-final-questions.json   # accepted-safe only (accepted-only mode)
├── semantic-final-report.json
├── final-gate-report.json          # Part 13J gate counts
├── final-gate-summary.md
├── review-items.json               # includes final_gate_status + reasons
├── review-items.md
├── review-patch.template.json
├── review-patch.applied.json
└── review-patch-report.json
```

**Final gate statuses:** `accepted_safe`, `review_visual_required`, `review_evidence_corrupt`, `review_manual_patch_required`, `blocked_bad_item`.

**SSC sample (fresh run):** `accepted_count` 88 → `accepted_safe_count` 24, `unsafe_previously_accepted_count` 64, `exported_count` 24, `ready_for_partial_accepted_ingestion` true.

Does not mutate `semantic-binding/semantic-bound-questions.json` or deterministic `final/`.

Final export report reads `semantic-binding-evaluation.repaired.json` and propagates semantic quality (`failed` on hallucination or count mismatch). `ready_for_partial_accepted_ingestion` is true only when all exported items are accepted-safe. `semantic-remaining-issues.json` uses top-level `items` (not `issues`).

### Stability guard + chunk diagnostics (Part 13I)

Pipeline order: `bind-semantically` → `apply-semantic-bad-item-guard` → `repair` → `diagnose` → `evaluate` → optional final export.

```bash
uv run python -m meritranker_data_ingestion.cli replay-semantic-chunks \
  --package output/extraction_package \
  --only-suspicious \
  --expected-count 100
# Add --execute to re-call LLM for suspicious chunks only
```

Artifacts:

```
semantic-binding/
├── chunks/chunk-000.json …
├── chunk-diagnostics.json
├── semantic-bad-items.json
└── semantic-chunk-replay-plan.json
```

Repair semantic binding after real LLM bind (no LLM — Part 13F/13G):

```bash
uv run python -m meritranker_data_ingestion.cli repair-semantic-binding \
  --package runs/ssc_cgl_tier_27th/extraction_package \
  --answer-mode answer-key-only \
  --expected-count 100

uv run python -m meritranker_data_ingestion.cli evaluate-semantic-binding \
  --package runs/ssc_cgl_tier_27th/extraction_package \
  --expected-count 100 \
  --use-repaired
```

Repaired artifacts (default — does not overwrite canonical bind output):

```
semantic-binding/
├── semantic-bound-questions.repaired.json
├── semantic-binding-repair-report.json
├── semantic-binding-validation.repaired.json
├── semantic-binding-evaluation.repaired.json
└── semantic-binding-repair-summary.md
```

Use `--overwrite-semantic-binding` only when explicitly replacing canonical semantic files.

Pipeline with semantic binder:

```bash
uv run python -m meritranker_data_ingestion.cli run-pipeline \
  --input file.pdf --output output \
  --normalize-evidence --use-semantic-binder \
  --force-semantic-binder \
  --semantic-binder-provider azure-openai \
  --include-answer-key-evidence \
  --timeout-seconds 180
```

Artifacts:

```
output/extraction_package/semantic-binding/
├── semantic-bound-questions.json
├── semantic-binding-validation.json
├── semantic-binding-evaluation.json   # Part 13D
├── semantic-binding-evaluation.md     # Part 13D
├── semantic-binding-report.json
├── semantic-binding-summary.md
└── binder-cache-manifest.json
```

**Answer-key-only mode:** accepts missing solution when answer key exists in source. Use `--answer-mode required` to flag missing solutions.

Env: `MERITRANKER_BINDER_PROVIDER`, `MERITRANKER_BINDER_MODEL`, `MERITRANKER_BINDER_ENDPOINT`, `MERITRANKER_BINDER_API_KEY`, `MERITRANKER_BINDER_API_VERSION` (Azure)

Evaluation report includes: quality thresholds, deterministic vs semantic comparison, `options_recovered_from_deterministic_failure`.

### Validated commands (import regression fix)

```bash
# Import smoke
uv run python -c "import meritranker_data_ingestion.cli; print('cli import ok')"

# Mock semantic bind + evaluate
uv run python -m meritranker_data_ingestion.cli bind-semantically \
  --package output/extraction_package \
  --answer-mode answer-key-only \
  --expected-count 100 \
  --provider mock \
  --force \
  --include-answer-key-evidence \
  --timeout-seconds 180

uv run python -m meritranker_data_ingestion.cli evaluate-semantic-binding \
  --package output/extraction_package \
  --expected-count 100
```

**Provider setup:**

```bash
cp .env.example .env   # edit with Azure/OpenAI credentials

uv run python -m meritranker_data_ingestion.cli test-llm-provider \
  --provider azure-openai \
  --model <deployment-name> \
  --timeout-seconds 60

uv run python -m meritranker_data_ingestion.cli bind-semantically \
  --package output/extraction_package \
  --answer-mode answer-key-only \
  --expected-count 100 \
  --provider azure-openai \
  --model <deployment-name> \
  --force \
  --include-answer-key-evidence \
  --timeout-seconds 180
```

`MERITRANKER_BINDER_MODEL` = Azure **deployment name** (not base model name).

Real-provider validation: `real_provider_validation_pending_credentials` unless `.env` is configured.

### SSC sample (mock, Part 13D)

| Metric | 13C mock | 13D mock |
|--------|----------|----------|
| semantic_item_count | 48 | 48 |
| questions_with_4_options | 28 | 30 |
| answer_available_count | 0 | 48 |
| accepted_count | 0 | 38 |
| hallucination_suspected | 0 | 0 |

Real LLM expected to improve semantic_item_count toward 100.

---

## Next recommended work

- **Part 14:** Accept/review semantic-bound candidates into final package (optional merge path)
- MeritRanker pattern ingestion consumer (separate system)
- Batch aggregation over `pattern-question-input-package.json` summaries
- Manual review workflow for `hold_for_review` items
