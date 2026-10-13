from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class LessonInfo(BaseModel):
    """Metadatos básicos de una clase con transcripción."""
    course_id: str
    lesson_id: str
    title: str
    source_format: str = Field(description="Formato de la transcripción de origen: vtt o json")
    segment_count: int
    duration_seconds: float
    duration_label: str = Field(description="Duración legible (MM:SS)")
    is_official_dmc: bool
    disclaimer: str


class StorageHealth(BaseModel):
    status: str = Field(description="ok o error")
    engine: str = "sqlite"
    database_file: str
    sessions: Optional[int] = None
    messages: Optional[int] = None
    error: Optional[str] = None


class KnowledgeHealth(BaseModel):
    courses: int
    lessons: int
    load_errors: int


class LLMHealth(BaseModel):
    active_provider: str = Field(description="Proveedor que atiende las respuestas: demo | kimi")
    configured_provider: str
    mode: str = Field(description="DEMO | LIVE")
    selection_reason: str = Field(description="Por qué se eligió este proveedor")
    model: Optional[str] = Field(default=None, description="Modelo configurado (solo LIVE)")
    live_authorized: bool
    api_key_configured: bool = Field(description="Solo indica si hay una clave; nunca se expone")
    fallback_to_demo: bool
    live_calls_used: Optional[int] = None
    live_calls_limit: Optional[int] = None


class HealthResponse(BaseModel):
    status: str = Field(description="ok o degraded")
    service: str = "dmc-rescuepath-ai"
    version: str
    mode: str = Field(description="Modo de funcionamiento: DEMO")
    llm_provider: str = Field(description="Proveedor activo")
    llm_provider_configured: str = Field(description="Proveedor pedido en la configuración")
    llm: Optional[LLMHealth] = None
    real_lesson: Optional[Dict[str, Any]] = Field(default=None, description="Estado de la clase real (sin rutas ni texto de la clase)")
    storage: StorageHealth
    knowledge: KnowledgeHealth
    timestamp: str


class ErrorDetail(BaseModel):
    code: str = Field(description="Código estable para clientes, p. ej. session_mismatch")
    message: str = Field(description="Mensaje legible para el usuario")
    details: Optional[Any] = Field(default=None, description="Información adicional no sensible")


class ErrorResponse(BaseModel):
    """Formato único de error de la API."""
    error: ErrorDetail
