"""`FGPDAnalyzer`: the public entry point."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from .config import AnalyzerConfig, ColumnMap
from .exceptions import ConfigError
from .pipeline import PreparedInput, prepare_input
from .render import render_transcript
from .validation import ValidationReport

Columns = ColumnMap | Mapping[str, str] | None


class FGPDAnalyzer:
    """Measure the Four Goals for Productive Discussion in group chats.

    >>> fa = FGPDAnalyzer(agent_pattern="Clair", influence_lag="90s")
    >>> fa.validate_input("chats.tsv").ok
    """

    def __init__(
        self, config: AnalyzerConfig | None = None, *, model: str | None = None, **overrides: Any
    ) -> None:
        base = config or AnalyzerConfig()
        if model is not None:
            overrides["model"] = model
        try:
            self.config = base.replace(**overrides) if overrides else base
        except (TypeError, ValueError) as e:
            raise ConfigError(str(e)) from e

    def __repr__(self) -> str:
        c = self.config
        return (
            f"FGPDAnalyzer(model={c.model!r}, n_runs={c.n_runs}, "
            f"agent_pattern={c.agent_pattern!r}, "
            f"influence_lag={c.influence_lag}, goals={c.goals})"
        )

    # ------------------------------------------------------------------ input
    def prepare(self, source: Any, *, columns: Columns = None, **read_kw: Any) -> PreparedInput:
        return prepare_input(source, self.config, columns, **read_kw)

    def validate_input(
        self, source: Any, *, columns: Columns = None, **read_kw: Any
    ) -> ValidationReport:
        """Check the input without any network call. Never raises on bad data."""
        return self.prepare(source, columns=columns, **read_kw).report

    def load(self, source: Any, *, columns: Columns = None, **read_kw: Any) -> pd.DataFrame:
        """Message table after validation and preprocessing (no text column)."""
        prep = self.prepare(source, columns=columns, **read_kw)
        prep.report.raise_if_errors()
        return prep.messages()

    def preview(self, source: Any, *, group: str, columns: Columns = None, **read_kw: Any) -> str:
        """The exact (pseudonymized) transcript that would be sent for `group`."""
        prep = self.prepare(source, columns=columns, **read_kw)
        prep.report.raise_if_errors()
        if group not in prep.groups:
            raise KeyError(f"group {group!r} not found; available: {list(prep.groups)}")
        return render_transcript(prep.groups[group])
