"""Portal del alumno: avance, sesiones, actividad, micro-preguntas, notificaciones, baja asistida y consentimiento."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

from .. import config
from ..db import get_db
from ..deps import current_user, get_course, get_session_or_404
from ..models import (ClassSession, Consent, Course, Enrollment, Heartbeat, MicroAnswer, Outbox, QuizAttempt, User,
                      WatchRange, WithdrawalRequest)
from ..services import alerts
from ..services.attendance import certification
from ..services.chat import classify_micro
from ..services.recompute import compute_course, recompute_course
from ..services.text import fecha_larga
from ..views import STATUS_LABELS, compliance_json, indicators_json, iso

router = APIRouter(tags=["alumno"])


def _student_status(db: Session, course_id: int, user: User):
    data, statuses = compute_course(db, course_id, user_ids=[user.id])
    if user.id not in statuses:
        raise HTTPException(404, "No estás matriculado en este curso")
    return data, statuses[user.id]


@router.get("/me/courses")
def my_courses(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    courses = db.exec(select(Course).join(Enrollment, Enrollment.course_id == Course.id)
                      .where(Enrollment.user_id == user.id)).all()
    out = []
    for c in courses:
        _, st = _student_status(db, c.id, user)
        out.append({"id": c.id, "name": c.name, "schedule": c.schedule, **indicators_json(st.indicators)})
    return out


@router.get("/courses/{course_id}")
def course_detail(course_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    course = get_course(db, course_id)
    teacher = db.get(User, course.teacher_id) if course.teacher_id else None
    assistant = db.get(User, course.academic_assistant_id) if course.academic_assistant_id else None
    days, _, hours = course.schedule.partition(", ")
    return {"id": course.id, "name": course.name, "start_date": course.start_date.isoformat(),
            "end_date": course.end_date.isoformat(),
            "start_text": f"{fecha_larga(course.start_date)}, {course.start_date.year}",
            "end_text": f"{fecha_larga(course.end_date)}, {course.end_date.year}",
            "schedule": course.schedule, "days": days, "hours": hours, "total_sessions": course.total_sessions,
            "teacher": teacher.name if teacher else None, "academic_assistant": assistant.name if assistant else None}


@router.get("/courses/{course_id}/progress")
def progress(course_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    course = get_course(db, course_id)
    data, st = _student_status(db, course_id, user)
    now = config.now()
    titles = {s.id: s.title for s in data.sessions}
    finished = bool(data.sessions) and data.sessions[-1].ends_at <= now
    assistant = db.get(User, course.academic_assistant_id) if course.academic_assistant_id else None
    pending = []
    for c in st.indicators.pending_items:
        item = compliance_json(c, now)
        item["title"] = titles.get(c.session_id, "")
        pending.append(item)
    quizzes = [{"session_id": c.session_id, "number": c.number, "title": titles.get(c.session_id, ""),
                "best": c.best_quiz, "status": c.status}
               for c in st.compliances if c.best_quiz is not None]
    return {
        "course": {"id": course.id, "name": course.name, "schedule": course.schedule,
                   "recovery_days": course.recovery_days, "quiz_pass_score": course.quiz_pass_score,
                   "key_coverage_min": course.key_coverage_min, "live_min_ratio": course.live_min_ratio,
                   "academic_assistant": assistant.name if assistant else None},
        "now": now.isoformat(),
        **indicators_json(st.indicators),
        "pending_items": pending,
        "quizzes": quizzes,
        "certification": certification(st.indicators, st.student.grade, st.student.project_status, finished),
    }


@router.get("/courses/{course_id}/sessions")
def sessions_list(course_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    data, st = _student_status(db, course_id, user)
    now = config.now()
    by_id = {c.session_id: c for c in st.compliances}
    out = []
    for s in data.sessions:
        row = {"id": s.id, "number": s.number, "title": s.title, "starts_at": iso(s.starts_at),
               "ends_at": iso(s.ends_at)}
        c = by_id.get(s.id)
        if c:
            row.update(compliance_json(c, now))
        else:
            row.update({"status": "proxima", "status_label": STATUS_LABELS["proxima"]})
        out.append(row)
    return out


class WatchIn(BaseModel):
    session_id: int
    ranges: list[tuple[float, float]] = Field(max_length=500)


@router.post("/watch")
def watch(body: WatchIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    session = get_session_or_404(db, body.session_id)
    duration = (session.ends_at - session.starts_at).total_seconds()
    now = config.now()
    for a, b in body.ranges:
        a, b = max(0.0, min(a, b)), min(duration, max(a, b))
        if b - a >= 1:
            db.add(WatchRange(user_id=user.id, session_id=session.id, start_s=round(a, 1), end_s=round(b, 1), at=now))
    db.commit()
    statuses = recompute_course(db, session.course_id, now, user_ids=[user.id])
    st = statuses.get(user.id)
    c = next((c for c in st.compliances if c.session_id == session.id), None) if st else None
    return {"key_coverage": round(c.key_coverage, 3) if c else None, "status": c.status if c else None}


class HeartbeatIn(BaseModel):
    course_id: int
    page: str = ""
    active_seconds: int = Field(ge=0, le=120)


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.add(Heartbeat(user_id=user.id, course_id=body.course_id, page=body.page[:120],
                     active_seconds=min(body.active_seconds, 60), at=config.now()))
    db.commit()
    return {"ok": True}


def _pending_micro(db: Session, user: User, course_id: int) -> str | None:
    now = config.now()
    recent_quiz = db.exec(select(QuizAttempt).join(ClassSession, ClassSession.id == QuizAttempt.session_id)
                          .where(QuizAttempt.user_id == user.id, ClassSession.course_id == course_id,
                                 QuizAttempt.at <= now, QuizAttempt.at >= now - timedelta(hours=24))).first()
    if recent_quiz is None:
        return None
    last = db.exec(select(MicroAnswer).where(MicroAnswer.user_id == user.id, MicroAnswer.course_id == course_id,
                                             MicroAnswer.at <= now).order_by(MicroAnswer.at.desc())).first()
    if last and now - last.at < timedelta(days=config.MICRO_EVERY_DAYS):
        return None
    asked = db.exec(select(func.count()).select_from(MicroAnswer).where(
        MicroAnswer.user_id == user.id, MicroAnswer.course_id == course_id)).one()
    return config.MICRO_QUESTIONS[asked % len(config.MICRO_QUESTIONS)]


@router.get("/me/micro-question")
def get_micro(course_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"question": _pending_micro(db, user, course_id)}


class MicroIn(BaseModel):
    course_id: int
    question: str
    answer: str | None = Field(default=None, max_length=2000)


@router.post("/me/micro-question")
def post_micro(body: MicroIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if body.question not in config.MICRO_QUESTIONS:
        raise HTTPException(400, "Pregunta desconocida")
    answer = (body.answer or "").strip() or None
    row = MicroAnswer(user_id=user.id, course_id=body.course_id, question=body.question, answer=answer,
                      at=config.now())
    if answer:
        result = classify_micro(body.question, answer)
        row.topic, row.sentiment, row.summary = result.topic, result.sentiment, result.summary
    db.add(row)
    db.commit()
    recompute_course(db, body.course_id, user_ids=[user.id])
    return {"saved": True, "answered": answer is not None, "topic": row.topic, "sentiment": row.sentiment}


@router.get("/me/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    now = config.now()
    rows = db.exec(select(Outbox).where(Outbox.user_id == user.id, Outbox.channel == "portal", Outbox.at <= now)
                   .order_by(Outbox.at.desc()).limit(20)).all()
    return {"unread": sum(not r.read for r in rows),
            "items": [{"id": r.id, "text": r.text, "at": iso(r.at), "read": r.read} for r in rows]}


@router.post("/me/notifications/read")
def notifications_read(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    for row in db.exec(select(Outbox).where(Outbox.user_id == user.id, Outbox.read == False)).all():  # noqa: E712
        row.read = True
        db.add(row)
    db.commit()
    return {"ok": True}


class WithdrawalIn(BaseModel):
    reason: Literal["tiempo", "trabajo", "contenido", "economico", "salud", "otro"]
    comment: str | None = Field(default=None, max_length=2000)
    choice: Literal["recuperar", "pausa", "otra_cohorte", "hablar_asistente", "confirmar_retiro"]


@router.post("/courses/{course_id}/withdrawal")
def withdrawal(course_id: int, body: WithdrawalIn, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict:
    course = get_course(db, course_id)
    now = config.now()
    offered = ["recuperar", "pausa", "otra_cohorte", "hablar_asistente"]
    db.add(WithdrawalRequest(user_id=user.id, course_id=course.id, reason=body.reason, comment=body.comment,
                             offered_json=json.dumps(offered), outcome=body.choice, at=now))
    labels = {"recuperar": "quiere recuperar con grabaciones", "pausa": "pide pausar y retomar en otra cohorte",
              "otra_cohorte": "pide cambiar de cohorte", "hablar_asistente": "quiere hablar con el Asistente Académico",
              "confirmar_retiro": "confirmó su solicitud de retiro"}
    alerts.escalate_now(db, course, user.id, f"Solicitud de retiro (motivo: {body.reason}); {labels[body.choice]}", now)
    db.commit()
    assistant = db.get(User, course.academic_assistant_id) if course.academic_assistant_id else None
    return {"saved": True, "message": f"Registramos tu solicitud. {assistant.name if assistant else 'El Asistente Académico'} "
                                      "te contactará en las próximas 48 horas."}


class ConsentIn(BaseModel):
    version: str = config.CONSENT_VERSION


@router.post("/consent")
def consent(body: ConsentIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.add(Consent(user_id=user.id, version=body.version, at=config.now()))
    db.commit()
    return {"ok": True}
