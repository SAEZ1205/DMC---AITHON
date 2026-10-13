"""
Proveedor LIVE: Kimi alojado en NVIDIA Build (`moonshotai/kimi-k3`), API de chat compatible con OpenAI.

Documentación consultada (build.nvidia.com/moonshotai/kimi-k3 y platform.kimi.ai/docs/guide/use-thinking-effort):
  * Endpoint : POST https://integrate.api.nvidia.com/v1/chat/completions   Authorization: Bearer <NVIDIA_API_KEY>
  * Tokens   : el ejemplo oficial usa `max_completion_tokens` (configurable a `max_tokens` con NVIDIA_MAX_TOKENS_PARAM).
  * Razonamiento: "Thinking is always enabled". Parámetro de nivel superior `reasoning_effort` = low | high | max (por
    defecto max). El razonamiento llega aparte, en `message.reasoning_content`: aquí se IGNORA (nunca se muestra, guarda
    ni registra). NO existe el parámetro `thinking` de Moonshot.
  * Entrada  : solo texto en esta integración.
  * NO documentado para NVIDIA (por eso no se envía por defecto): `response_format`, `temperature`, y cómo cuentan los
    tokens de razonamiento contra el límite de salida (se asume que cuentan: hay un mínimo de 512 tokens por llamada).

Diferencias deliberadas con `KimiProvider` (Moonshot), que NO se modifica:
  * Sin `thinking`, sin `response_format` por defecto, `reasoning_effort` siempre explícito (por defecto `low`).
  * Parseo tolerante: ignora `reasoning_content`, bloques <think> y texto alrededor del JSON.
  * Errores con mensajes precisos de acceso (401/403/404/429) y FRENO DE EMERGENCIA: tras un error de acceso o de cuota
    no se vuelve a llamar hasta que pase el enfriamiento (cero reintentos en bucle).
  * Candado de privacidad: el contenido de la clase REAL no sale hacia NVIDIA salvo NVIDIA_ALLOW_CLASS_CONTENT=true.
  * La URL base debe ser HTTPS y de *.nvidia.com: así la clave no puede enviarse por error a otro destino.

PRIVACIDAD: NVIDIA advierte que el servicio de prueba puede registrar entradas y salidas. Nunca se envía el video, ni la
transcripción completa, ni identificadores de estudiantes.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

import httpx

from app.config import LLMConfig
from app.llm.base import ResponseKind, TutorContext, TutorReply
from app.llm.demo_provider import DemoProvider
from app.llm.errors import (
    LLMAuthError, LLMContentNotAuthorized, LLMError, LLMMalformedResponse, LLMRateLimited, LLMRequestRejected,
    LLMTimeout, LLMUnavailable,
)
from app.llm.kimi_provider import KimiProvider, LiveCallBudget, SummaryPiece
from app.llm.verification import parse_model_json
from app.models.transcript import SegmentMatch

logger = logging.getLogger(__name__)

NVIDIA_HOST_SUFFIX = ".nvidia.com"
LOCAL_HOSTS = ("localhost", "127.0.0.1")
MIN_COMPLETION_TOKENS = 512                 # el razonamiento siempre activo consume parte del límite de salida
EFFORTS = ("low", "high", "max")
PAUSE_SECONDS = {401: 300.0, 402: 300.0, 403: 300.0, 404: 300.0, 429: 60.0}
MAX_PAUSE_SECONDS = 3600.0
PROBE_PROMPT = "Explica brevemente qué es una variable en programación."   # texto ficticio e inocuo


def validate_base_url(url: str) -> str:
    """HTTPS y host *.nvidia.com (o localhost, para pruebas locales): evita enviar la clave a un destino equivocado."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    is_local = host in LOCAL_HOSTS
    if parsed.scheme != "https" and not (is_local and parsed.scheme == "http"):
        raise ValueError("NVIDIA_BASE_URL debe usar https://")
    if not (host.endswith(NVIDIA_HOST_SUFFIX) or is_local):
        raise ValueError("NVIDIA_BASE_URL debe pertenecer a un dominio *.nvidia.com")
    if parsed.username or parsed.password:
        raise ValueError("NVIDIA_BASE_URL no debe incluir credenciales")
    return url.rstrip("/")


