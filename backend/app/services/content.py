"""Contenido de una sesión a partir de su transcripción y materiales: capítulos, tramos clave y quiz."""
from __future__ import annotations

from sqlmodel import Session

from ..models import ClassSession
from .processing import process_session


def rebuild(db: Session, session: ClassSession) -> dict:
    return process_session(db, session, force=True)
