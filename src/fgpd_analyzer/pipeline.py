"""Input pipeline: source -> validated, prepared groups (no network)."""

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
    report: ValidationReport
    groups: dict[str, PreparedGroup]

    @property
    def eligible(self) -> list[str]:
        return [g for g, p in self.groups.items() if p.eligibility == "eligible"]

    def messages(self) -> pd.DataFrame:
        if not self.groups:
            return pd.DataFrame()
        return pd.concat([p.messages for p in self.groups.values()], ignore_index=True)


def prepare_input(
    source: Any,
    config: AnalyzerConfig,
    columns: ColumnMap | Mapping[str, str] | None = None,
    **read_kw: Any,
) -> PreparedInput:
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
