"""
NVIDIA Build (Kimi K3): proveedor, configuración, errores, privacidad y fallback. TODO con transporte simulado.

Garantías de estas pruebas: ninguna abre un socket (algunas lo bloquean a propósito), la clave es inventada y el
tutor/guardián nunca reciben respuestas reales. Prueban NUESTRO código, no que la API real de NVIDIA responda.
"""
import json
import re
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import LLMConfig, settings
from app.knowledge.retriever import KnowledgeRetriever
from app.llm import DemoProvider, KimiProvider, NvidiaKimiProvider, select_provider
from app.llm.base import ResponseKind, TutorContext
from app.llm.errors import (
    LLMAuthError, LLMContentNotAuthorized, LLMMalformedResponse, LLMRateLimited, LLMRequestRejected, LLMTimeout, LLMUnavailable,
)
from app.llm.nvidia_provider import MIN_COMPLETION_TOKENS, PROBE_PROMPT, validate_base_url
from app.llm.verification import parse_model_json
from app.main import create_app

from conftest import ADMIN_TOKEN, LESSON_1, N8N_COURSE, N8N_LESSON, chat_n8n, post_chat, rows
from llm_helpers import (
    FAKE_NVIDIA_KEY, NVIDIA_CONFIG, REASONING_SECRET, FakeKimi, good_answer, json_response, nv_completion, nv_error,
)

CHAT = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_OK = lambda request, payload: json_response(nv_completion(good_answer("S1")))      # noqa: E731


@pytest.fixture
def evidence(retriever):
    return retriever.retrieve_segments("sql-demo", "ORDER BY", lesson_id=LESSON_1, top_k=2)


def ctx(evidence, **kw):
    base = dict(kind=ResponseKind.GROUNDED, student_message="Explícame ORDER BY", course_name="SQL", lesson_title="Clase 1",
                topic_label="ORDER BY", evidence=evidence)
    base.update(kw)
    return TutorContext(**base)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def provider(fake=None, config=NVIDIA_CONFIG, clock=None, **kw):
    fake = fake or FakeKimi(DEFAULT_OK)
    return NvidiaKimiProvider(config, client=fake.client(), clock=clock or Clock(), **kw), fake


def nvidia_app(db_path, retriever, fake, config=NVIDIA_CONFIG, **kw):
    return create_app(db_path=db_path, retriever=retriever, llm_config=config, llm_client=fake.client(), **kw)


