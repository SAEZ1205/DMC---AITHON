"""Dependencias comunes. Autenticación de demostración: el frontend envía el id del usuario en X-User-Id
(o ?uid= para navigator.sendBeacon, que no permite cabeceras)."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Query
from sqlmodel import Session

from . import config
from .db import get_db
from .models import ClassSession, Course, User


def current_user(
    x_user_id: int | None = Header(default=None),
    uid: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> User:
    user_id = x_user_id or uid
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(401, "Inicia sesión con un acceso de demostración")
    return user


def require_tutor(user: User = Depends(current_user)) -> User:
    if user.role not in config.TUTOR_ROLES:
        raise HTTPException(403, "Solo para tutor, asistente académico, docente o coordinador")
    return user


def get_course(db: Session, course_id: int) -> Course:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(404, "Curso no encontrado")
    return course


def get_session_or_404(db: Session, session_id: int) -> ClassSession:
    session = db.get(ClassSession, session_id)
    if session is None:
        raise HTTPException(404, "Sesión no encontrada")
    return session
