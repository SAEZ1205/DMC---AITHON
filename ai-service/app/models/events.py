"""
Telemetría del asistente: catálogo CERRADO de eventos y contratos de consulta.

Solo se registra lo que ocurre en este servicio (preguntas al asistente, resúmenes, quizzes, micro-pregunta).
No hay clasificador de riesgo ni tasas de deserción: esas decisiones son de otro módulo.
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TelemetryEventType(str, Enum):
    CHAT_QUESTION = "chat_question"
    TOPIC_DIFFICULTY = "topic_difficulty"
    CONTENT_RETRIEVED = "content_retrieved"
    LESSON_SUMMARY_REQUESTED = "lesson_summary_requested"
    MISSED_CONTENT_REQUESTED = "missed_content_requested"
    QUIZ_STARTED = "quiz_started"
    QUIZ_COMPLETED = "quiz_completed"
    MICRO_QUESTION_ANSWERED = "micro_question_answered"


# Descripción y campos del payload de cada evento (fuente de la documentación del contrato).
EVENT_CATALOG: Dict[str, Dict[str, Any]] = {
    "chat_question": {
        "description": "El estudiante envió un mensaje al asistente (no se guarda el texto en el evento).",
        "payload": {"user_intent": "question|follow_up|greeting|closing", "response_type": "grounded|no_evidence|exhausted|needs_context|greeting|closing",
                    "grounded": "bool", "topic": "id de tema o null", "message_length": "int"},
    },
    "topic_difficulty": {
        "description": "Señal observable de dificultad con un tema: el estudiante pidió repetir la explicación, o falló una pregunta del quiz.",
        "payload": {"topic": "tema o concepto", "signal": "repeat_request|quiz_incorrect", "question_id": "solo en quiz_incorrect", "attempt_id": "solo en quiz_incorrect"},
    },
    "content_retrieved": {
        "description": "Se recuperaron segmentos de transcripción para responder (qué parte de la clase se consultó).",
        "payload": {"segments": "[{lesson_id, start_seconds, end_seconds}]", "source": "chat"},
    },
    "lesson_summary_requested": {
        "description": "Se pidió el resumen de una clase.",
        "payload": {"engine": "extractive", "section_count": "int", "concept_count": "int"},
    },
    "missed_content_requested": {
        "description": "Se pidió 'lo que te perdiste' con intervalos de ausencia SIMULADOS.",
        "payload": {"interval_count": "int", "total_missed_seconds": "float", "segment_count": "int", "concepts": "[str]"},
    },
    "quiz_started": {
        "description": "Se inició (o retomó) un intento de quiz.",
        "payload": {"attempt_id": "str", "num_questions": "int", "resumed": "bool"},
    },
    "quiz_completed": {
        "description": "Se envió y calificó un intento de quiz. No modifica asistencia ni certificación.",
        "payload": {"attempt_id": "str", "total_questions": "int", "correct_count": "int", "score_percent": "float", "passed": "bool"},
    },
    "micro_question_answered": {
        "description": "El estudiante respondió la micro-pregunta opcional (las omitidas no generan evento).",
        "payload": {"question_id": "str", "period_index": "int", "category": "categoría interpretada por reglas", "confidence": "baja|media"},
    },
}

EVENT_TYPES = tuple(t.value for t in TelemetryEventType)

TELEMETRY_NOTICE = (
    "Este asistente registra tu actividad académica (por ejemplo, temas consultados y resultados de quizzes) "
    "para generar métricas de acompañamiento. No se guarda el contenido de tus mensajes dentro de las métricas."
)


class TelemetryEventOut(BaseModel):
    event_id: str
    event_type: TelemetryEventType
    student_ref: Optional[str] = Field(default=None, description="Seudónimo estable del estudiante (nunca el identificador original)")
    course_id: Optional[str] = None
    lesson_id: Optional[str] = None
    session_id: Optional[str] = None
    mode: str = Field(description="DEMO | LIVE: quién generó la respuesta asociada")
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str


class TelemetryEventsResponse(BaseModel):
    events: List[TelemetryEventOut]
    count: int
    limit: int
    next_before: Optional[str] = Field(default=None, description="Pasar como `before` para la página siguiente")
    data_scope: str = "demo_synthetic"
    privacy_notice: str


class CountItem(BaseModel):
    key: str
    count: int


class QuizStats(BaseModel):
    started: int
    completed: int
    average_score_percent: Optional[float] = None
    passed: int = 0
    pass_rate_percent: Optional[float] = Field(default=None, description="Entre quizzes completados; umbral DEMO configurable")


class TelemetrySummaryResponse(BaseModel):
    generated_at: str
    filters: Dict[str, Optional[str]]
    total_events: int
    distinct_students: int = Field(description="Conteo; no se devuelven identificadores")
    events_by_type: List[CountItem]
    top_topics: List[CountItem] = Field(description="Temas más consultados en el chat (chat_question con tema detectado)")
    topics_with_difficulty_signals: List[CountItem] = Field(description="Temas con señales observables de dificultad (topic_difficulty)")
    most_retrieved_lessons: List[CountItem]
    quiz: QuizStats
    missed_content_requests: int
    summary_requests: int
    micro_question_categories: List[CountItem]
    mode_breakdown: List[CountItem]
    data_scope: str = "demo_synthetic"
    is_demo_metrics: bool = True
    methodology_notice: str
