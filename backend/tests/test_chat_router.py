"""Ruteo del chat: plataforma y logística se responden con datos reales y sin LLM."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import config
from app.main import app
from app.models import ChatMessage, QuizItem
from app.services import chat_router, llm
from helpers import START
from test_api import seed


@pytest.mark.parametrize("message, route", [
    ("¿Hasta cuándo puedo recuperar la sesión?", "plataforma"),
    ("¿Cuándo vence?", "plataforma"),
    ("¿Cuántas me faltan para el certificado?", "plataforma"),
    ("¿Ya tengo el quiz aprobado?", "plataforma"),
    ("¿Cuál es mi nota?", "plataforma"),
    ("No me abre el link de Zoom", "logistica"),
    ("¿Dónde descargo el material?", "logistica"),
    ("¿Cuál es el horario del curso?", "logistica"),
    ("¿Hay grupo de WhatsApp?", "logistica"),
    ("¿Qué es un webhook?", "contenido"),
    ("¿Para qué sirve el nodo HTTP Request en n8n?", "contenido"),
])
def test_classify(message, route):
    assert chat_router.classify(message) == route


def test_platform_answer_uses_real_data_without_llm(db, monkeypatch):
    uid, sid, _ = seed(db)
    config.set_now(START + timedelta(days=2))
    called = []
    monkeypatch.setattr(llm, "complete", lambda *a, **k: called.append(a))
    headers = {"X-User-Id": str(uid)}
    with TestClient(app) as client:
        res = client.post(f"/api/sessions/{sid}/chat", headers=headers, json={"message": "¿Cuándo vence la sesión?"}).json()
        assert res["route"] == "plataforma" and res["citations"] == []
        assert "La sesión 1 vence el martes 13 de octubre" in res["answer"]
        assert "Te faltan ~110 min de tramos clave (~74 min a 1.5×); llevas 0 % visto, y el quiz." in res["answer"]
        assert "0 de 1 sesión dictada" in res["answer"]
        assert res["link"]["href"] == "progress.html"

        client.post("/api/watch", headers=headers, json={"session_id": sid, "ranges": [[600, 3600], [7200, 10800]]})
        res = client.post(f"/api/sessions/{sid}/chat", headers=headers, json={"message": "¿Qué me falta?"}).json()
        assert "Ya viste los tramos clave; te falta aprobar el quiz." in res["answer"]

        history = client.get(f"/api/sessions/{sid}/chat", headers=headers).json()
        assert history[-1]["route"] == "plataforma" and history[-1]["link"]["href"] == "progress.html"
    assert not called
    kinds = db.exec(select(ChatMessage.kind).where(ChatMessage.role == "user")).all()
    assert kinds == ["plataforma", "plataforma"]


def test_platform_answer_warns_quiz_in_preparation(db):
    uid, sid, _ = seed(db)
    for item in db.exec(select(QuizItem)).all()[3:]:
        item.status = "borrador"
        db.add(item)
    db.commit()
    config.set_now(START + timedelta(days=2))
    with TestClient(app) as client:
        res = client.post(f"/api/sessions/{sid}/chat", headers={"X-User-Id": str(uid)},
                          json={"message": "¿Hasta cuándo puedo recuperar?"}).json()
    assert "El quiz todavía está en preparación" in res["answer"]


def test_logistics_answer_links_course_page(db):
    uid, sid, _ = seed(db)
    config.set_now(START + timedelta(days=2))
    with TestClient(app) as client:
        res = client.post(f"/api/sessions/{sid}/chat", headers={"X-User-Id": str(uid)},
                          json={"message": "No me abre el link de Zoom"}).json()
    assert res["route"] == "logistica" and res["link"]["href"] == "course-detail.html"
    assert "Asistente Académico" in res["answer"]
