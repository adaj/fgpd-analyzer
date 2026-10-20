"""Post-validation of one model (or human) response (DESIGN §8.3 and §3.4).

The post-validation stage of the pipeline (input → units → prompts → requests
→ runs → **post-validation** → consensus → influence → outputs). It parses the
JSON output of one call and enforces the codebook rules that code can check:
known ids and indicators, evidence inside the span, valid antecedents and
agent citations, the structural level constraints per goal, and merging of
overlapping patterns of the same goal.

Every correction is deterministic and recorded in `qc_flags`. The same checks
are reported, without being applied, by `coding-sheet check` for human codes.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from .codebook import ANTECEDENT_REQUIRED, GOAL_INDICATORS
from .exceptions import ResponseParseError
from .preprocess import PreparedGroup

#: Justifications longer than this many words are truncated (flag ``justification_truncated``).
JUSTIFICATION_TRUNCATE_WORDS = 60


@dataclass(frozen=True)
class Pattern:
    """One validated pattern of one run (or one human coder).

    Attributes
    ----------
    group, goal : str
    run_idx : int
        Run index (0-based); -1 for a consensus pattern.
    start_id, end_id : str
        First and last unit of the span.
    evidence_ids : tuple of str
        Student units that show the goal, in chat order, inside the span.
    antecedent_id : str or None
        Peer student unit taken up (required for G2 and G4); never an
        evidence unit.
    indicators : tuple of str
        Indicators of the goal.
    level : int
        1–4.
    agent_uptake_id : str or None
        Agent unit whose content the students take up, as cited (lag-free).
    justification : str
        Short pseudonymized justification.
    qc_flags : tuple of str
        Every correction applied (e.g. ``span_swapped``, ``demoted_4_to_3``).
    source : str
        ``"model"`` or ``"human"``.
    k : int
        Position among the patterns of this group × goal × run.
    """

    group: str
    goal: str
    run_idx: int
    start_id: str
    end_id: str
    evidence_ids: tuple[str, ...]
    antecedent_id: str | None
    indicators: tuple[str, ...]
    level: int
    agent_uptake_id: str | None
    justification: str
    qc_flags: tuple[str, ...] = ()
    source: str = "model"
    k: int = 0

    @property
    def run_pattern_id(self) -> str:
        """Identifier ``{group}:{goal}:{run_idx}:{k}``."""
        return f"{self.group}:{self.goal}:{self.run_idx}:{self.k}"

    @property
    def score(self) -> float:
        """Level / 4, in [0.25, 1]."""
        return self.level / 4


@dataclass
class QC:
    """Counter of quality-control events (corrections, drops, merges, fallbacks)."""

    counts: Counter[str] = field(default_factory=Counter)

    def flag(self, flags: list[str], name: str) -> None:
        """Append `name` to a pattern's `flags` and count it."""
        flags.append(name)
        self.counts[name] += 1


def parse_response(text: str, goal: str) -> list[dict[str, Any]]:
    """Parse the JSON output of one call.

    Parameters
    ----------
    text : str
        Response text.
    goal : str
        The goal that was asked for.

    Returns
    -------
    list of dict
        The raw pattern objects (non-dict items are skipped).

    Raises
    ------
    ResponseParseError
        For invalid JSON, a missing ``patterns`` list or another goal. The
        runner retries the call once.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as e:
        raise ResponseParseError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("patterns"), list):
        raise ResponseParseError("response lacks a 'patterns' list")
    if str(data.get("goal", "")).upper() != goal:
        raise ResponseParseError(f"goal mismatch: asked {goal}, got {data.get('goal')!r}")
    return [p for p in data["patterns"] if isinstance(p, dict)]


def _norm_id(v: Any) -> str | None:
    """Return the lower-case, stripped id, or None for empty values."""
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def _norm_ids(v: Any) -> list[str]:
    """Return normalized, de-duplicated ids from a list or a single value."""
    if not isinstance(v, list):
        v = [v] if v else []
    out = [x for x in (_norm_id(i) for i in v) if x]
    return list(dict.fromkeys(out))


def _level(v: Any) -> int:
    """Return the integer level of an int, a float such as 3.0 or a string; 0 if invalid."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else 0


