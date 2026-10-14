"""
Etapa 8 — Agente Validador (simulación académica local) y su convivencia con la pantalla existente.

El validador es 100 % JavaScript local: dos preguntas verificadas contra la transcripción, sin backend, sin modelo y sin registros. Estas
pruebas lo ejercitan en un Chrome REAL (se omiten si no hay Chrome/Node) con las conexiones externas bloqueadas por `conftest`; el NVIDIA de
las pruebas es un transporte simulado que responde 503 (fuera de servicio). Nada de esto prueba la calidad de Kimi.
"""
import asyncio
import json
import re
import shutil
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.config import AcademicConfig
from app.knowledge.real_lesson import load_real_lesson
from app.knowledge.retriever import KnowledgeRetriever
from app.knowledge.text_utils import tokenize
from app.main import create_app

from browser_helpers import FRONTEND, chrome_page, find_chrome, free_port, static_server
from conftest import ADMIN_TOKEN
from llm_helpers import NVIDIA_CONFIG, FakeKimi

ROOT = Path(__file__).resolve().parents[2]
HTML = (FRONTEND / "demo-n8n.html").read_text(encoding="utf-8")
CHAT_JS = (FRONTEND / "demo-n8n.js").read_text(encoding="utf-8")
VALIDATOR_JS = (FRONTEND / "validator-n8n.js").read_text(encoding="utf-8")
CSS = (FRONTEND / "demo-n8n.css").read_text(encoding="utf-8")
REAL_ASSETS = ROOT / "demo-assets"
needs_chrome = pytest.mark.skipif(find_chrome() is None, reason="no hay Chrome/Edge instalado")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")
needs_real = pytest.mark.skipif(not (REAL_ASSETS / "transcripcion-original.txt").is_file(), reason="no hay clase real en demo-assets/")

LABELS = {"wrong": "Aún no", "right": "Correcto"}


# ============================================================ estructura de la página (sin navegador)
def test_the_page_has_the_two_tabs_and_the_validator_block():
    assert 'id="tabClass"' in HTML and 'id="tabValidator"' in HTML and "Validar aprendizaje" in HTML and "Clase y tutor" in HTML
    assert 'id="panelClass"' in HTML and 'id="panelValidator"' in HTML and 'role="tablist"' in HTML
    assert "Agente Validador — Participación Efectiva" in HTML and "Simulación académica" in HTML
    assert "Validar respuestas" in HTML and "Volver a intentar" in HTML
    assert HTML.index('src="demo-n8n.js"') < HTML.index('src="validator-n8n.js"')                 # el validador usa lo que expone la pantalla
    assert re.search(r'id="panelValidator"[^>]*\bhidden\b', HTML)                                    # arranca en la pestaña de siempre


def test_the_existing_screen_is_untouched_by_the_new_tabs():
    for element in ('id="player"', 'id="chapterList"', 'id="summaryOverview"', 'id="summaryBadges"', 'id="messages"', 'id="chatForm"',
                    'id="sendBtn"', 'id="suggestions"', 'id="question"', 'id="errorPanel"', 'id="modeBadge"'):
        assert element in HTML, element
    for feature in ("Kimi está preparando tu respuesta…", "AbortController", "NO la generó Kimi", "Resumen local por temas", "seekTo(target)"):
        assert feature in CHAT_JS, feature
    assert CHAT_JS.count("window.RescueDemo = { seekTo };") == 1


def test_the_validator_makes_no_network_calls_and_stores_nothing():
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", VALIDATOR_JS, flags=re.S)
    for forbidden in ("fetch(", "XMLHttpRequest", "sendBeacon", "localStorage", "sessionStorage", "indexedDB", "document.cookie", "/api/", "nvidia", "Idempotency"):
        assert forbidden not in code, forbidden
    assert "Simulación académica" in HTML and "no registra asistencia" in VALIDATOR_JS
    assert "innerHTML" not in code                                                                    # todo se pinta con textContent


# ============================================================ contenido y lógica
def node(script: str):
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=str(FRONTEND), timeout=30)
    assert out.returncode == 0, out.stderr[:300]
    return json.loads(out.stdout)


@needs_node
def test_the_quiz_has_two_questions_with_one_correct_option_each():
    quiz = node("console.log(JSON.stringify(require('./validator-n8n.js').QUIZ))")
    assert len(quiz["questions"]) == 2
    for q in quiz["questions"]:
        ids = [o["id"] for o in q["options"]]
        assert 3 <= len(ids) <= 4 and len(set(ids)) == len(ids) and q["correct"] in ids
        assert q["text"].endswith("?") and q["explanation"] and q["hint"] and q["reference"]["label"]
        assert q["explanation"] not in [o["text"] for o in q["options"]]
    assert [q["correct"] for q in quiz["questions"]] == ["b", "c"]                                    # no siempre la primera alternativa
    assert [q["reference"]["startSeconds"] for q in quiz["questions"]] == [558, 1544]
    assert [q["reference"]["label"] for q in quiz["questions"]] == ["09:18–10:10", "25:44–26:15"]


