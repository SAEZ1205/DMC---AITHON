"""
Análisis determinista y verificable de una transcripción: conceptos, capítulos, resumen extractivo y
cálculo de contenido perdido. No usa ningún modelo de lenguaje.

Principios
  * Todo texto que se muestra como contenido de clase es una FRASE TEXTUAL de la transcripción
    (recortada por frontera de oración); nada se parafrasea ni se inventa.
  * Todos los tiempos (inicio/fin de capítulos, minutos) salen de los segmentos reales.
  * Los conceptos salen de (a) las fichas de tema del curso y (b) términos en MAYÚSCULAS presentes
    en la propia transcripción.
"""
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from app.knowledge.text_utils import STOPWORDS, contains_phrase, stem, tokenize
from app.llm.demo_provider import shorten
from app.models.transcript import LessonTranscript, TranscriptSegment, format_timestamp

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_UPPER_RE = re.compile(r"\b[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ_]+\b")
KEY_SENTENCE_MAX_CHARS = 220
MAX_TERMS = 10
INTRO_TITLE = "Introducción"
EPS = 1e-9

# Clases largas sin fichas de tema: capítulos por ventanas de tiempo
LONG_LESSON_SECONDS = 1200
WINDOW_SECONDS = 600
MIN_LAST_WINDOW_SECONDS = 180
TITLE_TERMS = 3
MAX_TERM_WINDOW_RATIO = 0.4           # un término presente en >40 % de las ventanas no distingue ningún capítulo
# Muletillas y relleno oral frecuentes en transcripciones automáticas de clases en vivo
ORAL_FILLER = frozenset(
    "bueno entonces ahora vamos chicos chicas aca aqui eso esto digamos verdad mira claro bien despues luego ahorita "
    "porque tambien cosas tiene tienen hacer puede pueden podemos podria podriamos tengo tenemos veamos seria ejemplo "
    "pues como algo alguien nadie decir dice dicen dijo digo mismo misma tipo forma manera parte vez veces poco poquito "
    "asi allí alli ahi ahí bueno gracias profe profesor minuto minutos segundo segundos momento nada todo todos todas dale porfa mientras atras regresa listo aja ajá habilitar mensaje".split()
)


# ============================================================== conceptos
@dataclass(frozen=True)
class TopicDef:
    topic_id: str
    label: str          # "ORDER BY"
    title: str          # "ORDER BY — Ordenamiento de Resultados"
    phrases: Tuple[Tuple[str, ...], ...]   # secuencias de raíces que identifican el tema


@dataclass(frozen=True)
class ConceptHit:
    label: str
    kind: str           # "tema" | "termino"
    position: int       # posición (en tokens) de la primera aparición dentro del segmento


def _stems(text: str) -> List[str]:
    return [stem(t) for t in tokenize(text)]


def topic_defs(course: Optional[dict]) -> List[TopicDef]:
    """Fichas de tema del curso -> definiciones. Solo el id y la etiqueta del título identifican un tema."""
    defs = []
    for topic in (course or {}).get("topics", []):
        label = re.split(r"\s[—–-]\s", topic["title"], maxsplit=1)[0].strip()
        phrases = []
        for candidate in (label, topic["id"].replace("_", " ")):
            seq = tuple(_stems(candidate))
            if seq and seq not in phrases:
                phrases.append(seq)
        defs.append(TopicDef(topic["id"], label, topic["title"], tuple(phrases)))
    return defs


def uppercase_terms(text: str) -> List[Tuple[str, int]]:
    """Términos en MAYÚSCULAS (SELECT, IS NULL, COUNT DISTINCT...) con su posición; las secuencias contiguas se unen."""
    found: List[Tuple[str, int]] = []
    last_end = -10
    for m in _UPPER_RE.finditer(text):
        word = m.group(0)
        if found and m.start() - last_end == 1:          # separadas por un único espacio -> mismo término
            prev, pos = found[-1]
            found[-1] = (f"{prev} {word}", pos)
        else:
            found.append((word, m.start()))
        last_end = m.end()
    return found


