"""Cliente único compatible con OpenAI (Groq con openai/gpt-oss-120b por defecto). El proveedor se cambia con
LLM_BASE_URL, LLM_API_KEY y LLM_MODEL_*. Sin API key (o con LLM_MOCK=1) los servicios usan su modo simulado.

Cada llamada reserva cupo (solicitudes y tokens por minuto y por día, con margen). Si no hay cupo en
LLM_MAX_WAIT_S segundos, o el proveedor responde 429, se lanza LLMError y el servicio usa su simulación."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import Counter, OrderedDict, deque
from functools import lru_cache
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from .. import config

log = logging.getLogger("dmc.llm")
T = TypeVar("T", bound=BaseModel)
PURPOSES = ("tutor", "micro", "quiz_assist")
CALLS: Counter = Counter()
FALLBACKS: Counter = Counter()


def track(purpose: str) -> None:
    """Cuenta cada operación que usa el LLM (o su simulación). Solo hay tres usos: tutor, micro y quiz_assist."""
    if purpose not in PURPOSES:
        raise ValueError(f"Uso de LLM no previsto: {purpose}")
    CALLS[purpose] += 1
    log.info("LLM [%s] llamada %d (%s) · totales %s", purpose, CALLS[purpose],
             "simulado" if config.LLM_MOCK else config.LLM_MODEL_CHAT, dict(CALLS))


def usage() -> dict:
    return {p: CALLS[p] for p in PURPOSES}


class LLMError(RuntimeError):
    pass


# --- esquemas de salida ---

class Citation(BaseModel):
    sec: float
    label: str = ""


class SlideRef(BaseModel):
    file: str = ""
    n: int


class TutorOut(BaseModel):
    reply: str
    level: int = Field(ge=1, le=4)
    citations: list[Citation] = Field(default_factory=list)
    slides: list[SlideRef] = Field(default_factory=list)
    asks_student: bool = True
    evaluation: Literal["correcta", "parcial", "incorrecta"] | None = None


class QuizItemOut(BaseModel):
    question: str
    options: list[str] = Field(min_length=4, max_length=4)
    answer_idx: int = Field(ge=0, le=3)
    explanation: str = ""


class QuizProposalItem(QuizItemOut):
    minute: str = ""
    slide: int | None = None


class QuizProposalOut(BaseModel):
    items: list[QuizProposalItem] = Field(max_length=5)


class DistractorsOut(BaseModel):
    distractors: list[str] = Field(min_length=3, max_length=3)


class ReviewOut(BaseModel):
    issues: list[str] = Field(default_factory=list)
    suggestion: str = ""


class MicroOut(BaseModel):
    topic: Literal["tiempo", "trabajo", "contenido", "ritmo", "satisfaccion", "otro"]
    sentiment: Literal["positivo", "neutro", "negativo"]
    summary: str = ""


# --- cupo del proveedor ---

# Tokens de salida por tipo de respuesta (incluyen el razonamiento de gpt-oss). El proveedor descuenta
# max_tokens del cupo por minuto al recibir la solicitud, así que conviene no pedir de más.
MAX_OUTPUT = {"TutorOut": 1024, "MicroOut": 400, "QuizProposalOut": 3000, "DistractorsOut": 800, "ReviewOut": 1000}
DEFAULT_MAX_OUTPUT = 1024


def parse_duration(text: str | None) -> float:
    """Duraciones de los encabezados del proveedor: «2», «7.66s», «2m59.56s», «450ms» → segundos."""
    if not text:
        return 0.0
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    units = {"ms": 0.001, "h": 3600.0, "m": 60.0, "s": 1.0}
    return sum(float(v) * units[u] for v, u in re.findall(r"([\d.]+)(ms|h|m|s)", text))


class Budget:
    """Cupo con margen LLM_SAFETY. Los tokens por minuto son un balde que se rellena de forma continua (como
    en Groq); las solicitudes por minuto, una ventana móvil; el día, contadores. Vive en la instancia: los
    encabezados x-ratelimit del proveedor, que miden a toda la organización, lo corrigen."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.requests: deque[float] = deque()
        self.level = float(self.limits()["tpm"])
        self.refilled_at = clock()
        self.day = ""
        self.day_requests = 0
        self.day_tokens = 0
        self.blocked_until = 0.0
        self.server_tokens_left: int | None = None

    @staticmethod
    def limits() -> dict[str, int]:
        s = config.LLM_SAFETY
        return {"rpm": int(config.LLM_RPM * s), "tpm": int(config.LLM_TPM * s),
                "rpd": int(config.LLM_RPD * s), "tpd": int(config.LLM_TPD * s)}

    def _refresh(self, now: float) -> None:
        tpm = self.limits()["tpm"]
        self.level = min(tpm, self.level + (now - self.refilled_at) * tpm / 60)
        self.refilled_at = now
        while self.requests and now - self.requests[0] >= 60:
            self.requests.popleft()
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.day:
            self.day, self.day_requests, self.day_tokens = today, 0, 0

    def _wait(self, tokens: int, now: float) -> float | None:
        """Segundos hasta tener cupo para `tokens` (0 = ya); None si no alcanza en lo que queda del día."""
        lim = self.limits()
        if tokens > lim["tpm"] or self.day_requests + 1 > lim["rpd"] or self.day_tokens + tokens > lim["tpd"]:
            return None
        wait = max(0.0, self.blocked_until - now, (tokens - self.level) * 60 / lim["tpm"])
        if len(self.requests) >= lim["rpm"]:
            wait = max(wait, self.requests[len(self.requests) - lim["rpm"]] + 60 - now)
        return wait

    def acquire(self, tokens: int, max_wait: float) -> int:
        deadline = self.clock() + max_wait
        while True:
            with self.lock:
                now = self.clock()
                self._refresh(now)
                wait = self._wait(tokens, now)
                if wait is None:
                    raise LLMError("Cupo diario del LLM agotado")
                if wait <= 0:
                    self.level -= tokens
                    self.requests.append(now)
                    self.day_requests += 1
                    self.day_tokens += tokens
                    return tokens
            if now + wait > deadline:
                raise LLMError(f"Sin cupo del LLM por {wait:.0f} s")
            self.sleep(wait)

    def settle(self, reserved: int, tokens: int) -> None:
        """Cambia la reserva por los tokens que el proveedor informó (0 si la solicitud no se procesó)."""
        with self.lock:
            self.level = min(self.limits()["tpm"], self.level + reserved - tokens)
            self.day_tokens += tokens - reserved

    def observe(self, headers) -> None:
        with self.lock:
            self._refresh(self.clock())
            left = headers.get("x-ratelimit-remaining-tokens")
            if left is not None and left.isdigit():
                self.server_tokens_left = int(left)
                self.level = min(self.level, int(left) - config.LLM_TPM * (1 - config.LLM_SAFETY))
            requests_left = headers.get("x-ratelimit-remaining-requests")
            if requests_left is not None and requests_left.isdigit() \
                    and int(requests_left) < config.LLM_RPD * (1 - config.LLM_SAFETY):
                self.blocked_until = max(self.blocked_until,
                                         self.clock() + parse_duration(headers.get("x-ratelimit-reset-requests")))

    def rate_limited(self, retry_after: float) -> None:
        with self.lock:
            self.blocked_until = max(self.blocked_until, self.clock() + max(retry_after, 1.0))

    def status(self) -> dict:
        with self.lock:
            now = self.clock()
            self._refresh(now)
            return {"limits": self.limits(),
                    "minute": {"requests": len(self.requests), "tokens_available": int(self.level)},
                    "day": {"requests": self.day_requests, "tokens": self.day_tokens},
                    "blocked_for_s": round(max(0.0, self.blocked_until - now), 1),
                    "server_tokens_left": self.server_tokens_left}


