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
PROCESSED_DIR = DATA_DIR / "processed"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# En Vercel el bundle es de solo lectura: la base generada en el build se copia a /tmp al arrancar
# (los cambios duran mientras viva la instancia).
ON_VERCEL = bool(os.getenv("VERCEL"))
DB_SNAPSHOT = DATA_DIR / "dmc.db" if ON_VERCEL else None
DATABASE_URL = os.getenv("DATABASE_URL") or (
    "sqlite:////tmp/dmc.db" if ON_VERCEL else f"sqlite:///{(DATA_DIR / 'dmc.db').as_posix()}"
)

# --- LLM (proveedor compatible con OpenAI; Kimi por defecto) ---
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.moonshot.ai/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL_CHAT = os.getenv("LLM_MODEL_CHAT", "kimi-k2.6")
LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", "kimi-k2.6")
LLM_CONTEXT_TOKENS = int(os.getenv("LLM_CONTEXT_TOKENS", "256000"))
LLM_TEMPERATURE = os.getenv("LLM_TEMPERATURE")  # vacío = valor por defecto del proveedor
LLM_MOCK = os.getenv("LLM_MOCK", "").strip() in {"1", "true", "yes"} or not LLM_API_KEY or LLM_API_KEY == "..."
CHAT_CONTEXT_RATIO = 0.70
CHAT_TOP_FRAGMENTS = 8
CHAT_HISTORY_TURNS = 6
CHAT_NOT_FOUND = "Eso no se vio en esta clase. Puedes consultarlo con el docente o en el grupo del curso."

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
RECOVERY_MINUTES_HINT = 20

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