class _PausableBudget(LiveCallBudget):
    """Tope de llamadas que además respeta el freno de emergencia (una llamada en pausa no consume presupuesto)."""

    def __init__(self, limit: int):
        super().__init__(limit)
        self.owner: Optional["NvidiaKimiProvider"] = None

    def acquire(self) -> None:
        if self.owner is not None:
            self.owner._raise_if_paused()
        super().acquire()


@dataclass
class ProbeResult:
    ok: bool
    status_code: Optional[int] = None
    model: Optional[str] = None
    content: Optional[str] = None
    finish_reason: Optional[str] = None
    reasoning_present: bool = False
    usage: Dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    generated_by_nvidia: bool = False


class NvidiaKimiProvider(KimiProvider):
    name = "nvidia-kimi"
    mode = "LIVE"

    def __init__(
        self,
        config: LLMConfig,
        client: Optional[httpx.Client] = None,
        budget: Optional[LiveCallBudget] = None,
        local: Optional[DemoProvider] = None,
        clock: Callable[[], float] = time.monotonic,
        private_lessons: Iterable[Tuple[str, str]] = (),
    ):
        validate_base_url(config.base_url)
        pausable = None if budget is not None else _PausableBudget(config.max_live_calls)
        super().__init__(config, client=client, budget=budget or pausable, local=local)
        if pausable is not None:
            pausable.owner = self
        self._clock = clock
        self._pause_until = 0.0
        self._pause_error: Optional[LLMError] = None
        self._private: Set[Tuple[str, str]] = set(private_lessons)

    # =================================================== privacidad del contenido
    def restrict_lesson(self, course_id: str, lesson_id: str) -> None:
        """Marca una clase como PRIVADA (la clase real de DMC): su contenido no sale hacia NVIDIA sin autorización."""
        self._private.add((course_id, lesson_id))

    def content_allowed(self, course_id: str, lesson_id: Optional[str] = None) -> bool:
        if self.config.allow_class_content:
            return True
        if lesson_id is None:
            return not any(c == course_id for c, _ in self._private)
        return (course_id, lesson_id) not in self._private

    def _check_content(self, segments: Sequence[SegmentMatch]) -> None:
        for m in segments:
            if not self.content_allowed(m.segment.course_id, m.segment.lesson_id):
                raise LLMContentNotAuthorized(
                    "contenido de la clase real bloqueado", None,
                    public_message=("El contenido de la clase real no está autorizado para enviarse a NVIDIA "
                                    "(NVIDIA_ALLOW_CLASS_CONTENT=false); esta respuesta se generó en modo DEMO."),
                )

    # ==================================================================== API pública
    def generate(self, context: TutorContext) -> TutorReply:
        if context.kind is ResponseKind.GROUNDED:
            self._check_content(context.evidence)
        return super().generate(context)

    def summarize_segments(self, heading: str, segments: Sequence[SegmentMatch], instruction: str) -> SummaryPiece:
        self._check_content(segments)
        return super().summarize_segments(heading, segments, instruction)

    def probe(self, prompt: str = PROBE_PROMPT, max_tokens: int = 600, effort: str = "low") -> ProbeResult:
        """
        UNA sola solicitud de comprobación con texto ficticio. Sin reintentos y sin transformar la salida: informa
        estado, modelo y si la respuesta vino realmente del endpoint de NVIDIA. No pasa por el tutor ni por el recuperador.
        """
        started = time.monotonic()
        payload = {
            "model": self.config.model, "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": max_tokens, "stream": False,
            "reasoning_effort": effort if effort in EFFORTS else "low",
        }
        try:
            self._budget.acquire()
            body = self._post(payload, enforce_floor=False)
        except LLMError as exc:
            return ProbeResult(ok=False, status_code=exc.upstream_status, error_code=exc.code, error_message=exc.public_message,
                               elapsed_seconds=round(time.monotonic() - started, 2))
        choice = (body.get("choices") or [{}])[0] if isinstance(body.get("choices"), list) and body.get("choices") else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = self._text_of(message.get("content"))
        usage = self._usage(body)
        ok = bool(content.strip())
        return ProbeResult(
            ok=ok, status_code=200, model=body.get("model") or None, content=content.strip() or None,
            finish_reason=choice.get("finish_reason"), reasoning_present=bool(message.get("reasoning_content") or message.get("reasoning")),
            usage=usage, elapsed_seconds=round(time.monotonic() - started, 2),
            error_code=None if ok else "llm_empty_content",
            error_message=None if ok else ("La respuesta llegó sin contenido"
                                           + (" (el razonamiento agotó el límite de tokens: sube --max-tokens)" if choice.get("finish_reason") == "length" else "")),
            generated_by_nvidia=bool(body.get("choices")),
        )

    def provider_status(self) -> Dict[str, Any]:
        """Estado operativo para /health: 'ok' o 'paused' con el motivo preciso. Sin credenciales."""
        remaining = self._pause_until - self._clock()
        if self._pause_error is not None and remaining > 0:
            return {"state": "paused", "code": self._pause_error.code, "message": self._pause_error.public_message,
                    "upstream_status": self._pause_error.upstream_status, "retry_after_seconds": round(remaining)}
        return {"state": "ok", "code": None, "message": None, "upstream_status": None, "retry_after_seconds": 0}

    # ================================================================ HTTP
    def _payload(self, messages) -> Dict[str, Any]:
        cfg = self.config
        payload: Dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "max_completion_tokens": cfg.max_output_tokens,     # se renombra según NVIDIA_MAX_TOKENS_PARAM en _post
            "stream": False,
            "reasoning_effort": cfg.reasoning_effort if cfg.reasoning_effort in EFFORTS else "low",
        }
        if cfg.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key}", "Accept": "application/json"}

    def _post(self, payload: Dict[str, Any], enforce_floor: bool = True) -> Dict[str, Any]:
        self._raise_if_paused()
        body = dict(payload)
        tokens = body.pop("max_completion_tokens", None)
        if tokens is not None:
            if enforce_floor:
                tokens = max(int(tokens), MIN_COMPLETION_TOKENS)
            body[self.config.max_tokens_param] = tokens
        url = f"{self.config.base_url}/chat/completions"
        try:
            response = self._client.post(url, json=body, headers=self._headers(), timeout=self.config.timeout_seconds)
        except httpx.TimeoutException as exc:
            raise self._soft(LLMTimeout(f"timeout tras {self.config.timeout_seconds}s", None,
                                        public_message=f"NVIDIA no respondió en {self.config.timeout_seconds:g} s.")) from exc
        except httpx.HTTPError as exc:
            raise self._soft(LLMUnavailable(f"fallo de red: {type(exc).__name__}", None,
                                            public_message="No se pudo conectar con NVIDIA (red o DNS).")) from exc

        if response.status_code >= 400:
            err = self._http_error(response)
            self._maybe_pause(err, response)
            raise err
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMMalformedResponse("el cuerpo de la respuesta no es JSON", response.status_code) from exc
        if not isinstance(data, dict):
            raise LLMMalformedResponse("el cuerpo de la respuesta no es un objeto", response.status_code)
        return data

    # --------------------------------------------------------------- errores
    def _redact(self, text: str) -> str:
        key = self.config.api_key
        return text.replace(key, "***") if key else text

    @staticmethod
    def _soft(err: LLMError) -> LLMError:
        err.fallback_allowed = True
        return err

    def _http_error(self, response: httpx.Response) -> LLMError:
        status = response.status_code
        title = ""
        try:
            data = response.json()
            if isinstance(data, dict):
                err = data.get("error") if isinstance(data.get("error"), dict) else data
                title = str(err.get("title") or err.get("type") or err.get("code") or "")[:60]
        except ValueError:
            pass
        detail = self._redact(f"HTTP {status} {title}".strip())   # nunca se guarda el cuerpo: puede nombrar la cuenta o la petición
        model = self.config.model
        if status == 401:
            err: LLMError = LLMAuthError(detail, status, public_message=(
                "NVIDIA rechazó la credencial (HTTP 401): revisa NVIDIA_API_KEY en ai-service/.env."))
        elif status == 403:
            err = LLMAuthError(detail, status, public_message=(
                "NVIDIA denegó el acceso (HTTP 403): la clave no tiene permiso para este servicio o modelo."))
        elif status == 404:
            err = LLMRequestRejected(detail, status, public_message=(
                f"NVIDIA no encontró el modelo '{model}' (HTTP 404): no existe o no está habilitado para tu cuenta."))
        elif status == 402:
            err = LLMRateLimited(detail, status, public_message="Los créditos o la cuota de NVIDIA se agotaron (HTTP 402).")
        elif status == 408:
            err = LLMTimeout(detail, status, public_message="NVIDIA agotó el tiempo de espera (HTTP 408).")
        elif status == 429:
            err = LLMRateLimited(detail, status, public_message=(
                "Se alcanzó el límite de uso del nivel gratuito de NVIDIA (HTTP 429). Espera unos minutos; no se reintenta."))
        elif status >= 500:
            err = LLMUnavailable(detail, status, public_message=f"NVIDIA no está disponible temporalmente (HTTP {status}).")
        else:
            err = LLMRequestRejected(detail, status, public_message=(
                f"NVIDIA rechazó la petición (HTTP {status}): revisa NVIDIA_MODEL y NVIDIA_MAX_TOKENS_PARAM."))
        return self._soft(err)       # en NVIDIA, incluso un error de acceso degrada a DEMO con aviso visible

    def _maybe_pause(self, err: LLMError, response: httpx.Response) -> None:
        seconds = PAUSE_SECONDS.get(err.upstream_status or 0)
        if seconds is None:
            return
        retry_after = response.headers.get("retry-after", "")
        if err.upstream_status == 429 and retry_after.strip().isdigit():
            seconds = float(retry_after.strip())
        seconds = min(max(seconds, 1.0), MAX_PAUSE_SECONDS)
        self._pause_until = self._clock() + seconds
        self._pause_error = err
        logger.warning("NVIDIA en pausa %.0f s tras %s (HTTP %s); no se harán más solicitudes mientras tanto.",
                       seconds, err.code, err.upstream_status)

    def _raise_if_paused(self) -> None:
        remaining = self._pause_until - self._clock()
        if self._pause_error is not None and remaining > 0:
            base = self._pause_error
            raise self._soft(type(base)(
                base.detail + " [en pausa]", base.upstream_status,
                public_message=f"{base.public_message} Solicitudes en pausa {remaining:.0f} s para no repetir un error de acceso o cuota.",
            ))

    # ------------------------------------------------------------- respuesta
    @staticmethod
    def _text_of(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):                       # formato por partes (OpenAI): [{"type": "text", "text": "..."}]
            return "".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
        return ""

    @staticmethod
    def _usage(body: Dict[str, Any]) -> Dict[str, Any]:
        raw = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        usage = {k: raw.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(raw.get(k), int)}
        details = raw.get("completion_tokens_details")
        if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
            usage["reasoning_tokens"] = details["reasoning_tokens"]
        return usage

    @staticmethod
    def _extract_content(body: Dict[str, Any]):
        """Contenido final del modelo como texto JSON limpio. `reasoning_content` se ignora por completo."""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMMalformedResponse("falta 'choices'")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = NvidiaKimiProvider._text_of(message.get("content"))
        reasoned = bool(message.get("reasoning_content") or message.get("reasoning"))
        if choice.get("finish_reason") == "length":
            hint = " (el razonamiento siempre activo consume el límite: sube NVIDIA_MAX_OUTPUT_TOKENS)" if reasoned or not content.strip() else ""
            raise LLMMalformedResponse(f"respuesta truncada por el límite de tokens{hint}")
        if not content.strip():
            raise LLMMalformedResponse("contenido vacío" + (" (solo hubo razonamiento)" if reasoned else ""))
        data = parse_model_json(content, lenient=True)
        return json.dumps(data, ensure_ascii=False), NvidiaKimiProvider._usage(body)
