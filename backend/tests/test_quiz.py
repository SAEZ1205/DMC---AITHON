"""Quiz del docente: puerta de 5 aprobadas, la IA solo crea borradores, aprobación una por una y contador."""
import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import select

from app import config
from app.main import app
from app.models import Chapter, ClassSession, Course, Enrollment, MaterialUnit, QuizItem, TranscriptSegment, User
from app.services import llm
from app.services.quiz import review_rules
from helpers import START


def seed(db, approved: int = 4, drafts: int = 2):
    tutor = User(name="Lincoln Villanueva", email="docente@dmc.pe", role="docente")
    student = User(name="Alumna", email="alumna@correo.pe", role="alumno")
    db.add_all([tutor, student])
    db.flush()
    course = Course(name="Curso", start_date=START.date(), end_date=START.date(), total_sessions=1, schedule="",
                    **config.COURSE_DEFAULTS)
    db.add(course)
    db.flush()
    db.add(Enrollment(user_id=student.id, course_id=course.id))
    s = ClassSession(course_id=course.id, number=1, title="n8n", starts_at=START, ends_at=START + timedelta(hours=1),
                     processed=True)
    db.add(s)
    db.flush()
    topics = [("Webhooks", "URL de test y URL de producción del webhook"), ("Credenciales", "Las credenciales se guardan cifradas"),
              ("Reintentos", "Retry On Fail reintenta ante fallas temporales"), ("Expresiones", "Las expresiones van entre dobles llaves")]
    for i, (title, bullet) in enumerate(topics):
        db.add(MaterialUnit(session_id=s.id, source_file="clase.pptx", kind="slide", unit_no=i + 1, title=title,
                            text=f"{title}\n{bullet}"))
        db.add(Chapter(session_id=s.id, start=i * 900, end=(i + 1) * 900, title=title, is_key=i in (0, 2),
                       slide_from=i + 1, slide_to=i + 1))
        db.add(TranscriptSegment(session_id=s.id, start=i * 900 + 60, end=i * 900 + 80, speaker="",
                                 text=f"Ahora vemos {title.lower()} con un ejemplo práctico en el flujo de n8n."))
    for i in range(approved + drafts):
        db.add(QuizItem(session_id=s.id, question=f"Pregunta {i}", options_json=json.dumps(["a", "b", "c", "d"]),
                        answer_idx=0, explanation="", minute="20:00", status="aprobada" if i < approved else "borrador"))
    db.commit()
    return {"X-User-Id": str(tutor.id)}, {"X-User-Id": str(student.id)}, s.id, tutor.id


def test_quiz_inactive_until_five_approved(db):
    teacher, student, sid, tid = seed(db, approved=4)
    with TestClient(app) as client:
        quiz = client.get(f"/api/sessions/{sid}/quiz", headers=student).json()
        assert quiz["ready"] is False and quiz["items"] == [] and quiz["approved"] == 4
        assert "Quiz en preparación" in quiz["message"]
        any_id = db.exec(select(QuizItem.id).where(QuizItem.session_id == sid)).first()
        assert client.post(f"/api/sessions/{sid}/quiz", headers=student, json={"answers": {any_id: 0}}).status_code == 409

        bank = client.get(f"/api/tutor/sessions/{sid}/quiz", headers=teacher).json()
        assert bank["counts"]["active"] is False and "falta 1 pregunta aprobada" in bank["counts"]["warning"]
        draft = next(i for i in bank["items"] if i["status"] == "borrador")
        res = client.patch(f"/api/tutor/quiz/{draft['id']}", headers=teacher, json={"action": "aprobar"}).json()
        approved = next(i for i in res["items"] if i["id"] == draft["id"])
        assert approved["status"] == "aprobada" and approved["approved_by"] == "Lincoln Villanueva" and approved["approved_at"]
        assert res["counts"]["active"] is True and res["counts"]["warning"] is None

        quiz = client.get(f"/api/sessions/{sid}/quiz", headers=student).json()
        assert quiz["ready"] is True and len(quiz["items"]) == 5
        assert all(i["id"] != next(x["id"] for x in res["items"] if x["status"] == "borrador") for i in quiz["items"])


