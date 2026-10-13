"""
Orquestación del asistente: validación -> sesión -> intención -> recuperación -> redacción -> persistencia.

Principios
  * Toda la evidencia sale de `KnowledgeRetriever.retrieve_segments` con el `course_id` y
    el `lesson_id` de la sesión. Las citas se construyen aquí a partir de ESA evidencia;
    el proveedor de lenguaje nunca las crea ni las modifica.
  * Sin evidencia suficiente no se cita nada.
  * La sesión pertenece a un único (estudiante, curso, clase); cualquier otra combinación
    se rechaza antes de leer mensajes.
"""
import logging
import re
from dataclasses import dataclass, field, replace
from typing import List, Optional, Set, Tuple

from app.knowledge.retriever import FRAME_STEMS, KnowledgeRetriever
from app.knowledge.text_utils import normalize_text, stem
from app.llm.base import (
    ChatTurn, FollowUpMode, LLMProvider, ResponseKind, TutorContext, TutorReply,
)
from app.llm.errors import LLMError
from app.models.api import LessonInfo
from app.models.chat import ChatRequest, ChatResponse, GuardianInfo
from app.models.events import TELEMETRY_NOTICE, TelemetryEventType
from app.models.transcript import SegmentMatch, format_timestamp
from app.services.errors import (
    AppError, CourseNotFound, LessonNotFound, SessionMismatch, SessionNotFound,
)
from app.privacy import mask_pii
from app.services.guardian import Guardian, GuardianVerdict, Relevance
from app.services.intents import MessageAnalysis, analyze_message
from app.services.telemetry_service import TelemetryService
from app.storage.conversation_store import ConversationStore, SessionRecord

logger = logging.getLogger(__name__)

HISTORY_WINDOW_MESSAGES = 6      # mensajes previos entregados como contexto (3 turnos)
HISTORY_MESSAGE_MAX_CHARS = 500  # tope por mensaje en el contexto
MAX_CANDIDATES = 10              # segmentos pedidos al recuperador
# Evidencia por respuesta: la define cada proveedor (`max_evidence`, `secondary_score_ratio`).
# DEMO: 2 y 0.85 (un segundo segmento solo si casi empata; el resto queda para "otro ejemplo").

DEMO_NOTICE = (
    "Modo DEMO: el asistente usa transcripciones y material FICTICIOS escritos para el MVP; "
    "no es contenido oficial de DMC Institute."
)


@dataclass
class _Plan:
    kind: ResponseKind
    evidence: List[SegmentMatch] = field(default_factory=list)
    topic_id: Optional[str] = None
    label: Optional[str] = None
    mode: FollowUpMode = FollowUpMode.NONE
    focus_query: Optional[str] = None
    update_focus: bool = False


def _segment_key(match: SegmentMatch) -> Tuple[str, float]:
    return (match.segment.lesson_id, match.segment.start_seconds)


