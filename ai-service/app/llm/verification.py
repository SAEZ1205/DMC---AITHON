"""
Verificación de la salida del LLM. Es la barrera entre "texto del modelo" y "respuesta al estudiante".

Qué garantiza (y qué no)
  * Las citas NUNCA vienen del modelo: el modelo solo nombra etiquetas (S1, S2…) de segmentos que el
    backend le entregó; aquí se validan contra esa lista y el backend construye las citas reales.
  * Los minutos del texto salen del backend (se sustituye [S1] por "(min 06:22–07:20)"); cualquier marca
    de tiempo escrita a mano por el modelo que no pertenezca a la evidencia hace rechazar la respuesta.
  * Se rechazan fugas del prompt, enlaces no presentes en la evidencia y afirmaciones sobre certificados,
    asistencia o aprobación que no estén en la evidencia ni en la pregunta del estudiante.
  * NO garantiza que la explicación sea fiel al 100 % al segmento: un LLM puede parafrasear mal. La
    verificación reduce el riesgo; no lo elimina. Por eso se muestra la cita textual verificable (minuto).
"""
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.knowledge.text_utils import normalize_text
from app.llm.demo_provider import shorten
from app.llm.errors import LLMMalformedResponse
from app.llm.prompts import CANARY, PROMPT_VERSION
from app.models.transcript import SegmentMatch

MAX_ANSWER_CHARS = 1600
MAX_QUESTION_CHARS = 300

_TAG_GROUP_RE = re.compile(r"\[\s*(S\d+(?:\s*[,;y]\s*S\d+)*)\s*\]", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"\[\s*S\d+[^\]]*\]", re.IGNORECASE)
_TIME_RE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?::\d{2})?(?![\d:])")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
_LEAK_MARKERS = ("system prompt", "prompt de sistema", "prompt del sistema", "instrucciones del sistema",
                 PROMPT_VERSION.lower())
# Temas sobre los que el asistente no puede afirmar nada que no esté en la evidencia o en la pregunta.
_SENSITIVE_STEMS = ("certificad", "certificacion", "asistencia", "aprobad", "aprobar", "aprobacion", "nota minima")


