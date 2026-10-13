"""Quiz: banco fundamentado, entrega sin respuestas, calificación en backend, anti doble envío y persistencia."""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.knowledge.quiz_bank import QuizBank
from app.knowledge.retriever import KnowledgeRetriever
from app.main import create_app
from app.services.quiz_service import OPTION_IDS, QuizService

from conftest import (
    LESSON_1, LESSON_2, STUDENT_A, STUDENT_B, answer_with, correct_options, rows, start_quiz, submit_quiz,
)


# ============================================================ banco de preguntas
@pytest.fixture(scope="module")
def bank(retriever):
    return QuizBank(retriever)


def test_demo_bank_loads_cleanly_and_has_enough_questions(bank):
    assert bank.load_errors == []
    for lesson in (LESSON_1, LESSON_2):
        assert bank.has_quiz("sql-demo", lesson) and len(bank.questions("sql-demo", lesson)) >= 5
        assert "DEMOSTRATIVO" in bank.disclaimer("sql-demo", lesson)


@pytest.mark.parametrize("lesson", [LESSON_1, LESSON_2])
def test_every_question_is_grounded_in_its_segment_and_well_formed(bank, lesson):
    ids = set()
    for q in bank.questions("sql-demo", lesson):
        assert q.question_id not in ids
        ids.add(q.question_id)
        assert len(q.options) == 4 and len(set(q.options)) == 4          # 4 alternativas distintas
        assert 0 <= q.correct_index < 4 and q.prompt.endswith("?") and q.explanation
        assert q.segment.lesson_id == lesson and q.segment.timestamp_range


def test_bank_rejects_questions_that_are_not_grounded(tmp_path, retriever):
    good = {"question_id": "ok", "concept": "SELECT", "prompt": "¿Qué hace SELECT según la clase?",
            "options": ["Lee datos", "Borra datos", "Crea tablas", "Cambia permisos"], "correct_index": 0,
            "explanation": "porque sí", "segment_id": 4, "evidence_quote": "SELECT no modifica los datos"}
    variants = {
        "cita_inventada": {**good, "question_id": "v1", "evidence_quote": "SELECT cifra los datos con AES"},
        "tres_opciones": {**good, "question_id": "v2", "options": ["a", "b", "c"]},
        "indice_fuera": {**good, "question_id": "v3", "correct_index": 4},
        "opciones_repetidas": {**good, "question_id": "v4", "options": ["Lee datos", "lee datos", "x", "y"]},
        "segmento_inexistente": {**good, "question_id": "v5", "segment_id": 99},
        "sin_explicacion": {**good, "question_id": "v6", "explanation": ""},
        "no_es_pregunta": {**good, "question_id": "v7", "prompt": "Explica que SELECT lee datos"},
        "duplicada": {**good},
    }
    folder = tmp_path / "q" / "sql-demo"
    folder.mkdir(parents=True)
    (folder / f"{LESSON_1}.json").write_text(json.dumps({
        "course_id": "sql-demo", "lesson_id": LESSON_1, "questions": [good, *variants.values()]}), encoding="utf-8")
    (folder / "clase-fantasma.json").write_text(json.dumps({"course_id": "sql-demo", "lesson_id": "clase-fantasma", "questions": []}))
    (folder / "rota.json").write_text("{no es json")
    bank = QuizBank(retriever, tmp_path / "q")
    assert [q.question_id for q in bank.questions("sql-demo", LESSON_1)] == ["ok"]      # solo la fundamentada y válida
    assert len(bank.load_errors) == len(variants) + 2
    assert not bank.has_quiz("sql-demo", LESSON_1)                                      # 1 pregunta < mínimo de 3


# ================================================================ iniciar quiz
@pytest.mark.parametrize("n", [3, 4, 5])
def test_quiz_has_between_3_and_5_questions_with_4_options(acad, n):
    resp = start_quiz(acad, n=n)
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_questions"] == len(body["questions"]) == n and body["status"] == "started"
    assert [q["position"] for q in body["questions"]] == list(range(n))
    assert len({q["question_id"] for q in body["questions"]}) == n
    for q in body["questions"]:
        assert [o["option_id"] for o in q["options"]] == list(OPTION_IDS)
        assert len({o["text"] for o in q["options"]}) == 4
        assert q["question_id"].startswith("c2-") and q["prompt"].endswith("?")


