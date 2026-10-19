"""Command line interface: `fgpd ...`.

Exit codes: 0 ok, 2 validation errors, 3 backend failure, 4 partial results.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(
    help="Measure the Four Goals for Productive Discussion in group chats.", no_args_is_help=True
)
prompts_app = typer.Typer(help="View and export the base prompts.", no_args_is_help=True)
codebook_app = typer.Typer(help="Human codebook.", no_args_is_help=True)
sheet_app = typer.Typer(help="Human coding workbook.", no_args_is_help=True)
cache_app = typer.Typer(help="Local response cache.", no_args_is_help=True)
app.add_typer(prompts_app, name="prompts")
app.add_typer(codebook_app, name="codebook")
app.add_typer(sheet_app, name="coding-sheet")
app.add_typer(cache_app, name="cache")

AgentOpt = Annotated[str, typer.Option("--agent-pattern", help="Username substring of agents.")]
ColOpt = Annotated[
    list[str] | None,
    typer.Option(  # noqa: UP007
        "--column", "-c", help="Column mapping canonical=source, e.g. text=Mensagem."
    ),
]


def _columns(items: list[str] | None) -> dict[str, str] | None:
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
    exclude_flagged: bool = False,
) -> None:
    """Validate an input file. No network."""
    fa = _analyzer(agent_pattern=agent_pattern, exclude_flagged=exclude_flagged)
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
    n_runs: int = 3,
    exclude_flagged: bool = False,
) -> None:
    """Estimate the cost of an analysis. No network."""
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
    n_runs: int = 3,
    lag: str = "2min",
    every: Annotated[list[str] | None, typer.Option(help="Time-series bins.")] = None,  # noqa: UP007
    backend: str = "anthropic",
    model: str | None = None,  # noqa: UP007
    effort: str | None = None,  # noqa: UP007
    justification_language: str | None = None,  # noqa: UP007
    chat_language: str | None = None,  # noqa: UP007
    exclude_flagged: bool = False,
    fmt: Annotated[str, typer.Option("--format")] = "csv",
) -> None:
    """Run the analysis and save all outputs to OUT."""
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
        )
        res = fa.analyze(chats, columns=_columns(column))
    except InputValidationError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    except (BackendError, ConfigError) as e:
        typer.echo(f"backend error: {e}", err=True)
        raise typer.Exit(3) from e
    res.save(out, format=fmt, timeseries_every=tuple(every or ["5min"]))
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
def timeseries(results: Path, every: str = "5min", out: Path | None = None) -> None:  # noqa: UP007
    """Recompute the cumulative time series from saved results. No network."""
    from .results import FGPDResults

    ts = FGPDResults.load(results).timeseries(every)
    path = out or results / f"timeseries_{every.replace(' ', '')}.csv"
    ts.to_csv(path, index=False)
    typer.echo(f"wrote {path}")


@app.command()
def influence(
    results: Path, lag: str = "2min", rule: str = "uptake", out: Path | None = None
) -> None:  # noqa: UP007
    """Recompute agent influence with another lag or rule. No network."""
    from .results import FGPDResults

    res = FGPDResults.load(results).with_influence(lag, rule)
    target = out or results
    res.save(target)
    typer.echo(f"influence recomputed (lag {lag}, rule {rule}) in {target}")


@prompts_app.command("list")
def prompts_list() -> None:
    typer.echo(_analyzer().list_prompts().to_string(index=False))


@prompts_app.command("show")
def prompts_show(name: str, rendered: bool = False, goal: str | None = None) -> None:  # noqa: UP007
    typer.echo(_analyzer().show_prompt(name, rendered=rendered, goal=goal))


@prompts_app.command("export")
def prompts_export(directory: Path) -> None:
    for p in _analyzer().export_prompts(directory):
        typer.echo(str(p))


@codebook_app.command("export")
def codebook_export(path: Path, justification_language: str = "en") -> None:
    typer.echo(str(_analyzer(justification_language=justification_language).export_codebook(path)))


@sheet_app.command("export")
def sheet_export(
    chats: Path,
    path: Path,
    group: list[str] | None = None,  # noqa: UP007
    coder: str = "",
    agent_pattern: AgentOpt = "agent",
    column: ColOpt = None,
) -> None:
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
    """Report structural problems in a filled workbook, without changing it."""
    from .coding import check_coding_sheet

    fa = _analyzer(agent_pattern=agent_pattern)
    report = check_coding_sheet(fa.prepare(chats, columns=_columns(column)), workbook)
    typer.echo(report.to_string(index=False) if not report.empty else "no problems found")
    raise typer.Exit(0 if report.empty else 4)


@cache_app.command("info")
def cache_info() -> None:
    from .cache import ResponseCache

    c = ResponseCache()
    typer.echo(f"{c.path}: {len(c)} responses")


@cache_app.command("export")
def cache_export(path: Path, results: Path | None = None) -> None:
    """Write cached responses as JSONL for ReplayBackend (only those of RESULTS, if given)."""
    import json

    from .cache import ResponseCache

    keys = None
    if results is not None:
        meta = json.loads((results / "run_metadata.json").read_text(encoding="utf-8"))
        keys = set(meta.get("cache_keys", []))
    out = ResponseCache().export_jsonl(path, keys)
    typer.echo(f"wrote {out}")


@cache_app.command("clear")
def cache_clear() -> None:
    from .cache import ResponseCache

    typer.echo(f"removed {ResponseCache().clear()} responses")


if __name__ == "__main__":  # pragma: no cover
    app()
