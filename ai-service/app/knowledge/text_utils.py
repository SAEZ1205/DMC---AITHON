"""
Utilidades de texto para la recuperación documental.

Todo el emparejamiento consulta <-> contenido se hace sobre términos
normalizados: minúsculas, sin acentos, sin palabras vacías y con un
stemming ligero (plural + prefijo), de modo que "proyección", "PROYECCION"
y "proyecciones" producen el mismo término.
"""
import re
import unicodedata
from typing import List, Sequence

# Longitud del prefijo usado como "raíz" (ordenar/orden/ordenamiento -> "orden").
STEM_LENGTH = 5

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Marcadores del enmascarado de privacidad ([correo], [nombre]...): no son palabras de búsqueda ni de la clase.
_PLACEHOLDER_RE = re.compile(r"\[(?:correo|telefono|documento|secreto|nombre)\]")

# Palabras vacías del español (ya sin acentos) más palabras "meta" que aparecen
# en preguntas sobre la grabación pero no describen el contenido consultado.
STOPWORDS = frozenset(
    """
    a al algo algun alguna algunas alguno algunos ante antes aqui asi aun aunque
    cada como con contra cual cuales cuando cuanto cuantos cuantas de del desde
    donde dos durante e el ella ellas ellos en entre era eran eres es esa esas
    ese eso esos esta estaba estan estar estas este esto estos estoy fue fueron
    ha hace hacen hacer han hasta hay he la las le les lo los mas me mi mis mucho
    muchos muy nada ni no nos nosotros nuestro nuestra o os otra otras otro otros
    para pero poco por porque que quien quienes quiero se ser si sin sobre son
    soy su sus tambien tanto te tengo tiene tienen todo todos tu tus un una uno
    unas unos usa usan uso usted va vamos van veces vez voy y ya yo
    puedo puede pueden podria podrias necesito dime dame quisiera saber
    usar utilizar utiliza utilizan hacer hago funciona
    explica explican explicame explico explicas explicacion explicar
    clase clases grabacion grabaciones video minuto minutos segundo segundos
    docente profesor profesora ejemplo ejemplos curso tema temas
    """.split()
)


def strip_accents(text: str) -> str:
    """Elimina diacríticos (tildes, diéresis, ñ -> n)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    """Minúsculas y sin acentos."""
    return strip_accents(text).lower()


def tokenize(text: str) -> List[str]:
    """Tokens alfanuméricos normalizados (sin filtrar palabras vacías)."""
    return _TOKEN_RE.findall(_PLACEHOLDER_RE.sub(" ", normalize_text(text)))


def stem(token: str) -> str:
    """Raíz ligera: quita la 's' final de plurales y recorta a STEM_LENGTH."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token[:STEM_LENGTH]


def content_tokens(text: str) -> List[str]:
    """Tokens normalizados con contenido (sin palabras vacías ni tokens de 1 carácter)."""
    return [tok for tok in tokenize(text) if len(tok) > 1 and tok not in STOPWORDS]


def content_stems(text: str) -> List[str]:
    """Raíces de los términos con contenido, en orden y con repeticiones."""
    return [stem(tok) for tok in content_tokens(text)]


def unique(items: Sequence[str]) -> List[str]:
    """Elimina duplicados conservando el orden."""
    return list(dict.fromkeys(items))


def contains_phrase(sequence: Sequence[str], phrase: Sequence[str]) -> bool:
    """True si `phrase` aparece contigua dentro de `sequence`."""
    n = len(phrase)
    if n == 0 or n > len(sequence):
        return False
    return any(list(sequence[i:i + n]) == list(phrase) for i in range(len(sequence) - n + 1))


def bigrams(sequence: Sequence[str]) -> List[tuple]:
    return list(zip(sequence, sequence[1:]))
