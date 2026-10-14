"""
Resumen de clase PERSISTIDO: se genera una vez por versión de transcripción y se reutiliza.

  * Huella (`transcript_fingerprint`): SHA-256 del contenido de los segmentos (y del archivo original si lo hay). Si la
    transcripción cambia, la huella cambia y el resumen anterior deja de usarse (se regenera una vez).
  * Clave de caché: (curso, clase, huella, motor, versión del pipeline). Cambiar el algoritmo (`PIPELINE_VERSION`)
    también invalida.
  * Concurrencia: un cerrojo por clave garantiza UN solo cálculo aunque lleguen peticiones simultáneas dentro del proceso;
    entre procesos, la clave primaria evita filas duplicadas.
  * Motores: `extractive` (heurístico/DEMO, gratis) y `kimi` (LLM por fragmentos, de pago, solo por acción administrativa).
"""
import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from app.models.transcript import LessonTranscript
from app.storage.academic_store import AcademicStore

PIPELINE_VERSION = "summary/1.2"       # 1.2: capítulos por léxico temático (topic_lexicon automation-es/1)
ENGINES = ("extractive", "kimi")


def transcript_fingerprint(lesson: LessonTranscript) -> str:
    """Huella estable del contenido de la clase (texto + tiempos de los segmentos + huella del archivo original)."""
    canonical = json.dumps(
        {"course": lesson.course_id, "lesson": lesson.lesson_id, "source": lesson.content_hash,
         "segments": [[s.start_seconds, s.end_seconds, s.text] for s in lesson.segments]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class StoredSummary:
    payload: Dict[str, Any]
    generator_meta: Dict[str, Any]
    engine: str
    fingerprint: str
    pipeline_version: str
    generated_at: str
    cached: bool


class SummaryService:
    def __init__(self, store: AcademicStore):
        self.store = store
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.generations: Dict[str, int] = {}          # métricas/pruebas: cuántas veces se CALCULÓ cada motor

    # ---------------------------------------------------------------- lectura
    def peek(self, lesson: LessonTranscript, engine: str) -> Optional[StoredSummary]:
        """Resumen guardado y vigente (misma huella) o None. No genera nada."""
        row = self.store.get_summary(lesson.course_id, lesson.lesson_id, transcript_fingerprint(lesson), engine, PIPELINE_VERSION)
        return self._wrap(row, cached=True) if row else None

    # ------------------------------------------------------------- generación
    def get_or_create(
        self,
        lesson: LessonTranscript,
        engine: str,
        compute: Callable[[], Tuple[Dict[str, Any], Dict[str, Any]]],
        force: bool = False,
    ) -> StoredSummary:
        """
        Devuelve el resumen vigente o lo calcula UNA vez con `compute() -> (payload, generator_meta)`.
        `force=True` (acción administrativa explícita) lo vuelve a calcular y reemplaza al guardado.
        """
        if engine not in ENGINES:
            raise ValueError(f"motor desconocido: {engine}")
        fingerprint = transcript_fingerprint(lesson)
        key = f"{lesson.course_id}|{lesson.lesson_id}|{fingerprint}|{engine}|{PIPELINE_VERSION}"
        with self._lock_for(key):
            if not force:
                row = self.store.get_summary(lesson.course_id, lesson.lesson_id, fingerprint, engine, PIPELINE_VERSION)
                if row:
                    return self._wrap(row, cached=True)
            payload, meta = compute()                                  # fuera de cualquier transacción de SQLite
            self.generations[engine] = self.generations.get(engine, 0) + 1
            self.store.save_summary(lesson.course_id, lesson.lesson_id, fingerprint, engine, PIPELINE_VERSION, payload, meta)
            row = self.store.get_summary(lesson.course_id, lesson.lesson_id, fingerprint, engine, PIPELINE_VERSION)
            return self._wrap(row, cached=False)

    # --------------------------------------------------------------- internos
    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    @staticmethod
    def _wrap(row: Dict[str, Any], cached: bool) -> StoredSummary:
        return StoredSummary(
            payload=json.loads(row["payload"]), generator_meta=json.loads(row["generator_meta"]), engine=row["engine"],
            fingerprint=row["transcript_fingerprint"], pipeline_version=row["pipeline_version"],
            generated_at=row["created_at"], cached=cached,
        )
