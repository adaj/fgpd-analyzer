"""Backend interface. A backend turns one `LLMRequest` into one `LLMResponse`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMRequest:
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
        """Exactly what is sent to the Messages API. No sampling parameters."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": self.messages,
            "output_config": self.output_config,
        }


@dataclass
class LLMResponse:
    text: str
    stop_reason: str
    usage: dict[str, int] = field(default_factory=dict)
    request_id: str | None = None
    model: str = ""
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "stop_reason": self.stop_reason,
            "usage": self.usage,
            "request_id": self.request_id,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, from_cache: bool = False) -> LLMResponse:
        return cls(
            text=d["text"],
            stop_reason=d["stop_reason"],
            usage=dict(d.get("usage") or {}),
            request_id=d.get("request_id"),
            model=d.get("model", ""),
            from_cache=from_cache,
        )


class LLMBackend(Protocol):
    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def aclose(self) -> None: ...
