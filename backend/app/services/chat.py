"""Chat de la grabación: responde solo con la transcripción de la sesión y cita el minuto.
También clasifica preguntas (contenido/logística/otro) y respuestas a micro-preguntas."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from .. import config
from ..models import Chapter, ChatMessage, ClassSession, TranscriptSegment
from . import llm
from .processing import chapters_text
from .text import SegmentIndex, fmt_ts, parse_citations, strip_accents

log = logging.getLogger("dmc.chat")


@dataclass
class ChatAnswer:
    answer: str
    citations: list[int]
    kind: str


def _segment_line(s: TranscriptSegment) -> str:
    return f"[{fmt_ts(s.start)}] {s.speaker + ': ' if s.speaker else ''}{s.text}"


def build_context(segments: list[TranscriptSegment], chapters: list[Chapter], question: str) -> tuple[str, str]:
    """Capítulos + transcripción completa; si supera el 70 % de la ventana, solo los fragmentos más relevantes (BM25)."""
    full = "\n".join(_segment_line(s) for s in segments)
    ch_text = chapters_text(chapters)
    if llm.estimate_tokens(full + ch_text) <= config.CHAT_CONTEXT_RATIO * config.LLM_CONTEXT_TOKENS:
        return ch_text, full
    index = SegmentIndex([s.text for s in segments])
    top = sorted(i for i, _ in index.search(question, config.CHAT_TOP_FRAGMENTS))
    return ch_text, "\n".join(_segment_line(segments[i]) for i in top)


def _mock_answer(segments: list[TranscriptSegment], question: str) -> str:
    index = SegmentIndex([s.text for s in segments])
    hits = index.search(question, 3)
    if not hits or hits[0][1] < 1.5:
        return config.CHAT_NOT_FOUND
    picked: list[int] = []
    for i, score in hits:
        if score < hits[0][1] * 0.6:
            break
        if segments[i].text.rstrip().endswith("?") and i + 1 < len(segments):
            i += 1  # si el fragmento es una pregunta de un alumno, responde con lo que dijo el docente después
        if i not in picked:
            picked.append(i)
    best = sorted((segments[i] for i in picked[:2]), key=lambda s: s.start)
    parts = [f"{s.text} [{fmt_ts(s.start)}]" for s in best]
    return "Según la clase: " + " ".join(parts)


LOGISTIC_WORDS = ("entrega", "fecha", "nota", "certificad", "pago", "horario", "material", "grabacion", "asistencia",
                  "proyecto", "plazo", "vence", "tarea", "examen")


def classify_question(message: str) -> str:
    if not config.LLM_MOCK:
        try:
            return llm.complete_json([{"role": "user", "content": llm.load_prompt("classify_question", MESSAGE=message)}],
                                     llm.QuestionKindOut, model=config.LLM_MODEL_FAST).kind
        except llm.LLMError as exc:
            log.warning("Clasificación de pregunta en modo simulado: %s", exc)
    text = strip_accents(message.lower())
    if any(w in text for w in LOGISTIC_WORDS):
        return "logistica"
    return "contenido" if "?" in message or len(text.split()) > 3 else "otro"


def answer_question(db: Session, session: ClassSession, user_id: int, message: str, now: datetime) -> ChatAnswer:
    segments = db.exec(select(TranscriptSegment).where(TranscriptSegment.session_id == session.id)
                       .order_by(TranscriptSegment.start)).all()
    chapters = db.exec(select(Chapter).where(Chapter.session_id == session.id).order_by(Chapter.start)).all()
    history = db.exec(select(ChatMessage).where(ChatMessage.session_id == session.id, ChatMessage.user_id == user_id)
                      .order_by(ChatMessage.at.desc(), ChatMessage.id.desc()).limit(config.CHAT_HISTORY_TURNS * 2)).all()
    history = list(reversed(history))

    if not segments:
        text = config.CHAT_NOT_FOUND
    elif config.LLM_MOCK:
        text = _mock_answer(segments, message)
    else:
        ch_text, transcript = build_context(segments, chapters, message)
        system = llm.load_prompt("chat_system", NOT_FOUND=config.CHAT_NOT_FOUND, CHAPTERS=ch_text, TRANSCRIPT=transcript)
        messages = [{"role": "system", "content": system}]
        messages += [{"role": m.role, "content": m.text} for m in history]
        messages.append({"role": "user", "content": message})
        try:
            text = llm.complete(messages)
        except llm.LLMError as exc:
            log.error("Fallo del chat, se usa el modo simulado: %s", exc)
            text = _mock_answer(segments, message)

    kind = classify_question(message)
    db.add(ChatMessage(user_id=user_id, session_id=session.id, role="user", text=message, kind=kind, at=now))
    db.add(ChatMessage(user_id=user_id, session_id=session.id, role="assistant", text=text, at=now))
    db.commit()
    return ChatAnswer(answer=text, citations=parse_citations(text), kind=kind)


MICRO_KEYWORDS = [
    ("trabajo", ("trabajo", "chamba", "empleo", "turno", "jefe", "oficina")),
    ("tiempo", ("tiempo", "horario", "tarde", "noche", "no llego", "cruza")),
    ("ritmo", ("rapido", "ritmo", "lento", "atras")),
    ("contenido", ("tema", "contenido", "dificil", "cuesta", "entiendo", "ejercicio")),
    ("satisfaccion", ("gusta", "util", "excelente", "bueno", "malo", "aburr")),
]
NEGATIVE = ("no ", "cuesta", "dificil", "tarde", "cruza", "atras", "imposible", "cansad", "aburr", "malo", "poco")
POSITIVE = ("bien", "gusta", "excelente", "acomoda", "util", "genial", "ayuda", "puedo")


def classify_micro(question: str, answer: str) -> llm.MicroOut:
    if not config.LLM_MOCK:
        try:
            return llm.complete_json([{"role": "user", "content": llm.load_prompt(
                "classify_micro", QUESTION=question, ANSWER=answer)}], llm.MicroOut, model=config.LLM_MODEL_FAST)
        except llm.LLMError as exc:
            log.warning("Clasificación de micro-pregunta en modo simulado: %s", exc)
    text = " " + strip_accents(answer.lower()) + " "
    topic = next((t for t, words in MICRO_KEYWORDS if any(w in text for w in words)), "otro")
    neg, pos = sum(w in text for w in NEGATIVE), sum(w in text for w in POSITIVE)
    sentiment = "negativo" if neg > pos else "positivo" if pos > neg else "neutro"
    return llm.MicroOut(topic=topic, sentiment=sentiment, summary=" ".join(answer.split()[:15]))