@needs_node
@pytest.mark.parametrize("answers, correct, validated", [
    ({"q1": "a", "q2": "a"}, 0, False), ({"q1": "b", "q2": "a"}, 1, False), ({"q1": "a", "q2": "c"}, 1, False), ({"q1": "b", "q2": "c"}, 2, True)])
def test_grading_gives_0_1_or_2_of_2(answers, correct, validated):
    graded = node(f"console.log(JSON.stringify(require('./validator-n8n.js').gradeQuiz({json.dumps(answers)})))")
    assert (graded["correct"], graded["total"], graded["complete"], graded["validated"]) == (correct, 2, True, validated)


@needs_node
@pytest.mark.parametrize("answers", [{}, {"q1": "b"}, {"q2": "c"}, {"q1": "b", "q2": "zz"}, {"q1": "b", "q2": None}, None])
def test_an_incomplete_quiz_is_never_scored(answers):
    graded = node(f"console.log(JSON.stringify(require('./validator-n8n.js').gradeQuiz({json.dumps(answers)})))")
    assert graded["complete"] is False and graded["correct"] == 0 and graded["validated"] is False


@needs_real
def test_the_answers_are_supported_by_the_real_transcript():
    """Cada pregunta se verifica contra los renglones reales de su tramo (compara palabras; no imprime texto de la clase)."""
    lesson = load_real_lesson(REAL_ASSETS).lesson
    lines = [ln for s in lesson.segments for ln in (s.lines or [])]

    def passage(start, end):
        return " " + " ".join(tokenize(" ".join(ln.text for ln in lines if start <= ln.start_seconds < end))) + " "
    q1 = passage(558, 610)                                    # 09:18–10:10
    assert all(w in q1 for w in (" repetitivos ", " reglas ", " automatizar ")) and " ordenadas " in q1 and " definidas " in q1
    q2 = passage(1544, 1575)                                  # 25:44–26:15
    assert all(w in q2 for w in (" workflow ", " api ", " rpa ", " clic ", " botones "))
    assert any(ln.start_seconds == 558 for ln in lines) and any(ln.start_seconds == 1544 for ln in lines)   # los saltos caen en un renglón real


# ============================================================ navegador real: el flujo del validador (sin backend)
CHECKS = """(() => ({
  tabs: [...document.querySelectorAll('[role=tab]')].map(t => [t.textContent.trim(), t.getAttribute('aria-selected')]),
  classHidden: document.getElementById('panelClass').hidden, validatorHidden: document.getElementById('panelValidator').hidden,
  questions: [...document.querySelectorAll('.demo-q')].map(q => ({legend: q.querySelector('legend').textContent, text: q.querySelector('.demo-q-text').textContent,
    options: q.querySelectorAll('input[type=radio]').length, checked: q.querySelectorAll('input:checked').length})),
  result: document.getElementById('quizResult').textContent, resultClass: document.getElementById('quizResult').className,
  attempt: document.getElementById('quizAttempt').textContent, hint: document.getElementById('quizHint').textContent,
  validateDisabled: document.getElementById('quizValidate').disabled, retryHidden: document.getElementById('quizRetry').hidden,
  feedback: [...document.querySelectorAll('.demo-q-feedback')].map(f => f.textContent), classes: [...document.querySelectorAll('.demo-q')].map(q => q.className),
  radiosDisabled: [...document.querySelectorAll('.demo-q input')].every(i => i.disabled),
}))()"""


def pick(q1, q2):
    return f"(() => {{ document.querySelector('input[name=q1][value={q1}]').click(); document.querySelector('input[name=q2][value={q2}]').click(); }})()"


