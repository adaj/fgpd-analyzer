"""Request construction (DESIGN §4): one request per group × goal × run.

The requests stage of the pipeline (input → units → prompts → **requests** →
runs → post-validation → ...). It assembles the rendered prompts, the
transcript and the output schema into an `LLMRequest` and computes its cache
key.

Layout, with cache breakpoints after the system block, the codebook block and
the transcript block, so that the 4 goals × n_runs calls of a chat share the
prefix and only the short task tail differs::

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
    """Return the one-line summary appended to the transcript block.

    Number of units (student and agent), student pseudonyms, duration in
    minutes and chat language.
    """
    lang = cb.language_name(config.chat_language) or "not specified"
    students = ", ".join(dict.fromkeys(u.speaker for u in pg.units if not u.is_agent))
    return (
        f"Chat facts: {len(pg.units)} units ({pg.n_student_units} student units, "
        f"{len(pg.agent_unit_ids)} agent units); students: {students}; "
        f"duration {pg.duration_min:.0f} minutes; language: {lang}."
    )


@dataclass(frozen=True)
class ChatPayload:
    """Everything that is identical across the goal and run calls of one chat.

    Attributes
    ----------
    group : str
        Group id.
    transcript : str
        Rendered transcript.
    chat_hash : str
        SHA-256 of `transcript`.
    schema : dict
        Output JSON schema of this chat.
    schema_hash : str
        SHA-256 of `schema`.
    system_text : str
        Rendered system prompt (cached block 1).
    codebook_text : str
        Rendered codebook block (cached block 2).
    transcript_block : str
        ``<transcript>`` block plus chat facts (cached block 3).
    """

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
    """Build the per-chat part of the requests.

    Parameters
    ----------
    pg : PreparedGroup
    registry : PromptRegistry
    config : AnalyzerConfig
    string_ids : bool, default False
        Use the fallback schema without id enums (DESIGN §8.2), sent after
        the API rejected the enum schema.

    Returns
    -------
    ChatPayload
    """
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
    """Return the cache key of one request.

    Parameters
    ----------
    api_kwargs : dict
        The request as sent, with the configured (not the escalated)
        ``max_tokens``, plus the prompt-set hash and schema version.
    namespace : str
        Backend namespace (``"anthropic"``, ``"mock"``).
    run_idx : int
        Run index (0-based).

    Returns
    -------
    str
        SHA-256 hex digest. Anything that changes what is sent changes the
        key by construction.
    """
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
    """Build the request of one group × goal × run.

    Parameters
    ----------
    payload : ChatPayload
        Per-chat blocks and schema.
    pg : PreparedGroup
    registry : PromptRegistry
    config : AnalyzerConfig
    prompt_hash : str
        Prompt-set hash (part of the cache key).
    goal : str
        ``"G1"`` … ``"G4"``.
    run_idx : int
        Run index (0-based).
    max_tokens : int, optional
        Budget for this attempt; defaults to ``config.max_tokens``. The cache
        key always uses ``config.max_tokens``.
    namespace : str, default "anthropic"
        Backend namespace for the cache key.

    Returns
    -------
    LLMRequest
        With ``cache_key`` set.
    """
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
