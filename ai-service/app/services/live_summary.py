"""
Resumen de clase con Kimi POR FRAGMENTOS (map-reduce). Solo se ejecuta por una acción administrativa explícita.

  * Nunca se envía la transcripción completa: cada llamada recibe UN capítulo/ventana (~10 min, unos 8 000 caracteres).
  * Una llamada final "reduce" recibe únicamente los resúmenes ya verificados de cada ventana (como pseudo-segmentos con su
    rango de minutos), no el texto original.
  * Cada resumen pasa por la misma verificación que las respuestas del tutor: etiquetas válidas, minutos solo del backend,
    sin fugas del prompt, enlaces ni afirmaciones sensibles sin respaldo.
  * Todo o nada: si una llamada falla, no se guarda un resumen parcial.
  * Costo aproximado: una llamada por capítulo + una de reducción (la clase real de ~3,5 h: ~21 llamadas). Aun así, solo
    se ejecuta con LLM_LIVE_AUTHORIZED=true y respeta el tope LLM_MAX_LIVE_CALLS.
"""
import logging
from typing import Any, Dict, List, Tuple

from app.llm.kimi_provider import KimiProvider, SummaryPiece
from app.models.academic import KeyConcept, LessonSummaryResponse, SegmentRef, SummarySection
from app.models.transcript import LessonTranscript, SegmentMatch, TranscriptSegment, format_timestamp
from app.services.lesson_service import LessonService

logger = logging.getLogger(__name__)

MAX_SEGMENTS_PER_CALL = 20           # tope por llamada (≈ 10 min de clase)
MAX_CHARS_PER_CALL = 12000
WINDOW_INSTRUCTION = "Resume este fragmento de la clase en 3 a 5 ideas, citando los segmentos."
REDUCE_INSTRUCTION = (
    "Cada segmento es el resumen de un capítulo de la clase. Redacta un resumen general de la clase completa en 4 a 6 "
    "ideas, citando los capítulos."
)
LIVE_METHOD_NOTE = (
    "Resumen generado con Kimi por fragmentos (un capítulo por llamada) y verificado: los minutos los inserta el backend a "
    "partir de los segmentos citados. Un modelo puede parafrasear mal un fragmento; contrasta con el minuto indicado."
)


def _chunks(segments: List[TranscriptSegment]) -> List[List[TranscriptSegment]]:
    """Reparte los segmentos de un capítulo en grupos que respeten el tope de tamaño por llamada."""
    groups, current, size = [], [], 0
    for seg in segments:
        if current and (len(current) >= MAX_SEGMENTS_PER_CALL or size + len(seg.text) > MAX_CHARS_PER_CALL):
            groups.append(current)
            current, size = [], 0
        current.append(seg)
        size += len(seg.text)
    if current:
        groups.append(current)
    return groups


class LiveSummaryGenerator:
    def __init__(self, provider: KimiProvider, lessons: LessonService):
        self.provider, self.lessons = provider, lessons

    def generate(self, lesson: LessonTranscript) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        chapters, concepts, _ = self.lessons.extractive_skeleton(lesson)
        calls, tokens = 0, 0
        sections: List[SummarySection] = []
        pseudo: List[SegmentMatch] = []

        def tally(piece: SummaryPiece) -> None:
            nonlocal calls, tokens
            calls += 1
            tokens += int((piece.usage or {}).get("total_tokens") or 0)

        for ch in chapters:
            texts: List[str] = []
            cited: List[TranscriptSegment] = []
            for group in _chunks(ch.segments):
                evidence = [SegmentMatch(segment=s, lesson_title=lesson.title, score=1.0, matched_terms=[]) for s in group]
                piece = self.provider.summarize_segments(ch.title, evidence, WINDOW_INSTRUCTION)
                tally(piece)
                texts.append(piece.text)
                cited += [evidence[i].segment for i in piece.cited_indices]
            window_text = " ".join(texts)
            sections.append(SummarySection(
                chapter_id=ch.chapter_id, title=ch.title, start_seconds=ch.start_seconds, end_seconds=ch.end_seconds,
                timestamp=f"{format_timestamp(ch.start_seconds)}–{format_timestamp(ch.end_seconds)}",
                key_point=window_text, segment_ids=[s.segment_id for s in ch.segments],
                references=self.lessons._citations(lesson, cited or ch.segments[:1]),
            ))
            pseudo.append(SegmentMatch(
                segment=TranscriptSegment(
                    course_id=lesson.course_id, lesson_id=lesson.lesson_id, segment_id=len(pseudo), text=window_text,
                    start_seconds=ch.start_seconds, end_seconds=ch.end_seconds),
                lesson_title=lesson.title, score=1.0, matched_terms=[]))

        overview_piece = self.provider.summarize_segments(lesson.title, pseudo, REDUCE_INSTRUCTION)
        tally(overview_piece)

        by_id = {s.segment_id: s for s in lesson.segments}
        used_ids = sorted({sid for sec in sections for r in sec.references for sid in [self._segment_id(lesson, r.start_seconds)]
                           if sid is not None})
        used = [by_id[i] for i in used_ids]
        response = LessonSummaryResponse(
            course_id=lesson.course_id, lesson_id=lesson.lesson_id, lesson_title=lesson.title,
            duration_seconds=lesson.duration_seconds, duration_label=format_timestamp(lesson.duration_seconds),
            mode="LIVE", engine="kimi", provider="kimi", generated_by=f"kimi:{self.provider.config.model}",
            overview=overview_piece.text, sections=sections,
            key_concepts=[KeyConcept(concept=c.concept, kind=c.kind, first_seconds=c.first_seconds,
                                     first_minute=format_timestamp(c.first_seconds), mentions=c.mentions,
                                     segment_ids=c.segment_ids[:50]) for c in concepts],
            segments_used=[SegmentRef(segment_id=s.segment_id, start_seconds=s.start_seconds, end_seconds=s.end_seconds,
                                      timestamp=s.timestamp_range) for s in used],
            references=self.lessons._citations(lesson, used),
            method_note=LIVE_METHOD_NOTE, content_notice=self.lessons._notice(lesson),
        )
        meta = {"generator": f"kimi:{self.provider.config.model}", "llm_calls": calls, "total_tokens": tokens,
                "chapters": len(sections)}
        logger.info("Resumen Kimi generado: %d llamadas, %d tokens", calls, tokens)
        return response.model_dump(mode="json", exclude={"cache", "provider_notice", "telemetry_notice"}), meta

    @staticmethod
    def _segment_id(lesson: LessonTranscript, start_seconds: float):
        return next((s.segment_id for s in lesson.segments if s.start_seconds == start_seconds), None)
