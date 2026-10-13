"""Tablero del tutor / Asistente Académico (solo roles de staff)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import config
from ..db import get_db
from ..deps import get_course, get_session_or_404, require_tutor
from ..models import (Alert, Chapter, ClassSession, MicroAnswer, Outbox, RiskSnapshot, User, WithdrawalRequest)
from ..services.attendance import EN_VIVO, RECUPERADA
from ..services.recompute import StudentStatus, compute_course, recompute_course
from ..services.risk import ACTIVO, EN_ADAPTACION, EN_RIESGO, INACTIVO
from ..views import STATUS_LABELS, compliance_json, indicators_json, iso

router = APIRouter(prefix="/tutor", tags=["tutor"], dependencies=[Depends(require_tutor)])

STATE_LABELS = {ACTIVO: "Activo", EN_ADAPTACION: "En adaptación", EN_RIESGO: "En riesgo", INACTIVO: "Inactivo"}
STATE_ORDER = {INACTIVO: 0, EN_RIESGO: 1, EN_ADAPTACION: 2, ACTIVO: 3}


def _open_alerts(db: Session, course_id: int, now: datetime) -> dict[int, Alert]:
    rows = db.exec(select(Alert).where(Alert.course_id == course_id, Alert.status == "abierta",
                                       Alert.created_at <= now)).all()
    return {a.user_id: a for a in rows}


def _student_row(st: StudentStatus, alert: Alert | None) -> dict:
    cur = st.current
    return {
        "user_id": st.student.user_id,
        "name": st.student.name,
        "email": st.student.email,
        "state": st.risk.state,
        "state_label": STATE_LABELS[st.risk.state],
        "reasons": st.risk.reasons,
        **indicators_json(st.indicators),
        "last_activity": iso(cur.last_activity_at),
        "days_since_activity": round(cur.days_since_activity, 1),
        "quiz_recent_mean": round(cur.quiz_recent_mean, 2) if cur.quiz_recent_mean is not None else None,
        "alert": {"id": alert.id, "level": alert.level, "created_at": iso(alert.created_at),
                  "escalated_at": iso(alert.escalated_at)} if alert else None,
    }


@router.get("/courses/{course_id}/students")
def students(course_id: int, db: Session = Depends(get_db)) -> dict:
    course = get_course(db, course_id)
    now = config.now()
    data, statuses = compute_course(db, course.id, now)
    alerts = _open_alerts(db, course.id, now)
    rows = sorted((_student_row(st, alerts.get(uid)) for uid, st in statuses.items()),
                  key=lambda r: (STATE_ORDER[r["state"]], r["projection"]))
    counts = Counter(r["state"] for r in rows)
    return {
        "course": {"id": course.id, "name": course.name, "threshold": course.attendance_threshold,
                   "dictated": len(data.dictated(now)), "total": course.total_sessions},
        "now": now.isoformat(),
        "counters": [{"state": s, "label": STATE_LABELS[s], "count": counts.get(s, 0)}
                     for s in (ACTIVO, EN_ADAPTACION, EN_RIESGO, INACTIVO)],
        "level2_open": sum(1 for a in alerts.values() if a.level == 2),
        "students": rows,
    }


@router.get("/courses/{course_id}/stats")
def stats(course_id: int, db: Session = Depends(get_db)) -> dict:
    course = get_course(db, course_id)
    now = config.now()
    data, statuses = compute_course(db, course.id, now)
    weeks: dict[int, dict] = defaultdict(lambda: {"live": 0, "recovered": 0, "quiz_live": [], "quiz_recovered": [],
                                                  "chat": 0, "voice": 0, "sessions": 0})
    session_week = {s.id: (s.number + 1) // 2 for s in data.sessions}
    dictated = {s.id for s in data.dictated(now)}
    for sid in dictated:
        weeks[session_week[sid]]["sessions"] += 1
    for st in statuses.values():
        for c in st.compliances:
            w = weeks[session_week[c.session_id]]
            if c.status == EN_VIVO:
                w["live"] += 1
                if c.best_quiz is not None:
                    w["quiz_live"].append(c.best_quiz)
            elif c.status == RECUPERADA:
                w["recovered"] += 1
                if c.best_quiz is not None:
                    w["quiz_recovered"].append(c.best_quiz)
        for sid, msgs in st.student.zoom_chat.items():
            if sid in dictated:
                weeks[session_week[sid]]["chat"] += len(msgs)
        for sid, turns in st.student.voice.items():
            if sid in dictated:
                weeks[session_week[sid]]["voice"] += turns
    out = []
    for week in sorted(k for k, v in weeks.items() if v["sessions"]):
        w = weeks[week]
        out.append({"week": week, "live": w["live"], "recovered": w["recovered"],
                    "quiz_live": round(mean(w["quiz_live"]), 2) if w["quiz_live"] else None,
                    "quiz_recovered": round(mean(w["quiz_recovered"]), 2) if w["quiz_recovered"] else None,
                    "participation": w["chat"] + w["voice"], "chat": w["chat"], "voice": w["voice"]})
    return {"weeks": out}


@router.get("/students/{user_id}/timeline")
def timeline(user_id: int, course_id: int, db: Session = Depends(get_db)) -> dict:
    course = get_course(db, course_id)
    now = config.now()
    data, statuses = compute_course(db, course.id, now, user_ids=[user_id])
    st = statuses.get(user_id)
    if st is None:
        raise HTTPException(404, "Alumno no matriculado en el curso")
    titles = {s.id: s.title for s in data.sessions}
    feats = {f.session_id: f for f in st.sessions}
    sessions = []
    for c in st.compliances:
        f = feats.get(c.session_id)
        sessions.append({**compliance_json(c, now), "title": titles.get(c.session_id, ""),
                         "starts_at": iso(c.starts_at), "attended": c.live.attended,
                         "late_min": f.late_min if f else None, "early_leave_min": f.early_leave_min if f else None,
                         "reconnects": f.reconnects if f else 0, "chat_msgs": f.chat_msgs if f else 0,
                         "voice_turns": f.voice_turns if f else 0})
    snapshots = db.exec(select(RiskSnapshot).where(RiskSnapshot.user_id == user_id, RiskSnapshot.course_id == course.id,
                                                   RiskSnapshot.at <= now).order_by(RiskSnapshot.at)).all()
    changes, prev = [], None
    for snap in snapshots:
        if snap.state != prev:
            changes.append({"at": iso(snap.at), "state": snap.state, "state_label": STATE_LABELS[snap.state],
                            "reasons": json.loads(snap.reasons_json)})
            prev = snap.state
    alerts = db.exec(select(Alert).where(Alert.user_id == user_id, Alert.course_id == course.id,
                                         Alert.created_at <= now).order_by(Alert.created_at.desc())).all()
    messages = db.exec(select(Outbox).where(Outbox.user_id == user_id, Outbox.course_id == course.id,
                                            Outbox.channel == "portal", Outbox.at <= now)
                       .order_by(Outbox.at.desc())).all()
    micro = db.exec(select(MicroAnswer).where(MicroAnswer.user_id == user_id, MicroAnswer.course_id == course.id,
                                              MicroAnswer.at <= now).order_by(MicroAnswer.at.desc())).all()
    withdrawals = db.exec(select(WithdrawalRequest).where(WithdrawalRequest.user_id == user_id,
                                                          WithdrawalRequest.course_id == course.id)).all()
    cur = st.current
    return {
        "student": _student_row(st, alerts[0] if alerts and alerts[0].status == "abierta" else None),
        "features": {
            "active_min_week": round(cur.active_min_week, 1), "study_hour_mode": cur.study_hour_mode,
            "previous_study_hour_mode": cur.previous_study_hour_mode,
            "questions_content": cur.questions_content, "questions_logistics": cur.questions_logistics,
            "quiz_trend": round(cur.quiz_trend, 3) if cur.quiz_trend is not None else None,
            "pattern_drop": cur.pattern_drop, "late_shift": cur.late_shift, "mode_shift": cur.mode_shift,
        },
        "sessions": sessions,
        "state_changes": changes,
        "alerts": [{"id": a.id, "level": a.level, "status": a.status, "created_at": iso(a.created_at),
                    "escalated_at": iso(a.escalated_at), "closed_at": iso(a.closed_at), "outcome": a.outcome,
                    "reasons": json.loads(a.reasons_json), "contacts": json.loads(a.contact_json)} for a in alerts],
        "messages": [{"at": iso(m.at), "text": m.text, "read": m.read} for m in messages],
        "micro": [{"at": iso(m.at), "question": m.question, "answer": m.answer, "topic": m.topic,
                   "sentiment": m.sentiment, "summary": m.summary} for m in micro],
        "withdrawals": [{"at": iso(w.at), "reason": w.reason, "comment": w.comment, "choice": w.outcome}
                        for w in withdrawals],
    }


@router.get("/alerts")
def alerts_queue(course_id: int, level: int = 2, db: Session = Depends(get_db)) -> list[dict]:
    course = get_course(db, course_id)
    now = config.now()
    rows = db.exec(select(Alert).where(Alert.course_id == course.id, Alert.level == level, Alert.status == "abierta",
                                       Alert.created_at <= now).order_by(Alert.escalated_at, Alert.created_at)).all()
    if not rows:
        return []
    _, statuses = compute_course(db, course.id, now, user_ids=[a.user_id for a in rows])
    out = []
    for a in rows:
        st = statuses.get(a.user_id)
        user = db.get(User, a.user_id)
        out.append({
            "id": a.id, "level": a.level, "created_at": iso(a.created_at), "escalated_at": iso(a.escalated_at),
            "hours_open": round((now - (a.escalated_at or a.created_at)).total_seconds() / 3600, 1),
            "assigned_role": a.assigned_role, "reasons": json.loads(a.reasons_json),
            "contacts": json.loads(a.contact_json),
            "student": {"user_id": a.user_id, "name": user.name if user else "", "email": user.email if user else "",
                        "state": st.risk.state if st else None,
                        "state_label": STATE_LABELS.get(st.risk.state) if st else None,
                        **(indicators_json(st.indicators) if st else {})},
        })
    return out


class ContactIn(BaseModel):
    contacted_at: datetime | None = None
    channel: Literal["llamada", "whatsapp", "email", "reunion"]
    responded: bool
    reason: str = Field(default="", max_length=200)
    action: str = Field(default="", max_length=500)
    result: str = Field(default="", max_length=500)
    close: bool = False


@router.post("/alerts/{alert_id}/contact")
def register_contact(alert_id: int, body: ContactIn, user: User = Depends(require_tutor),
                     db: Session = Depends(get_db)) -> dict:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(404, "Alerta no encontrada")
    now = config.now()
    contacts = json.loads(alert.contact_json)
    contacts.append({"at": iso(body.contacted_at or now), "by": user.name, "channel": body.channel,
                     "responded": body.responded, "reason": body.reason, "action": body.action,
                     "result": body.result})
    alert.contact_json = json.dumps(contacts, ensure_ascii=False)
    if body.close:
        alert.status, alert.closed_at = "cerrada", now
        alert.outcome = body.result or body.action or "Contacto registrado"
    db.add(alert)
    db.commit()
    return {"id": alert.id, "status": alert.status, "contacts": contacts}


@router.get("/sessions")
def sessions(course_id: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.exec(select(ClassSession).where(ClassSession.course_id == course_id).order_by(ClassSession.number)).all()
    chapters = defaultdict(list)
    for ch in db.exec(select(Chapter).where(Chapter.session_id.in_([s.id for s in rows]))).all():
        chapters[ch.session_id].append(ch)
    now = config.now()
    out = []
    for s in rows:
        chs = chapters.get(s.id, [])
        duration = (s.ends_at - s.starts_at).total_seconds()
        keys = [c for c in chs if c.is_key]
        out.append({"id": s.id, "number": s.number, "title": s.title, "starts_at": iso(s.starts_at),
                    "dictated": s.ends_at <= now, "processed": s.processed, "chapters": len(chs),
                    "key_ratio": round(sum(c.end - c.start for c in keys) / duration, 3) if duration else 0,
                    "validated": bool(keys) and all(c.validated for c in keys)})
    return out


def _chapters_payload(db: Session, s: ClassSession) -> dict:
    chs = db.exec(select(Chapter).where(Chapter.session_id == s.id).order_by(Chapter.start)).all()
    duration = (s.ends_at - s.starts_at).total_seconds()
    ratio = sum(c.end - c.start for c in chs if c.is_key) / duration if duration else 0
    warning = None
    if ratio < 0.30 or ratio > 0.50:
        warning = f"Los tramos clave suman {round(ratio * 100)} % de la clase; se recomienda entre 30 % y 50 %."
    return {"session": {"id": s.id, "number": s.number, "title": s.title, "duration_s": duration},
            "key_ratio": round(ratio, 3), "warning": warning,
            "chapters": [{"id": c.id, "start": c.start, "end": c.end, "title": c.title, "summary": c.summary,
                          "is_key": c.is_key, "validated": c.validated} for c in chs]}


@router.get("/sessions/{session_id}/chapters")
def get_chapters(session_id: int, db: Session = Depends(get_db)) -> dict:
    return _chapters_payload(db, get_session_or_404(db, session_id))


class ChapterPatch(BaseModel):
    id: int
    is_key: bool
    title: str | None = Field(default=None, max_length=200)


class ChaptersPatch(BaseModel):
    chapters: list[ChapterPatch]
    validate_all: bool = True


@router.patch("/sessions/{session_id}/chapters")
def patch_chapters(session_id: int, body: ChaptersPatch, db: Session = Depends(get_db)) -> dict:
    s = get_session_or_404(db, session_id)
    rows = {c.id: c for c in db.exec(select(Chapter).where(Chapter.session_id == s.id)).all()}
    for patch in body.chapters:
        ch = rows.get(patch.id)
        if ch is None:
            raise HTTPException(400, f"El capítulo {patch.id} no pertenece a la sesión")
        ch.is_key = patch.is_key
        if patch.title:
            ch.title = patch.title.strip()
    if body.validate_all:
        for ch in rows.values():
            ch.validated = True
    for ch in rows.values():
        db.add(ch)
    db.commit()
    recompute_course(db, s.course_id)
    return _chapters_payload(db, s)


@router.get("/labels")
def labels() -> dict:
    return {"states": STATE_LABELS, "compliance": STATUS_LABELS}
