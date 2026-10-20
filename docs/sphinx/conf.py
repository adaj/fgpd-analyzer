"""Sphinx configuration for the fgpd-analyzer API documentation.

Build from the repository root with::

    pip install -e ".[docs]"
    sphinx-build -b html docs/sphinx docs/sphinx/_build/html
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fgpd_analyzer import __version__  # noqa: E402

project = "fgpd-analyzer"
author = "Adelson de Araujo"
copyright = "2026, Adelson de Araujo"
release = __version__
version = __version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
]

source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build"]

# Single backticks in docstrings are cross-references (unresolved ones render as code).
default_role = "py:obj"

autodoc_member_order = "bysource"
autodoc_typehints = "signature"
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
    "exclude-members": "model_config, model_fields, model_computed_fields",
}

napoleon_google_docstring = False
napoleon_numpy_docstring = True
# Render "Attributes" as fields, so properties documented by autodoc are not duplicated.
napoleon_use_ivar = True
napoleon_use_rtype = False

html_theme = "furo"
html_static_path = ["_static"]
html_title = f"fgpd-analyzer {release}"
