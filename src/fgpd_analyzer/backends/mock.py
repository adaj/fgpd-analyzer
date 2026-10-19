"""Offline backends.

`MockBackend` returns deterministic, schema-valid synthetic patterns built from
the transcript, with small run-to-run variation. It exercises the whole
pipeline without network and is used by the examples and tests. Its numbers
mean nothing about the chats.

`ReplayBackend` answers from recorded responses (a JSONL file of
``{"cache_key": ..., "response": {...}}``) and fails on any request it has not
seen, so a replayed analysis is bit-identical to the recorded one.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..codebook import GOAL_INDICATORS
from ..exceptions import BackendError
from .base import LLMRequest, LLMResponse

_LINE = re.compile(r"^(u\d+) \| [^|]+ \| [^|]+ \| ([^|]+?) \| (.*)$")


def parse_transcript_block(request: LLMRequest) -> list[tuple[str, str, bool, str]]:
    """(unit_id, speaker, is_agent, text) from the transcript block of a request."""
    block = request.messages[0]["content"][1]["text"]
    body = block.split("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0]
    out = []
    for line in body.split("\n"):
        m = _LINE.match(line)
        if m:
            who = m.group(2)
            out.append(
                (m.group(1), who.replace(" [AGENT]", ""), who.endswith("[AGENT]"), m.group(3))
            )
    return out


class MockBackend:
    name = "mock"
    cache_namespace = "mock"

    def __init__(
        self,
        *,
        drop_rate: float = 0.15,
        script: Callable[[LLMRequest], LLMResponse | None] | None = None,
    ) -> None:
        self.drop_rate = drop_rate
        self.script = script
        self.calls: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if self.script is not None:
            scripted = self.script(request)
            if scripted is not None:
                return scripted
        return LLMResponse(
            text=json.dumps(self._patterns(request)),
            stop_reason="end_turn",
            usage=self._usage(request),
            request_id=f"mock-{len(self.calls)}",
            model=request.model,
        )

    def _usage(self, request: LLMRequest) -> dict[str, int]:
        chars = sum(len(b["text"]) for b in request.messages[0]["content"])
        return {
            "input_tokens": 60,
            "output_tokens": 900,
            "cache_creation_input_tokens": chars // 3 if request.run_idx == 0 else 0,
            "cache_read_input_tokens": 0 if request.run_idx == 0 else chars // 3,
        }

    def _patterns(self, request: LLMRequest) -> dict[str, Any]:
        units = parse_transcript_block(request)
        block = request.messages[0]["content"][1]["text"]
        seed = int(hashlib.sha256(f"{block}|{request.goal}".encode()).hexdigest()[:8], 16)
        rng_run = random.Random(seed + 1000 * request.run_idx)
        rng_fix = random.Random(seed)
        goal = request.goal
        students = [(i, u) for i, u in enumerate(units) if not u[2]]
        patterns = []
        k = 0
        while k + 1 < len(students):
            (ia, a), (ib, b) = students[k], students[k + 1]
            k += 3
            if a[1] == b[1] and goal in ("G2", "G4"):
                continue
            if rng_fix.random() < 0.4 or rng_run.random() < self.drop_rate:
                continue
            level = rng_fix.choice([1, 2, 2, 3, 3, 4])
            if rng_run.random() < 0.2:
                level = max(1, min(4, level + rng_run.choice([-1, 1])))
            agent_before = [u[0] for u in units[max(0, ia - 2) : ib] if u[2]]
            relational = goal in ("G2", "G4")
            patterns.append(
                {
                    "start_id": a[0],
                    "end_id": b[0],
                    "evidence_ids": [b[0]] if relational else [a[0], b[0]],
                    "antecedent_id": a[0] if relational else None,
                    "indicators": [GOAL_INDICATORS[goal][0]],
                    "justification": f"Mock pattern over {a[0]}-{b[0]}.",
                    "level": level,
                    "agent_uptake_id": agent_before[0]
                    if agent_before and rng_fix.random() < 0.5
                    else None,
                }
            )
        return {"goal": goal, "patterns": patterns}

    async def aclose(self) -> None:
        return None


class ReplayBackend:
    name = "replay"

    def __init__(
        self, records: Path | str | Iterable[dict[str, Any]], *, namespace: str = "anthropic"
    ) -> None:
        """`namespace`: the backend whose recorded responses are replayed."""
        self.cache_namespace = namespace
        if isinstance(records, (str, Path)):
            lines = Path(records).read_text(encoding="utf-8").splitlines()
            items = [json.loads(x) for x in lines if x.strip()]
        else:
            items = list(records)
        self._by_key = {r["cache_key"]: r["response"] for r in items}

    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            return LLMResponse.from_dict(self._by_key[request.cache_key])
        except KeyError:
            raise BackendError(
                f"replay: no recorded response for {request.group}/{request.goal}/run "
                f"{request.run_idx}"
            ) from None

    async def aclose(self) -> None:
        return None
