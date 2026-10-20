"""Output schema ``patterns_v1`` (DESIGN §8.2), built per chat.

Part of the requests stage of the pipeline (input → units → prompts →
**requests** → runs → post-validation → ...): the schema is sent as
``output_config.format`` and constrains the model's JSON output.

Unit ids are enums, so the model cannot cite a unit that does not exist. The
schema is identical for the four goals of a chat (the goal is an enum field),
which keeps the prompt cache valid across goal calls and runs of that chat.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .codebook import ALL_INDICATORS
from .config import GOALS
from .preprocess import PreparedGroup

#: Version label of the output schema; part of the prompt-set hash and metadata.
SCHEMA_VERSION = "patterns_v1"
#: Above this many unit ids, an id field is a plain string instead of an enum.
MAX_ENUM_IDS = 1500


def _id_enum(ids: list[str], max_ids: int) -> dict[str, Any]:
    """Return the schema of one id field: an enum, a plain string (many ids) or null."""
    if not ids:
        return {"type": "null"}
    if len(ids) > max_ids:
        return {"type": "string"}
    return {"type": "string", "enum": ids}


def build_schema(pg: PreparedGroup, max_enum_ids: int = MAX_ENUM_IDS) -> dict[str, Any]:
    """Build the JSON schema of the model output for one chat.

    The output is ``{"goal": "G1".."G4", "patterns": [...]}``; each pattern has
    ``start_id``, ``end_id`` (any unit), ``evidence_ids`` (student units, at
    least one), ``antecedent_id`` (student unit or null), ``indicators`` (at
    least one of the known indicators), ``justification``, ``level`` (1–4)
    and ``agent_uptake_id`` (agent unit or null).

    Parameters
    ----------
    pg : PreparedGroup
        The chat whose unit ids become enums.
    max_enum_ids : int, default MAX_ENUM_IDS
        Above this many ids, id fields are plain strings. ``0`` gives the
        string-id fallback schema used after the API rejects the enum schema.

    Returns
    -------
    dict
        A JSON schema (``$defs`` with ``unit_id``, ``student_unit_id`` and,
        if the chat has agents, ``agent_unit_id``).
    """
    all_ids = [u.unit_id for u in pg.units]
    student = pg.student_unit_ids
    agent = pg.agent_unit_ids
    agent_field: dict[str, Any] = (
        {"anyOf": [{"$ref": "#/$defs/agent_unit_id"}, {"type": "null"}]}
        if agent
        else {"type": "null"}
    )
    pattern = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "start_id",
            "end_id",
            "evidence_ids",
            "antecedent_id",
            "indicators",
            "justification",
            "level",
            "agent_uptake_id",
        ],
        "properties": {
            "start_id": {"$ref": "#/$defs/unit_id"},
            "end_id": {"$ref": "#/$defs/unit_id"},
            "evidence_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/student_unit_id"},
            },
            "antecedent_id": {"anyOf": [{"$ref": "#/$defs/student_unit_id"}, {"type": "null"}]},
            "indicators": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "enum": list(ALL_INDICATORS)},
            },
            "justification": {"type": "string"},
            "level": {"type": "integer", "enum": [1, 2, 3, 4]},
            "agent_uptake_id": agent_field,
        },
    }
    defs: dict[str, Any] = {
        "unit_id": _id_enum(all_ids, max_enum_ids),
        "student_unit_id": _id_enum(student, max_enum_ids),
    }
    if agent:
        defs["agent_unit_id"] = _id_enum(agent, max_enum_ids)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["goal", "patterns"],
        "properties": {
            "goal": {"type": "string", "enum": list(GOALS)},
            "patterns": {"type": "array", "items": pattern},
        },
        "$defs": defs,
    }


def schema_hash(schema: dict[str, Any]) -> str:
    """SHA-256 hex digest of a schema (canonical JSON), stored in run metadata."""
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
