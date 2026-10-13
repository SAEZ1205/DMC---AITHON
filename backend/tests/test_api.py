"""Vista de clase por API: «Lo que te perdiste», quiz (5 preguntas sin respuestas) y recuperación de la sesión."""
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.models import Chapter, ClassSession, Course, Enrollment, QuizItem, User, ZoomInterval
from helpers import START

CHAPTERS = [(0, 600, False), (600, 1800, True), (1800, 3600, True), (3600, 7200, False), (7200, 10800, True)]


def seed(db):
    student = User(name="Alumna", email="alumna@correo.pe", role="alumno")
    db.add(student)
    db.flush()
    course = Course(name="Curso", start_date=START.date(), end_date=START.date(), total_sessions=4, schedule="",
                    **config.COURSE_DEFAULTS)
    db.add(course)
    db.flush()
    db.add(Enrollment(user_id=student.id, course_id=course.id))
    s = ClassSession(course_id=course.id, number=1, title="Webhooks", starts_at=START,
                     ends_at=START + timedelta(hours=3), processed=True)
    db.add(s)
    db.flush()
    chapters = [Chapter(session_id=s.id, start=a, end=b, title=f"Capítulo {i + 1}", is_key=k)
                for i, (a, b, k) in enumerate(CHAPTERS)]
    db.add_all(chapters)
    for i in range(8):
        db.add(QuizItem(session_id=s.id, question=f"Pregunta {i}", options_json=json.dumps(["a", "b", "c", "d"]),
                        answer_idx=i % 4, explanation=f"Explicación [0{i}:00]"))
    # Conectada de 10:00 a 60:00, con un corte de 1 min (conexión) a los 30:00.
    db.add(ZoomInterval(session_id=s.id, user_id=student.id, join_at=START + timedelta(minutes=10),
                        leave_at=START + timedelta(minutes=30)))
    db.add(ZoomInterval(session_id=s.id, user_id=student.id, join_at=START + timedelta(minutes=31),
                        leave_at=START + timedelta(minutes=60)))
    db.commit()
    return student.id, s.id, [c.id for c in chapters]


def test_missed_lists_exactly_overlapping_chapters(db):
    uid, sid, ch = seed(db)
    config.set_now(START + timedelta(days=1))
    with TestClient(app) as client:
        d = client.get(f"/api/sessions/{sid}", headers={"X-User-Id": str(uid)}).json()
    assert d["missed"]["absent_ranges"] == [[0, 600], [3600, 10800]]
    assert d["missed"]["chapter_ids"] == [ch[0], ch[3], ch[4]]
    assert d["compliance"]["status"] == "por_recuperar"
    assert d["banner"]["tone"] == "warn" and "Vence el martes 13 de octubre" in d["banner"]["text"]


def test_quiz_hides_answers_and_recovery_flow(db):
    uid, sid, _ = seed(db)
    config.set_now(START + timedelta(days=2))
    headers = {"X-User-Id": str(uid)}
    with TestClient(app) as client:
        quiz = client.get(f"/api/sessions/{sid}/quiz", headers=headers).json()
        assert len(quiz["items"]) == 5
        assert all("answer_idx" not in item for item in quiz["items"])
        assert all(sorted(o["idx"] for o in item["options"]) == [0, 1, 2, 3] for item in quiz["items"])

        wrong = {item["id"]: 99 for item in quiz["items"]}
        res = client.post(f"/api/sessions/{sid}/quiz", headers=headers, json={"answers": wrong}).json()
        assert res["score"] == 0 and not res["passed"]

        keys = [[600, 3600], [7200, 10800]]
        assert client.post("/api/watch", headers=headers, json={"session_id": sid, "ranges": keys}).json()["key_coverage"] == 1.0
        right = {}
        for item in quiz["items"]:
            n = int(item["question"].split()[-1])
            right[item["id"]] = n % 4
        res = client.post(f"/api/sessions/{sid}/quiz", headers=headers, json={"answers": right}).json()
        assert res["score"] == 5 and res["passed"]
        assert res["compliance"]["status"] == "recuperada"
        assert res["banner"]["tone"] == "ok"
        assert client.get(f"/api/me/micro-question?course_id=1", headers=headers).json()["question"]