def test_default_is_five_and_out_of_range_is_rejected(acad):
    assert start_quiz(acad, n=5).json()["num_questions"] == 5
    for bad in (2, 6, 0, -1, "cinco"):
        assert acad.post("/api/quiz/start", json={"student_id": STUDENT_A, "course_id": "sql-demo",
                                                  "lesson_id": LESSON_2, "num_questions": bad}).status_code == 422
    body = acad.post("/api/quiz/start", json={"student_id": STUDENT_B, "course_id": "sql-demo", "lesson_id": LESSON_2}).json()
    assert body["num_questions"] == 5


def test_correct_answers_and_explanations_are_not_exposed_before_submitting(acad, db_path):
    started = start_quiz(acad).json()
    raw = json.dumps(started, ensure_ascii=False).lower()
    for forbidden in ("correct", "is_correct", "explanation", "explicaci", "answer_key", "score"):
        assert forbidden not in raw
    assert set(started["questions"][0]) == {"question_id", "position", "prompt", "options"}
    assert set(started["questions"][0]["options"][0]) == {"option_id", "text"}
    # y la respuesta correcta sí existe, solo en el servidor
    key = correct_options(db_path, started["attempt_id"])
    assert set(key) == {q["question_id"] for q in started["questions"]} and set(key.values()) <= set(OPTION_IDS)


def test_the_marked_correct_option_really_is_the_banks_correct_text(acad, db_path, bank):
    by_id = {q.question_id: q for q in bank.questions("sql-demo", LESSON_2)}
    for i in range(12):                         # varios intentos => varias barajas
        who = f"demo-student-{100 + i}"
        started = start_quiz(acad, student_id=who, n=5).json()
        key = correct_options(db_path, started["attempt_id"])
        for q in started["questions"]:
            texts = {o["option_id"]: o["text"] for o in q["options"]}
            assert texts[key[q["question_id"]]] == by_id[q["question_id"]].options[by_id[q["question_id"]].correct_index]


def test_options_are_shuffled_across_attempts_so_the_key_is_not_constant(acad, db_path):
    letters = set()
    for i in range(20):
        started = start_quiz(acad, student_id=f"demo-student-{200 + i}", n=5).json()
        letters.update(correct_options(db_path, started["attempt_id"]).values())
    assert len(letters) >= 3


def test_question_draw_is_reproducible_from_the_attempt_id(bank):
    qs = bank.questions("sql-demo", LESSON_2)
    assert QuizService._draw("abc123", qs, 4) == QuizService._draw("abc123", qs, 4)
    assert QuizService._draw("abc123", qs, 4) != QuizService._draw("zzz999", qs, 4)


def test_each_attempt_has_a_unique_id_and_is_stored(acad, db_path):
    a = start_quiz(acad, student_id=STUDENT_A).json()
    b = start_quiz(acad, student_id=STUDENT_B).json()
    assert a["attempt_id"] != b["attempt_id"] and len(a["attempt_id"]) == 32
    stored = rows(db_path, "SELECT * FROM quiz_attempts ORDER BY created_at")
    assert [(r["student_id"], r["course_id"], r["lesson_id"], r["status"]) for r in stored] == [
        (STUDENT_A, "sql-demo", LESSON_2, "started"), (STUDENT_B, "sql-demo", LESSON_2, "started")]
    assert a["pass_threshold_percent"] == 60.0 and "FICTICIA" in a["content_notice"]


