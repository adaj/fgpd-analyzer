"""Backend interface: `LLMRequest`, `LLMResponse` and the `LLMBackend` protocol.

The boundary between the requests and runs stages of the pipeline (input →
units → prompts → requests → **runs** → post-validation → ...). A backend turns
one `LLMRequest` into one `LLMResponse`; the runner handles caching, retries
and concurrency around it, so a backend stays small.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMRequest:
    """One request to the Messages API, plus bookkeeping fields that are never sent.

    Attributes
    ----------
    model : str
        Model id.
    system : list of dict
        System content blocks (with ``cache_control``).
    messages : list of dict
        One user message with the codebook, transcript and task blocks.
    output_config : dict
        ``{"format": {"type": "json_schema", "schema": ...}, "effort": ...}``.
    max_tokens : int
        Output-token budget of this attempt.
    group, goal : str
        Bookkeeping: which group and goal.
    run_idx : int
        Bookkeeping: run index (0-based).
    cache_key : str
        Bookkeeping: cache key (see `fgpd_analyzer.request.cache_key`).
    student_unit_ids, agent_unit_ids : tuple of str
        Bookkeeping: unit ids of the chat, by role.
    """

    model: str
    system: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    output_config: dict[str, Any]
    max_tokens: int
    # bookkeeping, never sent
    group: str = ""
    goal: str = ""
    run_idx: int = 0
    cache_key: str = ""
    student_unit_ids: tuple[str, ...] = ()
    agent_unit_ids: tuple[str, ...] = ()

    def api_kwargs(self) -> dict[str, Any]:
        """Return exactly the keyword arguments sent to ``messages.create``.

        ``model``, ``max_tokens``, ``system``, ``messages`` and
        ``output_config``; no sampling parameters.
        """
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": self.messages,
            "output_config": self.output_config,
        }


@dataclass
class LLMResponse:
    """The parts of one model response that the package uses.

    Attributes
    ----------
    text : str
        Concatenated text blocks (the JSON output).
    stop_reason : str
        API stop reason, e.g. ``"end_turn"``, ``"max_tokens"``, ``"refusal"``.
    usage : dict of str to int
        ``input_tokens``, ``output_tokens``, ``cache_creation_input_tokens``
        and ``cache_read_input_tokens``.
    request_id : str or None
        API request id.
    model : str
        Model that answered.
    from_cache : bool
        True when read from the local cache (not stored).
    """

    text: str
    stop_reason: str
    usage: dict[str, int] = field(default_factory=dict)
    request_id: str | None = None
    model: str = ""
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict for the cache and replay files (without `from_cache`)."""
        return {
            "text": self.text,
            "stop_reason": self.stop_reason,
            "usage": self.usage,
            "request_id": self.request_id,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, from_cache: bool = False) -> LLMResponse:
        """Build a response from the output of `to_dict`.

        Parameters
        ----------
        d : dict
            Needs ``text`` and ``stop_reason``; other keys are optional.
        from_cache : bool, default False
            Value of the `from_cache` attribute.

        Returns
        -------
        LLMResponse
        """
        return cls(
            text=d["text"],
            stop_reason=d["stop_reason"],
            usage=dict(d.get("usage") or {}),
            request_id=d.get("request_id"),
            model=d.get("model", ""),
            from_cache=from_cache,
        )


class LLMBackend(Protocol):
    """Interface that every backend implements.

    Attributes
    ----------
    name : str
        Backend name written to the run metadata. Only ``"anthropic"`` gets a
        cost in USD.

    Notes
    -----
    Optional members used when present:

    - ``cache_namespace`` (str): part of the cache key, so responses of one
      backend are never served to another. Defaults to `name`.
    - ``has(key) -> bool``: whether a response for a cache key is available
      (used by `ReplayBackend` to pick the fallback schema directly).

    `complete` should raise `BackendError` for API failures; any other
    exception also fails only its own call.
    """

    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send one request and return its response.

        Parameters
        ----------
        request : LLMRequest

        Returns
        -------
        LLMResponse

        Raises
        ------
        BackendError
            If the request fails; ``status`` should hold the HTTP code.
        """
        ...

    async def aclose(self) -> None:
        """Release resources; called once after all calls of an analysis."""
        ...
