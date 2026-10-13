"""Rescate en 2 niveles con reloj simulado: nivel 1 automático, escalamiento a las 48 h sin actividad,
cierre automático, baja asistida y registro del contacto por el Asistente Académico."""
import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import config
from app.main import app
from app.models import (Alert, ClassSession, Course, Enrollment, Outbox, QuizAttempt, User, WatchRange,
                        ZoomInterval)
from app.services.recompute import recompute_course
from helpers import START

N = 16
M = timedelta(minutes=1)
SESSION_STARTS = [START + timedelta(days=(i // 2) * 7 + (i % 2) * 2) for i in range(N)]


def seed(db: Session, late_from: int = 9) -> dict:
    """Alumno puntual que desde la sesión `late_from` llega tarde y sale antes sin recuperar (perfil cambio_horario)."""
    aa = User(name="Michela López", email="michela@dmc.pe", role="asistente_academico")
    student = User(name="Alumno Prueba", email="alumno@correo.pe", role="alumno")
    db.add(aa)
    db.add(student)
    db.flush()
    course = Course(name="Curso", start_date=START.date(), end_date=SESSION_STARTS[-1].date(), total_sessions=N,
                    schedule="mar/jue", academic_assistant_id=aa.id, **config.COURSE_DEFAULTS)
    db.add(course)
    db.flush()
    db.add(Enrollment(user_id=student.id, course_id=course.id))
    sessions = []
    for i, start in enumerate(SESSION_STARTS):
        s = ClassSession(course_id=course.id, number=i + 1, starts_at=start, ends_at=start + timedelta(hours=3))
        db.add(s)
        sessions.append(s)
    db.flush()
    for s in sessions[:12]:
        late, early = (0, 0) if s.number < late_from else (45, 40)
        db.add(ZoomInterval(session_id=s.id, user_id=student.id, join_at=s.starts_at + late * M,
                            leave_at=s.ends_at - early * M))
    db.commit()
    alert_at = sessions[11].ends_at + timedelta(days=5)
    return {"course": course, "student": student, "aa": aa, "sessions": sessions, "t0": alert_at}


def open_alert(db: Session, user_id: int) -> Alert | None:
    db.expire_all()
    return db.exec(select(Alert).where(Alert.user_id == user_id, Alert.status == "abierta")).first()


def test_level1_alert_creates_outbox_messages(db):
    d = seed(db)
    statuses = recompute_course(db, d["course"].id, d["t0"])
    assert statuses[d["student"].id].risk.state == "en_riesgo"
    alert = open_alert(db, d["student"].id)
    assert alert.level == 1 and alert.escalated_at is None
    messages = db.exec(select(Outbox).where(Outbox.alert_id == alert.id)).all()
    assert {m.channel for m in messages} == {"portal", "email", "whatsapp"}
    assert "por recuperar" in messages[0].text and "20 minutos" in messages[0].text


def test_escalates_to_level2_after_48h_without_activity(db):
    d = seed(db)
    course_id, uid, t0 = d["course"].id, d["student"].id, d["t0"]
    recompute_course(db, course_id, t0)
    recompute_course(db, course_id, t0 + timedelta(hours=47))
    assert open_alert(db, uid).level == 1
    recompute_course(db, course_id, t0 + timedelta(hours=49))
    alert = open_alert(db, uid)
    assert alert.level == 2
    assert alert.assigned_role == "asistente_academico"
    assert alert.escalated_at == t0 + timedelta(hours=49)


def test_new_activity_prevents_escalation(db):
    d = seed(db)
    course_id, uid, t0 = d["course"].id, d["student"].id, d["t0"]
    recompute_course(db, course_id, t0)
    db.add(WatchRange(user_id=uid, session_id=d["sessions"][11].id, start_s=0, end_s=600, at=t0 + timedelta(hours=10)))
    db.commit()
    recompute_course(db, course_id, t0 + timedelta(hours=49))
    assert open_alert(db, uid).level == 1


def test_alert_closes_when_student_recovers(db):
    d = seed(db, late_from=11)
    course_id, uid = d["course"].id, d["student"].id
    t0 = d["sessions"][11].ends_at + timedelta(days=4)  # 11 y 12 llevan ≥ 4 días sin recuperar, aún en plazo
    assert recompute_course(db, course_id, t0)[uid].risk.state == "en_riesgo"
    alert_id = open_alert(db, uid).id
    pending_ids = [s.id for s in d["sessions"][10:12]]
    db.commit()
    config.set_now(t0 + timedelta(hours=2))
    with TestClient(app) as client:
        headers = {"X-User-Id": str(uid)}
        for sid in pending_ids:
            res = client.post("/api/watch", json={"session_id": sid, "ranges": [[0, 10800]]}, headers=headers)
            assert res.json()["key_coverage"] == 1.0
    for sid in pending_ids:  # sesiones sin procesar (sin banco de quiz): el intento se registra directamente
        db.add(QuizAttempt(user_id=uid, session_id=sid, score=4, at=t0 + timedelta(hours=2)))
    db.commit()
    statuses = recompute_course(db, course_id, t0 + timedelta(hours=3))
    assert statuses[uid].risk.state in ("activo", "en_adaptacion")
    db.expire_all()
    closed = db.get(Alert, alert_id)
    assert closed.status == "cerrada" and closed.outcome.startswith("auto")


def test_withdrawal_request_creates_immediate_level2(db):
    d = seed(db)
    config.set_now(d["t0"])
    with TestClient(app) as client:
        res = client.post(f"/api/courses/{d['course'].id}/withdrawal",
                          json={"reason": "trabajo", "comment": "Nuevo horario", "choice": "hablar_asistente"},
                          headers={"X-User-Id": str(d["student"].id)})
    assert res.status_code == 200
    alert = open_alert(db, d["student"].id)
    assert alert.level == 2 and alert.assigned_role == "asistente_academico"
    assert any("retiro" in r for r in json.loads(alert.reasons_json))


def test_tutor_registers_contact_and_closes_alert(db):
    d = seed(db)
    course_id, uid, t0 = d["course"].id, d["student"].id, d["t0"]
    recompute_course(db, course_id, t0)
    recompute_course(db, course_id, t0 + timedelta(hours=49))
    alert = open_alert(db, uid)
    config.set_now(t0 + timedelta(hours=50))
    with TestClient(app) as client:
        staff = {"X-User-Id": str(d["aa"].id)}
        queue = client.get(f"/api/tutor/alerts?course_id={course_id}&level=2", headers=staff).json()
        assert [a["id"] for a in queue] == [alert.id]
        assert client.get(f"/api/tutor/alerts?course_id={course_id}", headers={"X-User-Id": str(uid)}).status_code == 403
        res = client.post(f"/api/tutor/alerts/{alert.id}/contact", headers=staff, json={
            "contacted_at": (t0 + timedelta(hours=50)).isoformat(), "channel": "llamada", "responded": True,
            "reason": "trabajo", "action": "recuperar", "result": "Recuperará el sábado", "close": True,
        })
        assert res.status_code == 200
        assert client.get(f"/api/tutor/alerts?course_id={course_id}&level=2", headers=staff).json() == []
    db.expire_all()
    saved = db.get(Alert, alert.id)
    contact = json.loads(saved.contact_json)[0]
    assert saved.status == "cerrada" and saved.outcome == "Recuperará el sábado"
    assert contact["channel"] == "llamada" and contact["responded"] and contact["reason"] == "trabajo"


def test_admin_recompute_endpoint(db):
    d = seed(db)
    with TestClient(app) as client:
        res = client.post("/api/admin/recompute", headers={"X-User-Id": str(d["aa"].id)},
                          json={"course_id": d["course"].id, "now": d["t0"].isoformat()})
        assert res.status_code == 200
        assert res.json()["courses"][str(d["course"].id)] == {"en_riesgo": 1}
        assert client.post("/api/admin/recompute", headers={"X-User-Id": str(d["student"].id)}).status_code == 403
    assert open_alert(db, d["student"].id).level == 1
