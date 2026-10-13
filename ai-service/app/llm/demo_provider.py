"""
Proveedor DEMO: respuestas deterministas con plantillas pedagógicas.

No genera texto libre. Todo lo que afirma sobre la clase es una cita textual de los
segmentos recibidos en `context.evidence`; el resto son frases de tutoría fijas.
Con el mismo contexto produce siempre la misma respuesta (útil para pruebas y demos).
"""
import re
from app.llm.base import FollowUpMode, LLMProvider, ResponseKind, TutorContext, TutorReply
from app.models.transcript import SegmentMatch

NO_EVIDENCE_TEXT = (
    "No encontré ese tema en la transcripción de esta clase. "
    "Podemos revisar otro fragmento o consultar al docente."
)

MAX_QUOTE_CHARS = 380

_SOCRATIC_QUESTIONS = (
    "Para comprobar que lo tienes claro: ¿cómo explicarías con tus palabras lo que dice el docente sobre {topic}?",
    "Cuéntame tu idea para contrastarla con el fragmento: ¿en qué situación de tu propia práctica aplicarías {topic}?",
    "Antes de seguir, piénsalo un momento: ¿qué crees que ocurriría si pasaras por alto lo que se explica sobre {topic}?",
)

_OPENINGS = {
    FollowUpMode.NONE: (
        "Esto es lo que se explica sobre «{topic}» en la grabación:",
        "Revisemos juntos qué dice la clase sobre «{topic}»:",
    ),
    FollowUpMode.MORE: (
        "Otro fragmento relacionado con «{topic}» es este:",
        "Además, la clase vuelve sobre «{topic}» aquí:",
    ),
    FollowUpMode.REPEAT: (
        "Volvamos a ese punto con calma, en palabras del docente:",
        "Repasemos el fragmento otra vez, paso a paso:",
    ),
}


def shorten(text: str, limit: int = MAX_QUOTE_CHARS) -> str:
    """Recorta una cita por una frontera de oración (o de palabra) sin alterar el texto original."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sentence_end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if sentence_end > limit * 0.5:
        return cut[: sentence_end + 1]
    return re.sub(r"\s+\S*$", "", cut).rstrip(",;:") + "…"


def _cite_line(match: SegmentMatch, with_lesson: bool) -> str:
    seg = match.segment
    where = f"min {seg.timestamp_range}"
    if with_lesson:
        where = f"{match.lesson_title}, {where}"
    return f"«{shorten(match.focus_text or seg.text)}» ({where})"


class DemoProvider(LLMProvider):
    name = "demo"
    mode = "DEMO"

    def generate(self, context: TutorContext) -> TutorReply:
        kind = context.kind
        if kind is ResponseKind.GROUNDED:
            return self._grounded(context)
        if kind is ResponseKind.NO_EVIDENCE:
            return TutorReply(NO_EVIDENCE_TEXT)
        if kind is ResponseKind.EXHAUSTED:
            topic = context.topic_label or "ese tema"
            return TutorReply(
                f"Ya te mostré todos los fragmentos de la transcripción que encontré sobre «{topic}». "
                "Podemos repasar alguno de ellos con más detalle o consultar al docente si quieres ir más allá."
            )
        if kind is ResponseKind.OUT_OF_SCOPE:
            scope = f"«{context.lesson_title}»" if context.lesson_title else f"el curso «{context.course_name}»"
            return TutorReply(
                f"Tu pregunta parece no estar relacionada con {scope}, así que no la respondo para no inventar contenido. "
                "Puedo ayudarte con lo que se explica en la clase: prueba con otra duda sobre sus temas o consulta al docente."
            )
        if kind is ResponseKind.NEEDS_CONTEXT:
            return TutorReply(
                "Todavía no tenemos un tema previo en esta conversación, así que no sé a qué te refieres. "
                "¿Sobre qué concepto de la clase quieres profundizar?"
            )
        if kind is ResponseKind.GREETING:
            scope = f"«{context.lesson_title}»" if context.lesson_title else f"el curso «{context.course_name}»"
            return TutorReply(
                f"¡Hola! Soy el asistente de DMC RescuePath AI (modo DEMO). Puedo ayudarte a repasar {scope} "
                "y a ubicar en qué minuto de la grabación se explica cada tema. "
                "¿Qué concepto te gustaría repasar?"
            )
        if kind is ResponseKind.CLOSING:
            return TutorReply(
                "¡Con gusto! Si te queda alguna duda, vuelve a los minutos de la grabación que te indiqué "
                "o consulta al docente. ¡Mucho éxito con tu aprendizaje!"
            )
        raise ValueError(f"Tipo de respuesta no soportado: {kind}")

    # ------------------------------------------------------------------
    def _grounded(self, context: TutorContext) -> TutorReply:
        if not context.evidence:
            # Defensa en profundidad: sin evidencia nunca se redacta una explicación.
            return TutorReply(NO_EVIDENCE_TEXT)

        primary, *others = context.evidence
        topic = context.topic_label or "este tema"
        variant = context.turn_number + primary.segment.segment_id

        openings = _OPENINGS[context.follow_up_mode]
        lessons_involved = {m.segment.lesson_id for m in context.evidence}
        with_lesson = context.lesson_title is None or len(lessons_involved) > 1

        parts = [
            openings[variant % len(openings)].format(topic=topic),
            _cite_line(primary, with_lesson),
        ]
        for extra in others:
            parts.append("También se menciona aquí: " + _cite_line(extra, with_lesson))

        question = _SOCRATIC_QUESTIONS[variant % len(_SOCRATIC_QUESTIONS)].format(topic=topic)
        parts.append(question)
        return TutorReply("\n\n".join(parts), follow_up_question=question)
