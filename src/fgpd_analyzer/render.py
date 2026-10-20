"""Transcript rendering (DESIGN §2.4).

Between the units and the prompts in the pipeline (input → units →
**prompts** → requests → ...): it turns the units of a group into the text of
the ``<transcript>`` block. The same text goes to the model, to
`FGPDAnalyzer.preview` and to the human coding sheet, so humans and the model
code exactly the same units.
"""

from __future__ import annotations

import hashlib

from .preprocess import PreparedGroup, Unit


def _offset(t_min: float) -> str:
    """Format minutes since the start as ``+MM:SS``."""
    total = int(round(t_min * 60))
    m, s = divmod(total, 60)
    return f"+{m:02d}:{s:02d}"


def render_unit(u: Unit) -> str:
    """Render one unit as one line.

    Parameters
    ----------
    u : Unit

    Returns
    -------
    str
        ``unit_id | HH:MM:SS | +MM:SS | speaker | text``; agents are marked
        ``speaker [AGENT]`` and line breaks inside the text become spaces.
    """
    who = f"{u.speaker} [AGENT]" if u.is_agent else u.speaker
    text = " ".join(u.text.splitlines())  # one line per unit (\n, \r\n, \r, U+2028)
    return f"{u.unit_id} | {u.t_start:%H:%M:%S} | {_offset(u.t_min)} | {who} | {text}"


def render_transcript(pg: PreparedGroup) -> str:
    """Render all units of a group, one line per unit (see `render_unit`)."""
    return "\n".join(render_unit(u) for u in pg.units)


def chat_hash(transcript: str) -> str:
    """SHA-256 hex digest of a rendered transcript (UTF-8), stored in run metadata."""
    return hashlib.sha256(transcript.encode("utf-8")).hexdigest()
