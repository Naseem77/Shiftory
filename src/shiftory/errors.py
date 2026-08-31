"""Typed errors shared by the library and CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ShiftoryError(Exception):
    message: str
    code: str = "shiftory_error"
    details: dict[str, Any] | None = None
    exit_code: int = 2

    def __str__(self) -> str:
        return self.message


class GitError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "git_error", details)


class GitFilterError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "git_filter_error", details)


class ScopeError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "invalid_scope", details)


class CoverageError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "coverage_error", details)


class GraphoraError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "graphora_error", details)


class ValidationError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "validation_error", details)


class CacheError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "cache_error", details)


class ChunkBudgetError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "chunk_budget_error", details)


class RetrievalError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "retrieval_error", details)


class CompositionError(ShiftoryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "composition_error", details)
