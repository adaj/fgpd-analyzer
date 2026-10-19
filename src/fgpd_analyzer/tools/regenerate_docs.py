"""Regenerate docs/codebook.md from the built-in prompts with default settings.

Run from the repository root: ``python -m fgpd_analyzer.tools.regenerate_docs``.
"""

from __future__ import annotations

from pathlib import Path

from fgpd_analyzer import FGPDAnalyzer


def main(root: Path = Path(".")) -> Path:
    return FGPDAnalyzer().export_codebook(root / "docs" / "codebook.md")


if __name__ == "__main__":
    print(main())
