"""Endpoints HTTP: contratos, validaciones y errores controlados."""
import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMProvider, TutorReply
from app.main import create_app

from conftest import LESSON_1, LESSON_2, post_chat


def assert_error(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    return body["error"]


# ----------------------------------------------------------- arranque y docs
def test_server_starts_and_exposes_interactive_docs(client):
    docs = client.get("/docs")
    assert docs.status_code == 200 and "text/html" in docs.headers["content-type"]
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/health", "/api/courses", "/api/courses/{course_id}/lessons", "/api/chat"} <= set(paths)


def test_importing_the_module_level_app_has_no_side_effects_on_disk():
    from app.main import app  # create_app() a nivel de módulo
    assert app.title == "DMC RescuePath AI"


# ------------------------------------------------------------------- /health
def test_health_reports_mode_storage_and_knowledge(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mode"] == "DEMO"
    assert body["llm_provider"] == "demo"
    assert body["storage"]["status"] == "ok" and body["storage"]["engine"] == "sqlite"
    assert body["storage"]["database_file"] == "rescuepath-test.db"
    assert (body["storage"]["sessions"], body["storage"]["messages"]) == (0, 0)
    assert body["knowledge"] == {"courses": 1, "lessons": 2, "load_errors": 0}


def test_health_counts_follow_activity(client):
    post_chat(client, "Explícame ORDER BY")
    storage = client.get("/health").json()["storage"]
    assert (storage["sessions"], storage["messages"]) == (1, 2)


def test_health_degrades_with_503_when_storage_is_unusable(tmp_path, retriever):
    broken = tmp_path  # un directorio no se puede abrir como base de datos
    with TestClient(create_app(db_path=broken, retriever=retriever)) as c:
        resp = c.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded" and body["storage"]["status"] == "error"
    assert str(tmp_path) not in resp.text  # sin rutas internas


# ----------------------------------------------------------------- catálogo
def test_courses_lists_the_sql_demo_course(client):
    courses = client.get("/api/courses").json()
    assert [c["course_id"] for c in courses] == ["sql-demo"]
    assert courses[0]["is_official_dmc"] is False
    assert len(courses[0]["topics"]) == 7


def test_lessons_are_identified_with_metadata(client):
    lessons = {l["lesson_id"]: l for l in client.get("/api/courses/sql-demo/lessons").json()}
    assert set(lessons) == {LESSON_1, LESSON_2}
    first = lessons[LESSON_1]
    assert first["course_id"] == "sql-demo"
    assert (first["source_format"], first["segment_count"], first["duration_label"]) == ("vtt", 9, "08:05")
    assert first["is_official_dmc"] is False and "FICTICIO" in first["disclaimer"]
    assert (lessons[LESSON_2]["source_format"], lessons[LESSON_2]["duration_label"]) == ("json", "13:25")


def test_lessons_of_unknown_course_is_a_structured_404(client):
    err = assert_error(client.get("/api/courses/no-existe/lessons"), 404, "course_not_found")
    assert "no-existe" in err["message"]


def test_unknown_route_and_wrong_method_are_structured(client):
    assert_error(client.get("/api/nada"), 404, "not_found")
    assert_error(client.get("/api/chat"), 405, "method_not_allowed")


# ---------------------------------------------------------------- /api/chat
def test_chat_accepts_a_valid_question_and_returns_the_contract(client):
    resp = post_chat(client, "¿Cómo funciona ORDER BY?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "DEMO"
    assert body["session_id"] and body["student_id"] == "demo-student-001"
    assert (body["course_id"], body["lesson_id"]) == ("sql-demo", LESSON_1)
    assert body["response_type"] == "grounded" and body["grounded"] is True
    assert body["topic_detected"] == "order_by"
    assert body["telemetry_event_id"] is None
    assert "FICTICIOS" in body["content_notice"] and "no es contenido oficial" in body["content_notice"]
    assert "No reemplaza al docente" in body["disclaimer"]


def test_demo_answer_is_coherent_socratic_and_cites_exact_minutes(client):
    body = post_chat(client, "¿Cómo funciona ORDER BY?").json()
    ref = body["references"][0]
    assert ref["lesson_id"] == LESSON_1
    assert (ref["start_seconds"], ref["end_seconds"], ref["timestamp"]) == (382.0, 440.0, "06:22–07:20")
    assert ref["reference"] == f"ref:sql-demo/{LESSON_1}#t=382"
    text = body["response"]
    assert "ORDER BY sirve para ordenar el resultado" in text      # cita textual de la transcripción
    assert "min 06:22–07:20" in text
    assert body["follow_up_question"].endswith("?") and body["follow_up_question"] in text


def test_question_outside_the_lesson_gets_no_invented_citations(client):
    body = post_chat(client, "¿Qué es una API REST?").json()
    assert body["response_type"] == "no_evidence" and body["grounded"] is False
    assert body["references"] == [] and body["follow_up_question"] is None
    assert body["response"].startswith(
        "No encontré ese tema en la transcripción de esta clase. "
        "Podemos revisar otro fragmento o consultar al docente."
    )
    assert "min " not in body["response"]


def test_topic_of_another_lesson_is_not_answered_from_it(client):
    body = post_chat(client, "¿Cómo funciona LEFT JOIN?", lesson_id=LESSON_1).json()
    assert body["response_type"] == "no_evidence" and body["references"] == []
    assert "NULL" not in body["response"]  # nada de la clase 2 se filtra


def test_chat_without_lesson_searches_the_whole_course_and_labels_each_citation(client):
    body = post_chat(client, "¿Cómo funciona LEFT JOIN?", lesson_id=None).json()
    assert body["lesson_id"] is None and body["grounded"] is True
    assert {r["lesson_id"] for r in body["references"]} == {LESSON_2}
    assert "Clase 2" in body["response"]  # cuando no hay clase fija, se nombra la clase de cada cita


@pytest.mark.parametrize("payload, field", [
    ({"message": ""}, "message"),
    ({"message": "    "}, "message"),
    ({"message": "x" * 1001}, "message"),
    ({}, "message"),
    ({"message": "hola", "student_id": "../etc/passwd"}, "student_id"),
    ({"message": "hola", "student_id": "con espacios"}, "student_id"),
    ({"message": "hola", "course_id": "a" * 65}, "course_id"),
    ({"message": "hola", "lesson_id": "clase;drop"}, "lesson_id"),
    ({"message": "hola", "session_id": "x y"}, "session_id"),
    ({"message": 123}, "message"),
])
def test_invalid_requests_get_controlled_422(client, payload, field):
    err = assert_error(client.post("/api/chat", json=payload), 422, "validation_error")
    assert field in [d["field"] for d in err["details"]]
    assert all(set(d) == {"field", "message"} for d in err["details"])  # sin eco del input


def test_malformed_json_is_a_controlled_422(client):
    resp = client.post("/api/chat", content="{no es json", headers={"content-type": "application/json"})
    assert_error(resp, 422, "validation_error")


def test_validation_error_does_not_echo_the_submitted_text(client):
    resp = client.post("/api/chat", json={"message": "SECRETO" * 200})
    assert resp.status_code == 422 and "SECRETO" not in resp.text


def test_unknown_course_and_lesson_are_structured_404(client):
    assert_error(post_chat(client, "hola", course_id="no-existe"), 404, "course_not_found")
    assert_error(post_chat(client, "hola", lesson_id="clase-99"), 404, "lesson_not_found")


def test_message_is_trimmed_before_processing(client):
    body = post_chat(client, "   Explícame ORDER BY   \n").json()
    assert body["grounded"] is True


# ------------------------------------------------------------ errores internos
class _ExplodingProvider(LLMProvider):
    name, mode = "demo", "DEMO"

    def generate(self, context):
        raise RuntimeError("detalle interno secreto /ruta/privada")


def test_unexpected_errors_return_a_generic_500_without_internals(db_path, retriever):
    app = create_app(db_path=db_path, retriever=retriever, provider=_ExplodingProvider())
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = post_chat(c, "Explícame ORDER BY")
    assert_error(resp, 500, "internal_error")
    assert "secreto" not in resp.text and "RuntimeError" not in resp.text and "Traceback" not in resp.text
    # y el turno fallido no deja mensajes a medias
    with TestClient(create_app(db_path=db_path, retriever=retriever)) as c:
        assert c.get("/health").json()["storage"]["messages"] == 0


def test_storage_failure_on_chat_is_a_controlled_503(tmp_path, retriever):
    with TestClient(create_app(db_path=tmp_path, retriever=retriever)) as c:
        resp = post_chat(c, "Explícame ORDER BY")
    assert_error(resp, 503, "storage_unavailable")
    assert str(tmp_path) not in resp.text
