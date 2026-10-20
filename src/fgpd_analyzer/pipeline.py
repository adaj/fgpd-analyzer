"""Input stage: from a source table to validated, prepared groups. No network.

`prepare_input` chains the first stages of the pipeline (input → validation →
units and pseudonyms → eligibility): it reads the source (`io`), checks and
cleans it (`validation`), then builds the units, pseudonyms and eligibility of
every group (`preprocess`). Its result, `PreparedInput`, is what the prompt,
request, cost-estimate and coding-sheet stages consume.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .config import AnalyzerConfig, ColumnMap
from .exceptions import PrivacyWarning
from .io import read_source
from .preprocess import PreparedGroup, prepare
from .validation import ValidationReport, check_frame


@dataclass
class PreparedInput:
    """Validated input, ready for prompts and requests.

    Attributes
    ----------
    report : ValidationReport
        Every issue found while reading, validating and preprocessing.
    groups : dict of str to PreparedGroup
        One prepared group per group id, in sorted order. Empty when the
        report has errors.
    """

    report: ValidationReport
    groups: dict[str, PreparedGroup]

    @property
    def eligible(self) -> list[str]:
        """Ids of the groups whose eligibility is ``"eligible"``."""
        return [g for g, p in self.groups.items() if p.eligibility == "eligible"]

    def messages(self) -> pd.DataFrame:
        """Return the message tables of all groups, concatenated (no text column)."""
        if not self.groups:
            return pd.DataFrame()
        return pd.concat([p.messages for p in self.groups.values()], ignore_index=True)


def prepare_input(
    source: Any,
    config: AnalyzerConfig,
    columns: ColumnMap | Mapping[str, str] | None = None,
    **read_kw: Any,
) -> PreparedInput:
    """Read, validate and preprocess an input. No network.

    Parameters
    ----------
    source : pandas.DataFrame, str or path-like
        The chat table or a file (see `fgpd_analyzer.io.read_source`).
    config : AnalyzerConfig
        Configuration (roles, units, privacy, eligibility, strictness).
    columns : ColumnMap or mapping, optional
        Explicit column mapping.
    **read_kw
        Passed to the pandas reader.

    Returns
    -------
    PreparedInput
        Groups are prepared only when validation found no errors. With
        ``config.strict`` every warning is escalated to an error afterwards.

    Raises
    ------
    FileNotFoundError
        If `source` is a missing path.
    ConfigError
        If the file type is unsupported or `columns` has unknown keys.

    Warns
    -----
    PrivacyWarning
        If ``config.pseudonymize`` is False.
    """
    if not config.pseudonymize:
        warnings.warn(
            "pseudonymize=False: real names will be sent to the model and kept in outputs",
            PrivacyWarning,
            stacklevel=3,
        )
    raw = read_source(source, **read_kw)
    report, clean = check_frame(raw, ColumnMap.coerce(columns), config)
    groups: dict[str, PreparedGroup] = {}
    if clean is not None and report.ok:
        groups = prepare(clean, config, report)
    if config.strict:
        report.escalate()
    return PreparedInput(report, groups)
