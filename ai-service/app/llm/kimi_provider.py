"""
Proveedor LIVE: API de chat-completions de Moonshot/Kimi (compatible con OpenAI).

Documentación oficial consultada (platform.kimi.ai/docs/api/chat):
  * Endpoint  : POST {base_url}/chat/completions  con  Authorization: Bearer <clave>
  * Base URL  : https://api.moonshot.ai/v1
  * Modelos   : kimi-k3, kimi-k2.6, kimi-k2.7-code, kimi-k2.7-code-highspeed
  * Salida    : `max_completion_tokens` (`max_tokens` está obsoleto)
  * JSON      : response_format {"type": "json_object"}
  * Razonamiento: kimi-k2.6 acepta thinking {"type": "disabled"}; kimi-k3 siempre razona
                  (reasoning_effort low|high|max). Sus tokens de razonamiento se facturan como salida.
  * No se envía `temperature`: la documentación consultada no indica sus límites por modelo.

COSTO: la API es de pago por tokens. Este módulo nunca se instancia sin clave + autorización explícita
(ver `get_provider`) y aplica un tope de llamadas por proceso.
"""
import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from app.config import LLMConfig
from app.llm.base import LLMProvider, ResponseKind, TutorContext, TutorReply
from app.llm.demo_provider import DemoProvider
from app.llm.errors import (
    LLMAuthError, LLMBudgetExceeded, LLMError, LLMMalformedResponse, LLMRateLimited,
    LLMRequestRejected, LLMTimeout, LLMUnavailable,
)
from app.llm.prompts import build_guardian_messages, build_messages, build_summary_messages
from app.llm.verification import parse_model_json, verify_and_render
from app.models.transcript import SegmentMatch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummaryPiece:
    """Resumen verificado de un fragmento: texto con minutos reales y posiciones de la evidencia que cita."""
    text: str
    cited_indices: Tuple[int, ...]
    usage: Optional[Dict[str, Any]] = None


