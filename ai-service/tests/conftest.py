import json
from pathlib import Path

import pytest

from app.knowledge.retriever import KnowledgeRetriever

LESSON_1 = "clase-01-introduccion-sql"
LESSON_2 = "clase-02-uniones-y-agrupacion"


LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    """
    Aísla CADA prueba del entorno real del equipo. `app.config` carga ai-service/.env al importarse: si el dueño activó NVIDIA
    (LLM_PROVIDER=nvidia, LLM_LIVE_AUTHORIZED=true, clave real), una prueba con la configuración por defecto haría una solicitud
    REAL. Por eso: (1) la configuración por defecto vuelve a DEMO sin claves; (2) solo se permiten conexiones a la propia máquina
    (el bucle de eventos de Windows y los servidores de prueba locales las necesitan) y la resolución DNS de nombres externos falla.
    Las pruebas que necesitan otra configuración la pasan de forma explícita (llm_config=...) o con monkeypatch propio.
    """
    import socket
    from app.config import settings

    for name, value in (("LLM_PROVIDER", "demo"), ("LLM_API_KEY", ""), ("LLM_LIVE_AUTHORIZED", False),
                        ("NVIDIA_API_KEY", ""), ("NVIDIA_ALLOW_CLASS_CONTENT", False), ("ANSWER_CACHE_ENABLED", False),
                        ("CHAT_STREAMING_ENABLED", False)):
        monkeypatch.setattr(settings, name, value)

    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def guarded_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, (tuple, list)) and address else None
        if host is not None and str(host) not in LOOPBACK:
            raise RuntimeError("las pruebas no pueden conectarse a la red externa (hermetic_environment)")
        return real_connect(self, address, *args, **kwargs)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is not None and str(host) not in LOOPBACK:
            raise RuntimeError("las pruebas no pueden resolver nombres externos (hermetic_environment)")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(scope="session")
def retriever() -> KnowledgeRetriever:
    """Recuperador cargado con el material DEMO real del repositorio."""
    return KnowledgeRetriever()


@pytest.fixture
def two_course_tree(tmp_path: Path) -> Path:
    """
    Árbol de transcripciones sintético con dos cursos que comparten vocabulario
    (y hasta el mismo lesson_id) para comprobar que no se mezclan.
    """
    root = tmp_path / "transcripts"
    (root / "curso-a").mkdir(parents=True)
    (root / "curso-b").mkdir(parents=True)

    (root / "curso-a" / "clase-01.vtt").write_text(
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:30.000\nEl nodo webhook recibe datos de la aplicación.\n\n"
        "00:00:30.000 --> 00:01:00.000\nLa autenticación protege el endpoint.\n",
        encoding="utf-8",
    )
    (root / "curso-a" / "clase-02.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:40.000\nEl webhook también sirve para disparar flujos.\n",
        encoding="utf-8",
    )
    (root / "curso-b" / "clase-01.json").write_text(
        json.dumps({
            "title": "Otro curso",
            "segments": [
                {"start_seconds": 10, "end_seconds": 50, "text": "En este otro curso el webhook se usa distinto."}
            ],
        }),
        encoding="utf-8",
    )
    return root


# --------------------------------------------------------------------- API
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

