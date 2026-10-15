"""Modelo de datos (sección 9 de la especificación). Las fechas se guardan sin zona horaria, en hora de Lima."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "user"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(index=True)
    role: str = Field(index=True)  # alumno, tutor, asistente_academico, docente, coordinador


class Course(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    start_date: date
    end_date: date
    total_sessions: int
    schedule: str
    teacher_id: Optional[int] = None
    academic_assistant_id: Optional[int] = None
    attendance_threshold: float = 0.80
    live_min_ratio: float = 0.70
    recovery_days: int = 7
    key_coverage_min: float = 0.90
    quiz_pass_score: int = 3
    level2_role: str = "asistente_academico"


class Enrollment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    course_id: int = Field(index=True)
    status: str = "activo"
    grade: Optional[float] = None
    project_status: str = "pendiente"  # pendiente, aprobado, desaprobado


class ClassSession(SQLModel, table=True):
    __tablename__ = "session"
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(index=True)
    number: int
    title: str = ""
    starts_at: NaiveDatetime
    ends_at: NaiveDatetime
    video_path: Optional[str] = None
    video_url: Optional[str] = None
    transcript_path: Optional[str] = None
    approx_times: bool = False  # transcripción sin marcas de tiempo: minutos aproximados
    real_content: bool = False  # contenido de una sesión real (no de muestra)
    processed: bool = False


class TranscriptSegment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    start: float
    end: float
    speaker: str = ""
    text: str


class Chapter(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    start: float
    end: float
    title: str
    summary: str = ""
    is_key: bool = False
    validated: bool = False
    slide_from: Optional[int] = None
    slide_to: Optional[int] = None
    source: str = "texttiling"  # slides, texttiling, docente
    logistics: bool = False
    key_sentences_json: str = "[]"


class MaterialUnit(SQLModel, table=True):
    __tablename__ = "material_unit"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    source_file: str  # ruta relativa dentro de materials/
    category: str = ""  # Descubre, Explora, Aplica…
    kind: str = "slide"  # slide, page, block, code
    unit_no: int
    title: str = ""
    text: str = ""
    is_key: bool = False


class SessionSummary(SQLModel, table=True):
    session_id: int = Field(primary_key=True)
    text: str


class ZoomInterval(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    user_id: int = Field(index=True)
    join_at: NaiveDatetime
    leave_at: NaiveDatetime
    match_confidence: str = "high"


class ZoomChat(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    user_id: int = Field(index=True)
    at: NaiveDatetime
    text: str


class ZoomVoice(SQLModel, table=True):
    """Turnos de voz por alumno y sesión (conteo de bloques del VTT)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    user_id: int = Field(index=True)
    turns: int = 0


class WatchRange(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    session_id: int = Field(index=True)
    start_s: float
    end_s: float
    at: NaiveDatetime


class Heartbeat(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    course_id: int = Field(index=True)
    page: str = ""
    active_seconds: int = 0
    at: NaiveDatetime


class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    session_id: int = Field(index=True)
    role: str  # user, assistant
    text: str
    kind: Optional[str] = None  # contenido, logistica, plataforma (solo preguntas del alumno)
    at: NaiveDatetime
    thread_id: Optional[int] = Field(default=None, index=True)
    level: Optional[int] = None
    meta_json: str = "{}"  # citas, diapositivas y botones de la respuesta del asistente


class ChatThread(SQLModel, table=True):
    __tablename__ = "chat_thread"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    session_id: int = Field(index=True)
    question: str
    chapter_id: Optional[int] = None
    level: int = 1
    max_level: int = 4  # 2 si la pregunta se parece al quiz y el alumno aún no lo aprueba
    attempts: int = 0
    evaluation: Optional[str] = None  # correcta, parcial, incorrecta
    closed: bool = False
    created_at: NaiveDatetime


class QuizItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    question: str
    options_json: str
    answer_idx: int
    explanation: str = ""
    minute: Optional[str] = None  # "mm:ss" donde se explica
    slide: Optional[int] = None
    status: str = "borrador"  # borrador, aprobada, descartada
    source: str = "docente"  # docente, propuesta_ia
    edited_by_teacher: bool = False
    approved_by: Optional[int] = None
    approved_at: Optional[NaiveDatetime] = None


class QuizAttempt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    session_id: int = Field(index=True)
    score: int
    answers_json: str = "{}"
    at: NaiveDatetime


class MicroAnswer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    course_id: int = Field(index=True)
    question: str
    answer: Optional[str] = None  # None = el alumno la omitió
    topic: Optional[str] = None
    sentiment: Optional[str] = None
    summary: Optional[str] = None
    at: NaiveDatetime


class SessionCompliance(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    session_id: int = Field(index=True)
    status: str  # en_vivo, recuperada, por_recuperar, no_cumplida
    completed_at: Optional[NaiveDatetime] = None
    deadline: NaiveDatetime
    live_ratio: float = 0.0
    key_coverage: float = 0.0
    best_quiz: Optional[int] = None


class StudentFeatures(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    course_id: int = Field(index=True)
    session_id: int = Field(index=True)
    live_ratio: float = 0.0
    late_min: Optional[float] = None
    early_leave_min: Optional[float] = None
    reconnects: int = 0
    gap_min: float = 0.0
    chat_msgs: int = 0
    voice_turns: int = 0
    active_min_week: float = 0.0
    days_since_activity: float = 0.0
    study_hour_mode: Optional[int] = None
    key_coverage: float = 0.0
    questions_content: int = 0
    questions_logistics: int = 0
    quiz_score: Optional[int] = None
    quiz_trend: Optional[float] = None
    micro_topic: Optional[str] = None
    micro_sentiment: Optional[str] = None
    pattern_drop: bool = False
    late_shift: bool = False
    mode_shift: bool = False
    projection: Optional[float] = None
    computed_at: NaiveDatetime


class RiskSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    course_id: int = Field(index=True)
    at: NaiveDatetime
    state: str  # activo, en_adaptacion, en_riesgo, inactivo
    reasons_json: str = "[]"
    projection: Optional[float] = None


class Alert(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    course_id: int = Field(index=True)
    level: int = 1
    status: str = "abierta"  # abierta, cerrada
    created_at: NaiveDatetime
    escalated_at: Optional[NaiveDatetime] = None
    closed_at: Optional[NaiveDatetime] = None
    assigned_role: Optional[str] = None
    reasons_json: str = "[]"
    contact_json: str = "[]"
    outcome: Optional[str] = None


class Outbox(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    course_id: Optional[int] = None
    alert_id: Optional[int] = None
    channel: str  # portal, email, whatsapp
    text: str
    at: NaiveDatetime
    read: bool = False


class WithdrawalRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    course_id: int = Field(index=True)
    reason: str
    comment: Optional[str] = None
    offered_json: str = "[]"
    outcome: Optional[str] = None  # opción elegida por el alumno antes de confirmar
    at: NaiveDatetime


class Consent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    version: str
    at: NaiveDatetime
