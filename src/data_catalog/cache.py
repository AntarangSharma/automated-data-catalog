"""SQLite enrichment cache keyed on SHA256(compiled_sql)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrichment (
    cache_key TEXT PRIMARY KEY,
    data      TEXT NOT NULL,
    cached_at TEXT NOT NULL
);
"""


def cache_key(compiled_sql: str | None) -> str:
    return hashlib.sha256((compiled_sql or "").encode("utf-8")).hexdigest()


class EnrichmentCache:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM enrichment WHERE cache_key = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, data: dict, *, cached_at: str = "1970-01-01T00:00:00Z") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO enrichment (cache_key, data, cached_at) VALUES (?, ?, ?)",
            (key, json.dumps(data), cached_at),
        )
        self._conn.commit()

    def has(self, key: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM enrichment WHERE cache_key = ?", (key,)
            ).fetchone()
            is not None
        )

    def close(self) -> None:
        self._conn.close()
