"""
Telemetría del asistente: registro de eventos (catálogo cerrado), consulta y agregación.

Alcance deliberadamente acotado: cuenta lo que ocurre en ESTE servicio. No calcula riesgo ni deserción,
ni asistencia efectiva; eso lo hace otro módulo con las reglas institucionales.
"""
import hashlib
import hmac
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import AcademicConfig
from app.models.events import (
    EVENT_TYPES, TELEMETRY_NOTICE, CountItem, QuizStats, TelemetryEventOut, TelemetryEventsResponse,
    TelemetryEventType, TelemetrySummaryResponse,
)
from app.services.errors import AppError
from app.storage.academic_store import AcademicStore

logger = logging.getLogger(__name__)

PRIVACY_NOTICE = (
    "Datos SINTÉTICOS de demostración. Los estudiantes se muestran con un seudónimo estable (student_ref), "
    "nunca con su identificador original. Con estudiantes reales este endpoint exige autenticación y autorización."
)
METHODOLOGY_NOTICE = (
    "Conteos de actividad registrada por el asistente (DEMO). No son una probabilidad de deserción, no miden "
    "asistencia institucional y no sustituyen las reglas académicas de DMC."
)
MAX_EVENT_PAGE = 500


def parse_iso(value: Optional[str], field: str) -> Optional[str]:
    """Valida un instante ISO-8601 y lo normaliza a UTC (las marcas guardadas están en UTC)."""
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppError(422, "invalid_datetime", f"'{field}' debe ser una fecha-hora ISO-8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


class TelemetryService:
    def __init__(self, store: AcademicStore, config: AcademicConfig):
        self.store = store
        self.config = config

    # ------------------------------------------------------------- registro
    @staticmethod
    def idem_key(scope: Optional[str], event_type: TelemetryEventType, seq: int = 0) -> Optional[str]:
        """Clave por evento derivada de la clave idempotente de la petición (None si no hubo clave)."""
        return f"{scope}|{event_type.value}|{seq}" if scope else None

    def event_spec(self, event_type: TelemetryEventType, mode: str, payload: Dict[str, Any], *, idem_scope=None,
                   seq: int = 0, **ids) -> Dict[str, Any]:
        """Argumentos listos para `AcademicStore.insert_event_tx` (para escribir el evento dentro de otra transacción)."""
        return dict(event_type=event_type.value, mode=mode, payload=payload,
                    idempotency_key=self.idem_key(idem_scope, event_type, seq), **ids)

    def record(self, event_type: TelemetryEventType, mode: str, payload: Dict[str, Any], *, idem_scope=None,
               seq: int = 0, best_effort: bool = False, **ids) -> Optional[str]:
        """Registra un evento. Con `best_effort`, un fallo de almacenamiento se registra en el log y no rompe la petición."""
        spec = self.event_spec(event_type, mode, payload, idem_scope=idem_scope, seq=seq, **ids)
        try:
            return self.store.record_event(**spec)
        except sqlite3.Error:
            if not best_effort:
                raise
            logger.warning("No se pudo registrar el evento de telemetría %s", event_type.value)
            return None

    # ------------------------------------------------------------ privacidad
    def pseudonym(self, student_id: Optional[str]) -> Optional[str]:
        if not student_id:
            return None
        digest = hmac.new(self.config.telemetry_pseudonym_salt.encode(), student_id.encode(), hashlib.sha256)
        return "est_" + digest.hexdigest()[:12]

    def check_token(self, presented: Optional[str]) -> None:
        """Protege la consulta de eventos individuales: deshabilitada si no hay token configurado."""
        expected = self.config.telemetry_api_token
        if not expected:
            raise AppError(403, "telemetry_events_disabled",
                           "La consulta de eventos está deshabilitada: configura TELEMETRY_API_TOKEN para habilitarla.")
        if not presented or not hmac.compare_digest(presented.encode(), expected.encode()):
            raise AppError(401, "telemetry_unauthorized", "Token de telemetría ausente o inválido.")

    # -------------------------------------------------------------- consulta
    def list_events(self, *, course_id=None, lesson_id=None, event_type: Optional[str] = None, since=None, until=None,
                    before=None, limit: int = 100) -> TelemetryEventsResponse:
        if event_type is not None and event_type not in EVENT_TYPES:
            raise AppError(422, "invalid_event_type", f"event_type debe ser uno de: {', '.join(EVENT_TYPES)}")
        limit = max(1, min(limit, MAX_EVENT_PAGE))
        rows = self.store.query_events(course_id, lesson_id, event_type, parse_iso(since, "since"),
                                       parse_iso(until, "until"), parse_iso(before, "before"), limit)
        events = [
            TelemetryEventOut(
                event_id=r["event_id"], event_type=r["event_type"], student_ref=self.pseudonym(r["student_id"]),
                course_id=r["course_id"], lesson_id=r["lesson_id"], session_id=r["session_id"], mode=r["mode"],
                payload=r["payload"], timestamp=r["created_at"],
            )
            for r in rows
        ]
        return TelemetryEventsResponse(
            events=events, count=len(events), limit=limit,
            next_before=events[-1].timestamp if len(events) == limit else None,
            privacy_notice=PRIVACY_NOTICE,
        )

    def summary(self, *, course_id=None, lesson_id=None, since=None, until=None) -> TelemetrySummaryResponse:
        rows = self.store.events_for_summary(course_id, lesson_id, parse_iso(since, "since"), parse_iso(until, "until"))
        by_type = Counter(r["event_type"] for r in rows)
        topics = Counter(r["payload"].get("topic") for r in rows if r["event_type"] == "chat_question" and r["payload"].get("topic"))
        difficulty = Counter(r["payload"].get("topic") for r in rows if r["event_type"] == "topic_difficulty" and r["payload"].get("topic"))
        lessons = Counter()
        for r in rows:
            if r["event_type"] == "content_retrieved":
                for lesson in {s.get("lesson_id") for s in r["payload"].get("segments", []) if s.get("lesson_id")}:
                    lessons[lesson] += 1
        completed = [r for r in rows if r["event_type"] == "quiz_completed"]
        scores = [r["payload"]["score_percent"] for r in completed if isinstance(r["payload"].get("score_percent"), (int, float))]
        passed = sum(1 for r in completed if r["payload"].get("passed") is True)
        categories = Counter(r["payload"].get("category") for r in rows
                             if r["event_type"] == "micro_question_answered" and r["payload"].get("category"))

        def items(counter: Counter, limit: Optional[int] = None) -> List[CountItem]:
            ordered = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
            return [CountItem(key=str(k), count=v) for k, v in (ordered[:limit] if limit else ordered)]

        return TelemetrySummaryResponse(
            generated_at=datetime.now(timezone.utc).isoformat(),
            filters={"course_id": course_id, "lesson_id": lesson_id, "since": since, "until": until},
            total_events=len(rows),
            distinct_students=len({r["student_id"] for r in rows if r["student_id"]}),
            events_by_type=[CountItem(key=t, count=by_type.get(t, 0)) for t in EVENT_TYPES],
            top_topics=items(topics, 10),
            topics_with_difficulty_signals=items(difficulty, 10),
            most_retrieved_lessons=items(lessons, 10),
            quiz=QuizStats(
                started=by_type.get("quiz_started", 0), completed=len(completed),
                average_score_percent=round(sum(scores) / len(scores), 1) if scores else None,
                passed=passed, pass_rate_percent=round(100 * passed / len(completed), 1) if completed else None,
            ),
            missed_content_requests=by_type.get("missed_content_requested", 0),
            summary_requests=by_type.get("lesson_summary_requested", 0),
            micro_question_categories=items(categories),
            mode_breakdown=items(Counter(r["mode"] for r in rows)),
            methodology_notice=METHODOLOGY_NOTICE,
        )


NOTICE = TELEMETRY_NOTICE
