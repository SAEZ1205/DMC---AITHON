"""Chat de la grabación: plataforma y logística se responden sin LLM (chat_router); el contenido, con el tutor
(tutor_chat: escalera de ayuda y minuto exacto). También clasifica respuestas a micro-preguntas."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session

from .. import config
from ..models import ChatMessage, ChatThread, ClassSession, User
from . import chat_router, llm, tutor_chat
from .text import strip_accents

log = logging.getLogger("dmc.chat")


class ChatError(ValueError):
    pass


@dataclass
class ChatAnswer:
    answer: str
    kind: str | None
    route: str = chat_router.CONTENIDO
    citations: list[dict] = field(default_factory=list)
    link: dict | None = None
    level: int | None = None
    slides: list[dict] = field(default_factory=list)
    asks_student: bool = False
    evaluation: str | None = None
    thread_id: int | None = None
    buttons: list[dict] = field(default_factory=list)
    closed: bool = True

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def classify_question(message: str) -> str:
    route = chat_router.classify(message)
    if route != chat_router.CONTENIDO:
        return route
    return "contenido" if "?" in message or len(message.split()) > 3 else "otro"


def _routed(db: Session, session: ClassSession, user: User, message: str, route: str, now: datetime) -> ChatAnswer:
    res = (chat_router.platform_answer(db, session, user, now) if route == chat_router.PLATAFORMA
           else chat_router.logistics_answer(db, session))
    meta = json.dumps({"route": res.route, "link": res.link}, ensure_ascii=False)
    db.add(ChatMessage(user_id=user.id, session_id=session.id, role="user", text=message, kind=route, at=now))
    db.add(ChatMessage(user_id=user.id, session_id=session.id, role="assistant", text=res.text, at=now, meta_json=meta))
    db.commit()
    return ChatAnswer(answer=res.text, kind=route, route=route, link=res.link)


def _from_tutor(res: tutor_chat.TutorReply, kind: str | None) -> ChatAnswer:
    return ChatAnswer(answer=res.reply, kind=kind, citations=res.citations, level=res.level, slides=res.slides,
                      asks_student=res.asks_student, evaluation=res.evaluation, thread_id=res.thread_id,
                      buttons=res.buttons, closed=res.closed)


def answer_question(db: Session, session: ClassSession, user_id: int, message: str, now: datetime,
                    thread_id: int | None = None, action: str | None = None) -> ChatAnswer:
    """Mensaje nuevo → ruteo (plataforma, logística o tutor). Con `thread_id`, acción sobre un hilo del tutor:
    `answer` (intento del alumno), `hint`, `explain` o `close`."""
    user = db.get(User, user_id)
    if thread_id is not None or action:
        thread = db.get(ChatThread, thread_id) if thread_id is not None else None
        if not thread or thread.user_id != user_id or thread.session_id != session.id:
            raise ChatError("No encontramos esa conversación.")
        action = action or tutor_chat.ANSWER_ACTION
        if action not in tutor_chat.ACTIONS:
            raise ChatError(f"Acción no válida: {action}")
        if action == tutor_chat.ANSWER_ACTION and not message:
            raise ChatError("Escribe tu respuesta.")
        return _from_tutor(tutor_chat.Tutor(db, session, user, now).act(thread, action, message), None)
    kind = classify_question(message)
    if kind in (chat_router.PLATAFORMA, chat_router.LOGISTICA):
        return _routed(db, session, user, message, kind, now)
    return _from_tutor(tutor_chat.Tutor(db, session, user, now).ask(message, kind), kind)


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
    llm.track("micro")
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
