"""
Etapa 6.1 — Privacidad antes de NVIDIA: ningún nombre CONOCIDO sale en preguntas, historial ni fragmentos recuperados.

Todo con transporte simulado (`httpx.MockTransport`): ninguna prueba llama a NVIDIA. Los nombres de estas pruebas son ficticios.
Las pruebas contra la clase real (al final) se omiten solas si `demo-assets/` no existe y NUNCA imprimen texto ni nombres:
solo comparan contadores.

Alcance honesto: estas pruebas demuestran que los nombres que el filtro CONOCE (lista `mask-names.txt` + reglas) no viajan. No
pueden demostrar que no exista un nombre que ni la lista ni las reglas reconozcan.
"""
import contextlib
import importlib.util
import json
import re
import socket
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AcademicConfig
from app.knowledge.real_lesson import MASK_NAMES_FILE, _read_extra_names, load_real_lesson
from app.knowledge.retriever import KnowledgeRetriever
from app.llm import NvidiaKimiProvider
from app.llm.base import ChatTurn, ResponseKind, TutorContext
from app.llm.errors import LLMError, LLMUnmaskedContent
from app.llm.prompts import CANARY, SYSTEM_PROMPT
from app.main import create_app
from app.privacy import PrivacyFilter, mask_pii

from conftest import ADMIN_TOKEN, LESSON_1, SYNTH_DURATION, build_synthetic_txt, chat_n8n, rows
from llm_helpers import FAKE_NVIDIA_KEY, NVIDIA_CONFIG, FakeKimi, good_answer, json_response, nv_completion
from mp4_builder import build_mp4

ALLOW = replace(NVIDIA_CONFIG, allow_class_content=True)       # autorización concedida SOLO en estas pruebas, con transporte simulado
OK = lambda request, payload: json_response(nv_completion(good_answer("S1")))      # noqa: E731

# Nombres ficticios. LISTED: solo los conoce mask-names.txt. LEARNED ("Gracias Pedro" ya está en la clase sintética): lo aprende el filtro.
LISTED_A, LISTED_B, LEARNED = "Zulemita", "Bartolomeo", "Pedro"
WEBHOOK_LINE = f"El nodo webhook recibe datos de otra aplicación; {LISTED_A} lo probó ayer y {LISTED_B} lo configuró en su máquina."
TECH_SENTENCE = "Con n8n, Docker, una API, Git, Google, JavaScript y un RPA automatizamos Slack, Excel, Windows y WhatsApp."


def make_assets(root: Path, name: str, with_names: bool) -> Path:
    d = root / name
    d.mkdir()
    (d / "transcripcion-original.txt").write_bytes(build_synthetic_txt(extra={700: WEBHOOK_LINE, 720: TECH_SENTENCE}))
    (d / "clase-demo.mp4").write_bytes(build_mp4(SYNTH_DURATION))
    if with_names:
        (d / MASK_NAMES_FILE).write_text(f"# nombres ficticios de prueba\n{LISTED_A}\n{LISTED_B}\n", encoding="utf-8")
    return d


def app_for(db_path, assets, fake, config=ALLOW):
    return create_app(db_path=db_path, retriever=KnowledgeRetriever(), real_lesson=True, demo_assets_dir=assets, llm_config=config,
                      llm_client=fake.client(), academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN))


def sent_text(fake) -> str:
    return json.dumps(fake.payloads, ensure_ascii=False)


def tutor_payload(fake) -> dict:
    return next(p for p in fake.payloads if "<segmentos>" in p["messages"][-1]["content"])


def tutor_user_message(fake) -> str:
    return tutor_payload(fake)["messages"][-1]["content"]


@pytest.fixture
def assets(tmp_path):
    return make_assets(tmp_path, "con-nombres", with_names=True)


# ============================================================ PrivacyFilter (unidad)
def test_multiword_entries_are_registered_word_by_word_and_tiny_words_are_ignored():
    f = PrivacyFilter(extra_names=["Ana Torres", "Jo", "  ", ""])
    assert f.known_name_count == 2                                                # "Ana", "Torres"; "Jo" (<3) y vacíos fuera
    assert f.mask("Ana y Torres hablaron con Jo")[0] == "[nombre] y [nombre] hablaron con Jo"