# ================================================================= configuración
def test_the_nvidia_defaults_match_the_official_configuration(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", FAKE_NVIDIA_KEY)
    cfg = settings.nvidia_config()
    assert (cfg.provider, cfg.model, cfg.base_url) == ("nvidia", "moonshotai/kimi-k3", "https://integrate.api.nvidia.com/v1")
    assert cfg.api_key == FAKE_NVIDIA_KEY and cfg.live_authorized is False and cfg.allow_class_content is False
    assert cfg.max_tokens_param == "max_completion_tokens" and cfg.json_mode is False and cfg.reasoning_effort == "low"
    assert FAKE_NVIDIA_KEY not in repr(cfg) and "<definida>" in repr(cfg)


def test_llm_provider_nvidia_uses_the_nvidia_variables_and_moonshot_keeps_its_own(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", FAKE_NVIDIA_KEY)
    monkeypatch.setattr(settings, "LLM_API_KEY", "clave-moonshot-inventada")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "nvidia")
    nv = settings.llm_config()
    assert (nv.provider, nv.api_key, nv.model) == ("nvidia", FAKE_NVIDIA_KEY, "moonshotai/kimi-k3") and "moonshot" not in nv.base_url
    monkeypatch.setattr(settings, "LLM_PROVIDER", "kimi")
    ms = settings.llm_config()
    assert (ms.provider, ms.api_key, ms.base_url) == ("kimi", "clave-moonshot-inventada", "https://api.moonshot.ai/v1")


def test_demo_stays_the_default_even_with_a_key_present(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", FAKE_NVIDIA_KEY)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "demo")
    assert settings.is_demo_mode is True
    monkeypatch.setattr(settings, "LLM_PROVIDER", "nvidia")
    assert settings.is_demo_mode is True                                   # clave sin LLM_LIVE_AUTHORIZED => DEMO
    monkeypatch.setattr(settings, "LLM_LIVE_AUTHORIZED", True)
    assert settings.is_demo_mode is False


@pytest.mark.parametrize("config, kind, reason", [
    (LLMConfig(provider="nvidia", api_key=""), DemoProvider, "falta NVIDIA_API_KEY"),
    (replace(NVIDIA_CONFIG, live_authorized=False), DemoProvider, "LLM_LIVE_AUTHORIZED"),
    (replace(NVIDIA_CONFIG, base_url="http://integrate.api.nvidia.com/v1"), DemoProvider, "configuración NVIDIA inválida"),
    (replace(NVIDIA_CONFIG, base_url="https://api.example.com/v1"), DemoProvider, "configuración NVIDIA inválida"),
    (NVIDIA_CONFIG, NvidiaKimiProvider, "LIVE autorizado (NVIDIA Build)"),
    (replace(NVIDIA_CONFIG, provider="kimi", base_url="https://api.moonshot.ai/v1", model="kimi-k2.6"), KimiProvider, "LIVE autorizado"),
])
def test_provider_selection_requires_key_and_authorization_and_a_safe_url(config, kind, reason):
    selection = select_provider(config)
    assert type(selection.provider) is kind and reason in selection.reason
    assert FAKE_NVIDIA_KEY not in repr(selection)


@pytest.mark.parametrize("url, ok", [
    ("https://integrate.api.nvidia.com/v1", True), ("https://INTEGRATE.API.NVIDIA.COM/v1/", True), ("https://x.nvidia.com/v1", True),
    ("http://localhost:9000/v1", True), ("http://127.0.0.1:8080/v1", True),
    ("http://integrate.api.nvidia.com/v1", False), ("https://nvidia.com.evil.example/v1", False), ("https://evilnvidia.com/v1", False),
    ("https://api.moonshot.ai/v1", False), ("https://user:pass@integrate.api.nvidia.com/v1", False), ("ftp://integrate.api.nvidia.com", False),
    ("integrate.api.nvidia.com/v1", False), ("", False),
])
def test_the_base_url_must_be_https_and_nvidia_so_the_key_cannot_leak_elsewhere(url, ok):
    if ok:
        assert validate_base_url(url) == url.rstrip("/")
    else:
        with pytest.raises(ValueError):
            validate_base_url(url)


def test_the_provider_refuses_to_exist_without_a_key():
    with pytest.raises(ValueError):
        NvidiaKimiProvider(replace(NVIDIA_CONFIG, api_key=""))


def test_existing_providers_are_untouched():
    assert KimiProvider.name == "kimi" and DemoProvider.name == "demo" and NvidiaKimiProvider.name == "nvidia-kimi"
    assert issubclass(NvidiaKimiProvider, KimiProvider)
    for method in ("_payload", "_post", "_extract_content", "_error_for"):
        assert KimiProvider.__dict__[method] is not NvidiaKimiProvider.__dict__.get(method, None)


# ======================================================================== petición
def test_the_request_follows_the_documented_nvidia_contract(evidence, no_network):
    p, fake = provider()
    p.generate(ctx(evidence))
    req, body = fake.requests[0], fake.payloads[0]
    assert req.method == "POST" and str(req.url) == CHAT
    assert req.headers["authorization"] == f"Bearer {FAKE_NVIDIA_KEY}" and req.headers["accept"] == "application/json"
    assert body["model"] == "moonshotai/kimi-k3" and body["stream"] is False
    assert body["reasoning_effort"] == "low" and body["max_completion_tokens"] == 800
    assert body["messages"][0]["role"] == "system" and body["messages"][-1]["role"] == "user"
    for moonshot_only in ("thinking", "response_format", "temperature", "max_tokens", "top_p", "tools"):
        assert moonshot_only not in body                                   # nada exclusivo de Moonshot ni no documentado
    assert FAKE_NVIDIA_KEY not in json.dumps(body) and FAKE_NVIDIA_KEY not in str(req.url)


@pytest.mark.parametrize("effort, sent", [("low", "low"), ("high", "high"), ("max", "max"), ("ultra", "low"), ("", "low")])
def test_reasoning_effort_is_always_explicit_and_validated(evidence, effort, sent):
    p, fake = provider(config=replace(NVIDIA_CONFIG, reasoning_effort=effort))
    p.generate(ctx(evidence))
    assert fake.payloads[0]["reasoning_effort"] == sent


def test_the_token_limit_parameter_name_is_configurable_and_a_floor_protects_against_reasoning_exhaustion(evidence):
    p, fake = provider(config=replace(NVIDIA_CONFIG, max_tokens_param="max_tokens", max_output_tokens=100))
    p.generate(ctx(evidence))
    body = fake.payloads[0]
    assert body["max_tokens"] == MIN_COMPLETION_TOKENS and "max_completion_tokens" not in body       # renombrado y con mínimo
    p2, fake2 = provider(config=replace(NVIDIA_CONFIG, max_output_tokens=3000))
    p2.generate(ctx(evidence))
    assert fake2.payloads[0]["max_completion_tokens"] == 3000


def test_json_mode_is_off_by_default_and_opt_in(evidence):
    p, fake = provider(config=replace(NVIDIA_CONFIG, json_mode=True))
    p.generate(ctx(evidence))
    assert fake.payloads[0]["response_format"] == {"type": "json_object"}


# ================================================================== respuestas
def test_a_successful_answer_keeps_the_verified_citation_and_is_labeled_nvidia(evidence):
    p, fake = provider()
    reply = p.generate(ctx(evidence))
    assert (reply.provider, reply.mode, reply.model) == ("nvidia-kimi", "LIVE", "moonshotai/kimi-k3")
    assert reply.cited_indices == (0,) and "(min 06:22–07:20)" in reply.text and "[S1]" not in reply.text
    assert reply.usage == {"prompt_tokens": 300, "completion_tokens": 200, "total_tokens": 500, "reasoning_tokens": 120}


def test_reasoning_content_is_ignored_never_shown_stored_or_logged(evidence, caplog):
    caplog.set_level("DEBUG")
    p, _ = provider()
    reply = p.generate(ctx(evidence))
    assert REASONING_SECRET not in reply.text and REASONING_SECRET not in str(reply) and REASONING_SECRET not in caplog.text


@pytest.mark.parametrize("make", [
    lambda inner: json.dumps(inner),
    lambda inner: "```json\n" + json.dumps(inner) + "\n```",
    lambda inner: "Claro, aquí está el resultado:\n" + json.dumps(inner) + "\nEspero que sirva.",
    lambda inner: "<think>pensando en voz alta {no es json}</think>" + json.dumps(inner),
    lambda inner: [{"type": "text", "text": json.dumps(inner)}],
])
def test_the_model_output_is_parsed_tolerantly(evidence, make):
    inner = good_answer("S1")
    content = make(inner)
    body = nv_completion(content=content if isinstance(content, str) else None)
    if not isinstance(content, str):
        body["choices"][0]["message"]["content"] = content
    p, _ = provider(FakeKimi(lambda r, pl: json_response(body)))
    assert p.generate(ctx(evidence)).cited_indices == (0,)


@pytest.mark.parametrize("body, needle", [
    (nv_completion(content=""), "vacío"),
    (nv_completion(content="   "), "vacío"),
    (nv_completion(content=None) | {"choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "stop"}]}, "vacío"),
    (nv_completion(content="", finish_reason="length"), "NVIDIA_MAX_OUTPUT_TOKENS"),
    (nv_completion(good_answer("S1"), finish_reason="length"), "truncada"),
    (nv_completion(content="texto libre sin JSON"), "JSON"),
    (nv_completion(content='{"answer": "incompleto'), "JSON"),
    (nv_completion(content="[1, 2, 3]"), "JSON"),
    ({"choices": []}, "choices"),
    ({"id": "x"}, "choices"),
    ({"choices": ["no es objeto"]}, "choices"),
])
def test_empty_truncated_or_malformed_outputs_are_rejected(evidence, body, needle):
    p, fake = provider(FakeKimi(lambda r, pl: json_response(body)))
    with pytest.raises(LLMMalformedResponse) as info:
        p.generate(ctx(evidence))
    assert needle in str(info.value) or needle in info.value.detail
    assert fake.calls == 1                                                  # sin reintentos


@pytest.mark.parametrize("answer", [
    {"answer": "Cita inventada [S9]", "used_segments": ["S9"]},                          # etiqueta inexistente
    {"answer": "Empieza en 12:34 de la clase [S1]", "used_segments": ["S1"]},             # minuto a mano
    {"answer": "Tienes certificado asegurado [S1]", "used_segments": ["S1"]},
    {"answer": "Mira https://phishing.example [S1]", "used_segments": ["S1"]},
])
def test_the_verifier_still_blocks_invented_citations_and_claims(evidence, answer):
    p, _ = provider(FakeKimi(lambda r, pl: json_response(nv_completion(answer))))
    with pytest.raises(LLMMalformedResponse):
        p.generate(ctx(evidence))


def test_non_json_http_bodies_are_malformed(evidence):
    for resp in (httpx.Response(200, text="<html>gateway</html>"), json_response([1, 2])):
        p, _ = provider(FakeKimi(lambda r, pl, resp=resp: resp))
        with pytest.raises(LLMMalformedResponse):
            p.generate(ctx(evidence))


def test_lenient_parser_still_requires_complete_valid_json():
    assert parse_model_json('ruido {"a": 1} ruido', lenient=True) == {"a": 1}
    for bad in ('ruido {"a": 1', "sin llaves", "{a: 1}"):
        with pytest.raises(LLMMalformedResponse):
            parse_model_json(bad, lenient=True)
    with pytest.raises(LLMMalformedResponse):
        parse_model_json('ruido {"a": 1} ruido')                                            # el modo estricto sigue siendo estricto


# ======================================================================== errores
@pytest.mark.parametrize("status, error, needle", [
    (401, LLMAuthError, "NVIDIA_API_KEY"),
    (403, LLMAuthError, "permiso"),
    (404, LLMRequestRejected, "moonshotai/kimi-k3"),
    (400, LLMRequestRejected, "NVIDIA_MAX_TOKENS_PARAM"),
    (422, LLMRequestRejected, "NVIDIA_MODEL"),
    (402, LLMRateLimited, "créditos"),
    (408, LLMTimeout, "tiempo"),
    (429, LLMRateLimited, "límite de uso"),
    (500, LLMUnavailable, "HTTP 500"),
    (503, LLMUnavailable, "HTTP 503"),
])
def test_http_errors_map_to_precise_typed_errors_that_never_leak_the_key_or_the_body(evidence, status, error, needle):
    body_with_secret = nv_error(status, title="Titulo", detail=f"Authorization: Bearer {FAKE_NVIDIA_KEY} cuenta=ACME-123")
    p, fake = provider(FakeKimi(lambda r, pl: body_with_secret))
    with pytest.raises(error) as info:
        p.generate(ctx(evidence))
    e = info.value
    assert needle in e.public_message and e.upstream_status == status and fake.calls == 1
    assert e.fallback_allowed is True                                                         # en NVIDIA se degrada a DEMO CON aviso
    blob = " ".join([str(e), e.detail, e.public_message, repr(e)])
    assert FAKE_NVIDIA_KEY not in blob and "ACME-123" not in blob and "cuenta=" not in blob and "Bearer" not in blob


@pytest.mark.parametrize("exc, error", [
    (httpx.ReadTimeout("lento"), LLMTimeout), (httpx.ConnectTimeout("lento"), LLMTimeout),
    (httpx.ConnectError(f"fallo con {FAKE_NVIDIA_KEY}"), LLMUnavailable), (httpx.RemoteProtocolError("corte"), LLMUnavailable),
])
def test_network_failures_are_controlled_without_retries_or_secrets(evidence, exc, error):
    def boom(request, payload):
        raise exc
    p, fake = provider(FakeKimi(boom))
    with pytest.raises(error) as info:
        p.generate(ctx(evidence))
    assert fake.calls == 1 and FAKE_NVIDIA_KEY not in " ".join([str(info.value), info.value.detail, info.value.public_message])


# ================================================================ freno de emergencia
@pytest.mark.parametrize("status, seconds", [(401, 300), (403, 300), (404, 300), (402, 300), (429, 60)])
def test_after_an_access_or_quota_error_no_more_requests_are_made_until_the_pause_ends(evidence, status, seconds):
    clock = Clock()
    p, fake = provider(FakeKimi(lambda r, pl: nv_error(status)), clock=clock)
    with pytest.raises(Exception):
        p.generate(ctx(evidence))
    assert fake.calls == 1 and p.provider_status()["state"] == "paused" and p.provider_status()["retry_after_seconds"] == seconds
    for _ in range(5):                                                              # ráfaga de intentos: ninguno llega a la red
        with pytest.raises(Exception) as info:
            p.generate(ctx(evidence))
        assert "en pausa" in info.value.public_message and info.value.upstream_status == status
    assert fake.calls == 1
    assert p._budget.used == 1                                                       # tampoco consumen el tope de llamadas
    clock.t += seconds + 1                                                           # terminó el enfriamiento: se permite UN intento
    with pytest.raises(Exception):
        p.generate(ctx(evidence))
    assert fake.calls == 2


def test_retry_after_from_the_server_is_honored_and_capped(evidence):
    clock = Clock()
    p, _ = provider(FakeKimi(lambda r, pl: nv_error(429, headers={"Retry-After": "120"})), clock=clock)
    with pytest.raises(LLMRateLimited):
        p.generate(ctx(evidence))
    assert p.provider_status()["retry_after_seconds"] == 120
    p2, _ = provider(FakeKimi(lambda r, pl: nv_error(429, headers={"Retry-After": "999999"})), clock=Clock())
    with pytest.raises(LLMRateLimited):
        p2.generate(ctx(evidence))
    assert p2.provider_status()["retry_after_seconds"] == 3600


def test_server_errors_and_malformed_answers_do_not_trigger_the_pause_but_are_never_retried(evidence):
    p, fake = provider(FakeKimi(lambda r, pl: nv_error(503)))
    for expected in (1, 2):
        with pytest.raises(LLMUnavailable):
            p.generate(ctx(evidence))
        assert fake.calls == expected and p.provider_status()["state"] == "ok"


def test_a_successful_call_after_the_pause_clears_nothing_silently_but_works(evidence):
    clock = Clock()
    seq = iter([nv_error(429), json_response(nv_completion(good_answer("S1")))])
    p, fake = provider(FakeKimi(lambda r, pl: next(seq)), clock=clock)
    with pytest.raises(LLMRateLimited):
        p.generate(ctx(evidence))
    clock.t += 61
    assert p.generate(ctx(evidence)).mode == "LIVE" and fake.calls == 2 and p.provider_status()["state"] == "ok"


def test_the_live_call_cap_still_applies(evidence):
    p, fake = provider(config=replace(NVIDIA_CONFIG, max_live_calls=2))
    p.generate(ctx(evidence)); p.generate(ctx(evidence))
    with pytest.raises(Exception) as info:
        p.generate(ctx(evidence))
    assert info.value.code == "llm_budget_exceeded" and fake.calls == 2


def test_standard_non_grounded_replies_never_touch_the_network(evidence, no_network):
    p, fake = provider()
    for kind in (ResponseKind.NO_EVIDENCE, ResponseKind.GREETING, ResponseKind.OUT_OF_SCOPE, ResponseKind.NEEDS_CONTEXT):
        reply = p.generate(ctx([], kind=kind))
        assert (reply.provider, reply.mode) == ("demo", "DEMO")                      # no se afirma NVIDIA si respondió una plantilla
    assert fake.calls == 0


# ======================================================== chat completo (API) y fallback
def test_a_live_nvidia_chat_answer_reports_nvidia_and_keeps_the_real_timestamps(db_path, retriever):
    fake = FakeKimi(DEFAULT_OK)
    with TestClient(nvidia_app(db_path, retriever, fake)) as c:
        body = post_chat(c, "Explícame ORDER BY").json()
    assert (body["mode"], body["provider"], body["model"], body["fallback_used"]) == ("LIVE", "nvidia-kimi", "moonshotai/kimi-k3", False)
    ref = body["references"][0]
    assert (ref["start_seconds"], ref["end_seconds"], ref["timestamp"]) == (382.0, 440.0, "06:22–07:20")
    assert "(min 06:22–07:20)" in body["response"] and body["grounded"] is True
    stored = rows(db_path, "SELECT provider FROM messages WHERE role = 'tutor'")
    assert stored == [{"provider": "nvidia-kimi:moonshotai/kimi-k3"}]


@pytest.mark.parametrize("status, code_in_notice", [
    (401, "llm_auth_error"), (403, "llm_auth_error"), (404, "llm_request_rejected"), (429, "llm_rate_limited"), (503, "llm_unavailable"),
])
def test_access_and_quota_errors_fall_back_to_demo_with_the_precise_cause_visible(db_path, retriever, status, code_in_notice):
    fake = FakeKimi(lambda r, p: nv_error(status))
    with TestClient(nvidia_app(db_path, retriever, fake)) as c:
        body = post_chat(c, "Explícame ORDER BY").json()
        health = c.get("/health").json()
    assert (body["mode"], body["provider"], body["fallback_used"], body["grounded"]) == ("DEMO", "demo", True, True)
    assert code_in_notice in body["provider_notice"] and f"HTTP {status}" in body["provider_notice"] or status in (503,)
    assert [r["timestamp"] for r in body["references"]] == ["06:22–07:20"]              # el DEMO conserva las citas verificables
    assert "nvidia" not in body["provider"]                                              # jamás se atribuye a NVIDIA lo que escribió el DEMO
    stored = rows(db_path, "SELECT provider, fallback_reason FROM messages WHERE role = 'tutor'")[0]
    assert stored["provider"] == "demo" and code_in_notice in stored["fallback_reason"]
    assert FAKE_NVIDIA_KEY not in json.dumps([body, health])


def test_after_an_access_error_the_next_questions_do_not_call_nvidia_again_and_health_shows_why(db_path, retriever):
    fake = FakeKimi(lambda r, p: nv_error(403))
    with TestClient(nvidia_app(db_path, retriever, fake)) as c:
        for q in ("Explícame ORDER BY", "Explícame WHERE", "¿Qué hace SELECT?"):
            assert post_chat(c, q).json()["mode"] == "DEMO"
        health = c.get("/health").json()
    assert fake.calls == 1                                                              # una sola solicitud fallida, sin martilleo
    llm = health["llm"]
    assert (llm["active_provider"], llm["configured_provider"], llm["mode"], llm["model"]) == ("nvidia-kimi", "nvidia", "LIVE", "moonshotai/kimi-k3")
    assert llm["provider_status"]["state"] == "paused" and llm["provider_status"]["code"] == "llm_auth_error"
    assert llm["provider_status"]["upstream_status"] == 403 and "permiso" in llm["provider_status"]["message"]
    assert llm["api_key_configured"] is True and llm["class_content_authorized"] is False


def test_health_for_a_healthy_nvidia_provider_and_without_secrets(db_path, retriever):
    with TestClient(nvidia_app(db_path, retriever, FakeKimi(DEFAULT_OK))) as c:
        resp = c.get("/health")
    llm = resp.json()["llm"]
    assert llm["provider_status"]["state"] == "ok" and llm["selection_reason"] == "LIVE autorizado (NVIDIA Build)"
    assert FAKE_NVIDIA_KEY not in resp.text and "Bearer" not in resp.text


# ============================================================ no hay llamadas no autorizadas
def test_nothing_is_called_at_startup_or_when_reading_pages_or_summaries(db_path, fake_assets):
    fake = FakeKimi(DEFAULT_OK)
    app = create_app(db_path=db_path, retriever=KnowledgeRetriever(), real_lesson=True, demo_assets_dir=fake_assets,
                     llm_config=NVIDIA_CONFIG, llm_client=fake.client())
    with TestClient(app) as c:
        c.get("/health"); c.get("/api/demo/lesson")
        for engine in ("extractive", "best", "llm"):
            assert c.get(f"/api/courses/{N8N_COURSE}/lessons/{N8N_LESSON}/summary?engine={engine}").status_code == 200
        c.get(f"/api/courses/{N8N_COURSE}/lessons/{N8N_LESSON}/chapters")
    assert fake.calls == 0                                                              # el resumen generativo NO se activa solo


def block_sockets(monkeypatch):
    """Bloquea nuevas conexiones DESPUÉS de arrancar el cliente de prueba (el bucle de eventos de Windows necesita su socket local)."""
    import socket

    def blocked(*args, **kwargs):
        raise RuntimeError("acceso a red bloqueado en las pruebas")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def test_demo_mode_with_a_key_present_but_not_authorized_makes_no_network_calls(db_path, retriever, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", FAKE_NVIDIA_KEY)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "nvidia")
    monkeypatch.setattr(settings, "LLM_LIVE_AUTHORIZED", False)
    with TestClient(create_app(db_path=db_path, retriever=retriever)) as c:
        block_sockets(monkeypatch)
        assert c.get("/health").json()["llm"]["active_provider"] == "demo"
        body = post_chat(c, "Explícame ORDER BY").json()
    assert body["mode"] == "DEMO" and body["grounded"] and body["fallback_used"] is False


def test_the_real_environment_never_goes_live_by_accident(db_path, retriever, monkeypatch):
    """Con el .env real del equipo (clave presente, LLM_PROVIDER por defecto) todo sigue en DEMO."""
    with TestClient(create_app(db_path=db_path, retriever=retriever)) as c:
        block_sockets(monkeypatch)
        health = c.get("/health").json()
        assert post_chat(c, "Explícame ORDER BY").json()["mode"] == "DEMO"
    assert health["llm"]["active_provider"] == "demo" or settings.LLM_LIVE_AUTHORIZED   # solo LIVE si el dueño lo autoriza explícitamente


# =============================================================== privacidad del contenido
def n8n_app(db_path, fake_assets, fake, config=NVIDIA_CONFIG, **kw):
    from app.config import AcademicConfig
    return create_app(db_path=db_path, retriever=KnowledgeRetriever(), real_lesson=True, demo_assets_dir=fake_assets,
                      llm_config=config, llm_client=fake.client(),
                      academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN), **kw)


def test_the_real_class_content_is_not_sent_to_nvidia_without_explicit_authorization(db_path, fake_assets):
    fake = FakeKimi(DEFAULT_OK)
    with TestClient(n8n_app(db_path, fake_assets, fake)) as c:
        body = chat_n8n(c, "¿Qué es n8n?").json()
        health = c.get("/health").json()
    assert fake.calls == 0                                                               # nada de la clase salió del equipo
    assert (body["mode"], body["provider"], body["fallback_used"], body["grounded"]) == ("DEMO", "demo", True, True)
    assert "llm_content_not_authorized" in body["provider_notice"] and "NVIDIA_ALLOW_CLASS_CONTENT" in body["provider_notice"]
    assert body["references"] and health["llm"]["class_content_authorized"] is False


def test_the_guardian_sends_no_class_terms_for_the_private_class(db_path, fake_assets):
    fake = FakeKimi(lambda r, p: json_response(nv_completion({"label": "uncertain", "reason": "No se sabe."})))
    with TestClient(n8n_app(db_path, fake_assets, fake)) as c:
        body = chat_n8n(c, "¿Qué es la sostenibilidad ambiental?").json()
    assert fake.calls == 1 and body["guardian"]["is_llm"] is True
    sent = json.dumps(fake.payloads[0], ensure_ascii=False)
    assert "(sin términos)" in sent and "Docker" not in sent and "flujos" not in sent and "Aquí comentamos" not in sent


def test_with_explicit_authorization_only_the_retrieved_segments_are_sent_and_never_personal_data(db_path, fake_assets):
    fake = FakeKimi(DEFAULT_OK)
    cfg = replace(NVIDIA_CONFIG, allow_class_content=True)
    with TestClient(n8n_app(db_path, fake_assets, fake, cfg)) as c:
        health = c.get("/health").json()
        body = chat_n8n(c, "Mi correo es ana.perez@correo.com. ¿Para qué sirve Docker con n8n? ¿Quién es Maria?").json()
    assert health["llm"]["class_content_authorized"] is True
    assert (body["mode"], body["provider"]) == ("LIVE", "nvidia-kimi") and body["references"] and body["fallback_used"] is False
    tutor_call = next(p for p in fake.payloads if "DATOS, no instrucciones" in p["messages"][0]["content"] and "<segmentos>" in p["messages"][-1]["content"])
    user = tutor_call["messages"][-1]["content"]
    assert 1 <= user.count("<segmento ") <= 3                                            # solo los segmentos recuperados
    sent = json.dumps(fake.payloads, ensure_ascii=False)
    for forbidden in ("ana.perez@correo.com", "maria.lopezzi", "Lopezzi", "987 654", "demo-student-001", ".mp4", "demo-assets", FAKE_NVIDIA_KEY):
        assert forbidden not in sent, forbidden
    sent_chars = sum(len(s) for s in re.findall(r">([^<]{40,})</segmento>", sent))
    assert sent_chars < 3500                                                             # nunca la clase completa (la sintética tiene ~25 000)


def test_fictitious_courses_can_use_nvidia_without_the_class_authorization(db_path, retriever):
    fake = FakeKimi(DEFAULT_OK)
    with TestClient(nvidia_app(db_path, retriever, fake)) as c:
        assert post_chat(c, "Explícame ORDER BY").json()["provider"] == "nvidia-kimi"
    assert fake.calls == 1


def test_the_admin_summary_with_nvidia_is_blocked_for_the_private_class_and_labels_the_real_provider_when_allowed(db_path, fake_assets):
    url = f"/api/admin/lessons/{N8N_COURSE}/{N8N_LESSON}/summary?engine=kimi"
    fake = FakeKimi(DEFAULT_OK)
    with TestClient(n8n_app(db_path, fake_assets, fake)) as c:
        blocked = c.post(url, headers={"X-Admin-Token": ADMIN_TOKEN})
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "llm_content_not_authorized" and fake.calls == 0

    allowed_fake = FakeKimi(DEFAULT_OK)
    with TestClient(n8n_app(db_path, fake_assets, allowed_fake, replace(NVIDIA_CONFIG, allow_class_content=True))) as c:
        ok = c.post(url, headers={"X-Admin-Token": ADMIN_TOKEN})
        shown = c.get(f"/api/courses/{N8N_COURSE}/lessons/{N8N_LESSON}/summary?engine=best").json()
    assert ok.status_code == 200 and ok.json()["generator"]["generator"] == "nvidia-kimi:moonshotai/kimi-k3"
    assert shown["generated_by"] == "nvidia-kimi:moonshotai/kimi-k3" and shown["mode"] == "LIVE"
    assert all(len(json.dumps(p["messages"])) < 16000 for p in allowed_fake.payloads)   # por fragmentos, nunca la clase entera


# ===================================================================== secretos en el repositorio
ROOT = Path(__file__).resolve().parents[2]
SECRET_PATTERNS = (re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}"), re.compile(r"\bsk-[A-Za-z0-9\-]{20,}"), re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"))
ALLOWED_FAKES = {FAKE_NVIDIA_KEY, "sk-FAKE-TEST-KEY-do-not-use-0000", "sk-FAKE-local-check-0000"}


def candidate_files():
    skip = {".venv", "__pycache__", ".pytest_cache", "data", "demo-assets", ".git", "node_modules"}
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & skip) and p.name != ".env" and p.suffix in {
                ".py", ".md", ".txt", ".ini", ".example", ".json", ".js", ".html", ".css", ".yml", ".yaml", ".toml", ".gitignore"} | {""}:
            yield p


def test_no_real_looking_secret_exists_in_any_project_file_except_the_clearly_fake_test_keys():
    offenders = []
    for path in candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in SECRET_PATTERNS:
            offenders += [(str(path.relative_to(ROOT)), m.group(0)[:12]) for m in pattern.finditer(text) if m.group(0) not in ALLOWED_FAKES]
    assert offenders == []


def test_the_private_env_file_and_data_are_ignored_by_git():
    import shutil
    import subprocess
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("no es un repositorio git")
    for target in ("ai-service/.env", ".env", "ai-service/data/rescuepath.db", "demo-assets/clase-demo.mp4"):
        assert subprocess.run(["git", "check-ignore", "-q", target], cwd=ROOT).returncode == 0, target
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    assert not [f for f in tracked if f.endswith(".env") or f.endswith(".db") or f.startswith("demo-assets/")]


def test_env_example_documents_nvidia_without_any_credential():
    text = (ROOT / "ai-service" / ".env.example").read_text(encoding="utf-8")
    for name in ("NVIDIA_API_KEY", "NVIDIA_MODEL=moonshotai/kimi-k3", "NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1",
                 "NVIDIA_ALLOW_CLASS_CONTENT=false", "LLM_LIVE_AUTHORIZED=false", "LLM_PROVIDER=demo"):
        assert name in text, name
    assert re.search(r"^NVIDIA_API_KEY=\s*$", text, re.M)                                # la clave va VACÍA


def test_the_frontend_never_contains_the_key_or_calls_nvidia_directly():
    for name in ("demo-n8n.js", "demo-n8n.html"):
        text = (ROOT / "frontend" / name).read_text(encoding="utf-8")
        assert "nvidia" not in text.lower() and "NVIDIA_API_KEY" not in text and "nvapi" not in text
