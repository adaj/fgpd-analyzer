"""Input validation (DESIGN §9): column mapping, parsing, cleaning and checks.

This is the second stage of the pipeline (input → validation → units →
prompts → requests → runs → post-validation → consensus → influence →
outputs). `check_frame` maps the input columns to canonical names, parses the
timestamps, drops empty and duplicate rows, sorts by time and records every
problem in a `ValidationReport`. The checks are pure: they never raise on bad
data and never call the network, and issue messages never contain chat text.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from .config import REQUIRED_COLUMNS, AnalyzerConfig, ColumnMap
from .exceptions import InputValidationError

Severity = Literal["error", "warning", "info"]
_MAX_ROWS = 20


@dataclass(frozen=True)
class Issue:
    """One finding of input validation.

    Attributes
    ----------
    code : str
        Issue code, e.g. ``"E004"`` or ``"W009"`` (see `ValidationReport`).
    severity : {"error", "warning", "info"}
        Errors block the analysis; warnings and infos do not (unless
        ``strict=True``, which turns warnings into errors).
    message : str
        Description. Never contains chat text.
    group : str or None
        Group concerned, or None for input-wide issues.
    row_indices : tuple of int
        Positions (0-based) of the concerned rows in the input table, at
        most 20.
    hint : str
        Suggested fix, possibly empty.
    """

    code: str
    severity: Severity
    message: str
    group: str | None = None
    row_indices: tuple[int, ...] = ()
    hint: str = ""

    def to_text(self) -> str:
        """Return the issue as one line of text.

        The format is ``CODE severity [group g]: message rows [...]. Hint: ...``.
        """
        where = f" [group {self.group}]" if self.group is not None else ""
        rows = f" rows {list(self.row_indices)}" if self.row_indices else ""
        hint = f" Hint: {self.hint}" if self.hint else ""
        return f"{self.code} {self.severity}{where}: {self.message}{rows}.{hint}"


@dataclass
class ValidationReport:
    """All issues found in one input, plus a per-group summary.

    Returned by `FGPDAnalyzer.validate_input`; also carried by
    `InputValidationError` and stored in ``FGPDResults.validation``.

    Attributes
    ----------
    issues : list of Issue
        Every finding, in the order found.
    info : pandas.DataFrame
        One row per group with ``group``, ``n_messages``, ``n_student``,
        ``n_agent``, ``n_students``, ``n_student_units``, ``duration_min``
        (minutes), ``agent_share`` (fraction of messages), ``flagged_agent_share``,
        ``est_tokens`` (estimated prompt tokens) and ``eligibility``
        (``eligible``, ``insufficient_data``, ``too_long`` or ``excluded``).
        Empty when the input could not be prepared.

    Notes
    -----
    Issue codes (DESIGN §9, extended in docs/decisions.md):

    ====  ========  ==========================================================
    Code  Severity  Meaning
    ====  ========  ==========================================================
    E001  error     Required column missing, or a mapped column not found
    E002  error     Several source columns match one canonical column
    E003  error     Missing group, username or timestamp
    E004  error     Unparseable timestamp
    E006  error     Mixed timezone-aware and naive timestamps
    E007  error     No student messages in the whole input
    E008  error     Duplicate or empty ``message_id`` within a group
    E009  error     Reserved; invalid configuration raises `ConfigError`
    W001  warning   Empty text (row dropped)
    W002  warning   Exact duplicate rows (dropped)
    W003  warning   Too few student messages or students → ``insufficient_data``
    W004  warning   Group has no agent messages
    W005  warning   Timestamps out of order, stable-sorted (error if ``sort=False``)
    W006  warning   Identical timestamps within a group (file order kept)
    W007  warning   Gap longer than 60 minutes within a group
    W008  warning   Agent pattern matches no username, or > 50 % of a group's
    W009  warning   Agent share above ``max_agent_share`` (group flagged)
    W010  warning   Estimated prompt above ``max_prompt_tokens`` → ``too_long``
    W013  warning   Names shorter than ``min_name_len_in_text`` not replaced in text
    W014  warning   Usernames look like pseudonyms; other prefixes used
    W015  warning   Day/month order of some timestamps was guessed
    W016  warning   Words agents use to address someone replaced by ``[NAME]``
    W017  warning   Words one letter away from a known name replaced
    I001  info      Extra columns ignored
    I002  info      ``message_id`` column empty; ids generated
    ====  ========  ==========================================================
    """

    issues: list[Issue] = field(default_factory=list)
    info: pd.DataFrame = field(default_factory=pd.DataFrame)  # one row per group

    def add(
        self,
        code: str,
        severity: Severity,
        message: str,
        *,
        group: str | None = None,
        rows: object = (),
        hint: str = "",
    ) -> None:
        """Append an issue; `rows` is capped at the first 20 indices."""
        idx = tuple(int(r) for r in list(rows)[:_MAX_ROWS])  # type: ignore[call-overload]
        self.issues.append(Issue(code, severity, message, group, idx, hint))

    @property
    def errors(self) -> list[Issue]:
        """Issues with severity ``"error"``."""
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        """Issues with severity ``"warning"``."""
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        """True when there is no error (warnings allowed)."""
        return not self.errors

    def codes(self) -> list[str]:
        """Return the code of every issue, in order (duplicates kept)."""
        return [i.code for i in self.issues]

    def to_text(self, errors_only: bool = False) -> str:
        """Return the issues as text, one per line.

        Parameters
        ----------
        errors_only : bool, default False
            List errors only.

        Returns
        -------
        str
            ``"no issues"`` when there is nothing to list.
        """
        items = self.errors if errors_only else self.issues
        return "\n".join(i.to_text() for i in items) or "no issues"

    def to_dataframe(self) -> pd.DataFrame:
        """Return the issues as a table with the `Issue` fields as columns."""
        return pd.DataFrame(
            [i.__dict__ for i in self.issues],
            columns=["code", "severity", "message", "group", "row_indices", "hint"],
        )

    def raise_if_errors(self) -> None:
        """Raise `InputValidationError` if there is any error.

        Raises
        ------
        InputValidationError
            If `ok` is False.
        """
        if not self.ok:
            raise InputValidationError(self)

    def escalate(self) -> None:
        """Turn every warning into an error (used for ``strict=True``)."""
        self.issues = [
            Issue(i.code, "error", i.message, i.group, i.row_indices, i.hint)
            if i.severity == "warning"
            else i
            for i in self.issues
        ]


def parse_timestamps(
    s: pd.Series, missing: pd.Series, *, dayfirst: bool = False
) -> tuple[pd.Series, list[int], list[int], list[int], list[int]]:
    """Parse timestamps one value at a time, never raising.

    Parameters
    ----------
    s : pandas.Series
        Timestamp strings.
    missing : pandas.Series of bool
        True where the value is missing (left as ``NaT``, not reported).
    dayfirst : bool, default False
        Passed to ``pd.to_datetime`` for ambiguous dates.

    Returns
    -------
    parsed : pandas.Series
        Parsed values. When all values are timezone-aware they are converted
        to the offset of the first one, so clock times stay local.
    aware, naive, bad, guessed : list of int
        Positions of timezone-aware, naive and unparseable values, and of
        values whose day/month order pandas had to guess.
    """
    values: list[Any] = []
    aware: list[int] = []
    naive: list[int] = []
    bad: list[int] = []
    order_warn: list[int] = []
    for i, (v, miss) in enumerate(zip(s.tolist(), missing.tolist(), strict=True)):
        if miss or v is None or v is pd.NA:
            values.append(pd.NaT)
            continue
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                t = pd.Timestamp(pd.to_datetime(v, dayfirst=dayfirst))
            if any("dayfirst" in str(w.message) for w in caught):
                order_warn.append(i)
        except (ValueError, TypeError, OverflowError):
            t = pd.NaT
        if t is pd.NaT or pd.isna(t):
            bad.append(i)
            values.append(pd.NaT)
            continue
        (aware if t.tzinfo is not None else naive).append(i)
        values.append(t)
    if aware and not naive:
        tz = values[aware[0]].tzinfo
        out = pd.Series([pd.NaT if pd.isna(v) else v.tz_convert(tz) for v in values], index=s.index)
        return pd.to_datetime(out, utc=True).dt.tz_convert(tz), aware, naive, bad, order_warn
    if aware:
        return pd.Series(values, index=s.index), aware, naive, bad, order_warn
    return pd.to_datetime(pd.Series(values, index=s.index)), aware, naive, bad, order_warn


def _closest(name: str, options: list[str]) -> list[str]:
    """Up to three column names similar to `name`, for the E001 hint."""
    import difflib

    return difflib.get_close_matches(name, options, n=3, cutoff=0.4)


def check_frame(
    raw: pd.DataFrame, columns: ColumnMap, config: AnalyzerConfig
) -> tuple[ValidationReport, pd.DataFrame | None]:
    """Map columns, parse timestamps, clean rows and record every issue.

    Parameters
    ----------
    raw : pandas.DataFrame
        Raw input from `fgpd_analyzer.io.read_source`.
    columns : ColumnMap
        Explicit column mapping (unmapped columns are found by name).
    config : AnalyzerConfig
        Uses ``sort`` and ``dayfirst``.

    Returns
    -------
    report : ValidationReport
        Issues E001–E008, W001, W002, W005–W007, W015, I001 and I002. Role,
        unit and eligibility issues are added later by
        `fgpd_analyzer.preprocess.prepare`.
    clean : pandas.DataFrame or None
        Canonical table with ``group``, ``username``, ``timestamp``,
        ``text``, optionally ``message_id``, and ``row_index`` (row in
        `raw`); text columns normalized to NFC and stripped; empty and
        duplicate rows dropped; sorted by group and time when ``sort=True``.
        None when columns or timestamps make the input unusable.
    """
    rep = ValidationReport()
    mapping, problems = columns.resolve(raw.columns)
    for canon, problem in problems:
        code = "E001" if problem.startswith("mapped column") else "E002"
        rep.add(
            code,
            "error",
            f"column {canon!r}: {problem}",
            hint=f"pass columns={{'{canon}': '<source column>'}}",
        )
    flagged = {c for c, _ in problems}
    for canon in REQUIRED_COLUMNS:
        if canon not in mapping and canon not in flagged:
            sugg = _closest(canon, [str(c) for c in raw.columns])
            rep.add(
                "E001",
                "error",
                f"required column {canon!r} not found",
                hint=(f"closest: {sugg}; " if sugg else "") + f"pass columns={{'{canon}': ...}}",
            )
    if not rep.ok:
        return rep, None
    extra = [c for c in raw.columns if c not in mapping.values()]
    if extra:
        rep.add("I001", "info", f"{len(extra)} extra column(s) ignored")

    df = pd.DataFrame({canon: raw[src] for canon, src in mapping.items()})
    df["row_index"] = range(len(df))
    for c in ("group", "username", "text", "message_id"):
        if c in df:
            df[c] = df[c].astype("string").str.normalize("NFC").str.strip()
            df.loc[df[c] == "", c] = pd.NA

    # E003 missing keys
    for c in ("group", "username", "timestamp"):
        miss = (
            df[c].isna()
            if c != "timestamp"
            else df[c].isna() | (df[c].astype("string").str.strip() == "")
        )
        if miss.any():
            rep.add(
                "E003",
                "error",
                f"{int(miss.sum())} row(s) with missing {c}",
                rows=df.loc[miss, "row_index"],
            )

    # timestamps: E006 mixed tz, E004 unparseable (never raises)
    ts_missing = df["timestamp"].isna() | (df["timestamp"].astype("string").str.strip() == "")
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        parsed, aware_rows, naive_rows, bad_rows, order_rows = parse_timestamps(
            df["timestamp"].astype("string").str.strip(), ts_missing, dayfirst=config.dayfirst
        )
        if aware_rows and naive_rows:
            rep.add(
                "E006",
                "error",
                "mixed timezone-aware and naive timestamps",
                rows=df["row_index"].iloc[naive_rows],
                hint="make all timestamps naive or all timezone-aware",
            )
            return rep, None
        if bad_rows:
            rep.add(
                "E004",
                "error",
                f"{len(bad_rows)} unparseable timestamp(s)",
                rows=df["row_index"].iloc[bad_rows],
                hint="use ISO 8601, e.g. 2026-09-17 19:38:23",
            )
        if order_rows:
            rep.add(
                "W015",
                "warning",
                f"{len(order_rows)} timestamp(s) with a guessed day/month order",
                rows=df["row_index"].iloc[order_rows],
                hint="set dayfirst=True for dd/mm/yyyy data, or use ISO 8601",
            )
        df["timestamp"] = parsed
    if not rep.ok:
        return rep, None

    # W001 empty text
    empty = df["text"].isna()
    if empty.any():
        rep.add(
            "W001",
            "warning",
            f"{int(empty.sum())} empty message(s) dropped",
            rows=df.loc[empty, "row_index"],
        )
        df = df.loc[~empty]

    # W002 exact duplicates
    key = ["group", "username", "timestamp", "text"]
    if "message_id" in df:
        key.append("message_id")
    dup = df.duplicated(subset=key, keep="first")
    if dup.any():
        rep.add(
            "W002",
            "warning",
            f"{int(dup.sum())} exact duplicate row(s) dropped",
            rows=df.loc[dup, "row_index"],
        )
        df = df.loc[~dup]

    # E008 duplicate message ids (an all-empty id column is ignored)
    if "message_id" in df and df["message_id"].isna().all():
        rep.add("I002", "info", "message_id column is empty; ids are generated")
        df = df.drop(columns="message_id")
    if "message_id" in df:
        d = df.duplicated(subset=["group", "message_id"], keep=False) & df["message_id"].notna()
        if d.any():
            rep.add(
                "E008", "error", "duplicate message_id within a group", rows=df.loc[d, "row_index"]
            )
        if df["message_id"].isna().any():
            rep.add(
                "E008",
                "error",
                "message_id column has empty values",
                rows=df.loc[df["message_id"].isna(), "row_index"],
            )

    # W005 ordering
    for g, sub in df.groupby("group", sort=False):
        if not sub["timestamp"].is_monotonic_increasing:
            if config.sort:
                rep.add("W005", "warning", "timestamps out of order; stable-sorted", group=str(g))
            else:
                rep.add("W005", "error", "timestamps out of order and sort=False", group=str(g))
    if config.sort:
        df = df.sort_values(["group", "timestamp"], kind="stable")
    df = df.reset_index(drop=True)

    # W006 ties, W007 long gaps
    for g, sub in df.groupby("group", sort=False):
        ties = sub["timestamp"].duplicated(keep=False)
        if ties.any():
            rep.add(
                "W006",
                "warning",
                f"{int(ties.sum())} message(s) share a timestamp; file order kept",
                group=str(g),
                rows=sub.loc[ties, "row_index"],
            )
        gaps = sub["timestamp"].diff().dt.total_seconds()
        if (gaps > 3600).any():
            rep.add(
                "W007",
                "warning",
                f"{int((gaps > 3600).sum())} gap(s) longer than 60 min",
                group=str(g),
            )
    return rep, df
