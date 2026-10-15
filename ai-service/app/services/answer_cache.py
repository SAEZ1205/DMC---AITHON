"""
Caché de respuestas académicas YA VERIFICADAS (SQLite local, ignorada por Git). Su único fin es que una pregunta repetida sobre la clase no
vuelva a esperar a Kimi. Se diseñó para no poder confundirse con una generación nueva ni filtrar datos de una conversación a otra:

  * SOLO se guarda lo que ya pasó el verificador de citas, vino de una generación LIVE nueva (no de un fallback ni de otra caché), fue la
    primera pregunta de una conversación (sin historial) y no contenía datos personales enmascarados ([nombre], [correo]...).
  * La clave es un hash de: pregunta normalizada + clase + huella de la transcripción enmascarada + segmentos de evidencia elegidos +
    versión de recuperación + versión del prompt + proveedor + modelo + parámetros de generación + señales de evidencia. Cambiar cualquiera
    (otra transcripción, otra lista de nombres, otro ranking, otro prompt, otro modelo...) hace que la entrada deje de coincidir.
  * No se guarda la pregunta (solo su hash), ni el estudiante, ni la sesión, ni el historial: únicamente el texto ya verificado (derivado de
    la clase enmascarada), qué segmentos cita, el modelo y las fechas.
  * La respuesta servida se etiqueta `CACHED` con su fecha original: jamás se presenta como una generación nueva de Kimi.
"""
import hashlib
import json
import re
import threading
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.knowledge.text_utils import tokenize
from app.storage.conversation_store import ConversationStore, utc_now

RETRIEVAL_VERSION = "retrieval/3"     # súbela si cambian el ranking, la selección de evidencia o el historial enviado
_PERSONAL_RE = re.compile(r"\[(?:correo|teléfono|documento|secreto|nombre)\]")
MAX_ENTRIES = 500

_DDL = """
CREATE TABLE IF NOT EXISTS answer_cache (
    cache_key        TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    last_hit_at      TEXT,
    hits             INTEGER NOT NULL DEFAULT 0,
    provider         TEXT NOT NULL,
    model            TEXT,
    answer_text      TEXT NOT NULL,
    follow_up        TEXT,
    cited_starts     TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class CachedAnswer:
    text: str
    follow_up_question: Optional[str]
    cited_starts: List[float]
    provider: str
    model: Optional[str]
    created_at: str
    hits: int


def normalize_question(message: str) -> str:
    """Pregunta ya enmascarada, en minúsculas y sin acentos ni puntuación (dos formas de escribir lo mismo dan la misma clave)."""
    return " ".join(tokenize(message))


def has_personal_data(message: str) -> bool:
    return bool(_PERSONAL_RE.search(message))


def make_key(*, question: str, course_id: str, lesson_id: str, fingerprint: str, evidence_starts: Sequence[float], prompt_version: str,
             provider: str, model: str, effort: str, max_tokens: int, direct_evidence: Optional[bool], missing_terms: Sequence[str],
             retrieval_version: str = RETRIEVAL_VERSION) -> str:
    material: Dict[str, Any] = {
        "q": normalize_question(question), "course": course_id, "lesson": lesson_id, "fp": fingerprint,
        "evidence": [round(float(s), 3) for s in evidence_starts], "prompt": prompt_version, "provider": provider, "model": model,
        "effort": effort, "tokens": int(max_tokens), "direct": direct_evidence, "missing": list(missing_terms), "retrieval": retrieval_version,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class AnswerCache:
    def __init__(self, store: ConversationStore, enabled: bool = True, max_entries: int = MAX_ENTRIES):
        self.store, self.enabled, self.max_entries = store, enabled, max_entries
        self._lock = threading.Lock()
        self._ready = False

    def _ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if not self._ready:
                self.store.initialize()
                with self.store.transaction() as conn:
                    conn.execute(_DDL)
                self._ready = True

    def get(self, key: str) -> Optional[CachedAnswer]:
        if not self.enabled:
            return None
        self._ensure()
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM answer_cache WHERE cache_key = ?", (key,)).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE answer_cache SET hits = hits + 1, last_hit_at = ? WHERE cache_key = ?", (utc_now(), key))
            return CachedAnswer(row["answer_text"], row["follow_up"], json.loads(row["cited_starts"]), row["provider"], row["model"],
                                row["created_at"], int(row["hits"]) + 1)

    def put(self, key: str, *, text: str, follow_up_question: Optional[str], cited_starts: Sequence[float], provider: str,
            model: Optional[str]) -> None:
        if not self.enabled or not text.strip() or not cited_starts:
            return
        self._ensure()
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO answer_cache (cache_key, created_at, last_hit_at, hits, provider, model, answer_text, follow_up, cited_starts) "
                "VALUES (?, ?, NULL, 0, ?, ?, ?, ?, ?)",
                (key, utc_now(), provider, model, text, follow_up_question, json.dumps([float(s) for s in cited_starts])))
            conn.execute("DELETE FROM answer_cache WHERE cache_key IN (SELECT cache_key FROM answer_cache ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                         (self.max_entries,))

    def summary(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "entries": 0, "hits": 0}
        self._ensure()
        with closing(self.store.connection()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(hits), 0) AS h FROM answer_cache").fetchone()
        return {"enabled": True, "entries": int(row["n"]), "hits": int(row["h"])}

    def clear(self) -> int:
        """Vacía la caché (acción explícita de administración o de pruebas)."""
        self._ensure()
        with self.store.transaction() as conn:
            return conn.execute("DELETE FROM answer_cache").rowcount
