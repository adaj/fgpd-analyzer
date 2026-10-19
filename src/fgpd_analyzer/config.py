"""Configuration: `AnalyzerConfig` (frozen) and `ColumnMap` (input column mapping)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .exceptions import ConfigError

GOALS: tuple[str, ...] = ("G1", "G2", "G3", "G4")
GOAL_LABELS: dict[str, str] = {"G1": "share", "G2": "listen", "G3": "deepen", "G4": "engage"}

_DURATION_RX = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hours?)?\s*$",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "ms": 0.001,
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "hours": 3600,
}


def parse_duration(value: Any) -> timedelta:
    """Accept '60s', '2min', '1.5 h', an int/float number of seconds or a timedelta."""
    if isinstance(value, timedelta):
        td = value
    elif isinstance(value, bool):
        raise ConfigError(f"invalid duration: {value!r}")
    elif isinstance(value, (int, float)):
        td = timedelta(seconds=float(value))
    elif isinstance(value, str):
        m = _DURATION_RX.match(value)
        if not m:
            raise ConfigError(f"invalid duration: {value!r} (use e.g. '60s', '2min', '1h')")
        unit = (m.group(2) or "s").lower()
        td = timedelta(seconds=float(m.group(1)) * _UNIT_SECONDS[unit])
    else:
        raise ConfigError(f"invalid duration: {value!r}")
    if td.total_seconds() < 0:
        raise ConfigError(f"duration must be non-negative: {value!r}")
    return td


# Fields that change what the model sees or how outputs are computed from model output.
# Post-hoc fields (influence lag/rule, consensus thresholds) are excluded on purpose:
# they can be changed on saved results without new API calls.
_FINGERPRINT_FIELDS = (
    "model",
    "backend",
    "effort",
    "max_tokens",
    "unit_gap",
    "pattern_gap_units",
    "pattern_gap",
    "goals",
    "n_runs",
    "pseudonymize",
    "min_name_len_in_text",
    "chat_language",
    "justification_language",
    "agent_pattern",
    "agent_pattern_regex",
    "extra_agent_patterns",
)


class AnalyzerConfig(BaseModel):
    """Every option of the analyzer. Immutable; use `.replace(**changes)` to derive one."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    # model / backend
    model: str = "claude-haiku-5-5"
    api_key: SecretStr | None = None
    backend: Literal["anthropic", "mock", "replay"] = "anthropic"
    effort: Literal["low", "medium", "high"] = "medium"
    max_tokens: int = Field(16000, ge=1024, le=64000)
    timeout_s: float = Field(300.0, gt=0)
    max_retries: int = Field(5, ge=0, le=10)
    max_concurrency: int = Field(4, ge=1, le=64)
    requests_per_minute: int | None = Field(None, ge=1)

    # roles
    agent_pattern: str = "agent"
    agent_pattern_regex: bool = False
    extra_agent_patterns: tuple[str, ...] = ()

    # agent influence (post hoc)
    influence_lag: timedelta = timedelta(minutes=2)
    influence_rule: Literal["uptake", "temporal"] = "uptake"

    # units and patterns
    unit_gap: timedelta = timedelta(seconds=60)
    pattern_gap_units: int = Field(6, ge=1)
    pattern_gap: timedelta = timedelta(minutes=5)
    goals: tuple[str, ...] = GOALS

    # runs and consensus
    n_runs: int = Field(3, ge=1, le=20)
    consensus_min_support: float = Field(0.5, gt=0, le=1)
    match_iou: float = Field(0.3, gt=0, le=1)

    # eligibility
    min_student_messages: int = Field(8, ge=1)
    min_students: int = Field(2, ge=1)
    max_agent_share: float = Field(0.40, gt=0, le=1)
    exclude_flagged: bool = False
    max_prompt_tokens: int = Field(80000, ge=1000)

    # privacy
    pseudonymize: bool = True
    extra_names: tuple[str, ...] = Field((), repr=False)
    min_name_len_in_text: int = Field(3, ge=1)
    redact_vocatives: bool = True

    # languages
    chat_language: str | None = None
    justification_language: str = "en"

    # behaviour
    sort: bool = True
    dayfirst: bool = False
    strict: bool = False
    cache: Literal["use", "refresh", "off"] = "use"
    cache_dir: Path | None = None
    prompts_dir: Path | None = None

    @field_validator("influence_lag", "unit_gap", "pattern_gap", mode="before")
    @classmethod
    def _dur(cls, v: Any) -> timedelta:
        return parse_duration(v)

    @field_validator("goals", mode="before")
    @classmethod
    def _goals(cls, v: Any) -> tuple[str, ...]:
        if isinstance(v, str):
            v = (v,)
        goals = tuple(str(g).upper() for g in v)
        bad = [g for g in goals if g not in GOALS]
        if bad or not goals:
            raise ConfigError(f"goals must be a non-empty subset of {GOALS}; got {v!r}")
        return tuple(g for g in GOALS if g in goals)  # canonical order, no duplicates

    @field_validator("extra_agent_patterns", "extra_names", mode="before")
    @classmethod
    def _tuple(cls, v: Any) -> tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, str):
            return (v,)
        return tuple(str(x) for x in v)

    @field_validator("api_key", mode="before")
    @classmethod
    def _key(cls, v: Any) -> Any:
        return None if v in ("", None) else v

    @model_validator(mode="after")
    def _check(self) -> AnalyzerConfig:
        if not self.agent_pattern:
            raise ConfigError("agent_pattern must not be empty")
        if self.agent_pattern_regex:
            for p in (self.agent_pattern, *self.extra_agent_patterns):
                try:
                    re.compile(p)
                except re.error as e:
                    raise ConfigError(f"invalid agent regex {p!r}: {e}") from e
        return self

    def replace(self, **changes: Any) -> AnalyzerConfig:
        data = self.model_dump()
        data.update(changes)
        return AnalyzerConfig(**data)

    def fingerprint(self) -> str:
        """Hash of every field that affects what is sent to the model (never the key)."""
        payload = {}
        for f in _FINGERPRINT_FIELDS:
            v = getattr(self, f)
            payload[f] = v.total_seconds() if isinstance(v, timedelta) else v
        payload["extra_names_sha"] = hashlib.sha256(
            "\x00".join(sorted(self.extra_names)).encode()
        ).hexdigest()
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def redacted(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        d["api_key"] = "***" if self.api_key else None
        d["extra_names"] = f"<{len(self.extra_names)} name(s)>"
        return d


# --------------------------------------------------------------------------- columns

CANONICAL_COLUMNS: tuple[str, ...] = ("group", "username", "timestamp", "text", "message_id")
REQUIRED_COLUMNS: tuple[str, ...] = ("group", "username", "timestamp", "text")

SYNONYMS: dict[str, tuple[str, ...]] = {
    "group": (
        "group",
        "group_id",
        "groupid",
        "group_name",
        "grupo",
        "chat",
        "chat_id",
        "room",
        "room_id",
        "team",
        "team_id",
        "groep",
    ),
    "username": (
        "username",
        "user",
        "user_name",
        "userid",
        "user_id",
        "author",
        "sender",
        "speaker",
        "usuario",
        "usuário",
        "nome",
        "name",
        "gebruiker",
    ),
    "timestamp": (
        "timestamp",
        "time",
        "datetime",
        "date_time",
        "created_at",
        "sent_at",
        "date",
        "data",
        "hora",
        "data_hora",
        "ts",
        "tijd",
    ),
    "text": ("text", "message", "msg", "content", "body", "mensagem", "texto", "bericht"),
    "message_id": ("message_id", "msg_id", "messageid", "id_mensagem"),
}


def _norm(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


class ColumnMap(BaseModel):
    """Canonical column -> source column. Missing entries are found by `auto`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    group: str | None = None
    username: str | None = None
    timestamp: str | None = None
    text: str | None = None
    message_id: str | None = None

    @classmethod
    def coerce(cls, value: ColumnMap | Mapping[str, str] | None) -> ColumnMap:
        if value is None:
            return cls()
        if isinstance(value, ColumnMap):
            return value
        bad = set(value) - set(CANONICAL_COLUMNS)
        if bad:
            raise ConfigError(f"unknown canonical column(s) {sorted(bad)}; use {CANONICAL_COLUMNS}")
        return cls(**dict(value))

    def resolve(self, columns: Iterable[str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
        """Return (canonical -> source) and a list of (canonical, problem) pairs.

        Explicit entries win; the rest are matched case-insensitively against SYNONYMS.
        """
        cols = [str(c) for c in columns]
        by_norm: dict[str, list[str]] = {}
        for c in cols:
            by_norm.setdefault(_norm(c), []).append(c)
        mapping: dict[str, str] = {}
        problems: list[tuple[str, str]] = []
        for canon in CANONICAL_COLUMNS:
            explicit = getattr(self, canon)
            if explicit is not None:
                if explicit in cols:
                    mapping[canon] = explicit
                else:
                    problems.append((canon, f"mapped column {explicit!r} not found"))
                continue
            hits = [c for syn in SYNONYMS[canon] for c in by_norm.get(_norm(syn), [])]
            hits = list(dict.fromkeys(h for h in hits if h not in mapping.values()))
            if len(hits) >= 1:
                mapping[canon] = hits[0]
                if len(hits) > 1:
                    problems.append((canon, f"ambiguous: {hits}; using {hits[0]!r}"))
        return mapping, problems