@pytest.mark.parametrize("text, hidden", [
    ("Dime este Quirinox, ¿ya instalaste Docker?", "Quirinox"),
    ("dime Quirinox", "Quirinox"),
    ("Dinos Quirinox qué pasó", "Quirinox"),
    ("Perdóname Quirinox por la demora", "Quirinox"),
    ("Gracias Quirinox", "Quirinox"),
])
def test_vocative_triggers_hide_the_addressed_person(text, hidden):
    out, counts = PrivacyFilter().mask(text)
    assert hidden not in out and "[nombre]" in out and counts["name"] == 1


@pytest.mark.parametrize("text", ["Dime Docker", "Dime n8n", "Dime este Docker Desktop", "Dime, ¿qué es una API?", "Perdona, Git no abre"])
def test_vocative_triggers_do_not_hide_products_or_ordinary_words(text):
    assert PrivacyFilter().mask(text)[0] == text


def test_technical_terms_stay_intact_even_with_names_registered():
    f = PrivacyFilter(extra_names=[LISTED_A, LISTED_B])
    out, counts = f.mask(TECH_SENTENCE)
    assert out == TECH_SENTENCE and "name" not in counts
    assert f.mask(f"{LISTED_A} usa {TECH_SENTENCE}")[0] == f"[nombre] usa {TECH_SENTENCE}"


@pytest.mark.parametrize("invisible", ["\u200b", "\u00ad", "\ufeff", "\u2060"])
def test_invisible_characters_cannot_split_a_name_to_slip_through(invisible):
    f = PrivacyFilter(extra_names=["Anselmo"])
    out = f.mask(f"hola Fran{invisible}cis, ¿qué tal?")[0]
    assert out == "hola [nombre], ¿qué tal?" and f.known_name_hits(out) == 0


def test_known_name_hits_is_an_independent_check_that_returns_only_a_number():
    f = PrivacyFilter(extra_names=["Anselmo", "Müller"])
    assert f.known_name_hits("hola Anselmo") == 1 and f.known_name_hits("ANSELMO y anselmo") == 2
    assert f.known_name_hits("hola [nombre], usa Docker") == 0 and f.known_name_hits("") == 0
    assert isinstance(f.known_name_hits("Anselmo"), int)
    assert PrivacyFilter().known_name_hits("cualquier texto") == 0


# ====================================================== barrera final del proveedor (unidad)
class LeakyFilter(PrivacyFilter):
    """Filtro defectuoso a propósito: `mask` no oculta nada. La barrera debe detectarlo con su comprobación independiente."""

    def mask(self, text):
        return text, {}


def sql_evidence(retriever):
    matches = retriever.retrieve_segments("sql-demo", "ORDER BY", lesson_id=LESSON_1, top_k=2)
    dirty = matches[0].segment.model_copy(update={"text": f"Aquí {LISTED_A} explica ORDER BY y {LISTED_B} lo repite."})
    return [matches[0].model_copy(update={"segment": dirty}), *matches[1:]]


def context(retriever, **kw):
    base = dict(kind=ResponseKind.GROUNDED, student_message=f"Explícame ORDER BY, dice {LISTED_A}", course_name="SQL",
                lesson_title="Clase 1", topic_label="ORDER BY", evidence=sql_evidence(retriever),
                history=[ChatTurn("student", f"Soy curioso: {LISTED_B} preguntó antes"), ChatTurn("tutor", f"Respuesta previa para {LISTED_A}")])
    base.update(kw)
    return TutorContext(**base)


def provider_with(filter_, fake=None):
    fake = fake or FakeKimi(OK)
    p = NvidiaKimiProvider(NVIDIA_CONFIG, client=fake.client())
    p.attach_privacy(filter_)
    return p, fake


