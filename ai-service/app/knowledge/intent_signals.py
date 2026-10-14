"""
Señales léxicas para entender QUÉ tipo de respuesta pide una pregunta y qué segmentos la contestan de verdad.

Sin modelos de lenguaje y sin respuestas predefinidas: son reglas generales del español hablado.
Una pregunta de DEFINICIÓN ("¿qué es X?", "¿para qué sirve X?") se contesta, sobre todo, en los momentos en que
  (a) X aparece junto a una expresión de función ("X te permite…", "X sirve para…", "X es un…") o
  (b) alguien plantea esa misma pregunta dentro de la clase ("¿qué hace el X?"), que suele anticipar la explicación.
Un segmento que solo MENCIONA X muchas veces (p. ej. durante la instalación o la resolución de problemas) no explica X,
aunque repita el término: BM25 satura la frecuencia, así que estas señales deshacen los empates a favor de la explicación.
"""
import re
from typing import Iterable, List, Sequence, Set

from app.knowledge.text_utils import normalize_text, stem

# Verbos/expresiones de función en una sola palabra (ya sin acentos).
FUNCTION_TOKENS = frozenset(
    "sirve sirven hace hacen permite permiten significa consiste usa utiliza utilizan ayuda llama trata funciona".split())
COPULA_TOKENS = frozenset("es son".split())
INDEFINITE_TOKENS = frozenset("un una".split())
STYLE_GAP = "¶"          # separador que deja `strip_style` donde quitó una frase; `retriever._query_ngrams` no lo cruza
NEAR_WINDOW = 6          # tokens de distancia máxima entre el sujeto y la expresión de función
ASK_SPAN = 4             # tokens tras "qué" en los que se espera "es/hace/sirve…"
SUBJECT_SPAN = 7         # tokens tras "qué" en los que se espera el sujeto

NEAR_BONUS = 0.7         # multiplicador del puntaje: +70 % si el sujeto está junto a una expresión de función (BM25 satura la repetición en x2,2)
ASKED_BONUS = 0.8       # +80 % si la clase plantea la misma pregunta sobre el sujeto

_DEFINITION_RE = re.compile(
    r"\bque (es|son|significa|quiere decir|hace|hacen)\b|\bpara que (sirve|sirven|se usa|se utiliza|se utilizan)\b|"
    r"\ben que consiste\b|\bque funcion\b|\bdefin(e|ir|icion)\b")

# Frases de ESTILO que no describen el tema consultado ("explícamelo de forma sencilla según lo enseñado por el profesor").
_ADJ = r"(?:sencill|simpl|clar|brev|facil|cort|resumid|detallad|basic|concret|directa?)(?:o|a|os|as|e|es)?"
_ADJS = rf"{_ADJ}(?:\s*(?:,|y|e)\s*{_ADJ})*"          # «clara y breve», «simple, corta y directa»
_STYLE_RES = [re.compile(p, re.IGNORECASE) for p in (
    rf"\bde (forma|manera|modo) (muy |bien |mas )?{_ADJS}\b",
    rf"\b(en|con) (palabras|terminos) (muy )?{_ADJS}\b",
    r"\b(de|en) (forma|manera) (que )?(yo )?(pueda )?(entender|comprender)\b",
    r"\bsegun (lo (que )?)?(ense[ñn]ad[oa]|explicad[oa]|dicho|visto|mencionad[oa]|comentad[oa]|dijo|explic[oó]|ense[ñn][oó])"
    r"( (por|en|durante) (el|la) (profesor|profesora|docente|clase|grabacion|video))?",
    r"\b(segun|de acuerdo (con|a)) (lo que (dice|dijo|explica|explico|ensen[oó]) )?(el|la) (profesor|profesora|docente|clase|grabacion|video)\b",
    r"\blo (que )?(ense[ñn][oó]|explic[oó]|dijo|mencion[oó]) (el|la) (profesor|profesora|docente)\b",
    r"\b(por favor|porfa|gracias)\b",
)]


def strip_style(message: str) -> str:
    """El mensaje sin las frases de estilo/cortesía. Conserva acentos y mayúsculas del resto."""
    plain = message
    for rx in _STYLE_RES:
        plain = _remove(rx, plain)
    return " ".join(plain.split())


def _remove(rx: "re.Pattern[str]", text: str) -> str:
    """Quita las coincidencias de `rx` buscando sobre el texto SIN acentos, pero recortando el texto original (misma longitud)."""
    from app.knowledge.text_utils import strip_accents
    folded = strip_accents(text)
    if len(folded) != len(text):          # normalización que cambia la longitud: se omite por seguridad
        return text
    out, last = [], 0
    for m in rx.finditer(folded):
        out.append(text[last:m.start()])
        out.append(f" {STYLE_GAP} ")        # marca el hueco: ninguna frase literal debe cruzarlo
        last = m.end()
    out.append(text[last:])
    return " ".join("".join(out).split())


def is_definition_question(message: str) -> bool:
    return bool(_DEFINITION_RE.search(normalize_text(message)))


def definition_signals(tokens: Sequence[str], subject_stems: Set[str]) -> "tuple[bool, bool]":
    """(cerca, preguntada): ¿el sujeto está junto a una expresión de función? ¿la clase pregunta por él ("qué hace el X")?"""
    positions = [i for i, t in enumerate(tokens) if stem(t) in subject_stems]
    if not positions:
        return False, False

    def is_function(j: int) -> bool:
        t = tokens[j]
        return t in FUNCTION_TOKENS or (t in COPULA_TOKENS and j + 1 < len(tokens) and tokens[j + 1] in INDEFINITE_TOKENS)

    near = any(is_function(j) for i in positions for j in range(max(0, i - NEAR_WINDOW), min(len(tokens), i + NEAR_WINDOW + 1)))
    asked = False
    for j, t in enumerate(tokens):
        if t != "que":
            continue
        after = tokens[j + 1:j + 1 + ASK_SPAN]
        if not any(a in FUNCTION_TOKENS or a in COPULA_TOKENS for a in after):
            continue
        if any(stem(x) in subject_stems for x in tokens[j + 1:j + 1 + SUBJECT_SPAN]):
            asked = True
            break
    return near, asked
