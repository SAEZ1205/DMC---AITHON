"""Accesos de demostración (sin contraseñas)."""
from __future__ import annotations

import csv
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import config
from ..db import get_db
from ..deps import current_user
from ..models import Consent, Course, Enrollment, User
from ..services.recompute import compute_course
from ..services.risk import EN_RIESGO

router = APIRouter(tags=["auth"])


class DemoLogin(BaseModel):
    role: Literal["alumno", "tutor"]
    user_id: int | None = None


def user_json(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


def _profiles() -> dict[str, str]:
    path = config.COHORT_DIR / "ground_truth.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {row["email"]: row["profile"] for row in csv.DictReader(fh)}


def _demo_student(db: Session) -> User:
    """Alumno sugerido para la demo: un perfil cambio_horario en riesgo, con proyección bajo el umbral
    (lo más cerca posible) y clases por recuperar."""
    course = db.exec(select(Course)).first()
    profiles = _profiles()
    if course:
        _, statuses = compute_course(db, course.id)

        def rank(st):
            ind = st.indicators
            return (profiles.get(st.student.email) != "cambio_horario", st.risk.state != EN_RIESGO,
                    ind.projection >= ind.threshold, -min(ind.pending, 2), ind.threshold - ind.projection)

        ranked = sorted(statuses.values(), key=rank)
        if ranked:
            return db.get(User, ranked[0].student.user_id)
    student = db.exec(select(User).where(User.role == config.STUDENT_ROLE)).first()
    if student is None:
        raise HTTPException(503, "No hay datos: ejecuta scripts/seed_demo.py")
    return student


@router.post("/auth/demo")
def demo_login(body: DemoLogin, db: Session = Depends(get_db)) -> dict:
    if body.user_id:
        user = db.get(User, body.user_id)
        if user is None:
            raise HTTPException(404, "Usuario no encontrado")
    elif body.role == "alumno":
        user = _demo_student(db)
    else:
        course = db.exec(select(Course)).first()
        user = db.get(User, course.academic_assistant_id) if course and course.academic_assistant_id else \
            db.exec(select(User).where(User.role.in_(config.TUTOR_ROLES))).first()
    if user is None:
        raise HTTPException(503, "No hay datos: ejecuta scripts/seed_demo.py")
    return user_json(user)


@router.get("/auth/demo-users")
def demo_users(db: Session = Depends(get_db)) -> dict:
    profiles = _profiles()
    students = db.exec(select(User).where(User.role == config.STUDENT_ROLE).order_by(User.name)).all()
    staff = db.exec(select(User).where(User.role.in_(config.TUTOR_ROLES)).order_by(User.name)).all()
    return {
        "students": [{**user_json(u), "profile": profiles.get(u.email)} for u in students],
        "staff": [user_json(u) for u in staff],
    }


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    consent = db.exec(select(Consent).where(Consent.user_id == user.id, Consent.version == config.CONSENT_VERSION)).first()
    if user.role in config.TUTOR_ROLES:
        courses = db.exec(select(Course)).all()
    else:
        courses = db.exec(select(Course).join(Enrollment, Enrollment.course_id == Course.id)
                          .where(Enrollment.user_id == user.id)).all()
    return {
        **user_json(user),
        "is_staff": user.role in config.TUTOR_ROLES,
        "consent": consent is not None,
        "consent_version": config.CONSENT_VERSION,
        "courses": [{"id": c.id, "name": c.name} for c in courses],
        "now": config.now().isoformat(),
        "llm_mode": "simulado" if config.LLM_MOCK else config.LLM_MODEL_CHAT,
    }