BUDGET = Budget()


class _Cache:
    """Respuestas validadas por prompt exacto: repetir una pregunta en la demo no gasta cupo."""

    def __init__(self, size: int = 256):
        self.size = size
        self.items: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self.lock = threading.Lock()

    @staticmethod
    def key(*parts) -> str:
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def get(self, key: str) -> dict | None:
        with self.lock:
            hit = self.items.get(key)
            if not hit or time.monotonic() - hit[0] > config.LLM_CACHE_TTL_S:
                return None
            self.items.move_to_end(key)
            return hit[1]

    def put(self, key: str, value: dict) -> None:
        with self.lock:
            self.items[key] = (time.monotonic(), value)
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)

    def clear(self) -> None:
        with self.lock:
            self.items.clear()


CACHE = _Cache()


def status() -> dict:
    return {"mode": "simulado" if config.LLM_MOCK else "real", "model": config.LLM_MODEL_CHAT,
            "base_url": config.LLM_BASE_URL, "usage": usage(), "fallbacks": dict(FALLBACKS),
            "cached_answers": len(CACHE.items), **BUDGET.status()}


# --- cliente ---

@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    return OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, timeout=config.LLM_TIMEOUT_S,
                  max_retries=0)


def _create(**kwargs):
    """Devuelve la respuesta cruda (encabezados + .parse())."""
    return _client().chat.completions.with_raw_response.create(**kwargs)


