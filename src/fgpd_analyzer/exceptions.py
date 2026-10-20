"""Exceptions and warnings raised by fgpd-analyzer.

Every exception derives from `FGPDError`. They are raised only for problems
that make a whole call meaningless: invalid configuration, invalid input
(before any network I/O), a broken prompt or cache. A single failing
group × goal × run call never aborts a batch; it is recorded in the results
instead (``FGPDResults.calls``). The classes are used at every stage of the
pipeline, from input validation to the outputs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .validation import ValidationReport


class FGPDError(Exception):
    """Base class for every error raised by fgpd-analyzer."""


class ConfigError(FGPDError, ValueError):
    """Invalid configuration value or combination.

    Also a `ValueError`, so code that catches ``ValueError`` keeps working.
    """


class InputValidationError(FGPDError):
    """The input failed validation.

    Raised by every method that needs valid input (`FGPDAnalyzer.analyze`,
    `estimate_cost`, `preview`, `load`, ...) before any network I/O.

    Parameters
    ----------
    report : ValidationReport
        The full report; the message lists its errors.

    Attributes
    ----------
    report : ValidationReport
        Every issue found, errors and warnings.
    """

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        n = len(report.errors)
        super().__init__(
            f"input validation failed with {n} error(s):\n{report.to_text(errors_only=True)}"
        )


class PromptError(FGPDError):
    """Problem with a prompt template: unknown name, empty or invalid template."""


class PromptSchemaMismatch(PromptError):
    """A custom prompt does not use exactly the variables of the built-in prompt.

    Prevents a replacement from silently dropping, e.g., the pattern-gap rule.
    """


class BackendError(FGPDError):
    """The LLM backend failed after all transport retries.

    Parameters
    ----------
    message : str
        Description without chat content.
    status : int, optional
        HTTP status code, if any. A 400 triggers the string-id schema
        fallback once.
    request_id : str, optional
        API request id, if any.
    **extra
        Further details, kept in `extra`.

    Attributes
    ----------
    status : int or None
    request_id : str or None
    extra : dict
    """

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
    """The model response could not be parsed into the output schema.

    Raised for invalid JSON, a missing ``patterns`` list or a goal mismatch.
    The runner retries such a call once.
    """


class CacheError(FGPDError):
    """The local response cache is unreadable or inconsistent."""


class PartialResultsError(FGPDError):
    """Some group × goal × run call did not succeed.

    Raised by ``analyze(raise_on_partial=True)`` and
    ``analyze_async(raise_on_partial=True)``.

    Parameters
    ----------
    message : str
        Description.
    results : FGPDResults
        The (partial) results.

    Attributes
    ----------
    results : FGPDResults
        The results, so that the successful calls are not lost.
    """

    def __init__(self, message: str, results: Any) -> None:
        super().__init__(message)
        self.results = results


class PrivacyWarning(UserWarning):
    """Warning for a setting or action that may expose personal data.

    Emitted for ``pseudonymize=False`` and for
    ``FGPDResults.save(include_pseudonym_map=True)``.
    """
