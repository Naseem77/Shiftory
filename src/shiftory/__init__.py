"""Shiftory's typed public API."""

__version__ = "0.1.0"

from shiftory.evidence.builder import analyze
from shiftory.explain.validator import validate_explanation
from shiftory.git.repository import resolve_comparison, resolve_repository
from shiftory.render.report import render_report

__all__ = [
    "__version__",
    "analyze",
    "render_report",
    "resolve_comparison",
    "resolve_repository",
    "validate_explanation",
]
