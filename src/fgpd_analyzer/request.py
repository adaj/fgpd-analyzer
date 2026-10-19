"""Request construction (DESIGN §4). One request per group × goal × run.

Layout, with cache breakpoints after the system block, the codebook block and
the transcript block, so that the 4 goals × n_runs calls of a chat share the
prefix and only the short task tail differs:

    system  : system prompt                                    [cache 1]
    user[0] : codebook (general rules, conventions, G1..G4)     [cache 2]
    user[1] : <transcript> … </transcript> + chat facts         [cache 3]
    user[2] : task instruction for goal Gk
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from . import codebook as cb
from .backends.base import LLMRequest
from .config import AnalyzerConfig
from .preprocess import PreparedGroup
from .prompts.registry import PromptRegistry
from .render import chat_hash, render_transcript
from .schemas import SCHEMA_VERSION, build_schema, schema_hash

_CACHE = {"type": "ephemeral"}


def chat_facts(pg: PreparedGroup, config: AnalyzerConfig) -> str:
    lang = cb.language_name(config.chat_language) or "not specified"
    students = ", ".join(dict.fromkeys(u.speaker for u in pg.units if not u.is_agent))
    return (
        f"Chat facts: {len(pg.units)} units ({pg.n_student_units} student units, "
        f"{len(pg.agent_unit_ids)} agent units); students: {students}; "
        f"duration {pg.duration_min:.0f} minutes; language: {lang}."
    )


@dataclass(frozen=True)
class ChatPayload:
    """Everything that is identical across the goal calls of one chat."""

    group: str
    transcript: str
    chat_hash: str
    schema: dict[str, Any]
    schema_hash: str
    system_text: str
    codebook_text: str
    transcript_block: str


def build_payload(
    pg: PreparedGroup,
    registry: PromptRegistry,
    config: AnalyzerConfig,
    *,
    string_ids: bool = False,
) -> ChatPayload:
    """`string_ids=True` is the fallback schema without id enums (DESIGN §8.2)."""
    transcript = render_transcript(pg)
    schema = build_schema(pg, max_enum_ids=0) if string_ids else build_schema(pg)
    block = f"<transcript>\n{transcript}\n</transcript>\n\n{chat_facts(pg, config)}"
    return ChatPayload(
        group=pg.group,
        transcript=transcript,
        chat_hash=chat_hash(transcript),
        schema=schema,
        schema_hash=schema_hash(schema),
        system_text=cb.render_prompt(registry, "system", config),
        codebook_text=cb.render_codebook_block(registry, config),
        transcript_block=block,
    )


def cache_key(api_kwargs: dict[str, Any], namespace: str, run_idx: int) -> str:
    """Hash of the exact request (with the configured, not the escalated,
    max_tokens), the backend namespace and the run index. Anything that changes
    what is sent changes the key by construction."""
    payload = {"request": api_kwargs, "namespace": namespace, "run_idx": run_idx}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def build_request(
    payload: ChatPayload,
    pg: PreparedGroup,
    registry: PromptRegistry,
    config: AnalyzerConfig,
    prompt_hash: str,
    goal: str,
    run_idx: int,
    max_tokens: int | None = None,
    namespace: str = "anthropic",
) -> LLMRequest:
    mt = max_tokens or config.max_tokens
    task = cb.render_prompt(registry, "task_detect", config, goal=goal)
    req = LLMRequest(
        model=config.model,
        system=[{"type": "text", "text": payload.system_text, "cache_control": _CACHE}],
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": payload.codebook_text, "cache_control": _CACHE},
                    {"type": "text", "text": payload.transcript_block, "cache_control": _CACHE},
                    {"type": "text", "text": task},
                ],
            }
        ],
        output_config={
            "format": {"type": "json_schema", "schema": payload.schema},
            "effort": config.effort,
        },
        max_tokens=mt,
        group=pg.group,
        goal=goal,
        run_idx=run_idx,
        student_unit_ids=tuple(pg.student_unit_ids),
        agent_unit_ids=tuple(pg.agent_unit_ids),
    )
    base = {**req.api_kwargs(), "max_tokens": config.max_tokens}
    key_extra = {"prompt_set_hash": prompt_hash, "schema_version": SCHEMA_VERSION}
    return replace(req, cache_key=cache_key({**base, **key_extra}, namespace, run_idx))
