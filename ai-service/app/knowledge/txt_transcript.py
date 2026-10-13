"""
Ingesta de transcripciones TXT con marcas de tiempo ("17:09", "1:00:03") hacia segmentos recuperables.

Formato esperado (el de la clase real): una línea que contiene SOLO una marca de tiempo, seguida del texto
transcrito en la(s) línea(s) siguientes. El archivo original NUNCA se modifica.

Decisiones de diseño (todas quedan en el reporte, nada se corrige en silencio)
  * Una marca válida ocupa la línea ENTERA (`M:SS`, `MM:SS`, `H:MM:SS`, `HH:MM:SS`). Horas pronunciadas dentro de una
    frase ("a las 2:30, por ejemplo") jamás se interpretan como marcas porque no ocupan la línea completa.
  * Una línea que parece marca pero rompe la secuencia (retrocede en el tiempo, repite la anterior o excede la duración
    del video) se trata como TEXTO y se reporta.
  * El fin de cada renglón es el inicio del siguiente, con un tope (`MAX_LINE_SPAN_SECONDS`) para no atribuir minutos
    de silencio a unas pocas palabras; los topes se cuentan y se marcan `end_is_estimated`.
  * El último renglón no tiene marca posterior: su fin es la duración del video si se conoce (marcado como estimado);
    si no, se usa un fin mínimo y se reporta.
  * Los renglones (~5 s) se agrupan en segmentos de ~40 s (límite de caracteres y pausas largas) para recuperar y citar.
    Cada segmento conserva la marca original y la línea del archivo de su primer renglón.
  * Correos, teléfonos, documentos, secretos y nombres se enmascaran en memoria (ver `app.privacy`).
"""
import hashlib
import re
import statistics
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from app.models.transcript import LessonTranscript, SegmentLine, TranscriptSegment, format_timestamp
from app.privacy import PrivacyFilter

PARSER_VERSION = "txt-ts/1.0"
MAX_LINE_SPAN_SECONDS = 30.0        # tope del fin de un renglón (la mediana real es ~5 s)
CHUNK_TARGET_SECONDS = 40.0         # un segmento se cierra al alcanzar esta duración...
CHUNK_MAX_CHARS = 700               # ...o este tamaño
LONG_PAUSE_SECONDS = 60.0           # un hueco así se reporta como pausa y corta el segmento
DURATION_TOLERANCE_SECONDS = 2.0
SAMPLE_POINTS = 10

# H:MM:SS | HH:MM:SS | M:SS | MM:SS  (la línea completa)
_TS_RE = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)\s*$")


@dataclass
class IngestIssue:
    code: str
    severity: str          # info | warning | error
    message: str
    line: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return {"code": self.code, "severity": self.severity, "line": self.line, "message": self.message}


