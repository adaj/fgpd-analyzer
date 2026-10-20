"""LLM-based measurement of the Four Goals for Productive Discussion in group chats.

`fgpd-analyzer` measures the Four Goals for Productive Discussion (FGPD;
Michaels & O'Connor) in student group chats with a Claude model: G1 *share*,
G2 *listen*, G3 *deepen* and G4 *engage*. The pipeline is: input table →
validation → units and pseudonyms → prompts → one request per group × goal ×
run → post-validation → consensus over runs → agent influence (post hoc) →
output tables. This package namespace re-exports the public API:
`FGPDAnalyzer`, `AnalyzerConfig`, `ColumnMap`, `ValidationReport`, `Issue`,
the exception classes and `parse_duration`.
"""

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

__version__ = "0.1.0"

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