def test_start_errors_are_structured(acad, retriever, two_course_tree, db_path, make_app):
    assert start_quiz(acad, lesson_id="clase-99").json()["error"]["code"] == "lesson_not_found"
    r = acad.post("/api/quiz/start", json={"student_id": STUDENT_A, "course_id": "nope", "lesson_id": "x"})
    assert r.json()["error"]["code"] == "course_not_found"
    app = make_app(retriever=KnowledgeRetriever(transcripts_dir=two_course_tree))      # clases sin banco de preguntas
    with TestClient(app) as c:
        r = c.post("/api/quiz/start", json={"student_id": STUDENT_A, "course_id": "curso-a", "lesson_id": "clase-01"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "quiz_not_available"


# ======================================================================= enviar
def test_all_correct_gets_full_marks_with_explanations_and_minutes(acad, db_path, retriever):
    started = start_quiz(acad, n=5).json()
    resp = submit_quiz(acad, started["attempt_id"], answer_with(db_path, started, correct=True))
    assert resp.status_code == 200
    body = resp.json()
    assert (body["total_questions"], body["correct_count"], body["answered_count"]) == (5, 5, 5)
    assert (body["score_percent"], body["grade_20"], body["passed"], body["pass_threshold_percent"]) == (100.0, 20.0, True, 60.0)
    assert body["attempt_id"] == started["attempt_id"] and body["student_id"] == STUDENT_A and body["lesson_id"] == LESSON_2
    transcript = retriever.get_lesson("sql-demo", LESSON_2)
    for r in body["results"]:
        assert r["is_correct"] and r["selected_option_id"] == r["correct_option_id"] and r["explanation"] and r["concept"]
        ref = r["reference"]
        seg = next(s for s in transcript.segments if s.start_seconds == ref["start_seconds"])
        assert (ref["lesson_id"], ref["end_seconds"], ref["timestamp"]) == (LESSON_2, seg.end_seconds, seg.timestamp_range)
    assert "no modifica la asistencia" in body["institutional_notice"] and "certificación" in body["institutional_notice"]


def test_grading_is_exact_for_partial_scores_and_threshold_is_configurable(acad, db_path, make_app):
    started = start_quiz(acad, n=4).json()
    answers = answer_with(db_path, started, correct=True)
    answers[0] = answer_with(db_path, started, correct=False)[0]          # 1 mal => 3/4
    answers[1] = answer_with(db_path, started, correct=False)[1]          # 2 mal => 2/4
    body = submit_quiz(acad, started["attempt_id"], answers).json()
    assert (body["correct_count"], body["score_percent"], body["grade_20"], body["passed"]) == (2, 50.0, 10.0, False)
    assert [r["is_correct"] for r in body["results"]] == [False, False, True, True]
    # un umbral DEMO distinto cambia el estado de aprobación, no la nota
    from app.config import AcademicConfig
    cfg = AcademicConfig(quiz_pass_threshold_percent=50.0)
    with TestClient(make_app(academic_config=cfg)) as c:
        s2 = start_quiz(c, student_id=STUDENT_B, n=4).json()
        a2 = answer_with(db_path, s2, correct=True)[:2] + answer_with(db_path, s2, correct=False)[2:]
        b2 = submit_quiz(c, s2["attempt_id"], a2, student_id=STUDENT_B).json()
    assert (b2["score_percent"], b2["passed"], b2["pass_threshold_percent"]) == (50.0, True, 50.0)


def test_unanswered_questions_count_as_incorrect(acad, db_path):
    started = start_quiz(acad, n=5).json()
    body = submit_quiz(acad, started["attempt_id"], answer_with(db_path, started, correct=True, only=2)).json()
    assert (body["answered_count"], body["correct_count"], body["score_percent"]) == (2, 2, 40.0)
    assert [r["selected_option_id"] for r in body["results"]][2:] == [None, None, None]
    empty = start_quiz(acad, student_id=STUDENT_B, n=3).json()
    zero = submit_quiz(acad, empty["attempt_id"], [], student_id=STUDENT_B).json()
    assert (zero["correct_count"], zero["score_percent"], zero["passed"]) == (0, 0.0, False)


@pytest.mark.parametrize("extra", [{"score": 100}, {"score_percent": 100.0}, {"grade": 20}, {"passed": True}, {"correct_count": 5}])
def test_a_grade_sent_by_the_browser_is_rejected(acad, db_path, extra):
    started = start_quiz(acad).json()
    payload = {"attempt_id": started["attempt_id"], "student_id": STUDENT_A,
               "answers": answer_with(db_path, started, correct=False), **extra}
    resp = acad.post("/api/quiz/submit", json=payload)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "validation_error"
    assert rows(db_path, "SELECT status FROM quiz_attempts")[0]["status"] == "started"        # nada se calificó


def test_the_same_attempt_cannot_be_answered_twice_to_change_the_grade(acad, db_path):
    started = start_quiz(acad).json()
    first = submit_quiz(acad, started["attempt_id"], answer_with(db_path, started, correct=False))
    assert first.status_code == 200 and first.json()["score_percent"] == 0.0
    second = submit_quiz(acad, started["attempt_id"], answer_with(db_path, started, correct=True))
    assert second.status_code == 409 and second.json()["error"]["code"] == "quiz_already_submitted"
    stored = rows(db_path, "SELECT * FROM quiz_attempts")[0]
    assert (stored["status"], stored["score_percent"], stored["correct_count"]) == ("completed", 0.0, 0)   # la nota no cambió
    assert len(rows(db_path, "SELECT * FROM telemetry_events WHERE event_type = 'quiz_completed'")) == 1


def test_concurrent_submissions_close_the_attempt_exactly_once(acad, db_path, make_app):
    started = start_quiz(acad).json()
    store = acad.app.state.academic_store
    graded = [{"position": i, "selected_option_id": "a", "is_correct": False} for i in range(4)]
    outcomes = []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        outcomes.append(store.complete_attempt(started["attempt_id"], graded, 0, 0.0, False, "2026-10-13T12:00:00+00:00", []))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count(True) == 1 and outcomes.count(False) == 5


def test_answers_must_belong_to_the_attempt_and_not_repeat(acad, db_path):
    started = start_quiz(acad, n=3).json()
    good = answer_with(db_path, started, correct=True)
    other_bank_q = {"question_id": "c1-q01", "option_id": "a"}
    assert submit_quiz(acad, started["attempt_id"], good + [other_bank_q]).json()["error"]["code"] == "invalid_answers"
    assert submit_quiz(acad, started["attempt_id"], good + [good[0]]).json()["error"]["code"] == "invalid_answers"
    assert submit_quiz(acad, started["attempt_id"], [{"question_id": good[0]["question_id"], "option_id": "e"}]).status_code == 422
    assert submit_quiz(acad, started["attempt_id"], good * 2).status_code == 422                # más de 5 respuestas
    assert rows(acad.app.state.academic_store.base.db_path, "SELECT status FROM quiz_attempts")[0]["status"] == "started"
    assert submit_quiz(acad, started["attempt_id"], good).status_code == 200                    # el intento seguía válido


def test_unknown_attempt_is_404(acad):
    r = submit_quiz(acad, "0" * 32, [])
    assert r.status_code == 404 and r.json()["error"]["code"] == "quiz_attempt_not_found"


# ============================================================ aislamiento y límites
def test_students_cannot_submit_or_read_each_others_attempts(acad, db_path):
    a = start_quiz(acad, student_id=STUDENT_A).json()
    b = start_quiz(acad, student_id=STUDENT_B).json()
    assert a["attempt_id"] != b["attempt_id"]
    stolen = submit_quiz(acad, a["attempt_id"], answer_with(db_path, a, correct=True), student_id=STUDENT_B)
    assert stolen.status_code == 403 and stolen.json()["error"]["code"] == "quiz_attempt_mismatch"
    assert a["attempt_id"] not in stolen.text and "prompt" not in stolen.text and "explanation" not in stolen.text
    assert rows(db_path, "SELECT status FROM quiz_attempts WHERE attempt_id = ?", (a["attempt_id"],))[0]["status"] == "started"
    # cada uno sigue con lo suyo
    assert submit_quiz(acad, a["attempt_id"], answer_with(db_path, a, True)).json()["student_id"] == STUDENT_A
    assert submit_quiz(acad, b["attempt_id"], answer_with(db_path, b, True), student_id=STUDENT_B).json()["student_id"] == STUDENT_B


def test_an_open_attempt_is_resumed_not_duplicated_and_is_private(acad, db_path):
    first = start_quiz(acad).json()
    again = start_quiz(acad).json()
    assert again["resumed"] is True and again["attempt_id"] == first["attempt_id"]
    assert again["questions"] == first["questions"] and again["attempts_used"] == 1           # mismo orden, misma baraja
    other = start_quiz(acad, student_id=STUDENT_B).json()
    assert other["resumed"] is False and other["attempt_id"] != first["attempt_id"]
    assert len(rows(db_path, "SELECT 1 FROM quiz_attempts")) == 2
    assert len(rows(db_path, "SELECT 1 FROM telemetry_events WHERE event_type = 'quiz_started'")) == 2   # retomar no duplica el evento


def test_attempts_per_lesson_are_limited(acad, db_path):
    for i in range(3):
        started = start_quiz(acad).json()
        assert started["attempts_used"] == i + 1 and started["attempts_allowed"] == 3
        assert submit_quiz(acad, started["attempt_id"], answer_with(db_path, started, True)).status_code == 200
    blocked = start_quiz(acad)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "quiz_attempt_limit"
    assert blocked.json()["error"]["details"] == {"attempts_used": 3, "attempts_allowed": 3}
    assert start_quiz(acad, lesson_id=LESSON_1).status_code == 200          # el límite es por clase
    assert start_quiz(acad, student_id=STUDENT_B).status_code == 200        # y por estudiante


def test_attempts_expire(acad, db_path, clock):
    started = start_quiz(acad).json()
    clock.advance(minutes=61)
    late = submit_quiz(acad, started["attempt_id"], answer_with(db_path, started, True))
    assert late.status_code == 410 and late.json()["error"]["code"] == "quiz_attempt_expired"
    assert rows(db_path, "SELECT status FROM quiz_attempts")[0]["status"] == "started"
    fresh = start_quiz(acad).json()                                          # un intento caducado no se retoma
    assert fresh["resumed"] is False and fresh["attempt_id"] != started["attempt_id"] and fresh["attempts_used"] == 2


# ============================================================= persistencia
def test_results_persist_in_sqlite_and_survive_a_restart(make_app, db_path):
    with TestClient(make_app()) as c1:
        started = start_quiz(c1, n=4).json()
        answers = answer_with(db_path, started, True)[:3] + answer_with(db_path, started, False)[3:]
        body = submit_quiz(c1, started["attempt_id"], answers).json()
    attempt = rows(db_path, "SELECT * FROM quiz_attempts")[0]
    assert (attempt["status"], attempt["correct_count"], attempt["score_percent"], attempt["passed"]) == ("completed", 3, 75.0, 1)
    assert attempt["submitted_at"] and attempt["total_questions"] == 4 and attempt["pass_threshold_percent"] == 60.0
    saved = rows(db_path, "SELECT * FROM quiz_answers ORDER BY position")
    assert [bool(r["is_correct"]) for r in saved] == [True, True, True, False]
    assert [r["selected_option_id"] for r in saved] == [a["option_id"] for a in answers]
    assert body["score_percent"] == 75.0
    with TestClient(make_app()) as c2:                                       # "reinicio" del servidor
        again = submit_quiz(c2, started["attempt_id"], answer_with(db_path, started, True))
        assert again.status_code == 409
        assert c2.app.state.academic_store.get_attempt(started["attempt_id"])["score_percent"] == 75.0


def test_passing_the_quiz_changes_nothing_about_attendance_or_certification(acad, db_path):
    started = start_quiz(acad).json()
    body = submit_quiz(acad, started["attempt_id"], answer_with(db_path, started, True)).json()
    assert body["passed"] is True
    assert not [k for k in body if "attend" in k or "asist" in k or "certif" in k]          # solo el resultado académico
    tables = {r["name"] for r in rows(db_path, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not [t for t in tables if "attend" in t or "asist" in t or "certif" in t]