class TutorService:
    def __init__(
        self,
        retriever: KnowledgeRetriever,
        store: ConversationStore,
        provider: LLMProvider,
        fallback_provider: Optional[LLMProvider] = None,
        telemetry: Optional[TelemetryService] = None,
        guardian: Optional[Guardian] = None,
        privacy=None,
    ):
        self.retriever = retriever
        self.store = store
        self.provider = provider
        # Proveedor DEMO al que se degrada si LIVE falla de forma temporal (None = no degradar, devolver el error).
        self.fallback_provider = fallback_provider
        self.telemetry = telemetry
        self.guardian = guardian
        self.privacy = privacy   # PrivacyFilter con los nombres aprendidos de la clase (o None)

    # ------------------------------------------------------------ catálogo
    def list_courses(self):
        return self.retriever.list_courses()

    def list_lessons(self, course_id: str) -> List[LessonInfo]:
        self._require_course(course_id)
        return [
            LessonInfo(
                course_id=l.course_id,
                lesson_id=l.lesson_id,
                title=l.title,
                source_format=l.source_format,
                segment_count=len(l.segments),
                duration_seconds=l.duration_seconds,
                duration_label=format_timestamp(l.duration_seconds),
                is_official_dmc=l.is_official_dmc,
                disclaimer=l.disclaimer,
            )
            for l in self.retriever.list_lessons(course_id)
        ]

    # ---------------------------------------------------------------- chat
    def chat(self, req: ChatRequest, idem_scope: Optional[str] = None) -> ChatResponse:
        course_name = self._require_course(req.course_id)
        if req.lesson_id is not None and self.retriever.get_lesson(req.course_id, req.lesson_id) is None:
            raise LessonNotFound(req.course_id, req.lesson_id)

        session = self._resolve_session(req)
        is_new = session is None
        session_id = self.store.new_session_id() if is_new else session.session_id
        lesson_id = req.lesson_id if is_new else session.lesson_id

        # Privacidad: correos, teléfonos, documentos y secretos se enmascaran ANTES de procesar, guardar o enviar a un modelo.
        message = mask_pii(req.message)
        if self.privacy is not None:
            message = self.privacy.mask(message)[0]
        analysis = analyze_message(message)
        cited = set() if is_new else self.store.get_cited_segments(session_id)

        verdict = self.guardian.assess(message, req.course_id, lesson_id) if (self.guardian and analysis.user_intent == "question") else None
        if verdict is not None and verdict.label is Relevance.NOT_RELEVANT:
            plan = _Plan(ResponseKind.OUT_OF_SCOPE)          # no se recupera ni se llama a ningún modelo
        else:
            plan = self._plan(req.course_id, lesson_id, analysis, session, cited, message)
            if verdict is not None and verdict.label is Relevance.UNCERTAIN and plan.kind is ResponseKind.GROUNDED:
                verdict = GuardianVerdict(Relevance.RELEVANT, "evidence", ("Se encontró evidencia en la transcripción de la clase.",),
                                          verdict.matched_terms)

        history_records = [] if is_new else self.store.get_recent_messages(session_id, HISTORY_WINDOW_MESSAGES)
        turn_number = 1 if is_new else self.store.count_turns(session_id) + 1
        lesson = self.retriever.get_lesson(req.course_id, lesson_id) if lesson_id else None

        context = TutorContext(
            kind=plan.kind,
            student_message=message,
            course_name=course_name,
            lesson_title=lesson.title if lesson else None,
            topic_label=plan.label,
            evidence=plan.evidence,
            history=[ChatTurn(r.role, r.content[:HISTORY_MESSAGE_MAX_CHARS]) for r in history_records],
            turn_number=turn_number,
            follow_up_mode=plan.mode,
            user_intent=analysis.user_intent,
        )
        reply = self._generate(context)

        # Las citas salen SOLO de los segmentos recuperados que la respuesta usa realmente, y las construye
        # el backend: ningún proveedor puede crear referencias propias.
        used = self._used_evidence(plan, reply)
        if plan.kind is ResponseKind.GROUNDED and not used:
            # El proveedor no pudo apoyarse en ningún segmento: se informa como falta de evidencia.
            plan.kind, plan.update_focus = ResponseKind.NO_EVIDENCE, False
        citations = self.retriever.build_segment_citations(used) if plan.kind is ResponseKind.GROUNDED else []

        self.store.record_turn(
            session_id=session_id,
            student_message=message,
            student_intent=analysis.user_intent,
            tutor_message=reply.text,
            tutor_intent=plan.kind.value,
            topic=plan.topic_id,
            citations=[c.model_dump() for c in citations],
            update_focus=plan.update_focus,
            focus_topic=plan.topic_id,
            focus_query=plan.focus_query,
            new_session=(req.student_id, req.course_id, lesson_id) if is_new else None,
            provider=f"{reply.provider}:{reply.model}" if reply.model else reply.provider,
            fallback_reason=reply.fallback_reason,
        )
        logger.info(
            "chat session=%s turn=%d user_intent=%s response=%s cited=%d provider=%s fallback=%s",
            session_id, turn_number, analysis.user_intent, plan.kind.value, len(citations),
            reply.provider, bool(reply.fallback_reason),
        )
        self._record_telemetry(req, message, session_id, lesson_id, analysis, plan, reply, used, idem_scope, verdict)

        return ChatResponse(
            response=reply.text,
            session_id=session_id,
            student_id=req.student_id,
            course_id=req.course_id,
            lesson_id=lesson_id,
            mode=reply.mode,                       # lo que generó el texto, no lo que estaba configurado
            references=citations,
            topic_detected=plan.topic_id,
            response_type=plan.kind.value,
            grounded=plan.kind is ResponseKind.GROUNDED,
            follow_up_question=reply.follow_up_question,
            content_notice=self._notice(req.course_id, lesson_id),
            provider=reply.provider,
            model=reply.model,
            fallback_used=reply.fallback_reason is not None,
            provider_notice=self._provider_notice(reply),
            telemetry_notice=TELEMETRY_NOTICE,
            guardian=None if verdict is None else GuardianInfo(
                label=verdict.label.value, method=verdict.method, is_llm=verdict.is_llm, reasons=list(verdict.reasons)),
        )

    def _record_telemetry(self, req, message, session_id, lesson_id, analysis, plan, reply, used, idem_scope, verdict=None) -> None:
        """Eventos del chat que son responsabilidad de este módulo. Nunca incluyen el texto del mensaje."""
        if self.telemetry is None:
            return
        ids = dict(student_id=req.student_id, course_id=req.course_id, lesson_id=lesson_id,
                   session_id=session_id, idem_scope=idem_scope, best_effort=True)
        label = self._topic_label(req.course_id, plan.topic_id, fallback=None) if plan.topic_id else None
        self.telemetry.record(TelemetryEventType.CHAT_QUESTION, reply.mode, {
            "user_intent": analysis.user_intent, "response_type": plan.kind.value,
            "grounded": plan.kind is ResponseKind.GROUNDED, "topic": label, "topic_id": plan.topic_id,
            "message_length": len(message),
            **({"guardian": verdict.label.value, "guardian_method": verdict.method} if verdict else {})}, **ids)
        if plan.kind is ResponseKind.GROUNDED and used:
            self.telemetry.record(TelemetryEventType.CONTENT_RETRIEVED, reply.mode, {
                "segments": [{"lesson_id": m.segment.lesson_id, "start_seconds": m.segment.start_seconds,
                              "end_seconds": m.segment.end_seconds} for m in used],
                "source": "chat"}, **ids)
            if analysis.follow_up_mode is FollowUpMode.REPEAT and label:
                self.telemetry.record(TelemetryEventType.TOPIC_DIFFICULTY, reply.mode,
                                      {"topic": label, "signal": "repeat_request"}, **ids)

    # ------------------------------------------------------------ internos
    def _generate(self, context: TutorContext) -> TutorReply:
        """Llama al proveedor; si LIVE falla de forma temporal degrada a DEMO SIN ocultarlo."""
        try:
            return self.provider.generate(context)
        except LLMError as exc:
            logger.warning(
                "Fallo del proveedor %s: code=%s upstream=%s detail=%s",
                self.provider.name, exc.code, exc.upstream_status, exc.detail,
            )
            fallback = self.fallback_provider
            if exc.fallback_allowed and fallback is not None:
                # DEMO aplica SUS reglas de selección (la evidencia vino ordenada por relevancia) y declara
                # explícitamente cuántos segmentos usa, para que solo esos se citen.
                best = context.evidence[0].score if context.evidence else 0.0
                chosen = [m for i, m in enumerate(context.evidence) if i == 0 or m.score >= best * fallback.secondary_score_ratio]
                chosen = chosen[: fallback.max_evidence]
                demo_ctx = replace(context, evidence=chosen)
                return replace(
                    fallback.generate(demo_ctx),
                    cited_indices=tuple(range(len(chosen))),
                    fallback_reason=f"{exc.code}: {exc.public_message}",
                )
            raise AppError(
                exc.http_status, exc.code, exc.public_message,
                details={"provider": self.provider.name, "upstream_status": exc.upstream_status},
            ) from exc

    @staticmethod
    def _used_evidence(plan: "_Plan", reply: TutorReply) -> List[SegmentMatch]:
        if reply.cited_indices is None:
            return list(plan.evidence)
        picked: List[int] = []
        for i in reply.cited_indices:  # defensa adicional: solo posiciones válidas, sin duplicados
            if isinstance(i, int) and 0 <= i < len(plan.evidence) and i not in picked:
                picked.append(i)
        return [plan.evidence[i] for i in picked]

    def _provider_notice(self, reply: TutorReply) -> Optional[str]:
        if reply.fallback_reason:
            return (
                f"Esta respuesta la generó el modo DEMO porque el proveedor LIVE ({self.provider.name}) "
                f"no pudo responder ({reply.fallback_reason})."
            )
        return None

    def _require_course(self, course_id: str) -> str:
        course = self.retriever.get_course(course_id)
        if course is not None:
            return course.get("course_name", course_id)
        if self.retriever.list_lessons(course_id):
            return course_id
        raise CourseNotFound(course_id)

    def _resolve_session(self, req: ChatRequest) -> Optional[SessionRecord]:
        """Devuelve la sesión existente validada, o None si hay que crear una nueva."""
        if req.session_id is None:
            return None
        session = self.store.get_session(req.session_id)
        if session is None:
            raise SessionNotFound()
        if session.student_id != req.student_id or session.course_id != req.course_id:
            raise SessionMismatch()
        if req.lesson_id is not None and req.lesson_id != session.lesson_id:
            raise SessionMismatch()
        return session

    def _search(self, course_id: str, lesson_id: Optional[str], query: str,
                phrase_text: Optional[str] = None) -> List[SegmentMatch]:
        return self.retriever.retrieve_segments(course_id, query, lesson_id=lesson_id, top_k=MAX_CANDIDATES,
                                                phrase_text=phrase_text)

    def _detect_topic(self, course_id: str, query: str) -> Optional[str]:
        topics = self.retriever.retrieve(course_id, query, top_k=1)
        return topics[0]["id"] if topics else None

    def _topic_label(self, course_id: str, topic_id: Optional[str], fallback: str) -> str:
        course = self.retriever.get_course(course_id) or {}
        for topic in course.get("topics", []):
            if topic["id"] == topic_id:
                return re.split(r"\s[—–-]\s", topic["title"], maxsplit=1)[0]
        if fallback:      # sin ficha de tema: se usa lo que escribió el estudiante, sin palabras "marco" como "sirve"
            kept = [w for w in fallback.split() if stem(normalize_text(w)) not in FRAME_STEMS]
            return " ".join(kept) or fallback
        return fallback

    def _plan(
        self,
        course_id: str,
        lesson_id: Optional[str],
        analysis: MessageAnalysis,
        session: Optional[SessionRecord],
        cited: Set[Tuple[str, float]],
        message: str = "",
    ) -> _Plan:
        if analysis.user_intent == "greeting":
            return _Plan(ResponseKind.GREETING)
        if analysis.user_intent == "closing":
            return _Plan(ResponseKind.CLOSING)

        previous_query = session.current_query if session else None
        query = analysis.query
        reuse_previous = False

        if analysis.user_intent == "follow_up":
            if not previous_query:
                return _Plan(ResponseKind.NEEDS_CONTEXT)
            query, reuse_previous = previous_query, True

        candidates = self._search(course_id, lesson_id, query, None if reuse_previous else message)
        if not candidates and analysis.refers_to_previous and previous_query and not reuse_previous:
            # "¿Y qué pasa si no uso eso?": sin términos útiles propios, se interpreta con el tema previo.
            query, reuse_previous = previous_query, True
            candidates = self._search(course_id, lesson_id, query)

        topic_id = session.current_topic if (reuse_previous and session) else self._detect_topic(course_id, query)
        label = self._topic_label(course_id, topic_id, fallback=query)

        if not candidates:
            return _Plan(ResponseKind.NO_EVIDENCE, topic_id=topic_id, label=label)

        mode = analysis.follow_up_mode
        if mode is FollowUpMode.MORE:
            candidates = [m for m in candidates if _segment_key(m) not in cited]
            if not candidates:
                return _Plan(ResponseKind.EXHAUSTED, topic_id=topic_id, label=label, mode=mode)

        evidence = [candidates[0]] + [
            m for m in candidates[1:] if m.score >= candidates[0].score * self.provider.secondary_score_ratio
        ][: self.provider.max_evidence - 1]
        return _Plan(
            ResponseKind.GROUNDED,
            evidence=evidence,
            topic_id=topic_id,
            label=label,
            mode=mode,
            focus_query=query,
            update_focus=not reuse_previous,
        )

    def _notice(self, course_id: str, lesson_id: Optional[str]) -> str:
        lessons = (
            [self.retriever.get_lesson(course_id, lesson_id)] if lesson_id else self.retriever.list_lessons(course_id)
        )
        all_official = bool(lessons) and all(l is not None and l.is_official_dmc for l in lessons)
        return "" if all_official else DEMO_NOTICE