@dataclass(frozen=True)
class VerifiedAnswer:
    text: str                       # respuesta lista para mostrar (minutos reales, sin etiquetas)
    follow_up_question: Optional[str]
    cited_indices: Tuple[int, ...]  # posiciones en context.evidence realmente usadas


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def parse_model_json(content: str, lenient: bool = False) -> Dict[str, Any]:
    """
    Extrae el objeto JSON de la respuesta del modelo (tolera un bloque ```json```).
    `lenient=True` (modelos con razonamiento sin modo JSON garantizado): ignora bloques <think> y toma el objeto
    {...} más externo aunque haya texto alrededor. Sigue exigiendo un JSON completo y válido.
    """
    if not isinstance(content, str) or not content.strip():
        raise LLMMalformedResponse("contenido vacío")
    body = _THINK_RE.sub("", content).strip() if lenient else content.strip()
    fence = _FENCE_RE.match(body)
    if fence:
        body = fence.group(1)
    if lenient and not body.startswith("{") and "{" in body and "}" in body:
        body = body[body.index("{"): body.rindex("}") + 1]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMMalformedResponse(f"JSON inválido: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise LLMMalformedResponse("la respuesta no es un objeto JSON")
    return data


def verify_and_render(
    data: Dict[str, Any],
    label_to_index: Dict[str, int],
    evidence: List[SegmentMatch],
    student_message: str,
) -> VerifiedAnswer:
    """
    Valida `data` (ya parseado) contra la evidencia entregada y devuelve el texto final.
    Lanza LLMMalformedResponse ante cualquier incumplimiento.
    """
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise LLMMalformedResponse("falta 'answer'")

    # 1. etiquetas usadas -> posiciones reales de la evidencia
    used = data.get("used_segments")
    if not isinstance(used, list) or not used or not all(isinstance(u, str) for u in used):
        raise LLMMalformedResponse("'used_segments' debe ser una lista no vacía de etiquetas")
    indices: List[int] = []
    for raw in used:
        label = raw.strip().upper()
        if label not in label_to_index:
            raise LLMMalformedResponse(f"etiqueta inexistente en used_segments: {raw!r}")
        idx = label_to_index[label]
        if not 0 <= idx < len(evidence):
            raise LLMMalformedResponse("etiqueta fuera de la evidencia")
        if idx not in indices:
            indices.append(idx)

    # 2. etiquetas dentro del texto: solo válidas; se sustituyen por el minuto real del backend
    def _render(match: "re.Match[str]") -> str:
        labels = [x.strip().upper() for x in re.split(r"[,;y]", match.group(1)) if x.strip()]
        minutes = []
        for label in labels:
            if label not in label_to_index:
                raise LLMMalformedResponse(f"etiqueta inexistente en el texto: {label}")
            seg = evidence[label_to_index[label]].segment
            minutes.append(f"min {seg.timestamp_range}")
        return "(" + "; ".join(minutes) + ")"

    rendered = _TAG_GROUP_RE.sub(_render, answer)
    if _ANY_TAG_RE.search(rendered):
        raise LLMMalformedResponse("etiqueta mal formada en el texto")

    # 3. marcas de tiempo: solo las que el backend conoce (rangos de la evidencia entregada)
    allowed_times = set()
    for m in evidence:
        allowed_times.update(m.segment.timestamp_range.split("–"))
        allowed_times.update(_TIME_RE.findall(m.segment.text))
    for stamp in _TIME_RE.findall(rendered):
        if stamp not in allowed_times:
            raise LLMMalformedResponse(f"marca de tiempo no respaldada: {stamp}")

    # 4. fuga del prompt / token canario
    low = rendered.lower()
    if CANARY.lower() in low or any(marker in low for marker in _LEAK_MARKERS):
        raise LLMMalformedResponse("la respuesta filtra instrucciones internas")

    # 5. enlaces: solo si aparecen en la evidencia
    evidence_text = " ".join(m.segment.text for m in evidence)
    for url in _URL_RE.findall(rendered):
        if url.rstrip(".,;)") not in evidence_text:
            raise LLMMalformedResponse("la respuesta incluye un enlace no respaldado")

    # 6. afirmaciones sensibles (certificados, asistencia, aprobación) sin respaldo
    supported = normalize_text(evidence_text + " " + student_message)
    shown = normalize_text(rendered)
    for stem in _SENSITIVE_STEMS:
        if stem in shown and stem not in supported:
            raise LLMMalformedResponse(f"afirmación sensible sin respaldo ({stem})")

    # 7. longitud y pregunta socrática
    rendered = shorten(rendered.strip(), MAX_ANSWER_CHARS)
    question = data.get("follow_up_question")
    if isinstance(question, str):
        question = question.strip()
        if not question or len(question) > MAX_QUESTION_CHARS or not question.endswith("?"):
            question = None
        elif _problem_in_aux_text(question, label_to_index, allowed_times, supported):
            question = None
    else:
        question = None

    # 8. el texto debe mostrar el minuto de cada segmento citado (aunque el modelo olvide la etiqueta)
    missing = [i for i in indices if f"min {evidence[i].segment.timestamp_range}" not in rendered]
    if missing:
        sources = "; ".join(f"min {evidence[i].segment.timestamp_range}" for i in missing)
        rendered = f"{rendered}\n\nFuente: {sources}."

    text = rendered if question is None else f"{rendered}\n\n{question}"
    return VerifiedAnswer(text=text, follow_up_question=question, cited_indices=tuple(indices))


def _problem_in_aux_text(text: str, label_to_index, allowed_times, supported: str) -> bool:
    """La pregunta de seguimiento sigue las mismas reglas básicas (sin etiquetas, tiempos, enlaces, filtraciones)."""
    if _ANY_TAG_RE.search(text) or _URL_RE.search(text):
        return True
    if any(stamp not in allowed_times for stamp in _TIME_RE.findall(text)):
        return True
    low = text.lower()
    return CANARY.lower() in low or any(marker in low for marker in _LEAK_MARKERS)
