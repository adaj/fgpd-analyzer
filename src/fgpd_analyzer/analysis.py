"""Orchestration of one analysis: requests → runs → consensus → results."""

from __future__ import annotations

import asyncio
import hashlib
import math
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeVar

import pandas as pd

from . import codebook as cb
from .backends.base import LLMBackend
from .backends.pricing import PRICE_TABLE_VERSION, cost_usd
from .cache import ResponseCache
from .config import AnalyzerConfig
from .consensus import ConsensusPattern, build_consensus, fleiss_kappa, membership_table
from .pipeline import PreparedInput
from .postprocess import Pattern
from .preprocess import PreparedGroup
from .prompts.registry import PromptRegistry
from .request import ChatPayload, build_payload
from .results import FGPDResults
from .runner import CallResult, Job, run_jobs
from .schemas import SCHEMA_VERSION

T = TypeVar("T")

# Assumptions for the cost estimate (replaced by measured usage after a run).
EST_OUTPUT_TOKENS = (1500, 2500, 4000)  # low, mid, high per call (mostly thinking)
EST_TAIL_TOKENS = 120
CHARS_PER_TOKEN = 3.2


def run_coro(coro: Any) -> Any:
    """asyncio.run that also works inside a running loop (e.g. Jupyter)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["v"]


@dataclass
class CostEstimate:
    per_group: pd.DataFrame
    n_calls: int
    usd_low: float
    usd_mid: float
    usd_high: float
    model: str
    price_table_version: str = PRICE_TABLE_VERSION
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"{self.n_calls} calls with {self.model}: about ${self.usd_mid:.3f} "
            f"(range ${self.usd_low:.3f}–${self.usd_high:.3f})"
        )


def estimate(
    prep: PreparedInput, registry: PromptRegistry, config: AnalyzerConfig, groups: list[str]
) -> CostEstimate:
    prefix_chars = len(cb.render_prompt(registry, "system", config)) + len(
        cb.render_codebook_block(registry, config)
    )
    rows = []
    totals = [0.0, 0.0, 0.0]
    n_calls = 0
    for g in groups:
        pg = prep.groups[g]
        payload = build_payload(pg, registry, config)
        prefix = int((prefix_chars + len(payload.transcript_block)) / CHARS_PER_TOKEN)
        calls = len(config.goals) * config.n_runs
        n_calls += calls
        costs = []
        for out in EST_OUTPUT_TOKENS:
            usage_first = {
                "input_tokens": EST_TAIL_TOKENS,
                "cache_creation_input_tokens": prefix,
                "cache_read_input_tokens": 0,
                "output_tokens": out,
            }
            usage_rest = {
                "input_tokens": EST_TAIL_TOKENS,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": prefix,
                "output_tokens": out,
            }
            c1 = cost_usd(config.model, usage_first)
            c2 = cost_usd(config.model, usage_rest)
            costs.append(math.nan if c1 is None or c2 is None else c1 + (calls - 1) * c2)
        totals = [a + b for a, b in zip(totals, costs, strict=True)]
        rows.append(
            {
                "group": g,
                "prompt_tokens": prefix + EST_TAIL_TOKENS,
                "calls": calls,
                "usd_low": costs[0],
                "usd_mid": costs[1],
                "usd_high": costs[2],
            }
        )
    notes = [
        f"output assumed {EST_OUTPUT_TOKENS[0]}–{EST_OUTPUT_TOKENS[2]} tokens per call",
        "one cache write per chat, cache reads for the other calls",
    ]
    return CostEstimate(
        pd.DataFrame(rows),
        n_calls,
        totals[0],
        totals[1],
        totals[2],
        model=config.model,
        notes=notes,
    )


def _units_table(pg: PreparedGroup) -> pd.DataFrame:
    msgs = pg.messages.set_index("message_id")
    t0 = pg.messages["timestamp"].iloc[0]
    rows = []
    for u in pg.units:
        rows.append(
            {
                "group": pg.group,
                "unit_id": u.unit_id,
                "order": u.order,
                "speaker": u.speaker,
                "is_agent": u.is_agent,
                "t_start": u.t_start,
                "t_end": u.t_end,
                "t_start_min": round((u.t_start - t0).total_seconds() / 60, 4),
                "t_end_min": round((u.t_end - t0).total_seconds() / 60, 4),
                "message_ids": tuple(u.message_ids),
                "n_messages": len(u.message_ids),
            }
        )
    del msgs
    return pd.DataFrame(rows)


def _pattern_rows(pats: list[Pattern]) -> list[dict[str, Any]]:
    return [
        {
            "run_pattern_id": p.run_pattern_id,
            "group": p.group,
            "goal": p.goal,
            "run_idx": p.run_idx,
            "k": p.k,
            "start_id": p.start_id,
            "end_id": p.end_id,
            "evidence_ids": p.evidence_ids,
            "antecedent_id": p.antecedent_id,
            "indicators": p.indicators,
            "level": p.level,
            "agent_uptake_id": p.agent_uptake_id,
            "justification": p.justification,
            "qc_flags": p.qc_flags,
            "source": p.source,
        }
        for p in pats
    ]


def _consensus_rows(cps: list[ConsensusPattern], version: str) -> list[dict[str, Any]]:
    rows = []
    for k, cp in enumerate(cps):
        p = cp.pattern
        rows.append(
            {
                "group": p.group,
                "goal": p.goal,
                "k": k,
                "start_id": p.start_id,
                "end_id": p.end_id,
                "evidence_ids": p.evidence_ids,
                "antecedent_id": p.antecedent_id,
                "indicators": p.indicators,
                "level": p.level,
                "level_min": cp.level_min,
                "level_max": cp.level_max,
                "agent_uptake_id": p.agent_uptake_id,
                "justification": p.justification,
                "qc_flags": p.qc_flags,
                "support": cp.support,
                "n_runs_detected": cp.n_runs_detected,
                "member_ids": cp.member_ids,
                "prompt_set_version": version,
            }
        )
    return rows


CONSENSUS_COLUMNS = [
    "group",
    "goal",
    "k",
    "start_id",
    "end_id",
    "evidence_ids",
    "antecedent_id",
    "indicators",
    "level",
    "level_min",
    "level_max",
    "agent_uptake_id",
    "justification",
    "qc_flags",
    "support",
    "n_runs_detected",
    "member_ids",
    "prompt_set_version",
]
RUN_COLUMNS = [
    "run_pattern_id",
    "group",
    "goal",
    "run_idx",
    "k",
    "start_id",
    "end_id",
    "evidence_ids",
    "antecedent_id",
    "indicators",
    "level",
    "agent_uptake_id",
    "justification",
    "qc_flags",
    "source",
]


async def run_analysis(
    prep: PreparedInput,
    groups: list[str],
    registry: PromptRegistry,
    config: AnalyzerConfig,
    backend: LLMBackend,
    cache: ResponseCache | None,
    *,
    source_sha256: str = "",
    package_version: str = "",
) -> FGPDResults:
    started = datetime.now(timezone.utc)
    prompt_hash = cb.prompt_set_hash(registry, config, SCHEMA_VERSION)
    version = registry.version()
    payloads: dict[str, ChatPayload] = {
        g: build_payload(prep.groups[g], registry, config) for g in groups
    }
    jobs = [
        Job(prep.groups[g], payloads[g], goal, r)
        for g in groups
        for goal in config.goals
        for r in range(config.n_runs)
    ]
    try:
        results: list[CallResult] = await run_jobs(
            jobs, backend, cache, config, registry, prompt_hash
        )
    finally:
        await backend.aclose()

    by_gg: dict[tuple[str, str], dict[int, list[Pattern]]] = {}
    for r in results:
        if r.status == "ok":
            by_gg.setdefault((r.group, r.goal), {})[r.run_idx] = r.patterns
    consensus: list[ConsensusPattern] = []
    unstable: list[ConsensusPattern] = []
    stability: dict[str, Any] = {}
    consensus_qc: dict[str, dict[str, int]] = {}
    for goal in config.goals:
        tables = []
        for g in groups:
            runs = by_gg.get((g, goal), {})
            res = build_consensus(
                runs, prep.groups[g], goal, config.match_iou, config.consensus_min_support
            )
            consensus += res.consensus
            unstable += res.unstable
            if res.qc.counts:
                consensus_qc[f"{g}:{goal}"] = dict(res.qc.counts)
            if len(runs) == config.n_runs and config.n_runs > 1:
                tables.append(membership_table(runs, prep.groups[g]))
        k = (
            fleiss_kappa(pd.concat([pd.DataFrame(t) for t in tables]).to_numpy())
            if tables
            else None
        )
        stability[goal] = {"membership_fleiss_kappa": k, "n_groups": len(tables)}

    # per-goal-group numbering of consensus patterns
    cons_rows: list[dict[str, Any]] = []
    for g, goal in sorted({(c.pattern.group, c.pattern.goal) for c in consensus}):
        cons_rows += _consensus_rows(
            [c for c in consensus if (c.pattern.group, c.pattern.goal) == (g, goal)], version
        )
    unst_rows = _consensus_rows(unstable, version)

    groups_rows = []
    for g, pg in prep.groups.items():
        groups_rows.append(
            {
                "group": g,
                "eligibility": pg.eligibility,
                "n_student_messages": pg.n_student_messages,
                "n_student_units": pg.n_student_units,
                "n_agent_messages": pg.n_agent_messages,
                "n_students": pg.n_students,
                "duration_min": pg.duration_min,
                "agent_share": pg.agent_share,
                "flagged_agent_share": pg.flagged_agent_share,
                "first_ts": pg.messages["timestamp"].iloc[0],
                "chat_hash": payloads[g].chat_hash if g in payloads else None,
            }
        )
    calls_rows = [
        {
            "group": r.group,
            "goal": r.goal,
            "run_idx": r.run_idx,
            "status": r.status,
            "error": r.error,
            "attempts": r.attempts,
            "from_cache": r.from_cache,
            "n_patterns": len(r.patterns),
        }
        for r in results
    ]

    usage_rows = []
    total_cost = 0.0
    cost_known = True
    qc_counts: dict[str, dict[str, int]] = {}
    for r in results:
        for u in r.usage:
            c = cost_usd(config.model, u)
            if c is None:
                cost_known = False
            else:
                total_cost += c
            usage_rows.append({"group": r.group, "goal": r.goal, "run_idx": r.run_idx, **u})
        key = f"{r.group}:{r.goal}:{r.run_idx}"
        if r.qc.counts:
            qc_counts[key] = dict(r.qc.counts)

    meta: dict[str, Any] = {
        "package_version": package_version,
        "schema_version": SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "config": config.redacted(),
        "config_fingerprint": config.fingerprint(),
        "model": config.model,
        "effort": config.effort,
        "backend": getattr(backend, "name", type(backend).__name__),
        "prompts": [
            {"name": n, "sha256": registry.get(n).sha256, "source": registry.get(n).source}
            for n in registry.names()
        ],
        "prompt_set_version": version,
        "prompt_set_hash": prompt_hash,
        "chat_hashes": {g: p.chat_hash for g, p in payloads.items()},
        "schema_hashes": {g: p.schema_hash for g, p in payloads.items()},
        "input_sha256": source_sha256,
        "usage": usage_rows,
        "usage_total": {
            k: int(sum(u.get(k, 0) for u in usage_rows))
            for k in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "output_tokens",
            )
        },
        "request_ids": [rid for r in results for rid in r.request_ids],
        "cache_keys": [r.cache_key for r in results if r.status == "ok"],
        # only real API usage has a price (mock usage is synthetic)
        "cost_usd": round(total_cost, 6)
        if cost_known and getattr(backend, "name", "") == "anthropic"
        else None,
        "price_table_version": PRICE_TABLE_VERSION,
        "qc": qc_counts,
        "consensus_qc": consensus_qc,
        "no_dispersion": config.n_runs == 1,
        "stability": stability,
        "groups": {g: pg.eligibility for g, pg in prep.groups.items()},
        "n_calls": len(results),
        "n_api_calls": int(sum(r.attempts for r in results)),
        "n_cached": int(sum(r.from_cache for r in results)),
    }
    validation = {
        "issues": [i.__dict__ for i in prep.report.issues],
        "info": prep.report.info.to_dict(orient="records"),
    }
    units = pd.concat([_units_table(pg) for pg in prep.groups.values()], ignore_index=True)
    results_obj = FGPDResults(
        units=units,
        groups=pd.DataFrame(groups_rows),
        calls=pd.DataFrame(
            calls_rows,
            columns=[
                "group",
                "goal",
                "run_idx",
                "status",
                "error",
                "attempts",
                "from_cache",
                "n_patterns",
            ],
        ),
        consensus=pd.DataFrame(cons_rows, columns=CONSENSUS_COLUMNS),
        unstable_patterns=pd.DataFrame(unst_rows, columns=CONSENSUS_COLUMNS),
        run_patterns=pd.DataFrame(
            _pattern_rows([p for r in results for p in r.patterns]), columns=RUN_COLUMNS
        ),
        messages=prep.messages(),
        run_metadata=meta,
        validation=validation,
        influence_lag=config.influence_lag,
        influence_rule=config.influence_rule,
        goals=config.goals,
    )
    from .results import group_goal_status

    st = group_goal_status(results_obj.calls, results_obj.groups, config.goals)
    meta["groups"] = {
        g: {"eligibility": pg.eligibility, "status": {goal: st[(g, goal)] for goal in config.goals}}
        for g, pg in prep.groups.items()
    }
    results_obj._pseudonym_map = {
        g: dict(pg.pseudonymizer.mapping) for g, pg in prep.groups.items()
    }
    return results_obj


def sha256_of_source(source: Any) -> str:
    try:
        from pathlib import Path

        p = Path(source)
        if p.exists():
            return hashlib.sha256(p.read_bytes()).hexdigest()
    except TypeError:
        pass
    if isinstance(source, pd.DataFrame):
        return hashlib.sha256(
            pd.util.hash_pandas_object(source, index=False).values.tobytes()
        ).hexdigest()
    return ""