def segment_concepts(segment: TranscriptSegment, topics: Sequence[TopicDef]) -> List[ConceptHit]:
    """Conceptos presentes en un segmento, ordenados por aparición. Los temas tienen prioridad sobre los términos."""
    tokens = _stems(segment.text)
    hits: Dict[str, ConceptHit] = {}
    for topic in topics:
        positions = [i for phrase in topic.phrases for i in _phrase_positions(tokens, phrase)]
        if positions:
            hits[topic.label.lower()] = ConceptHit(topic.label, "tema", min(positions))
    topic_labels = {t.label.lower() for t in topics}
    for term, char_pos in uppercase_terms(segment.text):
        key = term.lower()
        if key in hits or key in topic_labels:
            continue
        hits[key] = ConceptHit(term, "termino", len(tokenize(segment.text[:char_pos])))
    return sorted(hits.values(), key=lambda h: h.position)


def _phrase_positions(tokens: List[str], phrase: Tuple[str, ...]) -> List[int]:
    n = len(phrase)
    return [i for i in range(len(tokens) - n + 1) if tuple(tokens[i:i + n]) == phrase]


# ================================================================ frases clave
def key_sentence(segments: Sequence[TranscriptSegment], preferred: Optional[str] = None) -> str:
    """
    Frase TEXTUAL representativa. Si hay un concepto principal (`preferred`), la primera frase de al menos
    6 palabras que lo menciona; si no, la de al menos 4 palabras con más términos destacados (la primera en empate).
    """
    sentences = [
        sentence.strip()
        for seg in segments
        for sentence in _SENTENCE_RE.split(seg.text.strip())
        if sentence.strip()
    ]
    if preferred:
        key = preferred.lower()
        for sentence in sentences:
            if len(sentence.split()) >= 6 and key in sentence.lower():
                return shorten(sentence, KEY_SENTENCE_MAX_CHARS)
    best, best_score = "", (-1, -1)
    for sentence in sentences:
        if len(sentence.split()) < 4:
            continue
        score = (len(uppercase_terms(sentence)), len(sentence.split()))   # más términos; en empate, la más informativa
        if score > best_score:
            best, best_score = sentence, score
    return shorten(best or segments[0].text.strip(), KEY_SENTENCE_MAX_CHARS)


# ================================================================== capítulos
@dataclass
class Chapter:
    chapter_id: str
    title: str
    segments: List[TranscriptSegment]
    concepts: List[str] = field(default_factory=list)
    description: str = ""
    # Segmentos que respaldan el capítulo en el resumen (por defecto todos; en clases largas, el más representativo)
    reference_segments: Optional[List[TranscriptSegment]] = None
    focus_seconds: Optional[float] = None

    @property
    def start_seconds(self) -> float:
        return self.segments[0].start_seconds

    @property
    def end_seconds(self) -> float:
        return self.segments[-1].end_seconds


def analyze_segments(lesson: LessonTranscript, topics: Sequence[TopicDef]) -> Dict[int, List[ConceptHit]]:
    return {seg.segment_id: segment_concepts(seg, topics) for seg in lesson.segments}


def build_chapters(lesson: LessonTranscript, topics: Sequence[TopicDef], hits: Dict[int, List[ConceptHit]]) -> List[Chapter]:
    """
    Un capítulo nuevo empieza cuando un segmento introduce, como concepto principal, un TEMA que aún no
    había aparecido y es distinto del capítulo en curso. Los segmentos sin tema nuevo se unen al capítulo
    anterior. Los capítulos cubren segmentos completos, así que sus tiempos son exactamente los de la transcripción.
    """
    segments = lesson.segments
    if not segments:
        return []
    if not topics:
        if lesson.duration_seconds > LONG_LESSON_SECONDS:
            return window_chapters(lesson, hits)
        return _fixed_chapters(segments, hits)

    titles = {t.label: t.title for t in topics}
    chapters: List[Chapter] = []
    seen: set = set()
    current_label: Optional[str] = None
    for seg in segments:
        theme_hits = [h for h in hits[seg.segment_id] if h.kind == "tema"]
        primary = theme_hits[0].label if theme_hits else None      # el que aparece primero en el texto
        starts_new = bool(primary) and primary not in seen and primary != current_label
        if not chapters or starts_new:
            if starts_new:
                title, current_label = titles[primary], primary
            else:
                title, current_label = INTRO_TITLE, None
            chapters.append(Chapter(f"cap-{len(chapters) + 1:02d}", title, []))
        chapters[-1].segments.append(seg)
        for h in theme_hits:
            seen.add(h.label)
    _finish(chapters, hits)
    return chapters


