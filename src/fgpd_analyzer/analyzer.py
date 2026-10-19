"""`FGPDAnalyzer`: the public entry point."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from . import codebook as cb
from .config import AnalyzerConfig, ColumnMap
from .exceptions import ConfigError, PromptError
from .pipeline import PreparedInput, prepare_input
from .prompts.registry import PromptRegistry, PromptTemplate
from .render import render_transcript
from .schemas import SCHEMA_VERSION
from .validation import ValidationReport

Columns = ColumnMap | Mapping[str, str] | None


class FGPDAnalyzer:
    """Measure the Four Goals for Productive Discussion in group chats.

    >>> fa = FGPDAnalyzer(agent_pattern="Clair", influence_lag="90s")
    >>> fa.validate_input("chats.tsv").ok
    """

    def __init__(
        self,
        config: AnalyzerConfig | None = None,
        *,
        model: str | None = None,
        prompts: PromptRegistry | Mapping[str, str | Path] | Path | str | None = None,
        **overrides: Any,
    ) -> None:
        base = config or AnalyzerConfig()
        if model is not None:
            overrides["model"] = model
        try:
            self.config = base.replace(**overrides) if overrides else base
        except (TypeError, ValueError) as e:
            raise ConfigError(str(e)) from e
        if isinstance(prompts, PromptRegistry):
            self.prompts = prompts
        elif prompts is not None:
            self.prompts = PromptRegistry(
                dict(prompts) if isinstance(prompts, Mapping) else prompts
            )
        else:
            self.prompts = PromptRegistry(self.config.prompts_dir)

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

    # ------------------------------------------------------------------ prompts
    def list_prompts(self) -> pd.DataFrame:
        """Name, version, hash, source (builtin/custom), variables and description."""
        rows = []
        for n in self.prompts.names():
            p = self.prompts.get(n)
            rows.append(
                {
                    "name": n,
                    "version": p.version,
                    "sha256": p.sha256[:12],
                    "source": p.source,
                    "variables": ", ".join(sorted(p.variables)),
                    "description": p.description,
                }
            )
        return pd.DataFrame(rows)

    def show_prompt(self, name: str, *, rendered: bool = False, goal: str | None = None) -> str:
        """The template text, or the text the model receives (`rendered=True`)."""
        if not rendered:
            return self.prompts.get(name).text
        if name == "task_detect" and goal is None:
            raise PromptError("task_detect is rendered per goal; pass goal='G1'..'G4'")
        return cb.render_prompt(self.prompts, name, self.config, goal=goal)

    def get_prompt(self, name: str) -> PromptTemplate:
        return self.prompts.get(name)

    def set_prompt(
        self, name: str, template: str | Path, *, description: str = ""
    ) -> PromptTemplate:
        """Replace a base prompt. It must use the same template variables.
        Results produced with custom prompts are marked as non-comparable."""
        return self.prompts.set(name, template, description=description)

    def reset_prompts(self, names: list[str] | None = None) -> None:
        self.prompts.reset(names)

    def export_prompts(self, directory: Path | str) -> list[Path]:
        return self.prompts.export(Path(directory))

    @property
    def prompt_set_version(self) -> str:
        return self.prompts.version()

    @property
    def prompt_set_hash(self) -> str:
        return cb.prompt_set_hash(self.prompts, self.config, SCHEMA_VERSION)

    def export_codebook(self, path: Path | str) -> Path:
        """Human codebook generated from the current prompts (DESIGN §12)."""
        from . import __version__

        return cb.export_codebook(
            self.prompts, self.config, Path(path), SCHEMA_VERSION, __version__
        )

    def export_coding_sheet(
        self,
        source: Any,
        path: Path | str,
        *,
        groups: list[str] | None = None,
        coder: str = "",
        columns: Columns = None,
        **read_kw: Any,
    ) -> Path:
        """Excel workbook for a human coder: transcript units plus one form per goal."""
        from .coding import export_coding_sheet

        prep = self.prepare(source, columns=columns, **read_kw)
        prep.report.raise_if_errors()
        return export_coding_sheet(
            prep, Path(path), groups=groups, prompt_set_hash=self.prompt_set_hash, coder=coder
        )
