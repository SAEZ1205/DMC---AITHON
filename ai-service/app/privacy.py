"""
Enmascarado CONSERVADOR de datos personales y secretos reconocibles.

Se aplica (1) a la transcripción de la clase real al ingerirla, (2) al mensaje del estudiante antes de procesarlo o
guardarlo y (3) a cualquier texto que viaje a un prompt. No es una garantía: es heurístico y puede dejar pasar
nombres escritos de forma inusual. Los archivos originales nunca se modifican; el enmascarado ocurre en memoria.

Categorías: correo, teléfono, documento (8 dígitos), secreto (claves/tokens) y nombre de persona.
Los nombres se detectan cuando alguien se presenta o es mencionado con un tratamiento ("soy X", "me llamo X",
"profesor X", "gracias X") y, además, se propaga el enmascarado a las demás menciones de ese nombre siempre que
el nombre no sea también una palabra común (p. ej. "link"): se compara cuántas veces aparece capitalizado frente a
minúsculas. Opcionalmente se puede aportar una lista explícita de nombres (`extra_names`).
"""
import re
from collections import Counter
from typing import Dict, Iterable, List, Set, Tuple

from app.knowledge.text_utils import STOPWORDS, normalize_text

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Teléfono: opcional +prefijo y 9-15 dígitos con separadores; no se confunde con horas (2:30) ni números cortos.
PHONE_RE = re.compile(r"(?<![\w:])(?:\+\d{1,3}[ .\-]?)?(?:\d[ .\-]?){8,14}\d(?![\w:])")
DOC_RE = re.compile(r"(?<![\w:.])\d{8}(?![\w:.])")
SECRET_RE = re.compile(
    r"\b(?:sk|pk|api|key|token|secret)[-_][A-Za-z0-9_\-]{12,}\b"
    r"|\bBearer\s+[A-Za-z0-9._\-]{16,}"
    r"|https?://\S*(?:token|key|secret|password)=\S+"
    r"|\b[A-Fa-f0-9]{32,}\b"
)

# Solo disparadores PERSONALES. Se excluyen "se llama/llamado", que en una clase técnica introducen productos
# ("se llama Docker", "se llama Google Sheets").
_TRIGGERS = (
    r"soy|me llamo|mi nombre es|profesor|profesora|docente|ingeniero|ingeniera|ing|"
    r"licenciado|licenciada|lic|señor|señora|sr|sra|don|doña|hola|gracias"
)
# Palabra capitalizada con minúsculas (Maria, Torres): las siglas (API, DMC) no son nombres.
_CAP = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}"
# El disparador admite una coma pero no un punto: "gracias. Hasta luego" no nombra a nadie.
NAME_INTRO_RE = re.compile(rf"(?i:\b(?:{_TRIGGERS})\b),?[ \t]+(?P<name>{_CAP}(?:[ \t]+{_CAP}){{0,2}})")
# Palabras capitalizadas que NO son nombres aunque sigan a un disparador ("Hola Buenas noches")
NOT_NAMES = frozenset(normalize_text(w) for w in (
    "Buenas Buenos Chicos Chicas Todos Todas Bienvenidos Bienvenidas Bueno Entonces Gracias Hola Muy Ahora Vamos "
    "Primero Segundo Tercero Sí Si No Ya Eso Esto Este Esta Aquí Aqui Dios Profesor Docente Clase Curso Ingeniero "
    "Licenciado Ingeniería Industrial Sistemas Software Python Docker Windows Google Microsoft Linux Mac".split()
))
PLACEHOLDERS = {"email": "[correo]", "phone": "[teléfono]", "document": "[documento]", "secret": "[secreto]", "name": "[nombre]"}


def mask_pii(text: str, extra_names: Iterable[str] = ()) -> str:
    """Enmascara solo con patrones (sin conocimiento del corpus): útil para mensajes sueltos y logs."""
    return PrivacyFilter(extra_names=extra_names).mask(text)[0]


class PrivacyFilter:
    def __init__(self, extra_names: Iterable[str] = ()):
        self._names: Set[str] = {normalize_text(n) for n in extra_names if n and len(n.strip()) >= 3}
        self.counts: Counter = Counter()

    # ----------------------------------------------------------- aprendizaje
    def learn_names(self, texts: Iterable[str]) -> Dict[str, int]:
        """
        Detecta nombres por presentación/tratamiento en `texts` y decide cuáles propagar a todo el corpus.
        Devuelve contadores {detected, propagated, kept_as_common_word}. Los nombres en sí NO se devuelven.
        """
        texts = list(texts)
        detected: Set[str] = set()
        for text in texts:
            for m in NAME_INTRO_RE.finditer(text):
                for token in m.group("name").split():
                    key = normalize_text(token)
                    if len(key) >= 3 and key not in NOT_NAMES and key not in STOPWORDS:
                        detected.add(key)
        # ¿el token es además una palabra común? => aparece más en minúsculas que capitalizado
        capital, lower = Counter(), Counter()
        word_re = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+")
        for text in texts:
            for w in word_re.findall(text):
                key = normalize_text(w)
                if key in detected:
                    (capital if w[0].isupper() else lower)[key] += 1
        propagated = {k for k in detected if capital[k] >= lower[k]}
        self._names |= propagated
        return {"detected": len(detected), "propagated": len(propagated), "kept_as_common_word": len(detected) - len(propagated)}

    # ------------------------------------------------------------ enmascarado
    def mask(self, text: str) -> Tuple[str, Dict[str, int]]:
        counts: Counter = Counter()

        def sub(regex, label, value):
            nonlocal text
            text, n = regex.subn(value, text)
            counts[label] += n

        sub(EMAIL_RE, "email", PLACEHOLDERS["email"])
        sub(SECRET_RE, "secret", PLACEHOLDERS["secret"])
        sub(PHONE_RE, "phone", PLACEHOLDERS["phone"])
        sub(DOC_RE, "document", PLACEHOLDERS["document"])

        text = NAME_INTRO_RE.sub(lambda m: self._intro(m, counts), text)
        if self._names:
            def hide_known(m: "re.Match[str]") -> str:
                if normalize_text(m.group(0)) in self._names:
                    counts["name"] += 1
                    return PLACEHOLDERS["name"]
                return m.group(0)
            text = re.sub(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{3,}", hide_known, text)
        self.counts.update(counts)
        return text, dict(counts)

    @staticmethod
    def _intro(m: "re.Match[str]", counts: Counter) -> str:
        """Enmascara solo las palabras capitalizadas que parecen nombre dentro de la presentación."""
        words = m.group("name").split()
        out = []
        for w in words:
            key = normalize_text(w)
            if key in NOT_NAMES or key in STOPWORDS:
                out.append(w)
            else:
                out.append(PLACEHOLDERS["name"]); counts["name"] += 1
        return m.group(0).replace(m.group("name"), " ".join(out))
