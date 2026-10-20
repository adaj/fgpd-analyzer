"""`FGPDAnalyzer`, the public entry point of the package.

The analyzer binds one `AnalyzerConfig`, one `PromptRegistry` and, optionally,
one LLM backend, and exposes every step of the pipeline as a method: reading
and validating the input (`validate_input`, `load`, `preview`), inspecting
and replacing prompts (`list_prompts`, `show_prompt`, `set_prompt`, ...),
exporting material for human coders (`export_codebook`,
`export_coding_sheet`), forecasting cost (`estimate_cost`) and running the
analysis (`analyze`, `analyze_async`). It sits on top of the whole pipeline:
input → units → prompts → requests → runs → post-validation → consensus →
influence → outputs. The work itself is done in `pipeline`, `analysis` and
the modules they call.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from . import codebook as cb
from .config import AnalyzerConfig, ColumnMap
from .exceptions import ConfigError, PromptError
from .io import read_source
from .pipeline import PreparedInput, prepare_input
from .prompts.registry import PromptRegistry, PromptTemplate
from .render import render_transcript
from .schemas import SCHEMA_VERSION
from .validation import ValidationReport

Columns = ColumnMap | Mapping[str, str] | None


class FGPDAnalyzer:
    """Measure the Four Goals for Productive Discussion in group chats.

    An analyzer holds an immutable configuration, a prompt registry and an
    optional backend. Every method that takes a ``source`` reads it again, so
    the same analyzer can be used for several files.

    Parameters
    ----------
    config : AnalyzerConfig, optional
        Base configuration. Defaults to ``AnalyzerConfig()``.
    model : str, optional
        Shortcut for ``model=...`` in `overrides`.
    prompts : PromptRegistry, mapping, path or str, optional
        Prompts to use. A `PromptRegistry` is used as is; a mapping
        ``{prompt name: template text or Path}`` or a directory of
        ``<name>.md.j2`` / ``<name>.md`` / ``<name>.txt`` files overrides the
        matching built-in prompts. When omitted, ``config.prompts_dir`` is used
        if set, else the built-in prompts.
    backend : LLMBackend or str, optional
        A backend instance (`AnthropicBackend`, `MockBackend`,
        `ReplayBackend` or any object following `LLMBackend`). A string
        (``"anthropic"``, ``"mock"`` or ``"replay"``) is treated as the
        ``backend`` configuration field instead. When omitted, the backend is
        built from the configuration at analysis time.
    **overrides
        Any `AnalyzerConfig` field. Applied on top of `config` with
        `AnalyzerConfig.replace`.

    Attributes
    ----------
    config : AnalyzerConfig
        The effective configuration.
    prompts : PromptRegistry
        The prompt registry used for rendering, hashing and export.

    Raises
    ------
    ConfigError
        If an override is not a valid configuration value or combination.
    PromptError
        If `prompts` names an unknown prompt, a missing directory or an
        invalid template.
    PromptSchemaMismatch
        If a custom prompt does not use exactly the template variables of the
        built-in prompt it replaces.

    See Also
    --------
    AnalyzerConfig : every option, its default and its effect.
    fgpd_analyzer.results.FGPDResults : what `analyze` returns.

    Notes
    -----
    The methods differ in what they do with the network:

    - `validate_input`, `load`, `preview`, `estimate_cost`, the prompt methods
      and the export methods never contact the API.
    - `analyze` and `analyze_async` send one request per eligible
      group × goal × run, except for responses already in the local cache.
      ``analyze(dry_run=True)`` sends nothing.

    Chat transcripts are sent to the Anthropic API when the backend is
    ``"anthropic"``. Use only data whose participants consented to this
    processing.

    Examples
    --------
    >>> from fgpd_analyzer import FGPDAnalyzer
    >>> fa = FGPDAnalyzer(agent_pattern="Clair", influence_lag="90s", n_runs=3)
    >>> report = fa.validate_input("chats.csv")  # doctest: +SKIP
    >>> report.ok  # doctest: +SKIP
    True
    >>> print(fa.estimate_cost("chats.csv"))  # doctest: +SKIP
    >>> print(fa.preview("chats.csv", group="g01"))  # doctest: +SKIP
    >>> res = fa.analyze("chats.csv")  # doctest: +SKIP
    >>> res.frequencies[["group", "goal", "frequency"]]  # doctest: +SKIP
    >>> res.save("results/")  # doctest: +SKIP

    Offline, with synthetic model output:

    >>> fa = FGPDAnalyzer(backend="mock", agent_pattern="Clair")
    >>> res = fa.analyze(chats_df)  # doctest: +SKIP
    """

    def __init__(
        self,
        config: AnalyzerConfig | None = None,
        *,
        model: str | None = None,
        prompts: PromptRegistry | Mapping[str, str | Path] | Path | str | None = None,
        backend: Any = None,
        **overrides: Any,
    ) -> None:
        if isinstance(backend, str):  # "anthropic" | "mock" | "replay"
            overrides["backend"] = backend
            backend = None
        base = config or AnalyzerConfig()
        if model is not None:
            overrides["model"] = model
        try:
            self.config = base.replace(**overrides) if overrides else base
        except (TypeError, ValueError) as e:
            raise ConfigError(str(e)) from e
        self._backend = backend
        if isinstance(prompts, PromptRegistry):
            self.prompts = prompts
        elif prompts is not None:
            self.prompts = PromptRegistry(
                dict(prompts) if isinstance(prompts, Mapping) else prompts
            )
        else:
            self.prompts = PromptRegistry(self.config.prompts_dir)

    def __repr__(self) -> str:
        """Summarize the main settings (never names or the API key)."""
        c = self.config
        return (
            f"FGPDAnalyzer(model={c.model!r}, n_runs={c.n_runs}, "
            f"agent_pattern={c.agent_pattern!r}, "
            f"influence_lag={c.influence_lag}, goals={c.goals})"
        )

    # ------------------------------------------------------------------ input
    def prepare(self, source: Any, *, columns: Columns = None, **read_kw: Any) -> PreparedInput:
        """Read, validate and preprocess the input. No network.

        This is the shared first step of every other method. Most users call
        `validate_input`, `load` or `preview` instead.

        Parameters
        ----------
        source : pandas.DataFrame, str or path-like
            The chat table, or a ``.csv``, ``.tsv``, ``.txt``, ``.xlsx`` or
            ``.parquet`` file with one row per message.
        columns : ColumnMap or mapping, optional
            Canonical column → source column, e.g. ``{"text": "Mensagem"}``.
            Columns not given are matched by name (see `ColumnMap`).
        **read_kw
            Passed to the pandas reader (``pd.read_csv``, ``pd.read_excel`` or
            ``pd.read_parquet``).

        Returns
        -------
        PreparedInput
            The validation report and, when there are no errors, one
            `PreparedGroup` (units, pseudonyms, eligibility) per group.

        Raises
        ------
        FileNotFoundError
            If `source` is a path that does not exist.
        ConfigError
            If the file type is not supported or `columns` names an unknown
            canonical column.

        Warns
        -----
        PrivacyWarning
            If ``pseudonymize=False``.
        """
        return prepare_input(source, self.config, columns, **read_kw)

    def validate_input(
        self, source: Any, *, columns: Columns = None, **read_kw: Any
    ) -> ValidationReport:
        """Check the input and report every problem. No network, no cost.

        Runs column mapping, timestamp parsing, cleaning, role detection,
        pseudonymization, unit building and eligibility checks, and collects
        every finding as an `Issue`. Problems in the data are reported, not
        raised, so the report always lists all of them at once.

        Parameters
        ----------
        source : pandas.DataFrame, str or path-like
            The chat table or a file (see `prepare`).
        columns : ColumnMap or mapping, optional
            Explicit column mapping (see `prepare`).
        **read_kw
            Passed to the pandas reader.

        Returns
        -------
        ValidationReport
            Every issue found (codes listed in `ValidationReport`), and a
            per-group summary in ``report.info``. ``report.ok`` is False when
            there is at least one error; `analyze` would then raise
            `InputValidationError`.

        Raises
        ------
        FileNotFoundError
            If `source` is a path that does not exist.
        ConfigError
            If the file type is not supported or `columns` names an unknown
            canonical column.

        See Also
        --------
        estimate_cost : forecast the cost of the same input.
        preview : the transcript the model would receive for one group.

        Notes
        -----
        With ``strict=True`` every warning is reported as an error.
        """
        return self.prepare(source, columns=columns, **read_kw).report

    def load(self, source: Any, *, columns: Columns = None, **read_kw: Any) -> pd.DataFrame:
        """Return the message table after validation and preprocessing. No network.

        Parameters
        ----------
        source : pandas.DataFrame, str or path-like
            The chat table or a file (see `prepare`).
        columns : ColumnMap or mapping, optional
            Explicit column mapping (see `prepare`).
        **read_kw
            Passed to the pandas reader.

        Returns
        -------
        pandas.DataFrame
            One row per kept message, sorted by group and time, with columns
            ``group``, ``row_index`` (row in the input), ``message_id``,
            ``unit_id``, ``seq`` (position in the group), ``speaker``
            (pseudonym), ``is_agent``, ``timestamp`` and ``t_min`` (minutes
            since the group's first message). There is no text column.

        Raises
        ------
        InputValidationError
            If validation found errors; ``.report`` holds every issue.
        """
        prep = self.prepare(source, columns=columns, **read_kw)
        prep.report.raise_if_errors()
        return prep.messages()

    def name_candidates(self, source: Any, *, columns: Columns = None, **read_kw: Any) -> list[str]:
        """List the words that agent messages use to address someone. No network.

        In exports where usernames were replaced by letters, these words are
        often real first names. With ``redact_vocatives=True`` (the default)
        they are replaced by ``[NAME]`` in every message before anything is
        sent (warning W016).

        Parameters
        ----------
        source : pandas.DataFrame, str or path-like
            The chat table or a file (see `prepare`).
        columns : ColumnMap or mapping, optional
            Explicit column mapping (see `prepare`).
        **read_kw
            Passed to the pandas reader.

        Returns
        -------
        list of str
            Sorted candidate words, as written in the data. Empty when the
            input cannot be used.

        Notes
        -----
        The result may contain personal data. It is meant for the
        researcher's eyes only: the package never logs or writes it. Add
        names that were missed with ``extra_names`` or ``name_map``.
        """
        from .preprocess import role_matcher, vocative_names
        from .validation import check_frame

        raw = read_source(source, **read_kw)
        _, clean = check_frame(raw, ColumnMap.coerce(columns), self.config)
        if clean is None:
            return []
        is_agent = role_matcher(self.config)
        rows = clean["username"].astype(str).map(lambda x: bool(is_agent(x)))
        return sorted(vocative_names(clean.loc[rows, "text"].astype(str).tolist()))

    def preview(self, source: Any, *, group: str, columns: Columns = None, **read_kw: Any) -> str:
        """Return the exact pseudonymized transcript that would be sent for one group.

        No network. The text is identical to the ``<transcript>`` block of
        every request for this group and to the transcript shown to human
        coders.

        Parameters
        ----------
        source : pandas.DataFrame, str or path-like
            The chat table or a file (see `prepare`).
        group : str
            Group identifier as it appears in the input.
        columns : ColumnMap or mapping, optional
            Explicit column mapping (see `prepare`).
        **read_kw
            Passed to the pandas reader.

        Returns
        -------
        str
            One line per unit: ``unit_id | HH:MM:SS | +MM:SS | speaker | text``,
            where agents are marked ``[AGENT]`` and the offset counts from the
            group's first message.

        Raises
        ------
        InputValidationError
            If validation found errors.
        KeyError
            If `group` is not in the input.
        """
        prep = self.prepare(source, columns=columns, **read_kw)
        prep.report.raise_if_errors()
        if group not in prep.groups:
            raise KeyError(f"group {group!r} not found; available: {list(prep.groups)}")
        return render_transcript(prep.groups[group])

    # ------------------------------------------------------------------ prompts
    def list_prompts(self) -> pd.DataFrame:
        """List the prompts in the registry.

        Returns
        -------
        pandas.DataFrame
            One row per prompt with ``name``, ``version`` (prompt-set version
            of the built-in file), ``sha256`` (first 12 hex characters of the
            template hash), ``source`` (``builtin`` or ``custom``),
            ``variables`` (template variables, comma-separated) and
            ``description``.
        """
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
        """Return a prompt as a template or as the text the model receives.

        Parameters
        ----------
        name : str
            Prompt name (see `list_prompts`).
        rendered : bool, default False
            If False, return the Jinja2 template. If True, render it with the
            current configuration (pattern gap, languages, goal).
        goal : {"G1", "G2", "G3", "G4"}, optional
            Required when rendering ``task_detect``, which differs per goal.

        Returns
        -------
        str
            The template or the rendered text.

        Raises
        ------
        PromptError
            If `name` is unknown, or ``task_detect`` is rendered without a
            goal.
        """
        if not rendered:
            return self.prompts.get(name).text
        if name == "task_detect" and goal is None:
            raise PromptError("task_detect is rendered per goal; pass goal='G1'..'G4'")
        return cb.render_prompt(self.prompts, name, self.config, goal=goal)

    def get_prompt(self, name: str) -> PromptTemplate:
        """Return one prompt template.

        Parameters
        ----------
        name : str
            Prompt name (see `list_prompts`).

        Returns
        -------
        PromptTemplate

        Raises
        ------
        PromptError
            If `name` is unknown.
        """
        return self.prompts.get(name)

    def set_prompt(
        self, name: str, template: str | Path, *, description: str = ""
    ) -> PromptTemplate:
        """Replace one base prompt.

        Parameters
        ----------
        name : str
            Prompt name (see `list_prompts`).
        template : str or pathlib.Path
            Template text, or a path to a UTF-8 file holding it. A plain
            ``str`` is always taken as text, never as a path.
        description : str, optional
            Description shown by `list_prompts`. Defaults to the built-in one.

        Returns
        -------
        PromptTemplate
            The stored template. Its ``source`` is ``custom`` unless the text
            equals the built-in text.

        Raises
        ------
        PromptError
            If `name` is unknown, the text is empty or not a valid template.
        PromptSchemaMismatch
            If the template does not use exactly the variables of the
            built-in prompt.

        Notes
        -----
        A customized registry reports ``prompt_set_version = "custom-<hash8>"``,
        and every result made with it carries that version: such results are
        not comparable with results from the built-in prompts. The new text
        also changes the prompt-set hash and hence every cache key.
        """
        return self.prompts.set(name, template, description=description)

    def reset_prompts(self, names: list[str] | None = None) -> None:
        """Restore built-in prompts.

        Parameters
        ----------
        names : list of str, optional
            Prompts to restore. All of them when omitted.

        Raises
        ------
        PromptError
            If a name is unknown.
        """
        self.prompts.reset(names)

    def export_prompts(self, directory: Path | str) -> list[Path]:
        """Write every current prompt template to `directory`.

        Parameters
        ----------
        directory : str or pathlib.Path
            Target directory, created if needed. Existing files with the same
            names are overwritten.

        Returns
        -------
        list of pathlib.Path
            One ``<name>.md.j2`` file per prompt. The directory can be passed
            back as ``prompts=`` or ``prompts_dir=``.
        """
        return self.prompts.export(Path(directory))

    @property
    def prompt_set_version(self) -> str:
        """Prompt-set version: ``"v1"`` for the built-in set, ``"custom-<hash8>"`` otherwise."""
        return self.prompts.version()

    @property
    def prompt_set_hash(self) -> str:
        """Hash of the prompts plus every setting rendered into them, and the schema version.

        Human coding sheets carry this hash so that coders and the model can be
        matched to the same codebook.
        """
        return cb.prompt_set_hash(self.prompts, self.config, SCHEMA_VERSION)

    def export_codebook(self, path: Path | str) -> Path:
        """Write the human codebook generated from the current prompts.

        The rule sections of the codebook are the rendered prompts, verbatim
        (DESIGN §12), so human coders apply the same text as the model.

        Parameters
        ----------
        path : str or pathlib.Path
            Output Markdown file. Parent directories are created.

        Returns
        -------
        pathlib.Path
            The written file.
        """
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
        """Write an Excel workbook for a human coder. No network.

        The workbook has a ``README`` sheet (coder, prompt-set hash, groups),
        a ``transcript`` sheet with exactly the units the model sees, a
        ``messages`` sheet (message ↔ unit ↔ input row) and one form sheet
        per goal with the fields of the model's output schema.

        Parameters
        ----------
        source : pandas.DataFrame, str or path-like
            The chat table or a file (see `prepare`).
        path : str or pathlib.Path
            Output ``.xlsx`` file. Parent directories are created.
        groups : list of str, optional
            Groups to include. All eligible groups when omitted. For a coding
            study, export one workbook per group.
        coder : str, optional
            Coder name or id, stored in the README sheet.
        columns : ColumnMap or mapping, optional
            Explicit column mapping (see `prepare`).
        **read_kw
            Passed to the pandas reader.

        Returns
        -------
        pathlib.Path
            The written workbook.

        Raises
        ------
        InputValidationError
            If validation found errors.
        KeyError
            If a requested group is not in the input.
        ConfigError
            If ``openpyxl`` is not installed (``pip install 'fgpd-analyzer[xlsx]'``).
        """
        from .coding import export_coding_sheet

        prep = self.prepare(source, columns=columns, **read_kw)
        prep.report.raise_if_errors()
        return export_coding_sheet(
            prep, Path(path), groups=groups, prompt_set_hash=self.prompt_set_hash, coder=coder
        )

    # ------------------------------------------------------------------ analysis
    def _make_backend(self) -> Any:
        """Return the backend passed to the constructor, or build one from the config."""
        if self._backend is not None:
            return self._backend
        c = self.config
        if c.backend == "mock":
            from .backends.mock import MockBackend

            return MockBackend()
        if c.backend == "replay":
            raise ConfigError(
                "backend='replay' needs a ReplayBackend instance: backend=ReplayBackend(path)"
            )
        from .backends.anthropic import AnthropicBackend

        key = c.api_key.get_secret_value() if c.api_key else None
        return AnthropicBackend(key, timeout_s=c.timeout_s, max_retries=c.max_retries)

    def _select(self, prep: PreparedInput, groups: list[str] | None) -> list[str]:
        """Return the requested groups that are eligible (all eligible ones by default)."""
        if groups is None:
            return prep.eligible
        missing = [g for g in groups if g not in prep.groups]
        if missing:
            raise KeyError(f"groups not found: {missing}")
        return [g for g in groups if prep.groups[g].eligibility == "eligible"]

    def estimate_cost(
        self,
        source: Any,
        *,
        columns: Columns = None,
        groups: list[str] | None = None,
        **read_kw: Any,
    ) -> Any:
        """Forecast the cost of `analyze` on this input. No network, no cost.

        The prompt size of each chat is computed from the rendered prompts and
        transcript (characters / 3.2). The output size is not known before a
        run, so it is assumed: 3,000 (low), 9,000 (mid) and 16,000 (high)
        output tokens per call, from the measured dev run (docs/decisions.md
        D40). Each chat is assumed to write the prompt cache once and read it
        in its other ``len(goals) × n_runs − 1`` calls.

        Parameters
        ----------
        source : pandas.DataFrame, str or path-like
            The chat table or a file (see `prepare`).
        columns : ColumnMap or mapping, optional
            Explicit column mapping (see `prepare`).
        groups : list of str, optional
            Restrict the estimate to these groups (ineligible ones are left
            out). All eligible groups when omitted.
        **read_kw
            Passed to the pandas reader.

        Returns
        -------
        CostEstimate
            Number of calls, USD low / mid / high, and a per-group table.
            Costs are NaN when the model is not in the price table.

        Raises
        ------
        InputValidationError
            If validation found errors.
        KeyError
            If a requested group is not in the input.

        Notes
        -----
        The estimate ignores responses already in the local cache (they cost
        nothing) and retries (a ``max_tokens`` retry is paid twice). Output,
        most of it thinking, dominates the cost; use the high value as a
        budget.
        """
        from .analysis import estimate

        prep = self.prepare(source, columns=columns, **read_kw)
        prep.report.raise_if_errors()
        return estimate(prep, self.prompts, self.config, self._select(prep, groups))

    async def analyze_async(
        self,
        source: Any,
        *,
        columns: Columns = None,
        groups: list[str] | None = None,
        raise_on_partial: bool = False,
        **read_kw: Any,
    ) -> Any:
        """Run the analysis as a coroutine. Makes API calls.

        Same as `analyze` without ``dry_run``; use it inside an existing event
        loop (``await fa.analyze_async(...)``). Calls run concurrently, at
        most ``max_concurrency`` at a time and, if set, at most
        ``requests_per_minute`` starts per minute.

        Parameters
        ----------
        source : pandas.DataFrame, str or path-like
            The chat table or a file (see `prepare`).
        columns : ColumnMap or mapping, optional
            Explicit column mapping (see `prepare`).
        groups : list of str, optional
            Analyze only these groups. Ineligible ones are skipped; eligible
            groups not listed get status ``not_selected``.
        raise_on_partial : bool, default False
            Raise `PartialResultsError` when any call did not succeed.
        **read_kw
            Passed to the pandas reader.

        Returns
        -------
        FGPDResults
            See `analyze`.

        Raises
        ------
        InputValidationError
            If validation found errors (before any network I/O).
        KeyError
            If a requested group is not in the input.
        ConfigError
            If the backend cannot be built (no API key, ``backend="replay"``
            without a `ReplayBackend` instance).
        CacheError
            If the local cache cannot be opened or holds a corrupt entry.
        PartialResultsError
            With ``raise_on_partial=True``, if some group × goal × run call
            failed or was refused; ``.results`` holds the results.
        """
        from . import __version__
        from .analysis import run_analysis, sha256_of_source
        from .cache import ResponseCache
        from .exceptions import PartialResultsError

        prep = self.prepare(source, columns=columns, **read_kw)
        prep.report.raise_if_errors()
        selected = self._select(prep, groups)
        cache = None if self.config.cache == "off" else ResponseCache(self.config.cache_dir)
        try:
            res = await run_analysis(
                prep,
                selected,
                self.prompts,
                self.config,
                self._make_backend(),
                cache,
                source_sha256=sha256_of_source(source),
                package_version=__version__,
            )
        finally:
            if cache is not None:
                cache.close()
        if raise_on_partial and (res.calls["status"] != "ok").any():
            raise PartialResultsError("some group x goal x run calls did not succeed", res)
        return res

    def analyze(
        self,
        source: Any,
        *,
        columns: Columns = None,
        groups: list[str] | None = None,
        dry_run: bool = False,
        raise_on_partial: bool = False,
        **read_kw: Any,
    ) -> Any:
        """Validate the input, call the model and return the results. Makes API calls.

        For every eligible group, goal and run, one request is built and sent
        (or answered from the local cache). Responses are post-validated,
        merged into a consensus over runs, and agent influence is computed
        after the fact with ``influence_lag`` and ``influence_rule``.

        Parameters
        ----------
        source : pandas.DataFrame, str or path-like
            The chat table or a file (see `prepare`).
        columns : ColumnMap or mapping, optional
            Explicit column mapping (see `prepare`).
        groups : list of str, optional
            Analyze only these groups. Ineligible ones are skipped; eligible
            groups not listed get status ``not_selected``.
        dry_run : bool, default False
            If True, send nothing and return a dict with the validation
            report, the cost estimate and the groups that would be analyzed.
        raise_on_partial : bool, default False
            Raise `PartialResultsError` when any call did not succeed.
        **read_kw
            Passed to the pandas reader.

        Returns
        -------
        FGPDResults or dict
            The results. With ``dry_run=True``, a dict with keys
            ``"validation"`` (`ValidationReport`), ``"estimate"``
            (`CostEstimate`) and ``"groups"`` (list of str).

        Raises
        ------
        InputValidationError
            If validation found errors. Raised before any network I/O.
        KeyError
            If a requested group is not in the input.
        ConfigError
            If the backend cannot be built.
        CacheError
            If the local cache cannot be opened or holds a corrupt entry.
        PartialResultsError
            With ``raise_on_partial=True``, if some call failed or was refused.

        See Also
        --------
        validate_input : checks only, no network.
        estimate_cost : cost forecast, no network.
        preview : the transcript of one group, no network.
        analyze_async : the same as a coroutine.

        Notes
        -----
        A failing call never aborts the batch: it is retried as described in
        `fgpd_analyzer.runner` and, if it still fails, its group × goal gets
        status ``partial`` (some runs succeeded) or ``failed`` / ``refused``
        (none did). Inspect ``res.calls`` and ``res.frequencies["status"]``.

        Only successful responses are cached. With ``cache="use"`` (the
        default) a rerun with the same input and settings sends no request.

        This method works inside a running event loop (e.g. Jupyter): the
        coroutine is then run in a separate thread.
        """
        from .analysis import run_coro

        if dry_run:
            prep = self.prepare(source, columns=columns, **read_kw)
            prep.report.raise_if_errors()
            return {
                "validation": prep.report,
                "estimate": self.estimate_cost(source, columns=columns, groups=groups, **read_kw),
                "groups": self._select(prep, groups),
            }
        return run_coro(
            self.analyze_async(
                source, columns=columns, groups=groups, raise_on_partial=raise_on_partial, **read_kw
            )
        )
