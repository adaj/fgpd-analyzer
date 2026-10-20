"""Anthropic Messages API backend, the default backend of real analyses.

Runs stage of the pipeline (input → units → prompts → requests → **runs** →
post-validation → ...): it sends one `LLMRequest` to the Messages API with the
official asynchronous SDK and returns the text, stop reason and token usage.

Transport retries (429, 529, 5xx, timeouts, with `retry-after`) are delegated to
the official SDK (`max_retries`). Content-level retries (max_tokens, refusal,
unparseable output) are handled by the runner.
"""

from __future__ import annotations

from typing import Any

from ..exceptions import BackendError, ConfigError
from .base import LLMRequest, LLMResponse


def _usage(u: Any) -> dict[str, int]:
    """Convert the SDK usage object to a dict of the four token counts (missing → 0)."""
    out: dict[str, int] = {}
    for k in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        v = getattr(u, k, None)
        out[k] = int(v or 0)
    return out


class AnthropicBackend:
    """Backend that calls the Anthropic Messages API.

    Parameters
    ----------
    api_key : str, optional
        API key; None reads ``$ANTHROPIC_API_KEY``.
    timeout_s : float, default 300.0
        Timeout per request, in seconds. An explicit timeout also lifts the
        SDK's refusal of non-streaming calls with a large ``max_tokens``.
    max_retries : int, default 5
        Transport retries done by the SDK (429, 529, 5xx, timeouts, honouring
        ``retry-after``).
    client : object, optional
        A ready ``anthropic.AsyncAnthropic``-like client, e.g. for tests;
        `api_key` and `max_retries` are then ignored.

    Attributes
    ----------
    name : str
        ``"anthropic"``.
    cache_namespace : str
        ``"anthropic"``.

    Raises
    ------
    ConfigError
        If the ``anthropic`` package is missing or no API key is found.

    Examples
    --------
    >>> from fgpd_analyzer.backends.anthropic import AnthropicBackend
    >>> fa = FGPDAnalyzer(backend=AnthropicBackend(timeout_s=600))  # doctest: +SKIP
    """

    name = "anthropic"
    cache_namespace = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout_s: float = 300.0,
        max_retries: int = 5,
        client: Any = None,
    ) -> None:
        # An explicit per-request timeout also disables the SDK's refusal of
        # non-streaming calls with large max_tokens (our calls are short).
        self.timeout_s = timeout_s
        if client is not None:
            self._client = client
            return
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ConfigError("the anthropic package is required: pip install anthropic") from e
        try:
            self._client = anthropic.AsyncAnthropic(
                api_key=api_key, timeout=timeout_s, max_retries=max_retries
            )
        except anthropic.AnthropicError as e:
            raise ConfigError("no API key: pass api_key=... or set ANTHROPIC_API_KEY") from e

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send one request and return its response.

        Parameters
        ----------
        request : LLMRequest

        Returns
        -------
        LLMResponse
            Text blocks concatenated; usage as four token counts.

        Raises
        ------
        BackendError
            For an API status error (``status`` set), a connection error after
            the SDK retries, any other SDK error, or a malformed response.
        """
        import anthropic

        try:
            msg = await self._client.messages.create(**request.api_kwargs(), timeout=self.timeout_s)
        except anthropic.APIStatusError as e:
            raise BackendError(
                f"API error {e.status_code}",
                status=e.status_code,
                request_id=getattr(e, "request_id", None),
            ) from e
        except anthropic.APIConnectionError as e:
            raise BackendError("connection error after retries") from e
        except anthropic.APIError as e:
            raise BackendError(f"API error: {type(e).__name__}") from e
        try:
            text = "".join(
                getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text"
            )
            return LLMResponse(
                text=text,
                stop_reason=str(msg.stop_reason),
                usage=_usage(msg.usage),
                request_id=getattr(msg, "_request_id", None) or getattr(msg, "id", None),
                model=str(msg.model),
            )
        except (AttributeError, TypeError) as e:
            raise BackendError(f"malformed API response ({type(e).__name__})") from e

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()
