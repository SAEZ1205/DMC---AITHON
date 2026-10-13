import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from app.models.chat import CourseInfo, CourseTopicInfo, CitationRef
from app.models.transcript import LessonTranscript, SegmentMatch, TranscriptSegment
from app.knowledge.text_utils import (
    STOPWORDS, bigrams, contains_phrase, content_stems, content_tokens, stem, tokenize, unique,
)
from app.knowledge.transcripts import TranscriptParseError, load_transcript_file

logger = logging.getLogger(__name__)

COURSES_DIR = Path(__file__).resolve().parent / "courses"
TRANSCRIPTS_DIR = Path(__file__).resolve().parent / "transcripts"

# --- Umbrales de relevancia -------------------------------------------------
# Temas (fichas del curso)
MIN_TOPIC_SCORE = 8.0          # exige frase de keyword/título o al menos dos coincidencias parciales
TOPIC_RELATIVE_CUTOFF = 0.4    # descarta temas con < 40 % de la puntuación del mejor
# Segmentos de transcripción
MIN_QUERY_COVERAGE = 0.5       # con 2+ términos, al menos la mitad debe existir en el material
MIN_PARTIAL_SCORE = 1.5        # si la cobertura es parcial, el mejor segmento debe superar este piso
SEGMENT_RELATIVE_CUTOFF = 0.35
BM25_K1 = 1.2
PHRASE_BONUS = 1.5             # por cada par de términos consecutivos de la consulta presente en el segmento
# Coincidencia LITERAL de frases de la pregunta (n-gramas de 3 a 6 palabras, stopwords incluidos): si el docente dijo
# casi las mismas palabras ("¿Qué es N8N?", "qué procesos conviene automatizar"), ese segmento es el mejor candidato.
NGRAM_MIN, NGRAM_MAX, NGRAM_BONUS_PER_TOKEN = 3, 6, 1.5
# Palabras "marco" de una pregunta (no describen el tema): pesan poco y no cuentan para la cobertura.
FRAME_STEMS = frozenset(stem(w) for w in ("sirve", "sirven", "significa", "significan", "define", "defines", "definicion"))
FRAME_WEIGHT = 0.3


@dataclass
class _IndexedSegment:
    lesson: LessonTranscript
    index: int
    tf: Counter = field(default_factory=Counter)
    pairs: set = field(default_factory=set)
    norm: str = ""                 # texto normalizado (" palabra palabra ") para buscar frases literales