def test_the_final_barrier_remasks_question_history_and_fragments_and_leaves_the_system_prompt_untouched(retriever):
    p, fake = provider_with(PrivacyFilter(extra_names=[LISTED_A, LISTED_B]))
    p.generate(context(retriever))
    assert fake.calls == 1
    messages = fake.payloads[0]["messages"]
    text = json.dumps(messages, ensure_ascii=False)
    assert LISTED_A not in text and LISTED_B not in text and text.count("[nombre]") >= 5       # pregunta, 2 de historial, 2 del fragmento
    assert messages[0]["role"] == "system" and messages[0]["content"] == SYSTEM_PROMPT and CANARY in messages[0]["content"]
    assert p.egress_stats["messages_remasked"] >= 2 and p.egress_stats["blocked"] == 0
    assert "ORDER BY" in text                                                                   # el contenido técnico no se toca


def test_the_barrier_blocks_the_request_when_a_known_name_would_still_leave(retriever):
    p, fake = provider_with(LeakyFilter(extra_names=[LISTED_A, LISTED_B]))
    with pytest.raises(LLMUnmaskedContent) as excinfo:
        p.generate(context(retriever))
    assert fake.calls == 0                                                                      # NADA salió
    assert excinfo.value.fallback_allowed is True and excinfo.value.code == "llm_unmasked_content"
    assert LISTED_A not in str(excinfo.value) and LISTED_A not in excinfo.value.public_message
    assert p.egress_stats["blocked"] == 1


def test_the_barrier_also_covers_summaries_and_the_guardian(retriever):
    fake = FakeKimi(OK)
    p = NvidiaKimiProvider(ALLOW, client=fake.client())
    p.attach_privacy(PrivacyFilter(extra_names=[LISTED_A, LISTED_B]))
    with contextlib.suppress(LLMError):         # la respuesta simulada no tiene el formato del guardián/resumen: aquí importa lo que SALE
        p.classify_relevance(f"¿{LISTED_A} sabe de ORDER BY?", f"Clase de {LISTED_B}", [LISTED_B, "ORDER BY"])
    with contextlib.suppress(LLMError):
        p.summarize_segments(f"Fragmento de {LISTED_A}", sql_evidence(retriever), "Resume en una frase")
    assert fake.calls == 2 and p.egress_stats["requests_checked"] == 2
    assert LISTED_A not in sent_text(fake) and LISTED_B not in sent_text(fake) and "ORDER BY" in sent_text(fake)


def test_without_an_attached_filter_the_payload_is_not_modified(retriever):
    p, fake = provider_with(None)
    p.generate(context(retriever))
    assert LISTED_A in sent_text(fake) and p.egress_stats["requests_checked"] == 0              # compatibilidad: sin filtro, comportamiento previo


# ================================================== de extremo a extremo (clase sintética)
def test_a_name_typed_in_the_question_never_reaches_nvidia_but_technical_terms_do(db_path, assets):
    fake = FakeKimi(OK)
    q = f"¿{LISTED_A} y {LEARNED} entendieron para qué sirve Docker con n8n y la API?"
    with TestClient(app_for(db_path, assets, fake)) as c:
        body = chat_n8n(c, q).json()
    assert (body["mode"], body["provider"]) == ("LIVE", "nvidia-kimi") and body["references"]
    text = sent_text(fake)
    assert LISTED_A not in text and LISTED_B not in text and LEARNED not in text            # el nombre listado y el aprendido
    user = tutor_user_message(fake)
    assert "[nombre]" in user and all(term in user for term in ("Docker", "n8n", "API"))


def test_a_name_stored_unmasked_in_the_history_is_masked_before_leaving(db_path, tmp_path, assets):
    """Escenario real: la conversación se guardó ANTES de añadir el nombre a mask-names.txt."""
    stale_assets = make_assets(tmp_path, "sin-nombres", with_names=False)
    first = FakeKimi(OK)
    with TestClient(app_for(db_path, stale_assets, first)) as c:
        session = chat_n8n(c, f"{LISTED_B} dice que no entendió el nodo webhook, ¿me lo explicas?").json()["session_id"]
    stored = " ".join(r["content"] for r in rows(db_path, "SELECT content FROM messages WHERE session_id=?", (session,)))
    assert LISTED_B in stored                                                                # el historial viejo SÍ tiene el nombre

    second = FakeKimi(OK)
    with TestClient(app_for(db_path, assets, second)) as c:
        body = chat_n8n(c, "No entendí, ¿puedes explicarlo otra vez?", session_id=session).json()      # seguimiento: SÍ viaja el último turno
    assert body["session_id"] == session and body["provider"] == "nvidia-kimi"
    history = tutor_payload(second)["messages"][1:-1]
    assert history and any("[nombre]" in m["content"] for m in history)                     # el turno viejo viaja, pero enmascarado
    assert LISTED_B not in sent_text(second) and LISTED_A not in sent_text(second)