def _term_stems(text: str) -> List[Tuple[str, str]]:
    """(raíz, palabra original en minúsculas) de las palabras de contenido de `text`, sin muletillas orales."""
    out = []
    for raw in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ0-9]+", text):
        low = raw.lower()
        key = stem(tokenize(low)[0]) if tokenize(low) else ""
        norm = tokenize(low)[0] if tokenize(low) else ""
        if len(norm) >= 4 and norm not in STOPWORDS and norm not in ORAL_FILLER:
            out.append((key, low))
    return out


def window_chapters(lesson: LessonTranscript, hits: Dict[int, List[ConceptHit]]) -> List[Chapter]:
    """
    Capítulos de ~10 min para clases largas sin fichas de tema.

    * Los límites son límites de SEGMENTO (nunca se inventa un tiempo).
    * El título reúne los términos más distintivos de la ventana (TF-IDF entre ventanas): son palabras que
      realmente aparecen en esa parte de la clase.
    * La descripción es una frase TEXTUAL del segmento más representativo, y el capítulo la cita con su renglón.
    """
    windows: List[List[TranscriptSegment]] = []
    for seg in lesson.segments:
        if not windows or seg.start_seconds - windows[-1][0].start_seconds >= WINDOW_SECONDS:
            windows.append([])
        windows[-1].append(seg)
    if len(windows) > 1 and windows[-1][-1].end_seconds - windows[-1][0].start_seconds < MIN_LAST_WINDOW_SECONDS:
        windows[-2].extend(windows.pop())

    per_window = [Counter(k for seg in w for k, _ in _term_stems(seg.text)) for w in windows]
    surface: Dict[str, Counter] = {}
    for w in windows:
        for seg in w:
            for k, low in _term_stems(seg.text):
                surface.setdefault(k, Counter())[low] += 1
    n = len(windows)
    doc_freq = Counter(k for c in per_window for k in c)

    def weight(key: str, count: int) -> float:
        if n > 1 and doc_freq[key] / n > MAX_TERM_WINDOW_RATIO:
            return 0.0
        return count * math.log(1 + n / doc_freq[key])

    chapters: List[Chapter] = []
    for i, (w, counts) in enumerate(zip(windows, per_window), start=1):
        ranked = sorted(((weight(k, c), k) for k, c in counts.items() if weight(k, c) > 0), reverse=True)[:TITLE_TERMS]
        words = [surface[k].most_common(1)[0][0] for _, k in ranked]
        title = " · ".join(wd.capitalize() for wd in words) if words else f"Parte {i}"
        key_weights = {k: weight(k, c) for c, k in ((c, k) for k, c in counts.items())}
        best_seg, best_score = w[0], -1.0
        for seg in w:
            score = sum(key_weights.get(k, 0.0) for k, _ in _term_stems(seg.text)) / math.sqrt(max(len(seg.text), 1))
            if score > best_score:
                best_seg, best_score = seg, score
        focus_line = None
        if best_seg.lines:
            focus_line = max(best_seg.lines, key=lambda ln: sum(key_weights.get(k, 0.0) for k, _ in _term_stems(ln.text)))
        description_source = (focus_line.text if focus_line else best_seg.text)
        concepts: List[str] = []
        for seg in w:
            for h in hits.get(seg.segment_id, []):
                if h.label not in concepts:
                    concepts.append(h.label)
        chapters.append(Chapter(
            chapter_id=f"cap-{i:02d}", title=title, segments=list(w), concepts=concepts[:8],
            description=shorten(" ".join(description_source.split()), KEY_SENTENCE_MAX_CHARS),
            reference_segments=[best_seg], focus_seconds=focus_line.start_seconds if focus_line else best_seg.start_seconds,
        ))
    return chapters


def _fixed_chapters(segments: Sequence[TranscriptSegment], hits) -> List[Chapter]:
    size = 3
    chapters = [
        Chapter(f"cap-{i // size + 1:02d}", f"Parte {i // size + 1}", list(segments[i:i + size]))
        for i in range(0, len(segments), size)
    ]
    _finish(chapters, hits)
    return chapters


def _finish(chapters: List[Chapter], hits: Dict[int, List[ConceptHit]]) -> None:
    for ch in chapters:
        labels: List[str] = []
        for seg in ch.segments:
            for h in hits[seg.segment_id]:
                if h.label not in labels:
                    labels.append(h.label)
        ch.concepts = labels
        ch.description = key_sentence(ch.segments, ch.title.split(" — ")[0] if ch.title != INTRO_TITLE else None)


# ===================================================================== resumen
@dataclass
class ConceptSummary:
    concept: str
    kind: str
    first_seconds: float
    mentions: int
    segment_ids: List[int]