class KnowledgeRetriever:
    """
    Motor local de recuperación documental.

    Gestiona dos tipos de material, siempre aislados por `course_id`:
      * Temas estructurados del curso (`courses/*.json`)  -> `retrieve()`
      * Transcripciones de clases con timestamps (`transcripts/<curso>/<clase>.vtt|json`)
        -> `retrieve_segments()`, que además puede acotarse a una única clase (`lesson_id`).

    La consulta y el contenido se normalizan (minúsculas, sin acentos, sin palabras vacías,
    stemming ligero) y se emparejan por término completo, nunca por subcadena. Si el
    material no contiene términos de la consulta, se devuelve una lista vacía en lugar de
    "el menos malo", para no citar contenido no relacionado.
    """
    def __init__(self, courses_dir: Optional[Path] = None, transcripts_dir: Optional[Path] = None):
        self._courses_dir = Path(courses_dir) if courses_dir is not None else COURSES_DIR
        self._transcripts_dir = Path(transcripts_dir) if transcripts_dir is not None else TRANSCRIPTS_DIR
        self._courses: Dict[str, Dict[str, Any]] = {}
        self._lessons: Dict[str, Dict[str, LessonTranscript]] = {}
        self._segment_index: Dict[Tuple[str, str], List[_IndexedSegment]] = {}
        self.load_errors: List[str] = []
        self._load_courses()
        self._load_transcripts()

    # ------------------------------------------------------------------ carga
    def _load_courses(self):
        """Carga todos los cursos disponibles en el directorio."""
        if not self._courses_dir.exists():
            return

        for filepath in sorted(self._courses_dir.glob("*.json")):
            try:
                with open(filepath, "r", encoding="utf-8-sig") as f:  # tolera BOM
                    data = json.load(f)
                    course_id = data.get("course_id")
                    if course_id:
                        self._courses[course_id] = data
            except Exception as e:
                self._record_error(f"Error cargando curso {filepath.name}: {e}")

    def _load_transcripts(self):
        """Carga `transcripts/<course_id>/<lesson_id>.(vtt|json)`; los archivos inválidos se omiten y se registran."""
        if not self._transcripts_dir.exists():
            return

        for course_dir in sorted(p for p in self._transcripts_dir.iterdir() if p.is_dir()):
            course_id = course_dir.name
            files = sorted(p for p in course_dir.iterdir() if p.suffix.lower() in (".vtt", ".json"))
            for filepath in files:
                try:
                    lesson = load_transcript_file(filepath, course_id)
                except (TranscriptParseError, OSError, UnicodeDecodeError) as e:
                    self._record_error(f"Error cargando transcripción {course_id}/{filepath.name}: {e}")
                    continue
                if lesson.lesson_id in self._lessons.get(course_id, {}):
                    self._record_error(
                        f"Transcripción duplicada {course_id}/{lesson.lesson_id} ({filepath.name}); se conserva la primera"
                    )
                    continue
                self._add_lesson(lesson)

    def _record_error(self, message: str):
        self.load_errors.append(message)
        logger.warning("[KnowledgeRetriever] %s", message)

    def register_lesson(self, lesson: LessonTranscript) -> None:
        """Agrega (o reemplaza) una lección ya construida, p. ej. la clase real ingerida desde un TXT privado."""
        self._add_lesson(lesson)

    def register_course(self, course_id: str, course_name: str, description: str = "", disclaimer: str = "",
                        is_official_dmc: bool = False) -> None:
        """Registra un curso sin fichas de tema (solo transcripciones)."""
        self._courses.setdefault(course_id, {
            "course_id": course_id, "course_name": course_name, "description": description,
            "is_official_dmc": is_official_dmc, "disclaimer": disclaimer, "topics": [],
        })

    def lesson_vocabulary(self, course_id: str, lesson_id: Optional[str] = None) -> Dict[str, int]:
        """{raíz: nº de segmentos que la contienen} para la clase (o todo el curso). Sirve al guardián de pertinencia."""
        df: Counter = Counter()
        lessons = [lesson_id] if lesson_id else list(self._lessons.get(course_id, {}))
        for lid in lessons:
            for seg in self._segment_index.get((course_id, lid), []):
                df.update(seg.tf.keys())
        return dict(df)

    def segment_count(self, course_id: str, lesson_id: Optional[str] = None) -> int:
        lessons = [lesson_id] if lesson_id else list(self._lessons.get(course_id, {}))
        return sum(len(self._segment_index.get((course_id, lid), [])) for lid in lessons)

    @staticmethod
    def _focus(segment: TranscriptSegment, stems_matched: List[str]):
        """Renglón con más raíces coincidentes (el primero en empate) y el texto desde ese renglón (~380 caracteres)."""
        if not segment.lines:
            return None, None
        wanted = set(stems_matched)
        best_idx, best_score = 0, -1
        for i, line in enumerate(segment.lines):
            score = len(wanted & set(content_stems(line.text)))
            if score > best_score:
                best_idx, best_score = i, score
        text, size = [], 0
        for line in segment.lines[best_idx:]:
            text.append(line.text)
            size += len(line.text) + 1
            if size >= 380:
                break
        return segment.lines[best_idx].start_seconds, " ".join(text)

    def _add_lesson(self, lesson: LessonTranscript):
        self._lessons.setdefault(lesson.course_id, {})[lesson.lesson_id] = lesson
        indexed = []
        for i, seg in enumerate(lesson.segments):
            stems = content_stems(seg.text)
            indexed.append(_IndexedSegment(
                lesson=lesson, index=i, tf=Counter(stems), pairs=set(bigrams(stems)),
                norm=" " + " ".join(tokenize(seg.text)) + " ",
            ))
        self._segment_index[(lesson.course_id, lesson.lesson_id)] = indexed

    # --------------------------------------------------------------- catálogo
    def list_courses(self) -> List[CourseInfo]:
        """Devuelve la lista de cursos disponibles con sus tópicos."""
        results = []
        for c in self._courses.values():
            topics = [
                CourseTopicInfo(
                    id=t["id"],
                    title=t["title"],
                    summary=t["summary"],
                    reference=t["reference"]
                )
                for t in c.get("topics", [])
            ]
            results.append(
                CourseInfo(
                    course_id=c["course_id"],
                    course_name=c["course_name"],
                    description=c.get("description", ""),
                    is_official_dmc=c.get("is_official_dmc", False),
                    disclaimer=c.get("disclaimer", ""),
                    topics=topics
                )
            )
        return results

    def get_course(self, course_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene un curso específico por su identificador."""
        return self._courses.get(course_id)

    def list_lessons(self, course_id: str) -> List[LessonTranscript]:
        """Clases con transcripción disponibles para un curso (vacío si no hay)."""
        return list(self._lessons.get(course_id, {}).values())

    def get_lesson(self, course_id: str, lesson_id: str) -> Optional[LessonTranscript]:
        return self._lessons.get(course_id, {}).get(lesson_id)

    # ------------------------------------------------- recuperación de temas
    def retrieve(self, course_id: str, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        """
        Recupera las fichas de tema más relevantes para una consulta dentro del curso.
        Devuelve [] si ningún tema alcanza la relevancia mínima.
        """
        course = self._courses.get(course_id)
        if not course:
            return []

        query_seq = content_stems(query)          # secuencia con contenido, en orden
        query_set = set(query_seq)
        if not query_set:
            return []

        scored_topics = []
        for topic in course.get("topics", []):
            score = self._score_topic(topic, query_seq, query_set)
            if score >= MIN_TOPIC_SCORE:
                scored_topics.append((score, topic))

        if not scored_topics:
            return []
        scored_topics.sort(key=lambda x: x[0], reverse=True)
        best = scored_topics[0][0]
        kept = [t for s, t in scored_topics if s >= best * TOPIC_RELATIVE_CUTOFF]
        return kept[:top_k]

    @staticmethod
    def _score_topic(topic: Dict[str, Any], query_seq: List[str], query_set: set) -> float:
        score = 0.0

        # 1. El id o la etiqueta del título aparecen como frase completa (peso alto)
        if contains_phrase(query_seq, content_stems(topic["id"])):
            score += 15.0
        title_label = re.split(r"\s[—–-]\s", topic["title"], maxsplit=1)[0]
        if contains_phrase(query_seq, content_stems(title_label)):
            score += 12.0

        # 2. Keywords del tema: frase completa (alto) o palabra significativa suelta (medio)
        for kw in topic.get("keywords", []):
            kw_seq = content_stems(kw)
            if not kw_seq:
                continue
            if contains_phrase(query_seq, kw_seq):
                score += 8.0
            elif len(kw_seq) > 1 and any(
                w in query_set for w, raw in zip(kw_seq, content_tokens(kw)) if len(raw) >= 3
            ):
                score += 4.0

        # 3. Solapamiento de términos con contenido y resumen (solo refuerza, no decide)
        topic_terms = set(content_stems(topic.get("content", "") + " " + topic.get("summary", "")))
        score += len(query_set & topic_terms) * 0.5
        return score

    # ------------------------------------- recuperación de segmentos de clase
    def retrieve_segments(
        self,
        course_id: str,
        query: str,
        lesson_id: Optional[str] = None,
        top_k: int = 3,
        phrase_text: Optional[str] = None,
    ) -> List[SegmentMatch]:
        """
        Recupera segmentos de transcripción relevantes con sus timestamps.

        - Solo se consideran clases de `course_id`.
        - Si se indica `lesson_id`, solo esa clase; con una clase inexistente devuelve [].
        - Sin `lesson_id` se busca en todas las clases del curso (cada resultado
          informa su `lesson_id`).
        - `phrase_text` (opcional): la pregunta ORIGINAL; solo se usa para la bonificación por frases literales
          ("qué es n8n"), no para los términos ni la cobertura.
        - Devuelve [] si la consulta no tiene términos con contenido, si la cobertura
          de la consulta en el material es insuficiente o si no hay coincidencias.
        """
        lessons = self._lessons.get(course_id, {})
        if lesson_id is not None:
            lessons = {lesson_id: lessons[lesson_id]} if lesson_id in lessons else {}
        if not lessons:
            return []

        pool = [seg for lid in lessons for seg in self._segment_index[(course_id, lid)]]
        if not pool:
            return []

        query_terms = unique(content_stems(query))
        if not query_terms:
            return []

        n = len(pool)
        df = {t: sum(1 for s in pool if t in s.tf) for t in query_terms}
        matched = [t for t in query_terms if df[t] > 0]
        # La cobertura se mide sobre los términos de contenido (las palabras marco como "sirve" no cuentan).
        core_terms = [t for t in query_terms if t not in FRAME_STEMS] or query_terms
        core_matched = [t for t in core_terms if df[t] > 0]
        if not core_matched:
            return []
        if len(core_terms) >= 2 and len(core_matched) / len(core_terms) < MIN_QUERY_COVERAGE:
            return []

        idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * (FRAME_WEIGHT if t in FRAME_STEMS else 1.0) for t in matched}
        query_pairs = set(bigrams(content_stems(query)))
        display = self._display_terms(query)
        ngrams = self._query_ngrams(phrase_text or query)

        scored: List[Tuple[float, _IndexedSegment, List[str]]] = []
        for seg in pool:
            hits = [t for t in matched if t in seg.tf]
            if not hits:
                continue
            score = sum(idf[t] * (seg.tf[t] * (BM25_K1 + 1)) / (seg.tf[t] + BM25_K1) for t in hits)
            score += PHRASE_BONUS * len(query_pairs & seg.pairs)
            score += NGRAM_BONUS_PER_TOKEN * max((len(g.split()) for g in ngrams if g in seg.norm), default=0)
            scored.append((score, seg, [display.get(t, t) for t in hits]))

        if not scored:
            return []
        scored.sort(key=lambda x: (-x[0], x[1].lesson.lesson_id, x[1].index))
        best = scored[0][0]
        if len(matched) < len(query_terms) and best < MIN_PARTIAL_SCORE:
            return []
        results = []
        for score, seg, terms in scored:
            if score < best * SEGMENT_RELATIVE_CUTOFF:
                break
            segment = seg.lesson.segments[seg.index]
            focus_seconds, focus_text = self._focus(segment, [t for t in matched if t in seg.tf])
            results.append(
                SegmentMatch(
                    segment=segment,
                    lesson_title=seg.lesson.title,
                    score=round(score, 4),
                    matched_terms=terms,
                    focus_seconds=focus_seconds,
                    focus_text=focus_text,
                )
            )
            if len(results) >= top_k:
                break
        return results

    @staticmethod
    def _query_ngrams(query: str) -> List[str]:
        """Frases contiguas de la pregunta (3 a 6 palabras) con al menos una palabra de contenido, listas para buscar en `norm`."""
        tokens = tokenize(query)
        grams = []
        for size in range(min(NGRAM_MAX, len(tokens)), NGRAM_MIN - 1, -1):
            for i in range(len(tokens) - size + 1):
                window = tokens[i:i + size]
                if any(t not in STOPWORDS and len(t) > 1 for t in window):
                    grams.append(" " + " ".join(window) + " ")
        return grams

    @staticmethod
    def _display_terms(query: str) -> Dict[str, str]:
        """Mapea cada raíz a la primera palabra (normalizada) de la consulta que la originó."""
        mapping: Dict[str, str] = {}
        for word in content_tokens(query):
            mapping.setdefault(stem(word), word)
        return mapping

    # --------------------------------------------------------------- citas
    def build_citations(self, matched_chunks: List[Dict[str, Any]]) -> List[CitationRef]:
        """Construye las referencias bibliográficas para la respuesta."""
        citations = []
        for chunk in matched_chunks:
            citations.append(
                CitationRef(
                    reference=chunk.get("reference", "ref:general"),
                    topic=chunk.get("id", "general"),
                    title=chunk.get("title", "Material del curso")
                )
            )
        return citations

    def build_segment_citations(self, matches: List[SegmentMatch]) -> List[CitationRef]:
        """Citas de grabación: incluyen clase y rango de minutos del segmento."""
        citations = []
        for m in matches:
            seg = m.segment
            citations.append(
                CitationRef(
                    reference=f"ref:{seg.course_id}/{seg.lesson_id}#t={int(seg.start_seconds)}",
                    topic=seg.lesson_id,
                    title=f"{m.lesson_title} — min {seg.timestamp_range}",
                    lesson_id=seg.lesson_id,
                    start_seconds=seg.start_seconds,
                    end_seconds=seg.end_seconds,
                    timestamp=seg.timestamp_range,
                    seek_seconds=m.focus_seconds if m.focus_seconds is not None else seg.start_seconds,
                )
            )
        return citations

# Instancia singleton
retriever = KnowledgeRetriever()
