"""Command line interface: ``fgpd ...``.

A thin layer over `FGPDAnalyzer` and `FGPDResults` that covers the whole
pipeline from a file: ``validate`` and ``estimate`` (input stage, no network),
``analyze`` (all stages, API calls), ``timeseries`` and ``influence`` (outputs
stage, on saved results, no network), plus prompts, codebook, coding-sheet and
cache utilities. Each command's docstring is its ``--help`` text.

Exit codes: 0 ok, 2 validation or configuration errors, 3 backend failure
(every call failed), 4 partial results (some calls failed) or problems found
by ``coding-sheet check``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(
    help="Measure the Four Goals for Productive Discussion in group chats.", no_args_is_help=True
)
prompts_app = typer.Typer(help="View and export the base prompts.", no_args_is_help=True)
codebook_app = typer.Typer(help="Export the human codebook.", no_args_is_help=True)
sheet_app = typer.Typer(help="Export and check human coding workbooks.", no_args_is_help=True)
cache_app = typer.Typer(
    help="Inspect, export and clear the local response cache.", no_args_is_help=True
)
app.add_typer(prompts_app, name="prompts")
app.add_typer(codebook_app, name="codebook")
app.add_typer(sheet_app, name="coding-sheet")
app.add_typer(cache_app, name="cache")

AgentOpt = Annotated[
    str,
    typer.Option("--agent-pattern", help="Usernames containing this text (any case) are agents."),
]
NameMapOpt = Annotated[
    Path | None,
    typer.Option(
        "--name-map",
        help="JSON file {real name: username}; real names in the text become pseudonyms.",
    ),
]
ExcludeOpt = Annotated[
    bool,
    typer.Option(help="Leave out groups whose agent share is above 40% (status 'excluded')."),
]
NRunsOpt = Annotated[
    int, typer.Option(help="Independent runs per group and goal (majority consensus).")
]
ColOpt = Annotated[
    list[str] | None,
    typer.Option(  # noqa: UP007
        "--column", "-c", help="Column mapping canonical=source, e.g. text=Mensagem."
    ),
]


def _columns(items: list[str] | None) -> dict[str, str] | None:
    """Parse repeated ``canonical=source`` options into a column mapping."""
    if not items:
        return None
    out = {}
    for it in items:
        k, _, v = it.partition("=")
        if not v:
            raise typer.BadParameter(f"use canonical=source, got {it!r}")
        out[k.strip()] = v.strip()
    return out


def _analyzer(**kw: Any) -> Any:
    """Build an `FGPDAnalyzer`, leaving options that were not given at their defaults."""
    from . import FGPDAnalyzer

    return FGPDAnalyzer(**{k: v for k, v in kw.items() if v is not None})


@app.command()
def version() -> None:
    """Print the package version."""
    from . import __version__

    typer.echo(__version__)


@app.command()
def validate(
    chats: Path,
    agent_pattern: AgentOpt = "agent",
    column: ColOpt = None,
    exclude_flagged: ExcludeOpt = False,
    name_map: NameMapOpt = None,
) -> None:
    """Validate an input file. No network, no cost.

    Checks columns, timestamps, roles, pseudonymization and eligibility of
    CHATS (csv, tsv, xlsx or parquet; one row per message) and prints every
    issue with its code, followed by one summary line per group. Exits with 2
    if there are errors.
    """
    fa = _analyzer(agent_pattern=agent_pattern, exclude_flagged=exclude_flagged, name_map=name_map)
    rep = fa.validate_input(chats, columns=_columns(column))
    typer.echo(rep.to_text())
    if not rep.info.empty:
        typer.echo(rep.info.to_string(index=False))
    raise typer.Exit(0 if rep.ok else 2)


@app.command()
def estimate(
    chats: Path,
    agent_pattern: AgentOpt = "agent",
    column: ColOpt = None,
    n_runs: NRunsOpt = 3,
    exclude_flagged: ExcludeOpt = False,
) -> None:
    """Estimate the cost of analyzing CHATS, in USD. No network, no cost.

    Prints the number of calls and a low / mid / high estimate, then one row
    per group. Output tokens per call cannot be known before a run and are
    assumed (3k / 9k / 16k); use the high value as a budget. Exits with 2 if
    the input has validation errors.
    """
    from .exceptions import InputValidationError

    fa = _analyzer(agent_pattern=agent_pattern, n_runs=n_runs, exclude_flagged=exclude_flagged)
    try:
        est = fa.estimate_cost(chats, columns=_columns(column))
    except InputValidationError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    typer.echo(str(est))
    typer.echo(est.per_group.to_string(index=False))


@app.command()
def analyze(
    chats: Path,
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")],
    agent_pattern: AgentOpt = "agent",
    column: ColOpt = None,
    n_runs: NRunsOpt = 3,
    lag: Annotated[
        str, typer.Option(help="Agent-influence window, e.g. 90s or 2min (post hoc).")
    ] = "2min",
    every: Annotated[
        list[str] | None,
        typer.Option(help="Time-series bin width, e.g. 5min; repeat for several (default 5min)."),
    ] = None,  # noqa: UP007
    backend: Annotated[
        str, typer.Option(help="anthropic (real API calls) or mock (synthetic, offline).")
    ] = "anthropic",
    model: Annotated[str | None, typer.Option(help="Model id (default claude-haiku-5-5).")] = None,  # noqa: UP007
    effort: Annotated[
        str | None, typer.Option(help="low, medium, high, xhigh or max (default medium).")
    ] = None,  # noqa: UP007
    justification_language: Annotated[
        str | None, typer.Option(help="Language of the justifications, e.g. pt (default en).")
    ] = None,  # noqa: UP007
    chat_language: Annotated[
        str | None, typer.Option(help="Language of the chats, e.g. pt-BR (default: any).")
    ] = None,  # noqa: UP007
    exclude_flagged: ExcludeOpt = False,
    fmt: Annotated[str, typer.Option("--format", help="Table format: csv or parquet.")] = "csv",
    group: Annotated[
        list[str] | None, typer.Option("--group", help="Only these groups; repeat for several.")
    ] = None,
    name_map: NameMapOpt = None,
    cache: Annotated[
        str, typer.Option(help="Response cache: use, refresh (ignore cached) or off.")
    ] = "use",
    cache_dir: Annotated[
        Path | None, typer.Option(help="Cache directory (default: the user cache directory).")
    ] = None,
) -> None:
    """Analyze CHATS with the model and save every output table to OUT.

    Makes API calls (one per eligible group, goal and run) unless the
    responses are cached; check the cost first with 'fgpd estimate'.
    Validation errors stop the command before any call (exit 2). Writes
    frequencies, patterns, time series, run metadata and the validation
    report to OUT. Exits with 3 if every call failed and 4 if some did.
    """
    from .exceptions import BackendError, ConfigError, InputValidationError

    try:
        fa = _analyzer(
            agent_pattern=agent_pattern,
            n_runs=n_runs,
            influence_lag=lag,
            backend=backend,
            model=model,
            effort=effort,
            justification_language=justification_language,
            chat_language=chat_language,
            exclude_flagged=exclude_flagged,
            cache=cache,
            cache_dir=cache_dir,
            name_map=name_map,
        )
    except (ConfigError, ValueError) as e:
        typer.echo(f"configuration error: {e}", err=True)
        raise typer.Exit(2) from e
    try:
        res = fa.analyze(chats, columns=_columns(column), groups=group or None)
    except InputValidationError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    except KeyError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    except (BackendError, ConfigError) as e:
        typer.echo(f"backend error: {e}", err=True)
        raise typer.Exit(3) from e
    res.save(out, format=fmt, timeseries_every=tuple(every or ["5min"]))
    if res.calls.empty:
        typer.echo("no eligible group to analyze; see validation.json", err=True)
        raise typer.Exit(2)
    meta = res.run_metadata
    typer.echo(
        f"saved to {out}; {meta['n_calls']} calls ({meta['n_cached']} cached), "
        f"cost ${meta['cost_usd'] or 0:.4f}"
    )
    statuses = res.calls["status"]
    if (statuses != "ok").all() and len(statuses):
        raise typer.Exit(3)
    if (statuses != "ok").any():
        raise typer.Exit(4)


@app.command()
def timeseries(
    results: Path,
    every: Annotated[str, typer.Option(help="Bin width, e.g. 10min.")] = "5min",
    out: Annotated[
        Path | None, typer.Option(help="Output CSV (default RESULTS/timeseries_<every>.csv).")
    ] = None,
) -> None:  # noqa: UP007
    """Recompute the cumulative time series from saved results. No network.

    Reads the directory RESULTS written by 'fgpd analyze' and writes one CSV
    with one row per group, goal and bin of the given width.
    """
    from .results import FGPDResults

    ts = FGPDResults.load(results).timeseries(every)
    fmt = "parquet" if (results / "units.parquet").exists() else "csv"
    path = out or results / f"timeseries_{every.replace(' ', '')}.{fmt}"
    if str(path).endswith(".parquet"):
        ts.to_parquet(path, index=False)
    else:
        ts.to_csv(path, index=False)
    typer.echo(f"wrote {path}")


@app.command()
def influence(
    results: Path,
    lag: Annotated[str, typer.Option(help="Agent-influence window, e.g. 90s or 2min.")] = "2min",
    rule: Annotated[
        str, typer.Option(help="uptake (content uptake only) or temporal (also proximity).")
    ] = "uptake",
    out: Annotated[
        Path | None, typer.Option(help="Output directory (default: overwrite RESULTS).")
    ] = None,
) -> None:  # noqa: UP007
    """Recompute agent influence with another lag or rule. No network.

    Reads the directory RESULTS written by 'fgpd analyze', applies the new lag
    and rule to the stored patterns, and rewrites patterns, frequencies and
    every existing time-series table in the same format. Exits with 2 for an
    invalid lag or rule.
    """
    from .exceptions import ConfigError
    from .results import FGPDResults

    try:
        res = FGPDResults.load(results).with_influence(lag, rule)
    except (ValueError, ConfigError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    target = out or results
    fmt = "parquet" if (results / "units.parquet").exists() else "csv"
    every = tuple(sorted(p.stem.split("_", 1)[1] for p in results.glob(f"timeseries_*.{fmt}"))) or (
        "5min",
    )
    res.save(target, format=fmt, timeseries_every=every)  # type: ignore[arg-type]
    typer.echo(f"influence recomputed (lag {lag}, rule {rule}) in {target}")


@prompts_app.command("list")
def prompts_list() -> None:
    """List the base prompts with version, hash, source and template variables."""
    typer.echo(_analyzer().list_prompts().to_string(index=False))


@prompts_app.command("show")
def prompts_show(
    name: str,
    rendered: Annotated[
        bool, typer.Option(help="Show the text the model receives, not the template.")
    ] = False,
    goal: Annotated[
        str | None, typer.Option(help="Goal G1-G4; needed to render task_detect.")
    ] = None,
) -> None:  # noqa: UP007
    """Print one base prompt NAME (see 'fgpd prompts list')."""
    typer.echo(_analyzer().show_prompt(name, rendered=rendered, goal=goal))


@prompts_app.command("export")
def prompts_export(directory: Path) -> None:
    """Write every base prompt to DIRECTORY as <name>.md.j2 and print the paths.

    Edited files can be loaded back with prompts_dir=DIRECTORY in Python.
    """
    for p in _analyzer().export_prompts(directory):
        typer.echo(str(p))


@codebook_app.command("export")
def codebook_export(
    path: Path,
    justification_language: Annotated[
        str, typer.Option(help="Language the codebook asks justifications in, e.g. pt.")
    ] = "en",
) -> None:
    """Write the human codebook to PATH (Markdown).

    Its rule sections are the prompts the model receives, verbatim.
    """
    typer.echo(str(_analyzer(justification_language=justification_language).export_codebook(path)))


@sheet_app.command("export")
def sheet_export(
    chats: Path,
    path: Path,
    group: Annotated[
        list[str] | None,
        typer.Option(help="Only these groups; repeat for several (default: all eligible)."),
    ] = None,  # noqa: UP007
    coder: Annotated[str, typer.Option(help="Coder name or id stored in the workbook.")] = "",
    agent_pattern: AgentOpt = "agent",
    column: ColOpt = None,
) -> None:
    """Write a coding workbook (xlsx) for a human coder from CHATS to PATH.

    The workbook shows exactly the units the model sees and has one form per
    goal with the fields of the model output. For a coding study, export one
    workbook per group. No network.
    """
    fa = _analyzer(agent_pattern=agent_pattern)
    typer.echo(
        str(
            fa.export_coding_sheet(chats, path, groups=group, coder=coder, columns=_columns(column))
        )
    )


@sheet_app.command("check")
def sheet_check(
    chats: Path, workbook: Path, agent_pattern: AgentOpt = "agent", column: ColOpt = None
) -> None:
    """Report structural problems in a filled coding WORKBOOK, without changing it.

    Applies the same structural rules as to the model output (in report-only
    mode) and prints one line per problem with its spreadsheet row. CHATS
    must be the input the workbook was exported from. Exits with 4 if
    problems were found.
    """
    from .coding import check_coding_sheet

    fa = _analyzer(agent_pattern=agent_pattern)
    report = check_coding_sheet(fa.prepare(chats, columns=_columns(column)), workbook)
    typer.echo(report.to_string(index=False) if not report.empty else "no problems found")
    raise typer.Exit(0 if report.empty else 4)


CacheDirOpt = Annotated[
    Path | None,
    typer.Option("--cache-dir", help="Cache directory (default: the user cache directory)."),
]


@cache_app.command("info")
def cache_info(cache_dir: CacheDirOpt = None) -> None:
    """Print the location of the response cache and the number of stored responses."""
    from .cache import ResponseCache

    c = ResponseCache(cache_dir)
    typer.echo(f"{c.path}: {len(c)} responses")


@cache_app.command("export")
def cache_export(
    path: Path,
    results: Annotated[
        Path | None,
        typer.Option(help="Results directory; export only the responses of that analysis."),
    ] = None,
    cache_dir: CacheDirOpt = None,
) -> None:
    """Write cached responses to PATH as JSONL, for ReplayBackend.

    With the same input and settings, the file replays an analysis bit for bit
    without API calls. It holds pseudonymized model output only.
    """
    import json

    from .cache import ResponseCache

    keys = None
    if results is not None:
        meta = json.loads((results / "run_metadata.json").read_text(encoding="utf-8"))
        keys = set(meta.get("cache_keys", []))
    out = ResponseCache(cache_dir).export_jsonl(path, keys)
    typer.echo(f"wrote {out}")


@cache_app.command("clear")
def cache_clear(cache_dir: CacheDirOpt = None) -> None:
    """Delete every cached response; later analyses call the API again."""
    from .cache import ResponseCache

    typer.echo(f"removed {ResponseCache(cache_dir).clear()} responses")


if __name__ == "__main__":  # pragma: no cover
    app()
