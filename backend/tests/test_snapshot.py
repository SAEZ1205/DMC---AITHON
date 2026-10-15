"""Copia de la base de demo a la base de trabajo y reset de la demo."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import config
from app.db import init_db, make_engine
from app.main import app
from app.models import ClassSession, Course, User
from app.services.snapshot import SnapshotError, copy_snapshot


def _snapshot(path) -> None:
    eng = make_engine(f"sqlite:///{path.as_posix()}")
    init_db(eng)
    with Session(eng) as db:
        db.add(User(name="Ana", email="ana@dmc.pe", role="alumno"))
        db.add(User(name="Michela", email="michela@dmc.pe", role="asistente_academico"))
        db.add(Course(name="Curso", start_date=date(2026, 7, 21), end_date=date(2026, 12, 1), total_sessions=1,
                      schedule="mar/jue", **config.COURSE_DEFAULTS))
        db.add(ClassSession(course_id=1, number=1, starts_at=datetime(2026, 7, 21, 19, 30),
                            ends_at=datetime(2026, 7, 21, 22, 30), processed=True))
        db.commit()
    with eng.connect() as conn:  # deja el archivo autocontenido, como el build
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    eng.dispose()


def test_copy_only_when_empty_and_force_replaces(tmp_path):
    snap = tmp_path / "snap.db"
    _snapshot(snap)
    target = make_engine(f"sqlite:///{(tmp_path / 'work.db').as_posix()}")

    first = copy_snapshot(target, snap)
    assert first["copied"] and first["rows"] == 4
    with Session(target) as db:
        session = db.exec(select(ClassSession)).one()
        assert session.starts_at == datetime(2026, 7, 21, 19, 30) and session.processed is True
        db.add(User(name="Nuevo", email="nuevo@correo.pe", role="alumno"))
        db.commit()

    assert copy_snapshot(target, snap) == {"copied": False, "reason": "la base ya tiene datos"}
    assert copy_snapshot(target, snap, force=True)["copied"]
    with Session(target) as db:
        assert [u.name for u in db.exec(select(User).order_by(User.id))] == ["Ana", "Michela"]
    target.dispose()


def test_missing_snapshot_raises(tmp_path):
    with pytest.raises(SnapshotError):
        copy_snapshot(make_engine("sqlite://"), tmp_path / "no-existe.db")


def test_postgres_urls_use_psycopg():
    assert config._normalize_db_url("postgres://u:p@h/db?sslmode=require") == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert config._normalize_db_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert config._normalize_db_url("sqlite:///x.db") == "sqlite:///x.db"


def test_reset_demo_restores_snapshot(db, tmp_path, monkeypatch):
    snap = tmp_path / "snap.db"
    _snapshot(snap)
    monkeypatch.setattr(config, "DB_SNAPSHOT", snap)
    staff = User(name="Michela", email="michela@dmc.pe", role="asistente_academico")
    db.add(staff)
    db.add(User(name="Temporal", email="temp@correo.pe", role="alumno"))
    db.commit()
    with TestClient(app) as client:
        res = client.post("/api/admin/reset-demo", headers={"X-User-Id": str(staff.id)})
    assert res.status_code == 200 and res.json()["copied"] and res.json()["rows"] == 4
    db.expire_all()
    assert [u.name for u in db.exec(select(User).order_by(User.id))] == ["Ana", "Michela"]


def test_reset_demo_is_staff_only_and_needs_snapshot(db):
    student = User(name="Ana", email="ana@dmc.pe", role="alumno")
    staff = User(name="Michela", email="michela@dmc.pe", role="asistente_academico")
    db.add(student)
    db.add(staff)
    db.commit()
    with TestClient(app) as client:
        assert client.post("/api/admin/reset-demo", headers={"X-User-Id": str(student.id)}).status_code == 403
        res = client.post("/api/admin/reset-demo", headers={"X-User-Id": str(staff.id)})
        assert res.status_code == 400 and "seed_demo" in res.json()["detail"]
