"""
Clase REAL de demostración (introducción a n8n): carga local y privada.

Los archivos viven en `demo-assets/` (excluida de Git) y NUNCA se copian ni se modifican:
  * clase-demo.mp4              -> se sirve con solicitudes Range desde su ubicación original
  * transcripcion-original.txt  -> se ingiere en memoria (con enmascarado de datos personales)
  * mask-names.txt (opcional)   -> nombres adicionales a enmascarar, uno por línea

La identidad es única y constante para que el asistente nunca consulte las transcripciones ficticias de SQL
mientras el estudiante ve esta clase: curso `n8n-intro`, clase `clase-01-introduccion-n8n`.
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app.knowledge.retriever import KnowledgeRetriever
from app.knowledge.txt_transcript import IngestReport, ingest_timestamped_txt
from app.media import Mp4Info, probe_mp4
from app.models.transcript import LessonTranscript, format_timestamp
from app.privacy import PrivacyFilter

logger = logging.getLogger(__name__)

REAL_COURSE_ID = "n8n-intro"
REAL_LESSON_ID = "clase-01-introduccion-n8n"
REAL_COURSE_NAME = "Introducción a n8n (clase real)"
REAL_LESSON_TITLE = "Introducción a n8n — Clase 1"
VIDEO_FILE = "clase-demo.mp4"
TRANSCRIPT_FILE = "transcripcion-original.txt"
MASK_NAMES_FILE = "mask-names.txt"
REAL_DISCLAIMER = (
    "Clase REAL de DMC Institute para demostración local. Material privado: no publicar ni compartir la grabación "
    "ni la transcripción. El asistente responde solo con lo que dice esta clase."
)


@dataclass
class RealLesson:
    lesson: LessonTranscript
    report: IngestReport
    video_path: Optional[Path]
    mp4: Mp4Info
    assets_dir: Path
    warnings: List[str] = field(default_factory=list)
    privacy: Optional[PrivacyFilter] = None

    @property
    def _duration(self) -> float:
        """Duración del VIDEO si se pudo leer; si no, el fin de la transcripción."""
        return self.mp4.duration_seconds if self.mp4.ok and self.mp4.duration_seconds else self.lesson.duration_seconds

    def public_info(self) -> dict:
        """Metadatos para el frontend/health: sin rutas locales, sin texto de la clase, sin nombres."""
        return {
            "course_id": REAL_COURSE_ID, "lesson_id": REAL_LESSON_ID, "title": REAL_LESSON_TITLE,
            "duration_seconds": self._duration, "duration_label": format_timestamp(self._duration),
            "video_available": self.video_path is not None,
            "video": self.mp4.to_public() if self.video_path else None,
            "transcript": self.report.public(), "warnings": self.warnings,
        }


def _read_extra_names(path: Path) -> List[str]:
    if not path.is_file():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8-sig").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def load_real_lesson(assets_dir: Path) -> Optional[RealLesson]:
    """Ingiere la clase real si sus archivos existen; devuelve None si falta la transcripción. Nunca lanza por datos malos."""
    assets_dir = Path(assets_dir).resolve()
    transcript_path = assets_dir / TRANSCRIPT_FILE
    if not transcript_path.is_file():
        return None

    warnings: List[str] = []
    video_path: Optional[Path] = (assets_dir / VIDEO_FILE).resolve()
    mp4 = Mp4Info(ok=False)
    if not video_path.is_file() or not video_path.is_relative_to(assets_dir):
        warnings.append(f"No se encontró {VIDEO_FILE} en demo-assets: la clase funcionará sin reproductor.")
        video_path = None
    else:
        mp4 = probe_mp4(video_path)
        warnings += mp4.problems
        if not mp4.ok:
            warnings.append("No se pudo leer la duración del MP4; no se validará la transcripción contra el video.")

    result = ingest_timestamped_txt(
        transcript_path.read_bytes(), REAL_COURSE_ID, REAL_LESSON_ID, REAL_LESSON_TITLE,
        video_duration=mp4.duration_seconds if mp4.ok else None, extra_names=_read_extra_names(assets_dir / MASK_NAMES_FILE),
        source_name=TRANSCRIPT_FILE, disclaimer=REAL_DISCLAIMER, is_official_dmc=True,
    )
    if result.lesson is None:
        logger.error("No se pudo ingerir la transcripción de la clase real (%d problema(s))", len(result.report.issues))
        return None
    for issue in result.report.errors:
        warnings.append(f"Transcripción: {issue.message}")
    return RealLesson(result.lesson, result.report, video_path, mp4, assets_dir, warnings, result.privacy)


def register_real_lesson(retriever: KnowledgeRetriever, real: RealLesson) -> None:
    retriever.register_course(REAL_COURSE_ID, REAL_COURSE_NAME, "Clase real de introducción a n8n.",
                              disclaimer=REAL_DISCLAIMER, is_official_dmc=True)
    retriever.register_lesson(real.lesson)


def write_local_reports(real: RealLesson, out_dir: Path) -> None:
    """
    Informes LOCALES (carpeta ignorada por Git) para revisar la ingesta y verificar a mano la sincronía con el video.
    Contienen fragmentos de la clase: no compartir ni subir.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    r = real.report
    (out_dir / "ingest_report.json").write_text(json.dumps({
        "public": r.public(), "issues": [i.to_dict() for i in r.issues], "capped_spans": r.capped_spans,
        "long_pauses": r.long_pauses, "median_line_seconds": r.median_line_seconds, "name_stats": r.name_stats,
        "mp4": real.mp4.to_public(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Verificación manual de sincronía (LOCAL — contiene fragmentos de la clase; no subir a Git)", "",
        "Las comprobaciones automáticas confirman la estructura (marca = segundos, dentro de la duración del video, texto en su segmento).",
        "**La sincronía entre lo que se oye y el texto solo se confirma escuchando el video**: abre cada minuto y marca la casilla.", "",
        f"- Duración del video (MP4): {format_timestamp(real.mp4.duration_seconds or 0)} · última marca del TXT: "
        f"{format_timestamp(r.last_seconds or 0)}", "- Ya verificado manualmente por el equipo (informado, no por esta herramienta): ~17:04–17:09 «¿Qué es n8n?».", "",
        "| Minuto | Línea TXT | Auto | Fragmento | Escuchado y coincide |", "|---|---|---|---|---|",
    ]
    for s in r.samples:
        auto = "✔" if (s["label_matches_seconds"] and s["inside_video_duration"] and s["chunk_contains_text"]) else "✘"
        lines.append(f"| {s['label']} | {s['line']} | {auto} | {str(s['excerpt']).replace('|', '/')} | [ ] |")
    (out_dir / "verificacion_manual.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