async def validator_flow(url):
    async with chrome_page(url) as c:
        await c.wait("document.getElementById('errorPanel') !== null")
        net_before = len(await c.js("window.__net"))
        s = await c.js(CHECKS)
        assert s["tabs"] == [["Clase y tutor", "true"], ["Validar aprendizaje", "false"]] and s["validatorHidden"] and not s["classHidden"]

        await c.js("document.getElementById('tabValidator').click()")                           # abrir la pestaña
        s = await c.js(CHECKS)
        assert not s["validatorHidden"] and s["classHidden"] and s["tabs"][1][1] == "true"
        assert [q["options"] for q in s["questions"]] == [4, 4] and [q["checked"] for q in s["questions"]] == [0, 0]
        assert s["questions"][0]["legend"] == "Pregunta 1 de 2" and "reporte repetitivo" in s["questions"][0]["text"]
        assert "aplicación antigua" in s["questions"][1]["text"] and "API" in s["questions"][1]["text"]
        assert s["attempt"] == "Intento 1" and s["hint"] == "Respondidas: 0 de 2."

        await c.js("document.querySelector('input[name=q1][value=b]').click()")                 # una sola respuesta
        assert (await c.js(CHECKS))["hint"] == "Respondidas: 1 de 2."
        await c.js("document.getElementById('quizValidate').click()")
        s = await c.js(CHECKS)
        assert "Responde las dos preguntas" in s["result"] and "Te faltan 1" in s["result"] and "respuestas correctas" not in s["result"]
        assert not s["validateDisabled"] and s["retryHidden"]                                  # no se calificó: sigue editable

        await c.js(pick("a", "a"))                                                              # 0/2 (cambia la selección de q1)
        await c.js("document.getElementById('quizValidate').click()")
        s = await c.js(CHECKS)
        assert s["result"].startswith("0 de 2 respuestas correctas") and "Volver a intentar" in s["result"] and "ok" not in s["resultClass"].split()
        assert s["classes"][0].endswith("bad") and s["classes"][1].endswith("bad") and s["radiosDisabled"] and s["validateDisabled"] and not s["retryHidden"]
        assert all("Aún no" in f for f in s["feedback"]) and "Pregúntate si la aplicación ofrece una API" in s["feedback"][1]
        assert "En la clase se distingue" not in s["feedback"][1]                              # no revela la respuesta: el estudiante puede reintentar
        assert "09:18–10:10" in s["feedback"][0] and "25:44–26:15" in s["feedback"][1]
        assert "Comprensión validada" not in s["result"]
        assert s["hint"] == "Respuestas validadas."                                              # ya no invita a validar de nuevo

        await c.js("document.getElementById('quizRetry').click()")                              # nuevo intento
        s = await c.js(CHECKS)
        assert s["attempt"] == "Intento 2" and [q["checked"] for q in s["questions"]] == [0, 0] and s["result"] == "" and not s["validateDisabled"]
        assert s["retryHidden"] and not s["radiosDisabled"] and all(f == "" for f in s["feedback"])

        await c.js(pick("b", "a"))                                                              # 1/2
        await c.js("document.getElementById('quizValidate').click()")
        s = await c.js(CHECKS)
        assert s["result"].startswith("1 de 2 respuestas correctas") and s["classes"][0].endswith("ok") and s["classes"][1].endswith("bad")
        assert "Correcto" in s["feedback"][0] and "En la clase se explica" in s["feedback"][0] and "Aún no" in s["feedback"][1]

        await c.js("document.getElementById('quizRetry').click()")
        await c.js(pick("b", "c"))                                                              # 2/2
        await c.js("document.getElementById('quizValidate').click()")
        s = await c.js(CHECKS)
        assert s["attempt"] == "Intento 3"
        assert s["result"].startswith("2 de 2 respuestas correctas") and "Comprensión validada (simulación académica)" in s["result"]
        assert "no registra asistencia ni participación oficial" in s["result"] and "ok" in s["resultClass"].split()
        assert all(cl.endswith("ok") for cl in s["classes"]) and all("Correcto" in f for f in s["feedback"])

        # volver a la clase desde la referencia del quiz, sin recargar
        await c.js("window.__seek = []; window.RescueDemo.seekTo = (s) => window.__seek.push(s);")
        await c.js("document.querySelector('#quiz-q2 .demo-cite').click()")
        s = await c.js(CHECKS)
        assert await c.js("window.__seek") == [1544] and not s["classHidden"] and s["validatorHidden"] and s["tabs"][0][1] == "true"
        assert len(await c.js("window.__net")) == net_before                                    # el validador no hizo NINGUNA solicitud de red
        return True


@needs_chrome
def test_the_validator_works_end_to_end_in_a_real_browser_even_without_a_backend():
    port = free_port()
    with static_server(port):
        assert asyncio.run(validator_flow(f"http://127.0.0.1:{port}/demo-n8n.html?api=http://127.0.0.1:9"))   # no hay backend: todo sigue funcionando


@needs_chrome
def test_the_hash_opens_the_validator_directly_and_tabs_are_keyboard_navigable():
    async def scenario(url):
        async with chrome_page(url + "#validar") as c:
            assert (await c.js(CHECKS))["tabs"] == [["Clase y tutor", "false"], ["Validar aprendizaje", "true"]]
            await c.js("document.getElementById('tabValidator').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true}))")
            s = await c.js(CHECKS)
            assert s["tabs"][0][1] == "true" and s["validatorHidden"] and await c.js("document.activeElement.id") == "tabClass"
            return True
    port = free_port()
    with static_server(port):
        assert asyncio.run(scenario(f"http://127.0.0.1:{port}/demo-n8n.html?api=http://127.0.0.1:9"))


