---
name: conventional-commits
description: "Use when: generating git commit messages, writing commit descriptions, analyzing code changes for commit messages, or helping users craft conventional commits following the Conventional Commits specification for Nahida Bot."
---

# Conventional Commits Guidelines for Nahida Bot

This instruction ensures AI agents generate clear, consistent, and actionable commit messages following the Conventional Commits specification.

## Quick Reference

**Format:** `<type>(<scope>): <description>`

**Example:** `feat(adapter): add message parsing for QQ platform`

## Commit Type

Must be one of:

| Type | Purpose | Example |
|------|---------|---------|
| `feat` | New feature or capability | `feat(adapter): add Telegram support` |
| `fix` | Bug fix | `fix(parser): handle malformed JSON correctly` |
| `docs` | Documentation changes only | `docs: update README with API examples` |
| `style` | Code formatting (no logic change) | `style: reformat imports with ruff` |
| `refactor` | Code restructuring (no feature/bug change) | `refactor(core): extract bot logic to separate module` |
| `test` | Test additions/modifications | `test(handler): add unit tests for message parsing` |
| `chore` | Dependencies, tooling, build (no code logic) | `chore: upgrade pytest to 8.4.1` |

## Scope (Optional but Recommended)

Specify the affected area:

- **Module:** `feat(adapters)`, `fix(handlers)`, `refactor(utils)`
- **Component:** `feat(message-parser)`, `fix(connection-pool)`
- **Feature area:** `feat(async-support)`, `docs(type-hints)`

Examples:
- `test(core): add async context manager tests`
- `fix(qq-adapter): decode message encoding correctly`

## Description

Write a **clear, concise, present-tense** description (50 characters or less when possible):

- ✅ `add error handling for network timeouts`
- ✅ `implement caching for user sessions`
- ❌ `Added error handling for network timeouts` (past tense)
- ❌ `error handling and validation improvements` (vague, multiple changes)

## Detailed Message Body (Optional but Recommended)

When a commit includes complex changes, add a blank line after the header, then a detailed body:

```
feat(adapter): add Telegram platform support

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
4. **Choose the scope** — What component/module is affected?
5. **Write a clear description** — Present tense, action-oriented, specific
6. **Include a body if complex** — Explain motivations and context
7. **Reference issues** — Link to related GitHub issues if applicable

## Common Pitfalls

❌ **Generic messages:** `Refactor code structure for improved readability and maintainability`
✅ **Specific messages:** `refactor(parser): extract validation logic into separate function`

❌ **Mixed concerns:** `fix: update docs and refactor handler`
✅ **Single concern:** `test(parser): add parametrized tests for edge cases`

❌ **Vague scope:** `feat: improvements`
✅ **Clear scope:** `feat(async): add asyncio support to message handlers`

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
- [ ] Scope (if present) refers to an actual component/module
- [ ] Description is concise and action-oriented
- [ ] Description uses present tense
- [ ] Description is specific to the actual changes
- [ ] Message clearly communicates intent
- [ ] No generic refactoring/improvement placeholders used