@dataclass
class IngestReport:
    parser_version: str = PARSER_VERSION
    source_name: str = ""
    content_hash: str = ""
    encoding: str = "utf-8"
    total_lines: int = 0
    timestamp_lines: int = 0
    rejected_timestamp_like_lines: int = 0
    entries_with_text: int = 0
    segments: int = 0
    first_seconds: Optional[int] = None
    last_seconds: Optional[int] = None
    video_duration_seconds: Optional[float] = None
    capped_spans: int = 0
    long_pauses: int = 0
    median_line_seconds: Optional[float] = None
    pii_counts: Dict[str, int] = field(default_factory=dict)
    name_stats: Dict[str, int] = field(default_factory=dict)
    issues: List[IngestIssue] = field(default_factory=list)
    samples: List[Dict[str, object]] = field(default_factory=list)   # puntos distribuidos para verificación

    @property
    def errors(self) -> List[IngestIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors and self.segments > 0

    def public(self) -> Dict[str, object]:
        """Resumen SIN texto de la clase ni nombres: seguro para /health y logs."""
        by_sev: Dict[str, int] = {}
        for i in self.issues:
            by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
        return {
            "parser_version": self.parser_version, "ok": self.ok, "transcript_sha256_short": self.content_hash[:12],
            "timestamp_lines": self.timestamp_lines, "segments": self.segments,
            "first_timestamp": format_timestamp(self.first_seconds) if self.first_seconds is not None else None,
            "last_timestamp": format_timestamp(self.last_seconds) if self.last_seconds is not None else None,
            "video_duration_seconds": self.video_duration_seconds, "issues_by_severity": by_sev,
            "pii_masked": dict(self.pii_counts),
        }


@dataclass
class IngestResult:
    lesson: Optional[LessonTranscript]
    report: IngestReport
    privacy: Optional[PrivacyFilter] = None      # filtro con los nombres aprendidos: sirve también para los mensajes del estudiante


@dataclass
class _Entry:
    seconds: int
    label: str
    line_no: int
    parts: List[str] = field(default_factory=list)
    end: float = 0.0
    end_estimated: bool = False

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def parse_timestamp_line(line: str) -> Optional[int]:
    """Segundos de una línea que es SOLO una marca de tiempo; None si no lo es."""
    m = _TS_RE.match(line)
    if not m:
        return None
    hours, minutes, secs = m.group(1), int(m.group(2)), int(m.group(3))
    if hours is not None and minutes >= 60:
        return None                         # "1:75:10" no es una hora válida
    return (int(hours) if hours is not None else 0) * 3600 + minutes * 60 + secs


def _decode(raw: bytes, report: IngestReport) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            report.encoding = enc
            if enc != "utf-8-sig":
                report.issues.append(IngestIssue("codificacion_no_utf8", "warning", f"El archivo no es UTF-8; se leyó como {enc}."))
            return text
        except UnicodeDecodeError:
            continue
    report.issues.append(IngestIssue("codificacion_desconocida", "warning", "Se decodificó con reemplazos (utf-8, errors=replace)."))
    report.encoding = "utf-8-replace"
    return raw.decode("utf-8", errors="replace")


def _collect_entries(lines: List[str], video_duration: Optional[float], report: IngestReport) -> List[_Entry]:
    entries: List[_Entry] = []
    seen_text_before_first = 0
    prev_was_ts = False
    for no, line in enumerate(lines, start=1):
        stripped = line.strip()
        seconds = parse_timestamp_line(line)
        accepted = False
        if seconds is not None:
            last = entries[-1] if entries else None
            if video_duration is not None and seconds > video_duration + DURATION_TOLERANCE_SECONDS:
                report.rejected_timestamp_like_lines += 1
                report.issues.append(IngestIssue(
                    "timestamp_fuera_de_duracion", "error",
                    f"La marca '{stripped}' ({seconds} s) excede la duración del video ({video_duration:.0f} s); se trató como texto.", no))
            elif last is not None and seconds < last.seconds:
                report.rejected_timestamp_like_lines += 1
                report.issues.append(IngestIssue(
                    "timestamp_no_monotono", "warning",
                    f"La marca '{stripped}' retrocede respecto de '{last.label}'; se trató como texto (¿número pronunciado?).", no))
            elif last is not None and seconds == last.seconds:
                report.rejected_timestamp_like_lines += 1
                report.issues.append(IngestIssue(
                    "timestamp_repetido", "warning", f"La marca '{stripped}' repite la anterior; se trató como texto.", no))
            else:
                if prev_was_ts:
                    report.issues.append(IngestIssue(
                        "marca_sin_texto", "info", f"La marca '{last.label}' no tiene texto (pausa); se omitió ese renglón.", last.line_no))
                entries.append(_Entry(seconds, stripped, no))
                report.timestamp_lines += 1
                accepted = True
        if accepted:
            prev_was_ts = True
            continue
        if not stripped:
            continue
        prev_was_ts = False
        if not entries:
            seen_text_before_first += 1
            continue
        entries[-1].parts.append(stripped)
    if seen_text_before_first:
        report.issues.append(IngestIssue(
            "texto_sin_marca_inicial", "warning", f"{seen_text_before_first} línea(s) de texto antes de la primera marca; se descartaron.", 1))
    return entries


def _assign_ends(entries: List[_Entry], video_duration: Optional[float], report: IngestReport) -> None:
    for i, e in enumerate(entries):
        if i + 1 < len(entries):
            gap = entries[i + 1].seconds - e.seconds
            if gap > LONG_PAUSE_SECONDS:
                report.long_pauses += 1
                report.issues.append(IngestIssue(
                    "pausa_larga", "info", f"Hueco de {gap} s entre '{e.label}' y '{entries[i + 1].label}'.", e.line_no))
            if gap > MAX_LINE_SPAN_SECONDS:
                e.end, e.end_estimated = e.seconds + MAX_LINE_SPAN_SECONDS, True
                report.capped_spans += 1
            else:
                e.end = float(entries[i + 1].seconds)
        else:
            if video_duration is not None and video_duration > e.seconds:
                e.end = min(video_duration, e.seconds + MAX_LINE_SPAN_SECONDS)
                report.issues.append(IngestIssue(
                    "ultimo_segmento_fin_estimado", "info",
                    "El último renglón no tiene marca posterior: su fin se tomó de la duración del video (estimado).", e.line_no))
            else:
                e.end = e.seconds + 1.0
                report.issues.append(IngestIssue(
                    "ultimo_segmento_sin_duracion", "warning",
                    "El último renglón no tiene marca posterior ni se conoce la duración del video: se usó un fin mínimo de 1 s.", e.line_no))
            e.end_estimated = True


def _chunk(entries: List[_Entry], course_id: str, lesson_id: str) -> List[TranscriptSegment]:
    chunks: List[TranscriptSegment] = []
    current: List[_Entry] = []

    def close():
        if not current:
            return
        first, last = current[0], current[-1]
        chunks.append(TranscriptSegment(
            course_id=course_id, lesson_id=lesson_id, segment_id=len(chunks),
            text=" ".join(e.text for e in current), start_seconds=float(first.seconds), end_seconds=float(last.end),
            source_label=first.label, source_line=first.line_no, end_is_estimated=last.end_estimated,
            lines=[SegmentLine(start_seconds=float(e.seconds), label=e.label, text=e.text) for e in current],
        ))
        current.clear()

    for e in entries:
        if current:
            gap = e.seconds - current[-1].end
            size = sum(len(x.text) for x in current)
            if e.seconds - current[0].seconds >= CHUNK_TARGET_SECONDS or size >= CHUNK_MAX_CHARS or gap > LONG_PAUSE_SECONDS:
                close()
        current.append(e)
    close()
    return chunks


def _sample_points(entries: List[_Entry], chunks: List[TranscriptSegment], duration: Optional[float]) -> List[Dict[str, object]]:
    """Puntos distribuidos a lo largo de la clase con las comprobaciones AUTOMÁTICAS que sí son posibles."""
    if not entries:
        return []
    picks = sorted({round(i * (len(entries) - 1) / (SAMPLE_POINTS - 1)) for i in range(SAMPLE_POINTS)})
    samples = []
    for idx in picks:
        e = entries[idx]
        owner = next((c for c in chunks if c.start_seconds <= e.seconds < c.end_seconds), None)   # intervalo semiabierto [inicio, fin)
        samples.append({
            "label": e.label, "seconds": e.seconds, "line": e.line_no,
            "label_matches_seconds": parse_timestamp_line(e.label) == e.seconds,
            "inside_video_duration": duration is None or e.seconds <= duration + DURATION_TOLERANCE_SECONDS,
            "assigned_chunk": owner.segment_id if owner else None,
            "chunk_contains_text": bool(owner and e.text and e.text in owner.text),
            "excerpt": e.text[:90],
            "needs_listening": True,      # la sincronía audio-texto solo se confirma escuchando el video
        })
    return samples


def ingest_timestamped_txt(
    raw: bytes,
    course_id: str,
    lesson_id: str,
    title: str,
    video_duration: Optional[float] = None,
    extra_names: Iterable[str] = (),
    source_name: str = "transcripcion.txt",
    disclaimer: str = "",
    is_official_dmc: bool = False,
) -> IngestResult:
    report = IngestReport(source_name=source_name, content_hash=hashlib.sha256(raw).hexdigest(),
                          video_duration_seconds=video_duration)
    text = _decode(raw, report)
    lines = text.splitlines()
    report.total_lines = len(lines)

    entries = _collect_entries(lines, video_duration, report)
    entries_with_text = [e for e in entries if e.text]
    if not entries_with_text:
        report.issues.append(IngestIssue("sin_segmentos", "error", "No se encontraron marcas de tiempo con texto."))
        return IngestResult(None, report)

    # Enmascarado de datos personales: primero se aprenden los nombres de TODO el texto, luego se aplica renglón a renglón.
    privacy = PrivacyFilter(extra_names=extra_names)
    report.name_stats = privacy.learn_names(e.text for e in entries_with_text)
    for e in entries_with_text:
        masked, _ = privacy.mask(e.text)
        e.parts = [masked]
    report.pii_counts = dict(privacy.counts)

    _assign_ends(entries_with_text, video_duration, report)
    report.entries_with_text = len(entries_with_text)
    report.first_seconds, report.last_seconds = entries_with_text[0].seconds, entries_with_text[-1].seconds
    spans = [e.end - e.seconds for e in entries_with_text if not e.end_estimated]
    report.median_line_seconds = round(statistics.median(spans), 2) if spans else None

    segments = _chunk(entries_with_text, course_id, lesson_id)
    report.segments = len(segments)
    report.samples = _sample_points(entries_with_text, segments, video_duration)
    for s in report.samples:
        if not (s["label_matches_seconds"] and s["inside_video_duration"] and s["chunk_contains_text"]):
            report.issues.append(IngestIssue("muestra_inconsistente", "error",
                                             f"El punto '{s['label']}' no pasó las comprobaciones automáticas.", int(s["line"])))
    if video_duration is not None and report.last_seconds is not None:
        lag = video_duration - report.last_seconds
        severity = "info" if 0 <= lag <= 60 else "warning"
        report.issues.append(IngestIssue(
            "cobertura_final", severity, f"La última marca ({format_timestamp(report.last_seconds)}) queda a {lag:.0f} s del final del video."))

    lesson = LessonTranscript(
        course_id=course_id, lesson_id=lesson_id, title=title, source_format="txt", is_official_dmc=is_official_dmc,
        disclaimer=disclaimer, content_hash=report.content_hash, segments=segments,
    )
    return IngestResult(lesson, report, privacy)