class LiveCallBudget:
    """Tope de llamadas LIVE por proceso (0 = sin tope). Evita gastos descontrolados por error o abuso."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            if self.limit and self.used >= self.limit:
                raise LLMBudgetExceeded(f"tope de {self.limit} llamadas alcanzado")
            self.used += 1


class KimiProvider(LLMProvider):
    name = "kimi"
    mode = "LIVE"
    secondary_score_ratio = 0.5

    def __init__(
        self,
        config: LLMConfig,
        client: Optional[httpx.Client] = None,
        budget: Optional[LiveCallBudget] = None,
        local: Optional[DemoProvider] = None,
    ):
        if not config.api_key:
            raise ValueError("KimiProvider requiere LLM_API_KEY")
        self.config = config
        self.max_evidence = config.max_evidence
        self._client = client or httpx.Client(timeout=httpx.Timeout(config.timeout_seconds), follow_redirects=False)
        self._budget = budget or LiveCallBudget(config.max_live_calls)
        self._local = local or DemoProvider()   # respuestas estándar que no necesitan (ni deben gastar) un LLM

    # ------------------------------------------------------------------
    def generate(self, context: TutorContext) -> TutorReply:
        # Sin evidencia, seguimiento sin tema, saludos, etc.: plantillas fijas, sin llamar al modelo.
        if context.kind is not ResponseKind.GROUNDED:
            return self._local.generate(context)

        bundle = build_messages(context, self.config.max_history_messages)
        if not bundle.label_to_index:
            # Todos los segmentos fueron retenidos por parecer instrucciones: no hay evidencia utilizable.
            logger.warning("Segmentos retenidos por posible inyección de instrucciones: %d", bundle.skipped_segments)
            return self._no_usable_evidence(context)
        if bundle.skipped_segments or bundle.student_message_flagged:
            logger.warning(
                "Posible inyección de instrucciones (segmentos retenidos=%d, mensaje_marcado=%s)",
                bundle.skipped_segments, bundle.student_message_flagged,
            )

        self._budget.acquire()
        body = self._post(self._payload(bundle.messages))
        content, usage = self._extract_content(body)

        data = parse_model_json(content)
        verified = verify_and_render(data, bundle.label_to_index, context.evidence, context.student_message)
        logger.info("kimi ok model=%s tokens=%s", body.get("model", self.config.model), usage)
        return TutorReply(
            text=verified.text,
            follow_up_question=verified.follow_up_question,
            cited_indices=verified.cited_indices,
            provider=self.name,
            mode=self.mode,
            model=body.get("model") or self.config.model,
            usage=usage,
        )

    # -------------------------------------------------- guardián y resúmenes
    def classify_relevance(self, question: str, lesson_title: str, hints: Sequence[str]) -> Tuple[str, str]:
        """Guardián LLM: (etiqueta, motivo). Una llamada pequeña; recibe la pregunta y términos de la clase, no la transcripción."""
        self._budget.acquire()
        payload = self._payload(build_guardian_messages(question, lesson_title, hints))
        payload["max_completion_tokens"] = min(self.config.max_output_tokens, 150)
        content, usage = self._extract_content(self._post(payload))
        data = parse_model_json(content)
        label, reason = data.get("label"), data.get("reason")
        if label not in ("relevant", "not_relevant", "uncertain") or not isinstance(reason, str):
            raise LLMMalformedResponse("el guardián devolvió una etiqueta o un motivo inválidos")
        logger.info("kimi guardian ok label=%s tokens=%s", label, usage)
        return label, " ".join(reason.split())[:160]

    def summarize_segments(self, heading: str, segments: Sequence[SegmentMatch], instruction: str) -> SummaryPiece:
        """
        Resume UN fragmento (o reduce resúmenes de fragmentos) con la misma verificación de citas que el tutor.
        Lanza LLMError si no hay evidencia utilizable o la respuesta no pasa la verificación.
        """
        bundle = build_summary_messages(heading, segments, instruction)
        if not bundle.label_to_index:
            raise LLMMalformedResponse("ningún segmento utilizable para resumir")
        self._budget.acquire()
        content, usage = self._extract_content(self._post(self._payload(bundle.messages)))
        verified = verify_and_render(parse_model_json(content), bundle.label_to_index, list(segments), "")
        return SummaryPiece(text=verified.text, cited_indices=verified.cited_indices, usage=usage)

    # ------------------------------------------------------------------
    def _no_usable_evidence(self, context: TutorContext) -> TutorReply:
        from dataclasses import replace
        degraded = replace(context, kind=ResponseKind.NO_EVIDENCE, evidence=[])
        return replace(self._local.generate(degraded), cited_indices=())

    def _payload(self, messages) -> Dict[str, Any]:
        cfg = self.config
        payload: Dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "max_completion_tokens": cfg.max_output_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        model = cfg.model.lower()
        if model.startswith("kimi-k2.6"):
            payload["thinking"] = {"type": cfg.thinking if cfg.thinking in ("enabled", "disabled") else "disabled"}
        elif model.startswith("kimi-k3"):
            payload["reasoning_effort"] = cfg.reasoning_effort if cfg.reasoning_effort in ("low", "high", "max") else "low"
        return payload

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.config.base_url}/chat/completions"
        try:
            response = self._client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                timeout=self.config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"timeout tras {self.config.timeout_seconds}s") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"fallo de red: {type(exc).__name__}") from exc

        status = response.status_code
        if status >= 400:
            raise self._error_for(status, response)
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMMalformedResponse("el cuerpo de la respuesta no es JSON") from exc
        if not isinstance(body, dict):
            raise LLMMalformedResponse("el cuerpo de la respuesta no es un objeto")
        return body

    def _error_for(self, status: int, response: httpx.Response) -> LLMError:
        # Solo se conserva el tipo/código de error del proveedor; el mensaje puede contener datos de la petición.
        err_type = ""
        try:
            err = response.json().get("error", {})
            err_type = str(err.get("type") or err.get("code") or "")[:60]
        except (ValueError, AttributeError):
            pass
        detail = f"HTTP {status} {err_type}".strip()
        if status in (401, 403):
            return LLMAuthError(detail, status)
        if status in (408,):
            return LLMTimeout(detail, status)
        if status == 429:
            return LLMRateLimited(detail, status)
        if status >= 500:
            return LLMUnavailable(detail, status)
        return LLMRequestRejected(detail, status)

    @staticmethod
    def _extract_content(body: Dict[str, Any]):
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMMalformedResponse("falta 'choices'")
        choice = choices[0]
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if choice.get("finish_reason") == "length":
            raise LLMMalformedResponse("respuesta truncada por el límite de tokens (finish_reason=length)")
        if not isinstance(content, str) or not content.strip():
            raise LLMMalformedResponse("contenido vacío")
        usage_raw = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        usage = {k: usage_raw.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens")
                 if isinstance(usage_raw.get(k), int)}
        return content, usage

    def close(self) -> None:
        self._client.close()
