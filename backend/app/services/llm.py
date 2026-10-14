"""Cliente único compatible con OpenAI (Kimi por defecto). El proveedor se cambia con LLM_BASE_URL,
LLM_API_KEY y LLM_MODEL_*. Sin API key (o con LLM_MOCK=1) los servicios usan su modo simulado."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from functools import lru_cache
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from .. import config

log = logging.getLogger("dmc.llm")
T = TypeVar("T", bound=BaseModel)
PURPOSES = ("tutor", "micro", "quiz_assist")
CALLS: Counter = Counter()


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


# --- cliente ---

@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    return OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, timeout=180)


def load_prompt(name: str, **values: str) -> str:
    text = (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def estimate_tokens(text: str) -> int:
    return int(len(text) / 3.5)


def complete(messages: list[dict], model: str | None = None, json_mode: bool = False) -> str:
    if config.LLM_MOCK:
        raise LLMError("LLM en modo simulado")
    kwargs: dict = {"model": model or config.LLM_MODEL_CHAT, "messages": messages}
    if config.LLM_TEMPERATURE:
        kwargs["temperature"] = float(config.LLM_TEMPERATURE)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = _client().chat.completions.create(**kwargs)
    except Exception as exc:  # algunos proveedores no aceptan response_format
        if json_mode and "response_format" in str(exc):
            kwargs.pop("response_format")
            resp = _client().chat.completions.create(**kwargs)
        else:
            raise LLMError(str(exc)) from exc
    return (resp.choices[0].message.content or "").strip()


def _extract_json(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end != -1 else text


def complete_json(messages: list[dict], schema: type[T], model: str | None = None) -> T:
    """Pide JSON y lo valida con Pydantic. Reintenta una vez; si vuelve a fallar, registra y lanza LLMError."""
    last_error = ""
    for attempt in range(2):
        msgs = list(messages)
        if attempt:
            msgs.append({"role": "user", "content": f"Tu respuesta anterior no era JSON válido ({last_error}). "
                                                    "Responde solo con el objeto JSON pedido."})
        raw = complete(msgs, model=model, json_mode=True)
        try:
            return schema.model_validate(json.loads(_extract_json(raw)))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:300]
            log.warning("Validación fallida (%s, intento %d): %s", schema.__name__, attempt + 1, last_error)
    log.error("No se obtuvo JSON válido para %s", schema.__name__)
    raise LLMError(f"JSON inválido para {schema.__name__}: {last_error}")
