"""
Análisis determinista del mensaje del estudiante (sin modelos de lenguaje).

Distingue cuatro situaciones:
  question   - pregunta con términos propios de contenido
  follow_up  - mensaje que depende del contexto anterior ("¿puedes darme otro ejemplo?")
  greeting   - saludo
  closing    - agradecimiento o despedida
"""
import re
from dataclasses import dataclass

from app.knowledge.intent_signals import is_definition_question, strip_style
from app.knowledge.text_utils import content_tokens, normalize_text, tokenize
from app.llm.base import FollowUpMode

GREETING_WORDS = frozenset(
    "hola buenas buenos dias tardes noches hey saludos holi buen".split()
)
CLOSING_WORDS = frozenset(
    "gracias chau chao adios luego vemos listo perfecto genial entendido excelente ok vale bye despido "
    "muchas muchisimas infinitas profe".split()
)
SOCIAL_WORDS = GREETING_WORDS | CLOSING_WORDS

# Piden fragmentos distintos a los ya mostrados
MORE_WORDS = frozenset("otro otra otros otras mas siguiente adicional adicionales ademas".split())
# Piden volver a explicar lo mismo
REPEAT_WORDS = frozenset(
    "entendi entiendo entender repite repetir repetime aclara aclarar confuso confusa perdi perdido "
    "nuevo nuevamente denuevo".split()
)
# Palabras de conversación que no son contenido de la clase
FILLER_WORDS = frozenset(
    """
    entonces pero duda dudas pregunta preguntar detalle detalles profundiza profundizar
    puedes podrias podria darme dame dar damelo muestrame mostrar muestra cuentame cuenta decir
    queria quisiera gustaria favor ayuda ayudame ayudar ver sabes pasa pasaria ocurre sucede
    explicalo explicarlo explicamelo repitelo
    """.split()
)
IGNORABLE_WORDS = MORE_WORDS | REPEAT_WORDS | FILLER_WORDS | SOCIAL_WORDS

# Referencias al tema anterior ("¿qué pasa si no uso eso?")
DEICTIC_WORDS = frozenset("eso esto esa ese esta este esos estos anterior mismo misma ello".split())


@dataclass(frozen=True)
class MessageAnalysis:
    user_intent: str         # question | follow_up | greeting | closing
    query: str               # términos propios del mensaje, con la ortografía del estudiante ("" si no tiene)
    follow_up_mode: FollowUpMode
    refers_to_previous: bool  # contiene deícticos (eso, esto, anterior...)
    search_text: str = ""     # el mensaje sin frases de estilo ("de forma sencilla", "según lo enseñado..."): sirve para frases literales
    kind: str = "other"       # definition | other: qué tipo de respuesta pide (ver app.knowledge.intent_signals)


def analyze_message(message: str) -> MessageAnalysis:
    tokens = tokenize(message)
    all_tokens = set(tokens)
    core = strip_style(message)          # las frases de estilo no describen el tema: "forma sencilla" no debe traer segmentos
    own_terms = [t for t in content_tokens(core) if t not in IGNORABLE_WORDS]
    kind = "definition" if is_definition_question(message) else "other"

    again = any(a == "otra" and b == "vez" for a, b in zip(tokens, tokens[1:]))  # "otra vez" = repetir
    if again:
        mode = FollowUpMode.REPEAT
    elif all_tokens & MORE_WORDS:
        mode = FollowUpMode.MORE
    elif all_tokens & REPEAT_WORDS:
        mode = FollowUpMode.REPEAT
    else:
        mode = FollowUpMode.NONE

    refers = bool(all_tokens & DEICTIC_WORDS)

    if own_terms:
        wanted = set(own_terms)
        words = [w for w in re.findall(r"\w+", core) if normalize_text(w) in wanted]
        return MessageAnalysis("question", " ".join(words), mode, refers, core, kind)

    if all_tokens & CLOSING_WORDS:
        return MessageAnalysis("closing", "", FollowUpMode.NONE, False)
    if all_tokens & GREETING_WORDS:
        return MessageAnalysis("greeting", "", FollowUpMode.NONE, False)

    # Sin términos propios ni saludo: depende del contexto. Sin pista explícita, se repasa lo mismo.
    return MessageAnalysis("follow_up", "", mode if mode is not FollowUpMode.NONE else FollowUpMode.REPEAT, True)
