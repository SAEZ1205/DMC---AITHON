"""Recálculo de cumplimiento, variables, estados y alertas."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, delete, select

from .. import config
from ..models import RiskSnapshot, SessionCompliance, StudentFeatures
from . import alerts
from .attendance import Compliance, Indicators, evaluate_student, indicators
from .features import CurrentFeatures, SessionFeatures, current_features, session_features
from .risk import RiskResult, classify
from .timeline import CourseData, StudentData, load_course_data


@dataclass
class StudentStatus:
    student: StudentData
    compliances: list[Compliance]
    indicators: Indicators
    sessions: list[SessionFeatures]
    current: CurrentFeatures
    risk: RiskResult


def student_status(student: StudentData, data: CourseData, now: datetime) -> StudentStatus:
    compliances = evaluate_student(student, data, now)
    ind = indicators(compliances, data.course)
    cur = current_features(student, data, compliances, ind, now)
    return StudentStatus(student, compliances, ind, session_features(student, compliances), cur,
                         classify(cur, ind, data.course))


def _persist(db: Session, data: CourseData, st: StudentStatus, now: datetime) -> None:
    uid, course = st.student.user_id, data.course
    session_ids = [s.id for s in data.sessions]
    db.exec(delete(SessionCompliance).where(SessionCompliance.user_id == uid,
                                            SessionCompliance.session_id.in_(session_ids)))
    for c in st.compliances:
        db.add(SessionCompliance(user_id=uid, session_id=c.session_id, status=c.status, completed_at=c.completed_at,
                                 deadline=c.deadline, live_ratio=round(c.live.live_ratio, 3),
                                 key_coverage=round(c.key_coverage, 3), best_quiz=c.best_quiz))

    db.exec(delete(StudentFeatures).where(StudentFeatures.user_id == uid, StudentFeatures.course_id == course.id))
    cur = st.current
    for i, sf in enumerate(st.sessions):
        row = StudentFeatures(
            user_id=uid, course_id=course.id, session_id=sf.session_id, live_ratio=sf.live_ratio,
            late_min=sf.late_min, early_leave_min=sf.early_leave_min, reconnects=sf.reconnects, gap_min=sf.gap_min,
            chat_msgs=sf.chat_msgs, voice_turns=sf.voice_turns, key_coverage=sf.key_coverage,
            quiz_score=sf.quiz_score, computed_at=now,
        )
        if i == len(st.sessions) - 1:  # las variables actuales y derivadas van en la última sesión
            row.active_min_week = cur.active_min_week
            row.days_since_activity = cur.days_since_activity
            row.study_hour_mode = cur.study_hour_mode
            row.questions_content, row.questions_logistics = cur.questions_content, cur.questions_logistics
            row.quiz_trend = cur.quiz_trend
            row.micro_topic = cur.micro.topic if cur.micro else None
            row.micro_sentiment = cur.micro.sentiment if cur.micro else None
            row.pattern_drop, row.late_shift, row.mode_shift = cur.pattern_drop, cur.late_shift, cur.mode_shift
            row.projection = round(cur.projection, 4)
        db.add(row)

    reasons_json = json.dumps(st.risk.reasons, ensure_ascii=False)
    last = db.exec(select(RiskSnapshot).where(RiskSnapshot.user_id == uid, RiskSnapshot.course_id == course.id)
                   .order_by(RiskSnapshot.at.desc())).first()
    if last is None or last.state != st.risk.state or last.reasons_json != reasons_json \
            or now - last.at > timedelta(days=1):
        db.add(RiskSnapshot(user_id=uid, course_id=course.id, at=now, state=st.risk.state,
                            reasons_json=reasons_json, projection=round(st.indicators.projection, 4)))

    alerts.process_alert(db, course, uid, st.risk.state, st.risk.reasons, cur.last_activity_at, st.indicators, now)


def recompute_course(db: Session, course_id: int, now: datetime | None = None,
                     user_ids: list[int] | None = None) -> dict[int, StudentStatus]:
    now = now or config.now()
    data = load_course_data(db, course_id, user_ids)
    out = {}
    for student in data.students.values():
        st = student_status(student, data, now)
        _persist(db, data, st, now)
        out[student.user_id] = st
    db.commit()
    return out


def compute_course(db: Session, course_id: int, now: datetime | None = None,
                   user_ids: list[int] | None = None) -> tuple[CourseData, dict[int, StudentStatus]]:
    """Cálculo en memoria, sin escribir en la base (lecturas del portal y del tablero)."""
    now = now or config.now()
    data = load_course_data(db, course_id, user_ids)
    return data, {uid: student_status(s, data, now) for uid, s in data.students.items()}
