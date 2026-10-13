"""
Guardián de pertinencia: ¿la pregunta tiene que ver con ESTA clase?

Responsabilidad lógica 1 (el guardián): clasificar la pregunta como RELEVANTE, NO RELEVANTE o INCIERTA.
Responsabilidad lógica 2 (el tutor): responder con evidencia recuperada; vive en `TutorService`.

Dos implementaciones, SIEMPRE etiquetadas con su método real:
  * `HeuristicGuardian` (method="heuristic"): determinista y comprobable. NO es un modelo de lenguaje.
      - relevante   : la pregunta contiene al menos un término específico del vocabulario de la clase.
      - incierta    : solo términos muy generales de la clase, o ninguno (no se bloquea: una pregunta académica razonable
                      puede usar palabras distintas a las de la transcripción; el tutor igualmente exige evidencia).
      - no relevante: ningún término de la clase Y señales claras de otro tema (deportes, cocina, clima...).
  * `LLMGuardian` (method="kimi"): solo existe con Kimi LIVE autorizado. Se consulta únicamente cuando la heurística queda
    INCIERTA (ahorra llamadas) y recibe la pregunta y términos de la clase, nunca la transcripción.

Importante: el guardián no es la barrera de seguridad contra respuestas inventadas; esa barrera es que el tutor solo
responde con segmentos recuperados y verificados.
"""
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Tuple

from app.knowledge.lesson_analysis import ORAL_FILLER
from app.knowledge.retriever import FRAME_STEMS, KnowledgeRetriever
from app.knowledge.text_utils import STOPWORDS, content_stems, normalize_text, tokenize, unique
from app.llm.errors import LLMError
from app.privacy import mask_pii

logger = logging.getLogger(__name__)

SPECIFIC_DF_RATIO = 0.5        # un término presente en más de la mitad de los segmentos es "general" para la clase
MAX_VOCAB_HINTS = 15

# Señales de que la pregunta trata de otro asunto (deliberadamente corta y conservadora)
OFF_TOPIC_CUES = frozenset(normalize_text(w) for w in (
    "fútbol mundial partido gol baloncesto tenis olimpiadas receta pizza cocinar ingredientes postre clima lluvia "
    "pronóstico película canción música videojuego horóscopo chiste política elecciones presidente candidato dieta "
    "vacaciones hotel novio novia poema cuento".split()
))


class Relevance(str, Enum):
    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class GuardianVerdict:
    label: Relevance
    method: str                       # heuristic | kimi | evidence
    reasons: Tuple[str, ...] = ()
    matched_terms: Tuple[str, ...] = ()

    @property
    def is_llm(self) -> bool:
        return self.method == "kimi"


class Guardian(Protocol):
    def assess(self, question: str, course_id: str, lesson_id: Optional[str]) -> GuardianVerdict: ...


class HeuristicGuardian:
    def __init__(self, retriever: KnowledgeRetriever):
        self.retriever = retriever

    def assess(self, question: str, course_id: str, lesson_id: Optional[str]) -> GuardianVerdict:
        terms = [t for t in unique(content_stems(question)) if t not in FRAME_STEMS]
        if not terms:
            return GuardianVerdict(Relevance.UNCERTAIN, "heuristic", ("La pregunta no contiene términos de contenido.",))
        vocab = self.retriever.lesson_vocabulary(course_id, lesson_id)
        total = max(self.retriever.segment_count(course_id, lesson_id), 1)
        known = [t for t in terms if t in vocab]
        specific = [t for t in known if vocab[t] / total <= SPECIFIC_DF_RATIO]
        if specific:
            return GuardianVerdict(Relevance.RELEVANT, "heuristic",
                                   ("Usa términos específicos de la clase.",), tuple(specific))
        if known:
            return GuardianVerdict(Relevance.UNCERTAIN, "heuristic",
                                   ("Solo usa términos muy generales de la clase.",), tuple(known))
        cues = sorted(set(tokenize(question)) & OFF_TOPIC_CUES)
        if cues:
            return GuardianVerdict(Relevance.NOT_RELEVANT, "heuristic",
                                   ("Ningún término de la clase y señales de otro tema.",), tuple(cues))
        return GuardianVerdict(Relevance.UNCERTAIN, "heuristic", ("Ningún término coincide con la clase.",))


class LLMGuardian:
    """Guardián con Kimi: refina SOLO las preguntas que la heurística dejó inciertas."""

    def __init__(self, provider, retriever: KnowledgeRetriever, base: HeuristicGuardian):
        self.provider, self.retriever, self.base = provider, retriever, base

    def assess(self, question: str, course_id: str, lesson_id: Optional[str]) -> GuardianVerdict:
        verdict = self.base.assess(question, course_id, lesson_id)
        if verdict.label is not Relevance.UNCERTAIN:
            return verdict
        lesson = self.retriever.get_lesson(course_id, lesson_id) if lesson_id else None
        title = lesson.title if lesson else course_id
        # Si la clase es privada y el proveedor no tiene autorización para su contenido, solo viaja la pregunta (ya enmascarada).
        allowed = getattr(self.provider, "content_allowed", lambda c, l: True)(course_id, lesson_id)
        hints = self._hints(course_id, lesson_id) if allowed else []
        try:
            label, reason = self.provider.classify_relevance(mask_pii(question), title, hints)
        except LLMError as exc:
            logger.warning("Guardián LLM no disponible (%s); se conserva el veredicto heurístico.", exc.code)
            return GuardianVerdict(verdict.label, "heuristic",
                                   verdict.reasons + ("El guardián LLM no respondió; se usó la heurística.",), verdict.matched_terms)
        return GuardianVerdict(Relevance(label), "kimi", (reason,), verdict.matched_terms)

    def _hints(self, course_id: str, lesson_id: Optional[str]):
        """Términos más característicos de la clase (por frecuencia en segmentos); no se envía texto de la transcripción."""
        vocab = self.retriever.lesson_vocabulary(course_id, lesson_id)
        total = max(self.retriever.segment_count(course_id, lesson_id), 1)
        scored = sorted(((df, t) for t, df in vocab.items() if len(t) >= 4 and df / total <= 0.5 and t not in STOPWORDS
                         and t not in ORAL_FILLER and re.fullmatch(r"[a-z0-9]+", t)), reverse=True)
        return [t for _, t in scored[:MAX_VOCAB_HINTS]]
