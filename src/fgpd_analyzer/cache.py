"""Local response cache (SQLite). It holds pseudonymized model output only,
keyed by a hash of everything that determines the request."""

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
    from platformdirs import user_cache_path

    return Path(user_cache_path("fgpd-analyzer"))


class ResponseCache:
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
        row = self._db.execute("SELECT response FROM responses WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return LLMResponse.from_dict(json.loads(row[0]), from_cache=True)
        except (ValueError, KeyError) as e:
            raise CacheError(f"corrupt cache entry {key[:12]}") from e

    def put(self, key: str, response: LLMResponse) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO responses (key, response, created) VALUES (?, ?, ?)",
            (key, json.dumps(response.to_dict()), datetime.now(timezone.utc).isoformat()),
        )
        self._db.commit()

    def __len__(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM responses").fetchone()[0])

    def items(self) -> Iterator[tuple[str, dict[str, Any]]]:
        for key, resp in self._db.execute("SELECT key, response FROM responses ORDER BY key"):
            yield key, json.loads(resp)

    def export_jsonl(self, path: Path, keys: set[str] | None = None) -> Path:
        """Write records usable by `ReplayBackend`."""
        with Path(path).open("w", encoding="utf-8") as f:
            for key, resp in self.items():
                if keys is None or key in keys:
                    f.write(json.dumps({"cache_key": key, "response": resp}) + "\n")
        return Path(path)

    def clear(self) -> int:
        n = len(self)
        self._db.execute("DELETE FROM responses")
        self._db.commit()
        return n

    def close(self) -> None:
        self._db.close()