class _Validator:
    """Per-group lookup tables plus the single-pattern rules (§8.3 steps 2-5, 7)."""

    def __init__(self, pg: PreparedGroup, goal: str) -> None:
        units = pg.unit_index
        self.pg, self.goal = pg, goal
        self.order = {uid: u.order for uid, u in units.items()}
        self.speaker = {uid: u.speaker for uid, u in units.items()}
        self.student = {uid for uid, u in units.items() if not u.is_agent}
        self.agent = {uid for uid, u in units.items() if u.is_agent}
        self.allowed = set(GOAL_INDICATORS[goal])

    def check(self, item: dict[str, Any], flags: list[str], qc: QC) -> dict[str, Any] | None:
        """Return the corrected fields, or None when the pattern must be dropped.

        Every problem is appended to `flags` and counted in `qc`.
        """
        order, speaker, goal = self.order, self.speaker, self.goal
        # 2. indicators
        inds_raw = [str(i).strip().lower() for i in (item.get("indicators") or [])]
        inds = [i for i in dict.fromkeys(inds_raw) if i in self.allowed]
        if len(inds) < len(set(inds_raw)):
            qc.flag(flags, "indicator_removed")
        if not inds:
            qc.flag(flags, "drop_no_indicators")
            return None
        level = _level(item.get("level"))
        if level not in (1, 2, 3, 4):
            qc.flag(flags, "drop_invalid_level")
            return None
        # 3. ids exist
        start, end = _norm_id(item.get("start_id")), _norm_id(item.get("end_id"))
        if start not in order or end not in order:
            qc.flag(flags, "drop_unknown_span")
            return None
        assert start is not None and end is not None
        # 4. swap
        if order[start] > order[end]:
            start, end = end, start
            qc.flag(flags, "span_swapped")
        evidence = _norm_ids(item.get("evidence_ids"))
        if any(e not in self.student for e in evidence):
            qc.flag(flags, "evidence_not_student_removed")
            evidence = [e for e in evidence if e in self.student]
        outside = [e for e in evidence if not order[start] <= order[e] <= order[end]]
        if outside:
            qc.flag(flags, "evidence_outside_span_removed")
            evidence = [e for e in evidence if e not in outside]
        antecedent = _norm_id(item.get("antecedent_id"))
        if antecedent is not None and antecedent not in self.student:
            qc.flag(flags, "antecedent_invalid_nulled")
            antecedent = None
        if antecedent is not None and antecedent in evidence:
            qc.flag(flags, "antecedent_removed_from_evidence")
            evidence = [e for e in evidence if e != antecedent]
        evidence.sort(key=lambda e: order[e])
        if not evidence:
            qc.flag(flags, "drop_no_evidence")
            return None
        last_ev = evidence[-1]
        if antecedent is not None and order[antecedent] > order[last_ev]:
            qc.flag(flags, "antecedent_after_evidence_nulled")
            antecedent = None
        if antecedent is not None and all(speaker[e] == speaker[antecedent] for e in evidence):
            # an antecedent is always a different student's unit (codebook §3)
            qc.flag(flags, "antecedent_same_speaker_nulled")
            antecedent = None
        uptake = _norm_id(item.get("agent_uptake_id"))
        if uptake is not None and (uptake not in self.agent or order[uptake] > order[last_ev]):
            qc.flag(flags, "agent_citation_invalid_nulled")
            uptake = None
        # 5. structural constraints (§3.4 as revised in docs/decisions.md D16)
        if goal in ANTECEDENT_REQUIRED and antecedent is None:
            qc.flag(flags, "drop_missing_antecedent")
            return None
        ev_speakers = {speaker[e] for e in evidence}
        new_level = level
        if goal == "G1":
            if level == 4 and len(ev_speakers) < 2:
                new_level = 3
            if new_level == 3 and len(ev_speakers) < 2 and len(evidence) < 2:
                new_level = 2
        elif goal == "G3":
            # Code cannot see whether the earlier in-span unit is a press; it only
            # checks that one exists (docs/decisions.md D27).
            if level >= 3 and len(evidence) < 2 and not order[start] < order[evidence[0]]:
                new_level = 2
        elif goal == "G4" and level >= 3:
            assert antecedent is not None
            a_spk = speaker[antecedent]
            first_op = next((i for i, e in enumerate(evidence) if speaker[e] != a_spk), None)
            returns = first_op is not None and any(
                speaker[e] == a_spk for e in evidence[first_op + 1 :]
            )
            if len(evidence) < 2 or not returns:
                new_level = 2
        if new_level != level:
            qc.flag(flags, f"demoted_{level}_to_{new_level}")
            level = new_level
        # 7. justification
        just = str(item.get("justification") or "").strip()
        words = just.split()
        if len(words) > JUSTIFICATION_TRUNCATE_WORDS:
            qc.flag(flags, "justification_truncated")
            just = " ".join(words[:JUSTIFICATION_TRUNCATE_WORDS])
        return {
            "start_id": start,
            "end_id": end,
            "evidence_ids": tuple(evidence),
            "antecedent_id": antecedent,
            "indicators": tuple(inds),
            "level": level,
            "agent_uptake_id": uptake,
            "justification": just,
        }


def _raw(p: Pattern) -> dict[str, Any]:
    """Convert a pattern back to the raw dict shape, to re-check a merged pattern."""
    return {
        "start_id": p.start_id,
        "end_id": p.end_id,
        "evidence_ids": list(p.evidence_ids),
        "antecedent_id": p.antecedent_id,
        "indicators": list(p.indicators),
        "level": p.level,
        "agent_uptake_id": p.agent_uptake_id,
        "justification": p.justification,
    }


