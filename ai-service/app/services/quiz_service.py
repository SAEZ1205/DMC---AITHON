"""
Quiz académico corto (3-5 preguntas) por clase, calificado SIEMPRE en el backend.

Seguridad
  * `start` nunca devuelve la alternativa correcta ni la explicación; solo se revelan tras `submit`.
  * La nota la calcula el servidor; el contrato rechaza cualquier campo extra (p. ej. un `score` del navegador).
  * Un intento solo puede enviarse una vez: el cierre es una actualización atómica condicionada a
    `status='started'`, así que dos envíos concurrentes no pueden alterar la nota.
  * Límites: intentos por estudiante y clase, caducidad del intento y máximo 5 respuestas.
  * Aprobar el quiz NO modifica asistencia ni implica certificación: el resultado solo se entrega
    para que otro módulo aplique las reglas institucionales.
"""
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from app.config import AcademicConfig
from app.knowledge.quiz_bank import BankQuestion, QuizBank
from app.knowledge.retriever import KnowledgeRetriever
from app.models.academic import (
    QuestionResult, QuizOption, QuizQuestionPublic, QuizStartRequest, QuizStartResponse, QuizSubmitRequest,
    QuizSubmitResponse,
)
from app.models.events import TELEMETRY_NOTICE, TelemetryEventType
from app.models.transcript import SegmentMatch
from app.services.errors import AppError, CourseNotFound, LessonNotFound
from app.services.telemetry_service import TelemetryService
from app.storage.academic_store import AcademicStore, new_id

OPTION_IDS = ("a", "b", "c", "d")
INSTITUTIONAL_NOTICE = (
    "Este resultado es solo académico y de demostración: no modifica la asistencia ni implica certificación. "
    "Otro módulo aplica las reglas institucionales."
)
DEMO_NOTICE = (
    "Modo DEMO: preguntas redactadas para el MVP a partir de una transcripción FICTICIA; "
    "no es material oficial de DMC Institute."
)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


