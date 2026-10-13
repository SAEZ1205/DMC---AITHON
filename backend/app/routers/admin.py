"""Operación: ingesta de Zoom, recálculo y reloj simulado (solo staff)."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import config
from ..db import get_db
from ..deps import get_session_or_404, require_tutor
from ..models import Course
from ..services.processing import process_session
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
    db.commit()
    processed = None
    if process:
        result = process_session(db, s)
        processed = {"chapters": len(result["chapters"]), "quiz": len(result["quiz"])}
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


class ClockIn(BaseModel):
    now: datetime | None = None


@router.get("/clock")
def get_clock() -> dict:
    return {"now": config.now().isoformat()}


@router.post("/clock")
def set_clock(body: ClockIn) -> dict:
    config.set_now(body.now.replace(tzinfo=None) if body.now else None)
    return {"now": config.now().isoformat()}
