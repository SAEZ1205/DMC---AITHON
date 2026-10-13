"""
Lectores de transcripciones de clases.

Formatos soportados:
  * WebVTT (.vtt): cues con tiempos `[HH:]MM:SS.mmm --> [HH:]MM:SS.mmm`. Los bloques
    `NOTE` pueden llevar metadatos `clave: valor` (title, disclaimer, is_official_dmc,
    course_id, lesson_id).
  * JSON (.json): objeto con `title`, `segments[]` (`start_seconds`, `end_seconds`, `text`)
    y metadatos opcionales.

El `course_id` y el `lesson_id` se toman de la estructura de carpetas
(`transcripts/<course_id>/<lesson_id>.vtt|json`); si el archivo también los declara,
deben coincidir.
"""
import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import ValidationError

from app.models.transcript import LessonTranscript, TranscriptSegment

_TIME = r"(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
_TIMING_RE = re.compile(rf"^\s*({_TIME})\s*-->\s*({_TIME})(?:\s+.*)?$")
_TAG_RE = re.compile(r"<[^>]+>")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_META_KEYS = {"title", "disclaimer", "is_official_dmc", "course_id", "lesson_id"}


class TranscriptParseError(ValueError):
    """La transcripción no tiene un formato válido."""


def parse_timestamp(value: str) -> float:
    """Convierte `[HH:]MM:SS[.mmm]` a segundos."""
    value = value.strip().replace(",", ".")
    seconds_part, _, fraction = value.partition(".")
    parts = seconds_part.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise TranscriptParseError(f"Timestamp inválido: {value!r}")
    nums = [int(p) for p in parts]
    if nums[-1] >= 60 or (len(nums) == 3 and nums[1] >= 60):
        raise TranscriptParseError(f"Timestamp fuera de rango: {value!r}")
    total = 0
    for n in nums:
        total = total * 60 + n
    millis = int(fraction.ljust(3, "0")) if fraction else 0
    return total + millis / 1000.0


def _validate_ids(course_id: str, lesson_id: str) -> None:
    for label, value in (("course_id", course_id), ("lesson_id", lesson_id)):
        if not _ID_RE.match(value):
            raise TranscriptParseError(
                f"{label} inválido {value!r}: use minúsculas, números, '-' o '_'"
            )


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "si", "sí", "yes"}


def _check_declared_id(label: str, declared: Optional[str], expected: str) -> None:
    if declared and declared != expected:
        raise TranscriptParseError(
            f"{label} declarado ({declared!r}) no coincide con el esperado ({expected!r})"
        )


def _clean_cue_text(lines: List[str]) -> str:
    text = " ".join(lines)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_vtt(content: str, course_id: str, lesson_id: str) -> LessonTranscript:
    """Parsea el texto de un archivo WebVTT."""
    _validate_ids(course_id, lesson_id)
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not content.startswith("WEBVTT"):
        raise TranscriptParseError("El archivo VTT debe comenzar con 'WEBVTT'")

    blocks = re.split(r"\n{2,}", content.strip("\n"))
    meta: Dict[str, str] = {}
    segments: List[TranscriptSegment] = []

    for block in blocks[1:]:  # blocks[0] es la cabecera WEBVTT
        lines = [ln for ln in block.split("\n")]
        first = lines[0].strip()
        if first == "NOTE" or first.startswith("NOTE "):
            note_lines = lines[1:] if first == "NOTE" else [first[5:]] + lines[1:]
            for ln in note_lines:
                key, sep, val = ln.partition(":")
                if sep and key.strip().lower() in _META_KEYS:
                    meta[key.strip().lower()] = val.strip()
            continue
        if first.startswith(("STYLE", "REGION")):
            continue

        # Un cue puede tener una línea de identificador antes del tiempo.
        timing_idx = next(
            (i for i, ln in enumerate(lines[:2]) if "-->" in ln), None
        )
        if timing_idx is None:
            raise TranscriptParseError(f"Bloque VTT sin línea de tiempo: {block[:60]!r}")
        match = _TIMING_RE.match(lines[timing_idx])
        if not match:
            raise TranscriptParseError(f"Línea de tiempo inválida: {lines[timing_idx]!r}")
        text = _clean_cue_text(lines[timing_idx + 1:])
        if not text:
            continue  # cue vacío: se ignora
        try:
            segments.append(
                TranscriptSegment(
                    course_id=course_id,
                    lesson_id=lesson_id,
                    segment_id=len(segments),
                    text=text,
                    start_seconds=parse_timestamp(match.group(1)),
                    end_seconds=parse_timestamp(match.group(2)),
                )
            )
        except ValidationError as exc:
            raise TranscriptParseError(f"Cue inválido ({lines[timing_idx]!r}): {exc.errors()[0]['msg']}") from exc

    _check_declared_id("course_id", meta.get("course_id"), course_id)
    _check_declared_id("lesson_id", meta.get("lesson_id"), lesson_id)
    if not segments:
        raise TranscriptParseError("La transcripción VTT no contiene cues con texto")

    return LessonTranscript(
        course_id=course_id,
        lesson_id=lesson_id,
        title=meta.get("title") or lesson_id,
        source_format="vtt",
        is_official_dmc=_as_bool(meta.get("is_official_dmc", "false")),
        disclaimer=meta.get("disclaimer", ""),
        segments=segments,
    )


def parse_json_transcript(
    data: Union[str, Dict[str, Any]], course_id: str, lesson_id: str
) -> LessonTranscript:
    """Parsea una transcripción en JSON (texto u objeto ya cargado)."""
    _validate_ids(course_id, lesson_id)
    if isinstance(data, str):
        try:
            data = json.loads(data.lstrip("\ufeff"))
        except json.JSONDecodeError as exc:
            raise TranscriptParseError(f"JSON inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise TranscriptParseError("El JSON de la transcripción debe ser un objeto")

    _check_declared_id("course_id", data.get("course_id"), course_id)
    _check_declared_id("lesson_id", data.get("lesson_id"), lesson_id)

    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise TranscriptParseError("El JSON debe incluir una lista 'segments' no vacía")

    segments: List[TranscriptSegment] = []
    for idx, raw in enumerate(raw_segments):
        try:
            segments.append(
                TranscriptSegment(
                    course_id=course_id,
                    lesson_id=lesson_id,
                    segment_id=idx,
                    text=str(raw["text"]).strip(),
                    start_seconds=float(raw["start_seconds"]),
                    end_seconds=float(raw["end_seconds"]),
                )
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise TranscriptParseError(f"Segmento {idx} inválido: {exc}") from exc

    return LessonTranscript(
        course_id=course_id,
        lesson_id=lesson_id,
        title=str(data.get("title") or lesson_id),
        source_format="json",
        is_official_dmc=bool(data.get("is_official_dmc", False)),
        disclaimer=str(data.get("disclaimer", "")),
        segments=segments,
    )


def load_transcript_file(path: Path, course_id: str) -> LessonTranscript:
    """Carga un archivo .vtt o .json; el lesson_id es el nombre del archivo sin extensión."""
    lesson_id = path.stem
    content = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".vtt":
        return parse_vtt(content, course_id, lesson_id)
    if suffix == ".json":
        return parse_json_transcript(content, course_id, lesson_id)
    raise TranscriptParseError(f"Extensión no soportada: {path.suffix}")
