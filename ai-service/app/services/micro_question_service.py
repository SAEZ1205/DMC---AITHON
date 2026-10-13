"""
Micro-pregunta abierta opcional (una por periodo, aprox. cada dos semanas, después del quiz).

  * Se muestra UNA sola pregunta breve cuando corresponde según la fecha de referencia configurada.
  * Responder es opcional: se puede omitir, y tras responder u omitir no se vuelve a preguntar en ese periodo.
  * Se guarda por separado lo que el estudiante EXPRESÓ y lo que el sistema INTERPRETÓ (reglas conservadoras).
  * No dispara notificaciones externas ni decisiones sobre el estudiante.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from app.config import AcademicConfig
from app.knowledge.retriever import KnowledgeRetriever
from app.models.academic import (
    MicroAnswerRequest, MicroAnswerResponse, MicroInterpretation, MicroQuestionPublic, MicroQuestionState,
)
from app.models.events import TELEMETRY_NOTICE, TelemetryEventType
from app.services.errors import AppError, CourseNotFound
from app.services.micro_classifier import DISCLAIMER, classify
from app.services.telemetry_service import TelemetryService
from app.storage.academic_store import AcademicStore, new_id
from app.storage.conversation_store import utc_now

QUESTIONS_FILE = Path(__file__).resolve().parents[1] / "knowledge" / "micro_questions.json"
NOTICE = ("Pregunta opcional: puedes omitirla. Tu respuesta se guarda para acompañarte mejor; "
          "no la uses para compartir datos personales.")


class MicroQuestionService:
    def __init__(self, retriever: KnowledgeRetriever, store: AcademicStore, telemetry: TelemetryService,
                 config: AcademicConfig, clock: Callable[[], datetime], questions_file: Optional[Path] = None):
        self.retriever, self.store, self.telemetry, self.config, self.clock = retriever, store, telemetry, config, clock
        data = json.loads(Path(questions_file or QUESTIONS_FILE).read_text(encoding="utf-8-sig"))
        self.questions: List[dict] = data["questions"]

    # ------------------------------------------------------------ elegibilidad
    def _reference(self) -> Optional[date]:
        raw = self.config.micro_question_reference_date
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    def period_for(self, as_of: date) -> Optional[int]:
        """Índice del periodo en curso (1 = primer periodo completo desde la fecha de referencia) o None."""
        ref = self._reference()
        if ref is None:
            return None
        days = (as_of - ref).days
        return days // self.config.micro_question_interval_days if days >= 0 else None

    def question_for(self, period_index: int) -> dict:
        return self.questions[(period_index - 1) % len(self.questions)]

    def state(self, student_id: str, course_id: str, as_of: Optional[date] = None) -> MicroQuestionState:
        self._require_course(course_id)
        as_of = as_of or self.clock().astimezone(timezone.utc).date()

        def not_due(reason: str, period: Optional[int] = None) -> MicroQuestionState:
            return MicroQuestionState(student_id=student_id, course_id=course_id, due=False, reason=reason,
                                      as_of=as_of, period_index=period, notice=NOTICE)

        if self._reference() is None:
            return not_due("sin_fecha_de_referencia_configurada")
        period = self.period_for(as_of)
        if period is None:
            return not_due("antes_de_la_fecha_de_referencia")
        if period < 1:
            return not_due("aun_no_termina_el_primer_periodo", period)
        if self.config.micro_question_require_quiz and not self.store.has_completed_quiz(student_id, course_id):
            return not_due("sin_quiz_completado", period)
        if self.store.micro_period_handled(student_id, course_id, period):
            return not_due("ya_respondida_u_omitida_en_este_periodo", period)
        q = self.question_for(period)
        return MicroQuestionState(
            student_id=student_id, course_id=course_id, due=True, reason="corresponde_en_este_periodo", as_of=as_of,
            period_index=period, notice=NOTICE,
            question=MicroQuestionPublic(question_id=q["question_id"], period_index=period, prompt=q["prompt"]),
        )

    # --------------------------------------------------------------- respuesta
    def answer(self, req: MicroAnswerRequest, *, idem_scope: Optional[str] = None) -> MicroAnswerResponse:
        self._require_course(req.course_id)
        as_of = req.as_of or self.clock().astimezone(timezone.utc).date()
        period = self.period_for(as_of)
        if period is None or period < 1:
            raise AppError(409, "micro_question_not_due", "No hay una micro-pregunta que corresponda en esta fecha.")
        if req.period_index != period:
            raise AppError(422, "period_not_current",
                           f"Solo se puede responder la micro-pregunta del periodo en curso ({period}).")
        expected = self.question_for(period)
        if req.question_id != expected["question_id"]:
            raise AppError(422, "question_period_mismatch", "Esa pregunta no corresponde a este periodo.")
        if self.config.micro_question_require_quiz and not self.store.has_completed_quiz(req.student_id, req.course_id):
            raise AppError(409, "micro_question_not_due", "La micro-pregunta se habilita después de completar un quiz.")
        if req.skipped and req.answer_text:
            raise AppError(422, "skipped_with_text", "Una pregunta omitida no puede llevar texto de respuesta.")
        if not req.skipped and not req.answer_text:
            raise AppError(422, "answer_required_or_skipped",
                           "Escribe una respuesta o marca la pregunta como omitida (skipped=true).")
        if self.store.get_micro_response(req.student_id, req.course_id, req.question_id, period) \
                or self.store.micro_period_handled(req.student_id, req.course_id, period):
            raise AppError(409, "micro_question_already_answered", "Ya respondiste u omitiste la micro-pregunta de este periodo.")

        interpretation = None if req.skipped else classify(req.answer_text)
        status = "skipped" if req.skipped else "answered"
        record = {
            "response_id": new_id(), "student_id": req.student_id, "course_id": req.course_id,
            "question_id": req.question_id, "period_index": period, "status": status,
            "answer_text": req.answer_text, "created_at": utc_now(),
        }
        event = None
        if interpretation is not None:
            record.update(category=interpretation.category.value, classification_method="reglas_conservadoras",
                          confidence=interpretation.confidence, matched_cues=interpretation.matched_cues)
            event = self.telemetry.event_spec(
                TelemetryEventType.MICRO_QUESTION_ANSWERED, "DEMO",
                {"question_id": req.question_id, "period_index": period,
                 "category": interpretation.category.value, "confidence": interpretation.confidence},
                student_id=req.student_id, course_id=req.course_id, idem_scope=idem_scope)
        if not self.store.insert_micro_response(record, event):
            raise AppError(409, "micro_question_already_answered", "Ya respondiste u omitiste la micro-pregunta de este periodo.")

        return MicroAnswerResponse(
            response_id=record["response_id"], student_id=req.student_id, course_id=req.course_id,
            question_id=req.question_id, period_index=period, status=status, expressed_text=req.answer_text,
            interpretation=None if interpretation is None else MicroInterpretation(
                category=interpretation.category, candidate_categories=interpretation.candidates,
                confidence=interpretation.confidence, matched_cues=interpretation.matched_cues, disclaimer=DISCLAIMER),
            notice="¡Gracias! Tu respuesta es opcional y no cambia tus resultados del curso." if status == "answered"
            else "Sin problema, omitimos la pregunta por este periodo.",
            telemetry_notice=TELEMETRY_NOTICE,
        )

    def _require_course(self, course_id: str) -> None:
        if self.retriever.get_course(course_id) is None and not self.retriever.list_lessons(course_id):
            raise CourseNotFound(course_id)
