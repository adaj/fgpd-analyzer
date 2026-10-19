"""Post-validation of one model (or human) response (DESIGN §8.3 and §3.4).

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

JUSTIFICATION_TRUNCATE_WORDS = 60


@dataclass(frozen=True)
class Pattern:
    """One validated pattern of one run (or one human coder)."""

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
        return f"{self.group}:{self.goal}:{self.run_idx}:{self.k}"

    @property
    def score(self) -> float:
        return self.level / 4


@dataclass
class QC:
    counts: Counter[str] = field(default_factory=Counter)

    def flag(self, flags: list[str], name: str) -> None:
        flags.append(name)
        self.counts[name] += 1


def parse_response(text: str, goal: str) -> list[dict[str, Any]]:
    """Parse JSON; raise ResponseParseError when unusable (the runner retries once)."""
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
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def _norm_ids(v: Any) -> list[str]:
    if not isinstance(v, list):
        v = [v] if v else []
    out = [x for x in (_norm_id(i) for i in v) if x]
    return list(dict.fromkeys(out))


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
    """Apply §8.3 steps 2–8. With `apply=False` (human sheets), nothing is
    demoted, dropped or merged; problems are only flagged."""
    qc = qc or QC()
    units = pg.unit_index
    order = {uid: u.order for uid, u in units.items()}
    speaker = {uid: u.speaker for uid, u in units.items()}
    student = {uid for uid, u in units.items() if not u.is_agent}
    agent = {uid for uid, u in units.items() if u.is_agent}
    allowed = set(GOAL_INDICATORS[goal])
    out: list[Pattern] = []

    def as_reported(item: dict[str, Any], flags: list[str]) -> Pattern:
        lv = item.get("level")
        return Pattern(
            group=pg.group,
            goal=goal,
            run_idx=run_idx,
            start_id=str(_norm_id(item.get("start_id"))),
            end_id=str(_norm_id(item.get("end_id"))),
            evidence_ids=tuple(_norm_ids(item.get("evidence_ids"))),
            antecedent_id=_norm_id(item.get("antecedent_id")),
            indicators=tuple(str(i) for i in (item.get("indicators") or [])),
            level=int(lv) if isinstance(lv, (int, float, str)) and str(lv).isdigit() else 0,
            agent_uptake_id=_norm_id(item.get("agent_uptake_id")),
            justification=str(item.get("justification") or ""),
            qc_flags=tuple(flags),
            source=source,
        )

    for item in raw:
        flags: list[str] = []
        dropped = False
        # 2. indicators
        inds_raw = [str(i).strip().lower() for i in (item.get("indicators") or [])]
        inds = [i for i in dict.fromkeys(inds_raw) if i in allowed]
        if len(inds) < len(set(inds_raw)):
            qc.flag(flags, "indicator_removed")
        if not inds:
            qc.flag(flags, "drop_no_indicators")
            dropped = True
        if dropped:
            if not apply:
                out.append(as_reported(item, flags))
            continue
        # level
        try:
            level = int(item.get("level"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            level = 0
        if level not in (1, 2, 3, 4):
            qc.flag(flags, "drop_invalid_level")
            dropped = True
        if dropped:
            if not apply:
                out.append(as_reported(item, flags))
            continue
        # 3. ids exist
        start, end = _norm_id(item.get("start_id")), _norm_id(item.get("end_id"))
        if start not in units or end not in units:
            qc.flag(flags, "drop_unknown_span")
            dropped = True
        if dropped:
            if not apply:
                out.append(as_reported(item, flags))
            continue
        # 4. swap
        if start in order and end in order and order[start] > order[end]:
            start, end = end, start
            qc.flag(flags, "span_swapped")
        evidence = _norm_ids(item.get("evidence_ids"))
        bad_ev = [e for e in evidence if e not in student]
        if bad_ev:
            qc.flag(flags, "evidence_not_student_removed")
            evidence = [e for e in evidence if e in student]
        if start in order and end in order:
            outside = [
                e for e in evidence if e in order and not order[start] <= order[e] <= order[end]
            ]
            if outside:
                qc.flag(flags, "evidence_outside_span_removed")
                evidence = [e for e in evidence if e not in outside]
        antecedent = _norm_id(item.get("antecedent_id"))
        if antecedent is not None and antecedent not in student:
            qc.flag(flags, "antecedent_invalid_nulled")
            antecedent = None
        if antecedent is not None and antecedent in evidence:
            qc.flag(flags, "antecedent_removed_from_evidence")
            evidence = [e for e in evidence if e != antecedent]
        evidence.sort(key=lambda e: order.get(e, 10**9))
        if not evidence:
            qc.flag(flags, "drop_no_evidence")
            if not apply:
                out.append(as_reported(item, flags))
            continue
        last_ev = evidence[-1] if evidence else None
        if antecedent is not None and last_ev is not None and order[antecedent] > order[last_ev]:
            qc.flag(flags, "antecedent_after_evidence_nulled")
            antecedent = None
        uptake = _norm_id(item.get("agent_uptake_id"))
        if uptake is not None and (
            uptake not in agent or (last_ev is not None and order[uptake] > order[last_ev])
        ):
            qc.flag(flags, "agent_citation_invalid_nulled")
            uptake = None

        # 5. structural constraints (§3.4, as revised in docs/decisions.md D16)
        ev_speakers = {speaker[e] for e in evidence if e in speaker}
        if goal in ANTECEDENT_REQUIRED and (
            antecedent is None or not any(speaker[e] != speaker[antecedent] for e in evidence)
        ):
            qc.flag(flags, "drop_missing_antecedent")
            if not apply:
                out.append(as_reported(item, flags))
            continue
        new_level = level
        if goal == "G1":
            if level == 4 and len(ev_speakers) < 2:
                new_level = 3 if len(evidence) >= 2 else 2
            if new_level == 3 and not (len(ev_speakers) >= 2 or len(evidence) >= 2):
                new_level = 2
        elif goal == "G3":
            if (
                level >= 3
                and len(evidence) < 2
                and not (start in order and order[start] < order[evidence[0]])
            ):
                new_level = 2
        elif goal == "G4" and level >= 3 and antecedent is not None:
            first_op = next(
                (i for i, e in enumerate(evidence) if speaker[e] != speaker[antecedent]), None
            )
            returns = first_op is not None and any(
                speaker[e] == speaker[antecedent] for e in evidence[first_op + 1 :]
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

        if not apply:
            out.append(as_reported(item, flags))
            continue
        out.append(
            Pattern(
                group=pg.group,
                goal=goal,
                run_idx=run_idx,
                start_id=str(start),
                end_id=str(end),
                evidence_ids=tuple(evidence),
                antecedent_id=antecedent,
                indicators=tuple(inds),
                level=level,
                agent_uptake_id=uptake,
                justification=just,
                qc_flags=tuple(flags),
                source=source,
            )
        )

    # 6. merge same-goal overlaps; 8. sort and number
    if not apply:
        idx = sorted(
            (i for i, p in enumerate(out) if p.start_id in order and p.end_id in order),
            key=lambda i: order[out[i].start_id],
        )
        for a, b in zip(idx, idx[1:], strict=False):
            if order[out[b].start_id] <= order[out[a].end_id]:
                qc.counts["overlap"] += 1
                out[b] = replace(out[b], qc_flags=(*out[b].qc_flags, "overlap"))
        return [replace(p, k=k) for k, p in enumerate(out)], qc
    out.sort(key=lambda p: (order.get(p.start_id, 0), order.get(p.end_id, 0)))
    merged: list[Pattern] = []
    for p in out:
        if merged and order[p.start_id] <= order[merged[-1].end_id]:
            merged[-1] = merge_patterns(merged[-1], p, order)
            qc.counts["overlap_merged"] += 1
        else:
            merged.append(p)
    return [replace(p, k=k) for k, p in enumerate(merged)], qc


def merge_patterns(a: Pattern, b: Pattern, order: dict[str, int]) -> Pattern:
    start = a.start_id if order[a.start_id] <= order[b.start_id] else b.start_id
    end = a.end_id if order[a.end_id] >= order[b.end_id] else b.end_id
    antecedent = a.antecedent_id or b.antecedent_id
    evidence = tuple(
        sorted((set(a.evidence_ids) | set(b.evidence_ids)) - {antecedent}, key=lambda e: order[e])
    )
    top = a if a.level >= b.level else b
    return replace(
        a,
        start_id=start,
        end_id=end,
        evidence_ids=evidence,
        level=max(a.level, b.level),
        indicators=tuple(dict.fromkeys(a.indicators + b.indicators)),
        antecedent_id=antecedent,
        agent_uptake_id=a.agent_uptake_id or b.agent_uptake_id,
        justification=top.justification,
        qc_flags=tuple(dict.fromkeys((*a.qc_flags, *b.qc_flags, "overlap_merged"))),
    )
