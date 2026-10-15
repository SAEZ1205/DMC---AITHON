"""Ruteo del chat sin LLM (spec v2, 3.5.1).

- `plataforma`: plazos, recuperación, asistencia, certificado, nota o quiz → responde el backend con los datos
  reales del alumno.
- `logistica`: Zoom, enlaces, horario, grupo, materiales → respuesta fija con enlace a la página del curso.
- `contenido`: todo lo demás → tutor (LLM o su simulación).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session

from .. import config
from ..models import ClassSession, Course, User
from . import quiz as quiz_service
from .attendance import EN_VIVO, NO_CUMPLIDA, POR_RECUPERAR, RECUPERADA
from .recompute import compute_course
from .text import fecha_larga, key_time, plural, strip_accents

PLATAFORMA, LOGISTICA, CONTENIDO = "plataforma", "logistica", "contenido"

PLATFORM_PATTERNS = [
    r"\bplazos?\b", r"\bvence\b", r"\bvencen\b", r"\bvencimiento\b", r"\brecuperar(la|las)?\b", r"\brecupero\b",
    r"\brecuperacion\b", r"\basistencias?\b", r"\bcertificad[oa]s?\b", r"\bmi nota\b", r"\bnotas?\b",
    r"\bquiz aprobado\b", r"\bapro?be el quiz\b", r"\bcuantas? me faltan?\b", r"\bme faltan?\b", r"\bcuanto me falta\b",
    r"\bpendientes?\b", r"\bcuenta para\b",
]
LOGISTICS_PATTERNS = [
    r"\bzoom\b", r"\blinks?\b", r"\benlaces?\b", r"\bhorarios?\b", r"\bgrupos?\b", r"\bwhatsapp\b",
    r"\bmateriale?s?\b", r"\bdescarga(r)?\b", r"\bppt\b", r"\bdiapositivas? de la clase\b",
]


@dataclass
class RoutedAnswer:
    route: str
    text: str
    link: dict | None = None
    data: dict = field(default_factory=dict)


def normalize(message: str) -> str:
    return " ".join(strip_accents(message.lower()).replace("¿", " ").replace("?", " ").split())


def classify(message: str) -> str:
    text = normalize(message)
    if any(re.search(p, text) for p in PLATFORM_PATTERNS):
        return PLATAFORMA
    if any(re.search(p, text) for p in LOGISTICS_PATTERNS):
        return LOGISTICA
    return CONTENIDO


def _sessions_text(numbers: list[int]) -> str:
    if len(numbers) == 1:
        return f"la sesión {numbers[0]}"
    return "las sesiones " + ", ".join(str(n) for n in numbers[:-1]) + f" y {numbers[-1]}"


def platform_answer(db: Session, session: ClassSession, user: User, now: datetime) -> RoutedAnswer:
    course = db.get(Course, session.course_id)
    if user.role != config.STUDENT_ROLE:
        return RoutedAnswer(PLATAFORMA, "Los plazos y la asistencia se calculan para cada alumno; con tu rol no hay "
                                        "pendientes. Puedes revisarlos en el tablero de seguimiento.")
    _data, statuses = compute_course(db, course.id, user_ids=[user.id])
    st = statuses.get(user.id)
    if st is None:
        return RoutedAnswer(PLATAFORMA, "No encontré tu matrícula en este curso. Escríbele a tu Asistente Académico.")
    ind = st.indicators
    c = next((x for x in st.compliances if x.session_id == session.id), None)
    parts = []
    if c is None:
        parts.append(f"La sesión {session.number} todavía no se dicta.")
    elif c.status == EN_VIVO:
        parts.append(f"La sesión {session.number} ya cuenta para tu asistencia: asististe en vivo.")
    elif c.status == RECUPERADA:
        parts.append(f"La sesión {session.number} ya cuenta para tu asistencia: la recuperaste el {fecha_larga(c.completed_at)}.")
    elif c.status == NO_CUMPLIDA:
        parts.append(f"El plazo de la sesión {session.number} venció el {fecha_larga(c.deadline)}; ya no suma a tu asistencia.")
    elif c.status == POR_RECUPERAR:
        coverage_ok = c.key_coverage >= course.key_coverage_min - 1e-9
        quiz_ok = c.best_quiz is not None and c.best_quiz >= course.quiz_pass_score
        active = quiz_service.is_active(len(quiz_service.approved_items(db, session.id)))
        left = f"{key_time(c.key_minutes_left)}; llevas {round(c.key_coverage * 100)} % visto"
        if not coverage_ok and not quiz_ok:
            todo = f"Te faltan {left}, y el quiz."
        elif not coverage_ok:
            todo = f"Ya aprobaste el quiz; te faltan {left}."
        else:
            todo = "Ya viste los tramos clave; te falta aprobar el quiz."
        if not quiz_ok and not active:
            todo += " El quiz todavía está en preparación: el docente lo está revisando."
        parts.append(f"La sesión {session.number} vence el {fecha_larga(c.deadline)}. {todo}")
    pending = [x.number for x in st.compliances if x.status == POR_RECUPERAR and x.session_id != session.id]
    if pending:
        parts.append(f"También tienes por recuperar {_sessions_text(pending)}.")
    threshold = round(ind.threshold * 100)
    parts.append(f"Tu asistencia efectiva va en {ind.complied} de {plural(ind.dictated, 'sesión dictada', 'sesiones dictadas')} "
                 f"({round(ind.effective * 100)} %); para el certificado necesitas {threshold} % al final del curso.")
    return RoutedAnswer(PLATAFORMA, " ".join(parts), link={"href": "progress.html", "label": "Ver Mi avance"},
                        data={"status": c.status if c else None})


def logistics_answer(db: Session, session: ClassSession) -> RoutedAnswer:
    course = db.get(Course, session.course_id)
    schedule = course.schedule.rstrip(".")
    text = (f"El horario del curso es: {schedule}. Los enlaces de Zoom, el grupo del curso y los materiales "
            "de cada sesión están en la página del curso. Si algo no te funciona, escríbele a tu Asistente Académico.")
    return RoutedAnswer(LOGISTICA, text, link={"href": "course-detail.html", "label": "Ir a la página del curso"})
