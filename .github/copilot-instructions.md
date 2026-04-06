# Nahida Bot Development Instructions

This document guides AI agents working on the Nahida Bot project to maintain code quality, consistency, and best practices.

## Quick Start

- **Runtime**: Python 3.12+ with `uv` (or `pip`)
- **Testing**: `pytest` with async support, ≥80% coverage requirement
- **Type Checking**: `pyright` (strict mode enabled)
- **Linting**: `ruff` (check + format)
- **Pre-commit**: Local hooks run ruff, pyright, pytest

## Code Quality Standards

### Linting & Formatting
- Run `ruff check .` before committing
- Run `ruff format .` to auto-fix formatting
- All code must pass ruff checks without warnings

### Type Checking
- All functions and methods must have type annotations
- Use `pyright` in `standard` mode (strict type checking enabled)
- Avoid `Any` types; use `TypeVar`, `Protocol`, or `TypeGuard` instead
- Use `TYPE_CHECKING` blocks to avoid circular imports

### Testing Requirements
- Write tests following **AAA pattern** (Arrange-Act-Assert)
- Minimum coverage: **80%** overall, **90%** for core modules, **95%** for new code
- Use `@pytest.mark.asyncio` for async tests
- Organize tests by: unit → integration → e2e
- Mock external dependencies; do not call real APIs in tests

## Naming Conventions

| Element | Style | Example |
|---------|-------|---------|
| Module | snake_case | `message_parser.py` |
| Class | PascalCase | `MessageHandler` |
| Function | snake_case | `parse_message()` |
| Variable | snake_case | `user_count` |
| Constant | UPPER_SNAKE_CASE | `MAX_RETRY_COUNT` |
| Private | _leading_underscore | `_internal_state` |

## Documentation Standards

### Docstrings (Google Style)
```python
def send_message(target: str, content: str, *, retry: int = 3) -> bool:
    """Send message to target.

    Args:
        target: Target identifier (user or group ID).
        content: Message content.
        retry: Retry count on failure.

    Returns:
        True if sent successfully, False otherwise.

    Raises:
        ConnectionError: When unable to connect to server.
        ValueError: When content is empty.
    """
```

### Import Order
1. Standard library (`asyncio`, `json`, `pathlib`, etc.)
2. Third-party (`httpx`, `pydantic`, etc.)
3. Local modules (`from nahida_bot.core import ...`)

## Type Hints Usage

- Use built-in generics: `list[T]`, `dict[K, V]`
- Nullable types: `T | None` instead of `Optional[T]`
- Type aliases: `TypeAlias` for complex types
- Async functions: `Awaitable[T]` for callbacks
- Use `Protocol` for structural typing; `ABC` for inheritance

## Error Handling

- Define custom exceptions inheriting from `BotError`
- Use exception chaining: `raise NewError(...) from e`
- Log exceptions with context using `logger.exception()`
- Avoid bare `except:` clauses

## Project Structure

```
nahida_bot/
├── core/          # Core bot functionality
├── adapters/      # Platform adapters (QQ, etc.)
├── handlers/      # Message handlers
├── utils/         # Utility functions
└── models/        # Data models

tests/
├── unit/          # Unit tests
├── integration/   # Integration tests
└── e2e/           # End-to-end tests
```

## Git Workflow

### Commit Messages (Conventional Commits)

When generating or writing commit messages, follow the **Conventional Commits** format.

**Quick reference:**
- Format: `<type>(<scope>): <subject>`
- Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`
- Example: `feat(adapter): add Telegram platform support`

**For detailed guidance:** See [`.github/instructions/conventional-commits.instructions.md`](.github/instructions/commits.instructions.md)

Key principles:
- Analyze **actual code changes** before generating messages
- Avoid generic placeholders like "improve code quality"
- Include context in the message body for complex changes
- Use clear, present-tense, action-oriented language

### Branches
- `main`: Stable release version
- `develop`: Development branch
- `feature/*`: Feature branches
- `fix/*`: Bug fix branches

## Pre-commit Checklist

Before committing:
- [ ] Code passes `ruff check`
- [ ] Code passes `ruff format`
- [ ] Type check passes (`pyright`)
- [ ] Tests pass (`pytest`)
- [ ] Coverage meets requirements (≥80%)
- [ ] Documentation updated if needed

## Async Programming

- Use async/await pattern for I/O operations
- Use `@pytest.mark.asyncio` for async tests
- Properly handle `asyncio` context in fixtures
- Use `AsyncIterator` for streaming operations

## Project Configuration

Key settings in `pyproject.toml`:
- Python version: 3.12+
- Ruff: line-length 88, target-version py312
- Pyright: typeCheckingMode = "standard"
- Pytest: asyncio_mode = "auto", min coverage 80%