# ============================================================ convivencia con el resto de la pantalla (backend + NVIDIA caído)
class DownNvidia(FakeKimi):
    """NVIDIA fuera de servicio: toda solicitud recibe 503. Cuenta cuántas llegaron."""

    def __init__(self):
        super().__init__(lambda request, payload: httpx.Response(503, json={"status": 503, "title": "Service Unavailable"}))


async def coexistence(url, kimi):
    async with chrome_page(url) as c:
        await c.wait("document.getElementById('chapterList').children.length > 0", 40)
        chapters_before = await c.js("[...document.querySelectorAll('.demo-chapter')].map(b => b.textContent)")
        overview = await c.js("document.getElementById('summaryOverview').textContent")
        assert chapters_before and overview and "Cargando" not in overview and await c.js("document.getElementById('errorPanel').hidden")
        await c.js("document.getElementById('player').__marker = 'mismo-nodo'")

        # chat (NVIDIA caído => respuesta DEMO etiquetada) con cita
        await c.js("document.getElementById('question').value = '¿Qué es n8n?'")
        await c.js("document.getElementById('chatForm').requestSubmit()")
        await c.wait("document.querySelectorAll('.demo-msg.tutor:not(.pending)').length >= 1 && !document.querySelector('.demo-msg.pending')", 40)
        answer = await c.js("(() => { const m = document.querySelector('.demo-msg.tutor:not(.pending)'); return {pills: [...m.querySelectorAll('.demo-pill')].map(x => x.textContent), "
                            "cites: [...m.querySelectorAll('.demo-cite')].map(b => b.textContent), fallback: m.classList.contains('fallback')}; })()")
        assert answer["cites"] and answer["fallback"] and any("NO la generó Kimi" in p for p in answer["pills"])
        kimi_calls_after_chat = kimi.calls
        net_after_chat = len(await c.js("window.__net"))
        await c.js("document.getElementById('question').value = 'borrador sin enviar'")

        # ir al validador, usarlo, y volver
        await c.js("document.getElementById('tabValidator').click()")
        await c.js(pick("b", "c"))
        await c.js("document.getElementById('quizValidate').click()")
        assert (await c.js(CHECKS))["result"].startswith("2 de 2 respuestas correctas")
        await c.js("document.getElementById('tabClass').click()")

        s = await c.js(CHECKS)
        assert not s["classHidden"] and s["validatorHidden"]
        assert await c.js("document.getElementById('player').__marker") == "mismo-nodo"           # el mismo reproductor: nada se recreó
        assert await c.js("[...document.querySelectorAll('.demo-chapter')].map(b => b.textContent)") == chapters_before
        assert await c.js("document.querySelectorAll('.demo-msg.tutor:not(.pending)').length") == 1
        assert await c.js("document.getElementById('question').value") == "borrador sin enviar"      # el texto a medias se conserva
        assert kimi.calls == kimi_calls_after_chat                                                  # el quiz no hizo ninguna llamada a Kimi/NVIDIA
        assert len(await c.js("window.__net")) == net_after_chat                                    # ni ninguna otra solicitud de red

        # el chat sigue funcionando después de usar el validador y la cita salta al video
        # (el MP4 de prueba no se decodifica: se observa que `seekTo` se ejecuta, pues lo primero que hace es llevar el reproductor a la vista)
        await c.js("window.__scrolled = 0; document.getElementById('player').scrollIntoView = () => { window.__scrolled += 1; };")
        await c.js("document.querySelector('.demo-msg.tutor .demo-cite').click()")
        assert await c.js("window.__scrolled") == 1
        await c.js("document.querySelectorAll('.demo-chapter')[0].click()")
        assert await c.js("window.__scrolled") == 2                                                 # y un capítulo también navega
        return True


@needs_chrome
def test_the_rest_of_the_screen_keeps_working_with_the_validator_and_nvidia_down(db_path, fake_assets):
    kimi = DownNvidia()
    api_port, front_port = free_port(), free_port()
    cfg = replace(NVIDIA_CONFIG, allow_class_content=True, timeout_seconds=5.0, max_live_calls=0)
    app = create_app(db_path=db_path, retriever=KnowledgeRetriever(), real_lesson=True, demo_assets_dir=fake_assets, llm_config=cfg,
                     llm_client=kimi.client(), academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN),
                     cors_origins=[f"http://127.0.0.1:{front_port}"])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started
        with static_server(front_port):
            assert asyncio.run(coexistence(f"http://127.0.0.1:{front_port}/demo-n8n.html?api=http://127.0.0.1:{api_port}", kimi))
        assert kimi.calls == 1                                                                      # solo la pregunta del chat llegó a "NVIDIA"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