def test_a_name_cut_in_half_by_the_history_truncation_does_not_leave_a_fragment(db_path, tmp_path, assets):
    """El historial se trunca a 500 caracteres: se enmascara ANTES de truncar, o quedaría un trozo de nombre irreconocible."""
    stale_assets = make_assets(tmp_path, "sin-nombres", with_names=False)
    long_message = f"¿Qué hace el nodo webhook? {'x' * 263} {LISTED_B} fin"                    # el nombre empieza en el carácter 291: el corte en 300 deja 9 de sus 10 letras
    with TestClient(app_for(db_path, stale_assets, FakeKimi(OK))) as c:
        session = chat_n8n(c, long_message).json()["session_id"]
    second = FakeKimi(OK)
    with TestClient(app_for(db_path, assets, second)) as c:
        chat_n8n(c, "No entendí, ¿puedes explicarlo otra vez?", session_id=session)
    assert tutor_payload(second)["messages"][1:-1]                                           # el historial SÍ viajó (si no, la prueba sería vacía)
    assert LISTED_B[:6] not in sent_text(second) and "tolome" not in sent_text(second)         # ni el nombre ni un trozo reconocible


def test_names_inside_the_retrieved_fragments_are_masked_and_the_fragment_is_still_used(db_path, assets):
    fake = FakeKimi(OK)
    with TestClient(app_for(db_path, assets, fake)) as c:
        body = chat_n8n(c, "¿Qué hace el nodo webhook que recibe datos de otra aplicación?").json()
    user = tutor_user_message(fake)
    assert body["references"] and "webhook" in user                                         # el fragmento con nombres SÍ se recuperó...
    assert LISTED_A not in user and LISTED_B not in user and "se[nombre]" not in user       # ...pero sin los nombres
    assert re.search(r"\[nombre\] lo probó ayer y \[nombre\] lo configuró", user)


def test_when_a_leak_is_forced_in_a_fragment_nothing_is_sent_and_the_answer_falls_back_to_demo(db_path, assets, monkeypatch):
    """Fallo simulado: el fragmento llega 'sucio' Y el filtro del proveedor no sabe ocultarlo => la barrera bloquea el envío."""
    fake = FakeKimi(OK)
    with TestClient(app_for(db_path, assets, fake)) as c:
        service = c.app.state.service
        real_retrieve = service.retriever.retrieve_segments

        def dirty(*args, **kwargs):
            return [m.model_copy(update={"segment": m.segment.model_copy(update={"text": f"{m.segment.text} Comenta {LISTED_A}."})})
                    for m in real_retrieve(*args, **kwargs)]
        monkeypatch.setattr(service.retriever, "retrieve_segments", dirty)
        service.provider.attach_privacy(LeakyFilter(extra_names=[LISTED_A]))
        body = chat_n8n(c, "¿Qué es n8n?").json()
        guard = c.get("/health").json()["llm"]["egress_guard"]
    assert fake.calls == 0                                                                  # NADA salió hacia el proveedor
    assert body["fallback_used"] is True and body["provider"] == "demo" and body["mode"] == "DEMO" and body["references"]
    assert "llm_unmasked_content" in body["provider_notice"] and LISTED_A not in body["provider_notice"]
    assert guard["blocked"] == 1


def test_health_reports_the_barrier_without_exposing_any_name(db_path, assets):
    with TestClient(app_for(db_path, assets, FakeKimi(OK))) as c:
        chat_n8n(c, "¿Qué es n8n?")
        health = c.get("/health").json()
    guard = health["llm"]["egress_guard"]
    assert guard["active"] is True and guard["known_names"] >= 3 and guard["blocked"] == 0 and guard["requests_checked"] >= 1
    dumped = json.dumps(health, ensure_ascii=False)
    assert LISTED_A not in dumped and LISTED_B not in dumped and LEARNED not in dumped


