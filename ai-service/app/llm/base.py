"""
Contrato entre el servicio tutor y los proveedores de lenguaje.

El servicio decide QUÉ evidencia existe (recuperación, memoria, intención); el proveedor
solo decide CÓMO redactarla. Un futuro proveedor LLM recibirá exactamente este mismo
`TutorContext`, de modo que no podrá citar segmentos que el servicio no haya recuperado:
las citas las construye el servicio a partir de `evidence`, nunca el proveedor.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.models.transcript import SegmentMatch


class ResponseKind(str, Enum):
    GROUNDED = "grounded"            # respuesta respaldada por segmentos
    NO_EVIDENCE = "no_evidence"      # el material no contiene el tema
    EXHAUSTED = "exhausted"          # ya se mostró todo el material relacionado
    NEEDS_CONTEXT = "needs_context"  # seguimiento sin tema previo
    OUT_OF_SCOPE = "out_of_scope"    # el guardián juzgó la pregunta ajena a la clase
    GREETING = "greeting"
    CLOSING = "closing"


class FollowUpMode(str, Enum):
    NONE = "none"      # pregunta nueva
    MORE = "more"      # "otro ejemplo": fragmentos distintos a los ya citados
    REPEAT = "repeat"  # "no entendí": volver a explicar lo mismo


@dataclass(frozen=True)
class ChatTurn:
    role: str  # "student" | "tutor"
    content: str


@dataclass(frozen=True)
class TutorContext:
    kind: ResponseKind
    student_message: str
    course_name: str
    lesson_title: Optional[str]                  # None si la consulta abarca todo el curso
    topic_label: Optional[str]                   # tema legible, p. ej. "ORDER BY"
    evidence: List[SegmentMatch] = field(default_factory=list)  # única fuente permitida
    history: List[ChatTurn] = field(default_factory=list)       # ventana acotada de mensajes previos
    turn_number: int = 1                         # turno actual dentro de la sesión (desde 1)
    follow_up_mode: FollowUpMode = FollowUpMode.NONE
    user_intent: str = "question"                # question | follow_up | greeting | closing


@dataclass(frozen=True)
class TutorReply:
    text: str
    follow_up_question: Optional[str] = None
    # Posiciones (en `context.evidence`) de los segmentos que la respuesta realmente usa.
    # None = todos los de la evidencia (comportamiento del modo DEMO). () = ninguno.
    cited_indices: Optional[Tuple[int, ...]] = None
    # Quién generó realmente el texto: el modo mostrado al estudiante sale de aquí.
    provider: str = "demo"
    mode: str = "DEMO"
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None       # solo contadores de tokens
    fallback_reason: Optional[str] = None        # se rellena si LIVE falló y se degradó a DEMO


class LLMProvider(ABC):
    name: str
    mode: str  # "DEMO" o "LIVE"
    # Cuánta evidencia recuperada conviene entregar a este proveedor (el servicio lo respeta).
    max_evidence: int = 2
    secondary_score_ratio: float = 0.85

    @abstractmethod
    def generate(self, context: TutorContext) -> TutorReply:
        """Redacta la respuesta usando SOLO `context.evidence` como fuente de contenido de clase."""
