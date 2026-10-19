"""fgpd-analyzer: LLM-based measurement of the Four Goals for Productive
Discussion (Michaels & O'Connor) in student group chats."""

from .analyzer import FGPDAnalyzer
from .config import GOAL_LABELS, GOALS, AnalyzerConfig, ColumnMap, parse_duration
from .exceptions import (
    BackendError,
    ConfigError,
    FGPDError,
    InputValidationError,
    PartialResultsError,
    PrivacyWarning,
    PromptError,
    PromptSchemaMismatch,
)
from .validation import Issue, ValidationReport

__version__ = "0.1.0.dev0"

__all__ = [
    "GOALS",
    "GOAL_LABELS",
    "AnalyzerConfig",
    "BackendError",
    "ColumnMap",
    "ConfigError",
    "FGPDAnalyzer",
    "FGPDError",
    "InputValidationError",
    "Issue",
    "PartialResultsError",
    "PrivacyWarning",
    "PromptError",
    "PromptSchemaMismatch",
    "ValidationReport",
    "__version__",
    "parse_duration",
]
