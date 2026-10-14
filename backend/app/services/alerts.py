"""Rescate en 2 niveles (sección 6.7)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .. import config
from ..models import Alert, Course, Outbox
from .attendance import Indicators
from .risk import ACTIVO, EN_ADAPTACION, EN_RIESGO, INACTIVO
from .text import fecha_larga, key_time, plural

CHANNELS = ("portal", "email", "whatsapp")


def level1_text(ind: Indicators, course: Course) -> str:
    if ind.pending_items:
        first = ind.pending_items[0]
        n = len(ind.pending_items)
        return (f"Tienes {plural(n, 'clase')} por recuperar; la primera vence el {fecha_larga(first.deadline)}. "
                f"Te faltan {key_time(ind.pending_key_minutes)} y el quiz de cada clase.")
    return (f"Te faltan {plural(ind.missing, 'sesión', 'sesiones')} para llegar al {round(ind.threshold * 100)} % "
            "de asistencia. Revisa «Mi avance»: asiste en vivo o recupera cada clase dentro de los "
            f"{course.recovery_days} días siguientes.")


def open_alert(db: Session, user_id: int, course_id: int) -> Alert | None:
    return db.exec(select(Alert).where(Alert.user_id == user_id, Alert.course_id == course_id,
                                       Alert.status == "abierta").order_by(Alert.created_at.desc())).first()


def process_alert(db: Session, course: Course, user_id: int, state: str, reasons: list[str],
                  last_activity_at: datetime, ind: Indicators, now: datetime) -> Alert | None:
    alert = open_alert(db, user_id, course.id)
    reasons_json = json.dumps(reasons, ensure_ascii=False)

    if state in (ACTIVO, EN_ADAPTACION):
        if alert:
            alert.status, alert.closed_at, alert.outcome = "cerrada", now, alert.outcome or f"auto: volvió a {state}"
            db.add(alert)
        return alert

    if alert is None:
        if state == EN_RIESGO:
            alert = Alert(user_id=user_id, course_id=course.id, level=1, created_at=now, reasons_json=reasons_json)
            db.add(alert)
            db.flush()
            text = level1_text(ind, course)
            for channel in CHANNELS:
                db.add(Outbox(user_id=user_id, course_id=course.id, alert_id=alert.id, channel=channel, text=text, at=now))
        elif state == INACTIVO:
            alert = Alert(user_id=user_id, course_id=course.id, level=2, created_at=now, escalated_at=now,
                          assigned_role=course.level2_role, reasons_json=reasons_json)
            db.add(alert)
        return alert

    alert.reasons_json = reasons_json
    if alert.level == 1:
        waited = now - alert.created_at >= timedelta(hours=config.ESCALATION_HOURS)
        no_activity = last_activity_at <= alert.created_at
        if state == INACTIVO or (waited and no_activity):
            alert.level, alert.escalated_at, alert.assigned_role = 2, now, course.level2_role
    db.add(alert)
    return alert


def escalate_now(db: Session, course: Course, user_id: int, reason: str, now: datetime) -> Alert:
    """Alerta de nivel 2 inmediata (por ejemplo, una solicitud de baja asistida)."""
    alert = open_alert(db, user_id, course.id)
    reasons = json.loads(alert.reasons_json) if alert else []
    reasons.append(reason)
    if alert is None:
        alert = Alert(user_id=user_id, course_id=course.id, created_at=now)
    alert.level, alert.escalated_at, alert.assigned_role = 2, now, course.level2_role
    alert.reasons_json = json.dumps(reasons, ensure_ascii=False)
    db.add(alert)
    return alert
