"""Chat tutor con LLM simulado: escalera de ayuda, evaluación del intento, protección del quiz y fuera de la clase."""
import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import select

from app import config
from app.main import app
from app.models import (Chapter, ChatThread, ClassSession, Course, Enrollment, MaterialUnit, QuizAttempt, QuizItem,
                        TranscriptSegment, User)
from app.services import llm, tutor_chat
from helpers import START

LINES = [
    (0, "Buenas noches, hoy vemos webhooks y credenciales en n8n. Primero la agenda de la sesión."),
    (60, "Revisamos la asistencia y los acuerdos del curso antes de comenzar."),
    (300, "Un webhook es un nodo que recibe datos por HTTP cuando otra aplicación le envía una petición."),
    (330, "El webhook tiene dos direcciones: la URL de test escucha solo mientras el editor está abierto."),
    (360, "La URL de producción funciona únicamente cuando el flujo está activo; si lo desactivas, deja de responder."),
    (390, "El listener espera la petición POST que manda el formulario y la convierte en items."),
    (900, "Ahora las credenciales: se guardan cifradas con la clave de la instancia de n8n."),
    (930, "Las credenciales son reutilizables: las creas una vez y varios nodos las usan sin ver el secreto."),
    (960, "Si cambias el token en la credencial, cambia en todos los flujos que la usan."),
    (1500, "Para terminar, el nodo If manda cada item por la rama true o la rama false según la condición."),
    (1530, "Con Switch eliges entre varias ramas y con Merge unes los datos de dos ramas en una sola."),
]


def seed(db, passed: bool = False):
    student = User(name="Alumna", email="alumna@correo.pe", role="alumno")
    tutor = User(name="Tutora", email="tutora@dmc.pe", role="tutor")
    db.add_all([student, tutor])
    db.flush()
    course = Course(name="Curso", start_date=START.date(), end_date=START.date(), total_sessions=1, schedule="",
                    **config.COURSE_DEFAULTS)
    db.add(course)
    db.flush()
    db.add(Enrollment(user_id=student.id, course_id=course.id))
    s = ClassSession(course_id=course.id, number=1, title="Webhooks", starts_at=START,
                     ends_at=START + timedelta(minutes=30), processed=True)
    db.add(s)
    db.flush()
    slides = [(1, "Agenda", "Agenda\nAcuerdos de la sesión"),
              (2, "Webhooks: dos URL", "Webhooks: dos URL\nURL de test: solo con el editor abierto\n"
                                       "URL de producción: solo con el flujo activo"),
              (3, "Credenciales seguras", "Credenciales seguras\nSe guardan cifradas con la clave de la instancia\n"
                                          "Reutilizables entre nodos y flujos"),
              (4, "Nodos de lógica", "Nodos de lógica\nIf, Switch y Merge deciden por dónde sigue el flujo")]
    for no, title, text in slides:
        db.add(MaterialUnit(session_id=s.id, source_file="clase.pptx", kind="slide", unit_no=no, title=title, text=text))
    db.add(Chapter(session_id=s.id, start=0, end=300, title="Agenda", is_key=False, logistics=True, slide_from=1, slide_to=1))
    db.add(Chapter(session_id=s.id, start=300, end=900, title="Webhooks: dos URL", is_key=True, slide_from=2, slide_to=2))
    db.add(Chapter(session_id=s.id, start=900, end=1500, title="Credenciales seguras", is_key=True, slide_from=3, slide_to=3))
    db.add(Chapter(session_id=s.id, start=1500, end=1800, title="Nodos de lógica", is_key=True, slide_from=4, slide_to=4))
    for start, text in LINES:
        db.add(TranscriptSegment(session_id=s.id, start=start, end=start + 25, speaker="", text=text))
    db.add(QuizItem(session_id=s.id, question="¿Qué URL del webhook funciona solo cuando el flujo está activo?",
                    options_json=json.dumps(["La URL de producción", "La URL de test", "La del editor", "Ninguna"]),
                    answer_idx=0, explanation="[06:00]", minute="06:00", slide=2, status="aprobada"))
    if passed:
        db.add(QuizAttempt(user_id=student.id, session_id=s.id, score=5, at=START + timedelta(days=1)))
    db.commit()
    return student.id, tutor.id, s.id


