"""Propagate a parent's total deadline without restarting its clock."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Iterator

_deadline: ContextVar[float | None] = ContextVar("execution_deadline", default=None)


@contextmanager
def deadline_scope(seconds: float) -> Iterator[None]:
    """Bound nested work by the earlier of its own and its parent's deadline."""
    parent = _deadline.get()
    deadline = monotonic() + seconds
    token = _deadline.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_seconds() -> float | None:
    deadline = _deadline.get()
    return max(0.0, deadline - monotonic()) if deadline is not None else None


@contextmanager
def independent_deadline_scope() -> Iterator[None]:
    """Detach background work from the submitting tool's short-lived budget."""
    token = _deadline.set(None)
    try:
        yield
    finally:
        _deadline.reset(token)
