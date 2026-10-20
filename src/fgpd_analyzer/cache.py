"""Local response cache (SQLite).

Used by the runs stage of the pipeline (input → units → prompts → requests →
**runs** → post-validation → ...): before a request is sent, its cache key is
looked up here, and every successful response is stored. The cache holds
pseudonymized model output only, keyed by a hash of everything that
determines the request, so a rerun with the same input and settings costs
nothing. Entries can be exported as JSONL for `ReplayBackend`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backends.base import LLMResponse
from .exceptions import CacheError


def default_cache_dir() -> Path:
    """Return the per-user cache directory (``platformdirs.user_cache_path("fgpd-analyzer")``)."""
    from platformdirs import user_cache_path

    return Path(user_cache_path("fgpd-analyzer"))


class ResponseCache:
    """SQLite store of model responses, keyed by request cache key.

    Parameters
    ----------
    directory : pathlib.Path, optional
        Directory of ``responses.sqlite``; created if needed. Defaults to
        `default_cache_dir`.

    Attributes
    ----------
    directory : pathlib.Path
    path : pathlib.Path
        The database file.

    Raises
    ------
    CacheError
        If the database cannot be opened.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory else default_cache_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "responses.sqlite"
        try:
            self._db = sqlite3.connect(self.path)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                "key TEXT PRIMARY KEY, response TEXT NOT NULL, created TEXT NOT NULL)"
            )
            self._db.commit()
        except sqlite3.DatabaseError as e:
            raise CacheError(f"cannot open cache {self.path}: {e}") from e

    def get(self, key: str) -> LLMResponse | None:
        """Return the cached response for `key` (marked ``from_cache``), or None.

        Raises
        ------
        CacheError
            If the stored entry is corrupt.
        """
        row = self._db.execute("SELECT response FROM responses WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return LLMResponse.from_dict(json.loads(row[0]), from_cache=True)
        except (ValueError, KeyError) as e:
            raise CacheError(f"corrupt cache entry {key[:12]}") from e

    def put(self, key: str, response: LLMResponse) -> None:
        """Store (or replace) the response for `key`."""
        self._db.execute(
            "INSERT OR REPLACE INTO responses (key, response, created) VALUES (?, ?, ?)",
            (key, json.dumps(response.to_dict()), datetime.now(timezone.utc).isoformat()),
        )
        self._db.commit()

    def __len__(self) -> int:
        """Return the number of stored responses."""
        return int(self._db.execute("SELECT COUNT(*) FROM responses").fetchone()[0])

    def items(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield ``(key, response dict)`` pairs in key order."""
        for key, resp in self._db.execute("SELECT key, response FROM responses ORDER BY key"):
            yield key, json.loads(resp)

    def export_jsonl(self, path: Path, keys: set[str] | None = None) -> Path:
        """Write cached responses as JSONL records usable by `ReplayBackend`.

        Parameters
        ----------
        path : pathlib.Path
            Output file, one ``{"cache_key": ..., "response": {...}}`` per line.
        keys : set of str, optional
            Export only these keys (e.g. ``run_metadata["cache_keys"]`` of one
            analysis). All entries when omitted.

        Returns
        -------
        pathlib.Path
        """
        with Path(path).open("w", encoding="utf-8") as f:
            for key, resp in self.items():
                if keys is None or key in keys:
                    f.write(json.dumps({"cache_key": key, "response": resp}) + "\n")
        return Path(path)

    def clear(self) -> int:
        """Delete every entry and return how many there were."""
        n = len(self)
        self._db.execute("DELETE FROM responses")
        self._db.commit()
        return n

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()
