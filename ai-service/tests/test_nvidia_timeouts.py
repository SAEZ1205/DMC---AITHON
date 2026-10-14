"""
Tiempos de espera de NVIDIA Kimi K3: timeout por llamada, tope total por pregunta, guardián, fallback visible y experiencia de carga.

Todo con transporte simulado y sin red (además, `conftest.hermetic_environment` bloquea cualquier conexión externa y neutraliza el
`.env` real del equipo). Estas pruebas verifican NUESTRO manejo de tiempos; que NVIDIA responda más rápido o más lento solo lo
demuestra una solicitud real autorizada.
"""
import json
import re
import shutil
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import LLMConfig, _env_float, settings
from app.knowledge.retriever import KnowledgeRetriever
from app.llm import NvidiaKimiProvider
from app.llm.base import ResponseKind, TutorContext
from app.llm.errors import LLMDeadlineExceeded, LLMTimeout
from app.llm.nvidia_provider import MIN_COMPLETION_TOKENS
from app.main import create_app
from app.services.guardian import HeuristicGuardian, LLMGuardian

from conftest import LESSON_1, post_chat
from llm_helpers import NVIDIA_CONFIG, FakeKimi, good_answer, json_response, nv_completion

OK = lambda request, payload: json_response(nv_completion(good_answer("S1")))      # noqa: E731
CFG = replace(NVIDIA_CONFIG, timeout_seconds=120.0, total_timeout_seconds=150.0, guardian_timeout_seconds=25.0, max_output_tokens=700)
ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def ctx(retriever, **kw):
    evidence = retriever.retrieve_segments("sql-demo", "ORDER BY", lesson_id=LESSON_1, top_k=2)
    base = dict(kind=ResponseKind.GROUNDED, student_message="Explícame ORDER BY", course_name="SQL", lesson_title="Clase 1",
                topic_label="ORDER BY", evidence=evidence)
    base.update(kw)
    return TutorContext(**base)


def read_timeout(request) -> float:
    return request.extensions["timeout"]["read"]


def provider(responder=OK, config=CFG, clock=None):
    fake = FakeKimi(responder)
    return NvidiaKimiProvider(config, client=fake.client(), clock=clock or Clock()), fake


# ===================================================================== configuración
def test_defaults_are_120_seconds_per_call_150_total_and_700_tokens():
    assert LLMConfig().total_timeout_seconds == 150.0 and LLMConfig().guardian_timeout_seconds == 25.0
    example = (ROOT / "ai-service" / ".env.example").read_text(encoding="utf-8")
    for line in ("NVIDIA_TIMEOUT_SECONDS=120", "NVIDIA_TOTAL_TIMEOUT_SECONDS=150", "NVIDIA_GUARDIAN_TIMEOUT_SECONDS=25",
                 "NVIDIA_MAX_OUTPUT_TOKENS=700"):
        assert re.search(rf"(?m)^{line}\s*$", example), line


