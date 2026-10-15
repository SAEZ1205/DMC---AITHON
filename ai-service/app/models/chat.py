from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

# Identificadores: solo caracteres seguros y longitud acotada.
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"
MAX_MESSAGE_LENGTH = 1000


class GuardianInfo(BaseModel):
    """Veredicto de pertinencia. `method` dice QUÉ lo decidió: heuristic (reglas), kimi (LLM) o evidence (se halló evidencia)."""
    label: str = Field(description="relevant | not_relevant | uncertain")
    method: str = Field(description="heuristic | kimi | evidence")
    is_llm: bool = Field(description="True solo si lo decidió un modelo de lenguaje real")
    reasons: List[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    student_id: str = Field(
        default="demo-student-001", pattern=ID_PATTERN,
        description="Identificador SINTÉTICO del estudiante (no es una credencial ni autentica a nadie)",
    )
    course_id: str = Field(default="sql-demo", pattern=ID_PATTERN, description="Identificador del curso piloto")
    lesson_id: Optional[str] = Field(
        default=None, pattern=ID_PATTERN,
        description="Clase/grabación a consultar. Si se omite, se busca en todas las clases del curso",
    )
    session_id: Optional[str] = Field(
        default=None, pattern=ID_PATTERN,
        description="Identificador de sesión para multi-turn; null inicia una conversación nueva",
    )
    message: str = Field(
        ..., min_length=1, max_length=MAX_MESSAGE_LENGTH,
        description="Pregunta o mensaje del estudiante",
    )

    @field_validator("message", mode="before")
    @classmethod
    def _strip_message(cls, value):
        return value.strip() if isinstance(value, str) else value

class CitationRef(BaseModel):
    reference: str = Field(..., description="Identificador de referencia bibliográfica o módulo")
    topic: str = Field(..., description="Tema o concepto referenciado")
    title: str = Field(..., description="Título legible del contenido")
    # Campos opcionales: solo se completan cuando la cita proviene de una grabación.
    lesson_id: Optional[str] = Field(default=None, description="Clase/grabación de origen")
    start_seconds: Optional[float] = Field(default=None, description="Segundo inicial en la grabación")
    end_seconds: Optional[float] = Field(default=None, description="Segundo final en la grabación")
    timestamp: Optional[str] = Field(default=None, description="Rango legible MM:SS–MM:SS")
    seek_seconds: Optional[float] = Field(
        default=None, description="Segundo exacto al que debe saltar el reproductor (renglón más relevante del segmento)")

class ChatResponse(BaseModel):
    response: str = Field(..., description="Respuesta del tutor socrático")
    session_id: str = Field(..., description="Identificador de sesión")
    student_id: str = Field(..., description="Identificador del estudiante")
    course_id: str = Field(..., description="Identificador del curso")
    mode: str = Field(..., description="Modo de ejecución: DEMO o LIVE")
    references: List[CitationRef] = Field(default_factory=list, description="Referencias a materiales del curso")
    topic_detected: Optional[str] = Field(default=None, description="Tema académico detectado")
    telemetry_event_id: Optional[str] = Field(default=None, description="ID del evento estructurado de telemetría registrado")
    disclaimer: str = Field(
        default="DMC RescuePath AI — Asistente de orientación académica preventiva. No reemplaza al docente titular.",
        description="Aviso institucional y pedagógico"
    )
    lesson_id: Optional[str] = Field(default=None, description="Clase consultada en la sesión (null = todo el curso)")
    response_type: str = Field(
        default="grounded",
        description="grounded | no_evidence | exhausted | needs_context | greeting | closing",
    )
    grounded: bool = Field(default=False, description="True si la respuesta se apoya en segmentos citados")
    follow_up_question: Optional[str] = Field(default=None, description="Pregunta socrática de seguimiento")
    content_notice: str = Field(default="", description="Aviso sobre el carácter demostrativo del contenido")
    provider: str = Field(default="demo", description="Proveedor que generó realmente el texto: demo | kimi")
    model: Optional[str] = Field(default=None, description="Modelo que generó el texto (solo en LIVE)")
    fallback_used: bool = Field(default=False, description="True si LIVE falló y se respondió con el modo DEMO")
    provider_notice: Optional[str] = Field(
        default=None, description="Explicación visible cuando el texto no lo generó el proveedor configurado"
    )
    telemetry_notice: str = Field(default="", description="Aviso al estudiante de que su actividad genera métricas")
    guardian: Optional[GuardianInfo] = Field(default=None, description="Veredicto de pertinencia de la pregunta")
    answer_origin: str = Field(default="demo", description="live (generación nueva) | cached (guardada y verificada antes) | demo | demo_fallback")
    cached_at: Optional[str] = Field(default=None, description="Si la respuesta salió de la caché: cuándo se generó originalmente")
    timing: Optional[Dict[str, Any]] = Field(
        default=None, description="Latencia por etapa en ms y contadores (solo números y un estado; nunca texto de la pregunta ni de la clase)")

class CourseTopicInfo(BaseModel):
    id: str
    title: str
    summary: str
    reference: str

class CourseInfo(BaseModel):
    course_id: str
    course_name: str
    description: str
    is_official_dmc: bool
    disclaimer: str
    topics: List[CourseTopicInfo]
