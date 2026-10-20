"""Regenerate docs/codebook.md from the built-in prompts with default settings.

A maintenance tool for the outputs of the prompts stage: run it after
changing a prompt, since a test fails when the committed codebook is stale.
Run from the repository root: ``python -m fgpd_analyzer.tools.regenerate_docs``.
"""

from __future__ import annotations

from pathlib import Path

from fgpd_analyzer import FGPDAnalyzer


def main(root: Path = Path(".")) -> Path:
    """Write ``<root>/docs/codebook.md`` and return its path.

    Parameters
    ----------
    root : pathlib.Path, default Path(".")
        Repository root.

    Returns
    -------
    pathlib.Path
    """
    return FGPDAnalyzer().export_codebook(root / "docs" / "codebook.md")


if __name__ == "__main__":
    print(main())