def test_assistant_only_creates_drafts(db):
    teacher, student, sid, _ = seed(db, approved=5, drafts=0)
    before = llm.usage()["quiz_assist"]
    with TestClient(app) as client:
        res = client.post(f"/api/tutor/sessions/{sid}/quiz/assist", headers=teacher, json={"action": "proponer"}).json()
        assert res["created"] and "una por una" in res["message"]
        created = [i for i in res["items"] if i["id"] in res["created"]]
        assert all(i["status"] == "borrador" and i["source"] == "propuesta_ia" for i in created)
        assert all(i["minute"] and i["slide"] for i in created)
        assert {i["question"] for i in created} == {"¿Cuál de estas ideas corresponde a «Webhooks»?",
                                                     "¿Cuál de estas ideas corresponde a «Reintentos»?"}
        assert res["counts"]["approved"] == 5 and res["counts"]["drafts"] == 2

        quiz_ids = set()
        for _ in range(10):
            quiz_ids |= {i["id"] for i in client.get(f"/api/sessions/{sid}/quiz", headers=student).json()["items"]}
        assert not quiz_ids & set(res["created"])

        again = client.post(f"/api/tutor/sessions/{sid}/quiz/assist", headers=teacher, json={"action": "proponer"}).json()
        assert again["created"] == [] and "ya tienen preguntas" in again["message"]

        target = created[0]["id"]
        alts = client.post(f"/api/tutor/sessions/{sid}/quiz/assist", headers=teacher,
                           json={"action": "alternativas", "item_id": target}).json()["distractors"]
        assert len(alts) == 3
        assert client.post(f"/api/tutor/sessions/{sid}/quiz/assist", headers=teacher,
                           json={"action": "revisar", "item_id": target}).json()["review"]["issues"]
        assert client.patch("/api/tutor/quiz/999", headers=teacher, json={"action": "aprobar"}).status_code == 404
    assert llm.usage()["quiz_assist"] == before + 4


def test_teacher_edit_then_approve_updates_counter(db):
    teacher, _, sid, _ = seed(db, approved=5, drafts=0)
    with TestClient(app) as client:
        res = client.post(f"/api/tutor/sessions/{sid}/quiz/assist", headers=teacher, json={"action": "proponer"}).json()
        first, second = res["created"]
        client.patch(f"/api/tutor/quiz/{first}", headers=teacher,
                     json={"question": "¿Qué URL del webhook se usa mientras pruebas el flujo?", "action": "aprobar"})
        res = client.patch(f"/api/tutor/quiz/{second}", headers=teacher, json={"action": "descartar"}).json()
        edited = next(i for i in res["items"] if i["id"] == first)
        assert edited["edited_by_teacher"] and edited["status"] == "aprobada"
        assert res["counts"]["text"] == "6 preguntas: 5 del docente, 1 propuesta por IA y editada"
        assert res["counts"]["discarded"] == 1

        created = client.post(f"/api/tutor/sessions/{sid}/quiz", headers=teacher, json={
            "question": "¿Dónde se guardan los tokens?", "options": ["Credenciales", "URL", "Nombre", "Notas"],
            "answer_idx": 0, "minute": "15:10", "slide": 2}).json()
        mine = next(i for i in created["items"] if i["question"] == "¿Dónde se guardan los tokens?")
        assert mine["status"] == "borrador" and mine["source"] == "docente"
        bad = {"question": "Pregunta sin alternativas", "options": ["a", " ", "c", "d"], "answer_idx": 0}
        assert client.post(f"/api/tutor/sessions/{sid}/quiz", headers=teacher, json=bad).status_code == 400
        assert client.patch(f"/api/tutor/quiz/{first}", headers={"X-User-Id": "2"}, json={"action": "aprobar"}).status_code == 403


def test_review_rules_flag_common_problems():
    item = QuizItem(session_id=1, question="¿Cuál NO es un nodo de lógica de n8n?", answer_idx=0,
                    options_json=json.dumps(["Gmail, que envía correos desde una cuenta de Google conectada", "If", "Switch", "Merge"]))
    issues = review_rules(item)["issues"]
    assert any("más larga" in i for i in issues) and any("negación" in i for i in issues)
    clean = QuizItem(session_id=1, question="¿Qué nodo divide el flujo en dos ramas?", answer_idx=0,
                     options_json=json.dumps(["If", "Gmail", "Sheets", "Slack"]))
    assert review_rules(clean)["issues"] == ["No encontré ambigüedades evidentes."]