@pytest.mark.parametrize("raw, expected", [
    (None, 120.0), ("90", 90.0), ("0", 5.0), ("-30", 5.0), ("100000", 300.0), ("inf", 300.0), ("abc", 120.0), ("nan", 120.0), ("", 120.0),
])
def test_timeout_settings_are_always_bounded_so_no_value_can_mean_an_endless_wait(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("X_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("X_TIMEOUT", raw)
    assert _env_float("X_TIMEOUT", 120.0, minimum=5.0, maximum=300.0) == expected


def test_nvidia_config_carries_the_new_limits():
    cfg = settings.nvidia_config()
    assert cfg.total_timeout_seconds == settings.NVIDIA_TOTAL_TIMEOUT_SECONDS and cfg.guardian_timeout_seconds == settings.NVIDIA_GUARDIAN_TIMEOUT_SECONDS
    assert 5 <= cfg.timeout_seconds <= 300 and 5 <= cfg.total_timeout_seconds <= 600 and 3 <= cfg.guardian_timeout_seconds <= 120


# ================================================================ timeout por llamada
def test_the_tutor_call_waits_up_to_the_configured_timeout_and_connects_fast(retriever):
    p, fake = provider()
    p.generate(ctx(retriever))
    timeout = fake.requests[0].extensions["timeout"]
    assert timeout["read"] == 120.0 and timeout["connect"] == 10.0                           # conectar falla rápido; esperar tarda lo configurado


def test_the_guardian_has_its_own_shorter_cap(retriever):
    guardian_ok = lambda r, p: json_response(nv_completion({"label": "relevant", "reason": "ok"}))      # noqa: E731
    p, fake = provider(guardian_ok)
    assert p.classify_relevance("¿Qué es ORDER BY?", "Clase 1", ["order"]) [0] == "relevant"
    assert read_timeout(fake.requests[0]) == 25.0


def test_a_timeout_is_reported_with_the_real_seconds_and_nothing_is_retried(retriever):
    def hang(request, payload):
        raise httpx.ReadTimeout("sin respuesta")
    p, fake = provider(hang)
    with pytest.raises(LLMTimeout) as excinfo:
        p.generate(ctx(retriever))
    assert fake.calls == 1                                                                   # cero reintentos
    assert excinfo.value.fallback_allowed is True and excinfo.value.public_message == "NVIDIA no respondió en 120 s."


def test_a_guardian_timeout_reports_the_guardian_cap_not_the_tutor_one():
    def hang(request, payload):
        raise httpx.ReadTimeout("sin respuesta")
    p, fake = provider(hang)
    with pytest.raises(LLMTimeout) as excinfo:
        p.classify_relevance("¿Qué es ORDER BY?", "Clase 1", [])
    assert excinfo.value.public_message == "NVIDIA no respondió en 25 s." and fake.calls == 1


# ============================================================= tope TOTAL por pregunta
def test_guardian_and_tutor_share_one_total_clock_and_the_second_call_gets_only_what_is_left(retriever):
    clock = Clock()

    def slow_then_ok(request, payload):
        clock.t += 100.0                                   # cada llamada "consume" 100 s de reloj
        if "<pregunta>" in payload["messages"][-1]["content"]:
            return json_response(nv_completion({"label": "uncertain", "reason": "dudoso"}))
        return json_response(nv_completion(good_answer("S1")))
    p, fake = provider(slow_then_ok, clock=clock)
    with p.request_scope():
        p.classify_relevance("¿Qué es ORDER BY?", "Clase 1", [])
        p.generate(ctx(retriever))
    assert [read_timeout(r) for r in fake.requests] == [25.0, 50.0]                          # 150 - 100 = 50 s restantes para el tutor


def test_when_the_total_time_is_spent_no_new_call_is_started(retriever):
    clock = Clock()

    def burn(request, payload):
        clock.t += 149.0
        return json_response(nv_completion(good_answer("S1")))
    p, fake = provider(burn, clock=clock)
    with p.request_scope():
        p.generate(ctx(retriever))
        with pytest.raises(LLMDeadlineExceeded) as excinfo:
            p.generate(ctx(retriever))
    assert fake.calls == 1                                                                   # la segunda ni siquiera salió
    assert excinfo.value.code == "llm_timeout" and "150 s" in excinfo.value.public_message and excinfo.value.fallback_allowed is True


def test_without_an_open_scope_each_call_keeps_its_full_timeout(retriever):
    clock = Clock()
    p, fake = provider(OK, clock=clock)
    p.generate(ctx(retriever)); clock.t += 10_000; p.generate(ctx(retriever))
    assert [read_timeout(r) for r in fake.requests] == [120.0, 120.0]


def test_a_total_of_zero_disables_only_the_total_clock_not_the_per_call_timeout(retriever):
    p, fake = provider(config=replace(CFG, total_timeout_seconds=0.0))
    with p.request_scope():
        p.generate(ctx(retriever))
    assert read_timeout(fake.requests[0]) == 120.0


def test_nested_scopes_do_not_reset_the_outer_clock(retriever):
    clock = Clock()

    def burn(request, payload):
        clock.t += 100.0
        return json_response(nv_completion(good_answer("S1")))
    p, fake = provider(burn, clock=clock)
    with p.request_scope():
        p.generate(ctx(retriever))
        with p.request_scope():                          # un ámbito interno no da 150 s nuevos
            p.generate(ctx(retriever))
    assert [read_timeout(r) for r in fake.requests] == [120.0, 50.0]


def test_two_concurrent_questions_do_not_share_a_clock(retriever):
    clock = Clock()
    p, fake = provider(OK, clock=clock)
    started, release, seen = threading.Event(), threading.Event(), {}

    def question_a():
        with p.request_scope():
            started.set()
            release.wait(5)                              # A mantiene su ámbito abierto mientras B trabaja
    t = threading.Thread(target=question_a); t.start(); started.wait(5)
    clock.t += 140.0                                     # al reloj de A solo le quedan 10 s...
    p.generate(ctx(retriever))                           # ...pero B (sin ámbito propio) conserva su timeout completo
    seen["b"] = read_timeout(fake.requests[0])
    release.set(); t.join(5)
    assert seen["b"] == 120.0


# ================================================== de extremo a extremo: fallback visible
def sql_app(db_path, retriever, fake, config=CFG):
    return create_app(db_path=db_path, retriever=retriever, llm_config=config, llm_client=fake.client())


def test_on_timeout_the_chat_falls_back_to_demo_keeps_the_citations_and_says_kimi_did_not_answer(db_path, retriever):
    def hang(request, payload):
        raise httpx.ReadTimeout("sin respuesta")
    fake = FakeKimi(hang)
    with TestClient(sql_app(db_path, retriever, fake)) as c:
        body = post_chat(c, "Explícame ORDER BY").json()
    assert fake.calls == 1                                                                   # una sola llamada, sin reintentos
    assert (body["mode"], body["provider"], body["fallback_used"]) == ("DEMO", "demo", True)
    assert body["references"] and body["grounded"] is True                                   # la evidencia y las citas se conservan
    notice = body["provider_notice"]
    assert "llm_timeout" in notice and "NVIDIA no respondió en 120 s" in notice and "Kimi no participó" in notice


def test_a_normal_question_makes_exactly_one_model_call_and_two_turns_make_two(db_path, retriever):
    fake = FakeKimi(OK)
    with TestClient(sql_app(db_path, retriever, fake)) as c:
        first = post_chat(c, "Explícame ORDER BY").json()
        assert fake.calls == 1                                                               # sin guardián LLM ni llamadas extra
        post_chat(c, "Dame otro ejemplo", session_id=first["session_id"])
    assert fake.calls == 2 and first["provider"] == "nvidia-kimi" and first["fallback_used"] is False


def test_a_guardian_that_times_out_does_not_block_the_tutor_and_costs_one_call():
    def hang(request, payload):
        raise httpx.ReadTimeout("sin respuesta")
    p, fake = provider(hang)
    kb = KnowledgeRetriever()
    verdict = LLMGuardian(p, kb, HeuristicGuardian(kb)).assess("¿Qué es la sostenibilidad ambiental?", "sql-demo", LESSON_1)   # veredicto heurístico: incierto
    assert fake.calls == 1 and read_timeout(fake.requests[0]) == 25.0                        # el guardián SÍ se consultó, con su tope corto
    assert verdict.method == "heuristic" and verdict.label.value == "uncertain"              # y al fallar se conserva el veredicto heurístico


# ================================================================ parámetros de la petición
def test_chat_requests_use_a_short_budget_and_cheap_reasoning(db_path, retriever):
    fake = FakeKimi(OK)
    with TestClient(sql_app(db_path, retriever, fake)) as c:
        post_chat(c, "Explícame ORDER BY")
    payload = fake.payloads[0]
    assert payload["max_completion_tokens"] == 700 and payload["reasoning_effort"] == "low" and payload["stream"] is False
    assert "response_format" not in payload and "temperature" not in payload and "thinking" not in payload
    assert 1 <= payload["messages"][-1]["content"].count("<segmento ") <= 3                  # la evidencia (y por tanto las citas) se conserva


def test_a_tiny_token_setting_is_raised_to_the_reasoning_floor(retriever):
    p, fake = provider(config=replace(CFG, max_output_tokens=100))
    p.generate(ctx(retriever))
    assert fake.payloads[0]["max_completion_tokens"] == MIN_COMPLETION_TOKENS


def test_health_shows_the_active_limits_without_secrets(db_path, retriever):
    with TestClient(sql_app(db_path, retriever, FakeKimi(OK))) as c:
        limits = c.get("/health").json()["llm"]["limits"]
        dumped = c.get("/health").text
    assert limits == {"timeout_seconds": 120.0, "total_timeout_seconds": 150.0, "guardian_timeout_seconds": 25.0,
                      "max_output_tokens": 700, "reasoning_effort": "low"}
    assert NVIDIA_CONFIG.api_key not in dumped


def test_the_privacy_barrier_is_still_attached_and_acts_inside_a_total_time_scope(retriever):
    from app.privacy import PrivacyFilter
    p, fake = provider()
    p.attach_privacy(PrivacyFilter(extra_names=["Zulemita"]))
    context = ctx(retriever, student_message="Explícame ORDER BY, dice Zulemita")
    with p.request_scope():
        p.generate(context)
    assert "Zulemita" not in json.dumps(fake.payloads, ensure_ascii=False) and p.egress_guard_status()["active"] is True


# =============================================================== la suite no puede salir a la red
def test_the_test_environment_cannot_reach_nvidia_even_with_the_real_dotenv_values():
    assert settings.LLM_PROVIDER == "demo" and settings.NVIDIA_API_KEY == "" and settings.LLM_LIVE_AUTHORIZED is False
    assert settings.NVIDIA_ALLOW_CLASS_CONTENT is False
    with pytest.raises(Exception):
        httpx.Client(timeout=2).post("https://integrate.api.nvidia.com/v1/chat/completions", json={})


# ================================================= experiencia de carga (frontend, comprobaciones estáticas)
JS = (ROOT / "frontend" / "demo-n8n.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "demo-n8n.css").read_text(encoding="utf-8")


def test_frontend_shows_a_kimi_loading_state_with_an_elapsed_counter():
    assert "Kimi está preparando tu respuesta…" in JS and "demo-spinner" in JS and "setInterval" in JS
    assert "role\", \"status\"" in JS.replace("'", '"') or 'setAttribute("role", "status")' in JS      # se anuncia a los lectores de pantalla
    assert "lesson.mode === \"LIVE\"" in JS                                                   # solo promete a Kimi cuando el modo es LIVE
    assert ".demo-spinner" in CSS and "prefers-reduced-motion" in CSS


def test_frontend_blocks_duplicate_sends_from_every_entry_point():
    assert "if (!text || busy) return false;" in JS and "setBusy(true);" in JS
    assert re.search(r'submit".*?\n\s*event\.preventDefault\(\);\n\s*if \(busy\) return;', JS, re.S)       # Enter / formulario
    assert '"#suggestions button"' in JS and "b.disabled = on" in JS and "sendBtn\").disabled = on" in JS   # sugerencias y botón
    assert ".demo-suggestions button:disabled" in CSS


def test_frontend_never_waits_forever_and_always_releases_the_lock():
    assert "AbortController" in JS and "CHAT_TIMEOUT_MS = 190000" in JS and "controller.abort()" in JS
    finally_block = JS[JS.index("} finally {", JS.index("async function ask")):]
    assert "setBusy(false)" in finally_block.split("return true;")[0] and "clearTimeout(abortTimer)" in finally_block


def test_frontend_labels_demo_fallbacks_as_not_generated_by_kimi():
    assert "NO la generó Kimi" in JS and "reply.fallback_used" in JS and "demo-note warn" in JS
    assert ".demo-msg.tutor.fallback" in CSS and ".demo-note.warn" in CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")
def test_frontend_script_is_syntactically_valid():
    result = subprocess.run(["node", "--check", str(ROOT / "frontend" / "demo-n8n.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:300]