def key_concepts(lesson: LessonTranscript, hits: Dict[int, List[ConceptHit]]) -> List[ConceptSummary]:
    by_label: Dict[str, ConceptSummary] = {}
    for seg in lesson.segments:
        for h in hits[seg.segment_id]:
            cs = by_label.get(h.label)
            if cs is None:
                by_label[h.label] = ConceptSummary(h.label, h.kind, seg.start_seconds, 1, [seg.segment_id])
            else:
                cs.mentions += 1
                cs.segment_ids.append(seg.segment_id)
    topics = [c for c in by_label.values() if c.kind == "tema"]
    terms = sorted((c for c in by_label.values() if c.kind == "termino"), key=lambda c: (-c.mentions, c.first_seconds))
    return topics + sorted(terms[:MAX_TERMS], key=lambda c: c.first_seconds)


def build_overview(lesson: LessonTranscript, chapters: Sequence[Chapter], concepts: Sequence[ConceptSummary]) -> str:
    shown = [c.title.split(" — ")[0] for c in chapters[:8]]
    names = ", ".join(shown) + (f" y {len(chapters) - 8} más" if len(chapters) > 8 else "")
    topics = [c.concept for c in concepts if c.kind == "tema"]
    text = (f"«{lesson.title}» dura {format_timestamp(lesson.duration_seconds)} y está organizada en "
            f"{len(chapters)} capítulo{'s' if len(chapters) != 1 else ''}: {names}.")
    if topics:
        text += f" Temas tratados: {', '.join(topics)}."
    return text


# ====================================================== contenido perdido
@dataclass(frozen=True)
class IntervalProblem:
    index: int
    reason: str          # negativo | invertido | vacio | fuera_de_duracion
    message: str


def validate_intervals(raw: Sequence[Tuple[float, float]], duration: float) -> List[IntervalProblem]:
    problems: List[IntervalProblem] = []
    for i, (start, end) in enumerate(raw):
        if start < 0 or end < 0:
            problems.append(IntervalProblem(i, "negativo", "Los segundos no pueden ser negativos."))
        elif end < start:
            problems.append(IntervalProblem(i, "invertido", "El fin es anterior al inicio."))
        elif end == start:
            problems.append(IntervalProblem(i, "vacio", "El intervalo no tiene duración."))
        elif start >= duration - EPS or end > duration + EPS:
            problems.append(IntervalProblem(
                i, "fuera_de_duracion",
                f"El intervalo excede la duración de la clase ({format_timestamp(duration)}, {duration:g} s)."))
    return problems


def normalize_intervals(raw: Sequence[Tuple[float, float]]) -> Tuple[List[Tuple[float, float]], List[str]]:
    """
    Descarta duplicados exactos y fusiona intervalos solapados o contiguos. Asume intervalos ya validados.
    Devuelve (intervalos disjuntos ordenados, notas explicativas).
    """
    notes: List[str] = []
    unique = sorted(set(raw))
    duplicates = len(raw) - len(unique)
    if duplicates:
        notes.append(f"Se descartaron {duplicates} intervalo(s) duplicado(s).")
    merged: List[List[float]] = []
    fused = 0
    for start, end in unique:
        if merged and start <= merged[-1][1] + EPS:
            fused += 1
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    if fused:
        notes.append(f"Se fusionaron {fused} intervalo(s) solapado(s) o contiguo(s) para no repetir contenido.")
    return [(a, b) for a, b in merged], notes


@dataclass(frozen=True)
class SegmentOverlap:
    segment: TranscriptSegment
    overlap_start: float
    overlap_end: float
    overlap_seconds: float
    partial: bool


def segment_overlaps(lesson: LessonTranscript, intervals: Sequence[Tuple[float, float]]) -> List[SegmentOverlap]:
    """Segmentos con solapamiento POSITIVO con los intervalos (disjuntos). Cada segmento aparece una sola vez."""
    result = []
    for seg in lesson.segments:
        pieces = [
            (max(seg.start_seconds, a), min(seg.end_seconds, b))
            for a, b in intervals
            if min(seg.end_seconds, b) - max(seg.start_seconds, a) > EPS
        ]
        if not pieces:
            continue
        seconds = sum(e - s for s, e in pieces)
        seg_len = seg.end_seconds - seg.start_seconds
        result.append(SegmentOverlap(seg, pieces[0][0], pieces[-1][1], round(seconds, 3), seconds < seg_len - EPS))
    return result
