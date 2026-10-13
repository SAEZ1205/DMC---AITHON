from typing import List, Optional
from pydantic import BaseModel, Field, computed_field, model_validator


def format_timestamp(seconds: float) -> str:
    """Formatea segundos como MM:SS (o H:MM:SS si la grabación supera la hora)."""
    total = int(max(seconds, 0))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class SegmentLine(BaseModel):
    """Renglón original (~5 s) dentro de un segmento: permite citar y saltar al punto exacto."""
    start_seconds: float = Field(..., ge=0)
    label: str
    text: str


class TranscriptSegment(BaseModel):
    """
    Fragmento de una transcripción de clase.
    Los tiempos se almacenan en segundos (float) relativos al inicio de la grabación;
    las etiquetas legibles (MM:SS) se derivan de ellos y nunca se guardan por separado.
    """
    course_id: str = Field(..., min_length=1, description="Identificador del curso")
    lesson_id: str = Field(..., min_length=1, description="Identificador de la clase/grabación")
    segment_id: int = Field(..., ge=0, description="Posición del segmento dentro de la clase (desde 0)")
    text: str = Field(..., min_length=1, description="Texto transcrito del segmento")
    start_seconds: float = Field(..., ge=0, description="Segundo inicial dentro de la grabación")
    end_seconds: float = Field(..., ge=0, description="Segundo final dentro de la grabación")
    # Referencia al archivo original (solo para transcripciones ingeridas desde TXT): permite auditar el segmento.
    source_label: Optional[str] = Field(default=None, description="Marca de tiempo original del primer renglón, p. ej. '1:00:03'")
    source_line: Optional[int] = Field(default=None, description="Número de línea (desde 1) de esa marca en el archivo original")
    end_is_estimated: bool = Field(default=False, description="True si el fin no proviene de una marca posterior del archivo")
    lines: Optional[List[SegmentLine]] = Field(default=None, description="Renglones originales con su marca (solo transcripciones TXT)")

    @model_validator(mode="after")
    def _check_range(self) -> "TranscriptSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError(
                f"end_seconds ({self.end_seconds}) debe ser mayor que start_seconds ({self.start_seconds})"
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def minute_ref(self) -> str:
        """Minuto de la grabación donde empieza el segmento (MM:SS)."""
        return format_timestamp(self.start_seconds)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def timestamp_range(self) -> str:
        """Rango legible, p. ej. '02:30–03:28'."""
        return f"{format_timestamp(self.start_seconds)}–{format_timestamp(self.end_seconds)}"


class LessonTranscript(BaseModel):
    """Transcripción completa de una clase."""
    course_id: str = Field(..., min_length=1)
    lesson_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    source_format: str = Field(default="json", description="Formato de origen: vtt o json")
    is_official_dmc: bool = Field(default=False, description="True solo para material oficial de DMC")
    disclaimer: str = Field(default="", description="Aviso sobre el origen del material")
    content_hash: Optional[str] = Field(default=None, description="SHA-256 del archivo original (huella de la versión de la transcripción)")
    segments: List[TranscriptSegment] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_seconds(self) -> float:
        return max((s.end_seconds for s in self.segments), default=0.0)


class SegmentMatch(BaseModel):
    """Segmento recuperado junto con su puntuación de relevancia."""
    segment: TranscriptSegment
    lesson_title: str
    score: float
    matched_terms: List[str] = Field(default_factory=list)
    # Renglón del segmento que mejor coincide con la consulta (solo si el segmento conserva sus renglones).
    focus_seconds: Optional[float] = None
    focus_text: Optional[str] = Field(default=None, description="Texto del segmento desde el renglón de foco")
