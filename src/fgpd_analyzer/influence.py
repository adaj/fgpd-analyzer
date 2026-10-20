"""Agent influence, applied after the fact (DESIGN §11; user decision U9).

The influence stage of the pipeline (input → units → prompts → requests →
runs → post-validation → consensus → **influence** → outputs). It needs no
API call, so `FGPDResults.with_influence` can apply another lag or rule to
saved results.

The model (or human coder) cites the agent unit whose content the students
take up, without knowing the lag. Code then applies the lag window::

    W(p) = agent units a with order(a) < order(last evidence)
                          and t(a) >= t(pattern start) - lag
    uptake        : the cited unit is in W(p)        -> influenced
    temporal_only : W(p) not empty, no valid citation -> not influenced (U9)
    none          : W(p) empty

`influence_rule="temporal"` also counts temporal_only (sensitivity only).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd


@dataclass(frozen=True)
class UnitInfo:
    """The unit fields needed to classify influence (from the ``units`` table)."""

    unit_id: str
    order: int
    is_agent: bool
    t_start: pd.Timestamp
    t_end: pd.Timestamp
    first_message_id: str


@dataclass(frozen=True)
class Influence:
    """Influence classification of one pattern.

    Attributes
    ----------
    agent_influence : {"uptake", "temporal_only", "none"}
    agent_unit_id, agent_message_id : str or None
        The agent unit (and its first message) the classification refers to.
    agent_lag_s : float or None
        Seconds from the agent unit to the first evidence unit after it.
    agent_position : {"before", "within"} or None
        Whether the agent unit precedes the pattern start or lies inside it.
    influenced : bool
        Whether the pattern counts as influenced under the rule.
    """

    agent_influence: str  # uptake | temporal_only | none
    agent_unit_id: str | None
    agent_message_id: str | None
    agent_lag_s: float | None
    agent_position: str | None  # before | within
    influenced: bool


def classify(
    start_id: str,
    evidence_ids: tuple[str, ...],
    cited: str | None,
    units: dict[str, UnitInfo],
    lag: timedelta,
    rule: str,
) -> Influence:
    """Classify the agent influence of one consensus pattern.

    Parameters
    ----------
    start_id : str
        First unit of the pattern.
    evidence_ids : tuple of str
        Evidence units.
    cited : str or None
        Agent unit cited by the consensus (``agent_uptake_id``).
    units : dict of str to UnitInfo
        Units of the group.
    lag : datetime.timedelta
        ``influence_lag``.
    rule : {"uptake", "temporal"}
        ``influence_rule``.

    Returns
    -------
    Influence
        ``uptake`` if `cited` is in the window W(p); else ``temporal_only``
        with the latest window unit at or before the start (else the
        earliest one) if W(p) is not empty; else ``none``.
    """
    start = units[start_id]
    ev = sorted((units[e] for e in evidence_ids), key=lambda u: u.order)
    last = ev[-1]
    lo = start.t_start - lag
    window = [u for u in units.values() if u.is_agent and u.order < last.order and u.t_start >= lo]
    window.sort(key=lambda u: u.order)
    agent: UnitInfo | None
    if cited is not None and cited in units and units[cited] in window:
        kind, agent = "uptake", units[cited]
    elif window:
        before = [u for u in window if u.t_start <= start.t_start]
        kind, agent = "temporal_only", (before[-1] if before else window[0])
    else:
        kind, agent = "none", None
    if agent is None:
        return Influence(kind, None, None, None, None, False)
    after = [u for u in ev if u.order > agent.order]
    lag_s = (after[0].t_start - agent.t_start).total_seconds() if after else None
    position = "before" if agent.order < start.order else "within"
    influenced = kind == "uptake" or (rule == "temporal" and kind == "temporal_only")
    return Influence(kind, agent.unit_id, agent.first_message_id, lag_s, position, influenced)
