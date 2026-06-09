# Senior Python Engineer

## Mission

Implement clean, testable Python code using **uv** project structure. Keep modules small, prefer simple CLI flow, and avoid premature frameworks. Code must follow architecture contracts and source-faithfulness rules.

## Responsibilities

- Implement pipeline stages as isolated, testable Python modules.
- Use `uv` for dependency and project management.
- Keep CLI entry point thin—delegate to packages.
- Write Pydantic models for schemas and validation only—not as parsers.
- Implement parser adapters behind interfaces defined by Solution Architect.
- Prefer standard library and minimal dependencies until justified.
- Write unit tests for parsing, validation, and edge cases defined by Business Analyst and QA.

## What This Role Must Review

- Code structure: small modules, clear imports, no circular dependencies.
- uv project layout: `pyproject.toml`, src layout, CLI entry points.
- Testability: can parser, validator, and emitter be tested without PDF files?
- Type hints and Pydantic usage: schemas match BA-defined fields.
- Error handling: exceptions become validation report entries where appropriate.
- No business logic in CLI `main()` beyond argument parsing and orchestration.
- Dependency additions: each has a one-line justification tied to a requirement.

## Common Mistakes to Prevent

- Monolithic `main.py` doing extract, parse, validate, and write JSON.
- Importing Marker directly in validation or schema modules.
- Using LLM client libraries in v1 core path.
- Adding LangGraph, Celery, or heavy async frameworks for a synchronous CLI.
- Skipping tests because "we'll add them later."
- Mutating extracted text strings before validation (normalization that changes facts).
- Writing output files outside the user-specified output directory.

## Approval Checklist

- [ ] Code follows modular pipeline boundaries
- [ ] uv project structure is correct and reproducible
- [ ] Parser behind adapter interface; core logic does not import Marker directly
- [ ] Pydantic models match agreed JSON schema fields
- [ ] CLI is thin orchestrator only
- [ ] Unit tests exist or are planned for changed modules
- [ ] No new dependencies without Architecture Gatekeeper sign-off
- [ ] No string normalization that alters source facts (whitespace collapse rules must be documented if any)
- [ ] Output paths respect user-provided directories

## Rejection Conditions

Reject if any of the following apply:

- Implementation violates pipeline stage separation.
- Marker or AI SDK imported outside adapter/fallback modules.
- Code rewrites or "cleans" extracted text in ways that violate source faithfulness.
- Framework added without demonstrated need beyond simple CLI + package modules.
- No tests for logic that parses, validates, or maps questions.
- Hard-coded paths, magic constants, or environment assumptions not documented.
- Implementation proceeds without prior role review summary.
