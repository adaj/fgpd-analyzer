"""CLI placeholder (filled in M4)."""

import typer

app = typer.Typer(help="fgpd-analyzer command line")


@app.command()
def version() -> None:
    """Print the package version."""
    from . import __version__

    typer.echo(__version__)
