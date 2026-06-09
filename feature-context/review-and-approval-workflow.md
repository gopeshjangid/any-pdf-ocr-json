# Review and Approval Workflow

Every code change, dependency addition, architecture change, or feature implementation must pass **all role reviews** before work begins (pre-implementation) and again before merge/release (post-implementation).

## Overview

```
Proposal → 10 Role Reviews → Architecture Gatekeeper → Release Gatekeeper (≥99%) → Implement → Docs Update → Post-Implementation Review → Release
```

If confidence is below 99% at any gate, **stop** and resolve blockers before proceeding.

## Roles (All Must Review)

| # | Role | Document |
|---|------|----------|
| 1 | Product Manager | `roles/product-manager.md` |
| 2 | Business Analyst | `roles/business-analyst.md` |
| 3 | Solution Architect | `roles/solution-architect.md` |
| 4 | AI Solution Architect | `roles/ai-solution-architect.md` |
| 5 | Senior Python Engineer | `roles/senior-python-engineer.md` |
| 6 | QA Reviewer | `roles/qa-reviewer.md` |
| 7 | Security Reviewer | `roles/security-reviewer.md` |
| 8 | Performance-Cost Reviewer | `roles/performance-cost-reviewer.md` |
| 9 | Documentation Mentor | `roles/documentation-mentor.md` |
| 10 | Architecture Gatekeeper | `roles/architecture-gatekeeper.md` |
| 11 | Release Gatekeeper | `roles/release-gatekeeper.md` |

## Cross-Role Communication

Roles do not work in isolation. Each reviewer must:

1. **Read concerns from prior roles** in the review summary before marking PASS.
2. **Flag downstream impact** (e.g., BA edge case → QA test requirement).
3. **Escalate conflicts** explicitly (e.g., Product Manager scope vs Architecture Gatekeeper stability).
4. **Not PASS with open questions**—open questions are blockers or `needs_review` at governance level.

**Cross-check matrix (minimum):**

| If this role PASSes... | Must confirm with... |
|------------------------|----------------------|
| Product Manager (scope) | Business Analyst (requirements coverage) |
| Solution Architect (design) | Architecture Gatekeeper (no unnecessary change) |
| AI Solution Architect | Source faithfulness rules + QA (no AI on critical path) |
| Senior Python Engineer | Solution Architect (boundaries) + Security (paths) |
| QA Reviewer | Business Analyst (acceptance criteria) |
| Security Reviewer | Performance-Cost (no default network) |
| Documentation Mentor | All roles (decisions recorded) |

## Architecture Gatekeeper Checkpoint

Before implementation, Architecture Gatekeeper must confirm:

- Four gate questions answered (see `roles/architecture-gatekeeper.md`)
- No stack replacement without adapter and decision record
- No new dependency without justification
- Pipeline contracts unchanged or explicitly migrated

**Architecture Gatekeeper can REJECT even if other roles PASS** if the change is an unnecessary design shift.

## Release Gatekeeper Final Approval

Release Gatekeeper approves only when:

- All 10 preceding roles show **PASS** with substantive notes
- Overall confidence **≥ 99%**
- Documentation Mentor confirms handoff and change log are current (post-implementation)
- No open blockers in blocker report template

If confidence is 90–98%: **BLOCK** with exact blockers.  
If confidence < 90%: **REJECT** proposal and restart review.

## Pre-Implementation vs Post-Implementation

### Pre-Implementation (Required Before Any Code)

Agent produces:

1. Proposed change description
2. Role review table (all 11 roles)
3. Confidence score and Release Gatekeeper decision
4. List of docs to update after implementation

**No Python files, dependencies, or config** until Release Gatekeeper approves with ≥99% confidence.

### Post-Implementation (Required Before Merge/Complete)

Agent produces:

1. Updated role review table focused on changed areas
2. QA evidence (tests run, fixtures added)
3. Documentation Mentor verification
4. Release Gatekeeper final sign-off

## Agent Instructions (Cursor / LLM)

1. Read `skills/SKILL.md` and this workflow first.
2. Read all `roles/*.md` and relevant `feature-context/*.md`.
3. Read `docs/project-context.md`, `docs/current-architecture.md`, `docs/implementation-decisions.md`, `docs/next-agent-handoff.md`.
4. Produce pre-implementation role review summary.
5. Wait for ≥99% approval (self-evaluate all roles honestly—do not rubber-stamp PASS).
6. Implement only after approval.
7. Update `docs/change-log.md` and `docs/next-agent-handoff.md` after every change.
8. Run post-implementation review before declaring task complete.

## Rubber-Stamping Is a Violation

Marking all roles PASS without reading checklists is equivalent to REJECT at Release Gatekeeper. Agents must cite specific checklist items satisfied or note N/A with reason.

## Current Phase

**Governance setup complete.** Next implementation task (Python/uv/Marker pipeline) requires full pre-implementation review per this workflow before any code is written.
