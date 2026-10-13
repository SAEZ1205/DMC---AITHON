from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class TelemetryEvent(BaseModel):
    """
    Evento estructurado de telemetría académica.
    Distingue entre evento observado, señal interpretada y nivel de confianza.
    """
    event_id: str = Field(..., description="Identificador único del evento")
    student_id: str = Field(..., description="Identificador del alumno")
    session_id: str = Field(..., description="Identificador de la sesión conversacional")
    course_id: str = Field(..., description="Identificador del curso")
    topic: str = Field(..., description="Tema académico principal abordado")
    interaction_type: str = Field(
        default="academic_question",
        description="Tipo de interacción: academic_question, clarification_request, frustration_signal, dropout_risk, resolution_acknowledgement, support_request"
    )
    difficulty_detected: bool = Field(default=False, description="Indica si se detectó una dificultad académica explícita")
    difficulty_level: str = Field(default="none", description="Nivel de dificultad: none, low, medium, high")
    signals: List[str] = Field(default_factory=list, description="Catálogo de señales cualitativas detectadas")
    signal_confidence: float = Field(default=0.75, ge=0.0, le=1.0, description="Nivel de confianza en la extracción (0.0 a 1.0)")
    suggested_action: str = Field(
        default="continue_monitoring",
        description="Acción de acompañamiento recomendada: offer_short_review, schedule_academic_tutor, send_catchup_guide, academic_coordinator_alert, reinforce_with_exercises, acknowledge_resolution"
    )
    source: str = Field(default="student_message", description="Origen de los datos")
    mode: str = Field(default="demo", description="Modo del motor: demo o live")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), description="Timestamp ISO8601")
    notes: Optional[str] = Field(default=None, description="Observaciones pedagógicas adicionales")

class TelemetrySummary(BaseModel):
    """
    Resumen consolidado para el dashboard institucional.
    Diferencia explícitamente valores de demostración de métricas de deserción institucional.
    """
    total_interactions: int = Field(..., description="Total de interacciones registradas en el piloto")
    active_students_pilot: int = Field(..., description="Estudiantes activos interactuando")
    top_topics: List[Dict[str, Any]] = Field(default_factory=list, description="Temas con mayor volumen de consulta")
    recurrent_difficulties: List[Dict[str, Any]] = Field(default_factory=list, description="Temas con mayores señales de dificultad")
    explicit_dropout_signals_count: int = Field(default=0, description="Interacciones con mención explícita de riesgo o intención de abandono")
    suggested_interventions: List[Dict[str, Any]] = Field(default_factory=list, description="Distribución de acciones recomendadas")
    is_demo_metrics: bool = Field(default=True, description="Indica que las métricas corresponden a un entorno demostrativo")
    disclaimer: str = Field(
        default="AVISO TÉCNICO: Estas señales son cualitativas para orientación tutorial preventiva y NO representan una probabilidad matemática definitiva de deserción.",
        description="Advertencia metodológica"
    )
