from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from . import config

try:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:  # sistema de archivos de solo lectura (Vercel)
    pass


def _restore_snapshot() -> None:
    if not config.DB_SNAPSHOT or not config.DATABASE_URL.startswith("sqlite:///"):
        return
    target = Path(config.DATABASE_URL.removeprefix("sqlite:///"))
    if target.exists() or not config.DB_SNAPSHOT.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{config.DB_SNAPSHOT.as_posix()}?mode=ro&immutable=1", uri=True)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()


def make_engine(url: str):
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return eng
    # Postgres serverless (Neon): conexiones que se cortan por inactividad; pool chico por instancia.
    # El pooler de Neon es PgBouncer en modo transacción: sin sentencias preparadas del lado del servidor.
    return create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2, pool_recycle=300,
                         connect_args={"prepare_threshold": None})


_restore_snapshot()
engine = make_engine(config.DATABASE_URL)


def init_db(target_engine=None) -> None:
    from . import models  # noqa: F401  registra las tablas

    SQLModel.metadata.create_all(target_engine or engine)


def get_db() -> Iterator[Session]:
    with Session(engine) as db:
        yield db
