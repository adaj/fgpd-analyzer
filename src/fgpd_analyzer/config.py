"""Configuration: `AnalyzerConfig` (every option) and `ColumnMap` (input columns).

`AnalyzerConfig` is the single, immutable source of settings for every stage
of the pipeline (input → units → prompts → requests → runs → post-validation →
consensus → influence → outputs). `ColumnMap` maps the columns of the input
table to the canonical names ``group``, ``username``, ``timestamp``, ``text``
and ``message_id``; it is used at the very first stage. The module also holds
the goal constants and `parse_duration`, which accepts the duration formats
used throughout the API.
"""

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

#: The four FGPD goals, in canonical order.
GOALS: tuple[str, ...] = ("G1", "G2", "G3", "G4")
#: Short label of each goal, as used in the ``goal_label`` output columns.
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
    """Convert a duration given in any accepted form to a `timedelta`.

    Parameters
    ----------
    value : str, int, float or datetime.timedelta
        A string with a number and an optional unit (``"60s"``, ``"2min"``,
        ``"1.5 h"``, ``"500ms"``; a bare number means seconds), a number of
        seconds, or a `timedelta`. Units: ``ms``; ``s``/``sec``/``secs``/
        ``second(s)``; ``m``/``min``/``mins``/``minute(s)``; ``h``/``hr``/
        ``hour(s)``, case-insensitive.

    Returns
    -------
    datetime.timedelta

    Raises
    ------
    ConfigError
        If the value cannot be parsed, is a bool, or is negative.

    Examples
    --------
    >>> parse_duration("2min")
    datetime.timedelta(seconds=120)
    >>> parse_duration(90)
    datetime.timedelta(seconds=90)
    """
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
    "redact_vocatives",
    "dayfirst",
    "sort",
)