def test_the_key_never_travels_in_the_body_and_only_the_documented_headers_are_sent(db_path, assets):
    fake = FakeKimi(OK)
    with TestClient(app_for(db_path, assets, fake)) as c:
        chat_n8n(c, "¿Qué es n8n?")
    assert FAKE_NVIDIA_KEY not in sent_text(fake)
    assert fake.requests[0].headers["authorization"] == f"Bearer {FAKE_NVIDIA_KEY}"        # solo en la cabecera, que nunca se informa


def test_demo_mode_and_the_closed_gate_still_work_and_send_nothing(db_path, assets):
    fake = FakeKimi(OK)
    with TestClient(app_for(db_path, assets, fake, config=NVIDIA_CONFIG)) as c:             # allow_class_content=False (valor por defecto)
        body = chat_n8n(c, f"¿Qué hace el nodo webhook? {LISTED_A} lo sabe").json()
    assert fake.calls == 0 and body["provider"] == "demo" and body["grounded"] is True
    assert LISTED_A not in json.dumps(body, ensure_ascii=False)                              # tampoco se devuelve al navegador


def test_the_gate_for_class_content_is_closed_by_default_everywhere():
    from app.config import LLMConfig
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"(?m)^NVIDIA_ALLOW_CLASS_CONTENT=false\s*$", example)                  # la plantilla sigue cerrada
    assert LLMConfig().allow_class_content is False and NVIDIA_CONFIG.allow_class_content is False


# ===================================== procedimiento de inspección local (scripts/inspect_nvidia_payload.py)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_nvidia_payload.py"


