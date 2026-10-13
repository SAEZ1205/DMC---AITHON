"""Contratos de resúmenes, capítulos, 'lo que te perdiste', quiz y micro-pregunta (Etapa 4)."""
from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.chat import CitationRef, ID_PATTERN

# ================================================================== comunes
STRICT = ConfigDict(extra="forbid")   # campos desconocidos (p. ej. una nota enviada por el navegador) => 422


class SegmentRef(BaseModel):
    segment_id: int
    start_seconds: float
    end_seconds: float
    timestamp: str


# ============================================================ resumen y capítulos
class SummarySection(BaseModel):
    chapter_id: str
    title: str
    start_seconds: float
    end_seconds: float
    timestamp: str
    key_point: str = Field(description="Frase textual de la transcripción que resume el capítulo")
    segment_ids: List[int]
    references: List[CitationRef]


class KeyConcept(BaseModel):
    concept: str
    kind: str = Field(description="tema (ficha del curso) | termino (término destacado en la transcripción)")
    first_seconds: float
    first_minute: str
    mentions: int
    segment_ids: List[int]


class SummaryCacheInfo(BaseModel):
    """Trazabilidad del resumen persistido: cuándo se generó y para qué versión de la transcripción."""
    cached: bool = Field(description="True si se reutilizó el resumen guardado; False si se generó en esta petición")
    generated_at: str
    engine: str = Field(description="extractive (heurístico DEMO) | kimi (LLM por fragmentos)")
    pipeline_version: str
    transcript_sha256_short: str = Field(description="Primeros 12 caracteres de la huella de la transcripción")


class LessonSummaryResponse(BaseModel):
    course_id: str
    lesson_id: str
    lesson_title: str
    duration_seconds: float
    duration_label: str
    mode: str
    engine: str = Field(description="extractive (determinista y verificable)")
    provider: str = "demo"
    overview: str
    sections: List[SummarySection]
    key_concepts: List[KeyConcept]
    segments_used: List[SegmentRef]
    references: List[CitationRef]
    method_note: str
    content_notice: str
    provider_notice: Optional[str] = None
    telemetry_notice: str = ""
    generated_by: str = Field(default="extractive-heuristic", description="Quién redactó el resumen: heurística DEMO o modelo")
    cache: Optional[SummaryCacheInfo] = None


class ChapterInfo(BaseModel):
    chapter_id: str
    title: str
    start_seconds: float
    end_seconds: float
    timestamp: str
    description: str
    segment_ids: List[int]
    concepts: List[str]
    references: List[CitationRef]


class ChaptersResponse(BaseModel):
    course_id: str
    lesson_id: str
    lesson_title: str
    duration_seconds: float
    chapters: List[ChapterInfo]
    method_note: str
    content_notice: str


# ================================================================ lo que te perdiste
class AbsenceInterval(BaseModel):
    model_config = STRICT
    start_seconds: float
    end_seconds: float


class MissedContentRequest(BaseModel):
    model_config = STRICT
    student_id: str = Field(pattern=ID_PATTERN, description="Identificador SINTÉTICO de demostración")
    course_id: str = Field(pattern=ID_PATTERN)
    lesson_id: str = Field(pattern=ID_PATTERN)
    absence_intervals: List[AbsenceInterval] = Field(
        min_length=1, max_length=20,
        description="Intervalos de ausencia SIMULADOS, en segundos desde el inicio de la grabación",
    )


class NormalizedInterval(BaseModel):
    start_seconds: float
    end_seconds: float
    start_label: str
    end_label: str
    duration_seconds: float


class MissedSegment(BaseModel):
    segment_id: int
    start_seconds: float
    end_seconds: float
    timestamp: str
    overlap_start_seconds: float
    overlap_end_seconds: float
    overlap_seconds: float
    partially_missed: bool
    text: str
    concepts: List[str]


class MissedConcept(BaseModel):
    concept: str
    first_seconds: float
    first_minute: str
    segment_ids: List[int]


class MissedPoint(BaseModel):
    timestamp: str
    text: str


class MissedContentResponse(BaseModel):
    student_id: str
    course_id: str
    lesson_id: str
    lesson_title: str
    mode: str
    normalized_intervals: List[NormalizedInterval]
    normalization_notes: List[str]
    total_missed_seconds: float
    missed_segments: List[MissedSegment]
    concepts_missed: List[MissedConcept]
    explanation: str
    key_points: List[MissedPoint]
    references: List[CitationRef]
    attendance_notice: str
    content_notice: str
    telemetry_notice: str = ""


