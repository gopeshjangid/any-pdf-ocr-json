# Project Context

## Repository

**Name:** `meritranker-data-ingestion`  
**Type:** Local-first Python CLI and tooling (planned)  
**Current phase:** Part 6 complete (final question package)—**MeritRanker pattern ingestion not implemented yet**

## Purpose

Build tooling to ingest exam and study PDFs for **MeritRanker**. The system converts PDFs (and intermediate markdown) into **source-faithful structured question JSON** with a **validation report**, preparing data for a later MeritRanker pattern ingestion stage.

## Problem Being Solved

Teachers, coaches, and content teams have PDFs in many formats:

- Previous year questions (PYQ)
- Government exam papers
- Teacher-created tests
- Documents with questions only or solutions at the end
- Scanned and image-heavy papers (future)

Manual conversion to structured JSON is slow and error-prone. Automated tools often **paraphrase, hallucinate answers, or drop content**. MeritRanker requires **faithful** structured data with audit trail—not summarized or "improved" text.

## Critical Principle

**Do not rewrite, summarize, paraphrase, infer, or alter facts from the original PDF.**

JSON must preserve original question facts, numbers, words, options, answers, solution mapping, image references, and source trace. Uncertain extractions go to review—not auto-approval.

## Intended Pipeline (High Level)

```
PDF → extraction package → deterministic parser → validation → review status → final JSON → (later) pattern ingestion
```

See `docs/current-architecture.md` for stage detail.

## Planned Technology (Not Implemented)

- Python + uv
- Marker (primary local PDF parser, behind adapter)
- Pydantic (schemas/validation)
- pypdfium2 (page render/crop, later)
- PaddleOCR (scanned fallback, future)
- Qwen3-VL (diagram syntax, future)
- Azure Document Intelligence (optional benchmark only)

## Governance System

This repository uses **11 role-based reviewers** under `roles/`. No implementation proceeds without:

1. Pre-implementation role review (all roles, ≥99% Release Gatekeeper confidence)
2. Post-implementation documentation updates
3. Architecture Gatekeeper approval for any stack or structure change

Entry point for agents: `skills/SKILL.md` and `feature-context/review-and-approval-workflow.md`.

## Key Documents

| Document | Purpose |
|----------|---------|
| `docs/next-agent-handoff.md` | Current state and next agent instructions |
| `docs/implementation-decisions.md` | Locked decisions and rationale |
| `docs/current-architecture.md` | Intended module and pipeline design |
| `docs/change-log.md` | Chronological change history |
| `feature-context/` | Feature scope, faithfulness rules, processing strategy |
| `roles/` | Enforceable review responsibilities |

## What This Repo Is Not

- Not a deployed web service (v1 is CLI)
- Not an LLM-first PDF summarizer
- Not authorized to invent answers or clean up teacher wording
- Not ready for production PDF ingestion until implementation passes full role review

## Audience for This Document

Future Cursor/LLM coding agents, human reviewers, and contributors who need a single-page orientation before touching code.
