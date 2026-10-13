"""
Resumen, capítulos y "lo que te perdiste" a partir de las transcripciones.

Todo es determinista y extractivo (frases textuales + tiempos reales de los segmentos); nunca se llama a un LLM
aquí. La generación de resúmenes con LLM está PREPARADA (parámetro `engine=llm`) pero deshabilitada: no hay
ninguna llamada facturable en esta etapa.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.knowledge.lesson_analysis import (
    Chapter, ConceptHit, analyze_segments, build_chapters, build_overview, key_concepts, key_sentence,
    normalize_intervals, segment_overlaps, topic_defs, validate_intervals,
)
from app.knowledge.retriever import KnowledgeRetriever
from app.models.academic import (
    ChapterInfo, ChaptersResponse, KeyConcept, LessonSummaryResponse, MissedConcept, MissedContentRequest, SummaryCacheInfo,
    MissedContentResponse, MissedPoint, MissedSegment, NormalizedInterval, SegmentRef, SummarySection,
)
from app.models.chat import CitationRef
from app.models.events import TELEMETRY_NOTICE, TelemetryEventType
from app.models.transcript import LessonTranscript, SegmentMatch, TranscriptSegment, format_timestamp
from app.services.errors import AppError, CourseNotFound, LessonNotFound
from app.services.summary_service import StoredSummary, SummaryService
from app.services.telemetry_service import TelemetryService

DEMO_NOTICE = (
    "Modo DEMO: el contenido proviene de transcripciones FICTICIAS escritas para el MVP; "
    "no es contenido oficial de DMC Institute."
)
ATTENDANCE_NOTICE = (
    "Los intervalos de ausencia de este endpoint son SIMULADOS (ingresados en la solicitud). No provienen de Zoom ni de "
    "ningún registro real de asistencia, y esta respuesta no modifica ni calcula asistencia institucional."
)
SUMMARY_METHOD_NOTE = (
    "Resumen extractivo y determinista: cada punto es una frase textual de la transcripción, con su minuto. "
    "No se ha medido cuánto tiempo ahorra y no sustituye ver la clase."
)
CHAPTERS_METHOD_NOTE = (
    "Capítulos derivados de la transcripción: un capítulo nuevo empieza cuando un segmento introduce un tema aún no visto. "
    "Los tiempos son los de los segmentos; no se modifica ningún video."
)
LLM_SUMMARY_NOT_GENERATED = (
    "El resumen con LLM es una acción administrativa explícita y todavía no se ha generado para esta versión de la "
    "transcripción: se devolvió el resumen heurístico (modo DEMO), sin ninguna llamada a un proveedor externo."
)
HEURISTIC_METHOD_NOTE = (
    "Resumen heurístico DEMO (sin modelo de lenguaje): capítulos por ventanas de ~10 min, títulos con los términos más "
    "distintivos de cada ventana y una frase textual representativa con su minuto. No se ha medido cuánto tiempo ahorra "
    "y no sustituye ver la clase."
)
MAX_KEY_POINTS = 12


class LessonService:
    def __init__(self, retriever: KnowledgeRetriever, telemetry: TelemetryService, summaries: Optional[SummaryService] = None):
        self.retriever = retriever
        self.telemetry = telemetry
        self.summaries = summaries

    # ------------------------------------------------------------ utilidades
    def _lesson(self, course_id: str, lesson_id: str) -> LessonTranscript:
        if self.retriever.get_course(course_id) is None and not self.retriever.list_lessons(course_id):
            raise CourseNotFound(course_id)
        lesson = self.retriever.get_lesson(course_id, lesson_id)
        if lesson is None:
            raise LessonNotFound(course_id, lesson_id)
        return lesson

    def _analysis(self, lesson: LessonTranscript):
        topics = topic_defs(self.retriever.get_course(lesson.course_id))
        hits = analyze_segments(lesson, topics)
        return topics, hits, build_chapters(lesson, topics, hits)

    def _citations(self, lesson: LessonTranscript, segments: List[TranscriptSegment],
                   focus_seconds: Optional[float] = None) -> List[CitationRef]:
        matches = [SegmentMatch(segment=s, lesson_title=lesson.title, score=1.0, matched_terms=[], focus_seconds=focus_seconds)
                   for s in segments]
        return self.retriever.build_segment_citations(matches)

    @staticmethod
    def _notice(lesson: LessonTranscript) -> str:
        return "" if lesson.is_official_dmc else DEMO_NOTICE

    @staticmethod
    def _seg_ref(seg: TranscriptSegment) -> SegmentRef:
        return SegmentRef(segment_id=seg.segment_id, start_seconds=seg.start_seconds,
                          end_seconds=seg.end_seconds, timestamp=seg.timestamp_range)

    # ---------------------------------------------------------------- capítulos
    def chapters(self, course_id: str, lesson_id: str) -> ChaptersResponse:
        lesson = self._lesson(course_id, lesson_id)
        _, _, chapters = self._analysis(lesson)
        return ChaptersResponse(
            course_id=course_id, lesson_id=lesson_id, lesson_title=lesson.title,
            duration_seconds=lesson.duration_seconds,
            chapters=[self._chapter_info(lesson, ch) for ch in chapters],
            method_note=CHAPTERS_METHOD_NOTE, content_notice=self._notice(lesson),
        )

    def _chapter_info(self, lesson: LessonTranscript, ch: Chapter) -> ChapterInfo:
        return ChapterInfo(
            chapter_id=ch.chapter_id, title=ch.title, start_seconds=ch.start_seconds, end_seconds=ch.end_seconds,
            timestamp=f"{format_timestamp(ch.start_seconds)}–{format_timestamp(ch.end_seconds)}",
            description=ch.description, segment_ids=[s.segment_id for s in ch.segments], concepts=ch.concepts,
            references=self._citations(lesson, ch.reference_segments or ch.segments, ch.focus_seconds),
        )

    # ------------------------------------------------------------------ resumen
    def summary(self, course_id: str, lesson_id: str, *, engine: str = "extractive", student_id: Optional[str] = None,
                idem_scope: Optional[str] = None) -> LessonSummaryResponse:
        """
        Devuelve el resumen PERSISTIDO de la clase (se genera una sola vez por versión de la transcripción).
          engine=extractive : siempre el heurístico DEMO.
          engine=best       : el de Kimi si ya fue generado para la versión vigente; si no, el heurístico.
          engine=llm        : igual que best, pero avisa si todavía no existe el de Kimi.
        Esta ruta NUNCA llama a un LLM: el resumen con Kimi se genera por una acción administrativa explícita.
        """
        if engine not in ("extractive", "llm", "best"):
            raise AppError(422, "invalid_engine", "engine debe ser 'extractive', 'llm' o 'best'.")
        lesson = self._lesson(course_id, lesson_id)
        stored, notice = self._resolve_summary(lesson, engine)

        response = LessonSummaryResponse(
            **stored.payload, provider_notice=notice, telemetry_notice=TELEMETRY_NOTICE,
            cache=SummaryCacheInfo(cached=stored.cached, generated_at=stored.generated_at, engine=stored.engine,
                                   pipeline_version=stored.pipeline_version, transcript_sha256_short=stored.fingerprint[:12]),
        )
        self.telemetry.record(
            TelemetryEventType.LESSON_SUMMARY_REQUESTED, response.mode,
            {"engine": stored.engine, "section_count": len(response.sections), "concept_count": len(response.key_concepts)},
            student_id=student_id, course_id=course_id, lesson_id=lesson_id, idem_scope=idem_scope, best_effort=True,
        )
        return response

    def _resolve_summary(self, lesson: LessonTranscript, engine: str) -> Tuple[StoredSummary, Optional[str]]:
        notice = None
        if engine in ("llm", "best"):
            kimi = self.summaries.peek(lesson, "kimi") if self.summaries else None
            if kimi:
                return kimi, None
            notice = LLM_SUMMARY_NOT_GENERATED if engine == "llm" else None
        if self.summaries is None:                       # sin persistencia configurada (uso aislado): se calcula al vuelo
            payload, meta = self._extractive_payload(lesson)
            return StoredSummary(payload, meta, "extractive", "", "n/a", "", cached=False), notice
        return self.summaries.get_or_create(lesson, "extractive", lambda: self._extractive_payload(lesson)), notice

    def ensure_extractive(self, course_id: str, lesson_id: str) -> StoredSummary:
        """Genera (una sola vez) el resumen heurístico de una clase; lo usa el procesamiento al arrancar."""
        lesson = self._lesson(course_id, lesson_id)
        return self.summaries.get_or_create(lesson, "extractive", lambda: self._extractive_payload(lesson))

    def extractive_skeleton(self, lesson: LessonTranscript):
        """(capítulos, conceptos) del análisis heurístico: base del resumen y de las ventanas del resumen con LLM."""
        _, hits, chapters = self._analysis(lesson)
        return chapters, key_concepts(lesson, hits), hits

    def _extractive_payload(self, lesson: LessonTranscript) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        chapters, concepts, _ = self.extractive_skeleton(lesson)
        windowed = any(ch.reference_segments for ch in chapters)
        sections = [
            SummarySection(
                chapter_id=ch.chapter_id, title=ch.title, start_seconds=ch.start_seconds, end_seconds=ch.end_seconds,
                timestamp=f"{format_timestamp(ch.start_seconds)}–{format_timestamp(ch.end_seconds)}",
                key_point=ch.description, segment_ids=[s.segment_id for s in ch.segments],
                references=self._citations(lesson, ch.reference_segments or ch.segments, ch.focus_seconds),
            )
            for ch in chapters
        ]
        by_id = {s.segment_id: s for s in lesson.segments}
        used_ids = sorted({r_id for ch in chapters for r_id in
                           ([s.segment_id for s in ch.reference_segments] if ch.reference_segments else [s.segment_id for s in ch.segments])})
        used = [by_id[i] for i in used_ids]
        response = LessonSummaryResponse(
            course_id=lesson.course_id, lesson_id=lesson.lesson_id, lesson_title=lesson.title,
            duration_seconds=lesson.duration_seconds, duration_label=format_timestamp(lesson.duration_seconds),
            mode="DEMO", engine="extractive", provider="demo", generated_by="extractive-heuristic/1.0",
            overview=build_overview(lesson, chapters, concepts), sections=sections,
            key_concepts=[KeyConcept(concept=c.concept, kind=c.kind, first_seconds=c.first_seconds,
                                     first_minute=format_timestamp(c.first_seconds), mentions=c.mentions,
                                     segment_ids=c.segment_ids[:50]) for c in concepts],
            segments_used=[self._seg_ref(s) for s in used], references=self._citations(lesson, used),
            method_note=HEURISTIC_METHOD_NOTE if windowed else SUMMARY_METHOD_NOTE, content_notice=self._notice(lesson),
        )
        payload = response.model_dump(mode="json", exclude={"cache", "provider_notice", "telemetry_notice"})
        return payload, {"generator": "extractive-heuristic/1.0", "chapters": len(sections), "llm_calls": 0}

    # ------------------------------------------------------------ lo que te perdiste
    def missed_content(self, req: MissedContentRequest, *, idem_scope: Optional[str] = None) -> MissedContentResponse:
        lesson = self._lesson(req.course_id, req.lesson_id)
        duration = lesson.duration_seconds
        raw = [(iv.start_seconds, iv.end_seconds) for iv in req.absence_intervals]

        problems = validate_intervals(raw, duration)
        if problems:
            raise AppError(
                422, "invalid_absence_intervals",
                f"Hay {len(problems)} intervalo(s) de ausencia inválido(s). Los segundos deben estar entre 0 y {duration:g}.",
                details=[{"index": p.index, "reason": p.reason, "message": p.message} for p in problems],
            )
        intervals, notes = normalize_intervals(raw)
        overlaps = segment_overlaps(lesson, intervals)

        topics = topic_defs(self.retriever.get_course(req.course_id))
        hits = analyze_segments(lesson, topics)

        missed_segments: List[MissedSegment] = []
        concept_index: dict = {}
        key_points: List[MissedPoint] = []
        for ov in overlaps:
            seg = ov.segment
            labels = [h.label for h in hits[seg.segment_id]]
            missed_segments.append(MissedSegment(
                segment_id=seg.segment_id, start_seconds=seg.start_seconds, end_seconds=seg.end_seconds,
                timestamp=seg.timestamp_range, overlap_start_seconds=ov.overlap_start, overlap_end_seconds=ov.overlap_end,
                overlap_seconds=ov.overlap_seconds, partially_missed=ov.partial, text=seg.text, concepts=labels,
            ))
            for label in labels:
                entry = concept_index.setdefault(label, {"first": ov.overlap_start, "ids": []})
                entry["ids"].append(seg.segment_id)
            key_points.append(MissedPoint(timestamp=seg.timestamp_range, text=key_sentence([seg])))

        concepts_missed = [
            MissedConcept(concept=label, first_seconds=e["first"], first_minute=format_timestamp(e["first"]), segment_ids=e["ids"])
            for label, e in concept_index.items()
        ]
        total = round(sum(b - a for a, b in intervals), 3)
        response = MissedContentResponse(
            student_id=req.student_id, course_id=req.course_id, lesson_id=req.lesson_id, lesson_title=lesson.title,
            mode="DEMO",
            normalized_intervals=[
                NormalizedInterval(start_seconds=a, end_seconds=b, start_label=format_timestamp(a),
                                   end_label=format_timestamp(b), duration_seconds=round(b - a, 3))
                for a, b in intervals
            ],
            normalization_notes=notes, total_missed_seconds=total, missed_segments=missed_segments,
            concepts_missed=concepts_missed,
            explanation=self._explain(lesson, intervals, missed_segments, concepts_missed),
            key_points=key_points[:MAX_KEY_POINTS],
            references=self._citations(lesson, [ov.segment for ov in overlaps]),
            attendance_notice=ATTENDANCE_NOTICE, content_notice=self._notice(lesson),
            telemetry_notice=TELEMETRY_NOTICE,
        )
        self.telemetry.record(
            TelemetryEventType.MISSED_CONTENT_REQUESTED, "DEMO",
            {"interval_count": len(intervals), "total_missed_seconds": total, "segment_count": len(missed_segments),
             "concepts": [c.concept for c in concepts_missed]},
            student_id=req.student_id, course_id=req.course_id, lesson_id=req.lesson_id, idem_scope=idem_scope,
            best_effort=True,
        )
        return response

    @staticmethod
    def _explain(lesson, intervals: List[Tuple[float, float]], segments: List[MissedSegment],
                 concepts: List[MissedConcept]) -> str:
        if not segments:
            return ("En los intervalos indicados no hay contenido de transcripción para esta clase, "
                    "así que no se perdió ningún fragmento registrado.")
        span = "; ".join(f"{format_timestamp(a)}–{format_timestamp(b)}" for a, b in intervals)
        text = f"En {'el intervalo' if len(intervals) == 1 else 'los intervalos'} {span} de «{lesson.title}» se explicó lo siguiente."
        temas = [c.concept for c in concepts]
        if temas:
            text += f" Conceptos que pudiste perderte: {', '.join(temas)}."
        partial = [s.timestamp for s in segments if s.partially_missed]
        if partial:
            text += f" Solo se perdió una parte de los fragmentos {', '.join(partial)}."
        return text
