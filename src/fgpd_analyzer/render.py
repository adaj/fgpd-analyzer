"""Transcript rendering (DESIGN §2.4). The same text goes to the LLM and to
human coders, so both code exactly the same units."""

from __future__ import annotations

import hashlib

from .preprocess import PreparedGroup, Unit


def _offset(t_min: float) -> str:
    total = int(round(t_min * 60))
    m, s = divmod(total, 60)
    return f"+{m:02d}:{s:02d}"


def render_unit(u: Unit) -> str:
    who = f"{u.speaker} [AGENT]" if u.is_agent else u.speaker
    text = " ".join(u.text.splitlines())  # one line per unit (\n, \r\n, \r, U+2028)
    return f"{u.unit_id} | {u.t_start:%H:%M:%S} | {_offset(u.t_min)} | {who} | {text}"


def render_transcript(pg: PreparedGroup) -> str:
    return "\n".join(render_unit(u) for u in pg.units)


def chat_hash(transcript: str) -> str:
    return hashlib.sha256(transcript.encode("utf-8")).hexdigest()