class AnalyzerConfig(BaseModel):
    """Every option of the analyzer (pydantic model, immutable).

    Build one with keyword arguments, or pass the same keywords to
    `FGPDAnalyzer`. Derive a modified copy with `replace`. Unknown fields are
    rejected.

    Each field below says what it changes:

    - **request**: changes what is sent to the model, hence the cache key.
      Changing it means new API calls (and new cost).
    - **selection**: changes which calls are made, not their content. Calls
      already made are served from the cache.
    - **post hoc**: applied to model output after the calls. Changing it never
      needs a new request; influence settings can even be changed on saved
      results (`FGPDResults.with_influence`).
    - **operational**: affects how calls are made, not the results.

    Attributes
    ----------
    model : str, default "claude-haiku-5-5"
        Model id sent to the API. Request. Costs are computed only for models
        in the price table (`fgpd_analyzer.backends.pricing`).
    api_key : pydantic.SecretStr or None, default None
        Anthropic API key. ``None`` (or ``""``) reads ``$ANTHROPIC_API_KEY``.
        Operational; never written to metadata or logs.
    backend : {"anthropic", "mock", "replay"}, default "anthropic"
        Backend built when `FGPDAnalyzer` receives no backend instance.
        ``"mock"`` gives synthetic, offline output; ``"replay"`` needs a
        `ReplayBackend` instance. Request: the backend namespace is part of
        the cache key, so mock responses are never served to a real run.
    effort : {"low", "medium", "high", "xhigh", "max"}, default "medium"
        Reasoning effort (``output_config.effort``). Request. Higher effort
        means more output (thinking) tokens and higher cost.
    max_tokens : int, default 32000
        Output-token budget per call, 1,024 to 64,000 tokens. Request (the
        configured value is in the cache key). A call cut off at this budget
        is retried once with twice the budget, capped at 64,000 tokens.
    timeout_s : float, default 300.0
        Timeout per API request, in seconds. Operational.
    max_retries : int, default 5
        Transport retries done by the Anthropic SDK (429, 529, 5xx,
        timeouts), 0 to 10. Operational.
    max_concurrency : int, default 4
        Maximum number of calls in flight, 1 to 64. Operational.
    requests_per_minute : int or None, default None
        Maximum number of request starts per rolling minute; ``None`` means
        no limit. Operational.
    agent_pattern : str, default "agent"
        Usernames containing this substring (case-insensitive) are agents;
        everyone else is a student. Must not be empty. Request (roles decide
        the ``[AGENT]`` markers, pseudonyms and units).
    agent_pattern_regex : bool, default False
        Treat `agent_pattern` and `extra_agent_patterns` as regular
        expressions (``re.search``, case-insensitive). Request.
    extra_agent_patterns : tuple of str, default ()
        Further agent patterns, e.g. for teachers or moderators. A single
        string is accepted. Request.
    influence_lag : datetime.timedelta, default 2 minutes
        Window for agent influence: an agent unit counts if it starts at most
        this long before the pattern start (or inside the pattern). Accepts
        any `parse_duration` form (``"2min"``, ``90``, a `timedelta`). Post
        hoc (DESIGN §11).
    influence_rule : {"uptake", "temporal"}, default "uptake"
        ``"uptake"`` counts a pattern as influenced only when the students
        take up the content of an agent unit inside the window;
        ``"temporal"`` also counts mere temporal proximity
        (``temporal_only``), for sensitivity analyses. Post hoc.
    unit_gap : datetime.timedelta, default 60 seconds
        Consecutive messages of one student at most this far apart form one
        unit (inclusive). Request (changes the units).
    pattern_gap_units : int, default 6
        A new pattern begins when this many units of any speaker pass without
        evidence for the goal. At least 1. Request (rendered into the
        codebook).
    pattern_gap : datetime.timedelta, default 5 minutes
        A new pattern begins when more than this time passes without
        evidence for the goal. Request (rendered into the codebook, in
        minutes).
    goals : tuple of str, default ("G1", "G2", "G3", "G4")
        Goals to measure; any non-empty subset, case-insensitive, stored in
        canonical order. Selection (one call per goal; the codebook always
        contains all four goals).
    n_runs : int, default 3
        Independent runs per group × goal, 1 to 20. Selection: run *k* has
        its own cache key, so raising `n_runs` reuses the earlier runs.
        With 1 run there is no dispersion (flag ``no_dispersion``).
    consensus_min_support : float, default 0.5
        Share of successful runs in which a pattern cluster must appear to
        become a consensus pattern, in (0, 1]. Applied literally: with two
        successful runs a pattern found in one counts. Post hoc (applied
        during `analyze`; a rerun with the cache sends no request).
    match_iou : float, default 0.3
        Minimum span overlap (intersection over union on unit positions) for
        two runs' patterns to be matched into one cluster, in (0, 1]. Post
        hoc, like `consensus_min_support`.
    min_student_messages : int, default 8
        Groups with fewer student messages get status ``insufficient_data``
        (W003) and no call. Selection.
    min_students : int, default 2
        Groups with fewer students get status ``insufficient_data`` (W003).
        Selection.
    max_agent_share : float, default 0.40
        Groups whose share of agent messages exceeds this fraction are
        flagged (W009, ``flagged_agent_share``), in (0, 1]. Flagging alone
        changes nothing else.
    exclude_flagged : bool, default False
        If True, flagged groups get status ``excluded`` and no call.
        Selection.
    max_prompt_tokens : int, default 80000
        Groups whose estimated prompt exceeds this many tokens get status
        ``too_long`` (W010) and no call. Keeps prompts below the 100k-token
        price tier. Selection.
    pseudonymize : bool, default True
        Replace usernames by S1, S2, ... (students) and AG1, ... (agents) in
        the speaker field and inside message text. ``False`` emits a
        `PrivacyWarning`. Request.
    extra_names : tuple of str, default ()
        Other known names (nicknames, teachers) to replace in the text by
        N1, N2, .... Shown only as a count in metadata. Request.
    min_name_len_in_text : int, default 3
        Names shorter than this many characters are not replaced inside
        text (W013), so that one-letter usernames do not corrupt words such
        as Portuguese *a*, *e*, *o*. Request.
    redact_vocatives : bool, default True
        Replace words that agents use to address someone (likely first
        names) by ``[NAME]`` in every message (W016). Request.
    name_map : dict of str to str, default {}
        Researcher's name key, real name → username in the data, e.g.
        ``{"rodrigo": "J"}``; also a path to a JSON file or to a printed
        Python dict. Every variant of a real name (full name, name parts,
        one-letter typos with `fuzzy_names`) is replaced by that student's
        pseudonym, or by ``[NAME]`` for anyone outside the group. Shown only
        as a count in metadata. Request.
    fuzzy_names : bool, default True
        Also replace single-word names with one extra letter, or one missing
        letter for names of 7+ letters (W017). Request.
    chat_language : str or None, default None
        Language of the chats, e.g. ``"pt-BR"``; ``None`` tells the model the
        chat may be in any language. Request (rendered into the prompts).
    justification_language : str, default "en"
        Language of the model's justifications, e.g. ``"pt"``. Request.
    sort : bool, default True
        Stable-sort messages by time within each group (W005). With
        ``False``, out-of-order timestamps are an error. Request.
    dayfirst : bool, default False
        Parse ambiguous dates such as ``05/09`` day first. Request.
    strict : bool, default False
        Report every validation warning as an error. Selection (an input
        with warnings cannot be analyzed).
    cache : {"use", "refresh", "off"}, default "use"
        ``"use"`` reads and writes the local response cache; ``"refresh"``
        ignores cached responses but stores new ones; ``"off"`` neither
        reads nor writes. Operational.
    cache_dir : pathlib.Path or None, default None
        Cache directory; ``None`` is the per-user cache directory from
        ``platformdirs`` (``user_cache_path("fgpd-analyzer")``). Operational.
    prompts_dir : pathlib.Path or None, default None
        Directory of prompt overrides, used when `FGPDAnalyzer` receives no
        ``prompts=``. Request (custom prompts change the prompt-set hash and
        mark results as non-comparable).

    Raises
    ------
    ConfigError
        From the field validators: invalid duration, goal subset, agent
        regex, `name_map` or empty `agent_pattern`. Pydantic's
        ``ValidationError`` (a ``ValueError``) for out-of-range numbers or
        unknown fields; `FGPDAnalyzer` converts both into `ConfigError`.

    See Also
    --------
    fingerprint : hash of the settings that affect outputs.
    redacted : the configuration as written to run metadata.

    Notes
    -----
    The cache key of a request is the hash of the exact request (with the
    configured `max_tokens`), the prompt-set hash, the schema version, the
    backend namespace and the run index, so every "request" setting changes
    it by construction (docs/decisions.md R16).

    Examples
    --------
    >>> cfg = AnalyzerConfig(agent_pattern="Clair", influence_lag="90s", n_runs=5)
    >>> cfg.influence_lag.total_seconds()
    90.0
    >>> cfg.replace(effort="low").effort
    'low'
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    # model / backend
    model: str = "claude-haiku-5-5"
    api_key: SecretStr | None = None
    backend: Literal["anthropic", "mock", "replay"] = "anthropic"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_tokens: int = Field(32000, ge=1024, le=64000)
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
    # researcher's name key: real name -> username in the data (e.g. {"rodrigo": "J"})
    name_map: dict[str, str] = Field(default_factory=dict, repr=False)
    fuzzy_names: bool = True

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

    @field_validator("name_map", mode="before")
    @classmethod
    def _name_map(cls, v: Any) -> dict[str, str]:
        if v is None:
            return {}
        if isinstance(v, (str, Path)):
            import json as _json

            text = Path(v).read_text(encoding="utf-8")
            try:
                v = _json.loads(text)
            except ValueError:
                import ast

                v = ast.literal_eval(text)  # also accepts a printed Python dict
        if not isinstance(v, Mapping):
            raise ConfigError("name_map must be a dict {real name: username} or a JSON file")
        return {str(k): str(val) for k, val in v.items()}

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
        """Return a copy with some fields changed; the result is validated again.

        Parameters
        ----------
        **changes
            Field names and new values, in any form the constructor accepts.

        Returns
        -------
        AnalyzerConfig
            A new configuration; `self` is not modified.

        Raises
        ------
        ConfigError
            If a new value fails a field validator.
        pydantic.ValidationError
            If a value is out of range or a field name is unknown.
        """
        data = self.model_dump()
        data.update(changes)
        return AnalyzerConfig(**data)

    def fingerprint(self) -> str:
        """Return a SHA-256 hex digest of the settings that affect the outputs.

        Covers model, backend, effort, max_tokens, unit and pattern gaps,
        goals, n_runs, pseudonymization settings, languages and agent
        patterns, plus hashes of `extra_names` and `name_map` (the names
        themselves are never stored). Never includes the API key. Post-hoc
        settings (influence lag and rule, consensus thresholds) are left out
        on purpose: they can change without new API calls.

        Returns
        -------
        str
            64 hexadecimal characters, stored as ``config_fingerprint`` in the
            run metadata.

        Notes
        -----
        This is a provenance label, not the cache key: the cache key hashes
        the exact request (see the class Notes).
        """
        payload = {}
        for f in _FINGERPRINT_FIELDS:
            v = getattr(self, f)
            payload[f] = v.total_seconds() if isinstance(v, timedelta) else v
        payload["extra_names_sha"] = hashlib.sha256(
            "\x00".join(sorted(self.extra_names)).encode()
        ).hexdigest()
        payload["name_map_sha"] = hashlib.sha256(
            json.dumps(sorted(self.name_map.items())).encode()
        ).hexdigest()
        payload["fuzzy_names"] = self.fuzzy_names
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def redacted(self) -> dict[str, Any]:
        """Return the configuration as a JSON-safe dict without personal data.

        Returns
        -------
        dict
            Every field, with ``api_key`` shown as ``"***"`` (or None) and
            ``extra_names`` / ``name_map`` shown as a count. Written to the
            run metadata.
        """
        d = self.model_dump(mode="json")
        d["api_key"] = "***" if self.api_key else None
        d["extra_names"] = f"<{len(self.extra_names)} name(s)>"
        d["name_map"] = f"<{len(self.name_map)} name(s)>"
        return d


# --------------------------------------------------------------------------- columns

#: Canonical input columns, in order.
CANONICAL_COLUMNS: tuple[str, ...] = ("group", "username", "timestamp", "text", "message_id")
#: Canonical columns that every input must provide.
REQUIRED_COLUMNS: tuple[str, ...] = ("group", "username", "timestamp", "text")

#: Source column names (after normalization) matched to each canonical column.
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
    """Normalize a column name for matching: lower case, spaces and dashes to ``_``."""
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


class ColumnMap(BaseModel):
    """Map canonical column names to the columns of the input table.

    Each field holds the name of the source column for one canonical column,
    or None to find it by name. Unmapped columns are matched against a list of
    synonyms after normalizing case, spaces and dashes (e.g. ``Group``,
    ``grupo``, ``chat_id`` → ``group``; ``Message``, ``Mensagem``, ``texto`` →
    ``text``; ``Timestamp``, ``created_at``, ``data_hora`` → ``timestamp``).

    Attributes
    ----------
    group : str or None
        Column with the group (chat) identifier. Required.
    username : str or None
        Column with the sender's username. Required. Agents are recognized
        from it with ``agent_pattern``.
    timestamp : str or None
        Column with the message time. Required. Any format pandas can parse;
        ISO 8601 is recommended.
    text : str or None
        Column with the message text. Required.
    message_id : str or None
        Column with message identifiers, unique within a group. Optional;
        when absent (or empty), ids ``m0001``, ``m0002``, ... are generated
        per group in time order.

    Examples
    --------
    >>> ColumnMap(text="Mensagem", username="Aluno").text
    'Mensagem'

    In `FGPDAnalyzer` methods a plain dict works too:
    ``columns={"text": "Mensagem"}``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    group: str | None = None
    username: str | None = None
    timestamp: str | None = None
    text: str | None = None
    message_id: str | None = None

    @classmethod
    def coerce(cls, value: ColumnMap | Mapping[str, str] | None) -> ColumnMap:
        """Build a `ColumnMap` from None, a `ColumnMap` or a mapping.

        Parameters
        ----------
        value : ColumnMap, mapping or None
            A mapping must use canonical names as keys.

        Returns
        -------
        ColumnMap

        Raises
        ------
        ConfigError
            If a key is not a canonical column name.
        """
        if value is None:
            return cls()
        if isinstance(value, ColumnMap):
            return value
        bad = set(value) - set(CANONICAL_COLUMNS)
        if bad:
            raise ConfigError(f"unknown canonical column(s) {sorted(bad)}; use {CANONICAL_COLUMNS}")
        return cls(**dict(value))

    def resolve(self, columns: Iterable[str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
        """Match canonical columns to the given source columns.

        Explicit entries win; the rest are matched against `SYNONYMS` after
        normalization. A source column is used at most once.

        Parameters
        ----------
        columns : iterable of str
            Column names of the input table.

        Returns
        -------
        mapping : dict of str to str
            Canonical column → source column, for every column found.
        problems : list of tuple of (str, str)
            ``(canonical, problem)`` pairs: an explicit column that does not
            exist (reported as E001), or several synonyms found (E002; the
            first one is used).
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
