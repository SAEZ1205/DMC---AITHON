"""Cupo del proveedor (Groq): ventana por minuto, encabezados, 429, caché y respaldo con la simulación."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import llm
from helpers import START


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class RateLimited(Exception):
    status_code = 429

    def __init__(self, retry_after="3"):
        super().__init__("429 Too Many Requests")
        self.response = SimpleNamespace(headers={"retry-after": retry_after})


def raw_response(content: dict, tokens: int = 500, headers: dict | None = None):
    resp = SimpleNamespace(usage=SimpleNamespace(total_tokens=tokens), choices=[
        SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(content)))])
    return SimpleNamespace(headers=headers or {}, parse=lambda: resp)


@pytest.fixture()
def real_llm(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(config, "LLM_MOCK", False)
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "low")
    monkeypatch.setattr(config, "LLM_SAFETY", 1.0)
    monkeypatch.setattr(config, "LLM_TPM", 1000)
    monkeypatch.setattr(config, "LLM_RPM", 30)
    monkeypatch.setattr(llm, "BUDGET", llm.Budget(clock=clock, sleep=clock.sleep))
    llm.CACHE.clear()
    llm.FALLBACKS.clear()
    yield clock
    llm.CACHE.clear()


def test_parse_duration():
    assert llm.parse_duration("2") == 2
    assert llm.parse_duration("7.66s") == pytest.approx(7.66)
    assert llm.parse_duration("2m59.56s") == pytest.approx(179.56)
    assert llm.parse_duration("450ms") == pytest.approx(0.45)
    assert llm.parse_duration(None) == 0


def test_tokens_refill_continuously(real_llm):
    budget = llm.BUDGET
    budget.acquire(600, max_wait=0)
    with pytest.raises(llm.LLMError):
        budget.acquire(600, max_wait=0)
    budget.acquire(600, max_wait=20)
    assert real_llm.slept == [pytest.approx(12)]  # faltaban 200 tokens: 1000 por minuto → 12 s
    with pytest.raises(llm.LLMError, match="diario|cupo"):
        budget.acquire(5000, max_wait=60)


def test_requests_per_minute(real_llm, monkeypatch):
    monkeypatch.setattr(config, "LLM_RPM", 2)
    budget = llm.BUDGET
    budget.acquire(10, max_wait=0)
    budget.acquire(10, max_wait=0)
    with pytest.raises(llm.LLMError):
        budget.acquire(10, max_wait=0)


def test_provider_headers_and_429_pause_calls(real_llm):
    budget = llm.BUDGET
    budget.observe({"x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "7.5s"})
    with pytest.raises(llm.LLMError):
        budget.acquire(300, max_wait=2)
    budget.acquire(300, max_wait=15)
    assert real_llm.slept[-1] == pytest.approx(12)
    budget.rate_limited(12)
    with pytest.raises(llm.LLMError):
        budget.acquire(10, max_wait=5)


def test_complete_json_caches_and_settles_tokens(real_llm, monkeypatch):
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return raw_response({"topic": "ritmo", "sentiment": "neutro", "summary": "rápido"}, tokens=180)

    monkeypatch.setattr(llm, "_create", fake_create)
    msgs = [{"role": "user", "content": "Responde solo con JSON"}]
    first = llm.complete_json(msgs, llm.MicroOut)
    second = llm.complete_json(msgs, llm.MicroOut)
    assert first == second and first.topic == "ritmo" and len(calls) == 1
    assert calls[0]["max_tokens"] == llm.MAX_OUTPUT["MicroOut"] and calls[0]["reasoning_effort"] == "low"
    assert calls[0]["extra_body"] == {"include_reasoning": False}
    status = llm.BUDGET.status()
    assert status["minute"]["tokens_available"] == 1000 - 180 and status["day"]["tokens"] == 180


def test_429_falls_back_and_pauses(real_llm, monkeypatch):
    calls = []

    def limited(**kwargs):
        calls.append(kwargs)
        raise RateLimited("20")

    monkeypatch.setattr(llm, "_create", limited)
    msgs = [{"role": "user", "content": "JSON"}]
    with pytest.raises(llm.LLMError, match="429"):
        llm.complete_json(msgs, llm.MicroOut)
    with pytest.raises(llm.LLMError, match="cupo"):
        llm.complete_json(msgs, llm.MicroOut)
    assert len(calls) == 1 and llm.FALLBACKS["MicroOut"] == 2
    assert llm.BUDGET.status()["blocked_for_s"] == pytest.approx(20)
    assert llm.BUDGET.status()["minute"]["tokens_available"] == 1000 and llm.BUDGET.status()["day"]["tokens"] == 0


def test_tutor_answers_with_simulation_when_provider_fails(db, real_llm, monkeypatch):
    from test_tutor_chat import post, seed as seed_chat

    monkeypatch.setattr(llm, "_create", lambda **kw: (_ for _ in ()).throw(RateLimited("30")))
    uid, _, sid = seed_chat(db)
    config.set_now(START + timedelta(days=1))
    with TestClient(app) as client:
        res = post(client, sid, uid, message="¿Cómo se guardan las credenciales?")
        assert res["level"] == 1 and res["answer"].startswith("Esto se explica en")
        status = client.get("/api/admin/llm", headers={"X-User-Id": str(_staff_id(db))}).json()
        assert status["mode"] == "real" and status["fallbacks"]["TutorOut"] == 1
        assert client.get("/api/admin/llm", headers={"X-User-Id": str(uid)}).status_code == 403


def test_wrong_attempt_uses_a_single_llm_call(db, real_llm, monkeypatch):
    from test_tutor_chat import post, seed as seed_chat

    monkeypatch.setattr(config, "LLM_TPM", 100000)
    monkeypatch.setattr(llm, "BUDGET", llm.Budget(clock=real_llm, sleep=real_llm.sleep))
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        evaluating = "Evalúa" in kwargs["messages"][-1]["content"]
        return raw_response({"reply": "Casi: no es un Excel. Pista: en [15:00] fíjate dónde quedan cifradas. ¿Dónde?",
                             "level": 2, "evaluation": "incorrecta" if evaluating else None}, tokens=300)

    monkeypatch.setattr(llm, "_create", fake)
    uid, _, sid = seed_chat(db)
    config.set_now(START + timedelta(days=1))
    with TestClient(app) as client:
        first = post(client, sid, uid, message="¿Cómo se guardan las credenciales?")
        res = post(client, sid, uid, thread_id=first["thread_id"], action="answer", message="En un Excel")
    assert len(calls) == 2
    assert res["evaluation"] == "incorrecta" and res["level"] == 2 and res["answer"].startswith("Casi: no es un Excel.")


def test_quiz_assist_uses_simulation_when_provider_fails(db, real_llm, monkeypatch):
    from test_quiz import seed as seed_quiz

    monkeypatch.setattr(llm, "_create", lambda **kw: (_ for _ in ()).throw(TimeoutError("timeout")))
    teacher, _, sid, _ = seed_quiz(db, approved=5, drafts=0)
    with TestClient(app) as client:
        res = client.post(f"/api/tutor/sessions/{sid}/quiz/assist", headers=teacher, json={"action": "proponer"})
        assert res.status_code == 200 and res.json()["created"]
        target = res.json()["created"][0]
        alts = client.post(f"/api/tutor/sessions/{sid}/quiz/assist", headers=teacher,
                           json={"action": "alternativas", "item_id": target})
        review = client.post(f"/api/tutor/sessions/{sid}/quiz/assist", headers=teacher,
                             json={"action": "revisar", "item_id": target})
    assert alts.status_code == 200 and len(alts.json()["distractors"]) == 3
    assert review.status_code == 200 and "issues" in review.json()["review"]


def _staff_id(db):
    from sqlmodel import select

    from app.models import User
    return db.exec(select(User).where(User.role == "tutor")).first().id
