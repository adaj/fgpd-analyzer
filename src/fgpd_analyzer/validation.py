"""Input validation (DESIGN §9). Pure checks; messages never contain chat text."""

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
    code: str
    severity: Severity
    message: str
    group: str | None = None
    row_indices: tuple[int, ...] = ()
    hint: str = ""

    def to_text(self) -> str:
        where = f" [group {self.group}]" if self.group is not None else ""
        rows = f" rows {list(self.row_indices)}" if self.row_indices else ""
        hint = f" Hint: {self.hint}" if self.hint else ""
        return f"{self.code} {self.severity}{where}: {self.message}{rows}.{hint}"


@dataclass
class ValidationReport:
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
        idx = tuple(int(r) for r in list(rows)[:_MAX_ROWS])  # type: ignore[call-overload]
        self.issues.append(Issue(code, severity, message, group, idx, hint))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def to_text(self, errors_only: bool = False) -> str:
        items = self.errors if errors_only else self.issues
        return "\n".join(i.to_text() for i in items) or "no issues"

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(
            [i.__dict__ for i in self.issues],
            columns=["code", "severity", "message", "group", "row_indices", "hint"],
        )

    def raise_if_errors(self) -> None:
        if not self.ok:
            raise InputValidationError(self)

    def escalate(self) -> None:
        """`strict=True`: every warning becomes an error."""
        self.issues = [
            Issue(i.code, "error", i.message, i.group, i.row_indices, i.hint)
            if i.severity == "warning"
            else i
            for i in self.issues
        ]


def parse_timestamps(
    s: pd.Series, missing: pd.Series, *, dayfirst: bool = False
) -> tuple[pd.Series, list[int], list[int], list[int], list[int]]:
    """Parse value by value. Returns (parsed, aware positions, naive positions,
    unparseable positions, positions whose day/month order was guessed). Aware
    values are converted to the offset of the first aware value, so clock times
    stay local."""
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
    import difflib

    return difflib.get_close_matches(name, options, n=3, cutoff=0.4)


def check_frame(
    raw: pd.DataFrame, columns: ColumnMap, config: AnalyzerConfig
) -> tuple[ValidationReport, pd.DataFrame | None]:
    """Map columns, parse and clean. Returns the report and the canonical frame
    (``group, username, timestamp, text, message_id?, row_index``), or None when
    the input cannot be used at all."""
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
