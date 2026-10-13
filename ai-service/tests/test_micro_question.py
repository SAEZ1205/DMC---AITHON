"""Micro-pregunta abierta opcional: cuándo se muestra, almacenamiento separado y clasificación conservadora."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import AcademicConfig
from app.models.academic import MicroCategory
from app.services.micro_classifier import DISCLAIMER, classify

from conftest import LESSON_2, STUDENT_A, STUDENT_B, answer_with, rows, start_quiz, submit_quiz

COURSE = "sql-demo"
DUE_DAY = "2026-10-13"        # 42 días desde 2026-09-01 => periodo 3 con intervalos de 14 días


def state(c, student=STUDENT_A, as_of=None):
    params = {"student_id": student, "course_id": COURSE}
    if as_of:
        params["as_of"] = as_of
    return c.get("/api/micro-question", params=params)


def answer(c, student=STUDENT_A, question_id="mq-contenido", period=3, text="El horario me queda justo por mi trabajo",
           skipped=False, as_of=DUE_DAY, **headers):
    payload = {"student_id": student, "course_id": COURSE, "question_id": question_id, "period_index": period,
               "skipped": skipped, "as_of": as_of}
    if text is not None:
        payload["answer_text"] = text
    return c.post("/api/micro-question/answer", json=payload, headers=headers)


def complete_quiz(c, db_path, student=STUDENT_A):
    started = start_quiz(c, student_id=student).json()
    submit_quiz(c, started["attempt_id"], answer_with(db_path, started, True), student_id=student)


# =========================================================== cuándo corresponde
def test_it_is_never_due_without_a_configured_reference_date(make_app):
    with TestClient(make_app(academic_config=AcademicConfig())) as c:
        body = state(c, as_of=DUE_DAY).json()
    assert (body["due"], body["reason"], body["question"]) == (False, "sin_fecha_de_referencia_configurada", None)


@pytest.mark.parametrize("as_of, reason", [
    ("2026-08-20", "antes_de_la_fecha_de_referencia"),
    ("2026-09-01", "aun_no_termina_el_primer_periodo"),
    ("2026-09-14", "aun_no_termina_el_primer_periodo"),
])
def test_it_is_not_due_before_the_first_two_weeks(acad, db_path, as_of, reason):
    complete_quiz(acad, db_path)
    body = state(acad, as_of=as_of).json()
    assert body["due"] is False and body["reason"] == reason and body["question"] is None


def test_it_only_appears_after_the_student_completed_a_quiz(acad, db_path):
    assert state(acad, as_of=DUE_DAY).json()["reason"] == "sin_quiz_completado"
    started = start_quiz(acad).json()                                   # iniciado pero no enviado: todavía no cuenta
    assert state(acad, as_of=DUE_DAY).json()["due"] is False
    submit_quiz(acad, started["attempt_id"], answer_with(db_path, started, True))
    body = state(acad, as_of=DUE_DAY).json()
    assert body["due"] is True and body["reason"] == "corresponde_en_este_periodo"
    assert state(acad, student=STUDENT_B, as_of=DUE_DAY).json()["due"] is False        # otro estudiante: sin quiz


def test_a_single_short_optional_question_with_rotation(acad, db_path):
    complete_quiz(acad, db_path)
    first = state(acad, as_of="2026-09-15").json()                      # periodo 1
    assert first["period_index"] == 1
    assert first["question"] == {"question_id": "mq-horario", "period_index": 1, "optional": True,
                                 "prompt": "¿Cómo te está acomodando el horario del curso estas semanas?"}
    assert state(acad, as_of="2026-09-29").json()["question"]["question_id"] == "mq-ritmo"          # periodo 2
    third = state(acad, as_of=DUE_DAY).json()["question"]
    assert third["question_id"] == "mq-contenido" and third["prompt"].endswith("?")
    assert state(acad, as_of="2026-10-27").json()["question"]["question_id"] == "mq-horario"        # periodo 4 vuelve a rotar
    assert "omitirla" in state(acad, as_of=DUE_DAY).json()["notice"]


def test_it_uses_the_clock_when_no_date_is_given(acad, db_path, clock):
    complete_quiz(acad, db_path)
    assert state(acad).json()["as_of"] == "2026-10-13" and state(acad).json()["due"] is True
    clock.advance(days=-60)
    assert state(acad).json()["reason"] == "antes_de_la_fecha_de_referencia"


def test_unknown_course_and_bad_ids(acad):
    assert acad.get("/api/micro-question", params={"student_id": STUDENT_A, "course_id": "nope"}).json()["error"]["code"] == "course_not_found"
    assert acad.get("/api/micro-question", params={"student_id": "../x", "course_id": COURSE}).status_code == 422
    assert acad.get("/api/micro-question", params={"student_id": STUDENT_A, "course_id": COURSE, "as_of": "ayer"}).status_code == 422


# ===================================================== responder / omitir
def test_expressed_text_and_interpreted_category_are_stored_separately(acad, db_path):
    complete_quiz(acad, db_path)
    resp = answer(acad)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered" and body["expressed_text"] == "El horario me queda justo por mi trabajo"
    interp = body["interpretation"]
    assert interp["category"] == "disponibilidad_tiempo" and interp["candidate_categories"] == ["disponibilidad_tiempo", "trabajo"]
    assert interp["confidence"] == "baja" and interp["method"] == "reglas_conservadoras"     # empate => confianza baja
    assert interp["matched_cues"] == ["horario", "trabajo"] and "no es un diagnóstico" in interp["disclaimer"]
    row = rows(db_path, "SELECT * FROM micro_responses")[0]
    assert row["answer_text"] == "El horario me queda justo por mi trabajo"                   # lo que EXPRESÓ
    assert (row["category"], row["classification_method"], row["confidence"]) == ("disponibilidad_tiempo", "reglas_conservadoras", "baja")
    assert json.loads(row["matched_cues"]) == ["horario", "trabajo"]
    assert (row["student_id"], row["course_id"], row["question_id"], row["period_index"], row["status"]) == (
        STUDENT_A, COURSE, "mq-contenido", 3, "answered")


def test_answering_is_optional_and_skipping_is_recorded_without_interpretation(acad, db_path):
    complete_quiz(acad, db_path)
    resp = answer(acad, text=None, skipped=True)
    body = resp.json()
    assert resp.status_code == 200 and body["status"] == "skipped" and body["interpretation"] is None and body["expressed_text"] is None
    row = rows(db_path, "SELECT * FROM micro_responses")[0]
    assert (row["status"], row["answer_text"], row["category"]) == ("skipped", None, None)
    assert rows(db_path, "SELECT 1 FROM telemetry_events WHERE event_type = 'micro_question_answered'") == []   # omitir no es responder


def test_after_answering_or_skipping_it_is_not_asked_again_in_the_same_period(acad, db_path):
    complete_quiz(acad, db_path)
    assert answer(acad, text=None, skipped=True).status_code == 200
    again = state(acad, as_of=DUE_DAY).json()
    assert again["due"] is False and again["reason"] == "ya_respondida_u_omitida_en_este_periodo"
    dup = answer(acad)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "micro_question_already_answered"
    assert len(rows(acad.app.state.academic_store.base.db_path, "SELECT 1 FROM micro_responses")) == 1
    nxt = state(acad, as_of="2026-10-27").json()                          # el periodo siguiente vuelve a corresponder
    assert nxt["due"] is True and nxt["period_index"] == 4


def test_answers_of_different_students_do_not_interfere(acad, db_path):
    complete_quiz(acad, db_path, STUDENT_A)
    complete_quiz(acad, db_path, STUDENT_B)
    assert answer(acad, student=STUDENT_A, text="Va muy rápido").status_code == 200
    assert state(acad, student=STUDENT_B, as_of=DUE_DAY).json()["due"] is True
    assert answer(acad, student=STUDENT_B, text="Todo bien").json()["interpretation"]["category"] == "satisfaccion"
    assert {r["student_id"]: r["category"] for r in rows(db_path, "SELECT * FROM micro_responses")} == {
        STUDENT_A: "ritmo_academico", STUDENT_B: "satisfaccion"}


@pytest.mark.parametrize("kwargs, status, code", [
    ({"text": "hola", "skipped": True}, 422, "skipped_with_text"),
    ({"text": None, "skipped": False}, 422, "answer_required_or_skipped"),
    ({"text": ""}, 422, "answer_required_or_skipped"),
    ({"question_id": "mq-horario"}, 422, "question_period_mismatch"),
    ({"period": 2}, 422, "period_not_current"),
    ({"as_of": "2026-09-05", "period": 1, "question_id": "mq-horario"}, 409, "micro_question_not_due"),
])
def test_invalid_micro_answers_get_controlled_errors(acad, db_path, kwargs, status, code):
    complete_quiz(acad, db_path)
    resp = answer(acad, **kwargs)
    assert resp.status_code == status and resp.json()["error"]["code"] == code
    assert rows(db_path, "SELECT 1 FROM micro_responses") == []


def test_micro_answer_needs_a_completed_quiz_and_valid_payload(acad):
    assert answer(acad).json()["error"]["code"] == "micro_question_not_due"
    for bad in ({"answer_text": "x" * 501}, {"period_index": 0}, {"unexpected": 1}):
        payload = {"student_id": STUDENT_A, "course_id": COURSE, "question_id": "mq-contenido", "period_index": 3,
                   "answer_text": "ok", "as_of": DUE_DAY, **bad}
        assert acad.post("/api/micro-question/answer", json=payload).status_code == 422
    r = acad.post("/api/micro-question/answer", json={"student_id": STUDENT_A, "course_id": "nope", "question_id": "mq-contenido",
                                                     "period_index": 3, "answer_text": "ok"})
    assert r.json()["error"]["code"] == "course_not_found"


def test_the_quiz_requirement_can_be_turned_off(make_app):
    with TestClient(make_app(academic_config=AcademicConfig(micro_question_reference_date="2026-09-01",
                                                            micro_question_require_quiz=False))) as c:
        assert state(c, as_of=DUE_DAY).json()["due"] is True
        assert answer(c).status_code == 200


def test_no_external_notification_or_network_is_involved(acad, db_path, no_network):
    complete_quiz(acad, db_path)
    assert state(acad, as_of=DUE_DAY).status_code == 200
    assert answer(acad, text="Me cuesta mucho el tema de joins").json()["interpretation"]["category"] == "dificultad_contenido"


# ======================================================= clasificador conservador
@pytest.mark.parametrize("text, category, confidence", [
    ("No tengo tiempo", "disponibilidad_tiempo", "media"),
    ("El horario me viene perfecto", "disponibilidad_tiempo", "baja"),         # horario + perfecto => empate, prioridad
    ("Mi trabajo termina tarde", "trabajo", "media"),
    ("Tuve una reunión larga con mi jefe", "trabajo", "media"),                # dos pistas, una sola categoría
    ("Me cuesta entender los joins", "dificultad_contenido", "media"),
    ("Va muy rápido para mí", "ritmo_academico", "media"),
    ("Todo bien, muy cómodo", "satisfaccion", "media"),
    ("El horario me queda complicado por mi trabajo", "disponibilidad_tiempo", "baja"),
    ("Prefiero estudiar por la mañana con mi equipo de amigos", "otro", "baja"),
    ("hola", "sin_informacion_suficiente", "baja"),
    ("ok", "sin_informacion_suficiente", "baja"),
])
def test_classifier_is_rule_based_and_conservative(text, category, confidence):
    result = classify(text)
    assert (result.category.value, result.confidence) == (category, confidence)


def test_negated_evaluative_cues_are_ignored_but_topical_cues_are_not():
    assert classify("No me cuesta nada").category is MicroCategory.SIN_INFORMACION_SUFICIENTE
    assert classify("No me gusta").category is MicroCategory.SIN_INFORMACION_SUFICIENTE
    assert classify("No tengo tiempo para nada").category is MicroCategory.DISPONIBILIDAD_TIEMPO
    assert classify("No me cuesta, me gusta").category is MicroCategory.SATISFACCION


def test_classifier_never_claims_high_confidence_and_every_cue_is_in_the_text():
    from app.knowledge.text_utils import tokenize
    samples = ["No tengo tiempo", "Todo bien", "El horario y el trabajo y el ritmo me cuestan", "xx", "Me gusta pero va rápido"]
    for text in samples:
        r = classify(text)
        assert r.confidence in ("baja", "media")
        words = " ".join(tokenize(text))
        assert all(cue in words for cue in r.matched_cues)          # solo pistas realmente presentes: verificable
    mixed = classify("El horario y el trabajo me superan, y además va rápido")
    assert mixed.confidence == "baja" and len(mixed.candidates) >= 2


def test_classification_is_a_topic_label_not_a_diagnosis():
    assert "diagnóstico" in DISCLAIMER and "psicológica" in DISCLAIMER and "no se usa para decidir" in DISCLAIMER
    assert not {c.value for c in MicroCategory} & {"ansiedad", "depresion", "estres", "burnout", "riesgo"}
