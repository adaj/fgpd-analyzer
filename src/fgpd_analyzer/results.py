"""`FGPDResults`: the outputs of an analysis (DESIGN §10).

The last stages of the pipeline (input → units → prompts → requests → runs →
post-validation → consensus → **influence → outputs**). `FGPDResults` stores
the internal tables produced by `fgpd_analyzer.analysis.run_analysis` and
derives the public tables from them: patterns with location and agent
influence, frequencies per group × goal, and time series.

Everything except the model calls is recomputed from stored tables, so the
influence lag, the influence rule and the time-series bin width can be changed
on saved results without any API call.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from .config import GOAL_LABELS, GOALS, parse_duration
from .influence import UnitInfo, classify

#: Columns holding lists: JSON strings in CSV, Arrow lists in parquet.
LIST_COLUMNS = (
    "indicators",
    "evidence_ids",
    "evidence_unit_ids",
    "evidence_message_ids",
    "speakers",
    "qc_flags",
    "member_ids",
    "message_ids",
)
#: Status values of a group × goal, in category order.
STATUSES = (
    "ok",
    "partial",
    "insufficient_data",
    "too_long",
    "excluded",
    "not_selected",
    "refused",
    "failed",
)
#: Statuses whose metrics are NA (no successful run).
NA_STATUSES = ("insufficient_data", "too_long", "excluded", "not_selected", "refused", "failed")


@dataclass
class FGPDResults:
    """Results of one analysis. No table contains message text.

    Returned by `FGPDAnalyzer.analyze`, or read back with `load`. The public
    tables are `frequencies`, `patterns`, `timeseries`, `unstable_patterns`,
    `run_patterns`, `messages` and `run_metadata`; `patterns`, `frequencies`
    and `timeseries` are derived from the stored tables on access, so
    `with_influence` and `timeseries` need no API call. The only free text is
    the model's pseudonymized ``justification``.

    Status of a group × goal (``frequencies["status"]``): ``ok`` (every run
    succeeded), ``partial`` (some runs failed, at least one succeeded),
    ``insufficient_data``, ``too_long``, ``excluded`` (not eligible),
    ``not_selected`` (eligible but left out by ``analyze(groups=...)``),
    ``refused`` and ``failed`` (no run succeeded). Every status except ``ok``
    and ``partial`` has NA metrics.

    Parameters
    ----------
    units, groups, calls, consensus, unstable_patterns, run_patterns, messages : pandas.DataFrame
        Stored tables (see Attributes).
    run_metadata : dict
        Provenance (see Attributes).
    validation : dict, optional
        Validation issues and per-group info.
    influence_lag : datetime.timedelta, default 2 minutes
        Lag used for `patterns`.
    influence_rule : {"uptake", "temporal"}, default "uptake"
        Rule used for `patterns`.
    goals : tuple of str, default all four
        Goals of the analysis.

    Attributes
    ----------
    frequencies : pandas.DataFrame
        One row per group × goal (DESIGN §10a). Columns: ``group``; ``goal``
        and ``goal_label`` (category); ``status`` (category); ``frequency``
        (Σ consensus ``score``, the main metric); ``count`` (consensus
        patterns); ``count_strong`` (patterns with level ≥ 3); ``mean_score``
        (frequency / count, NA if 0); ``rate_per_100`` (frequency per 100
        student messages, secondary); ``n_student_messages``,
        ``n_student_units``, ``n_agent_messages``, ``n_students``;
        ``duration_min`` (minutes, last − first message); ``participation``
        (share of the group's students in at least one evidence unit of this
        goal); ``influenced_frequency`` and ``influenced_count`` (patterns
        counted as influenced under the rule); ``uptake_count`` and
        ``temporal_only_count``; ``freq_run_mean`` and ``freq_run_sd`` (over
        the per-run Σ scores; SD NA with one run); ``n_runs_ok``;
        ``n_unstable`` (clusters below the support threshold). Counts are
        ``Int32``, metrics ``Float64``.
    patterns : pandas.DataFrame
        One row per consensus pattern (DESIGN §10c). Columns:
        ``pattern_id`` (``{group}:{goal}:{k}``); ``group``, ``goal``,
        ``goal_label``; ``level`` (1–4), ``score`` (level / 4),
        ``level_min``, ``level_max`` (over runs); ``indicators``;
        ``start_message_id`` / ``end_message_id`` (first message of the start
        unit, last message of the end unit); ``start_unit_id`` /
        ``end_unit_id``; ``start_ts`` / ``end_ts``; ``start_min`` /
        ``end_min`` (minutes since the group's first message);
        ``evidence_message_ids``, ``evidence_unit_ids``;
        ``antecedent_unit_id``, ``antecedent_message_id``; ``speakers``
        (pseudonyms of the evidence units) and ``n_speakers``;
        ``justification``; ``agent_cited_unit_id`` /
        ``agent_cited_message_id`` (the consensus citation, lag-free);
        ``agent_influence`` (``uptake``, ``temporal_only``, ``none``);
        ``agent_unit_id``, ``agent_message_id``, ``agent_lag_s`` (seconds),
        ``agent_position`` (``before``, ``within``); ``influenced``;
        ``support`` and ``n_runs_detected``; ``qc_flags``;
        ``prompt_set_version``.
    unstable_patterns : pandas.DataFrame
        Clusters below ``consensus_min_support`` and clusters dropped while
        re-validating the consensus; same columns as `consensus`. Not counted.
    run_patterns : pandas.DataFrame
        Every run's validated patterns: ``run_pattern_id``, ``group``,
        ``goal``, ``run_idx``, ``k``, ``start_id``, ``end_id``,
        ``evidence_ids``, ``antecedent_id``, ``indicators``, ``level``,
        ``agent_uptake_id``, ``justification``, ``qc_flags``, ``source``.
    messages : pandas.DataFrame
        Message ↔ unit ↔ input-row map (DESIGN §10d): ``group``,
        ``row_index``, ``message_id``, ``unit_id``, ``seq``, ``speaker``,
        ``is_agent``, ``timestamp``, ``t_min``.
    run_metadata : dict
        Provenance (DESIGN §10e): ``package_version``, ``schema_version``,
        ``run_id``, ``started_at`` / ``finished_at`` (UTC), ``config``
        (redacted) and ``config_fingerprint``, ``model``, ``effort``,
        ``backend``, ``prompts`` (name, sha256, source),
        ``prompt_set_version``, ``prompt_set_hash``, ``chat_hashes``,
        ``schema_hashes``, ``input_sha256``, ``usage`` (per API attempt) and
        ``usage_total`` (tokens), ``request_ids``, ``cache_keys``,
        ``cost_usd`` (None unless the backend is ``anthropic`` and the model
        is priced), ``price_table_version``, ``qc`` and ``consensus_qc``
        (correction counts), ``no_dispersion``, ``stability`` (Fleiss' kappa
        of evidence membership per goal), ``groups`` (eligibility and status
        per goal), ``n_calls``, ``n_api_calls``, ``n_cached``; after
        `with_influence` or `save`, also ``influence`` (``lag_s``, ``rule``).
    validation : dict
        ``{"issues": [...], "info": [...]}`` from the `ValidationReport`.
    units : pandas.DataFrame
        Internal: ``group``, ``unit_id``, ``order``, ``speaker``,
        ``is_agent``, ``t_start``, ``t_end``, ``t_start_min``,
        ``t_end_min``, ``message_ids``, ``n_messages``.
    groups : pandas.DataFrame
        Internal: one row per group with ``eligibility``, message and student
        counts, ``duration_min``, ``agent_share``, ``flagged_agent_share``,
        ``first_ts`` and ``chat_hash``.
    calls : pandas.DataFrame
        Internal: one row per group × goal × run with ``status`` (``ok``,
        ``failed``, ``refused``), ``error``, ``attempts``, ``from_cache`` and
        ``n_patterns``.
    consensus : pandas.DataFrame
        Internal: consensus patterns in unit ids, before location and
        influence are added.
    influence_lag : datetime.timedelta
    influence_rule : str
    goals : tuple of str

    Examples
    --------
    >>> res = fa.analyze("chats.csv")  # doctest: +SKIP
    >>> res.frequencies_wide()  # doctest: +SKIP
    >>> res.timeseries(every="10min")  # doctest: +SKIP
    >>> res.with_influence(lag="60s").frequencies  # doctest: +SKIP
    >>> res.save("results/")  # doctest: +SKIP
    >>> again = FGPDResults.load("results/")  # doctest: +SKIP
    """

    units: pd.DataFrame
    groups: pd.DataFrame
    calls: pd.DataFrame
    consensus: pd.DataFrame
    unstable_patterns: pd.DataFrame
    run_patterns: pd.DataFrame
    messages: pd.DataFrame
    run_metadata: dict[str, Any]
    validation: dict[str, Any] = field(default_factory=dict)
    influence_lag: timedelta = timedelta(minutes=2)
    influence_rule: str = "uptake"
    goals: tuple[str, ...] = GOALS

    # ------------------------------------------------------------- derived tables
    def __post_init__(self) -> None:
        """Set up the lazy caches of the derived tables."""
        self._patterns: pd.DataFrame | None = None
        self._frequencies: pd.DataFrame | None = None
        # original username -> pseudonym per group; in memory only, never saved by default
        self._pseudonym_map: dict[str, dict[str, str]] = {}

    @property
    def patterns(self) -> pd.DataFrame:
        """Consensus patterns with location and agent influence (see the class Attributes)."""
        if self._patterns is None:
            self._patterns = _locate(
                self.consensus, self.units, self.influence_lag, self.influence_rule
            )
        return self._patterns

    @property
    def frequencies(self) -> pd.DataFrame:
        """One row per group × goal with the metrics (see the class Attributes)."""
        if self._frequencies is None:
            self._frequencies = _frequencies(self)
        return self._frequencies

    def frequencies_wide(self, value: str = "frequency") -> pd.DataFrame:
        """Pivot one column of `frequencies` to groups × goals.

        Parameters
        ----------
        value : str, default "frequency"
            Any column of `frequencies`.

        Returns
        -------
        pandas.DataFrame
            Index ``group``, one column per goal.
        """
        return self.frequencies.pivot(index="group", columns="goal", values=value)

    def with_influence(
        self, lag: str | int | timedelta | None = None, rule: str | None = None
    ) -> FGPDResults:
        """Return the same results with another influence lag or rule. No API call.

        The model's agent citations are lag-free, so `patterns`,
        `frequencies` and `timeseries` can be recomputed for any lag.

        Parameters
        ----------
        lag : str, int or datetime.timedelta, optional
            New ``influence_lag`` in any `parse_duration` form; unchanged when
            omitted.
        rule : {"uptake", "temporal"}, optional
            New ``influence_rule``; unchanged when omitted.

        Returns
        -------
        FGPDResults
            A new object sharing the stored tables; its metadata records the
            lag (seconds) and rule under ``influence``.

        Raises
        ------
        ConfigError
            If `lag` cannot be parsed.
        ValueError
            If `rule` is not ``"uptake"`` or ``"temporal"``.
        """
        new = FGPDResults(
            self.units,
            self.groups,
            self.calls,
            self.consensus,
            self.unstable_patterns,
            self.run_patterns,
            self.messages,
            dict(self.run_metadata),
            self.validation,
            parse_duration(lag) if lag is not None else self.influence_lag,
            rule or self.influence_rule,
            self.goals,
        )
        if new.influence_rule not in ("uptake", "temporal"):
            raise ValueError("rule must be 'uptake' or 'temporal'")
        new.run_metadata["influence"] = {
            "lag_s": new.influence_lag.total_seconds(),
            "rule": new.influence_rule,
        }
        return new

    def timeseries(
        self,
        every: str | int | timedelta = "5min",
        *,
        cumulative: bool = True,
        origin: Literal["group_start", "absolute"] = "group_start",
    ) -> pd.DataFrame:
        """Return the time series of every group × goal, derived from `patterns`. No API call.

        Bin *k* covers minutes ``(k·N, (k+1)·N]`` from the group's first
        message, bin 0 also includes minute 0, and there are
        ``ceil(duration / N)`` bins (at least 1). A pattern counts in the bin
        that contains its end (``end_min``). The last bin of the cumulative
        series equals `frequencies`.

        Parameters
        ----------
        every : str, int or datetime.timedelta, default "5min"
            Bin width N, in any `parse_duration` form; must be positive.
        cumulative : bool, default True
            If True, cumulative values (columns ending in ``_cum``, plus
            ``rate_per_100_cum``); if False, per-bin increments (same names
            without ``_cum``, no rate).
        origin : {"group_start", "absolute"}, default "group_start"
            Bins are always relative to each group's first message;
            ``"absolute"`` adds a ``bin_end`` timestamp column.

        Returns
        -------
        pandas.DataFrame
            One row per group × goal × bin with ``group``, ``goal``,
            ``bin_index``, ``bin_start_min``, ``bin_end_min`` (minutes),
            ``frequency_cum``, ``count_cum``, ``influenced_frequency_cum``,
            ``student_messages_cum`` and ``rate_per_100_cum``. Group × goal
            pairs with NA status have no rows.

        Raises
        ------
        ConfigError
            If `every` cannot be parsed.
        ValueError
            If `every` is zero.
        """
        return _timeseries(self, parse_duration(every), cumulative, origin)

    # ------------------------------------------------------------- persistence
    def save(
        self,
        directory: str | Path,
        *,
        format: Literal["csv", "parquet"] = "csv",
        timeseries_every: tuple[str, ...] = ("5min",),
        include_pseudonym_map: bool = False,
    ) -> dict[str, Path]:
        """Write every table, the metadata and the validation report to a directory.

        Parameters
        ----------
        directory : str or pathlib.Path
            Created if needed; existing files are overwritten.
        format : {"csv", "parquet"}, default "csv"
            Table format. In CSV, list columns are JSON strings; parquet
            needs ``pyarrow``.
        timeseries_every : tuple of str, default ("5min",)
            Write one cumulative ``timeseries_<every>`` table per bin width.
        include_pseudonym_map : bool, default False
            Also write ``pseudonym_map.json`` (real username → pseudonym per
            group). This is personal data: keep the directory private.

        Returns
        -------
        dict of str to pathlib.Path
            Table name → file: ``frequencies``, ``patterns``,
            ``unstable_patterns``, ``run_patterns``, ``messages``, ``units``,
            ``groups``, ``calls``, ``consensus``, ``timeseries_<every>``,
            ``run_metadata`` (JSON), ``validation`` (JSON) and, if requested,
            ``pseudonym_map``.

        Raises
        ------
        ValueError
            If `include_pseudonym_map` is True but the results hold no map
            (maps are never loaded from disk).

        Warns
        -----
        PrivacyWarning
            When the pseudonym map is written.
        """
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        tables = {
            "frequencies": self.frequencies,
            "patterns": self.patterns,
            "unstable_patterns": self.unstable_patterns,
            "run_patterns": self.run_patterns,
            "messages": self.messages,
            "units": self.units,
            "groups": self.groups,
            "calls": self.calls,
            "consensus": self.consensus,
        }
        for every in timeseries_every:
            tables[f"timeseries_{every.replace(' ', '')}"] = self.timeseries(every)
        out: dict[str, Path] = {}
        for name, df in tables.items():
            path = d / f"{name}.{format}"
            _write(df, path, format)
            out[name] = path
        meta = {
            **self.run_metadata,
            "influence": {"lag_s": self.influence_lag.total_seconds(), "rule": self.influence_rule},
            "goals": list(self.goals),
        }
        (d / "run_metadata.json").write_text(
            json.dumps(meta, indent=2, default=str), encoding="utf-8"
        )
        (d / "validation.json").write_text(
            json.dumps(self.validation, indent=2, default=str), encoding="utf-8"
        )
        out["run_metadata"] = d / "run_metadata.json"
        if include_pseudonym_map:
            import warnings

            from .exceptions import PrivacyWarning

            if not self._pseudonym_map:
                raise ValueError(
                    "no pseudonym map in these results (maps are never loaded from disk)"
                )
            warnings.warn(
                "writing the pseudonym map (real usernames)", PrivacyWarning, stacklevel=2
            )
            path = d / "pseudonym_map.json"
            path.write_text(
                json.dumps(self._pseudonym_map, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            out["pseudonym_map"] = path
        out["validation"] = d / "validation.json"
        return out

    @classmethod
    def load(cls, directory: str | Path) -> FGPDResults:
        """Read results written by `save`.

        Parameters
        ----------
        directory : str or pathlib.Path
            A directory written by `save`. Parquet is used when
            ``units.parquet`` exists, else CSV.

        Returns
        -------
        FGPDResults
            With the saved influence lag and rule. CSV columns are cast back
            to their types; ids stay strings (e.g. ``007``). The pseudonym map
            is never loaded.

        Raises
        ------
        FileNotFoundError
            If a stored table or JSON file is missing.
        """
        d = Path(directory)
        fmt = "parquet" if (d / "units.parquet").exists() else "csv"
        t = {
            n: _read(d / f"{n}.{fmt}", fmt)
            for n in (
                "units",
                "groups",
                "calls",
                "consensus",
                "unstable_patterns",
                "run_patterns",
                "messages",
            )
        }
        meta = json.loads((d / "run_metadata.json").read_text(encoding="utf-8"))
        val = json.loads((d / "validation.json").read_text(encoding="utf-8"))
        inf = meta.get("influence", {})
        return cls(
            t["units"],
            t["groups"],
            t["calls"],
            t["consensus"],
            t["unstable_patterns"],
            t["run_patterns"],
            t["messages"],
            meta,
            val,
            timedelta(seconds=float(inf.get("lag_s", 120))),
            inf.get("rule", "uptake"),
            tuple(meta.get("goals", GOALS)),
        )


# =============================================================== helpers


def _write(df: pd.DataFrame, path: Path, fmt: str) -> None:
    """Write one table; list columns become JSON strings (CSV) or lists (parquet)."""
    df = df.copy()
    if fmt == "csv":
        for c in df.columns:
            if c in LIST_COLUMNS:
                df[c] = df[c].map(
                    lambda v: json.dumps(list(v)) if isinstance(v, (list, tuple, np.ndarray)) else v
                )
        df.to_csv(path, index=False)
    else:
        for c in df.columns:
            if c in LIST_COLUMNS:
                df[c] = df[c].map(
                    lambda v: list(v) if isinstance(v, (list, tuple, np.ndarray)) else v
                )
        df.to_parquet(path, index=False)


#: Columns cast to nullable integers when reading CSV results.
INT_COLUMNS = frozenset(
    [
        "order",
        "n_messages",
        "run_idx",
        "k",
        "level",
        "level_min",
        "level_max",
        "n_runs_detected",
        "n_student_messages",
        "n_student_units",
        "n_agent_messages",
        "n_students",
        "row_index",
        "seq",
        "attempts",
        "n_patterns",
        "count",
        "count_strong",
        "influenced_count",
        "uptake_count",
        "temporal_only_count",
        "n_runs_ok",
        "n_unstable",
        "n_speakers",
        "bin_index",
        "count_cum",
        "student_messages_cum",
    ]
)
#: Columns cast to nullable floats when reading CSV results.
FLOAT_COLUMNS = frozenset(
    [
        "t_start_min",
        "t_end_min",
        "t_min",
        "duration_min",
        "agent_share",
        "support",
        "start_min",
        "end_min",
        "score",
        "agent_lag_s",
        "frequency",
        "mean_score",
        "rate_per_100",
        "participation",
        "influenced_frequency",
        "freq_run_mean",
        "freq_run_sd",
        "bin_start_min",
        "bin_end_min",
        "frequency_cum",
        "influenced_frequency_cum",
        "rate_per_100_cum",
    ]
)
#: Columns cast to nullable booleans when reading CSV results.
BOOL_COLUMNS = frozenset(
    [
        "is_agent",
        "flagged_agent_share",
        "from_cache",
        "influenced",
    ]
)
#: Columns parsed as timestamps when reading CSV results.
TIME_COLUMNS = frozenset(
    [
        "t_start",
        "t_end",
        "timestamp",
        "start_ts",
        "end_ts",
        "first_ts",
        "bin_end",
    ]
)


def _read(path: Path, fmt: str) -> pd.DataFrame:
    """Read one table and restore list, numeric, boolean and time columns."""
    if fmt != "csv":
        df = pd.read_parquet(path)
        for c in df.columns:
            if c in LIST_COLUMNS:
                df[c] = df[c].map(lambda v: tuple(v) if v is not None else ())
        return df
    # Read everything as text, then cast known columns: ids such as "007", "NA"
    # or "001" must survive (docs/decisions.md D3).
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    for c in df.columns:
        col = df[c]
        if c in LIST_COLUMNS:
            df[c] = col.map(lambda v: tuple(json.loads(v)) if v else ())
        elif c in INT_COLUMNS:
            df[c] = pd.to_numeric(col.replace("", None), errors="coerce").astype("Int64")
        elif c in FLOAT_COLUMNS:
            df[c] = pd.to_numeric(col.replace("", None), errors="coerce").astype("Float64")
        elif c in BOOL_COLUMNS:
            df[c] = col.map({"True": True, "False": False, "": None}).astype("boolean")
        elif c in TIME_COLUMNS:
            df[c] = pd.to_datetime(col.replace("", None), format="mixed")
        else:
            df[c] = col.astype("string").replace("", pd.NA)
    return df


def _unit_infos(units: pd.DataFrame) -> dict[str, dict[str, UnitInfo]]:
    """Group → unit id → `UnitInfo`, from the ``units`` table."""
    out: dict[str, dict[str, UnitInfo]] = {}
    for r in units.itertuples(index=False):
        out.setdefault(str(r.group), {})[str(r.unit_id)] = UnitInfo(
            str(r.unit_id),
            int(r.order),
            bool(r.is_agent),
            pd.Timestamp(r.t_start),
            pd.Timestamp(r.t_end),
            str(r.message_ids[0]),
        )
    return out


#: Columns of the public ``patterns`` table, in order.
PATTERN_COLUMNS = [
    "pattern_id",
    "group",
    "goal",
    "goal_label",
    "level",
    "score",
    "level_min",
    "level_max",
    "indicators",
    "start_message_id",
    "end_message_id",
    "start_unit_id",
    "end_unit_id",
    "start_ts",
    "end_ts",
    "start_min",
    "end_min",
    "evidence_message_ids",
    "evidence_unit_ids",
    "antecedent_unit_id",
    "antecedent_message_id",
    "speakers",
    "n_speakers",
    "justification",
    "agent_cited_unit_id",
    "agent_cited_message_id",
    "agent_influence",
    "agent_unit_id",
    "agent_message_id",
    "agent_lag_s",
    "agent_position",
    "influenced",
    "support",
    "n_runs_detected",
    "qc_flags",
    "prompt_set_version",
]


def _locate(
    consensus: pd.DataFrame, units: pd.DataFrame, lag: timedelta, rule: str
) -> pd.DataFrame:
    """Build the ``patterns`` table: locate consensus patterns and classify influence."""
    if consensus.empty:
        return pd.DataFrame(columns=PATTERN_COLUMNS)
    infos = _unit_infos(units)
    u = units.set_index(["group", "unit_id"])
    rows = []
    for r in consensus.itertuples(index=False):
        g = str(r.group)
        ui = infos[g]
        ev = tuple(r.evidence_ids)
        start, end = u.loc[(g, r.start_id)], u.loc[(g, r.end_id)]
        inf = classify(
            str(r.start_id),
            ev,
            None if pd.isna(r.agent_uptake_id) else str(r.agent_uptake_id),
            ui,
            lag,
            rule,
        )
        cited = None if pd.isna(r.agent_uptake_id) else str(r.agent_uptake_id)
        ante = None if pd.isna(r.antecedent_id) else str(r.antecedent_id)
        speakers = list(dict.fromkeys(str(u.loc[(g, e)]["speaker"]) for e in ev))
        rows.append(
            {
                "pattern_id": f"{g}:{r.goal}:{int(r.k)}",
                "group": g,
                "goal": r.goal,
                "goal_label": GOAL_LABELS[str(r.goal)],
                "level": int(r.level),
                "score": int(r.level) / 4,
                "level_min": int(r.level_min),
                "level_max": int(r.level_max),
                "indicators": tuple(r.indicators),
                "start_message_id": start["message_ids"][0],
                "end_message_id": end["message_ids"][-1],
                "start_unit_id": r.start_id,
                "end_unit_id": r.end_id,
                "start_ts": start["t_start"],
                "end_ts": end["t_end"],
                "start_min": float(start["t_start_min"]),
                "end_min": float(end["t_end_min"]),
                "evidence_message_ids": tuple(m for e in ev for m in u.loc[(g, e)]["message_ids"]),
                "evidence_unit_ids": ev,
                "antecedent_unit_id": ante,
                "antecedent_message_id": u.loc[(g, ante)]["message_ids"][0] if ante else None,
                "speakers": tuple(speakers),
                "n_speakers": len(speakers),
                "justification": r.justification,
                "agent_cited_unit_id": cited,
                "agent_cited_message_id": ui[cited].first_message_id if cited in ui else None,
                "agent_influence": inf.agent_influence,
                "agent_unit_id": inf.agent_unit_id,
                "agent_message_id": inf.agent_message_id,
                "agent_lag_s": inf.agent_lag_s,
                "agent_position": inf.agent_position,
                "influenced": inf.influenced,
                "support": float(r.support),
                "n_runs_detected": int(r.n_runs_detected),
                "qc_flags": tuple(r.qc_flags),
                "prompt_set_version": r.prompt_set_version,
            }
        )
    df = pd.DataFrame(rows, columns=PATTERN_COLUMNS)
    df["agent_influence"] = pd.Categorical(
        df["agent_influence"], categories=["uptake", "temporal_only", "none"]
    )
    df["agent_position"] = pd.Categorical(df["agent_position"], categories=["before", "within"])
    df["goal"] = pd.Categorical(df["goal"], categories=list(GOALS))
    df["goal_label"] = pd.Categorical(df["goal_label"], categories=[GOAL_LABELS[g] for g in GOALS])
    for c in ("level", "level_min", "level_max", "n_speakers", "n_runs_detected"):
        df[c] = df[c].astype("Int8")
    return df


def group_goal_status(
    calls: pd.DataFrame, groups: pd.DataFrame, goals: tuple[str, ...]
) -> dict[tuple[str, str], str]:
    """Return the status of every group × goal.

    Parameters
    ----------
    calls : pandas.DataFrame
        The ``calls`` table.
    groups : pandas.DataFrame
        The ``groups`` table.
    goals : tuple of str

    Returns
    -------
    dict of (str, str) to str
        The eligibility for ineligible groups; else ``not_selected`` (no
        call), ``ok`` (all runs ok), ``partial`` (some ok), ``refused`` (none
        ok, at least one refused) or ``failed``.
    """
    status: dict[tuple[str, str], str] = {}
    for g in groups.itertuples(index=False):
        for goal in goals:
            if g.eligibility != "eligible":
                status[(str(g.group), goal)] = str(g.eligibility)
                continue
            c = calls[(calls["group"] == g.group) & (calls["goal"] == goal)]
            if c.empty:  # eligible but not requested (analyze(groups=[...]))
                status[(str(g.group), goal)] = "not_selected"
                continue
            ok = int((c["status"] == "ok").sum())
            if ok == len(c):
                status[(str(g.group), goal)] = "ok"
            elif ok:
                status[(str(g.group), goal)] = "partial"
            elif (c["status"] == "refused").any():
                status[(str(g.group), goal)] = "refused"
            else:
                status[(str(g.group), goal)] = "failed"
    return status


def _frequencies(res: FGPDResults) -> pd.DataFrame:
    """Build the ``frequencies`` table."""
    pats = res.patterns
    status = group_goal_status(res.calls, res.groups, res.goals)
    students = res.units[~res.units["is_agent"].astype(bool)]
    rows = []
    for g in res.groups.itertuples(index=False):
        gname = str(g.group)
        n_st = int(g.n_students)
        for goal in res.goals:
            st = status[(gname, goal)]
            base = {
                "group": gname,
                "goal": goal,
                "goal_label": GOAL_LABELS[goal],
                "status": st,
                "n_student_messages": int(g.n_student_messages),
                "n_student_units": int(g.n_student_units),
                "n_agent_messages": int(g.n_agent_messages),
                "n_students": n_st,
                "duration_min": float(g.duration_min),
            }
            if st in NA_STATUSES:
                if st in ("failed", "refused"):
                    base["n_runs_ok"] = 0
                rows.append(base)
                continue
            p = pats[(pats["group"] == gname) & (pats["goal"] == goal)]
            freq = float(p["score"].sum())
            cnt = len(p)
            ev_units = {e for t in p["evidence_unit_ids"] for e in t}
            sp = students[(students["group"] == gname) & students["unit_id"].isin(ev_units)]
            infl = p[p["influenced"].astype(bool)]
            rp = res.run_patterns
            rp = rp[(rp["group"] == gname) & (rp["goal"] == goal)] if not rp.empty else rp
            ok_runs = res.calls[
                (res.calls["group"] == gname)
                & (res.calls["goal"] == goal)
                & (res.calls["status"] == "ok")
            ]["run_idx"].tolist()
            per_run = [
                float(rp[rp["run_idx"] == r]["level"].sum() / 4) if not rp.empty else 0.0
                for r in ok_runs
            ]
            un = res.unstable_patterns
            n_un = int(((un["group"] == gname) & (un["goal"] == goal)).sum()) if not un.empty else 0
            rows.append(
                {
                    **base,
                    "frequency": freq,
                    "count": cnt,
                    "count_strong": int((p["level"] >= 3).sum()),
                    "mean_score": freq / cnt if cnt else math.nan,
                    "rate_per_100": 100 * freq / int(g.n_student_messages)
                    if int(g.n_student_messages)
                    else math.nan,
                    "participation": sp["speaker"].nunique() / n_st if n_st else math.nan,
                    "influenced_frequency": float(infl["score"].sum()),
                    "influenced_count": len(infl),
                    "uptake_count": int((p["agent_influence"] == "uptake").sum()),
                    "temporal_only_count": int((p["agent_influence"] == "temporal_only").sum()),
                    "freq_run_mean": float(np.mean(per_run)) if per_run else math.nan,
                    "freq_run_sd": float(np.std(per_run, ddof=1)) if len(per_run) > 1 else math.nan,
                    "n_runs_ok": len(ok_runs),
                    "n_unstable": n_un,
                }
            )
    cols = [
        "group",
        "goal",
        "goal_label",
        "status",
        "frequency",
        "count",
        "count_strong",
        "mean_score",
        "rate_per_100",
        "n_student_messages",
        "n_student_units",
        "n_agent_messages",
        "n_students",
        "duration_min",
        "participation",
        "influenced_frequency",
        "influenced_count",
        "uptake_count",
        "temporal_only_count",
        "freq_run_mean",
        "freq_run_sd",
        "n_runs_ok",
        "n_unstable",
    ]
    df = pd.DataFrame(rows).reindex(columns=cols)
    for c in (
        "count",
        "count_strong",
        "n_student_messages",
        "n_student_units",
        "n_agent_messages",
        "n_students",
        "influenced_count",
        "uptake_count",
        "temporal_only_count",
        "n_runs_ok",
        "n_unstable",
    ):
        df[c] = df[c].astype("Int32")
    for c in (
        "frequency",
        "mean_score",
        "rate_per_100",
        "duration_min",
        "participation",
        "influenced_frequency",
        "freq_run_mean",
        "freq_run_sd",
    ):
        df[c] = df[c].astype("Float64")
    df["goal"] = pd.Categorical(df["goal"], categories=list(GOALS))
    df["goal_label"] = pd.Categorical(df["goal_label"], categories=[GOAL_LABELS[g] for g in GOALS])
    df["status"] = pd.Categorical(df["status"], categories=list(STATUSES))
    return df


def _timeseries(res: FGPDResults, every: timedelta, cumulative: bool, origin: str) -> pd.DataFrame:
    """Build the time-series table (see `FGPDResults.timeseries`)."""
    n_min = every.total_seconds() / 60
    if n_min <= 0:
        raise ValueError("every must be positive")
    freq = res.frequencies
    pats = res.patterns
    msgs = res.messages
    rows = []
    for g in res.groups.itertuples(index=False):
        gname = str(g.group)
        n_bins = max(1, math.ceil(float(g.duration_min) / n_min))
        first_ts = pd.Timestamp(g.first_ts)
        sm = msgs[(msgs["group"] == gname) & ~msgs["is_agent"].astype(bool)]["t_min"].to_numpy()
        for goal in res.goals:
            st = freq[(freq["group"] == gname) & (freq["goal"] == goal)]["status"].iloc[0]
            if st in NA_STATUSES:
                continue
            p = pats[(pats["group"] == gname) & (pats["goal"] == goal)]
            ends = p["end_min"].to_numpy(dtype=float)
            scores = p["score"].to_numpy(dtype=float)
            infl = p["influenced"].to_numpy(dtype=bool)
            prev = {"f": 0.0, "c": 0, "i": 0.0, "m": 0}
            for k in range(n_bins):
                hi = (k + 1) * n_min
                upto = ends <= hi if k < n_bins - 1 else np.ones_like(ends, dtype=bool)
                m_upto = int((sm <= hi).sum()) if k < n_bins - 1 else len(sm)
                cur = {
                    "f": float(scores[upto].sum()),
                    "c": int(upto.sum()),
                    "i": float(scores[upto & infl].sum()),
                    "m": m_upto,
                }
                vals = cur if cumulative else {key: cur[key] - prev[key] for key in cur}
                row = {
                    "group": gname,
                    "goal": goal,
                    "bin_index": k,
                    "bin_start_min": k * n_min,
                    "bin_end_min": hi,
                    "frequency_cum" if cumulative else "frequency": vals["f"],
                    "count_cum" if cumulative else "count": vals["c"],
                    "influenced_frequency_cum" if cumulative else "influenced_frequency": vals["i"],
                    "student_messages_cum" if cumulative else "student_messages": vals["m"],
                }
                if cumulative:
                    row["rate_per_100_cum"] = 100 * cur["f"] / cur["m"] if cur["m"] else math.nan
                if origin == "absolute":
                    row["bin_end"] = first_ts + timedelta(minutes=hi)
                rows.append(row)
                prev = cur
    sfx = "_cum" if cumulative else ""
    cols = [
        "group",
        "goal",
        "bin_index",
        "bin_start_min",
        "bin_end_min",
        f"frequency{sfx}",
        f"count{sfx}",
        f"influenced_frequency{sfx}",
        f"student_messages{sfx}",
    ]
    if cumulative:
        cols.append("rate_per_100_cum")
    if origin == "absolute":
        cols.append("bin_end")
    df = pd.DataFrame(rows, columns=cols)
    for c in ("bin_index", f"count{sfx}", f"student_messages{sfx}"):
        df[c] = df[c].astype("Int32")
    return df
