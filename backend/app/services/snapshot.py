"""Copia la base de demo (SQLite generada por seed_demo.py en el build) a la base de trabajo (Postgres).

- `copy_snapshot(engine)` solo copia si la base está vacía (build de Vercel y arranque).
- `copy_snapshot(engine, force=True)` recrea las tablas y vuelve a copiar (POST /api/admin/reset-demo).
Corre en una sola transacción con un candado de Postgres, así dos instancias que arrancan a la vez no
duplican filas y la demo nunca queda a medio copiar. Al final ajusta las secuencias de los `id`.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from sqlalchemy import Engine, create_engine, func, select, text
from sqlmodel import SQLModel

from .. import config

LOCK_KEY = 20261012
CHUNK = 2000


class SnapshotError(RuntimeError):
    pass


def _source(path: Path) -> Engine:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    return create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True, check_same_thread=False))


def copy_snapshot(target: Engine, snapshot: Path | None = None, force: bool = False) -> dict:
    from .. import models  # noqa: F401  registra las tablas

    snapshot = snapshot or config.DB_SNAPSHOT
    if not snapshot or not snapshot.exists():
        raise SnapshotError("No hay base de demo para copiar (corre scripts/seed_demo.py o el build).")
    started = time.monotonic()
    metadata = SQLModel.metadata
    tables = metadata.sorted_tables
    src = _source(snapshot)
    counts: dict[str, int] = {}
    try:
        with target.begin() as dst:
            postgres = dst.dialect.name == "postgresql"
            quote = dst.dialect.identifier_preparer.quote
            if postgres:
                dst.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
            if force:  # recrea las tablas: también aplica cambios de esquema entre versiones
                metadata.drop_all(dst)
            metadata.create_all(dst)
            if not force and dst.execute(select(func.count()).select_from(models.User.__table__)).scalar_one():
                return {"copied": False, "reason": "la base ya tiene datos"}
            with src.connect() as s:
                for t in tables:
                    rows = [dict(r) for r in s.execute(select(t)).mappings()]
                    for i in range(0, len(rows), CHUNK):
                        dst.execute(t.insert(), rows[i:i + CHUNK])
                    counts[t.name] = len(rows)
            if postgres:
                for t in tables:
                    if "id" in t.c:
                        q = quote(t.name)
                        dst.execute(text(f"SELECT setval(pg_get_serial_sequence('{q}', 'id'), "
                                         f"COALESCE((SELECT MAX(id) FROM {q}), 1), "
                                         f"(SELECT MAX(id) FROM {q}) IS NOT NULL)"))
    finally:
        src.dispose()
    return {"copied": True, "rows": sum(counts.values()), "tables": len(counts),
            "seconds": round(time.monotonic() - started, 1)}
