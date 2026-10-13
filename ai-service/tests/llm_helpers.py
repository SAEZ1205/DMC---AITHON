"""
Kimi SIMULADO para las pruebas.

Todo pasa por `httpx.MockTransport`: no se abre ningún socket, no se consume ningún token facturable y la
clave usada es un valor inventado. Estas pruebas verifican NUESTRO código (contexto, verificación, fallback);
no prueban que la API real de Kimi funcione -- eso solo lo demuestra una llamada real autorizada.
"""
import json
import re
from typing import Callable, List, Optional

import httpx

from app.config import LLMConfig

FAKE_KEY = "sk-FAKE-TEST-KEY-do-not-use-0000"

LIVE_CONFIG = LLMConfig(
    provider="kimi",
    api_key=FAKE_KEY,
    base_url="https://api.moonshot.ai/v1",
    model="kimi-k2.6",
    live_authorized=True,
    fallback_to_demo=True,
    timeout_seconds=5.0,
    max_output_tokens=500,
)


def completion(inner, finish_reason="stop", model="kimi-k2.6", usage=True, raw_content=None):
    """Cuerpo de una respuesta chat.completion; `inner` (dict) viaja como texto JSON en message.content."""
    content = raw_content if raw_content is not None else json.dumps(inner, ensure_ascii=False)
    body = {
        "id": "cmpl-test", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 321, "completion_tokens": 87, "total_tokens": 408}
    return body


def good_answer(label="S1"):
    return {
        "answer": f"El docente explica que ORDER BY ordena el resultado de la consulta [{label}]. "
                  "Sin él, no hay un orden garantizado.",
        "follow_up_question": "¿Cómo ordenarías de mayor a menor por calificación?",
        "used_segments": [label],
    }


class FakeKimi:
    """Transporte simulado que registra las peticiones y responde con `responder(request, payload)`."""

    def __init__(self, responder: Optional[Callable] = None):
        self.responder = responder or (lambda request, payload: httpx.Response(200, json=completion(good_answer())))
        self.requests: List[httpx.Request] = []
        self.payloads: List[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append(request)
        self.payloads.append(payload)
        return self.responder(request, payload)

    @property
    def calls(self) -> int:
        return len(self.requests)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    # utilidades para inspeccionar lo que "se habría enviado"
    def last_user_message(self) -> str:
        return self.payloads[-1]["messages"][-1]["content"]

    def labels_in_last_prompt(self) -> dict:
        """{'S1': '06:22–07:20', ...} según los <segmento> del último prompt."""
        return dict(re.findall(r'<segmento id="(S\d+)" minutos="([^"]+)">', self.last_user_message()))


def json_response(body, status=200):
    return httpx.Response(status, json=body)


def error_response(status, err_type="invalid_authentication_error"):
    return httpx.Response(status, json={"error": {"message": "mensaje interno del proveedor", "type": err_type}})


# --------------------------------------------------------------- NVIDIA Build (simulado)
from app.config import LLMConfig as _LLMConfig  # noqa: E402

FAKE_NVIDIA_KEY = "nvapi-FAKE-TEST-KEY-not-a-real-credential-0000"
REASONING_SECRET = "razonamiento-privado-NO-DEBE-SALIR"

NVIDIA_CONFIG = _LLMConfig(
    provider="nvidia",
    api_key=FAKE_NVIDIA_KEY,
    base_url="https://integrate.api.nvidia.com/v1",
    model="moonshotai/kimi-k3",
    live_authorized=True,
    fallback_to_demo=True,
    timeout_seconds=5.0,
    max_output_tokens=800,
    reasoning_effort="low",
)


def nv_completion(inner=None, finish_reason="stop", model="moonshotai/kimi-k3", reasoning=REASONING_SECRET, content=None,
                  reasoning_tokens=120):
    """Respuesta tipo NVIDIA: `content` (JSON del tutor) + `reasoning_content` aparte, como documenta el modelo."""
    message = {"role": "assistant", "content": content if content is not None else json.dumps(inner, ensure_ascii=False)}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "id": "chatcmpl-nv-test", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 300, "completion_tokens": 200, "total_tokens": 500,
                  "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}},
    }


def nv_error(status, title="Error", detail="detalle interno del servidor que no debe propagarse", headers=None):
    return httpx.Response(status, json={"status": status, "title": title, "detail": detail}, headers=headers or {})
