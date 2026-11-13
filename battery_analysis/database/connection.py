"""engine/connection factory, one SQLite file per project."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from .schema import init_schema

# CRITICAL FIX: sqlite3 silently returns ZERO rows (no error!) when a
# query parameter is a numpy integer (np.int64/np.int32) instead of a
# plain Python int — which is exactly what you get any time an ID comes
# from a pandas DataFrame (e.g. row["measurement_id"], very common
# throughout the plotting code). Registering these adapters once, for
# every connection, fixes it everywhere instead of requiring int(...) at
# every call site — and protects every future technique's code too.
sqlite3.register_adapter(np.int64, int)
sqlite3.register_adapter(np.int32, int)


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the project's SQLite database."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn
