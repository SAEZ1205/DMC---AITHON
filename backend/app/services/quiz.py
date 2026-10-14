"""Quiz del docente con la IA como asistente (spec v2, 3.4).

El docente es el autor: el alumno solo ve preguntas `aprobada`, y el quiz se activa con al menos
QUIZ_MIN_APPROVED. La IA trabaja solo cuando el docente se lo pide y todo lo que propone queda en `borrador`.
"""
from __future__ import annotations

import json
import logging
import random
import re

from sqlmodel import Session, select

from .. import config
from ..models import Chapter, ClassSession, QuizItem
from . import llm, materials
from .summary import key_sentences, sentences, slide_bullets
from .text import fmt_ts, parse_citations
from .transcript_txt import parse_ts

log = logging.getLogger("dmc.quiz")
APPROVED, DRAFT, DISCARDED = "aprobada", "borrador", "descartada"
FROM_TEACHER, FROM_AI = "docente", "propuesta_ia"
NEGATIONS = re.compile(r"\b(no|nunca|excepto|salvo|ninguna?|jam[aá]s)\b", re.IGNORECASE)


def minute_seconds(item: QuizItem) -> float | None:
    if item.minute:
        try:
            return parse_ts(item.minute)
        except ValueError:
            pass
    cites = parse_citations(item.explanation or "")
    return float(cites[0]) if cites else None


def review_hint(item: QuizItem) -> dict:
    """Retroalimentación de active recall para una pregunta fallada: dónde repasar, sin la respuesta."""
    seconds = minute_seconds(item)
    where = f"el minuto [{fmt_ts(seconds)}]" if seconds is not None else "el capítulo correspondiente"
    slide = f" (diapositiva {item.slide})" if item.slide else ""
    return {"seconds": seconds, "slide": item.slide, "text": f"Revisa {where}{slide}."}


def approved_items(db: Session, session_id: int) -> list[QuizItem]:
    return db.exec(select(QuizItem).where(QuizItem.session_id == session_id, QuizItem.status == APPROVED)).all()


def is_active(approved_count: int) -> bool:
    return approved_count >= config.QUIZ_MIN_APPROVED


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def bank_counts(items: list[QuizItem]) -> dict:
    live = [q for q in items if q.status != DISCARDED]
    teacher = sum(q.source == FROM_TEACHER for q in live)
    ai = [q for q in live if q.source == FROM_AI]
    ai_edited = sum(q.edited_by_teacher for q in ai)
    approved = sum(q.status == APPROVED for q in live)
    text = f"{_n(len(live), 'pregunta', 'preguntas')}: {teacher} del docente"
    if ai:
        text += f", {_n(len(ai), 'propuesta', 'propuestas')} por IA"
        if ai_edited == len(ai):
            text += " y " + ("editada" if len(ai) == 1 else "editadas")
        elif ai_edited:
            text += f" ({ai_edited} {'editada' if ai_edited == 1 else 'editadas'})"
    missing = max(0, config.QUIZ_MIN_APPROVED - approved)
    return {"total": len(live), "approved": approved, "drafts": sum(q.status == DRAFT for q in live),
            "discarded": len(items) - len(live), "from_teacher": teacher, "from_ai": len(ai),
            "ai_edited": ai_edited, "active": is_active(approved), "missing": missing, "text": text,
            "warning": None if not missing else (
                f"Quiz inactivo: {'falta 1 pregunta aprobada' if missing == 1 else f'faltan {missing} preguntas aprobadas'}. "
                "Mientras tanto, los alumnos ven «Quiz en preparación» y no pueden recuperar esta sesión.")}


def item_json(q: QuizItem, teacher_names: dict[int, str] | None = None) -> dict:
    return {"id": q.id, "question": q.question, "options": json.loads(q.options_json), "answer_idx": q.answer_idx,
            "explanation": q.explanation, "minute": q.minute, "slide": q.slide, "status": q.status,
            "source": q.source, "edited_by_teacher": q.edited_by_teacher,
            "approved_by": (teacher_names or {}).get(q.approved_by) if q.approved_by else None,
            "approved_at": q.approved_at.isoformat() if q.approved_at else None}


# --- material de la sesión para las acciones de la IA ---

