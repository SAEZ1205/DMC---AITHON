"""scripts/verify_nvidia_live.py: UNA solicitud corta, con texto ficticio y solo con autorización explícita (simulada aquí)."""
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.config import LLMConfig
from app.llm.nvidia_provider import PROBE_PROMPT

from llm_helpers import FAKE_NVIDIA_KEY, NVIDIA_CONFIG, REASONING_SECRET, FakeKimi, json_response, nv_completion, nv_error

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_nvidia_live.py"
FLAG = "--i-authorize-one-nvidia-request"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("verify_nvidia_live", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(script, capsys, argv, fake=None, config=NVIDIA_CONFIG):
    fake = fake or FakeKimi(lambda r, p: json_response(nv_completion(content="Una variable es un nombre que guarda un valor.")))
    code = script.main(argv, config=config, client=fake.client())
    return code, capsys.readouterr().out, fake


def test_the_fictional_prompt_is_exactly_the_authorized_one():
    assert PROBE_PROMPT == "Explica brevemente qué es una variable en programación."


def test_without_the_explicit_flag_no_request_is_made(script, capsys):
    code, out, fake = run(script, capsys, [])
    assert code == 2 and fake.calls == 0 and "No se hizo ninguna solicitud" in out and FLAG in out


def test_without_a_key_no_request_is_made_and_the_user_is_told_how_to_configure_it(script, capsys):
    code, out, fake = run(script, capsys, [FLAG], config=replace(NVIDIA_CONFIG, api_key=""))
    assert code == 2 and fake.calls == 0 and "NVIDIA_API_KEY" in out and ".env" in out


@pytest.mark.parametrize("tokens", ["5", "99999", "-1"])
def test_out_of_range_token_limits_are_rejected_before_any_request(script, capsys, tokens):
    code, out, fake = run(script, capsys, [FLAG, "--max-tokens", tokens])
    assert code == 2 and fake.calls == 0 and "--max-tokens" in out


def test_an_unsafe_base_url_aborts_before_any_request(script, capsys):
    code, out, fake = run(script, capsys, [FLAG], config=replace(NVIDIA_CONFIG, base_url="https://evil.example.com/v1"))
    assert code == 2 and fake.calls == 0 and "nvidia.com" in out


def test_with_authorization_exactly_one_short_request_with_fictional_text_is_sent(script, capsys):
    code, out, fake = run(script, capsys, [FLAG])
    assert code == 0 and fake.calls == 1
    body, req = fake.payloads[0], fake.requests[0]
    assert str(req.url) == "https://integrate.api.nvidia.com/v1/chat/completions" and req.method == "POST"
    assert body["messages"] == [{"role": "user", "content": PROBE_PROMPT}]                 # solo la pregunta ficticia
    assert body["model"] == "moonshotai/kimi-k3" and body["stream"] is False and body["reasoning_effort"] == "low"
    assert body["max_completion_tokens"] == 600                                            # límite pequeño y sin el mínimo del tutor
    assert set(body) == {"model", "messages", "max_completion_tokens", "stream", "reasoning_effort"}


def test_the_report_shows_status_model_and_that_nvidia_generated_the_answer_without_secrets(script, capsys):
    code, out, fake = run(script, capsys, [FLAG, "--max-tokens", "400"])
    assert code == 0 and fake.payloads[0]["max_completion_tokens"] == 400
    assert "Código de estado HTTP" in out and "200" in out
    assert "moonshotai/kimi-k3" in out and "Generada realmente por NVIDIA : SÍ" in out and "integrate.api.nvidia.com" in out
    assert "Una variable es un nombre" in out and "total_tokens" in out
    assert "Razonamiento recibido         : sí (no se muestra ni se guarda)" in out
    for secret in (FAKE_NVIDIA_KEY, REASONING_SECRET, "Authorization", "Bearer"):
        assert secret not in out
    assert "NO activa el chat" in out


@pytest.mark.parametrize("response, code_in_out", [
    (nv_error(401), "llm_auth_error"), (nv_error(403), "llm_auth_error"), (nv_error(404), "llm_request_rejected"),
    (nv_error(429), "llm_rate_limited"), (nv_error(500), "llm_unavailable"),
])
def test_errors_stop_immediately_without_retries_or_secrets(script, capsys, response, code_in_out):
    fake = FakeKimi(lambda r, p: response)
    code, out, fake = run(script, capsys, [FLAG], fake=fake)
    assert code == 1 and fake.calls == 1 and f"[{code_in_out}]" in out
    assert "no se hacen reintentos automáticos" in out and "RESULTADO: FALLÓ" in out
    assert FAKE_NVIDIA_KEY not in out and "detalle interno" not in out


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("lento"), httpx.ConnectError("sin red")])
def test_network_errors_stop_after_one_attempt(script, capsys, exc):
    def boom(request, payload):
        raise exc
    code, out, fake = run(script, capsys, [FLAG], fake=FakeKimi(boom))
    assert code == 1 and fake.calls == 1 and "FALLÓ" in out and FAKE_NVIDIA_KEY not in out


def test_a_reasoning_only_answer_that_ran_out_of_tokens_is_reported_clearly(script, capsys):
    body = nv_completion(content="", finish_reason="length")
    code, out, fake = run(script, capsys, [FLAG], fake=FakeKimi(lambda r, p: json_response(body)))
    assert code == 1 and fake.calls == 1 and "sube --max-tokens" in out and REASONING_SECRET not in out


def test_the_script_never_runs_anything_on_import_and_documents_the_authorization():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in text and FLAG in text and "NUNCA se ejecuta sola" in text
    assert "nvapi-" not in text
