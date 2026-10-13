"""Telemetría del asistente: catálogo cerrado, eventos, idempotencia, privacidad y contrato de consulta."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import AcademicConfig
from app.models.events import EVENT_CATALOG, EVENT_TYPES, TelemetryEventType

from conftest import (
    LESSON_1, LESSON_2, STUDENT_A, STUDENT_B, TELEMETRY_TOKEN, answer_with, post_chat, rows, start_quiz, submit_quiz,
)

AUTH = {"X-Telemetry-Token": TELEMETRY_TOKEN}
EXPECTED_TYPES = ["chat_question", "topic_difficulty", "content_retrieved", "lesson_summary_requested",
                  "missed_content_requested", "quiz_started", "quiz_completed", "micro_question_answered"]


def events(db_path, event_type=None):
    sql = "SELECT * FROM telemetry_events" + (" WHERE event_type = ?" if event_type else "") + " ORDER BY created_at, rowid"
    out = rows(db_path, sql, (event_type,) if event_type else ())
    for r in out:
        r["payload"] = json.loads(r["payload"])
    return out


# ====================================================================== catálogo
def test_the_event_catalog_is_closed_and_documented():
    assert list(EVENT_TYPES) == EXPECTED_TYPES
    assert [t.value for t in TelemetryEventType] == EXPECTED_TYPES
    assert set(EVENT_CATALOG) == set(EXPECTED_TYPES)
    assert all(v["description"] and v["payload"] for v in EVENT_CATALOG.values())


def test_catalog_endpoint_exposes_events_and_the_student_notice(acad):
    body = acad.get("/api/telemetry/catalog").json()
    assert list(body["event_types"]) == EXPECTED_TYPES
    assert "registra tu actividad académica" in body["student_notice"] and body["data_scope"] == "demo_synthetic"


def test_the_database_has_no_event_type_outside_the_catalog_after_a_full_flow(acad, db_path):
    full_flow(acad, db_path)
    assert {e["event_type"] for e in events(db_path)} <= set(EXPECTED_TYPES)


# =================================================================== eventos
def full_flow(c, db_path, student=STUDENT_A):
    post_chat(c, "Explícame ORDER BY", student_id=student, lesson_id=LESSON_1)
    s = post_chat(c, "Explícame ORDER BY", student_id=student, lesson_id=LESSON_1).json()["session_id"]
    post_chat(c, "no entendí", student_id=student, lesson_id=LESSON_1, session_id=s)
    c.get(f"/api/courses/sql-demo/lessons/{LESSON_1}/summary", params={"student_id": student})
    c.post("/api/lessons/missed-content", json={"student_id": student, "course_id": "sql-demo", "lesson_id": LESSON_2,
                                                "absence_intervals": [{"start_seconds": 180, "end_seconds": 420}]})
    started = start_quiz(c, student_id=student, n=4).json()
    answers = answer_with(db_path, started, True)[:2] + answer_with(db_path, started, False)[2:]
    submit_quiz(c, started["attempt_id"], answers, student_id=student)
    c.post("/api/micro-question/answer", json={"student_id": student, "course_id": "sql-demo", "question_id": "mq-contenido",
                                              "period_index": 3, "answer_text": "Me cuesta un poco", "as_of": "2026-10-13"})
    return started


def test_every_catalog_event_is_generated_by_a_realistic_flow(acad, db_path):
    full_flow(acad, db_path)
    produced = {e["event_type"] for e in events(db_path)}
    assert produced == set(EXPECTED_TYPES)


def test_chat_events_have_identifiers_and_payload_without_message_text(acad, db_path):
    msg = "Explícame ORDER BY, por favor, con paciencia"
    resp = post_chat(acad, msg, student_id=STUDENT_A, lesson_id=LESSON_1).json()
    chat = events(db_path, "chat_question")[0]
    assert chat["student_id"] == STUDENT_A and chat["course_id"] == "sql-demo" and chat["lesson_id"] == LESSON_1
    assert chat["session_id"] == resp["session_id"] and chat["mode"] == "DEMO" and chat["event_id"] and chat["created_at"]
    assert chat["payload"] == {"user_intent": "question", "response_type": "grounded", "grounded": True,
                               "topic": "ORDER BY", "topic_id": "order_by", "message_length": len(msg),
                               "guardian": "relevant", "guardian_method": "heuristic"}
    retrieved = events(db_path, "content_retrieved")[0]
    assert retrieved["payload"]["segments"] == [{"lesson_id": LESSON_1, "start_seconds": 382.0, "end_seconds": 440.0}]
    assert retrieved["payload"]["source"] == "chat"
    assert all(msg not in json.dumps(e["payload"]) and "paciencia" not in json.dumps(e["payload"]) for e in events(db_path))


def test_no_evidence_questions_log_the_question_but_no_retrieval(acad, db_path):
    post_chat(acad, "¿Qué es una API REST?")
    assert [e["payload"]["response_type"] for e in events(db_path, "chat_question")] == ["no_evidence"]
    assert events(db_path, "content_retrieved") == [] and events(db_path, "topic_difficulty") == []


def test_topic_difficulty_comes_only_from_observable_signals(acad, db_path):
    s = post_chat(acad, "Explícame WHERE").json()["session_id"]
    assert events(db_path, "topic_difficulty") == []                                 # una pregunta normal no es dificultad
    post_chat(acad, "no entendí", session_id=s)
    ev = events(db_path, "topic_difficulty")
    assert [e["payload"] for e in ev] == [{"topic": "WHERE", "signal": "repeat_request"}]
    post_chat(acad, "otro ejemplo", session_id=s)
    assert len(events(db_path, "topic_difficulty")) == 1


def test_quiz_events_and_incorrect_answers(acad, db_path):
    started = start_quiz(acad, n=4).json()
    ev = events(db_path, "quiz_started")[0]
    assert ev["payload"] == {"attempt_id": started["attempt_id"], "num_questions": 4, "resumed": False}
    assert (ev["student_id"], ev["lesson_id"], ev["session_id"]) == (STUDENT_A, LESSON_2, None)
    answers = answer_with(db_path, started, True)[:3] + answer_with(db_path, started, False)[3:]
    body = submit_quiz(acad, started["attempt_id"], answers).json()
    done = events(db_path, "quiz_completed")[0]
    assert done["payload"] == {"attempt_id": started["attempt_id"], "total_questions": 4, "correct_count": 3,
                               "score_percent": 75.0, "passed": True}
    wrong = events(db_path, "topic_difficulty")
    assert len(wrong) == 1 and wrong[0]["payload"]["signal"] == "quiz_incorrect"
    failed = next(r for r in body["results"] if not r["is_correct"])
    assert wrong[0]["payload"]["topic"] == failed["concept"] and wrong[0]["payload"]["question_id"] == failed["question_id"]


def test_unanswered_questions_are_not_reported_as_topic_difficulty(acad, db_path):
    started = start_quiz(acad, n=3).json()
    submit_quiz(acad, started["attempt_id"], answer_with(db_path, started, True, only=1))
    assert events(db_path, "topic_difficulty") == []


def test_summary_and_micro_events(acad, db_path):
    full_flow(acad, db_path)
    s = events(db_path, "lesson_summary_requested")[0]
    assert s["lesson_id"] == LESSON_1 and s["student_id"] == STUDENT_A and s["payload"]["engine"] == "extractive"
    assert s["payload"]["section_count"] == 4
    m = events(db_path, "micro_question_answered")[0]
    assert m["payload"] == {"question_id": "mq-contenido", "period_index": 3, "category": "dificultad_contenido", "confidence": "media"}
    assert "Me cuesta un poco" not in json.dumps(m["payload"])                    # el texto vive en micro_responses, no en métricas
    assert m["course_id"] == "sql-demo" and m["lesson_id"] is None


def test_summary_without_student_records_an_anonymous_event(acad, db_path):
    acad.get(f"/api/courses/sql-demo/lessons/{LESSON_1}/summary")
    ev = events(db_path, "lesson_summary_requested")[0]
    assert ev["student_id"] is None and ev["lesson_id"] == LESSON_1


def test_failed_requests_record_nothing(acad, db_path):
    acad.get(f"/api/courses/sql-demo/lessons/clase-99/summary")
    acad.post("/api/lessons/missed-content", json={"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_2,
                                                  "absence_intervals": [{"start_seconds": -1, "end_seconds": 5}]})
    post_chat(acad, "hola", course_id="nope")
    assert events(db_path) == []


# ============================================================== idempotencia
def test_event_level_idempotency_key_prevents_duplicates(acad):
    store = acad.app.state.academic_store
    with store.transaction() as conn:
        first = store.insert_event_tx(conn, "quiz_started", "DEMO", {"x": 1}, student_id=STUDENT_A, idempotency_key="k1")
        again = store.insert_event_tx(conn, "quiz_started", "DEMO", {"x": 1}, student_id=STUDENT_A, idempotency_key="k1")
        other = store.insert_event_tx(conn, "quiz_started", "DEMO", {"x": 1}, student_id=STUDENT_A, idempotency_key="k2")
        free = store.insert_event_tx(conn, "quiz_started", "DEMO", {"x": 1}, student_id=STUDENT_A)
        free2 = store.insert_event_tx(conn, "quiz_started", "DEMO", {"x": 1}, student_id=STUDENT_A)
    assert first and again is None and other and free and free2          # sin clave no se deduplica


def test_a_retried_chat_with_the_same_key_does_not_duplicate_messages_or_events(acad, db_path):
    key = {"Idempotency-Key": "retry-chat-0001"}
    first = acad.post("/api/chat", json={"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_1,
                                         "message": "Explícame ORDER BY"}, headers=key)
    n_events = len(events(db_path))
    retry = acad.post("/api/chat", json={"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_1,
                                         "message": "Explícame ORDER BY"}, headers=key)
    assert first.status_code == retry.status_code == 200
    assert retry.json() == first.json() and retry.headers["idempotent-replay"] == "true"
    assert "idempotent-replay" not in first.headers
    assert len(events(db_path)) == n_events and n_events == 2                    # chat_question + content_retrieved
    assert len(rows(db_path, "SELECT 1 FROM messages")) == 2 and len(rows(db_path, "SELECT 1 FROM sessions")) == 1


def test_a_new_key_is_processed_normally(acad, db_path):
    for i in range(2):
        acad.post("/api/chat", json={"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_1,
                                     "message": "Explícame ORDER BY"}, headers={"Idempotency-Key": f"key-number-{i}"})
    assert len(rows(db_path, "SELECT 1 FROM sessions")) == 2 and len(events(db_path, "chat_question")) == 2


def test_reusing_a_key_with_a_different_body_is_rejected(acad, db_path):
    h = {"Idempotency-Key": "same-key-123"}
    acad.post("/api/chat", json={"student_id": STUDENT_A, "course_id": "sql-demo", "message": "Explícame WHERE"}, headers=h)
    clash = acad.post("/api/chat", json={"student_id": STUDENT_A, "course_id": "sql-demo", "message": "Explícame ORDER BY"}, headers=h)
    assert clash.status_code == 422 and clash.json()["error"]["code"] == "idempotency_key_reuse"
    assert len(rows(db_path, "SELECT 1 FROM messages")) == 2


def test_idempotency_keys_are_scoped_per_student_and_endpoint(acad, db_path):
    h = {"Idempotency-Key": "shared-key-77"}
    body = {"course_id": "sql-demo", "lesson_id": LESSON_1, "message": "Explícame ORDER BY"}
    a = acad.post("/api/chat", json={"student_id": STUDENT_A, **body}, headers=h).json()
    b = acad.post("/api/chat", json={"student_id": STUDENT_B, **body}, headers=h).json()
    assert a["session_id"] != b["session_id"] and a["student_id"] == STUDENT_A and b["student_id"] == STUDENT_B
    q = acad.post("/api/quiz/start", json={"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_1}, headers=h)
    assert q.status_code == 200 and "attempt_id" in q.json()                       # misma clave en otro endpoint: no es un replay


@pytest.mark.parametrize("bad", ["corta", "con espacios aqui", "x" * 65, "ab/cd/efgh"])
def test_invalid_idempotency_keys_are_rejected(acad, bad):
    r = acad.post("/api/chat", json={"student_id": STUDENT_A, "course_id": "sql-demo", "message": "hola"},
                  headers={"Idempotency-Key": bad})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_quiz_submit_retry_returns_the_original_result_not_a_conflict(acad, db_path):
    started = start_quiz(acad, n=4).json()
    answers = answer_with(db_path, started, True)
    h = {"Idempotency-Key": "submit-retry-1"}
    first = submit_quiz(acad, started["attempt_id"], answers, **h)
    retry = submit_quiz(acad, started["attempt_id"], answers, **h)
    assert first.status_code == retry.status_code == 200 and retry.json() == first.json()
    assert retry.headers["idempotent-replay"] == "true"
    assert len(events(db_path, "quiz_completed")) == 1
    no_key = submit_quiz(acad, started["attempt_id"], answers)                  # sin clave, el reenvío sigue siendo un conflicto
    assert no_key.status_code == 409


def test_start_micro_and_missed_retries_are_replayed(acad, db_path):
    h = {"Idempotency-Key": "start-retry-01"}
    s1, s2 = start_quiz(acad, **h).json(), start_quiz(acad, **h).json()
    assert s1 == s2 and len(events(db_path, "quiz_started")) == 1
    payload = {"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_2,
               "absence_intervals": [{"start_seconds": 180, "end_seconds": 420}]}
    hm = {"Idempotency-Key": "missed-retry-1"}
    m1 = acad.post("/api/lessons/missed-content", json=payload, headers=hm).json()
    m2 = acad.post("/api/lessons/missed-content", json=payload, headers=hm).json()
    assert m1 == m2 and len(events(db_path, "missed_content_requested")) == 1
    submit_quiz(acad, s1["attempt_id"], answer_with(db_path, s1, True))
    hq = {"Idempotency-Key": "micro-retry-01"}
    body = {"student_id": STUDENT_A, "course_id": "sql-demo", "question_id": "mq-contenido", "period_index": 3,
            "answer_text": "Va muy rápido", "as_of": "2026-10-13"}
    r1 = acad.post("/api/micro-question/answer", json=body, headers=hq)
    r2 = acad.post("/api/micro-question/answer", json=body, headers=hq)
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    assert len(events(db_path, "micro_question_answered")) == 1 and len(rows(db_path, "SELECT 1 FROM micro_responses")) == 1


# ============================================================ aviso al estudiante
def test_every_student_facing_response_tells_that_activity_produces_metrics(acad, db_path):
    started = start_quiz(acad).json()
    responses = [
        post_chat(acad, "Explícame ORDER BY").json(),
        acad.get(f"/api/courses/sql-demo/lessons/{LESSON_1}/summary").json(),
        acad.post("/api/lessons/missed-content", json={"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_2,
                                                      "absence_intervals": [{"start_seconds": 0, "end_seconds": 30}]}).json(),
        started,
    ]
    for body in responses:
        assert "registra tu actividad académica" in body["telemetry_notice"]


# ============================================================ consulta (dashboard)
def test_events_endpoint_is_disabled_without_a_configured_token(make_app):
    with TestClient(make_app(academic_config=AcademicConfig())) as c:
        r = c.get("/api/telemetry/events", headers=AUTH)
    assert r.status_code == 403 and r.json()["error"]["code"] == "telemetry_events_disabled"


@pytest.mark.parametrize("headers", [{}, {"X-Telemetry-Token": "equivocado"}, {"X-Telemetry-Token": TELEMETRY_TOKEN + "x"}])
def test_events_endpoint_requires_the_right_token(acad, headers):
    r = acad.get("/api/telemetry/events", headers=headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "telemetry_unauthorized"


def test_events_expose_pseudonyms_never_real_identifiers(acad, db_path):
    full_flow(acad, db_path, STUDENT_A)
    full_flow(acad, db_path, STUDENT_B)
    r = acad.get("/api/telemetry/events", headers=AUTH, params={"limit": 500})
    assert r.status_code == 200
    body = r.json()
    assert "demo-student" not in r.text and body["data_scope"] == "demo_synthetic" and "autenticación" in body["privacy_notice"]
    refs = {e["student_ref"] for e in body["events"]}
    assert len(refs) == 2 and all(x.startswith("est_") and len(x) == 16 for x in refs)
    again = acad.get("/api/telemetry/events", headers=AUTH, params={"limit": 500}).json()
    assert {e["student_ref"] for e in again["events"]} == refs                    # seudónimo estable
    first = body["events"][0]
    assert set(first) == {"event_id", "event_type", "student_ref", "course_id", "lesson_id", "session_id", "mode", "payload", "timestamp"}


def test_pseudonyms_depend_on_the_salt(make_app, db_path):
    def ref(salt):
        cfg = AcademicConfig(telemetry_api_token=TELEMETRY_TOKEN, telemetry_pseudonym_salt=salt)
        with TestClient(make_app(academic_config=cfg)) as c:
            post_chat(c, "Explícame ORDER BY", student_id=STUDENT_A, lesson_id=LESSON_1)
            return c.get("/api/telemetry/events", headers=AUTH).json()["events"][0]["student_ref"]
    assert ref("sal-1") != ref("sal-2")


def test_events_filters_and_pagination(acad, db_path):
    full_flow(acad, db_path)
    total = acad.get("/api/telemetry/events", headers=AUTH, params={"limit": 500}).json()["count"]
    quiz = acad.get("/api/telemetry/events", headers=AUTH, params={"event_type": "quiz_completed"}).json()
    assert quiz["count"] == 1 and quiz["events"][0]["event_type"] == "quiz_completed"
    by_lesson = acad.get("/api/telemetry/events", headers=AUTH, params={"lesson_id": LESSON_2}).json()
    assert by_lesson["count"] >= 2 and {e["lesson_id"] for e in by_lesson["events"]} == {LESSON_2}
    page1 = acad.get("/api/telemetry/events", headers=AUTH, params={"limit": 3}).json()
    assert page1["count"] == 3 and page1["next_before"]
    page2 = acad.get("/api/telemetry/events", headers=AUTH, params={"limit": 500, "before": page1["next_before"]}).json()
    ids1, ids2 = {e["event_id"] for e in page1["events"]}, {e["event_id"] for e in page2["events"]}
    assert not ids1 & ids2 and len(ids1 | ids2) <= total
    assert acad.get("/api/telemetry/events", headers=AUTH, params={"event_type": "inventado"}).json()["error"]["code"] == "invalid_event_type"
    assert acad.get("/api/telemetry/events", headers=AUTH, params={"limit": 501}).status_code == 422
    assert acad.get("/api/telemetry/events", headers=AUTH, params={"since": "ayer"}).json()["error"]["code"] == "invalid_datetime"
    assert acad.get("/api/telemetry/events", headers=AUTH, params={"since": "2999-01-01T00:00:00Z"}).json()["count"] == 0


# ===================================================================== resumen
def test_summary_of_an_empty_database_is_all_zeros(acad):
    body = acad.get("/api/telemetry/summary").json()
    assert body["total_events"] == 0 and body["distinct_students"] == 0 and body["top_topics"] == []
    assert [i["key"] for i in body["events_by_type"]] == EXPECTED_TYPES and all(i["count"] == 0 for i in body["events_by_type"])
    assert body["quiz"] == {"started": 0, "completed": 0, "average_score_percent": None, "passed": 0, "pass_rate_percent": None}


def test_summary_aggregates_without_exposing_students(acad, db_path):
    full_flow(acad, db_path, STUDENT_A)
    full_flow(acad, db_path, STUDENT_B)
    r = acad.get("/api/telemetry/summary")
    body = r.json()
    assert "demo-student" not in r.text and "est_" not in r.text                  # ni ids ni seudónimos: solo agregados
    by_type = {i["key"]: i["count"] for i in body["events_by_type"]}
    assert by_type["quiz_started"] == by_type["quiz_completed"] == 2 and by_type["chat_question"] == 6
    assert by_type["lesson_summary_requested"] == by_type["missed_content_requested"] == by_type["micro_question_answered"] == 2
    assert body["distinct_students"] == 2 and body["total_events"] == sum(by_type.values())
    assert body["top_topics"][0] == {"key": "ORDER BY", "count": 6}
    assert {"ORDER BY"} <= {i["key"] for i in body["topics_with_difficulty_signals"]}
    assert body["most_retrieved_lessons"] == [{"key": LESSON_1, "count": 6}]
    assert body["quiz"]["started"] == 2 and body["quiz"]["completed"] == 2 and body["quiz"]["average_score_percent"] == 50.0
    assert body["quiz"]["passed"] == 0 and body["quiz"]["pass_rate_percent"] == 0.0
    assert body["micro_question_categories"] == [{"key": "dificultad_contenido", "count": 2}]
    assert body["mode_breakdown"] == [{"key": "DEMO", "count": body["total_events"]}]
    assert body["is_demo_metrics"] is True and body["data_scope"] == "demo_synthetic"


def test_summary_filters(acad, db_path):
    full_flow(acad, db_path)
    only_l2 = acad.get("/api/telemetry/summary", params={"lesson_id": LESSON_2}).json()
    assert {i["key"]: i["count"] for i in only_l2["events_by_type"]}["quiz_started"] == 1
    assert {i["key"]: i["count"] for i in only_l2["events_by_type"]}["chat_question"] == 0
    assert only_l2["filters"]["lesson_id"] == LESSON_2
    assert acad.get("/api/telemetry/summary", params={"course_id": "otro"}).json()["total_events"] == 0
    assert acad.get("/api/telemetry/summary", params={"until": "no-fecha"}).json()["error"]["code"] == "invalid_datetime"


def test_summary_contract_has_no_risk_or_dropout_fields(acad, db_path):
    full_flow(acad, db_path)
    body = acad.get("/api/telemetry/summary").json()
    banned = ("risk", "riesgo", "dropout", "desercion", "churn", "attendance", "asistencia", "probab")
    assert not [k for k in body if any(b in k.lower() for b in banned)]
    assert "no son una probabilidad de deserción" in body["methodology_notice"].lower()
    assert "no miden asistencia" in body["methodology_notice"].lower()


def test_everything_runs_without_any_external_api(acad, db_path, no_network):
    full_flow(acad, db_path)
    assert acad.get("/api/telemetry/summary").status_code == 200
    assert acad.get("/api/telemetry/events", headers=AUTH).status_code == 200