@pytest.fixture(scope="module")
def inspector():
    spec = importlib.util.spec_from_file_location("inspect_nvidia_payload", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_inspection_script_writes_a_sanitized_private_report_without_sending_anything(inspector, assets, tmp_path):
    out = tmp_path / "informe.md"
    assert inspector.main(["--assets-dir", str(assets), "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    assert "No se envió nada" in report and "Authorization: Bearer [REDACTED]" in report
    assert report.count("## Solicitud") >= 5 and "[nombre]" in report
    for forbidden in (LISTED_A, LISTED_B, LEARNED, FAKE_NVIDIA_KEY, inspector.FAKE_KEY, "alumno.demo@ejemplo.org", "912 345 678", "demo-assets", ".mp4"):
        assert forbidden not in report, "el informe contiene algo prohibido"
    assert "coincidencias residuales en TODOS los mensajes enviados: **0**" in report
    assert not list(tmp_path.glob("**/*.db"))                                               # usó una base temporal y no dejó nada


def test_the_inspection_script_blocks_sockets_while_it_runs_and_restores_them(inspector):
    connect = socket.socket.connect
    with inspector.blocked_network():
        with pytest.raises(RuntimeError, match="red bloqueada"):
            socket.create_connection(("example.com", 80), timeout=1)
        with pytest.raises(RuntimeError, match="red bloqueada"):
            socket.socket().connect(("example.com", 80))
    assert socket.socket.connect is connect


def test_the_inspection_script_refuses_to_write_an_unsafe_report(inspector):
    f = PrivacyFilter(extra_names=[LISTED_A])
    assert inspector.safety_problems("todo en orden [nombre]", f) == []
    assert inspector.safety_problems(f"hola {LISTED_A}", f)
    assert inspector.safety_problems("clave nvapi-" + "ABCDEFGHIJKLMNOPQRSTUV", f)
    assert inspector.safety_problems("escribe a alguien@correo.com", f)
    assert inspector.safety_problems("llama al +51 987 654 321", f)


def test_the_inspection_script_fails_cleanly_without_the_class(inspector, tmp_path, capsys):
    assert inspector.main(["--assets-dir", str(tmp_path), "--out", str(tmp_path / "x.md")]) == 1
    assert not (tmp_path / "x.md").exists() and "Nada que inspeccionar" in capsys.readouterr().out


def test_the_inspection_script_ignores_the_real_key_and_uses_a_throwaway_one(inspector, assets, tmp_path, monkeypatch):
    from app.config import settings
    env_key = "nvapi-" + "ENV-KEY-SENTINEL-0123456789abcdef"      # partido: no es un secreto literal
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", env_key)
    monkeypatch.setenv("NVIDIA_API_KEY", env_key)
    seen_auth = []
    original_call = inspector.Recorder.__call__

    def spy(self, request):
        seen_auth.append(request.headers.get("authorization"))
        return original_call(self, request)
    monkeypatch.setattr(inspector.Recorder, "__call__", spy)
    out = tmp_path / "informe.md"
    assert inspector.main(["--assets-dir", str(assets), "--out", str(out)]) == 0
    assert seen_auth and set(seen_auth) == {f"Bearer {inspector.FAKE_KEY}"}                  # siempre la clave de mentira
    assert env_key not in out.read_text(encoding="utf-8") and inspector.FAKE_KEY not in out.read_text(encoding="utf-8")
    assert not re.search(r"^from app\.config import .*\bsettings\b", SCRIPT.read_text(encoding="utf-8"), re.M)


# =========================================== contra la clase REAL (omitidas si no existe demo-assets/)
REAL_ASSETS = Path(__file__).resolve().parents[2] / "demo-assets"
needs_real = pytest.mark.skipif(not (REAL_ASSETS / "transcripcion-original.txt").is_file(), reason="no hay clase real en demo-assets/")
TECH_TERMS = ("n8n", "docker", "api", "git", "google", "javascript", "rpa", "slack", "excel", "windows", "whatsapp", "gmail",
              "telegram", "linux", "workflow", "python", "json", "zoom", "teams")


@pytest.fixture(scope="module")
def real():
    return load_real_lesson(REAL_ASSETS)


def _count(term: str, text: str) -> int:
    from app.knowledge.text_utils import normalize_text
    return len(re.findall(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalize_text(text)))


@needs_real
def test_real_class_has_no_known_name_left_in_any_segment(real):
    texts = [s.text for s in real.lesson.segments]
    assert len(texts) > 100
    assert sum(real.privacy.known_name_hits(t) for t in texts) == 0


@needs_real
def test_real_class_every_listed_name_is_effective_and_actually_occurs_in_the_original(real):
    names = _read_extra_names(REAL_ASSETS / MASK_NAMES_FILE)
    if not names:
        pytest.skip("no hay mask-names.txt")
    raw = (REAL_ASSETS / "transcripcion-original.txt").read_text(encoding="utf-8-sig", errors="replace")
    masked = "\n".join(s.text for s in real.lesson.segments)
    in_original = sum(_count(w.lower(), raw) for n in names for w in n.split())
    in_masked = sum(_count(w.lower(), masked) for n in names for w in n.split())
    assert in_original > 0 and in_masked == 0                                                # la lista sirve y deja 0 apariciones


@needs_real
def test_real_class_technical_terms_keep_exactly_their_original_count(real):
    raw = (REAL_ASSETS / "transcripcion-original.txt").read_text(encoding="utf-8-sig", errors="replace")
    masked = "\n".join(s.text for s in real.lesson.segments)
    changed = [t for t in TECH_TERMS if _count(t, raw) != _count(t, masked)]
    assert changed == [] and sum(_count(t, raw) for t in TECH_TERMS) > 100


@needs_real
def test_real_class_final_remasking_is_idempotent_so_it_never_degrades_the_fragments(real):
    changed = [s.start_seconds for s in real.lesson.segments if real.privacy.mask(s.text)[0] != s.text]
    assert changed == []


@needs_real
def test_real_class_student_questions_with_listed_names_are_masked_by_the_service(real):
    names = _read_extra_names(REAL_ASSETS / MASK_NAMES_FILE)
    if not names:
        pytest.skip("no hay mask-names.txt")
    first = names[0].split()[0]
    question = f"¿Qué le respondió el profesor a {first} sobre Docker? Mi correo es a.b@c.org"
    out = mask_pii(question)
    out = real.privacy.mask(out)[0]
    assert real.privacy.known_name_hits(out) == 0 and "Docker" in out and "[correo]" in out and "[nombre]" in out
