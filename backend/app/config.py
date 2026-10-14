"""Parámetros del MVP. Los umbrales del diagnóstico son valores iniciales para recalibrar con datos reales."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dateutil import parser as dateparser
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
DATA_DIR = Path(os.getenv("DATA_DIR", BACKEND_DIR / "data"))
ZOOM_DIR = DATA_DIR / "zoom"
COHORT_DIR = DATA_DIR / "cohort"
SAMPLE_DIR = DATA_DIR / "sample"
SAMPLE_SESSION_DIR = SAMPLE_DIR / "session_demo"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Base de trabajo: Postgres si DATABASE_URL lo indica (Neon en Vercel); si no, SQLite.
# El build genera la base de demo en data/dmc.db (snapshot). Con Postgres se copia allí si está vacía
# (y con POST /api/admin/reset-demo); en Vercel sin Postgres se copia a /tmp y dura lo que la instancia.
ON_VERCEL = bool(os.getenv("VERCEL"))
SQLITE_FILE = DATA_DIR / "dmc.db"


def _normalize_db_url(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL") or (
    "sqlite:////tmp/dmc.db" if ON_VERCEL else f"sqlite:///{SQLITE_FILE.as_posix()}"
))
IS_SQLITE = DATABASE_URL.startswith("sqlite")
DB_SNAPSHOT = SQLITE_FILE if ON_VERCEL or not IS_SQLITE else None

# --- LLM (proveedor compatible con OpenAI; Groq por defecto) ---
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL_CHAT = os.getenv("LLM_MODEL_CHAT", "openai/gpt-oss-120b")
LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", "openai/gpt-oss-120b")
LLM_CONTEXT_TOKENS = int(os.getenv("LLM_CONTEXT_TOKENS", "131072"))
LLM_TEMPERATURE = os.getenv("LLM_TEMPERATURE")  # vacío = valor por defecto del proveedor
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")  # gpt-oss: el razonamiento cuenta como tokens
LLM_MOCK = os.getenv("LLM_MOCK", "").strip() in {"1", "true", "yes"} or not LLM_API_KEY or LLM_API_KEY == "..."
# Cupo del proveedor (plan gratuito de Groq para openai/gpt-oss-120b) y fracción que usamos como máximo.
# Sin cupo, el servicio responde con su simulación al instante en vez de fallar o hacer esperar al alumno.
LLM_RPM = int(os.getenv("LLM_RPM", "30"))
LLM_RPD = int(os.getenv("LLM_RPD", "1000"))
LLM_TPM = int(os.getenv("LLM_TPM", "8000"))
LLM_TPD = int(os.getenv("LLM_TPD", "200000"))
LLM_SAFETY = float(os.getenv("LLM_SAFETY", "0.85"))
LLM_MAX_WAIT_S = float(os.getenv("LLM_MAX_WAIT_S", "6"))  # espera máxima por cupo antes de simular
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "25"))
LLM_CACHE_TTL_S = int(os.getenv("LLM_CACHE_TTL_S", "21600"))  # respuestas idénticas no gastan cupo
CHAT_TOP_FRAGMENTS = 10  # fragmentos de transcripción por turno del tutor
CHAT_TOP_UNITS = 4  # unidades de material por turno
CHAT_HISTORY_TURNS = 4  # últimos mensajes del hilo que ve el LLM
# Lo que entra al prompt del tutor (el cupo de Groq es de 8K tokens por minuto): ~1K tokens de contexto.
CHAT_LLM_FRAGMENTS = 6
CHAT_LLM_UNITS = 3
CHAT_LLM_UNIT_WORDS = 50
CHAT_MAX_WORDS = 90
CHAT_EVIDENCE_MIN = 0.6  # peso (IDF) de la pregunta que debe aparecer en la clase para no ser «fuera de la clase»
CHAT_QUIZ_SIMILARITY = 0.5  # BM25 normalizado frente a una pregunta aprobada del quiz
CHAT_QUIZ_MAX_LEVEL = 2
CHAT_NOT_FOUND = "Eso no se vio en esta clase. Puedes consultarlo con el docente o con tu Asistente Académico."

# --- Reloj simulado ---
_SIM_NOW_ENV = os.getenv("SIM_NOW", "").strip()
_clock_override: datetime | None = None


def now() -> datetime:
    """Hora actual del sistema (naive, hora de Lima). SIM_NOW o set_now() la fijan para pruebas y demo."""
    if _clock_override is not None:
        return _clock_override
    if _SIM_NOW_ENV:
        return dateparser.parse(_SIM_NOW_ENV)
    return datetime.now().replace(microsecond=0)


def set_now(value: datetime | None) -> None:
    global _clock_override
    _clock_override = value


# --- Curso (valores por defecto de la tabla course) ---
COURSE_DEFAULTS = {
    "attendance_threshold": 0.80,
    "live_min_ratio": 0.70,
    "recovery_days": 7,
    "key_coverage_min": 0.90,
    "quiz_pass_score": 3,
    "level2_role": "asistente_academico",
}
QUIZ_QUESTIONS_PER_ATTEMPT = 5
QUIZ_BANK_SIZE = 8
QUIZ_MIN_APPROVED = 5  # el quiz se activa para el alumno con al menos 5 preguntas aprobadas por el docente
MIN_GRADE = 14

# --- Zoom ---
RECONNECT_GAP_MIN = 2  # cortes menores = problema de conexión

# --- Diagnóstico ---
INACTIVE_DAYS = 14
PATTERN_WINDOW = 3
PATTERN_MIN_PREVIOUS = 4
PATTERN_DROP_FACTOR = 0.70
LATE_SHIFT_MIN = 20
MODE_SHIFT_MIN_RECOVERED = 2
MODE_SHIFT_HISTORY_LIVE = 0.60
QUIZ_TREND_WINDOW = 4
CONSECUTIVE_WINDOW = 4  # buscar 2 no_cumplida seguidas entre las últimas N sesiones
UNRECOVERED_DAYS = 4  # días sin avance en una sesión por recuperar para considerarla "sin recuperar"
RECOVERY_BALANCE_WINDOW = 4  # "sin recuperar lo perdido": de las últimas N clases perdidas, recuperó menos de la mitad
MIN_UNRECOVERED = 2  # ...y al menos 2 de ellas siguen sin recuperar
STUDY_HOUR_WINDOW_DAYS = 28

# --- Rescate ---
ESCALATION_HOURS = 48

# --- Micro-preguntas ---
MICRO_EVERY_DAYS = 14
MICRO_SIGNAL_DAYS = 21  # una respuesta deja de pesar en el diagnóstico pasado este plazo
MICRO_QUESTIONS = [
    "¿Cómo te está acomodando el horario del curso estas semanas?",
    "¿Qué es lo que más te está costando del curso ahora?",
    "¿Hay algo que te haría más fácil seguir el ritmo?",
]

# --- Roles ---
STUDENT_ROLE = "alumno"
TUTOR_ROLES = {"tutor", "asistente_academico", "docente", "coordinador"}
CONSENT_VERSION = "2026-10"

# --- Tareas periódicas ---
SCHEDULER_ENABLED = os.getenv("DISABLE_SCHEDULER", "").strip() not in {"1", "true", "yes"} and not ON_VERCEL
RECOMPUTE_EVERY_MINUTES = 60
