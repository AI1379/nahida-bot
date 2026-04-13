---
description: "Use when reviewing any commit, pull request, module, or architecture change in this project. Enforce senior-level review covering correctness, security, stability, performance, redundant checks/tests, and both missing and unnecessary abstractions. Require report-only output with explicit TODO/FIXME markers and no direct code edits during review."
name: "Project-Wide Senior Code Review Rules"
---
# Project-Wide Senior Code Review Rules

## Scope

- Apply to the entire project, not just memory-related changes.
- Use for commit review, PR review, module review, and architecture review.
- Default mindset: senior engineer quality gate, not style-only scanning.

## Hard Requirements

- Do not modify code during review unless the user explicitly asks for implementation.
- Findings must include concrete evidence from real diffs and related runtime paths.
- Prioritize findings by severity and impact.
- Use explicit action markers:
  - `FIXME:` correctness, security, data integrity, or production-risk issues.
  - `TODO:` quality improvements, maintainability, observability, and test hardening.

## Mandatory Review Dimensions

- Correctness and Regression Risk:
  - Logical bugs, edge-case failures, state inconsistency, wrong assumptions.
  - Behavior changes that are undocumented or not covered by tests.
  - Error handling paths, fallback paths, and partial-failure behavior.
- Security and Trust Boundaries:
  - Input validation gaps, unsafe parsing/deserialization, injection surfaces.
  - Secret/token leakage in logs, exceptions, telemetry, snapshots, or tests.
  - Authorization and access-control assumptions, especially cross-tenant/session data.
  - Dependency and supply-chain risk signals when new packages are added.
- Concurrency and Reliability:
  - Race conditions, ordering assumptions, non-atomic multi-step writes.
  - Async cancellation safety, retry storms, idempotency, timeout behavior.
  - Resource lifecycle leaks (connections, file handles, tasks, sessions).
- Data Integrity and Persistence:
  - Schema/contract mismatch, backward compatibility, migration risks.
  - Timezone handling, ordering guarantees, dedup logic, retention/eviction correctness.
  - Lossy fallback behavior that may hide missing data.
- Performance and Cost:
  - Hot-path CPU/memory overhead, N+1 queries, unnecessary serialization.
  - Repeated work that should be cached or batched.
  - Token/context growth and unnecessary prompt churn for LLM workflows.
- API and Contract Design:
  - Public interface consistency, compatibility guarantees, and semantic clarity.
  - Distinguish missing abstraction from over-abstraction.
  - Flag abstraction leaks where callers depend on implementation-specific methods.
- Observability and Operability:
  - Missing metrics/traces for critical paths.
  - Logs that are noisy, misleading, or missing actionable context.
  - Missing error codes/classification that blocks diagnosis.
- Test Effectiveness:
  - Redundant tests, duplicate assertions, weak or non-deterministic checks.
  - Tests that pass but do not protect critical behavior.
  - Missing negative-path, boundary, failure-mode, and compatibility coverage.
  - Mismatch between test names/comments and actual assertions.
- Documentation and Change Hygiene:
  - Doc drift with code changes.
  - Deprecated paths/modules not reflected in architecture docs.
  - Ambiguous comments that can mislead future maintainers.

## Review Method

- Inspect target commits and surrounding call chains before concluding.
- Validate with focused tests where feasible; report what was and was not validated.
- Separate confirmed findings from hypotheses or assumptions.
- Avoid generic advice; tie each point to a specific file and line.

## Output Format

- Section 1: Findings (ordered by severity).
- Section 2: Open questions and assumptions.
- Section 3: Coverage gaps and residual risk.
- Section 4: Brief change summary (secondary).

For each finding include:
- Severity.
- Impact.
- Evidence (file and line references).
- Why it matters now.
- Suggested direction (no code edits unless requested).
- Required marker: `FIXME:` or `TODO:`.

## Quality Bar

- Prefer high-confidence conclusions based on code evidence.
- If evidence is insufficient, explicitly state uncertainty and required validation steps.
- Do not claim safety based only on passing tests.
- Explicitly call out both unnecessary abstraction and missing abstraction when relevant.

## Communication Style

- Keep language direct, technical, and actionable.
- Keep summaries brief; findings are the primary output.
- Avoid vague approval language when unresolved risks remain.
