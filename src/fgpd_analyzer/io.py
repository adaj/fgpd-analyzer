"""Reading chat tables from files or DataFrames.

This is the first stage of the pipeline (input → units → prompts → requests →
runs → post-validation → consensus → influence → outputs): it turns a file or
a DataFrame into a raw DataFrame of strings, without interpreting any column.
Column mapping and parsing happen next, in `fgpd_analyzer.validation`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .exceptions import ConfigError

Source = Any  # pd.DataFrame | str | os.PathLike


def read_source(source: Source, **read_kw: Any) -> pd.DataFrame:
    """Read a chat table from a DataFrame or a file.

    Parameters
    ----------
    source : pandas.DataFrame, str or path-like
        A DataFrame (copied), or a ``.csv``/``.txt`` (separator detected),
        ``.tsv``/``.tab``, ``.xlsx``/``.xlsm``/``.xls`` or ``.parquet`` file.
    **read_kw
        Passed to ``pd.read_csv``, ``pd.read_excel`` or ``pd.read_parquet``.

    Returns
    -------
    pandas.DataFrame
        The raw table with a fresh ``RangeIndex``. Text formats and Excel are
        read as strings without NA conversion, so ids such as ``007`` and
        cells reading ``NA`` survive (docs/decisions.md D3, R7). Unnamed index
        columns written by pandas (``Unnamed: 0``) and columns with an empty
        name are dropped.

    Raises
    ------
    FileNotFoundError
        If the path does not exist.
    ConfigError
        If the file type is unsupported, or the optional dependency for
        Excel (``openpyxl``) or parquet (``pyarrow``) is missing.
    """
    if isinstance(source, pd.DataFrame):
        df = source.copy()
    else:
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(p)
        suf = p.suffix.lower()
        if suf in (".tsv", ".tab"):
            df = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False, **read_kw)
        elif suf in (".csv", ".txt"):
            kw = {"sep": None, "engine": "python", **read_kw}
            df = pd.read_csv(p, dtype=str, keep_default_na=False, **kw)
        elif suf in (".xlsx", ".xlsm", ".xls"):
            try:
                df = pd.read_excel(p, dtype=str, keep_default_na=False, na_values=[], **read_kw)
            except ImportError as e:  # pragma: no cover
                raise ConfigError("reading Excel needs: pip install 'fgpd-analyzer[xlsx]'") from e
        elif suf == ".parquet":
            try:
                df = pd.read_parquet(p, **read_kw)
            except ImportError as e:  # pragma: no cover
                raise ConfigError(
                    "reading parquet needs: pip install 'fgpd-analyzer[parquet]'"
                ) from e
        else:
            raise ConfigError(f"unsupported file type {suf!r}; use csv, tsv, xlsx or parquet")
    drop = [c for c in df.columns if str(c).startswith("Unnamed:") or str(c) == ""]
    return df.drop(columns=drop).reset_index(drop=True)
