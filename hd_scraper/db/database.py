"""Acceso a la base de datos (SQLite en Fase 1).

Wrapper delgado sobre ``sqlite3`` con la misma superficie que necesitaríamos
en Postgres (execute / fetch_one / fetch_all / executescript). El esquema vive
en ``schema.sql`` y es portable. Migrar a Postgres = cambiar este wrapper por
uno equivalente (p. ej. psycopg) sin tocar el modelo ni el pipeline.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config import settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class Database:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            sqlite_path = settings.sqlite_path
            if sqlite_path is None:
                raise ValueError(
                    "HD_DATABASE_URL no es SQLite; la Fase 1 solo soporta sqlite:///"
                )
            path = sqlite_path
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA foreign_keys = ON;")

    # -- Inicialización -------------------------------------------------
    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    # -- Operaciones ----------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return cur

    def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


_db_singleton: Database | None = None


def get_db() -> Database:
    """Instancia compartida (para API/scheduler). Crea el esquema si falta."""
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = Database()
        _db_singleton.init_schema()
    return _db_singleton
