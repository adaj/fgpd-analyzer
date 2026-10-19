"""Async fan-out of the group × goal × run calls (DESIGN §4, §8.3 retries).

Transport errors (429/529/5xx/timeouts) are retried by the SDK. Here:
- `max_tokens`: one retry with twice the budget (capped at 32k), then `failed`;
- `refusal`: one fresh retry, then `refused` (never imputed as zero);
- unparseable output or wrong goal: one fresh retry, then `failed`.
Only successful responses are cached.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from ._logging import get_logger
from .backends.base import LLMBackend, LLMResponse
from .cache import ResponseCache
from .config import AnalyzerConfig
from .exceptions import BackendError, ResponseParseError
from .postprocess import QC, Pattern, parse_response, validate_patterns
from .preprocess import PreparedGroup
from .prompts.registry import PromptRegistry
from .request import ChatPayload, build_request

log = get_logger(__name__)
MAX_TOKENS_CAP = 32000
CallStatus = Literal["ok", "failed", "refused"]


@dataclass
class CallResult:
    group: str
    goal: str
    run_idx: int
    status: CallStatus
    patterns: list[Pattern] = field(default_factory=list)
    qc: QC = field(default_factory=QC)
    usage: list[dict[str, int]] = field(default_factory=list)  # one per API attempt
    request_ids: list[str] = field(default_factory=list)
    attempts: int = 0
    from_cache: bool = False
    error: str = ""
    cache_key: str = ""


class RateLimiter:
    """At most `rpm` request starts per rolling minute."""

    def __init__(self, rpm: int | None) -> None:
        self.rpm = rpm
        self._starts: list[float] = []
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self.rpm:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                self._starts = [t for t in self._starts if now - t < 60]
                if len(self._starts) < self.rpm:
                    self._starts.append(now)
                    return
                await asyncio.sleep(60 - (now - self._starts[0]) + 0.01)


@dataclass(frozen=True)
class Job:
    pg: PreparedGroup
    payload: ChatPayload
    goal: str
    run_idx: int


async def _one(job: Job, backend: LLMBackend, cache: ResponseCache | None, config: AnalyzerConfig,
               registry: PromptRegistry, prompt_hash: str, sem: asyncio.Semaphore,
               limiter: RateLimiter) -> CallResult:
    res = CallResult(job.pg.group, job.goal, job.run_idx, "failed")
    mt = config.max_tokens
    retried = {"max_tokens": False, "refusal": False, "parse": False}
    while True:
        req = build_request(job.payload, job.pg, registry, config, prompt_hash, job.goal,
                            job.run_idx, max_tokens=mt)
        res.cache_key = req.cache_key
        resp: LLMResponse | None = None
        if cache is not None and config.cache == "use":
            resp = cache.get(req.cache_key)
        if resp is None:
            async with sem:
                await limiter.wait()
                try:
                    resp = await backend.complete(req)
                except BackendError as e:
                    res.error = f"backend: {e}"
                    log.warning("call failed group=%s goal=%s run=%d status=%s",
                                job.pg.group, job.goal, job.run_idx, e.status)
                    return res
            res.attempts += 1
            res.usage.append(resp.usage)
            if resp.request_id:
                res.request_ids.append(resp.request_id)
        else:
            res.from_cache = True

        if resp.stop_reason == "max_tokens":
            if not retried["max_tokens"] and mt < MAX_TOKENS_CAP:
                retried["max_tokens"] = True
                mt = min(2 * mt, MAX_TOKENS_CAP)
                continue
            res.error = "max_tokens"
            return res
        if resp.stop_reason == "refusal":
            if not retried["refusal"]:
                retried["refusal"] = True
                continue
            res.status, res.error = "refused", "refusal"
            return res
        try:
            raw = parse_response(resp.text, job.goal)
        except ResponseParseError as e:
            if not retried["parse"] and not resp.from_cache:
                retried["parse"] = True
                continue
            res.error = f"parse: {e}"
            return res
        res.patterns, res.qc = validate_patterns(raw, job.pg, job.goal, job.run_idx)
        res.status = "ok"
        if cache is not None and config.cache != "off" and not resp.from_cache:
            cache.put(req.cache_key, resp)
        return res


async def run_jobs(jobs: Sequence[Job], backend: LLMBackend, cache: ResponseCache | None,
                   config: AnalyzerConfig, registry: PromptRegistry,
                   prompt_hash: str) -> list[CallResult]:
    sem = asyncio.Semaphore(config.max_concurrency)
    limiter = RateLimiter(config.requests_per_minute)
    tasks = [_one(j, backend, cache, config, registry, prompt_hash, sem, limiter) for j in jobs]
    return list(await asyncio.gather(*tasks))
