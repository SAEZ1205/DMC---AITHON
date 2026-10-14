"""Chat de contenido en modo tutor (spec v2, 3.5.2–3.5.3).

El backend controla la escalera de ayuda (1 ubicar, 2 pista, 3 paso guiado, 4 explicación), la protección del
quiz, la respuesta fija fuera de la clase, las citas y el límite de palabras; el LLM solo redacta cada turno.
Sin API key, una simulación determinista sigue las mismas reglas: cita el minuto, termina con una pregunta y no
da la respuesta completa antes del nivel 4.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from rank_bm25 import BM25Okapi
from sqlmodel import Session, select

from .. import config
from ..models import Chapter, ChatMessage, ChatThread, ClassSession, Course, MaterialUnit, QuizAttempt, QuizItem, \
    TranscriptSegment, User
from . import llm
from . import quiz as quiz_service
from .chaptering import tokens
from .text import TS_PATTERN, fmt_ts, parse_citations, strip_accents

log = logging.getLogger("dmc.tutor")

LOCATE, HINT, STEP, EXPLAIN = 1, 2, 3, 4
CORRECT, PARTIAL, WRONG = "correcta", "parcial", "incorrecta"
HINT_ACTION, EXPLAIN_ACTION, ANSWER_ACTION, CLOSE_ACTION = "hint", "explain", "answer", "close"
ACTIONS = (HINT_ACTION, EXPLAIN_ACTION, ANSWER_ACTION, CLOSE_ACTION)
WINDOW_BEFORE_S, WINDOW_AFTER_S = 20, 45
ACTION_TEXT = {HINT_ACTION: "Dame una pista", EXPLAIN_ACTION: "Ver explicación", CLOSE_ACTION: "Ya lo entendí"}
DONT_KNOW = re.compile(r"\b(no se|ni idea|no recuerdo|no me acuerdo)\b")


# Palabras de la pregunta que no dicen de qué tema se trata (no se buscan en la clase).
QUESTION_WORDS = set("""
significa significan quiere decir pasa pasaria existen existe tipos tipo ademas usamos usaron usamos devuelve
devuelven escribe escribir necesita necesito necesitan deberia debo funciona funcionan explicame explica
diferencia diferencias entre cuantos cuantas sirven conviene recomienda recomiendan forma manera paso pasos
lugar
""".split())
ANCHOR_CANDIDATES = 30
EARLIEST_SHARE = 0.75  # entre 0.70 y 0.80 da el mismo resultado en eval_chat.py
# frases con las que el docente define o explica algo; la ventana que las tiene pesa más
DEFINITION_CUES = re.compile(r"\b(te permite|permite|sirve para|es un|es una|quiere decir|significa|se refiere|"
                             r"basicamente|la diferencia|se usa para|lo que hace)\b")
DEFINITION_BONUS = 1.5
ASKED_BONUS = 2.0  # el docente suele plantear la misma pregunta («¿qué hace el Docker?») antes de explicarla
ASKED = re.compile(r"\b(que es|que son|que hace|que hacen|para que sirve|para que sirven|como se|como funciona|"
                   r"por que|cual es|cuales son)\b")


def stem(word: str) -> str:
    return word[:6] if len(word) >= 7 else word[:5]


def stems(text: str) -> list[str]:
    return [stem(w) for w in tokens(text)]


def query_stems(question: str) -> list[str]:
    return list(dict.fromkeys(stem(w) for w in tokens(question) if w not in QUESTION_WORDS))


def _surface(text: str) -> dict[str, str]:
    """Primera palabra original (con tildes) de cada raíz."""
    out: dict[str, str] = {}
    for word in re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ-]+", text):
        for s in stems(word):
            out.setdefault(s, word)
    return out


# --- corpus y evidencia ---

@dataclass(frozen=True)
class Seg:
    id: int
    start: float
    text: str


@dataclass(frozen=True)
class Unit:
    id: int
    source_file: str
    kind: str
    unit_no: int
    title: str
    text: str


@dataclass
class Corpus:
    """Copia en memoria (sin filas ORM, para poder reutilizarla entre peticiones)."""
    segments: list[Seg]
    units: list[Unit]
    seg_toks: list[list[str]]
    unit_toks: list[list[str]]
    df: dict[str, int]

    def __post_init__(self):
        self.n_docs = len(self.seg_toks) + len(self.unit_toks)
        self.seg_bm25 = BM25Okapi(self.seg_toks) if self.seg_toks else None
        self.unit_bm25 = BM25Okapi(self.unit_toks) if self.unit_toks else None

    def idf(self, term: str) -> float:
        return math.log((self.n_docs + 1) / (self.df.get(term, 0) + 0.5))

    def variants(self, term: str) -> set[str]:
        """La raíz y las del vocabulario que la contienen como prefijo o son prefijo de ella («host» ↔ «hoste»):
        tolera plurales y errores de la transcripción automática."""
        if len(term) < 4:
            return {term}
        similar = {v for v in self.df
                   if v.startswith(term) or (len(term) >= 5 and len(v) >= 5 and v[:5] == term[:5])
                   or (len(v) >= 4 and term.startswith(v) and term[len(v):] in ("e", "s", "es"))}
        return {term} | {v for v in similar if self.idf(v) >= self.idf(term) - 2.5}


_CORPUS: dict[tuple, Corpus] = {}


def corpus_of(db: Session, session: ClassSession) -> Corpus:
    segments = db.exec(select(TranscriptSegment).where(TranscriptSegment.session_id == session.id)
                       .order_by(TranscriptSegment.start)).all()
    units = db.exec(select(MaterialUnit).where(MaterialUnit.session_id == session.id)
                    .order_by(MaterialUnit.id)).all()
    key = (session.id, len(segments), segments[-1].id if segments else 0, sum(len(s.text) for s in segments),
           len(units), units[-1].id if units else 0)
    if key not in _CORPUS:
        segs = [Seg(s.id, s.start, s.text) for s in segments]
        unit_rows = [Unit(u.id, u.source_file, u.kind, u.unit_no, u.title or "", u.text or "") for u in units]
        seg_toks = [stems(s.text) for s in segs]
        unit_toks = [stems(f"{u.title} {u.title} {u.text}") for u in unit_rows]
        df: dict[str, int] = {}
        for toks in seg_toks + unit_toks:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        _CORPUS.clear()
        _CORPUS[key] = Corpus(segs, unit_rows, seg_toks, unit_toks, df)
    return _CORPUS[key]


@dataclass
class Evidence:
    found: bool
    coverage: float = 0.0
    query: list[str] = field(default_factory=list)
    fragments: list[Seg] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    anchor: Seg | None = None
    window: list[Seg] = field(default_factory=list)
    slide: Unit | None = None
    chapter: Chapter | None = None

    @property
    def ts(self) -> str:
        return fmt_ts(self.anchor.start) if self.anchor else ""

    @property
    def slide_text(self) -> str:
        return f" (diapositiva {self.slide.unit_no})" if self.slide else ""


def _top(scores, k: int) -> list[int]:
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return [i for i in ranked if scores[i] > 0]


def _asked_phrase(question: str) -> str:
    match = ASKED.search(strip_accents(question.lower()))
    return match.group(1) if match else ""


def _chapters_near_slide(chapters: list[Chapter], slide_no: int, max_distance: int = 2) -> list[Chapter]:
    """Capítulos anclados a esa diapositiva o a una a lo más `max_distance` diapositivas de distancia."""
    def distance(c: Chapter) -> int:
        lo, hi = c.slide_from, c.slide_to or c.slide_from
        return 0 if lo <= slide_no <= hi else min(abs(slide_no - lo), abs(slide_no - hi))

    return [c for c in chapters if c.slide_from and not c.logistics and distance(c) <= max_distance]


def find_evidence(corpus: Corpus, chapters: list[Chapter], question: str) -> Evidence:
    query = query_stems(question)
    if not query or not corpus.n_docs:
        return Evidence(False, query=query)
    variants = {t: corpus.variants(t) for t in query}
    total = sum(corpus.idf(t) for t in query)

    def coverage(found: set[str]) -> float:
        # una variante aporta a lo más su propio IDF: una palabra común («sale») no reemplaza a una rara («sales»)
        return sum(min(corpus.idf(t), max(corpus.idf(v) for v in hit)) if (hit := variants[t] & found) else 0.0
                   for t in query) / total

    expanded = list(dict.fromkeys(v for t in query for v in sorted(variants[t])))
    seg_scores = corpus.seg_bm25.get_scores(expanded) if corpus.seg_bm25 else []
    unit_scores = corpus.unit_bm25.get_scores(expanded) if corpus.unit_bm25 else []
    top_segs = _top(seg_scores, config.CHAT_TOP_FRAGMENTS)
    top_units = _top(unit_scores, config.CHAT_TOP_UNITS)
    unit_set = set().union(*(corpus.unit_toks[i] for i in top_units)) if top_units else set()

    asked = _asked_phrase(question)

    def best_window(candidates: list[int]):
        """Puntaje de cada ventana = densidad del tema (suma BM25) × cobertura de la pregunta, con bono si el
        docente define algo o repite la pregunta del alumno. Entre las ventanas con ≥ 60 % del mejor puntaje,
        la primera: el concepto se explica cuando aparece; después solo se repite.
        Devuelve (cobertura con material, cobertura solo de lo dicho, ancla, ventana)."""
        scored = []
        for i in candidates:
            seg = corpus.segments[i]
            idx = [j for j in range(max(0, i - 15), min(len(corpus.segments), i + 16))
                   if seg.start - WINDOW_BEFORE_S <= corpus.segments[j].start <= seg.start + WINDOW_AFTER_S]
            said = set().union(*(corpus.seg_toks[j] for j in idx))
            text = strip_accents(" ".join(corpus.segments[j].text for j in idx).lower())
            cov = coverage(said)
            score = sum(seg_scores[j] for j in idx) * (0.5 + cov)
            if DEFINITION_CUES.search(text):
                score *= DEFINITION_BONUS
            if asked and asked in text:
                score *= ASKED_BONUS
            scored.append((score, cov, seg, [corpus.segments[j] for j in idx], coverage(said | unit_set)))
        if not scored:
            return 0.0, 0.0, None, []
        top = max(s[0] for s in scored)
        _, _, anchor, window, _ = min((s for s in scored if s[0] >= EARLIEST_SHARE * top), key=lambda s: s[2].start)
        return max(s[4] for s in scored), max(s[1] for s in scored), anchor, window

    deck = [corpus.units[i] for i in top_units if corpus.units[i].kind in ("slide", "page")]
    best_cov, said_cov, anchor, window = best_window(_top(seg_scores, ANCHOR_CANDIDATES))
    best_cov = max(best_cov, coverage(unit_set))
    near = _chapters_near_slide(chapters, deck[0].unit_no) if deck else []
    if near:
        # a igual evidencia en lo dicho, manda la diapositiva: se cita el tramo anclado a ella
        inside = [i for i, s in enumerate(corpus.segments) if any(c.start <= s.start < c.end for c in near)]
        local = _top([seg_scores[i] for i in inside], ANCHOR_CANDIDATES)
        _, cov, a, w = best_window([inside[k] for k in local])
        if a is not None and cov >= said_cov:
            anchor, window = a, w
            top_segs = list(dict.fromkeys([inside[k] for k in local[:5]] + top_segs))[:config.CHAT_TOP_FRAGMENTS]

    chapter = None
    if anchor:
        chapter = next((c for c in chapters if c.start <= anchor.start < c.end), None)
    slide = None
    if chapter and chapter.slide_from:
        slide = next((u for u in deck if chapter.slide_from <= u.unit_no <= (chapter.slide_to or chapter.slide_from)), None)
    slide = slide or (deck[0] if deck else None)
    return Evidence(found=best_cov >= config.CHAT_EVIDENCE_MIN, coverage=round(best_cov, 2), query=query,
                    fragments=sorted((corpus.segments[i] for i in top_segs), key=lambda s: s.start),
                    units=[corpus.units[i] for i in top_units], anchor=anchor, window=window, slide=slide,
                    chapter=chapter)


# --- protección del quiz ---

def quiz_match(db: Session, session: ClassSession, question: str, corpus: Corpus | None = None) -> QuizItem | None:
    """Pregunta aprobada del quiz más parecida, si su BM25 (normalizado por el de la pregunta consigo misma) supera
    el umbral. La transcripción entra como fondo para que el IDF tenga sentido con pocas preguntas."""
    items = quiz_service.approved_items(db, session.id)
    docs = [stems(i.question) or ["_vacio_"] for i in items]
    if not docs:
        return None
    corpus = corpus or corpus_of(db, session)
    bm25 = BM25Okapi(docs + [t for t in corpus.seg_toks if t])
    scores = bm25.get_scores(stems(question))
    best = max(range(len(items)), key=lambda i: scores[i])
    own = bm25.get_scores(docs[best])[best]
    return items[best] if own > 0 and scores[best] / own >= config.CHAT_QUIZ_SIMILARITY else None


def passed_quiz(db: Session, session: ClassSession, user_id: int) -> bool:
    course = db.get(Course, session.course_id)
    scores = db.exec(select(QuizAttempt.score).where(QuizAttempt.user_id == user_id,
                                                     QuizAttempt.session_id == session.id)).all()
    return any(s >= course.quiz_pass_score for s in scores)


def leaks_answer(reply: str, item: QuizItem) -> bool:
    correct = set(stems(json.loads(item.options_json)[item.answer_idx]))
    if not correct:
        return False
    hits = len(correct & set(stems(reply)))
    return hits == len(correct) if len(correct) <= 2 else hits / len(correct) >= 0.6


# --- límites de la respuesta ---

def words(text: str) -> int:
    return len(text.split())


def cap_words(text: str, limit: int = config.CHAT_MAX_WORDS) -> str:
    """Recorta por frases hasta `limit` palabras y conserva la pregunta final."""
    if words(text) <= limit:
        return text
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    question = parts.pop() if parts and parts[-1].endswith("?") and len(parts) > 1 else ""
    budget = limit - words(question)
    kept: list[str] = []
    for p in parts:
        if words(" ".join(kept + [p])) > budget:
            break
        kept.append(p)
    if not kept:
        kept = [" ".join(parts[0].split()[:max(5, budget - 1)]) + "…"] if parts else []
    return " ".join(kept + ([question] if question else []))


def _quote(text: str, n: int) -> str:
    ws = text.split()
    return " ".join(ws[:n]) + ("…" if len(ws) > n else "")


# --- simulación determinista ---

def _focus(ev: Evidence, question: str, corpus: Corpus) -> str:
    surf = _surface(question)
    ranked = sorted(ev.query, key=corpus.idf, reverse=True)[:2]
    return " y ".join(f"«{surf.get(t, t)}»" for t in ranked) or "ese tema"


def _hint_terms(ev: Evidence, corpus: Corpus, k: int = 2) -> list[str]:
    source = f"{ev.slide.title} {ev.slide.text}" if ev.slide else " ".join(s.text for s in ev.window)
    surf = _surface(source)
    counts: dict[str, int] = {}
    for t in stems(source):
        if t not in ev.query and len(t) >= 4:
            counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts, key=lambda t: counts[t] * corpus.idf(t), reverse=True)[:k]
    return [surf.get(t, t) for t in ranked]


def _first_line(ev: Evidence) -> str:
    if ev.slide:
        lines = [ln.strip(" •-–\t") for ln in ev.slide.text.splitlines() if len(ln.split()) >= 4]
        if lines:
            return lines[0]
    return " ".join(s.text for s in ev.window[:2]) or (ev.anchor.text if ev.anchor else "")


def mock_turn(ev: Evidence, corpus: Corpus, question: str, level: int) -> str:
    ts, slide = ev.ts, ev.slide_text
    if level == LOCATE:
        where = f", en el capítulo «{ev.chapter.title}»" if ev.chapter else ""
        return (f"Esto se explica en [{ts}]{slide}{where}. Mira ese tramo y cuéntame: "
                f"¿qué recuerdas de lo que se dijo sobre {_focus(ev, question, corpus)}?")
    if level == HINT:
        terms = _hint_terms(ev, corpus)
        idea = " y ".join(f"«{t}»" for t in terms) if terms else "el ejemplo que se muestra"
        return (f"Pista: en [{ts}]{slide} fíjate en {idea}. "
                "¿Cómo se relaciona eso con tu pregunta?")
    if level == STEP:
        line = _first_line(ev).split()
        half = " ".join(line[:max(4, len(line) // 2)])
        return (f"Primer paso: en [{ts}]{slide} la idea empieza así: «{half}…». "
                "¿Cómo completarías el resto con tus palabras?")
    detail = ""
    if ev.slide:
        body = ev.slide.text.strip()
        if ev.slide.title and body.startswith(ev.slide.title):
            body = body[len(ev.slide.title):].strip()
        detail = f"En la diapositiva {ev.slide.unit_no} («{ev.slide.title}»): {_quote(body, 35).rstrip('.…')}… "
    said = _quote(" ".join(s.text for s in ev.window[:3]) or (ev.anchor.text if ev.anchor else ""), 30)
    return cap_words(f"{detail}En la clase [{ts}] se dijo: «{said}». "
                     "Para comprobar: ¿cómo lo aplicarías en un flujo propio?")


def mock_evaluate(ev: Evidence, corpus: Corpus, attempt: str) -> tuple[str, str]:
    raw = strip_accents(attempt.lower())
    reference = set().union(*(stems(s.text) for s in ev.window)) if ev.window else set()
    if ev.slide:
        reference |= set(stems(f"{ev.slide.title} {ev.slide.text}"))
    if ev.chapter:
        reference |= set(stems(ev.chapter.title))
    surf = _surface(attempt)
    hits = [t for t in dict.fromkeys(stems(attempt)) if t in reference and t not in ev.query and len(t) >= 3]
    named = ", ".join(f"«{surf.get(t, t)}»" for t in hits[:3])
    if not hits or (DONT_KNOW.search(raw) and len(hits) < 2):
        return WRONG, "No pasa nada, todavía no es lo que se explicó en esa parte."
    if len(hits) >= 3:
        return CORRECT, f"¡Bien! Mencionaste {named}, que es justo lo que se explicó."
    return PARTIAL, f"Vas bien con {named}; te falta completar la idea."


# --- LLM ---

def _context(ev: Evidence, chapters: list[Chapter]) -> dict[str, str]:
    ch = "\n".join(f"- [{fmt_ts(c.start)}–{fmt_ts(c.end)}] {c.title}{' (tramo clave)' if c.is_key else ''}"
                   for c in chapters)
    material = "\n".join(f"- {u.source_file}, {u.kind} {u.unit_no}: {u.title}. {_quote(u.text, 80)}" for u in ev.units)
    transcript = "\n".join(f"[{fmt_ts(s.start)}] {s.text}" for s in ev.fragments)
    return {"CHAPTERS": ch or "(sin capítulos)", "MATERIAL": material or "(sin material)",
            "TRANSCRIPT": transcript}


def llm_turn(ev: Evidence, chapters: list[Chapter], history: list[ChatMessage], level: int, protected: bool,
             task: str) -> llm.TutorOut:
    quiz_rule = ("Esta pregunta se parece a una del quiz que el alumno aún no aprueba: quédate en ubicación y "
                 "pistas, sin revelar la respuesta." if protected else "No hay restricciones adicionales.")
    system = llm.load_prompt("tutor_system", QUIZ_RULE=quiz_rule, **_context(ev, chapters))
    messages = [{"role": "system", "content": system}]
    messages += [{"role": m.role, "content": m.text} for m in history[-config.CHAT_HISTORY_TURNS:]]
    messages.append({"role": "user", "content": f"{task}\nResponde en el nivel {level}."})
    return llm.complete_json(messages, llm.TutorOut)


# --- hilo ---

@dataclass
class TutorReply:
    reply: str
    level: int | None = None
    citations: list[dict] = field(default_factory=list)
    slides: list[dict] = field(default_factory=list)
    asks_student: bool = False
    evaluation: str | None = None
    thread_id: int | None = None
    buttons: list[dict] = field(default_factory=list)
    closed: bool = True

    def meta(self) -> dict:
        return {"citations": self.citations, "slides": self.slides, "asks_student": self.asks_student,
                "evaluation": self.evaluation, "buttons": self.buttons, "closed": self.closed}


def buttons_for(thread: ChatThread) -> list[dict]:
    if thread.closed:
        return []
    out = []
    if thread.level < min(STEP, thread.max_level):
        out.append({"action": HINT_ACTION, "label": "Dame una pista" if thread.level == LOCATE else "Otra pista"})
    if thread.max_level >= EXPLAIN and thread.level < EXPLAIN and (thread.level >= STEP or thread.attempts >= 2):
        out.append({"action": EXPLAIN_ACTION, "label": "Ver explicación"})
    out.append({"action": CLOSE_ACTION, "label": "Ya lo entendí"})
    return out


def _next_key(chapters: list[Chapter], ev: Evidence) -> str:
    after = ev.chapter.start if ev.chapter else -1
    keys = [c for c in chapters if c.is_key and not c.logistics and (not ev.chapter or c.id != ev.chapter.id)]
    nxt = next((c for c in keys if c.start > after), keys[0] if keys else None)
    if not nxt:
        return " Cuando quieras, pon a prueba lo aprendido en el quiz de la sesión."
    return f" Sigue con el tramo clave «{nxt.title}» en [{fmt_ts(nxt.start)}]."


def _slides_json(ev: Evidence) -> list[dict]:
    if not ev.slide:
        return []
    u = ev.slide
    return [{"file": u.source_file, "n": u.unit_no, "title": u.title, "text": _quote(u.text, 120)}]


class Tutor:
    def __init__(self, db: Session, session: ClassSession, user: User, now: datetime):
        self.db, self.session, self.user, self.now = db, session, user, now
        self.chapters = db.exec(select(Chapter).where(Chapter.session_id == session.id).order_by(Chapter.start)).all()
        self.corpus = corpus_of(db, session)
        self._quiz_cache: dict[str, QuizItem | None] = {}

    def _history(self, thread: ChatThread) -> list[ChatMessage]:
        return self.db.exec(select(ChatMessage).where(ChatMessage.thread_id == thread.id)
                            .order_by(ChatMessage.at, ChatMessage.id)).all()

    def _protected_item(self, thread: ChatThread) -> QuizItem | None:
        if thread.max_level > config.CHAT_QUIZ_MAX_LEVEL:
            return None
        return self._quiz_item(thread.question)

    def _quiz_item(self, question: str) -> QuizItem | None:
        if question not in self._quiz_cache:
            self._quiz_cache[question] = quiz_match(self.db, self.session, question, self.corpus)
        return self._quiz_cache[question]

    def _write(self, ev: Evidence, thread: ChatThread, level: int, task: str, prefix: str = "") -> str:
        """Redacta el turno (LLM o simulación) y aplica las reglas: cita, 90 palabras y protección del quiz."""
        llm.track("tutor")
        protected = self._protected_item(thread)
        text = ""
        if not config.LLM_MOCK:
            try:
                text = llm_turn(ev, self.chapters, self._history(thread), level, bool(protected), task).reply.strip()
                text = f"{prefix} {text}".strip()
            except llm.LLMError as exc:
                log.error("Fallo del tutor, se usa la simulación: %s", exc)
        if not text:
            text = f"{prefix} {mock_turn(ev, self.corpus, thread.question, level)}".strip()
        if ev.anchor and not TS_PATTERN.search(text):
            text += f" Revísalo en [{ev.ts}]."
        if protected and leaks_answer(text, protected):
            text = (f"Esto se trabaja en [{ev.ts}]{ev.slide_text}. Como se parece a una pregunta del quiz, te ayudo "
                    "con la ubicación y pistas hasta que lo apruebes. ¿Qué recuerdas de esa parte?")
        return cap_words(text)

    def _finish(self, thread: ChatThread, ev: Evidence, text: str, asks: bool, evaluation: str | None = None) -> TutorReply:
        self.db.add(thread)
        self.db.flush()
        res = TutorReply(reply=text, level=thread.level,
                         citations=[{"sec": s, "label": fmt_ts(s)} for s in parse_citations(text)],
                         slides=_slides_json(ev), asks_student=asks and not thread.closed, evaluation=evaluation,
                         thread_id=thread.id, buttons=buttons_for(thread), closed=thread.closed)
        self.db.add(ChatMessage(user_id=self.user.id, session_id=self.session.id, role="assistant", text=text,
                                at=self.now, thread_id=thread.id, level=thread.level,
                                meta_json=json.dumps(res.meta(), ensure_ascii=False)))
        self.db.commit()
        return res

    def ask(self, question: str, kind: str) -> TutorReply:
        ev = find_evidence(self.corpus, self.chapters, question)
        user_msg = ChatMessage(user_id=self.user.id, session_id=self.session.id, role="user", text=question,
                               kind=kind, at=self.now)
        if not ev.found:
            self.db.add(user_msg)
            res = TutorReply(reply=config.CHAT_NOT_FOUND)
            self.db.add(ChatMessage(user_id=self.user.id, session_id=self.session.id, role="assistant",
                                    text=res.reply, at=self.now, meta_json=json.dumps(res.meta(), ensure_ascii=False)))
            self.db.commit()
            return res
        protected = self._quiz_item(question) and not passed_quiz(self.db, self.session, self.user.id)
        thread = ChatThread(user_id=self.user.id, session_id=self.session.id, question=question,
                            chapter_id=ev.chapter.id if ev.chapter else None, level=LOCATE,
                            max_level=config.CHAT_QUIZ_MAX_LEVEL if protected else EXPLAIN, created_at=self.now)
        self.db.add(thread)
        self.db.flush()
        user_msg.thread_id = thread.id
        self.db.add(user_msg)
        text = self._write(ev, thread, LOCATE, f"Pregunta del alumno: {question}")
        return self._finish(thread, ev, text, asks=True)

    def act(self, thread: ChatThread, action: str, message: str = "") -> TutorReply:
        ev = find_evidence(self.corpus, self.chapters, thread.question)
        shown = message if action == ANSWER_ACTION else ACTION_TEXT[action]
        self.db.add(ChatMessage(user_id=self.user.id, session_id=self.session.id, role="user", text=shown,
                                at=self.now, thread_id=thread.id, level=thread.level))
        if thread.closed:
            return self._finish(thread, ev, "Este tema ya quedó cerrado. Si te queda una duda, escríbela como "
                                f"una pregunta nueva. Lo puedes repasar en [{ev.ts}].", asks=False)
        if action == CLOSE_ACTION:
            thread.closed = True
            return self._finish(thread, ev, "¡Genial! Damos por cerrado este tema." + _next_key(self.chapters, ev),
                                asks=False)
        if action == ANSWER_ACTION:
            return self._evaluate(thread, ev, message)
        target = thread.level + 1 if action == HINT_ACTION else EXPLAIN
        if action == EXPLAIN_ACTION and thread.level < STEP and thread.attempts < 2:
            target = thread.level + 1
        if target > thread.max_level:
            text = (f"Como esta pregunta se parece a una del quiz, te ayudo solo con la ubicación y pistas hasta que "
                    f"lo apruebes. Vuelve a mirar [{ev.ts}]{ev.slide_text}: ¿qué responderías con tus palabras?")
            return self._finish(thread, ev, text, asks=True)
        thread.level = min(target, EXPLAIN)
        task = {HINT: "El alumno pidió una pista.", STEP: "El alumno pidió otra pista.",
                EXPLAIN: "El alumno pidió ver la explicación."}[thread.level]
        text = self._write(ev, thread, thread.level, task)
        return self._finish(thread, ev, text, asks=True)

    def _evaluate(self, thread: ChatThread, ev: Evidence, attempt: str) -> TutorReply:
        thread.attempts += 1
        evaluation, feedback = mock_evaluate(ev, self.corpus, attempt)
        llm.track("tutor")
        if not config.LLM_MOCK:
            try:
                out = llm_turn(ev, self.chapters, self._history(thread), thread.level, bool(self._protected_item(thread)),
                               f"Evalúa este intento del alumno: «{attempt}»")
                evaluation = out.evaluation or evaluation
                reply = out.reply.strip()
                feedback = reply if evaluation == CORRECT else (re.split(r"(?<=[.!])\s+", reply)[0] or feedback)
            except llm.LLMError as exc:
                log.error("Fallo de la evaluación del intento, se usa la simulación: %s", exc)
        thread.evaluation = evaluation
        if evaluation == CORRECT:
            thread.closed = True
            text = cap_words(feedback + _next_key(self.chapters, ev))
            return self._finish(thread, ev, text, asks=False, evaluation=evaluation)
        target = min(thread.max_level, EXPLAIN, max(thread.level + 1, thread.attempts + 1))
        if target == thread.level:
            text = (f"{feedback} Como esta pregunta se parece a una del quiz, sigo con pistas: vuelve a mirar "
                    f"[{ev.ts}]{ev.slide_text}. ¿Qué cambiarías en tu respuesta?")
            return self._finish(thread, ev, cap_words(text), asks=True, evaluation=evaluation)
        thread.level = target
        text = self._write(ev, thread, target, f"El intento del alumno fue {evaluation}: «{attempt}». Ya se le dio "
                           "la retroalimentación; ahora continúa con la ayuda de este nivel.", prefix=feedback)
        return self._finish(thread, ev, text, asks=True, evaluation=evaluation)


def hard_topics(db: Session, session_id: int) -> list[dict]:
    """Capítulos con más hilos que llegaron al nivel 3 o 4 (para el tablero)."""
    chapters = db.exec(select(Chapter).where(Chapter.session_id == session_id).order_by(Chapter.start)).all()
    threads = db.exec(select(ChatThread).where(ChatThread.session_id == session_id)).all()
    out = []
    for c in chapters:
        mine = [t for t in threads if t.chapter_id == c.id]
        hard = [t for t in mine if t.level >= STEP]
        if mine:
            out.append({"chapter_id": c.id, "title": c.title, "start": c.start, "is_key": c.is_key,
                        "threads": len(mine), "hard": len(hard), "students": len({t.user_id for t in mine}),
                        "correct": sum(t.evaluation == CORRECT for t in mine)})
    return sorted(out, key=lambda r: (-r["hard"], -r["threads"], r["start"]))
