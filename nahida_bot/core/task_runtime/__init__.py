"""Shared execution admission and inherited deadlines.

The first runtime slice governs exec operations; session ownership and durable
task recovery remain with their existing services.
"""

from .admission import AdmissionError, ExecutionAdmission
from .deadlines import deadline_scope, independent_deadline_scope, remaining_seconds

__all__ = [
    "AdmissionError",
    "ExecutionAdmission",
    "deadline_scope",
    "independent_deadline_scope",
    "remaining_seconds",
]