def _chapter_material(db: Session, session: ClassSession) -> list[dict]:
    from ..models import TranscriptSegment

    chapters = db.exec(select(Chapter).where(Chapter.session_id == session.id).order_by(Chapter.start)).all()
    deck = {u.unit_no: u for u in materials.deck_units(db, session.id)}
    segs = db.exec(select(TranscriptSegment).where(TranscriptSegment.session_id == session.id)
                   .order_by(TranscriptSegment.start)).all()
    sents = sentences(segs)
    out = []
    for ch in chapters:
        lo, hi = sorted([ch.slide_from or 0, ch.slide_to or ch.slide_from or 0])
        units = [deck[n] for n in range(lo, hi + 1) if n in deck]
        cached = json.loads(ch.key_sentences_json or "[]")
        if not cached:
            inside = [(t, s) for t, s in sents if ch.start <= t < ch.end]
            cached = key_sentences(inside, " ".join(f"{u.title} {u.text}" for u in units), 2)
        out.append({"chapter": ch, "bullets": slide_bullets(units), "sentences": cached,
                    "slide": ch.slide_from, "slide_titles": [u.title for u in units]})
    return out


def _key_content_text(material: list[dict]) -> str:
    lines = []
    for m in material:
        ch = m["chapter"]
        if not ch.is_key:
            continue
        slide = f" · diapositiva {m['slide']}" if m["slide"] else ""
        lines.append(f"## [{fmt_ts(ch.start)}–{fmt_ts(ch.end)}] {ch.title}{slide}")
        lines += [f"- {b}" for b in m["bullets"]]
        lines += [f"- [{fmt_ts(s['start'])}] «{s['text']}»" for s in m["sentences"]]
    return "\n".join(lines) or "(la sesión no tiene tramos clave marcados)"


def _covered(items: list[QuizItem], ch: Chapter) -> bool:
    return any((s := minute_seconds(q)) is not None and ch.start <= s < ch.end for q in items if q.status != DISCARDED)


