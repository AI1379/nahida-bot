---
name: conventional-commits
description: "Use when: generating git commit messages, writing commit descriptions, analyzing code changes for commit messages, or helping users craft conventional commits following the Conventional Commits specification for Nahida Bot."
---

# Conventional Commits Guidelines for Nahida Bot

This instruction ensures AI agents generate clear, consistent, and actionable commit messages following the Conventional Commits specification.

## Quick Reference

**Format:** `<type>(<scope>): <description>`

**Example:** `feat(channel): add message parsing for QQ platform`

## Commit Type

Must be one of:

| Type | Purpose | Example |
|------|---------|---------|
| `feat` | New feature or capability (scope required) | `feat(channel): add Telegram support` |
| `fix` | Bug fix (scope required) | `fix(memory): handle malformed JSON correctly` |
| `docs` | Documentation changes only (no scope) | `docs: update README with API examples` |
| `style` | Code formatting (no logic change, no scope) | `style: reformat imports with ruff` |
| `refactor` | Code restructuring (scope required) | `refactor(core): extract bot logic to separate module` |
| `test` | Test additions/modifications (scope required) | `test(db): add unit tests for memory repository` |
| `chore` | Dependencies, tooling, build (no scope) | `chore: upgrade pytest to 8.4.1` |

## Scope (Required for feat/fix/refactor/test)

For `feat`, `fix`, `refactor`, and `test` commits, a scope **must** be specified and chosen from the list below. For `docs`, `style`, and `chore` commits, omit the scope.

### Module Scopes

Map to `nahida_bot/` top-level packages:

| Scope | Path | Description |
|-------|------|-------------|
| `agent` | `agent/` | Agent core (loop, metrics, context, etc.) |
| `memory` | `agent/memory/` | Memory persistence and retrieval |
| `providers` | `agent/providers/` | LLM provider integrations |
| `channel` | `channel/` | Chat platform adapters (QQ, Telegram, etc.) |
| `cli` | `cli/` | CLI entry point and commands |
| `core` | `core/` | Event bus, app lifecycle, shared types |
| `db` | `db/` | Database engine and repositories |
| `gateway` | `gateway/` | Remote gateway and node communication |
| `node` | `node/` | Node runtime |
| `plugins` | `plugins/` | Plugin system |
| `workspace` | `workspace/` | Workspace management |

### Cross-cutting Scopes

| Scope | When to Use |
|-------|-------------|
| `docs` | ❌ Do not use as scope. Use `docs:` type directly. |
| `ci` | CI/CD config, git hooks, lint/format tooling |
| `tests` | Integration tests spanning multiple modules |

### Scope Rules

1. **Only use scopes from the list above** — no ad-hoc or nested scopes (e.g. avoid `agent/loop`, `agent/memory`, `qq-adapter`).
2. **Pick the most specific scope** — changes in `agent/providers/` use `providers`, not `agent`.
3. **No scope for docs/style/chore** — e.g. `docs: update ROADMAP`, `chore: upgrade pytest`.
4. **Changes spanning multiple modules with no clear primary target** — use the broadest applicable scope, or split into separate commits.

Examples:
- `feat(channel): add Telegram platform adapter`
- `fix(memory): resolve race condition in SQLite writes`
- `refactor(core): extract event dispatcher from app lifecycle`
- `test(db): add async connection pool stress tests`
- `ci: add pyright to pre-push hook`
- `docs: update ROADMAP for phase 3`

## Description

Write a **clear, concise, present-tense** description (50 characters or less when possible):

- ✅ `add error handling for network timeouts`
- ✅ `implement caching for user sessions`
- ❌ `Added error handling for network timeouts` (past tense)
- ❌ `error handling and validation improvements` (vague, multiple changes)

## Detailed Message Body (Optional but Recommended)

When a commit includes complex changes, add a blank line after the header, then a detailed body:

```
feat(channel): add Telegram platform support

- Implement message sending via Telegram Bot API
- Add webhook handler for incoming messages
- Support inline keyboards and reply markup
- Add configuration for API token management

Closes: #42
```

### Body Guidelines

1. **Explain the "why," not the "what"** — The diff shows what changed; explain why it matters
2. **One logical change per commit** — Don't mix multiple features or fixes
3. **Reference issues when applicable** — Use `Closes: #123` or `Fixes: #456`
4. **Keep formatting clean** — Use blank lines between logical sections
5. **Mention breaking changes** — If applicable, prefix footer with `BREAKING CHANGE:`

Example with breaking change:

```
refactor(core): change message interface

Renamed Message.text to Message.content for consistency.
Updated all adapters to use the new field name.

BREAKING CHANGE: Message.text is now Message.content
```

## Analysis Process for Commit Generation

When asked to generate a commit message:

1. **Inspect the actual changes** — Use git diff, git status, or examine staged files
2. **Identify the primary change** — What's the main intent?
3. **Determine the type** — Is it a feature, fix, refactor, or other?
4. **Choose the scope** — For feat/fix/refactor/test, pick from the allowed scope list. For other types, omit scope.
5. **Write a clear description** — Present tense, action-oriented, specific
6. **Include a body if complex** — Explain motivations and context
7. **Reference issues** — Link to related GitHub issues if applicable

## Common Pitfalls

❌ **Generic messages:** `Refactor code structure for improved readability and maintainability`
✅ **Specific messages:** `refactor(core): extract validation logic into separate function`

❌ **Mixed concerns:** `fix: update docs and refactor handler`
✅ **Single concern:** `test(db): add parametrized tests for edge cases`

❌ **Missing scope:** `feat: add caching`
✅ **Correct scope:** `feat(memory): add caching for user sessions`

❌ **Ad-hoc scope:** `feat(agent/loop): add retry logic`
✅ **Allowed scope:** `feat(agent): add retry logic to main loop`

❌ **Passive voice:** `multiple issues were fixed`
✅ **Active voice:** `fix(core): resolve race condition in event dispatcher`

## Workflow

When you encounter a request to generate a commit message:

1. **Extract changed files** — Analyze what's actually staged or modified
2. **Summarize changes logically** — Group related modifications together
3. **Check against guidelines** — Ensure type, scope, and description follow conventions
4. **Provide context** — Include brief body explaining why, not just what
5. **Avoid generic templates** — Never default to "improve code quality" or similar

## Pre-commit Validation

Before suggesting a commit message, verify:

- [ ] Type is one of: feat, fix, docs, style, refactor, test, chore
- [ ] Scope is present for feat/fix/refactor/test, omitted for docs/style/chore
- [ ] Scope (if present) is from the allowed scope list
- [ ] Description is concise and action-oriented
- [ ] Description uses present tense
- [ ] Description is specific to the actual changes
- [ ] Message clearly communicates intent
- [ ] No generic refactoring/improvement placeholders used