def validate_patterns(
    raw: list[dict[str, Any]],
    pg: PreparedGroup,
    goal: str,
    run_idx: int,
    *,
    source: str = "model",
    qc: QC | None = None,
    apply: bool = True,
) -> tuple[list[Pattern], QC]:
    """Validate and correct the raw patterns of one call (DESIGN §8.3).

    Parameters
    ----------
    raw : list of dict
        Output of `parse_response`, or rows of a human coding sheet.
    pg : PreparedGroup
        The chat, for ids, roles, speakers and order.
    goal : str
        Goal of the call.
    run_idx : int
        Run index stored on the patterns.
    source : str, default "model"
        ``"model"`` or ``"human"``.
    qc : QC, optional
        Counter to update; a new one when omitted.
    apply : bool, default True
        If False (human sheets), nothing is demoted, dropped or merged: every
        pattern is returned as reported, with its problems flagged
        (overlaps flagged as ``overlap``).

    Returns
    -------
    patterns : list of Pattern
        Sorted by span and numbered (`k`). Overlapping patterns of the goal
        are merged and the merge re-checked; if the union breaks a rule, the
        stronger pattern is kept (``overlap_dropped``).
    qc : QC
    """
    qc = qc or QC()
    v = _Validator(pg, goal)
    order = v.order

    def make(fields: dict[str, Any], flags: list[str]) -> Pattern:
        return Pattern(
            group=pg.group,
            goal=goal,
            run_idx=run_idx,
            qc_flags=tuple(flags),
            source=source,
            **fields,
        )

    def as_reported(item: dict[str, Any], flags: list[str]) -> Pattern:
        start, end = _norm_id(item.get("start_id")), _norm_id(item.get("end_id"))
        return make(
            {
                "start_id": start or "",
                "end_id": end or "",
                "evidence_ids": tuple(_norm_ids(item.get("evidence_ids"))),
                "antecedent_id": _norm_id(item.get("antecedent_id")),
                "indicators": tuple(str(i) for i in (item.get("indicators") or [])),
                "level": _level(item.get("level")),
                "agent_uptake_id": _norm_id(item.get("agent_uptake_id")),
                "justification": str(item.get("justification") or ""),
            },
            flags,
        )

    out: list[Pattern] = []
    for item in raw:
        flags: list[str] = []
        fields = v.check(item, flags, qc)
        if not apply:
            out.append(as_reported(item, flags))
        elif fields is not None:
            out.append(make(fields, flags))

    if not apply:
        idx = sorted(
            (i for i, p in enumerate(out) if p.start_id in order and p.end_id in order),
            key=lambda i: (order[out[i].start_id], order[out[i].end_id]),
        )
        max_end = -1
        for i in idx:
            if order[out[i].start_id] <= max_end:
                qc.counts["overlap"] += 1
                out[i] = replace(out[i], qc_flags=(*out[i].qc_flags, "overlap"))
            max_end = max(max_end, order[out[i].end_id])
        return [replace(p, k=k) for k, p in enumerate(out)], qc

    # 6. merge same-goal overlaps (then re-check the merged pattern); 8. sort, number
    out.sort(key=lambda p: (order[p.start_id], order[p.end_id]))
    merged: list[Pattern] = []
    for p in out:
        if merged and order[p.start_id] <= order[merged[-1].end_id]:
            prev = merged.pop()
            m = merge_patterns(prev, p, order)
            flags = list(m.qc_flags)
            fields = v.check(_raw(m), flags, qc)
            qc.counts["overlap_merged"] += 1
            if fields is None:
                # the union fails a rule: keep the stronger of the two
                keep = prev if (prev.level, -order[prev.start_id]) >= (p.level, 0) else p
                merged.append(replace(keep, qc_flags=(*keep.qc_flags, "overlap_dropped")))
            else:
                merged.append(make(fields, list(dict.fromkeys(flags))))
        else:
            merged.append(p)
    return [replace(p, k=k) for k, p in enumerate(merged)], qc


def merge_patterns(a: Pattern, b: Pattern, order: dict[str, int]) -> Pattern:
    """Return the union of two overlapping patterns.

    Span = union of spans; evidence = union (never lost); level = the higher;
    indicators = union; antecedent = the first of the two that is not
    evidence of either; justification of the stronger pattern.

    Parameters
    ----------
    a, b : Pattern
    order : dict of str to int
        Unit id → position.

    Returns
    -------
    Pattern
        Flagged ``overlap_merged``; not yet re-checked.
    """
    start = a.start_id if order[a.start_id] <= order[b.start_id] else b.start_id
    end = a.end_id if order[a.end_id] >= order[b.end_id] else b.end_id
    ev = set(a.evidence_ids) | set(b.evidence_ids)
    antecedent = next((x for x in (a.antecedent_id, b.antecedent_id) if x and x not in ev), None)
    top = a if a.level >= b.level else b
    return replace(
        a,
        start_id=start,
        end_id=end,
        evidence_ids=tuple(sorted(ev, key=lambda e: order[e])),
        level=max(a.level, b.level),
        indicators=tuple(dict.fromkeys(a.indicators + b.indicators)),
        antecedent_id=antecedent,
        agent_uptake_id=a.agent_uptake_id or b.agent_uptake_id,
        justification=top.justification,
        qc_flags=tuple(dict.fromkeys((*a.qc_flags, *b.qc_flags, "overlap_merged"))),
    )
