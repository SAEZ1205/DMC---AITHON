"""Resumen estructurado sin LLM: encabezado, viñetas de diapositivas y frases clave con minuto."""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.models import Chapter, ClassSession, Course, Enrollment, MaterialUnit, TranscriptSegment, User
from app.services.summary import sentences
from helpers import START


def test_sentences_join_fragments_with_start_time():
    segs = [TranscriptSegment(session_id=1, start=0, end=5, speaker="", text="Buenas noches, chicos. ¿Qué tal? Hoy"),
            TranscriptSegment(session_id=1, start=5, end=9, speaker="", text="vemos webhooks en n8n.")]
    assert sentences(segs) == [(0, "Buenas noches, chicos."), (0, "¿Qué tal?"), (0, "Hoy vemos webhooks en n8n.")]


def seed(db):
    student = User(name="Alumna", email="alumna@correo.pe", role="alumno")
    db.add(student)
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
    db.add(MaterialUnit(session_id=s.id, source_file="clase.pptx", kind="slide", unit_no=1, title="Agenda",
                        text="Agenda\nAcuerdos de la sesión de hoy"))
    db.add(MaterialUnit(session_id=s.id, source_file="clase.pptx", kind="slide", unit_no=2, title="Webhooks",
                        text="Webhooks\nURL de test y URL de producción\nEl listener espera la petición POST\nWebhooks"))
    db.add(Chapter(session_id=s.id, start=0, end=300, title="Agenda", is_key=False, logistics=True, slide_from=1, slide_to=1))
    db.add(Chapter(session_id=s.id, start=300, end=1800, title="Webhooks", is_key=True, slide_from=2, slide_to=2))
    lines = ["Bienvenidos, hoy revisamos la agenda y los acuerdos de la sesión de hoy."] * 3 + [
        "El webhook tiene una URL de test y una URL de producción distinta para cada flujo.",
        "Ahora miren, el listener del webhook espera la petición POST que manda el formulario.",
        "Bueno chicos, vamos a tomar agua un momento y regresamos en un ratito más.",
        "La URL de producción del webhook solo funciona cuando el flujo está activo en n8n.",
    ] * 4
    for i, text in enumerate(lines):
        start = 30 * i if i < 3 else 300 + 60 * (i - 3)
        db.add(TranscriptSegment(session_id=s.id, start=start, end=start + 20, speaker="", text=text))
    db.commit()
    return student.id, s.id


def test_summary_endpoint(db):
    uid, sid = seed(db)
    with TestClient(app) as client:
        d = client.get(f"/api/sessions/{sid}/summary", headers={"X-User-Id": str(uid)}).json()
    assert d["header"] == "Esta clase tiene 2 capítulos; los tramos clave suman 25 min."
    agenda, hooks = d["chapters"]
    assert agenda["logistics"] and not agenda["is_key"]
    assert hooks["bullets"] == ["URL de test y URL de producción", "El listener espera la petición POST"]
    assert 1 <= len(hooks["key_sentences"]) <= 2
    assert all(300 <= k["start"] < 1800 for k in hooks["key_sentences"])
    assert all("webhook" in k["text"].lower() for k in hooks["key_sentences"])
