"""Consensus across repeated runs (DESIGN §5). Deterministic.

The consensus stage of the pipeline (input → units → prompts → requests →
runs → post-validation → **consensus** → influence → outputs). It also
computes the cross-run stability (Fleiss' kappa) stored in the run metadata.

Patterns of one group × goal from R successful runs are clustered by span
overlap (IoU on unit positions, Hungarian matching run by run). A cluster
found in at least `consensus_min_support` of the runs becomes a consensus
pattern; the rest are kept as unstable and not counted.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import linear_sum_assignment

from .codebook import ANTECEDENT_REQUIRED
from .postprocess import QC, Pattern, validate_patterns
from .preprocess import PreparedGroup


def iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Intersection over union of two inclusive ``(start, end)`` unit-position spans."""
    inter = min(a[1], b[1]) - max(a[0], b[0]) + 1
    if inter <= 0:
        return 0.0
    union = max(a[1], b[1]) - min(a[0], b[0]) + 1
    return inter / union


def lower_median(values: Sequence[int]) -> int:
    """Median that, for an even count, takes the lower of the two middle values."""
    s = sorted(values)
    return s[(len(s) - 1) // 2]


@dataclass
class Cluster:
    """Patterns of different runs matched as the same pattern, with their spans."""

    members: list[Pattern] = field(default_factory=list)
    spans: list[tuple[int, int]] = field(default_factory=list)

    def rep(self) -> tuple[int, int]:
        """Return the representative span: median start and median end (truncated to int)."""
        return (
            int(np.median([s for s, _ in self.spans])),
            int(np.median([e for _, e in self.spans])),
        )


@dataclass(frozen=True)
class ConsensusPattern:
    """A consensus (or unstable) pattern with its cross-run statistics.

    Attributes
    ----------
    pattern : Pattern
        Consensus values (``run_idx = -1``).
    support : float
        Share of successful runs whose patterns are in the cluster.
    n_runs_detected : int
        Number of those runs.
    level_min, level_max : int
        Lowest and highest member level.
    member_ids : tuple of str
        ``run_pattern_id`` of every member.
    """

    pattern: Pattern  # the consensus values, run_idx = -1
    support: float
    n_runs_detected: int
    level_min: int
    level_max: int
    member_ids: tuple[str, ...]


def cluster_runs(
    runs: dict[int, list[Pattern]], order: dict[str, int], match_iou: float
) -> list[Cluster]:
    """Cluster the patterns of several runs by span overlap.

    Runs are processed in order; each run's patterns are matched to the
    existing clusters by maximum total IoU (Hungarian algorithm), and a match
    needs IoU ≥ `match_iou`. Unmatched patterns start new clusters.

    Parameters
    ----------
    runs : dict of int to list of Pattern
        Validated patterns per successful run.
    order : dict of str to int
        Unit id → position.
    match_iou : float
        Minimum IoU for a match.

    Returns
    -------
    list of Cluster
    """
    clusters: list[Cluster] = []
    for r in sorted(runs):
        pats = runs[r]
        spans = [(order[p.start_id], order[p.end_id]) for p in pats]
        if not clusters:
            clusters = [Cluster([p], [s]) for p, s in zip(pats, spans, strict=True)]
            continue
        matched: set[int] = set()
        if pats:
            m = np.array([[iou(c.rep(), s) for s in spans] for c in clusters])
            rows, cols = linear_sum_assignment(-m)
            for i, j in zip(rows, cols, strict=True):
                if m[i, j] >= match_iou:
                    clusters[i].members.append(pats[j])
                    clusters[i].spans.append(spans[j])
                    matched.add(int(j))
        for j, (p, s) in enumerate(zip(pats, spans, strict=True)):
            if j not in matched:
                clusters.append(Cluster([p], [s]))
    return clusters


def _majority_id(values: list[str | None], order: dict[str, int], strict_half: bool) -> str | None:
    """Return the most cited id (earliest on ties); with `strict_half`, only if > half."""
    n = len(values)
    c = Counter(v for v in values if v is not None)
    if not c:
        return None
    best = max(c.values())
    if strict_half and best * 2 <= n:
        return None
    winners = sorted((v for v, k in c.items() if k == best), key=lambda v: order[v])
    return winners[0]


def merge_cluster(cl: Cluster, order: dict[str, int], group: str, goal: str) -> Pattern:
    """Build the consensus values of one cluster.

    Level and span = lower medians; evidence = units cited by at least half of
    the members, the span widened to cover them; indicators = those cited by
    at least half (else the most cited); antecedent = most cited (G2, G4) or
    strict majority (G1, G3); agent citation = strict majority; justification
    of the member at the consensus level that best overlaps the span.

    Parameters
    ----------
    cl : Cluster
    order : dict of str to int
        Unit id → position.
    group, goal : str

    Returns
    -------
    Pattern
        With ``run_idx = -1`` and the union of the members' flags.
    """
    mem = cl.members
    n = len(mem)
    level = lower_median([p.level for p in mem])
    start_o = lower_median([s for s, _ in cl.spans])
    end_o = lower_median([e for _, e in cl.spans])
    by_order = {v: k for k, v in order.items()}
    start, end = by_order[start_o], by_order[max(end_o, start_o)]
    ev_count = Counter(e for p in mem for e in p.evidence_ids)
    evidence = [e for e, k in ev_count.items() if 2 * k >= n] or list(ev_count)
    # With an even number of members the lower-median span can end before shared
    # evidence; the span is widened to cover it so no supported evidence is lost.
    ev_orders = [order[e] for e in evidence]
    start_o, end_o = min(start_o, *ev_orders), max(end_o, *ev_orders)
    ind_count = Counter(i for p in mem for i in p.indicators)
    indicators = sorted(i for i, k in ind_count.items() if 2 * k >= n)
    if not indicators:
        top = max(ind_count.values())
        indicators = sorted(i for i, k in ind_count.items() if k == top)[:1]
    # Required antecedents (G2, G4) take the most cited one; optional ones need a
    # strict majority of members, so one citation out of three does not win.
    antecedent = _majority_id(
        [p.antecedent_id for p in mem], order, strict_half=goal not in ANTECEDENT_REQUIRED
    )
    uptake = _majority_id([p.agent_uptake_id for p in mem], order, strict_half=True)
    same_level = [p for p in mem if p.level == level] or mem
    span = (order[start], order[end])
    best = max(
        same_level, key=lambda p: (iou((order[p.start_id], order[p.end_id]), span), -p.run_idx)
    )
    flags = tuple(sorted({f for p in mem for f in p.qc_flags}))
    return Pattern(
        group=group,
        goal=goal,
        run_idx=-1,
        start_id=start,
        end_id=end,
        evidence_ids=tuple(sorted(evidence, key=lambda e: order[e])),
        antecedent_id=antecedent,
        indicators=tuple(indicators),
        level=level,
        agent_uptake_id=uptake,
        justification=best.justification,
        qc_flags=flags,
        source=mem[0].source,
    )


@dataclass
class ConsensusResult:
    """Consensus and unstable patterns of one group × goal, with QC counts."""

    consensus: list[ConsensusPattern]
    unstable: list[ConsensusPattern]
    qc: QC = field(default_factory=QC)


def build_consensus(
    runs: dict[int, list[Pattern]],
    pg: PreparedGroup,
    goal: str,
    match_iou: float,
    min_support: float,
) -> ConsensusResult:
    """Build the consensus patterns of one group × goal.

    Parameters
    ----------
    runs : dict of int to list of Pattern
        Validated patterns per successful run (failed runs absent).
    pg : PreparedGroup
    goal : str
    match_iou : float
        Minimum IoU to match patterns across runs.
    min_support : float
        Minimum share of successful runs for a consensus pattern.

    Returns
    -------
    ConsensusResult
        Consensus patterns are re-validated with the same rules as single
        runs; overlaps created by the medians are merged (flag
        ``consensus_merge``). A supported cluster that fails the rules is kept
        as unstable (``dropped_in_consensus``). With one successful run every
        pattern is flagged ``no_dispersion``.
    """
    order = {u.unit_id: u.order for u in pg.units}
    n_ok = len(runs)
    if n_ok == 0:
        return ConsensusResult([], [])
    clusters = cluster_runs(runs, order, match_iou)
    keep: list[ConsensusPattern] = []
    unstable: list[ConsensusPattern] = []
    for cl in clusters:
        merged = merge_cluster(cl, order, pg.group, goal)
        cp = ConsensusPattern(
            pattern=merged,
            support=len(cl.members) / n_ok,
            n_runs_detected=len(cl.members),
            level_min=min(p.level for p in cl.members),
            level_max=max(p.level for p in cl.members),
            member_ids=tuple(p.run_pattern_id for p in cl.members),
        )
        (keep if cp.support >= min_support else unstable).append(cp)

    # Re-validate consensus patterns: the same structural rules, and merging of
    # consensus spans that overlap after taking medians (flag consensus_merge).
    raw = [_as_raw(cp.pattern) for cp in keep]
    qc = QC()
    validated, qc = validate_patterns(
        raw, pg, goal, -1, source=keep[0].pattern.source if keep else "model", qc=qc
    )
    used: set[int] = set()
    final: list[ConsensusPattern] = []
    for vp in validated:
        span = (order[vp.start_id], order[vp.end_id])
        parts = [
            cp
            for cp in keep
            if iou((order[cp.pattern.start_id], order[cp.pattern.end_id]), span) > 0
            and order[cp.pattern.start_id] >= span[0]
            and order[cp.pattern.end_id] <= span[1]
        ]
        parts = parts or [
            max(
                keep,
                key=lambda cp: iou((order[cp.pattern.start_id], order[cp.pattern.end_id]), span),
            )
        ]
        used.update(id(cp) for cp in parts)
        flags = set(vp.qc_flags) | {f for cp in parts for f in cp.pattern.qc_flags}
        if n_ok == 1:
            flags.add("no_dispersion")
        if "overlap_merged" in vp.qc_flags and len(parts) > 1:
            flags.discard("overlap_merged")
            flags.add("consensus_merge")
        final.append(
            ConsensusPattern(
                pattern=replace(vp, qc_flags=tuple(sorted(flags))),
                support=max(cp.support for cp in parts),
                n_runs_detected=max(cp.n_runs_detected for cp in parts),
                level_min=min(cp.level_min for cp in parts),
                level_max=max(cp.level_max for cp in parts),
                member_ids=tuple(m for cp in parts for m in cp.member_ids),
            )
        )
    # A supported cluster that fails the rules once merged is kept as unstable, flagged.
    for cp in keep:
        if id(cp) not in used:
            qc.counts["dropped_in_consensus"] += 1
            unstable.append(
                replace(
                    cp,
                    pattern=replace(
                        cp.pattern, qc_flags=(*cp.pattern.qc_flags, "dropped_in_consensus")
                    ),
                )
            )
    return ConsensusResult(final, unstable, qc)


def _as_raw(p: Pattern) -> dict[str, object]:
    """Convert a pattern back to the raw dict shape, for re-validation."""
    return {
        "start_id": p.start_id,
        "end_id": p.end_id,
        "evidence_ids": list(p.evidence_ids),
        "antecedent_id": p.antecedent_id,
        "indicators": list(p.indicators),
        "justification": p.justification,
        "level": p.level,
        "agent_uptake_id": p.agent_uptake_id,
    }


def fleiss_kappa(table: np.ndarray) -> float | None:
    """Compute Fleiss' kappa of an items × categories count table.

    Parameters
    ----------
    table : numpy.ndarray
        Counts per item and category; every row must sum to the same number
        of raters (≥ 2).

    Returns
    -------
    float or None
        None when undefined: empty table, unequal or fewer than two raters,
        or every rating in one category.
    """
    if table.size == 0:
        return None
    n = table.sum(axis=1)
    if len(set(n.tolist())) != 1 or n[0] < 2:
        return None
    n_raters = n[0]
    p_j = table.sum(axis=0) / table.sum()
    p_i = ((table**2).sum(axis=1) - n_raters) / (n_raters * (n_raters - 1))
    p_bar, p_e = p_i.mean(), (p_j**2).sum()
    if p_e >= 1:  # every rating in one category: kappa is undefined
        return None
    return float((p_bar - p_e) / (1 - p_e))


def membership_table(runs: dict[int, list[Pattern]], pg: PreparedGroup) -> np.ndarray:
    """Count, per student unit, the runs that use it as evidence of some pattern.

    Returns
    -------
    numpy.ndarray
        One row per student unit, columns ``(not evidence, evidence)``
        counted over runs; input of `fleiss_kappa`.
    """
    rows = []
    for uid in pg.student_unit_ids:
        k = sum(any(uid in p.evidence_ids for p in pats) for pats in runs.values())
        rows.append([len(runs) - k, k])
    return np.array(rows, dtype=float)