STUDENT_A = "demo-student-001"
STUDENT_B = "demo-student-002"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Base SQLite temporal y aislada para cada prueba."""
    return tmp_path / "rescuepath-test.db"


@pytest.fixture
def client(db_path: Path, retriever: KnowledgeRetriever):
    with TestClient(create_app(db_path=db_path, retriever=retriever)) as c:
        yield c


def post_chat(client, message, student_id=STUDENT_A, course_id="sql-demo",
              lesson_id=LESSON_1, session_id=None, **extra):
    """Envía un mensaje a /api/chat con valores por defecto de la clase 1."""
    payload = {"student_id": student_id, "course_id": course_id, "lesson_id": lesson_id,
               "session_id": session_id, "message": message}
    payload.update(extra)
    return client.post("/api/chat", json=payload)


# ------------------------------------------------------------------ Etapa 4
import socket  # noqa: E402
import sqlite3  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from app.config import AcademicConfig  # noqa: E402

LESSON_2 = "clase-02-uniones-y-agrupacion"
TELEMETRY_TOKEN = "tok-test-123456"


class FakeClock:
    """Reloj controlable para probar caducidad de intentos y periodos de la micro-pregunta."""

    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def acad_cfg():
    return AcademicConfig(micro_question_reference_date="2026-09-01", telemetry_api_token=TELEMETRY_TOKEN)


@pytest.fixture
def make_app(db_path, retriever, clock, acad_cfg):
    """Fábrica de aplicaciones con base temporal, reloj falso y configuración académica de prueba."""
    def _make(**overrides):
        kwargs = dict(db_path=db_path, retriever=retriever, academic_config=acad_cfg, clock=clock)
        kwargs.update(overrides)
        return create_app(**kwargs)
    return _make


@pytest.fixture
def acad(make_app):
    with TestClient(make_app()) as c:
        yield c


@pytest.fixture
def no_network(monkeypatch):
    """Cualquier intento de abrir una conexión de red falla: demuestra que las funciones no dependen de APIs externas."""
    def blocked(*args, **kwargs):
        raise RuntimeError("acceso a red bloqueado en las pruebas")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def rows(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def start_quiz(c, student_id=STUDENT_A, lesson_id=LESSON_2, n=4, **headers):
    return c.post("/api/quiz/start", json={"student_id": student_id, "course_id": "sql-demo",
                                           "lesson_id": lesson_id, "num_questions": n}, headers=headers)


def correct_options(db_path, attempt_id):
    """{question_id: option_id correcta} leído del servidor (el cliente nunca lo recibe antes de enviar)."""
    return {r["question_id"]: r["correct_option_id"]
            for r in rows(db_path, "SELECT question_id, correct_option_id FROM quiz_answers WHERE attempt_id = ?", (attempt_id,))}


def submit_quiz(c, attempt_id, answers, student_id=STUDENT_A, **headers):
    return c.post("/api/quiz/submit", json={"attempt_id": attempt_id, "student_id": student_id, "answers": answers},
                  headers=headers)


def answer_with(db_path, started, correct=True, only=None):
    """Respuestas para un intento: todas correctas o todas incorrectas (opcionalmente solo las primeras `only`)."""
    key = correct_options(db_path, started["attempt_id"])
    out = []
    for q in started["questions"]:
        right = key[q["question_id"]]
        pick = right if correct else next(o["option_id"] for o in q["options"] if o["option_id"] != right)
        out.append({"question_id": q["question_id"], "option_id": pick})
    return out if only is None else out[:only]


# ------------------------------------------------------------------ Etapa 5
# Clase SINTÉTICA de demostración (contenido inventado) con el mismo formato que la clase real:
# marcas M:SS / H:MM:SS solas en una línea, seguidas de una línea de texto, y un MP4 mínimo válido.
import hashlib  # noqa: E402

from app.knowledge.real_lesson import REAL_COURSE_ID, REAL_LESSON_ID  # noqa: E402
from app.knowledge.retriever import KnowledgeRetriever as _KR  # noqa: E402
from mp4_builder import build_mp4  # noqa: E402

N8N_COURSE, N8N_LESSON = REAL_COURSE_ID, REAL_LESSON_ID
SYNTH_DURATION = 7300.0              # 2:01:40
SENTINEL_NAME = "Maria Lopezzi"      # nombre ficticio que debe quedar enmascarado
SENTINEL_EMAIL = "maria.lopezzi@ejemplo.org"
SENTINEL_PHONE = "+51 987 654 321"

FILLER = ["los cables rojos", "una tabla amarilla", "el tablero azul", "la ventana grande", "un reporte semanal",
          "las carpetas compartidas", "el calendario del equipo", "una lista de tareas", "los permisos del archivo",
          "una plantilla de correo", "el historial reciente", "las notas del proyecto"]
TOPIC_LINES = {      # segundo -> texto (contenido inventado sobre "n8n")
    30: f"Soy {SENTINEL_NAME}, escríbanme a {SENTINEL_EMAIL} o llamen al {SENTINEL_PHONE} para dudas.",
    120: "Primero veamos qué es n8n: una herramienta para crear flujos automatizados que conectan aplicaciones.",
    180: "El no code significa construir sin escribir programas, y el low code permite escribir poco código cuando hace falta.",
    300: "Conviene automatizar los procesos repetitivos y propensos a errores, como copiar datos entre hojas de cálculo.",
    420: "Un workflow es un flujo de pasos conectados; un RPA, en cambio, imita a una persona usando la pantalla.",
    540: "Docker sirve para empaquetar n8n en un contenedor y ejecutarlo igual en cualquier computadora.",
    900: "Gracias Pedro por la pregunta, la respuesta está en la documentación del producto.",
    1500: "Esto ocurrirá a las 2:30, por ejemplo, cada día laborable.",
    3600: "Pasada la primera hora retomamos con los nodos de decisión y las condiciones.",
    5400: "Para terminar repasamos las credenciales y cómo guardarlas de forma segura.",
}


def build_synthetic_txt(duration: float = SYNTH_DURATION, crlf: bool = True, extra=None) -> bytes:
    def label(sec: int) -> str:
        h, rest = divmod(sec, 3600)
        m, s = divmod(rest, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    entries = {}
    t, i = 5, 0
    while t < duration - 40:
        entries[t] = f"Aquí comentamos {FILLER[i % len(FILLER)]} y seguimos con el ejercicio número {i}."
        t += 30 if t > 2400 else 20
        i += 1
    entries.update(TOPIC_LINES)
    if extra:
        entries.update(extra)
    lines = []
    for sec in sorted(entries):
        lines += [label(sec), entries[sec]]
        if sec == 1500:
            lines += ["2:30"]                      # número pronunciado suelto: NO es una marca (retrocede en el tiempo)
    newline = "\r\n" if crlf else "\n"
    return newline.join(lines).encode("utf-8")


@pytest.fixture
def fake_assets(tmp_path):
    """demo-assets sintético: transcripción de prueba + MP4 mínimo (duración 7300 s)."""
    d = tmp_path / "demo-assets"
    d.mkdir()
    (d / "transcripcion-original.txt").write_bytes(build_synthetic_txt())
    (d / "clase-demo.mp4").write_bytes(build_mp4(SYNTH_DURATION))
    return d


@pytest.fixture
def n8n_app(db_path, fake_assets, clock, acad_cfg):
    """App con la clase 'real' sintética cargada. Usa un recuperador NUEVO (no muta el fixture de sesión)."""
    cfg = AcademicConfig(micro_question_reference_date="2026-09-01", telemetry_api_token=TELEMETRY_TOKEN, admin_api_token=ADMIN_TOKEN)
    return create_app(db_path=db_path, retriever=_KR(), real_lesson=True, demo_assets_dir=fake_assets,
                      academic_config=cfg, clock=clock)


@pytest.fixture
def n8n(n8n_app):
    with TestClient(n8n_app) as c:
        yield c


ADMIN_TOKEN = "admin-token-test-98765"


def chat_n8n(c, message, session_id=None, student_id=STUDENT_A, **headers):
    return c.post("/api/chat", json={"student_id": student_id, "course_id": N8N_COURSE, "lesson_id": N8N_LESSON,
                                     "session_id": session_id, "message": message}, headers=headers)
