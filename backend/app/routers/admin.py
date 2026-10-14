"""Operación: ingesta de Zoom, recálculo, reloj simulado, reinicio de la demo y estado del LLM (solo staff)."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import config
from ..db import engine, get_db
from ..deps import get_session_or_404, require_tutor
from ..models import Course
from ..services import content, llm, snapshot, tutor_chat
from ..services.real_session import apply_to_session, has_real_content
from ..services.recompute import recompute_course
from ..services.zoom_ingest import ingest_session

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_tutor)])


@router.post("/ingest/{session_id}")
def ingest(session_id: int, process: bool = True, db: Session = Depends(get_db)) -> dict:
    s = get_session_or_404(db, session_id)
    folder = config.ZOOM_DIR / str(s.id)
    if not folder.exists():
        raise HTTPException(404, f"No existe la carpeta data/zoom/{s.id}/ con los archivos de Zoom")
    report = ingest_session(db, s, folder)
    if has_real_content(folder):
        apply_to_session(db, s, folder)
    db.commit()
    processed = content.rebuild(db, s) if process else None
    recompute_course(db, s.course_id)
    return {"ingest": asdict(report), "processed": processed}


class RecomputeIn(BaseModel):
    course_id: int | None = None
    now: datetime | None = None


@router.post("/recompute")
def recompute(body: RecomputeIn | None = None, db: Session = Depends(get_db)) -> dict:
    body = body or RecomputeIn()
    query = select(Course)
    if body.course_id:
        query = query.where(Course.id == body.course_id)
    out = {}
    for course in db.exec(query).all():
        statuses = recompute_course(db, course.id, body.now)
        out[course.id] = dict(Counter(st.risk.state for st in statuses.values()))
    return {"now": (body.now or config.now()).isoformat(), "courses": out}


@router.post("/reset-demo")
def reset_demo(db: Session = Depends(get_db)) -> dict:
    """Vuelve la demo al estado inicial copiando otra vez la base generada en el build."""
    if not config.DB_SNAPSHOT:
        raise HTTPException(400, "En local con SQLite, vuelve a correr scripts/seed_demo.py con el servidor detenido.")
    db.close()  # la sesión de la autenticación leyó «user»: en Postgres ese candado bloquearía el DROP TABLE
    try:
        result = snapshot.copy_snapshot(engine, force=True)
    except snapshot.SnapshotError as e:
        raise HTTPException(400, str(e)) from e
    tutor_chat.clear_cache()
    return result


@router.get("/llm")
def llm_status() -> dict:
    """Modo del LLM, cupo usado (minuto y día) y veces que se respondió con la simulación por falta de cupo."""
    return llm.status()


class ClockIn(BaseModel):
    now: datetime | None = None


@router.get("/clock")
def get_clock() -> dict:
    return {"now": config.now().isoformat()}


@router.post("/clock")
def set_clock(body: ClockIn) -> dict:
    config.set_now(body.now.replace(tzinfo=None) if body.now else None)
    return {"now": config.now().isoformat()}
