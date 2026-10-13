"""
Clasificación CONSERVADORA de la respuesta a la micro-pregunta, por reglas verificables.

Qué es y qué no es
  * Detecta qué TEMA menciona el estudiante (tiempo, trabajo, contenido, ritmo, satisfacción).
  * NO mide emociones, no infiere estados psicológicos y no es un diagnóstico.
  * La confianza nunca es "alta": como mucho "media" (una sola categoría con pistas claras).
  * Las pistas negadas ("no me cuesta") se ignoran. Si no hay pistas, se admite no saber
    (`sin_informacion_suficiente`) en lugar de adivinar.
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from app.knowledge.text_utils import content_tokens, tokenize
from app.models.academic import MicroCategory

DISCLAIMER = (
    "Interpretación automática por reglas sobre palabras clave: indica el TEMA que mencionaste, no es un "
    "diagnóstico ni una valoración psicológica, y no se usa para decidir nada sobre ti."
)

# Pistas (ya sin acentos). Cada pista es una secuencia de palabras completas.
CUES: Dict[MicroCategory, Sequence[str]] = {
    MicroCategory.DISPONIBILIDAD_TIEMPO: [
        "tiempo", "horario", "horarios", "agenda", "disponibilidad", "cruza", "cruzan", "se cruza",
        "llego tarde", "llego justo", "no alcanzo", "no me alcanza", "no puedo conectarme",
    ],
    MicroCategory.TRABAJO: [
        "trabajo", "trabajando", "trabajar", "oficina", "jefe", "reunion", "reuniones", "laboral", "empresa", "guardia",
    ],
    MicroCategory.DIFICULTAD_CONTENIDO: [
        "dificil", "dificiles", "complicado", "complicada", "complejo", "confuso", "confusa", "dificultad",
        "me cuesta", "cuesta", "no entiendo", "no entendi", "no comprendo", "perdido", "perdida",
    ],
    MicroCategory.RITMO_ACADEMICO: [
        "ritmo", "rapido", "lento", "avanza", "avanzan", "velocidad", "apurado", "apurada", "atrasado", "atrasada",
        "mucho contenido",
    ],
    MicroCategory.SATISFACCION: [
        "me gusta", "me gustan", "contento", "contenta", "satisfecho", "satisfecha", "excelente", "genial",
        "comodo", "comoda", "perfecto", "perfecta", "muy bien", "todo bien", "me acomoda", "me acomoda bien",
    ],
}

# En empate se elige la primera de esta lista (y se informan todas las candidatas, con confianza baja).
PRIORITY: Tuple[MicroCategory, ...] = (
    MicroCategory.DISPONIBILIDAD_TIEMPO,
    MicroCategory.TRABAJO,
    MicroCategory.DIFICULTAD_CONTENIDO,
    MicroCategory.RITMO_ACADEMICO,
    MicroCategory.SATISFACCION,
)
NEGATIONS = frozenset({"no", "nunca", "tampoco", "ni", "jamas", "sin"})
# La negación solo anula pistas EVALUATIVAS ("no me cuesta", "no me gusta"). Las temáticas siguen valiendo:
# "no tengo tiempo" es un comentario sobre disponibilidad de tiempo, no su ausencia.
NEGATION_SENSITIVE = frozenset({
    MicroCategory.DIFICULTAD_CONTENIDO, MicroCategory.RITMO_ACADEMICO, MicroCategory.SATISFACCION,
})
NEGATION_WINDOW = 3          # una negación hasta 3 palabras antes anula la pista
MIN_WORDS_FOR_OTHER = 4      # menos palabras con contenido => no hay información suficiente


@dataclass(frozen=True)
class Interpretation:
    category: MicroCategory
    candidates: List[MicroCategory]
    confidence: str            # "baja" | "media"
    matched_cues: List[str]


def _find(tokens: List[str], cue_tokens: List[str]) -> List[int]:
    n = len(cue_tokens)
    return [i for i in range(len(tokens) - n + 1) if tokens[i:i + n] == cue_tokens]


def _negated(tokens: List[str], index: int) -> bool:
    return any(t in NEGATIONS for t in tokens[max(0, index - NEGATION_WINDOW):index])


_CLAUSE_SPLIT = re.compile(r"[.,;:!?¡¿()\n]|\b(?:pero|aunque|sino)\b", re.IGNORECASE)


def classify(text: str) -> Interpretation:
    # La negación solo afecta a la cláusula donde aparece ("No me cuesta, me gusta" => "me gusta" sigue valiendo).
    clauses = [tokenize(part) for part in _CLAUSE_SPLIT.split(text)]
    clauses = [c for c in clauses if c]
    matched: Dict[MicroCategory, List[str]] = {}
    for category, cues in CUES.items():
        for cue in cues:
            cue_tokens = cue.split()
            for tokens in clauses:
                positions = _find(tokens, cue_tokens)
                if category in NEGATION_SENSITIVE:
                    counts = any(cue_tokens[0] in NEGATIONS or not _negated(tokens, p) for p in positions)
                else:
                    counts = bool(positions)
                if counts:
                    if cue not in matched.setdefault(category, []):
                        matched[category].append(cue)
                    break
        if category in matched and not matched[category]:
            del matched[category]

    if not matched:
        words = [t for t in content_tokens(text) if len(t) > 2]
        if len(words) >= MIN_WORDS_FOR_OTHER:
            return Interpretation(MicroCategory.OTRO, [MicroCategory.OTRO], "baja", [])
        return Interpretation(MicroCategory.SIN_INFORMACION_SUFICIENTE, [], "baja", [])

    best_score = max(len(c) for c in matched.values())
    top = [c for c in PRIORITY if c in matched and len(matched[c]) == best_score]
    candidates = [c for c in PRIORITY if c in matched]
    primary = top[0]
    confidence = "media" if len(candidates) == 1 else "baja"
    cues = [cue for c in candidates for cue in matched[c]]
    return Interpretation(primary, candidates, confidence, cues)
