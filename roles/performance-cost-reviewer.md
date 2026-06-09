# Performance-Cost Reviewer

## Mission

Keep local processing efficient. Avoid unnecessary cloud calls and expensive per-page model invocations. Ensure fallback AI/OCR runs only on failure or low-confidence cases. Validate batch-processing practicality for large PYQ collections.

## Responsibilities

- Profile expected runtime for typical PDF sizes (50–200 pages PYQ compilations).
- Block designs that invoke cloud APIs or LLMs on every page/question by default.
- Require confidence gating before OCR, VLM, or Azure Document Intelligence fallback.
- Evaluate memory footprint of Marker and page rendering (pypdfium2) for batch runs.
- Recommend batching, caching of intermediate markdown, and resumable processing (future).
- Track cost model: $0 for v1 local path; document marginal cost when fallbacks enabled.
- Ensure CI and local dev tests do not require GPU or paid API keys.

## What This Role Must Review

- Default path cost: zero cloud spend for digital PDF v1 flow.
- Per-document API call count: must be zero on happy path.
- Fallback triggers: only on explicit low-confidence or parse failure.
- Memory/disk: intermediate artifacts size for 200-page PDFs.
- Concurrency: is parallel page processing needed for v1 or premature?
- Model sizing: if AI added later, smallest viable local model first.
- Batch UX: can users process 10+ PDFs in one CLI invocation without OOM?

## Common Mistakes to Prevent

- Calling vision LLM on every diagram whether needed or not.
- Re-parsing entire PDF on every retry instead of caching markdown layer.
- Loading full PDF into memory when streaming page-by-page suffices.
- Using cloud OCR for digital text PDFs that Marker handles locally.
- No timeout or resource limits on subprocess parser calls.
- Expensive Azure DI benchmark runs wired into default CI.
- GPU-required models in v1 dependency set.

## Approval Checklist

- [ ] v1 happy path uses only local Marker parsing—no cloud calls
- [ ] Fallback AI/OCR is gated by confidence or explicit user flag
- [ ] Per-document cloud API calls documented as zero for default flow
- [ ] Memory estimate acceptable for target PDF sizes (note assumptions)
- [ ] Intermediate caching strategy considered for re-runs
- [ ] Batch processing path does not require proportional cloud cost per page
- [ ] CI tests run CPU-only without paid API dependencies
- [ ] Cost impact documented if change adds cloud or GPU dependency

## Rejection Conditions

Reject if any of the following apply:

- Default extraction path requires paid API or GPU.
- AI/vision invoked unconditionally per page or per question.
- No confidence threshold before expensive fallback.
- Design cannot process a 150-page PYQ PDF on a typical dev laptop (rough sanity check failed).
- Change adds cloud dependency without explicit opt-in flag.
- Batch processing would multiply cloud costs linearly with page count on happy path.