def post(client, sid, uid, **body):
    res = client.post(f"/api/sessions/{sid}/chat", headers={"X-User-Id": str(uid)}, json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_first_turn_locates_cites_and_asks(db):
    uid, _, sid = seed(db)
    config.set_now(START + timedelta(days=1))
    before = llm.usage()["tutor"]
    with TestClient(app) as client:
        res = post(client, sid, uid, message="¿Cómo se guardan las credenciales?")
    assert res["route"] == "contenido" and res["level"] == 1 and res["asks_student"] and not res["closed"]
    assert res["answer"].rstrip().endswith("?") and len(res["answer"].split()) <= config.CHAT_MAX_WORDS
    assert any(900 <= c["sec"] <= 990 for c in res["citations"])
    assert res["slides"][0]["n"] == 3
    assert [b["action"] for b in res["buttons"]] == ["hint", "close"]
    assert llm.usage()["tutor"] == before + 1


def test_help_ladder_hint_step_explain_close(db):
    uid, tid, sid = seed(db)
    config.set_now(START + timedelta(days=1))
    with TestClient(app) as client:
        first = post(client, sid, uid, message="¿Cómo se guardan las credenciales?")
        thread = first["thread_id"]
        hint = post(client, sid, uid, thread_id=thread, action="hint")
        assert hint["level"] == 2 and hint["answer"].startswith("Pista") and hint["buttons"][0]["label"] == "Otra pista"
        step = post(client, sid, uid, thread_id=thread, action="hint")
        assert step["level"] == 3 and [b["action"] for b in step["buttons"]] == ["explain", "close"]
        explain = post(client, sid, uid, thread_id=thread, action="explain")
        assert explain["level"] == 4 and "cifradas" in explain["answer"] and explain["answer"].rstrip().endswith("?")
        done = post(client, sid, uid, thread_id=thread, action="close")
        assert done["closed"] and not done["buttons"] and "Nodos de lógica" in done["answer"]
        topics = client.get(f"/api/tutor/sessions/{sid}/hard-topics", headers={"X-User-Id": str(tid)}).json()["topics"]
        assert topics[0]["title"] == "Credenciales seguras" and topics[0]["hard"] == 1
        assert client.get(f"/api/tutor/sessions/{sid}/hard-topics", headers={"X-User-Id": str(uid)}).status_code == 403


def test_attempt_evaluation(db):
    uid, _, sid = seed(db)
    config.set_now(START + timedelta(days=1))
    with TestClient(app) as client:
        thread = post(client, sid, uid, message="¿Cómo se guardan las credenciales?")["thread_id"]
        wrong = post(client, sid, uid, thread_id=thread, action="answer", message="No sé, en un Excel")
        assert wrong["evaluation"] == "incorrecta" and wrong["level"] == 2 and not wrong["closed"]
        good = post(client, sid, uid, thread_id=thread, action="answer",
                    message="Quedan cifradas con la clave de la instancia y son reutilizables")
        assert good["evaluation"] == "correcta" and good["closed"] and not good["asks_student"]
    row = db.get(ChatThread, thread)
    assert row.attempts == 2 and row.evaluation == "correcta" and row.closed


def test_quiz_question_is_capped_at_hints_until_passed(db):
    uid, _, sid = seed(db)
    config.set_now(START + timedelta(days=1))
    item = db.exec(select(QuizItem)).first()
    with TestClient(app) as client:
        res = post(client, sid, uid, message="¿Qué URL del webhook funciona solo cuando el flujo está activo?")
        replies = [res["answer"]]
        for action in ("hint", "hint", "explain"):
            res = post(client, sid, uid, thread_id=res["thread_id"], action=action)
            replies.append(res["answer"])
            assert res["level"] <= config.CHAT_QUIZ_MAX_LEVEL
        assert not any(tutor_chat.leaks_answer(r, item) for r in replies)
        assert "se parece a una del quiz" in replies[-1]
    assert db.get(ChatThread, res["thread_id"]).max_level == config.CHAT_QUIZ_MAX_LEVEL


def test_quiz_question_is_open_after_passing(db):
    uid, _, sid = seed(db, passed=True)
    config.set_now(START + timedelta(days=1))
    with TestClient(app) as client:
        res = post(client, sid, uid, message="¿Qué URL del webhook funciona solo cuando el flujo está activo?")
    assert db.get(ChatThread, res["thread_id"]).max_level == 4


def test_out_of_class_fixed_reply_without_llm(db):
    uid, _, sid = seed(db)
    config.set_now(START + timedelta(days=1))
    before = llm.usage()["tutor"]
    with TestClient(app) as client:
        res = post(client, sid, uid, message="¿Cómo instalo Kubernetes en mi laptop?")
    assert res["answer"] == config.CHAT_NOT_FOUND and res["thread_id"] is None and not res["buttons"]
    assert llm.usage()["tutor"] == before


def test_thread_of_other_user_is_rejected(db):
    uid, tid, sid = seed(db)
    config.set_now(START + timedelta(days=1))
    with TestClient(app) as client:
        thread = post(client, sid, uid, message="¿Cómo se guardan las credenciales?")["thread_id"]
        res = client.post(f"/api/sessions/{sid}/chat", headers={"X-User-Id": str(tid)},
                          json={"thread_id": thread, "action": "hint"})
    assert res.status_code == 400


def test_cap_words_keeps_final_question():
    text = " ".join(["Frase larga de relleno."] * 30) + " ¿Qué opinas?"
    out = tutor_chat.cap_words(text)
    assert len(out.split()) <= config.CHAT_MAX_WORDS and out.endswith("¿Qué opinas?")
