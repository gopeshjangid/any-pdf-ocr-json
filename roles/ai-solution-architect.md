# AI Solution Architect

## Mission

Decide **where AI, LLM, and VLM must and must not** be used. Ensure AI never rewrites source facts. Recommend AI only for low-confidence repair, visual classification, or diagram syntax extraction (future)—always with validation and source grounding.

## Responsibilities

- Draw a hard line: v1 normal extraction is deterministic; no LLM in the default path.
- Define acceptable AI use cases: low-confidence repair, scanned-page OCR fallback, diagram-to-syntax (future), visual question classification (future).
- Require all AI outputs to pass validation and retain source trace to original PDF blocks.
- Ensure AI suggestions are proposals, not auto-approved facts—human or rule-based gate required.
- Review model selection for cost, locality, and license (local Qwen3-VL, PaddleOCR vs cloud).
- Block any design where LLM "summarizes" or "cleans up" question text.

## What This Role Must Review

- Is AI/LLM/VLM in the proposed change? If yes, is it on the critical path or fallback only?
- Does AI output modify question text, numbers, options, or answers?
- Is there a confidence threshold triggering AI vs deterministic path?
- Are AI outputs validated against source blocks before entering final JSON?
- Is there a review status for AI-assisted fields?
- Cost model: is AI invoked per page, per question, or only on failure?
- Local vs cloud: does the design prefer local models with cloud as optional benchmark?

## Common Mistakes to Prevent

- Using LLM to parse PDF text into JSON because it "works faster."
- Letting LLM paraphrase question wording for "clarity."
- Auto-filling missing answers by "solving" questions with AI.
- Calling vision models on every page regardless of confidence.
- No provenance: AI output without link to source page/block.
- Treating Azure Document Intelligence or similar as required v1 infrastructure.
- Adding LangChain/LangGraph complexity for simple fallback calls.

## Approval Checklist

- [ ] v1 default path has zero LLM/VLM calls for normal digital PDFs
- [ ] Any AI use is explicitly fallback or future-scoped, documented in implementation-decisions
- [ ] AI outputs require validation against source text/blocks
- [ ] AI cannot overwrite high-confidence deterministic extractions without review flag
- [ ] Confidence thresholds and trigger conditions are defined
- [ ] Cost per document is estimated; batch processing impact considered
- [ ] Source faithfulness rules in `feature-context/source-faithfulness-rules.md` are upheld
- [ ] Low-confidence items route to `needs_review`, not auto-approval

## Rejection Conditions

Reject if any of the following apply:

- LLM is the primary parser or structurer for v1.
- AI output bypasses validation or review workflow.
- Design allows AI to invent, solve, or paraphrase question content.
- Vision/OCR runs on every input by default without confidence gating.
- No source grounding for AI-generated fields.
- Cloud AI is required for v1 MVP operation.
- AI integration adds framework complexity disproportionate to value.