class QuizService:
    def __init__(
        self,
        retriever: KnowledgeRetriever,
        bank: QuizBank,
        store: AcademicStore,
        telemetry: TelemetryService,
        config: AcademicConfig,
        clock: Callable[[], datetime],
    ):
        self.retriever, self.bank, self.store = retriever, bank, store
        self.telemetry, self.config, self.clock = telemetry, config, clock

    # ---------------------------------------------------------------- inicio
    def start(self, req: QuizStartRequest, *, idem_scope: Optional[str] = None) -> QuizStartResponse:
        lesson = self._lesson(req.course_id, req.lesson_id)
        available = self.bank.questions(req.course_id, req.lesson_id)
        if not self.bank.has_quiz(req.course_id, req.lesson_id):
            raise AppError(404, "quiz_not_available", "Esta clase todavía no tiene un quiz disponible.")
        n = min(req.num_questions, len(available))
        now = self.clock()
        resumed = False

        with self.store.transaction() as conn:
            open_attempt = self.store.find_open_attempt_tx(conn, req.student_id, req.course_id, req.lesson_id, iso(now))
            if open_attempt:
                attempt, resumed = open_attempt, True
                answers = [dict(a) for a in self._answers_for(conn, attempt["attempt_id"])]
            else:
                used = self.store.count_attempts_tx(conn, req.student_id, req.course_id, req.lesson_id)
                if used >= self.config.quiz_max_attempts_per_lesson:
                    raise AppError(
                        429, "quiz_attempt_limit",
                        f"Alcanzaste el máximo de {self.config.quiz_max_attempts_per_lesson} intentos para esta clase.",
                        details={"attempts_used": used, "attempts_allowed": self.config.quiz_max_attempts_per_lesson},
                    )
                attempt_id = new_id()
                answers = self._draw(attempt_id, available, n)
                attempt = {
                    "attempt_id": attempt_id, "student_id": req.student_id, "course_id": req.course_id,
                    "lesson_id": req.lesson_id, "total_questions": n, "created_at": iso(now),
                    "expires_at": iso(now + timedelta(minutes=self.config.quiz_attempt_ttl_minutes)),
                    "pass_threshold_percent": self.config.quiz_pass_threshold_percent, "status": "started",
                }
                self.store.insert_attempt_tx(conn, attempt, answers)
                self.store.insert_event_tx(conn, **self.telemetry.event_spec(
                    TelemetryEventType.QUIZ_STARTED, "DEMO",
                    {"attempt_id": attempt_id, "num_questions": n, "resumed": False},
                    student_id=req.student_id, course_id=req.course_id, lesson_id=req.lesson_id, idem_scope=idem_scope))
            attempts_used = self.store.count_attempts_tx(conn, req.student_id, req.course_id, req.lesson_id)

        by_id = {q.question_id: q for q in available}
        questions = [self._public(a, by_id[a["question_id"]]) for a in answers]
        return QuizStartResponse(
            attempt_id=attempt["attempt_id"], student_id=req.student_id, course_id=req.course_id, lesson_id=req.lesson_id,
            lesson_title=lesson.title, status="started", num_questions=len(questions), questions=questions,
            created_at=attempt["created_at"], expires_at=attempt["expires_at"], resumed=resumed,
            attempts_used=attempts_used, attempts_allowed=self.config.quiz_max_attempts_per_lesson,
            pass_threshold_percent=attempt["pass_threshold_percent"], content_notice=DEMO_NOTICE,
            telemetry_notice=TELEMETRY_NOTICE,
        )

    @staticmethod
    def _answers_for(conn, attempt_id: str):
        rows = conn.execute("SELECT * FROM quiz_answers WHERE attempt_id = ? ORDER BY position", (attempt_id,)).fetchall()
        for r in rows:
            d = dict(r)
            d["option_order"] = json.loads(d["option_order"])
            yield d

    @staticmethod
    def _draw(attempt_id: str, available: List[BankQuestion], n: int) -> List[Dict]:
        """Elige preguntas y baraja alternativas de forma reproducible a partir del id del intento."""
        rng = random.Random(attempt_id)
        chosen = rng.sample(range(len(available)), n)
        drawn = []
        for position, idx in enumerate(chosen):
            q = available[idx]
            order = rng.sample(range(len(q.options)), len(q.options))       # order[i] = índice original en la posición i
            drawn.append({
                "position": position, "question_id": q.question_id, "option_order": order,
                "correct_option_id": OPTION_IDS[order.index(q.correct_index)],
            })
        return drawn

    @staticmethod
    def _public(answer: Dict, q: BankQuestion) -> QuizQuestionPublic:
        options = [QuizOption(option_id=OPTION_IDS[i], text=q.options[orig]) for i, orig in enumerate(answer["option_order"])]
        return QuizQuestionPublic(question_id=q.question_id, position=answer["position"], prompt=q.prompt, options=options)

    # ----------------------------------------------------------------- envío
    def submit(self, req: QuizSubmitRequest, *, idem_scope: Optional[str] = None) -> QuizSubmitResponse:
        attempt = self.store.get_attempt(req.attempt_id)
        if attempt is None:
            raise AppError(404, "quiz_attempt_not_found", "El intento de quiz indicado no existe.")
        if attempt["student_id"] != req.student_id:
            # Mensaje genérico: no revela de quién es el intento ni su contenido.
            raise AppError(403, "quiz_attempt_mismatch", "El intento no corresponde al estudiante indicado.")
        if attempt["status"] == "completed":
            raise AppError(409, "quiz_already_submitted", "Este intento ya fue enviado; no se puede responder dos veces.")
        now = self.clock()
        if iso(now) > attempt["expires_at"]:
            raise AppError(410, "quiz_attempt_expired", "El intento caducó. Inicia un nuevo quiz.")

        stored = self.store.get_attempt_answers(req.attempt_id)
        valid_ids = {a["question_id"] for a in stored}
        chosen: Dict[str, str] = {}
        for ans in req.answers:
            if ans.question_id not in valid_ids:
                raise AppError(422, "invalid_answers", f"La pregunta '{ans.question_id}' no pertenece a este intento.")
            if ans.question_id in chosen:
                raise AppError(422, "invalid_answers", f"La pregunta '{ans.question_id}' aparece más de una vez.")
            chosen[ans.question_id] = ans.option_id

        bank = {q.question_id: q for q in self.bank.questions(attempt["course_id"], attempt["lesson_id"])}
        graded, results = [], []
        for a in stored:
            selected = chosen.get(a["question_id"])
            correct = selected is not None and selected == a["correct_option_id"]
            graded.append({"position": a["position"], "selected_option_id": selected, "is_correct": correct})
            q = bank[a["question_id"]]
            results.append(QuestionResult(
                question_id=q.question_id, position=a["position"], prompt=q.prompt, selected_option_id=selected,
                correct_option_id=a["correct_option_id"], is_correct=correct, explanation=q.explanation, concept=q.concept,
                reference=self.retriever.build_segment_citations(
                    [SegmentMatch(segment=q.segment, lesson_title=q.lesson_title, score=1.0, matched_terms=[])])[0],
            ))
        total = len(stored)
        correct_count = sum(1 for g in graded if g["is_correct"])
        score = round(100.0 * correct_count / total, 1)
        passed = score >= attempt["pass_threshold_percent"]

        ids = dict(student_id=attempt["student_id"], course_id=attempt["course_id"], lesson_id=attempt["lesson_id"])
        events = [self.telemetry.event_spec(
            TelemetryEventType.QUIZ_COMPLETED, "DEMO",
            {"attempt_id": req.attempt_id, "total_questions": total, "correct_count": correct_count,
             "score_percent": score, "passed": passed}, idem_scope=idem_scope, **ids)]
        for seq, r in enumerate(x for x in results if x.selected_option_id is not None and not x.is_correct):
            events.append(self.telemetry.event_spec(
                TelemetryEventType.TOPIC_DIFFICULTY, "DEMO",
                {"topic": r.concept, "signal": "quiz_incorrect", "question_id": r.question_id, "attempt_id": req.attempt_id},
                idem_scope=idem_scope, seq=seq + 1, **ids))

        submitted_at = iso(now)
        if not self.store.complete_attempt(req.attempt_id, graded, correct_count, score, passed, submitted_at, events):
            raise AppError(409, "quiz_already_submitted", "Este intento ya fue enviado; no se puede responder dos veces.")

        return QuizSubmitResponse(
            attempt_id=req.attempt_id, student_id=attempt["student_id"], course_id=attempt["course_id"],
            lesson_id=attempt["lesson_id"], total_questions=total,
            answered_count=sum(1 for g in graded if g["selected_option_id"] is not None),
            correct_count=correct_count, score_percent=score, grade_20=round(20.0 * correct_count / total, 1),
            passed=passed, pass_threshold_percent=attempt["pass_threshold_percent"], results=results,
            submitted_at=submitted_at, institutional_notice=INSTITUTIONAL_NOTICE, content_notice=DEMO_NOTICE,
        )

    # ------------------------------------------------------------------ util
    def _lesson(self, course_id: str, lesson_id: str):
        if self.retriever.get_course(course_id) is None and not self.retriever.list_lessons(course_id):
            raise CourseNotFound(course_id)
        lesson = self.retriever.get_lesson(course_id, lesson_id)
        if lesson is None:
            raise LessonNotFound(course_id, lesson_id)
        return lesson