# ======================================================================== quiz
class QuizStartRequest(BaseModel):
    model_config = STRICT
    student_id: str = Field(pattern=ID_PATTERN, description="Identificador SINTÉTICO de demostración")
    course_id: str = Field(pattern=ID_PATTERN)
    lesson_id: str = Field(pattern=ID_PATTERN)
    num_questions: int = Field(default=5, ge=3, le=5)


class QuizOption(BaseModel):
    option_id: str
    text: str


class QuizQuestionPublic(BaseModel):
    """Pregunta tal como se entrega al estudiante: SIN respuesta correcta ni explicación."""
    question_id: str
    position: int
    prompt: str
    options: List[QuizOption]


class QuizStartResponse(BaseModel):
    attempt_id: str
    student_id: str
    course_id: str
    lesson_id: str
    lesson_title: str
    status: str
    num_questions: int
    questions: List[QuizQuestionPublic]
    created_at: str
    expires_at: str
    resumed: bool = Field(description="True si se devolvió un intento abierto en lugar de crear uno nuevo")
    attempts_used: int
    attempts_allowed: int
    pass_threshold_percent: float
    mode: str = "DEMO"
    content_notice: str
    telemetry_notice: str = ""


class QuizAnswerIn(BaseModel):
    model_config = STRICT
    question_id: str = Field(pattern=ID_PATTERN)
    option_id: str = Field(pattern=r"^[a-d]$")


class QuizSubmitRequest(BaseModel):
    model_config = STRICT       # una 'nota' o 'score' enviada por el cliente se rechaza: la nota la calcula el backend
    attempt_id: str = Field(pattern=ID_PATTERN)
    student_id: str = Field(pattern=ID_PATTERN)
    answers: List[QuizAnswerIn] = Field(min_length=0, max_length=5)


class QuestionResult(BaseModel):
    question_id: str
    position: int
    prompt: str
    selected_option_id: Optional[str]
    correct_option_id: str
    is_correct: bool
    explanation: str
    concept: str
    reference: CitationRef


class QuizSubmitResponse(BaseModel):
    attempt_id: str
    student_id: str
    course_id: str
    lesson_id: str
    total_questions: int
    answered_count: int
    correct_count: int
    score_percent: float
    grade_20: float = Field(description="Referencia en escala 0-20 (correct/total*20); no es una regla institucional")
    passed: bool = Field(description="Según el umbral DEMO configurable del quiz")
    pass_threshold_percent: float
    results: List[QuestionResult]
    submitted_at: str
    institutional_notice: str
    content_notice: str
    mode: str = "DEMO"


# ============================================================== micro-pregunta
class MicroCategory(str, Enum):
    DISPONIBILIDAD_TIEMPO = "disponibilidad_tiempo"
    TRABAJO = "trabajo"
    DIFICULTAD_CONTENIDO = "dificultad_contenido"
    RITMO_ACADEMICO = "ritmo_academico"
    SATISFACCION = "satisfaccion"
    OTRO = "otro"
    SIN_INFORMACION_SUFICIENTE = "sin_informacion_suficiente"


class MicroQuestionPublic(BaseModel):
    question_id: str
    period_index: int
    prompt: str
    optional: bool = True


class MicroQuestionState(BaseModel):
    student_id: str
    course_id: str
    due: bool
    reason: str
    as_of: date
    period_index: Optional[int] = None
    question: Optional[MicroQuestionPublic] = None
    notice: str


class MicroAnswerRequest(BaseModel):
    model_config = STRICT
    student_id: str = Field(pattern=ID_PATTERN)
    course_id: str = Field(pattern=ID_PATTERN)
    question_id: str = Field(pattern=ID_PATTERN)
    period_index: int = Field(ge=1)
    answer_text: Optional[str] = Field(default=None, max_length=500, description="Opcional. No incluyas datos personales.")
    skipped: bool = False
    as_of: Optional[date] = Field(default=None, description="Fecha de referencia (solo demostración/pruebas)")

    @field_validator("answer_text", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value


class MicroInterpretation(BaseModel):
    category: MicroCategory
    candidate_categories: List[MicroCategory]
    confidence: str = Field(description="baja | media")
    method: str = "reglas_conservadoras"
    matched_cues: List[str]
    disclaimer: str


class MicroAnswerResponse(BaseModel):
    response_id: str
    student_id: str
    course_id: str
    question_id: str
    period_index: int
    status: str = Field(description="answered | skipped")
    expressed_text: Optional[str] = Field(description="Lo que escribió el estudiante, tal cual")
    interpretation: Optional[MicroInterpretation] = Field(description="Lo que el sistema interpretó (separado del texto)")
    notice: str
    telemetry_notice: str = ""
