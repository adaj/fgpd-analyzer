"""Reading chat tables from files or DataFrames."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .exceptions import ConfigError

Source = Any  # pd.DataFrame | str | os.PathLike


def read_source(source: Source, **read_kw: Any) -> pd.DataFrame:
    """Return a raw DataFrame from a DataFrame, .csv, .tsv, .txt, .xlsx/.xls or .parquet.

    Unnamed index columns written by pandas (``Unnamed: 0``) are dropped.
    Everything is read as strings, so that ids such as ``007`` survive.
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
                df = pd.read_excel(p, dtype=str, **read_kw)
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