def load_prompt(name: str, **values: str) -> str:
    text = (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def estimate_tokens(text: str) -> int:
    return int(len(text) / 3.5)


def _retry_after(exc: Exception) -> float:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    return parse_duration(headers.get("retry-after")) or 10.0


def complete(messages: list[dict], model: str | None = None, json_mode: bool = False,
             max_tokens: int = DEFAULT_MAX_OUTPUT) -> str:
    if config.LLM_MOCK:
        raise LLMError("LLM en modo simulado")
    kwargs: dict = {"model": model or config.LLM_MODEL_CHAT, "messages": messages, "max_tokens": max_tokens}
    if config.LLM_TEMPERATURE:
        kwargs["temperature"] = float(config.LLM_TEMPERATURE)
    if config.LLM_REASONING_EFFORT:
        kwargs["reasoning_effort"] = config.LLM_REASONING_EFFORT
    if "groq.com" in config.LLM_BASE_URL:
        kwargs["extra_body"] = {"include_reasoning": False}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    reserved = estimate_tokens("".join(m["content"] for m in messages)) + 20 * len(messages) + max_tokens
    BUDGET.acquire(reserved, config.LLM_MAX_WAIT_S)
    try:
        raw = _create(**kwargs)
    except Exception as exc:
        BUDGET.settle(reserved, 0)
        if getattr(exc, "status_code", None) == 429:
            BUDGET.rate_limited(_retry_after(exc))
            raise LLMError("El proveedor del LLM pidió esperar (429)") from exc
        raise LLMError(str(exc)[:300]) from exc
    resp = raw.parse()
    BUDGET.settle(reserved, resp.usage.total_tokens if resp.usage else reserved)
    BUDGET.observe(raw.headers)
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        log.warning("Respuesta cortada por max_tokens=%d", max_tokens)
    return (choice.message.content or "").strip()


def _extract_json(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end != -1 else text


def complete_json(messages: list[dict], schema: type[T], model: str | None = None) -> T:
    """Pide JSON y lo valida con Pydantic. Reintenta una vez; si vuelve a fallar (o no hay cupo), lanza LLMError."""
    model = model or config.LLM_MODEL_CHAT
    key = CACHE.key(model, schema.__name__, messages)
    if (hit := CACHE.get(key)) is not None:
        return schema.model_validate(hit)
    last_error = ""
    try:
        for attempt in range(2):
            msgs = list(messages)
            if attempt:
                msgs.append({"role": "user", "content": f"Tu respuesta anterior no era JSON válido ({last_error}). "
                                                        "Responde solo con el objeto JSON pedido."})
            raw = complete(msgs, model=model, json_mode=True, max_tokens=MAX_OUTPUT.get(schema.__name__,
                                                                                        DEFAULT_MAX_OUTPUT))
            try:
                data = json.loads(_extract_json(raw))
                out = schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)[:300]
                log.warning("Validación fallida (%s, intento %d): %s", schema.__name__, attempt + 1, last_error)
                continue
            CACHE.put(key, data)
            return out
        log.error("No se obtuvo JSON válido para %s", schema.__name__)
        raise LLMError(f"JSON inválido para {schema.__name__}: {last_error}")
    except LLMError:
        if not config.LLM_MOCK:
            FALLBACKS[schema.__name__] += 1
        raise