def _trim(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


# --- acciones ---

def propose(db: Session, session: ClassSession) -> list[QuizItem]:
    """«Proponer preguntas»: hasta 5 borradores sobre tramos clave que aún no tienen preguntas."""
    llm.track("quiz_assist")
    items = db.exec(select(QuizItem).where(QuizItem.session_id == session.id)).all()
    material = _chapter_material(db, session)
    targets = [m for m in material if m["chapter"].is_key and not _covered(items, m["chapter"])][:5]
    if not targets:
        return []
    proposals = None
    if not config.LLM_MOCK:
        try:
            proposals = _llm_propose(items, material, targets)
        except llm.LLMError as exc:
            log.warning("Propuesta de preguntas en modo simulado: %s", exc)
    if proposals is None:
        proposals = _mock_propose(targets, material)
    created = []
    for p in proposals[:5]:
        q = QuizItem(session_id=session.id, question=p["question"], options_json=json.dumps(p["options"], ensure_ascii=False),
                     answer_idx=p["answer_idx"], explanation=p.get("explanation", ""), minute=p.get("minute") or None,
                     slide=p.get("slide"), status=DRAFT, source=FROM_AI)
        db.add(q)
        created.append(q)
    db.commit()
    for q in created:
        db.refresh(q)
    return created


def _llm_propose(items: list[QuizItem], material: list[dict], targets: list[dict]) -> list[dict]:
    out = llm.complete_json([{"role": "user", "content": llm.load_prompt(
        "quiz_assist", ACTION="Proponer preguntas",
        INSTRUCTIONS=(f"Propón hasta {len(targets)} preguntas de opción múltiple (4 alternativas, una correcta) "
                      "sobre los tramos clave que todavía no tienen preguntas: "
                      + "; ".join(m["chapter"].title for m in targets)
                      + ". Cada una con el minuto (mm:ss o h:mm:ss) y la diapositiva que la respaldan. "
                      "Los distractores deben ser errores comunes reales del tema, no opciones absurdas."),
        KEY_CONTENT=_key_content_text(material), EXISTING="\n".join(f"- {q.question}" for q in items) or "(ninguna)",
        ITEM="", FORMAT='{"items": [{"question": "...", "options": ["...", "...", "...", "..."], '
                        '"answer_idx": 0, "explanation": "...", "minute": "mm:ss", "slide": 1}]}')}],
        llm.QuizProposalOut)
    return [p.model_dump() for p in out.items]


def _mock_propose(targets: list[dict], material: list[dict]) -> list[dict]:
    proposals = []
    for m in targets:
        ch = m["chapter"]
        others = [o for o in material if o["chapter"].id != ch.id and not o["chapter"].logistics]
        others.sort(key=lambda o: abs(o["chapter"].start - ch.start))
        if m["bullets"]:
            correct = m["bullets"][0]
            pool = [b for o in others for b in o["bullets"][:1]]
        else:
            correct = _trim(m["sentences"][0]["text"]) if m["sentences"] else ch.title
            pool = [_trim(o["sentences"][0]["text"]) if o["sentences"] else o["chapter"].title for o in others]
        distractors = [p for p in dict.fromkeys(pool) if p != correct][:3]
        if len(distractors) < 3:
            continue
        start = m["sentences"][0]["start"] if m["sentences"] else ch.start
        slide = f" (diapositiva {m['slide']})" if m["slide"] else ""
        proposals.append({"question": f"¿Cuál de estas ideas corresponde a «{ch.title}»?",
                          "options": [correct, *distractors], "answer_idx": 0,
                          "explanation": f"Se explica en [{fmt_ts(start)}]{slide}.",
                          "minute": fmt_ts(start), "slide": m["slide"]})
    return proposals


def alternatives(db: Session, session: ClassSession, item: QuizItem) -> list[str]:
    """«Sugerir alternativas»: 3 distractores plausibles para una pregunta del docente. No modifica la pregunta."""
    llm.track("quiz_assist")
    options = json.loads(item.options_json)
    correct = options[item.answer_idx]
    if not config.LLM_MOCK:
        try:
            out = llm.complete_json([{"role": "user", "content": llm.load_prompt(
                "quiz_assist", ACTION="Sugerir alternativas",
                INSTRUCTIONS="Propón 3 distractores plausibles para la pregunta: errores comunes reales del tema, con "
                             "un largo y estilo parecidos a la respuesta correcta. No cambies la pregunta ni la respuesta.",
                KEY_CONTENT=_key_content_text(_chapter_material(db, session)), EXISTING="(no aplica)",
                ITEM=f"Pregunta: {item.question}\nRespuesta correcta: {correct}",
                FORMAT='{"distractors": ["...", "...", "..."]}')}], llm.DistractorsOut)
            return out.distractors
        except llm.LLMError as exc:
            log.warning("Alternativas en modo simulado: %s", exc)
    material = [m for m in _chapter_material(db, session) if not m["chapter"].logistics]
    pool = [b for m in material for b in m["bullets"]] + \
        [_trim(s["text"]) for m in material for s in m["sentences"]] + [m["chapter"].title for m in material]
    pool = [p for p in dict.fromkeys(pool) if p not in options]
    random.Random(item.id).shuffle(pool)
    return pool[:3]


def review(db: Session, session: ClassSession, item: QuizItem) -> dict:
    """«Revisar redacción»: señala ambigüedades y sugiere una versión, sin reemplazar el texto del docente."""
    llm.track("quiz_assist")
    if not config.LLM_MOCK:
        options = json.loads(item.options_json)
        try:
            out = llm.complete_json([{"role": "user", "content": llm.load_prompt(
                "quiz_assist", ACTION="Revisar redacción",
                INSTRUCTIONS="Señala ambigüedades: dos alternativas que podrían ser correctas, una pista en el "
                             "enunciado, negaciones confusas o alternativas de largo muy distinto. Sugiere una versión "
                             "mejorada del enunciado; no la reemplaces.",
                KEY_CONTENT=_key_content_text(_chapter_material(db, session)), EXISTING="(no aplica)",
                ITEM=f"Pregunta: {item.question}\nAlternativas: {options}\nCorrecta: {options[item.answer_idx]}",
                FORMAT='{"issues": ["..."], "suggestion": "..."}')}], llm.ReviewOut)
            return out.model_dump()
        except llm.LLMError as exc:
            log.warning("Revisión de redacción en modo simulado: %s", exc)
    return review_rules(item)


def review_rules(item: QuizItem) -> dict:
    options = json.loads(item.options_json)
    issues, suggestion = [], item.question
    lowered = [o.strip().lower() for o in options]
    if len(set(lowered)) < len(lowered):
        issues.append("Hay alternativas repetidas.")
    lengths = [len(o) for o in options]
    others = [n for i, n in enumerate(lengths) if i != item.answer_idx]
    if others and lengths[item.answer_idx] > 1.6 * (sum(others) / len(others)):
        issues.append("La alternativa correcta es bastante más larga que las demás: puede delatarla.")
    if NEGATIONS.search(item.question):
        issues.append("El enunciado tiene una negación: puede confundir. Resáltala o pregunta en positivo.")
        suggestion = NEGATIONS.sub(lambda m: m.group(0).upper(), item.question)
    q_words = {w for w in re.findall(r"\w{5,}", item.question.lower())}
    correct_words = {w for w in re.findall(r"\w{5,}", options[item.answer_idx].lower())}
    shared = q_words & correct_words - {w for i, o in enumerate(options) if i != item.answer_idx
                                         for w in re.findall(r"\w{5,}", o.lower())}
    if shared:
        issues.append(f"El enunciado repite palabras de la respuesta correcta ({', '.join(sorted(shared))}): es una pista.")
    if any(o in {"todas las anteriores", "ninguna de las anteriores"} for o in lowered):
        issues.append("Evita «todas/ninguna de las anteriores»: no funcionan bien con alternativas mezcladas.")
    return {"issues": issues or ["No encontré ambigüedades evidentes."], "suggestion": suggestion}
