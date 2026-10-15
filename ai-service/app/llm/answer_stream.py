"""
Extracción incremental del texto de «answer» mientras el modelo emite el JSON por streaming.

El modelo responde un objeto JSON ({"answer": "...", "used_segments": [...], ...}). Para mostrar texto progresivo hay que ir sacando,
a medida que llegan los trozos, los caracteres decodificados de la cadena JSON del campo «answer». Este texto es PROVISIONAL: la respuesta
oficial (con citas y minutos verificados por el backend) solo existe cuando termina el stream y pasa el verificador.

  * `AnswerStreamExtractor.feed(chunk)` devuelve los caracteres NUEVOS del valor de «answer» (puede ser "").
  * Maneja escapes JSON (barra+n, barra+comilla, barra+u+XXXX, incluidos pares sustitutos de emojis) aunque lleguen partidos entre trozos.
  * `ProvisionalText` retiene etiquetas de cita incompletas («[S1», «[S») y quita las etiquetas completas del texto provisional: el estudiante
    no debe ver «[S2]» como si fuera una referencia navegable.
El razonamiento interno del modelo (`reasoning_content`) NO pasa por aquí: el proveedor lo descarta antes.
"""
import re
from typing import List, Optional

_START_RE = re.compile(r'"answer"\s*:\s*"')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
MAX_SEARCH_CHARS = 20000          # si «answer» no aparece en esto, se abandona la extracción (no es el formato esperado)
_TAG_RE = re.compile(r"\[\s*S\d+(?:\s*[,;y]\s*S\d+)*\s*\]", re.IGNORECASE)
REPLACEMENT = "�"


class AnswerStreamExtractor:
    def __init__(self) -> None:
        self._buffer = ""
        self._in_string = False
        self._done = False
        self._failed = False
        self._escape = ""                 # escape a medias (barra invertida o barra+u+dígitos)
        self._high: Optional[int] = None  # mitad alta de un par sustituto pendiente de su pareja

    @property
    def active(self) -> bool:
        return self._in_string and not self._done

    @property
    def finished(self) -> bool:
        return self._done

    def feed(self, chunk: str) -> str:
        if self._done or self._failed or not chunk:
            return ""
        if not self._in_string:
            self._buffer += chunk
            match = _START_RE.search(self._buffer)
            if match is None:
                if len(self._buffer) > MAX_SEARCH_CHARS:
                    self._failed = True
                    self._buffer = ""
                return ""
            self._in_string = True
            chunk, self._buffer = self._buffer[match.end():], ""
        return self._decode(chunk)

    def _decode(self, text: str) -> str:
        out: List[str] = []
        for ch in text:
            if self._escape:
                self._escape += ch
                if self._escape[1] == "u":
                    if len(self._escape) < 6:
                        continue
                    try:
                        code: Optional[int] = int(self._escape[2:], 16)
                    except ValueError:
                        code = None
                    self._escape = ""
                    if code is not None:
                        self._emit_code(code, out)
                else:
                    self._flush_high(out)
                    out.append(_ESCAPES.get(self._escape[1], self._escape[1]))
                    self._escape = ""
                continue
            if ch != "\\":
                self._flush_high(out)                          # solo un nuevo escape puede traer la pareja del par sustituto
            if ch == "\\":
                self._escape = "\\"
            elif ch == '"':
                self._done = True
                break
            else:
                out.append(ch)
        return "".join(out)

    def _flush_high(self, out: List[str]) -> None:
        """Una mitad alta de par sustituto que no recibió su pareja se reemplaza por U+FFFD."""
        if self._high is not None:
            out.append(REPLACEMENT)
            self._high = None

    def _emit_code(self, code: int, out: List[str]) -> None:
        if self._high is not None:
            if 0xDC00 <= code <= 0xDFFF:                       # pareja del par sustituto (emoji, etc.)
                out.append(chr(0x10000 + ((self._high - 0xD800) << 10) + (code - 0xDC00)))
                self._high = None
                return
            self._flush_high(out)
        if 0xD800 <= code <= 0xDBFF:
            self._high = code                                  # se espera su pareja en el siguiente escape
        elif 0xDC00 <= code <= 0xDFFF:
            out.append(REPLACEMENT)
        else:
            out.append(chr(code))


class ProvisionalText:
    """Texto provisional apto para mostrar: sin etiquetas [S#] y sin dejar a la vista una etiqueta a medio escribir."""

    def __init__(self) -> None:
        self._pending = ""
        self.text = ""

    def push(self, fragment: str) -> str:
        """Devuelve lo nuevo que ya se puede mostrar."""
        if not fragment:
            return ""
        data = self._pending + fragment
        data = _TAG_RE.sub("", data)
        cut = data.rfind("[")
        if cut != -1 and "]" not in data[cut:]:
            self._pending, visible = data[cut:], data[:cut]
        else:
            self._pending, visible = "", data
        self.text += visible
        return visible

    def flush(self) -> str:
        rest, self._pending = _TAG_RE.sub("", self._pending), ""
        self.text += rest
        return rest


def decode_all(chunks, extractor: Optional[AnswerStreamExtractor] = None) -> str:
    """Utilidad de pruebas: concatena lo extraído de una secuencia de trozos."""
    ex = extractor or AnswerStreamExtractor()
    return "".join(ex.feed(c) for c in chunks)
