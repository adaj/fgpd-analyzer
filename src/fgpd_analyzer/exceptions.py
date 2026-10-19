"""Exception hierarchy. One failing group never aborts a batch; these are raised
only for problems that make the whole call meaningless."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .validation import ValidationReport


class FGPDError(Exception):
    """Base class for every error raised by fgpd-analyzer."""


class ConfigError(FGPDError, ValueError):
    """Invalid configuration value or combination."""


class InputValidationError(FGPDError):
    """The input failed validation. `.report` holds every issue found."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        n = len(report.errors)
        super().__init__(
            f"input validation failed with {n} error(s):\n{report.to_text(errors_only=True)}"
        )


class PromptError(FGPDError):
    """Problem with a prompt template."""


class PromptSchemaMismatch(PromptError):
    """A custom prompt dropped or added a template variable."""


class BackendError(FGPDError):
    """The LLM backend failed after all retries."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.extra = extra


class ResponseParseError(FGPDError):
    """The model response could not be parsed into the output schema."""


class CacheError(FGPDError):
    """The local response cache is unreadable or inconsistent."""


class PartialResultsError(FGPDError):
    """Raised by `analyze(raise_on_partial=True)` when some group x goal failed."""

    def __init__(self, message: str, results: Any) -> None:
        super().__init__(message)
        self.results = results


class PrivacyWarning(UserWarning):
    """Emitted when a setting may expose personal data (e.g. pseudonymize=False)."""
